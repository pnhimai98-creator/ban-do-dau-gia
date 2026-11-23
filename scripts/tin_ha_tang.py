#!/usr/bin/env python3
"""Gom tin báo mới nhất cho từng hạ tầng trên bản đồ (cầu, đường, metro, sân bay…).

Danh sách hạ tầng lấy TỰ ĐỘNG từ:
  1. data/ha-tang-tu-khoa.json  – danh sách tự khai (thêm tên/từ khóa riêng, loại trừ…)
  2. mọi file .geojson / .json trong thư mục layers/ và file *.geojson ở gốc repo
     (đọc tên trong properties: ten, name, title, label, ten_*…)
Ghi ra data/tin-ha-tang.json – bản đồ đọc file này khi bấm vào hạ tầng.
Nguồn tin: Google News RSS tiếng Việt. Chỉ dùng thư viện chuẩn của Python."""
import glob, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TU_KHAI = os.path.join(GOC, "data", "ha-tang-tu-khoa.json")
RA = os.path.join(GOC, "data", "tin-ha-tang.json")
SO_TIN, SO_NGAY, TOI_DA = 8, 120, 250
KHOA_TEN = re.compile(r"^(ten|name|title|label|tieu_de|ten_.*|name_.*|tooltip)$", re.I)
TU_TRANG_THAI = re.compile(r"\s*[–—-]\s*(đang|dự kiến|quy hoạch|đã|chuẩn bị|sắp|hoàn thành|khởi công).*$", re.I)
UU_TIEN = ["baodauthau.vn", "vnexpress.net", "dantri.com.vn", "laodong.vn", "tienphong.vn", "hanoimoi.vn",
           "hanoionline.vn", "kinhtedothi.vn", "cafef.vn", "congly.vn", "vietnamnet.vn", "tuoitre.vn",
           "thanhnien.vn", "baochinhphu.vn", "vneconomy.vn", "znews.vn", "nhandan.vn", "baogiaothong.vn"]

def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", s.replace("–", "-").replace("—", "-").replace(",", ".")).strip()

def lam_sach(ten):
    ten = re.sub(r"<[^>]+>", " ", str(ten))            # bỏ thẻ HTML nếu tên có <b>…
    ten = re.sub(r"\([^)]*\)", " ", ten)              # bỏ phần trong ngoặc
    ten = TU_TRANG_THAI.sub("", ten.split("\n")[0])   # bỏ "– đang xây"…
    ten = re.sub(r"\s+", " ", ten).strip(" -–:;,.")
    return ten if 3 <= len(ten) <= 80 and re.search(r"[A-Za-zÀ-ỹ]", ten) else ""

def ten_tu_layers():
    files = glob.glob(os.path.join(GOC, "layers", "**", "*.*json"), recursive=True) + glob.glob(os.path.join(GOC, "*.geojson"))
    out = []
    for f in files:
        try: j = json.load(open(f, encoding="utf-8"))
        except Exception as e: print(f"! bỏ qua {os.path.relpath(f, GOC)}: {e}", file=sys.stderr); continue
        def duyet(o):
            if isinstance(o, dict):
                props = o.get("properties") if isinstance(o.get("properties"), dict) else (o if "geometry" not in o else {})
                for k, v in props.items():
                    if KHOA_TEN.match(k) and isinstance(v, str):
                        t = lam_sach(v)
                        if t: out.append(t)
                        break
                for v in o.values():
                    if isinstance(v, (list, dict)): duyet(v)
            elif isinstance(o, list):
                for v in o: duyet(v)
        duyet(j)
    return out

def danh_sach():
    ds, da = [], set()
    try: tu_khai = json.load(open(TU_KHAI, encoding="utf-8"))
    except Exception: tu_khai = []
    for h in tu_khai:
        if fold(h["ten"]) not in da:
            da.add(fold(h["ten"])); ds.append(h)
    them = 0
    for t in ten_tu_layers():
        if fold(t) not in da and len(ds) < TOI_DA:
            da.add(fold(t)); ds.append({"ten": t, "tu_khoa": [t], "tu_dong": True}); them += 1
    print(f"Hạ tầng: {len(tu_khai)} tự khai + {them} lấy từ layers/")
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
    if a in b: return True
    toks = [w for w in a.split() if len(w) > 1]                 # tên dài: đủ ~75% chữ là được
    return len(toks) >= 4 and sum(w in b.split() for w in toks) >= 0.75 * len(toks)

def main():
    ds = danh_sach()
    cu = {}
    if os.path.exists(RA):
        try: cu = json.load(open(RA, encoding="utf-8")).get("muc", {})
        except Exception: cu = {}
    han = time.strftime("%Y-%m-%d", time.gmtime(time.time() - SO_NGAY * 86400))
    muc, loi, goi = {}, 0, 0
    for h in ds:
        ten, tks = h["ten"], h.get("tu_khoa") or [h["ten"]]
        loai = [fold(x) for x in h.get("loai_tru", [])]
        gom = {fold(t["tieu_de"]): t for t in cu.get(ten, {}).get("tin", [])}
        for tk in tks:
            goi += 1
            try: tins = doc_rss(rss(tk))
            except Exception as e: loi += 1; print(f"! {ten} / {tk}: {e}", file=sys.stderr); continue
            for t in tins:
                ft = fold(t["tieu_de"])
                if khop(tk, t["tieu_de"]) and not any(x in ft for x in loai): gom.setdefault(ft, t)
            time.sleep(1.2)
        tin = [t for t in gom.values() if not t.get("ngay") or t["ngay"] >= han]
        tin.sort(key=lambda t: (t.get("ngay") or "", any(d in (t.get("web") or "") for d in UU_TIEN)), reverse=True)
        muc[ten] = {"tu_khoa": tks, "tin": tin[:SO_TIN], "tim_them": "https://news.google.com/search?" +
                    urllib.parse.urlencode({"q": tks[0], "hl": "vi", "gl": "VN", "ceid": "VN:vi"})}
        print(f"{ten}: {len(muc[ten]['tin'])} tin")
    if goi and loi == goi: sys.exit("Không lấy được tin nào – giữ nguyên file cũ.")
    json.dump({"cap_nhat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "muc": muc},
              open(RA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
