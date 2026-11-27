#!/usr/bin/env python3
"""Gom tin báo mới nhất cho hạ tầng ĐANG XÂY / QUY HOẠCH trên bản đồ (cầu, đường, metro, sân bay…).

Danh sách hạ tầng lấy từ:
  1. data/ha-tang-tu-khoa.json – danh sách tự khai: ten, tu_khoa, loai_tru, và (tùy chọn)
       trang_thai: "hien_huu" (đã vận hành → KHÔNG theo dõi tin) | "dang_xay" | "quy_hoach" | "theo_doi" (luôn theo dõi)
       mo_ta: vài chữ mô tả vị trí để AI phân biệt hạ tầng trùng tên (VD "cầu vượt sông Hồng, Đan Phượng – Mê Linh")
  2. các lớp layers/*.geojson và *.geojson ở gốc repo: lấy tên + properties.status
       (hien_huu / dang_xay / quy_hoach). Chỉ hạ tầng dang_xay / quy_hoach được gom tin.
Thứ tự ưu tiên trạng thái: trang_thai tự khai > status trong lớp (trùng đúng tên) > mặc định theo dõi.

Hạ tầng đã vận hành vẫn có mục trong data/tin-ha-tang.json (van_hanh: true, không có tin) để popup ghi rõ
"đã đi vào vận hành" và giữ nút "Tìm thêm tin".

Lọc tin:
  - Có GEMINI_API_KEY → Gemini (miễn phí) đọc tiêu đề, chỉ giữ tin về CHÍNH hạ tầng đó và về
    quy hoạch / đầu tư / GPMB / thi công / tiến độ / thông xe…; bỏ tin tai nạn, ùn tắc, sự việc trên cầu…
    Kết quả lọc lưu ở data/tin-ha-tang-loc.json để không hỏi lại.
  - Không có khóa / hết hạn mức → lọc bằng quy tắc từ khóa (kém chính xác hơn).
Nguồn tin: Google News RSS tiếng Việt. Chỉ dùng thư viện chuẩn của Python."""
import glob, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D          # dùng chung hàm gọi Gemini (giãn cách, đổi model, hết hạn mức)

GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TU_KHAI = os.path.join(GOC, "data", "ha-tang-tu-khoa.json")
RA = os.path.join(GOC, "data", "tin-ha-tang.json")
LOC = os.path.join(GOC, "data", "tin-ha-tang-loc.json")
SO_TIN, SO_NGAY = 8, 120
MAX_GEMINI = int(os.environ.get("MAX_GEMINI", "150"))       # tối đa số lượt hỏi Gemini mỗi lần chạy
ACTIVE = {"dang_xay", "quy_hoach", "theo_doi"}
KHOA_TEN = re.compile(r"^(ten|name|title|label|tieu_de|ten_.*|name_.*|tooltip)$", re.I)
TU_TRANG_THAI = re.compile(r"\s*[–—-]\s*(đang|dự kiến|quy hoạch|đã|chuẩn bị|sắp|hoàn thành|khởi công).*$", re.I)
UU_TIEN = ["baodauthau.vn", "vnexpress.net", "dantri.com.vn", "laodong.vn", "tienphong.vn", "hanoimoi.vn",
           "hanoionline.vn", "kinhtedothi.vn", "cafef.vn", "congly.vn", "vietnamnet.vn", "tuoitre.vn",
           "thanhnien.vn", "baochinhphu.vn", "vneconomy.vn", "znews.vn", "nhandan.vn", "baogiaothong.vn"]
# Quy tắc dự phòng (khi không có Gemini)
TOT = re.compile(r"khởi công|thi công|tiến độ|dự án|đầu tư|giải phóng mặt bằng|GPMB|mặt bằng|quy hoạch|thông xe|hợp long|"
                 r"khánh thành|vốn|phê duyệt|nhà thầu|đấu thầu|mở rộng|xây dựng|nghiên cứu|đề xuất|chủ trương|lắp|dầm|trụ|"
                 r"nghiệm thu|đưa vào (khai thác|sử dụng|vận hành)|hoàn thành|chậm|đội vốn|tái định cư|bồi thường|BOT|PPP", re.I)
