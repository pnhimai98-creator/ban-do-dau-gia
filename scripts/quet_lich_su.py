#!/usr/bin/env python3
"""
Quét lịch sử (v3): tìm các phiên ĐÃ ĐẤU GIÁ có GIÁ TRÚNG CAO NHẤT, từ 1/1/2024 đến 29/5/2026
History backfill v3 – auctions with a known top winning price (default 2024-01-01 → 2026-05-29)

Chạy trong .github/workflows/quet-lich-su.yml (nhiều lượt, mỗi lượt làm tiếp phần còn lại):
    python scripts/quet_lich_su.py

Ba yêu cầu: (1) chỉ phiên đã trúng, bắt buộc có giá trúng cao nhất; (2) không trùng phiên/lô đất;
(3) không nhầm ngày đấu giá với ngày đăng bài.

Các bước
1. TÌM BÀI – chia khoảng thời gian thành khung 15 ngày, hỏi Google News (after:/before:).
   Google hay trả cả bài ngoài khung → chỉ nhận bài ĐĂNG trong [đầu khoảng − 30 ngày, cuối khoảng + 120 ngày].
2. LỌC TIÊU ĐỀ BẰNG GEMINI – mỗi lượt gọi xét ~40 tiêu đề, chỉ giữ bài "kết quả phiên đấu giá đất cụ thể"
   (bỏ thông báo trước phiên, bình luận, chính sách, tỉnh khác). Chỉ bài được giữ mới tải toàn văn → tiết kiệm hạn mức.
3. ĐỌC BÀI BẰNG GEMINI – câu lệnh riêng: cho biết NGÀY ĐĂNG BÀI, yêu cầu trả DANH SÁCH các phiên đã diễn ra
   có giá trúng cao nhất; ngày phiên phải là ngày tổ chức phiên ghi trong bài (không lấy ngày đăng, không đoán).
4. KIỂM TRA – bỏ phiên không có giá trúng cao nhất / không có ngày phiên / ngày phiên sau ngày đăng bài
   hoặc trước ngày đăng quá 1 năm / ngoài khoảng quét / ngoài vùng dự án.
5. CHỐNG TRÙNG – cùng tỉnh + cùng xã (tên mới hoặc cũ) + ngày lệch ≤ 3 ngày (hoặc cùng giá trúng cao nhất,
   lệch ≤ 30 ngày) = một phiên. Trùng phiên đã có trong data.csv → bỏ (hoặc tạo dòng cập nhật nếu phiên đó chưa có giá);
   trùng dòng đang chờ duyệt → GỘP: điền ô còn trống, thêm link nguồn khác vào ghi chú.
6. GHI vào cho-duyet.csv (khung ② trong admin) để duyệt tay. Không ghi thẳng data.csv.
   Tỉnh cũ → tỉnh mới; bài trước 1/7/2025 dùng tên xã cũ → tra data/xa-cu-moi.json (tự học từ data.csv,
   sửa/bổ sung tay được), không tra được thì để trống cột xa + ghi chú "Cần điền xã mới".
Hết hạn mức Gemini → dừng, lượt sau làm tiếp. Không có GEMINI_API_KEY → không chạy (tránh dữ liệu rác).
Chỉ dùng thư viện chuẩn Python.
"""
import csv, html, io, json, os, re, sys, time, unicodedata, urllib.parse
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D        # dùng chung: tải bài (fetch_text/body), giải link Google, gọi Gemini, geocode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
PEND = os.path.join(ROOT, "cho-duyet.csv")
STATE = os.path.join(ROOT, "data", "quet-lich-su.json")
XAMAP = os.path.join(ROOT, "data", "xa-cu-moi.json")
TODAY = datetime.now(timezone(timedelta(hours=7))).date()
T0 = time.monotonic()