XAU = re.compile(r"tai nạn|va chạm|nhảy (xuống|cầu)|tử vong|thi thể|mất tích|tìm kiếm|ùn (ứ|tắc)|kẹt xe|giao thông hà nội (sáng|chiều|tối)|"
                 r"bỏ lại xe|cháy|bắt (giữ|khẩn cấp)|cướp|trộm|đánh nhau|cố thủ|tự tử|đuối nước|lật|đâm|tông|say xỉn|nồng độ cồn|"
                 r"phạt|camera|thời tiết|mưa|ngập|dự báo", re.I)
GENERIC = re.compile(r"^(đường cao tốc|cầu|đường sắt đô thị|đường|cao tốc|metro|(ct|ql|đt|tl)\.?\s*\d+[a-z]?)$", re.I)


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", s.replace("–", "-").replace("—", "-").replace(",", ".")).strip()


def lam_sach(ten):
    ten = re.sub(r"<[^>]+>", " ", str(ten))            # bỏ thẻ HTML nếu tên có <b>…
    ten = re.sub(r"\([^)]*\)", " ", ten)              # bỏ phần trong ngoặc
    ten = TU_TRANG_THAI.sub("", ten.split("\n")[0])   # bỏ "– đang xây"…
    ten = re.sub(r"\s+", " ", ten).strip(" -–:;,.=")
    return ten if 3 <= len(ten) <= 80 and re.search(r"[A-Za-zÀ-ỹ]", ten) else ""


def ten_tu_layers():
    """[(tên, status)] lấy từ các lớp bản đồ."""
    files = glob.glob(os.path.join(GOC, "layers", "**", "*.*json"), recursive=True) + glob.glob(os.path.join(GOC, "*.geojson"))
    out = []
    for f in files:
        try: j = json.load(open(f, encoding="utf-8"))
        except Exception as e: print(f"! bỏ qua {os.path.relpath(f, GOC)}: {e}", file=sys.stderr); continue
        for ft in j.get("features", []) if isinstance(j, dict) else []:
            p = ft.get("properties") or {}
            for k, v in p.items():
                if KHOA_TEN.match(k) and isinstance(v, str):
                    t = lam_sach(v)
                    if t and not GENERIC.match(t): out.append((t, str(p.get("status") or "").strip()))
                    break
    return out


def danh_sach():
    """Ghép danh sách tự khai + lớp bản đồ, gán trạng thái cho từng hạ tầng."""
    try: tu_khai = json.load(open(TU_KHAI, encoding="utf-8"))
    except Exception: tu_khai = []
    lop = {}
    for t, st in ten_tu_layers():
        lop.setdefault(fold(t), {"ten": t, "st": set()})["st"].add(st)
    def st_lop(names):
        sts = set().union(*[lop[fold(n)]["st"] for n in names if fold(n) in lop]) if any(fold(n) in lop for n in names) else set()
        if sts & {"dang_xay"}: return "dang_xay"
        if sts & {"quy_hoach"}: return "quy_hoach"
        if sts and sts <= {"hien_huu", ""}: return "hien_huu"
        return ""
    ds, da = [], set()
    for h in tu_khai:
        if fold(h["ten"]) in da: continue
        names = [h["ten"]] + (h.get("tu_khoa") or [])
        h = dict(h); h["trang_thai"] = h.get("trang_thai") or st_lop(names) or "theo_doi"
        ds.append(h); da |= {fold(n) for n in names}
    for k, v in lop.items():
        if k in da: continue
        st = "dang_xay" if "dang_xay" in v["st"] else "quy_hoach" if "quy_hoach" in v["st"] else "hien_huu"
        ds.append({"ten": v["ten"], "tu_khoa": [v["ten"]], "tu_dong": True, "trang_thai": st}); da.add(k)
    n_act = sum(h["trang_thai"] in ACTIVE for h in ds)
    print(f"Hạ tầng: {len(ds)} mục ({len(tu_khai)} tự khai) – theo dõi tin {n_act}, đã vận hành (bỏ qua) {len(ds) - n_act}")
    return ds