START = date.fromisoformat(os.environ.get("QUET_TU") or "2024-01-01")
END = date.fromisoformat(os.environ.get("QUET_DEN") or "2026-05-29")
WINDOW = int(os.environ.get("KHUNG_NGAY", "15"))
MAX_READ = int(os.environ.get("MAX_ARTICLES", "80"))            # số bài đọc toàn văn tối đa mỗi lượt
BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "32"))
GATHER_SHARE = 0.35                                             # tối đa 35% thời gian cho bước tìm bài
PUB_FROM, PUB_TO = START - timedelta(days=30), END + timedelta(days=120)
MERGE_DAY = date(2025, 7, 1)
VERSION = "v3"
TAG = "quét lịch sử"

GN = "https://news.google.com/rss/search?hl=vi&gl=VN&ceid=VN:vi&q="
PROVINCES = ["Hà Nội", "Bắc Ninh", "Hưng Yên", "Phú Thọ", "Thái Nguyên", "Ninh Bình"]
OLD_PROV = {"Bắc Giang": "Bắc Ninh", "Vĩnh Phúc": "Phú Thọ", "Hòa Bình": "Phú Thọ", "Hoà Bình": "Phú Thọ",
            "Thái Bình": "Hưng Yên", "Hà Nam": "Ninh Bình", "Nam Định": "Ninh Bình", "Bắc Kạn": "Thái Nguyên"}
HN_OLD = ["Đông Anh", "Mê Linh", "Sóc Sơn", "Gia Lâm", "Thanh Oai", "Thanh Trì", "Hoài Đức", "Quốc Oai", "Thạch Thất", "Chương Mỹ",
          "Ba Vì", "Phúc Thọ", "Đan Phượng", "Sơn Tây", "Ứng Hòa", "Mỹ Đức", "Phú Xuyên", "Thường Tín", "Long Biên", "Hà Đông",
          "Bắc Từ Liêm", "Nam Từ Liêm"]
OR = lambda xs: "(" + " OR ".join(f'"{x}"' for x in xs) + ")"
QUERIES = [
    '"trúng đấu giá" đất "Hà Nội"',
    '"đấu giá quyền sử dụng đất" "Hà Nội" ("giá trúng" OR "trúng cao nhất" OR "đấu giá thành công")',
    '"trúng đấu giá" ' + OR(HN_OLD[:11]),
    '"trúng đấu giá" ' + OR(HN_OLD[11:]),
    '"trúng đấu giá" đất ' + OR(["Bắc Ninh", "Bắc Giang", "Hưng Yên", "Thái Bình"]),
    '"trúng đấu giá" đất ' + OR(["Phú Thọ", "Vĩnh Phúc", "Hòa Bình", "Thái Nguyên", "Bắc Kạn"]),
    '"trúng đấu giá" đất ' + OR(["Ninh Bình", "Hà Nam", "Nam Định"]),
    '"đấu giá" đất ("giá trúng cao nhất" OR "trúng cao nhất") ' + OR(["Hà Nội", "Bắc Ninh", "Hưng Yên", "Phú Thọ", "Vĩnh Phúc", "Bắc Giang"]),
]
PRE_OK = r"trung|ket qua|thanh cong|tr ?/ ?m|trieu dong|ty dong|cao nhat|chenh|bo coc|thu ve|gap"
PRE_NO = r"bien so|o to|xe may|khoang san|mo cat|co phan|tai san thi hanh an|tang vat|tau bien|so dep"


def log(*a): print(*a, flush=True)


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"^(xa|phuong|thi tran|dac khu|huyen|quan|thi xa|tinh|thanh pho|tp\.?)\s+", "", s.strip())
    return re.sub(r"\s+", " ", s)


def read_csv(path):
    if not os.path.exists(path): return [], []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh); return list(rd.fieldnames or []), list(rd)


def write_csv(path, head, rows):
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); [w.writerow({k: r.get(k, "") for k in head}) for r in rows]
    open(path, "w", encoding="utf-8", newline="").write(buf.getvalue())


def load_json(path, default):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)