def rss(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f'"{q}" when:{SO_NGAY}d', "hl": "vi", "gl": "VN", "ceid": "VN:vi"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ban-do-dau-gia bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def doc_rss(xml_bytes):
    out = []
    for it in ET.fromstring(xml_bytes).iter("item"):
        src = it.find("source")
        title = (it.findtext("title") or "").strip()
        nguon = (src.text or "").strip() if src is not None else ""
        if nguon and title.endswith(" - " + nguon): title = title[: -len(nguon) - 3].strip()
        try: ngay = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception: ngay = ""
        out.append({"tieu_de": title, "link": (it.findtext("link") or "").strip(), "nguon": nguon,
                    "web": src.get("url", "") if src is not None else "", "ngay": ngay})
    return out


def khop(tk, tieu_de):
    a, b = fold(tk), fold(tieu_de)
    if re.search(r"(?<![\w.])" + re.escape(a) + r"(?![\w]|[.,]\d)", b): return True   # "quốc lộ 5" không khớp "quốc lộ 50", "vành đai 3" không khớp "vành đai 3.5"
    toks = [w for w in a.split() if len(w) > 1]                 # tên dài: đủ ~75% chữ là được
    return len(toks) >= 4 and sum(w in b.split() for w in toks) >= 0.75 * len(toks)


PROMPT = """Bạn lọc tin báo cho bản đồ theo dõi hạ tầng ở Hà Nội và vùng Thủ đô.
Hạ tầng: "{ten}" ({st}){mo_ta}. Từ khóa đã dùng để tìm: {tk}.
Dưới đây là các tiêu đề tin (đánh số). Chọn những tin mà CẢ HAI điều kiện đều đúng:
1) Nói về CHÍNH hạ tầng này (không phải công trình khác trùng hoặc gần giống tên, không phải tỉnh khác);
2) Nội dung về quy hoạch, chủ trương, đầu tư, vốn, đấu thầu/nhà thầu, giải phóng mặt bằng, tái định cư, khởi công, thi công, tiến độ,
   hợp long, thông xe, khánh thành, đưa vào khai thác, điều chỉnh dự án, chậm tiến độ/đội vốn.
LOẠI các tin: tai nạn, va chạm, ùn tắc, bản tin giao thông, người nhảy cầu/sự việc trên cầu, tội phạm, thời tiết, ngập, phạt nguội,
và tin chỉ nhắc tên hạ tầng làm mốc địa điểm (VD "gần cầu X", "chân cầu X").
Trả về DUY NHẤT JSON dạng {{"giu":[số thứ tự…]}}.

{ds}"""


def loc_gemini(h, tins, cache, dem):
    """Trả tập link được giữ. Dùng cache; tin mới thì hỏi Gemini một lần cho cả nhóm."""
    giu, moi = set(), []
    for t in tins:
        k = f"{fold(h['ten'])}|{t['link']}"
        if k in cache: (giu.add(t["link"]) if cache[k]["giu"] else None)
        elif XAU.search(t["tieu_de"]): cache[k] = {"giu": 0, "ngay": t.get("ngay") or "", "cach": "quy tắc"}
        else: moi.append(t)
    if not moi: return giu
    dung_ai = D.AI_KEY and not D._ai_state["off"] and dem[0] < MAX_GEMINI
    if dung_ai:
        moi = moi[:25]
        st = {"dang_xay": "đang xây dựng", "quy_hoach": "đang quy hoạch / chuẩn bị đầu tư"}.get(h["trang_thai"], "đang theo dõi")
        ds = "\n".join(f"{i + 1}. {t['tieu_de']} ({t['nguon']}, {t.get('ngay', '')})" for i, t in enumerate(moi))
        out = D._gemini(PROMPT.format(ten=h["ten"], st=st, mo_ta=f", {h['mo_ta']}" if h.get("mo_ta") else "",
                                      tk=", ".join(h.get("tu_khoa") or [h["ten"]]), ds=ds))
        dem[0] += 1
        m = re.search(r"\{.*\}", out or "", re.S)
        try: idx = {int(x) for x in json.loads(m.group(0)).get("giu", [])} if m else None
        except Exception: idx = None
        if idx is not None:
            for i, t in enumerate(moi):
                ok = (i + 1) in idx
                cache[f"{fold(h['ten'])}|{t['link']}"] = {"giu": int(ok), "ngay": t.get("ngay") or "", "cach": "Gemini"}
                if ok: giu.add(t["link"])
            return giu
    for t in moi:                      # không có / lỗi Gemini → quy tắc, KHÔNG lưu cache để lần sau AI xét lại
        if TOT.search(t["tieu_de"]): giu.add(t["link"])
    return giu


def main():
    ds = danh_sach()
    cu = {}
    if os.path.exists(RA):
        try: cu = json.load(open(RA, encoding="utf-8")).get("muc", {})
        except Exception: cu = {}
    try: cache = json.load(open(LOC, encoding="utf-8"))
    except Exception: cache = {}
    han = time.strftime("%Y-%m-%d", time.gmtime(time.time() - SO_NGAY * 86400))
    cache = {k: v for k, v in cache.items() if not v.get("ngay") or v["ngay"] >= han}
    print("Lọc tin: " + ("Gemini (miễn phí) + quy tắc" if D.AI_KEY else "quy tắc – chưa có GEMINI_API_KEY"))
    muc, loi, goi, dem = {}, 0, 0, [0]
    for h in ds:
        ten, tks = h["ten"], h.get("tu_khoa") or [h["ten"]]
        tim = "https://news.google.com/search?" + urllib.parse.urlencode({"q": tks[0], "hl": "vi", "gl": "VN", "ceid": "VN:vi"})
        if h["trang_thai"] not in ACTIVE:
            muc[ten] = {"tu_khoa": tks, "tin": [], "van_hanh": True, "trang_thai": "hien_huu", "tim_them": tim}
            continue
        loai = [fold(x) for x in h.get("loai_tru", [])]
        gom = {t["link"]: t for t in cu.get(ten, {}).get("tin", [])}
        for tk in tks:
            goi += 1
            try: tins = doc_rss(rss(tk))
            except Exception as e: loi += 1; print(f"! {ten} / {tk}: {e}", file=sys.stderr); continue
            for t in tins:
                ft = fold(t["tieu_de"])
                if khop(tk, t["tieu_de"]) and not any(x in ft for x in loai): gom.setdefault(t["link"], t)
            time.sleep(1.2)
        tin = [t for t in gom.values() if not t.get("ngay") or t["ngay"] >= han]
        seen, uniq = set(), []                                   # bỏ tin trùng tiêu đề (cùng bài nhiều báo đăng lại)
        for t in sorted(tin, key=lambda t: t.get("ngay") or "", reverse=True):
            k = fold(t["tieu_de"])[:70]
            if k not in seen: seen.add(k); uniq.append(t)
        giu = loc_gemini(h, uniq[:40], cache, dem)
        tin = [t for t in uniq if t["link"] in giu]
        tin.sort(key=lambda t: (t.get("ngay") or "", any(d in (t.get("web") or "") for d in UU_TIEN)), reverse=True)
        muc[ten] = {"tu_khoa": tks, "tin": tin[:SO_TIN], "trang_thai": h["trang_thai"], "tim_them": tim}
        print(f"{ten} [{h['trang_thai']}]: {len(muc[ten]['tin'])}/{len(uniq)} tin giữ lại")
    if goi and loi == goi: sys.exit("Không lấy được tin nào – giữ nguyên file cũ.")
    json.dump({"cap_nhat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "muc": muc},
              open(RA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cache, open(LOC, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Xong. Gemini: {dem[0]} lượt" + (" (đã hết hạn mức – phần còn lại lọc bằng quy tắc)" if D._ai_state["off"] else ""))


if __name__ == "__main__":
    main()