def num(v):
    """'1.234,5' / '125,2' / 125.2 → 125.2 ; không phải số → None"""
    if v in (None, ""): return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace(" ", "")
    m = re.search(r"\d[\d.,]*", s)
    if not m: return None
    s = m.group(0)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s): s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s): s = s.replace(",", "")
    else: s = s.replace(",", ".")
    try: return float(s)
    except ValueError: return None


def fmt(x): return "" if x is None else (f"{x:.3f}".rstrip("0").rstrip("."))


def iso(s):
    try: return date.fromisoformat(str(s)[:10])
    except Exception: return None


def gemini(prompt, max_tokens=4096):
    """Gọi Gemini (giống D._gemini nhưng cho câu trả lời dài hơn). Hết hạn mức (429 hai lần) → D._ai_state['off'] = True."""
    import urllib.request
    S = D._ai_state
    if not D.AI_KEY or S["off"]: return None
    for attempt in range(3):
        wait = D.AI_GAP_S - (time.monotonic() - S["last"])
        if wait > 0: time.sleep(wait)
        S["last"] = time.monotonic()
        model = D.AI_MODELS[min(S["model"], len(D.AI_MODELS) - 1)]
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": max_tokens}}).encode()
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                                         headers={"x-goog-api-key": D.AI_KEY, "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r: j = json.loads(r.read().decode("utf-8"))
            S["calls"] += 1
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return "".join(x.get("text", "") for x in parts)
        except Exception as ex:
            code = getattr(ex, "code", 0); log(f"    Gemini lỗi ({model}): {ex}")
            if code == 404 and S["model"] < len(D.AI_MODELS) - 1: S["model"] += 1; continue
            if code == 429:
                if attempt == 0: time.sleep(40); continue
                if S["model"] < len(D.AI_MODELS) - 1: S["model"] += 1; log("    → hết hạn mức model này, thử model sau"); continue
                log("    → HẾT HẠN MỨC Gemini miễn phí hôm nay – dừng, lượt sau làm tiếp"); S["off"] = True; return None
            if code in (500, 502, 503, 504): time.sleep(8 * (attempt + 1)); continue
            if code in (400, 401, 403): log("    → khóa GEMINI_API_KEY sai hoặc chưa bật API"); S["off"] = True
            return None
    return None


def gemini_json(prompt):
    """Gọi Gemini, trả JSON (dict) hoặc None."""
    out = gemini(prompt)
    if not out: return None
    m = re.search(r"\{.*\}", out, re.S)
    try: return json.loads(m.group(0)) if m else None
    except Exception: return None


def ai_off(): return (not D.AI_KEY) or D._ai_state["off"]


def time_left(share=1.0): return time.monotonic() - T0 < BUDGET_MIN * 60 * share


# ---------------------------------------------------------------- 1. tìm bài
def windows():
    out, b = [], END + timedelta(days=1)
    while b > START:
        a = max(START, b - timedelta(days=WINDOW)); out.append((a, b)); b = a
    return out


def search(q):
    raw = D.get(GN + urllib.parse.quote(q), timeout=30)
    for it in ET.fromstring(raw.encode("utf-8")).iter("item"):
        src = it.find("source")
        try: pub = datetime.strptime((it.findtext("pubDate") or "").strip(), "%a, %d %b %Y %H:%M:%S %Z").date()
        except Exception: pub = None
        title = unicodedata.normalize("NFC", html.unescape(it.findtext("title") or "")).strip()
        srcname = (src.text or "").strip() if src is not None else ""
        if srcname and title.endswith(" - " + srcname): title = title[: -len(srcname) - 3].strip()
        yield {"title": title, "link": (it.findtext("link") or "").strip(), "pub": pub.isoformat() if pub else "", "src": srcname}


def gather(st):
    cand, done = st["ung_vien"], set(st["khung_da_quet"])
    titles = {fold(v["title"])[:90] for v in cand.values()}
    for a, b in windows():
        if a.isoformat() in done: continue
        if not time_left(GATHER_SHARE): log("⏱ Hết phần thời gian tìm bài – các khung còn lại để lượt sau."); return
        n_new, ok = 0, True
        for q in QUERIES:
            try:
                for it in search(f"{q} after:{(a - timedelta(days=1)).isoformat()} before:{b.isoformat()}"):
                    p, ft = iso(it["pub"]), fold(it["title"])
                    if not p or not (PUB_FROM <= p <= PUB_TO): continue          # bài đăng ngoài khoảng → bỏ (Google hay trả bài mới)
                    if it["link"] in cand or ft[:90] in titles: continue
                    if "dau gia" not in ft and "trung" not in ft: continue
                    if re.search(PRE_NO, ft) or not re.search(PRE_OK, ft): continue
                    cand[it["link"]] = {"title": it["title"], "pub": it["pub"], "src": it["src"], "khung": a.isoformat()}
                    titles.add(ft[:90]); n_new += 1
            except Exception as ex:
                log(f"    tìm lỗi ({ex}) – khung này để lượt sau"); ok = False; break
            time.sleep(1.2)
        log(f"• Khung {a} → {b - timedelta(days=1)}: +{n_new} bài")
        if ok: st["khung_da_quet"].append(a.isoformat())


# ---------------------------------------------------------------- 2. lọc tiêu đề bằng Gemini
CLASSIFY = """Bạn lọc tiêu đề báo cho bộ dữ liệu KẾT QUẢ đấu giá quyền sử dụng đất tại: Hà Nội, Bắc Ninh (gồm Bắc Giang cũ),
Hưng Yên (gồm Thái Bình cũ), Phú Thọ (gồm Vĩnh Phúc, Hòa Bình cũ), Thái Nguyên (gồm Bắc Kạn cũ), Ninh Bình (gồm Hà Nam, Nam Định cũ).
Với mỗi tiêu đề, trả "co" nếu tiêu đề cho thấy bài nêu KẾT QUẢ của (các) phiên đấu giá đất cụ thể đã diễn ra ở các tỉnh trên
(giá trúng, trúng cao nhất, đấu giá thành công, thu về bao nhiêu tỷ, chênh/gấp bao nhiêu lần giá khởi điểm, bỏ cọc sau phiên…);
trả "khong" nếu là: thông báo/sắp đấu giá, kế hoạch, chính sách/luật, bình luận chung không gắn phiên cụ thể, tỉnh khác,
đấu giá không phải đất (biển số, xe, khoáng sản, tài sản thi hành án…), hoặc không chắc.
Trả về DUY NHẤT JSON {{"kq":{{"<số>":"co"|"khong", …}}}}.

{ds}"""


def classify(st):
    todo = [k for k, v in st["ung_vien"].items() if "loai" not in v]
    if not todo: return
    todo.sort(key=lambda k: st["ung_vien"][k]["pub"], reverse=True)
    n_co = n = 0
    for i in range(0, len(todo), 40):
        if ai_off() or not time_left(0.6): break
        chunk = todo[i:i + 40]
        ds = "\n".join(f"{j + 1}. {st['ung_vien'][k]['title']} ({st['ung_vien'][k]['src']}, {st['ung_vien'][k]['pub']})" for j, k in enumerate(chunk))
        j = gemini_json(CLASSIFY.format(ds=ds))
        if not j or not isinstance(j.get("kq"), dict): continue                  # lỗi → lượt sau xét lại
        for jj, k in enumerate(chunk):
            v = str(j["kq"].get(str(jj + 1), "")).lower()
            if v in ("co", "có", "khong", "không"):
                st["ung_vien"][k]["loai"] = "co" if v in ("co", "có") else "khong"; n += 1; n_co += v in ("co", "có")
    log(f"Lọc tiêu đề: xét {n} bài, giữ {n_co} bài kết quả")


# ---------------------------------------------------------------- 3. đọc bài bằng Gemini
EXTRACT = """Bạn trích xuất KẾT QUẢ các phiên đấu giá quyền sử dụng đất từ một bài báo tiếng Việt.
BÀI ĐĂNG NGÀY: {pub} (dd/mm/yyyy). TIÊU ĐỀ: {title}

Liệt kê MỌI phiên đấu giá đất ĐÃ DIỄN RA được bài nêu GIÁ TRÚNG CAO NHẤT (đơn giá trên m²), kể cả phiên chỉ được nhắc lại.
Bỏ phiên không có giá trúng cao nhất, phiên chưa diễn ra, đấu giá không phải đất.
Mỗi phiên trả các khóa (không biết thì để ""):
- ngay: ngày TỔ CHỨC PHIÊN đấu giá, dạng YYYY-MM-DD. Chỉ lấy khi bài ghi rõ ngày phiên (VD "ngày 10/8", "sáng 5/11/2025").
  KHÔNG dùng ngày đăng bài, ngày công bố/phê duyệt kết quả, ngày nộp tiền. Bài chỉ ghi ngày/tháng thì suy ra năm sao cho
  ngày phiên KHÔNG SAU ngày đăng bài và gần ngày đăng bài nhất. Bài không ghi ngày phiên → "".
- ngay_nguyen_van: đoạn chữ ngắn trong bài nêu ngày phiên (VD "ngày 10/8"), không có thì "".
- tinh: tỉnh/thành phố như bài ghi (VD "Hà Nội", "Bắc Giang").
- xa: tên xã/phường/thị trấn như bài ghi, không kèm chữ "xã"/"phường".  huyen: huyện/quận/thị xã như bài ghi.
- xa_moi: tên xã/phường MỚI sau sáp nhập 7/2025 CHỈ KHI bài ghi rõ, không thì "".
- khu: tên khu/điểm/thôn đấu giá, ngắn gọn.  loai_dat: "Đất ở" | "Đất thương mại dịch vụ" | "Đất sản xuất kinh doanh" | "Đất nông nghiệp".
- so_thua (số thửa đưa ra), so_thua_trung (số thửa trúng), nguoi_tham_gia: số nguyên.
- gia_khoi_diem (thấp nhất), gia_trung_min, gia_trung_max: TRIỆU đồng/m², số thập phân dấu chấm (VD 125.2).
  Bài ghi "1,5 tỷ đồng/m²" → 1500. Không lấy tổng giá một lô, không lấy số liệu của năm/phiên khác để so sánh.
- tong_tien_ty: tổng tiền trúng (tỷ đồng).  bo_coc: "co" nếu bài nói có người trúng phiên này bỏ cọc/không nộp tiền, không thì "".
- chu_de_chinh: "co" nếu phiên này là chủ đề chính của bài, "khong" nếu chỉ được nhắc lại.
Chỉ ghi điều bài nêu rõ, không suy đoán. Trả về DUY NHẤT JSON {{"phien":[{{...}}, …]}} (không có phiên phù hợp → {{"phien":[]}}).

NỘI DUNG:
{text}"""


def extract(title, pub, text):
    j = gemini_json(EXTRACT.format(pub=iso(pub).strftime("%d/%m/%Y") if iso(pub) else "không rõ", title=title, text=text[:14000]))
    if j is None: return None
    out = j.get("phien", []) if isinstance(j, dict) else []
    return [x for x in out if isinstance(x, dict)]


# ---------------------------------------------------------------- 4–5. kiểm tra, chuẩn hóa, chống trùng
def learn_map(rows, xmap):
    n = 0
    for r in rows:
        olds = re.findall(r"(?:xã|phường|thị trấn)\s+([^,;()]+)", r.get("dia_danh_cu") or "")
        if len(olds) != 1 or not r.get("xa") or not r.get("tinh"): continue
        k = f"{fold(r['tinh'])}|{fold(olds[0])}"
        if k not in xmap: xmap[k] = r["xa"].strip(); n += 1
    return n


def names(r):
    """Tập tên xã (mới + cũ, đã bỏ dấu) của một dòng để so trùng."""
    s = {fold(r.get("xa"))} | {fold(x) for x in re.findall(r"(?:xã|phường|thị trấn)\s+([^,;()]+)", r.get("dia_danh_cu") or "")}
    return {x for x in s if x}


def same_session(a, b):
    if fold(a.get("tinh")) != fold(b.get("tinh")) or not (names(a) & names(b)): return False
    da, db = iso(a.get("ngay")), iso(b.get("ngay"))
    if da and db and abs((da - db).days) <= 3: return True
    ga, gb = num(a.get("gia_trung_max")), num(b.get("gia_trung_max"))
    return bool(ga and gb and abs(ga - gb) < 0.01 and da and db and abs((da - db).days) <= 30)


def check(s, pub, xmap):
    """Phiên Gemini trả → dòng chuẩn hoặc (None, lý do)."""
    gmax = num(s.get("gia_trung_max"))
    if not gmax or gmax <= 0: return None, "không có giá trúng cao nhất"
    p, d = iso(pub), iso(s.get("ngay"))
    if not d: return None, "bài không nêu ngày phiên"
    if p:                                                        # ngày phiên phải ≤ ngày đăng và không quá 1 năm trước
        if d > p:                                                # năm sai (thường AI lấy năm nay) → chọn năm gần nhất không sau ngày đăng
            best = None
            for y in (p.year, p.year - 1):
                try: c = d.replace(year=y)
                except ValueError: continue
                if c <= p and (best is None or c > best): best = c
            if best and (p - best).days <= 366: d = best
            else: return None, "ngày phiên sau ngày đăng bài"
        if (p - d).days > 366: return None, "ngày phiên cách ngày đăng hơn 1 năm (nghi nhầm)"
    if not (START <= d <= END): return None, "ngoài khoảng quét"
    tinh = re.sub(r"^(tỉnh|thành phố|TP\.?)\s+", "", str(s.get("tinh") or "").strip(), flags=re.I)
    old = []
    if tinh in OLD_PROV: old.append("tỉnh " + tinh); tinh = OLD_PROV[tinh]
    if not tinh and any(fold(h) == fold(s.get("huyen")) for h in HN_OLD): tinh = "Hà Nội"
    if tinh not in PROVINCES: return None, "ngoài vùng"
    xa, huyen, notes = str(s.get("xa") or "").strip(), str(s.get("huyen") or "").strip(), []
    if d < MERGE_DAY:                                            # trước sáp nhập: tên trong bài là tên cũ
        if xa or huyen:
            old.insert(0, ", ".join(filter(None, [f"xã {xa}" if xa else "", f"huyện {huyen}" if huyen else ""])))
        new = str(s.get("xa_moi") or "").strip() or (xmap.get(f"{fold(tinh)}|{fold(xa)}") if xa else "")
        if not new: notes.append("Cần điền xã mới (tên cũ: " + (xa or "?") + ")")
        xa = new or ""
    if not xa and not old: return None, "không rõ xã"
    r = {"ngay": d.isoformat(), "tinh": tinh, "xa": xa, "dia_danh_cu": ", ".join(old), "khu": str(s.get("khu") or "").strip(),
         "loai_dat": str(s.get("loai_dat") or "").strip() or "Đất ở", "trang_thai": "Đã đấu giá", "gia_trung_max": fmt(gmax)}
    for k in ("gia_khoi_diem", "gia_trung_min", "tong_tien_ty"):
        v = num(s.get(k)); r[k] = fmt(v) if v and v > 0 else ""
    for k in ("so_thua", "so_thua_trung", "nguoi_tham_gia"):
        v = num(s.get(k)); r[k] = str(int(v)) if v and v > 0 else ""
    if num(r["gia_trung_min"]) and num(r["gia_trung_min"]) > gmax: r["gia_trung_min"] = ""
    if str(s.get("bo_coc", "")).lower() in ("co", "có"): r["sau_dau_gia"] = "Bỏ cọc"
    if str(s.get("chu_de_chinh", "")).lower() in ("khong", "không"): notes.append("Phiên được nhắc lại trong bài")
    if s.get("ngay_nguyen_van"): notes.append(f'Bài ghi ngày phiên: "{str(s["ngay_nguyen_van"])[:40]}"')
    r["ghi_chu"] = "; ".join(notes)
    return r, ""


def merge_into(p, r, link, src):
    """Gộp phiên r (từ bài khác) vào dòng chờ duyệt p: điền ô trống, thêm nguồn khác."""
    for k in ("xa", "dia_danh_cu", "khu", "so_thua", "so_thua_trung", "gia_khoi_diem", "gia_trung_min", "tong_tien_ty", "nguoi_tham_gia", "sau_dau_gia"):
        if r.get(k) and not p.get(k): p[k] = r[k]
    if link and link not in (p.get("link"), p.get("ghi_chu", "")):
        extra = f"Nguồn khác: {src} {link}"
        if (p.get("ghi_chu") or "").count("Nguồn khác:") < 3: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), extra]))


# ---------------------------------------------------------------- chạy
def process(st, drows, prows, xmap):
    hist = [p for p in prows if (p.get("_cach_lay") or "").startswith(TAG)]
    known_links = {x.get(k, "") for x in drows + prows for k in ("link", "link_ket_qua", "link_thong_bao") if x.get(k)}
    todo = sorted([k for k, v in st["ung_vien"].items() if v.get("loai") == "co" and not v.get("doc")],
                  key=lambda k: st["ung_vien"][k]["pub"], reverse=True)
    n_read = added = merged = 0
    for k in todo:
        if n_read >= MAX_READ or not time_left() or ai_off(): break
        v = st["ung_vien"][k]
        link = D.decode_google(k) if "news.google." in k else k
        if not link or "news.google." in link:
            v["lan"] = v.get("lan", 0) + 1
            if v["lan"] >= 3: v["doc"] = "không giải được link"
            continue
        if link in known_links: v["doc"] = "link đã có"; continue
        try: text = D.body(D.fetch_text(link), v["title"])
        except Exception as ex:
            v["lan"] = v.get("lan", 0) + 1; log(f"  ! không đọc được {link}: {ex}")
            if v["lan"] >= 3: v["doc"] = "không tải được bài"
            continue
        if len(text) < 200: v["doc"] = "bài quá ngắn"; continue
        sess = extract(v["title"], v["pub"], text); n_read += 1
        if sess is None:
            if ai_off(): break                                   # hết hạn mức → bài này để lượt sau
            v["lan"] = v.get("lan", 0) + 1
            if v["lan"] >= 3: v["doc"] = "Gemini lỗi"
            continue
        v["doc"], v["link_goc"] = "xong", link
        src = D.src_name(link, v.get("src") or "")
        for s in sess:
            r, why = check(s, v["pub"], xmap)
            st["thong_ke"][why or "phiên hợp lệ"] = st["thong_ke"].get(why or "phiên hợp lệ", 0) + 1
            if not r: log(f"  · {why}: {v['title'][:70]}"); continue
            dup_d = next((x for x in drows if same_session(x, r)), None)
            if dup_d:
                if num(dup_d.get("gia_trung_max")) or any(p.get("_cap_nhat_id") == dup_d.get("id") for p in prows):
                    log(f"  = trùng phiên đã có {dup_d.get('id')}"); continue
                r["_cap_nhat_id"] = dup_d.get("id", "")      # phiên đã có nhưng chưa có giá → dòng cập nhật
            dup_p = next((x for x in hist if same_session(x, r)), None)
            if dup_p:
                merge_into(dup_p, r, link, src); merged += 1
                log(f"  ⇄ gộp vào dòng chờ duyệt {dup_p.get('xa') or dup_p.get('dia_danh_cu')} {dup_p.get('ngay')}"); continue
            r.update({"link": link, "nguon": src, "link_ket_qua": link, "nguon_ket_qua": src, "vi_tri": "gần đúng", "_khung": "2",
                      "_tieu_de": v["title"], "_ngay_dang": v["pub"], "_ngay_lay": TODAY.isoformat(), "_cach_lay": f"{TAG} (Gemini)",
                      "_tu_dien": ",".join(x for x in ("ngay", "xa", "khu", "so_thua", "so_thua_trung", "gia_khoi_diem", "gia_trung_min",
                                                       "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia") if r.get(x)),
                      "_da_doc": TODAY.isoformat(), "_so_lan_doc": "3"})
            for q in filter(None, [r["xa"], re.sub(r"^xã\s+", "", r["dia_danh_cu"].split(",")[0]).strip()]):
                g = D.geocode(q, r["tinh"]); time.sleep(1.1)
                if g: r["lat"], r["lng"] = g; break
            prows.append(r); hist.append(r); known_links.add(link); added += 1
            log(f"  + {r['tinh']} · {r['xa'] or r['dia_danh_cu']} · {r['ngay']} · trúng cao nhất {r['gia_trung_max']} tr/m²")
        time.sleep(1.5 if D.JINA_KEY else 3.5)
    return n_read, added, merged


def main():
    if not D.AI_KEY:
        log("⚠ Chưa có GEMINI_API_KEY (Settings → Secrets and variables → Actions) – quét lịch sử cần Gemini, dừng."); return
    st = load_json(STATE, {})
    scope = f"{VERSION}|{START}|{END}|{WINDOW}"
    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PEND)
    if st.get("pham_vi") != scope:
        same_ver = str(st.get("pham_vi", "")).startswith(VERSION + "|")
        st = {"pham_vi": scope, "khung_da_quet": [], "ung_vien": st.get("ung_vien", {}) if same_ver else {}, "thong_ke": {}}
        if not same_ver:                                         # bản mới: bỏ các dòng quét lịch sử do bản cũ tạo, quét lại từ đầu
            n0 = len(prows); prows = [p for p in prows if not (p.get("_cach_lay") or "").startswith(TAG)]
            log(f"Bản {VERSION}: xóa {n0 - len(prows)} dòng quét lịch sử cũ trong cho-duyet.csv, quét lại từ đầu")
    xmap = load_json(XAMAP, {}); nl = learn_map(drows, xmap)
    log(f"Khoảng quét {START} → {END} (nhận bài đăng {PUB_FROM} → {PUB_TO}) · bảng xã cũ→mới {len(xmap)} mục (+{nl})")
    gather(st)
    total_w = len(windows())
    log(f"Đã tìm {len(st['khung_da_quet'])}/{total_w} khung · {len(st['ung_vien'])} bài ứng viên")
    classify(st)
    n_read, added, merged = process(st, drows, prows, xmap)
    head = phead or dhead
    for h in dhead + ["link_ket_qua", "nguon_ket_qua", "sau_dau_gia", "_cap_nhat_id", "_khung", "_tieu_de", "_ngay_dang", "_ngay_lay",
                      "_cach_lay", "_tu_dien", "_da_doc", "_so_lan_doc"]:
        if h not in head: head.append(h)
    write_csv(PEND, head, prows)
    save_json(STATE, st); save_json(XAMAP, xmap)
    uv = st["ung_vien"].values()
    chua_loc = sum("loai" not in v for v in uv); chua_doc = sum(v.get("loai") == "co" and not v.get("doc") for v in uv)
    log(f"Xong lượt: đọc {n_read} bài · thêm {added} phiên · gộp {merged} lần · còn {total_w - len(st['khung_da_quet'])} khung chưa tìm, "
        f"{chua_loc} tiêu đề chưa lọc, {chua_doc} bài chờ đọc · Gemini {D._ai_state['calls']} lượt"
        + (" – ĐÃ HẾT HẠN MỨC, lượt sau làm tiếp" if D._ai_state["off"] else ""))
    log("Thống kê phiên: " + ", ".join(f"{k}: {v}" for k, v in sorted(st["thong_ke"].items(), key=lambda x: -x[1])))
    if len(st["khung_da_quet"]) == total_w and not chua_loc and not chua_doc:
        log("✅ ĐÃ QUÉT XONG – có thể tắt workflow 'Quét lịch sử đấu giá'.")


if __name__ == "__main__":
    main()
