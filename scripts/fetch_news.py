#!/usr/bin/env python3
"""
Tự gom tin đấu giá đất từ báo → điền sẵn vào cho-duyet.csv để duyệt trong admin.html.
Auto-collect land-auction news into cho-duyet.csv (pending review in admin.html).

- Chỉ dùng thư viện chuẩn Python (không cần pip install).
- Nếu có biến môi trường ANTHROPIC_API_KEY (GitHub Secret) → bóc tách bằng AI (chính xác hơn);
  không có → bóc tách bằng mẫu chữ (regex), điền được ít ô hơn.
- Không ghi vào data.csv. Chỉ lưu số liệu + link nguồn, không lưu nội dung bài báo.
- Link Google News được giải ra LINK BÁO GỐC (không giải được thì để lần sau thử lại, không lưu link Google).
- Bóc tách dùng chung bộ quy tắc trong scripts/dien_tin_cho_duyet.py (cần đặt file đó cùng thư mục);
  khi có AI thì kết quả AI được bổ sung các ô AI để trống.
"""
import csv, html, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D   # bóc tách đầy đủ + giải link Google News

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
PENDING = os.path.join(ROOT, "cho-duyet.csv")
SEEN = os.path.join(ROOT, "data", "tin-da-xu-ly.json")
COMMUNES = os.path.join(ROOT, "layers", "ranh-gioi-xa.geojson")

VN = timezone(timedelta(hours=7))
TODAY = datetime.now(VN).date()
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "25"))   # giới hạn số bài xử lý mỗi lần (kiểm soát chi phí AI)
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# ---------------- Nguồn tin (sửa / thêm tại đây) ----------------
GN = "https://news.google.com/rss/search?hl=vi&gl=VN&ceid=VN:vi&q="
FEEDS = [
    ("VnExpress", "https://vnexpress.net/rss/bat-dong-san.rss"),
    ("Dân trí", "https://dantri.com.vn/rss/bat-dong-san.rss"),
    ("Google News", GN + urllib.parse.quote('"đấu giá" đất "Hà Nội" when:14d')),
    ("Google News", GN + urllib.parse.quote('"đấu giá" "quyền sử dụng đất" (Hà Nội OR "Bắc Ninh" OR "Hưng Yên" OR "Phú Thọ") when:14d')),
    ("Google News", GN + urllib.parse.quote('"trúng đấu giá" đất "triệu đồng/m2" when:14d')),
]
PROVINCES = ["Hà Nội", "Bắc Ninh", "Hưng Yên", "Phú Thọ", "Thái Nguyên", "Ninh Bình"]
# tên huyện/quận cũ của Hà Nội hay xuất hiện trong tin đấu giá → nhận ra là Hà Nội
HN_OLD = ["Đông Anh", "Mê Linh", "Sóc Sơn", "Gia Lâm", "Thanh Oai", "Thanh Trì", "Hoài Đức", "Quốc Oai", "Thạch Thất", "Chương Mỹ",
          "Ba Vì", "Phúc Thọ", "Đan Phượng", "Sơn Tây", "Ứng Hòa", "Mỹ Đức", "Phú Xuyên", "Thường Tín", "Long Biên", "Hà Đông", "Bắc Từ Liêm", "Nam Từ Liêm"]
SRC_NAME = {"vnexpress.net": "VnExpress", "dantri.com.vn": "Dân trí", "baodauthau.vn": "Báo Đấu thầu", "congly.vn": "Báo Công lý", "cafef.vn": "CafeF",
            "cafeland.vn": "CafeLand", "vietnamnet.vn": "VietNamNet", "tuoitre.vn": "Tuổi Trẻ", "thanhnien.vn": "Thanh Niên", "laodong.vn": "Lao Động",
            "tienphong.vn": "Tiền Phong", "nld.com.vn": "Người Lao Động", "znews.vn": "Znews", "vtv.vn": "VTV", "hanoimoi.vn": "Hà Nội Mới",
            "kinhtedothi.vn": "Kinh tế & Đô thị", "batdongsan.com.vn": "Batdongsan.com.vn", "dgts.moj.gov.vn": "Cổng ĐGTS quốc gia"}

COLS = ["id", "ngay", "tinh", "xa", "dia_danh_cu", "khu", "loai_dat", "so_thua", "so_thua_trung", "gia_khoi_diem", "gia_trung_min", "gia_trung_max",
        "tong_tien_ty", "nguoi_tham_gia", "trang_thai", "lat", "lng", "vi_tri", "nguon", "link", "ghi_chu"]
META = ["link_thong_bao", "_cap_nhat_id", "_tieu_de", "_ngay_lay", "_cach_lay", "_tu_dien", "_da_doc"]
RESULT_KEYS = ["so_thua_trung", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]   # cột phụ, admin bỏ đi khi duyệt

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def log(*a):
    print(*a, flush=True)


def fetch(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept-Language": "vi,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        cs = r.headers.get_content_charset() or "utf-8"
        return raw.decode(cs, "replace"), r.geturl()


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"^(xa|phuong|thi tran|dac khu)\s+", "", s.strip())
    return re.sub(r"\s+", " ", s)


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style|noscript|figure|figcaption|table)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def article_text(page):
    """Lấy phần chữ chính của bài: các đoạn <p>, bỏ đoạn quá ngắn."""
    body = page
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
    if m and len(m.group(1)) > 1500:
        body = m.group(1)
    ps = [strip_tags(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)]
    txt = "\n".join(p for p in ps if len(p) > 40)
    return nfc(txt)[:9000]


def relevant(text):
    f = fold(text)
    if "dau gia" not in f:
        return False
    if not re.search(r"\b(dat|thua|lo dat|quyen su dung)\b", f):
        return False
    if re.search(r"\b(o to|bien so|xe may|khoang san|tai san thi hanh an)\b", f) and "thua dat" not in f:
        return False
    return any(fold(p) in f for p in PROVINCES + HN_OLD)


# ---------------- Bóc tách bằng regex (khi không có API key) ----------------
NUM = r"(\d{1,3}(?:[.,]\d{1,3})*(?:[.,]\d+)?)"


def vnum(x):
    x = x.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", x):        # 1.234 → 1234
        return x.replace(".", "")
    return x.replace(".", "").replace(",", ".") if re.fullmatch(r"\d{1,3}(\.\d{3})*,\d+", x) else x.replace(",", ".")


def find_price(text, pats):
    for p in pats:
        m = re.search(p + r"[^.\d]{0,60}?" + NUM + r"\s*(?:triệu|tr)\s*(?:đồng)?\s*/\s*m", text, re.I)
        if m:
            return vnum(m.group(1))
    return ""


def find_date(text, pub):
    yr = pub.year if pub else TODAY.year
    for m in re.finditer(r"(?:ngày|vào|sáng|chiều)\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", text, re.I):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or yr)
        try:
            return datetime(y, mo, d).date()
        except ValueError:
            continue
    return None


def regex_extract(title, text, pub, link=""):
    """Bóc tách không cần AI: dùng bộ quy tắc đầy đủ của dien_tin_cho_duyet, rồi bù bằng quy tắc cũ."""
    ex = D.extract(text, {"_tieu_de": title, "link": link})
    old = regex_extract_old(title, text, pub)
    r = old[0] if old else {k: "" for k in COLS}
    for k, v in ex.items():
        if k in COLS and v: r[k] = v
    if not r.get("tinh"):
        r["tinh"] = ex.get("tinh", "")
    d = r.get("ngay")
    if not ex.get("trang_thai"):
        r["trang_thai"] = "Sắp đấu giá" if not d or d >= TODAY.isoformat() else "Chờ kết quả"
    return [r] if r.get("tinh") else []


def regex_extract_old(title, text, pub):
    t = title + ". " + text
    r = {k: "" for k in COLS}
    for p in PROVINCES:
        if fold(p) in fold(t):
            r["tinh"] = p
            break
    if not r["tinh"] and any(fold(d) in fold(t) for d in HN_OLD):
        r["tinh"] = "Hà Nội"
    m = re.search(r"\b(xã|phường)\s+([A-ZĐ][\wÀ-ỹ]*(?:\s+[A-ZĐ][\wÀ-ỹ]*){0,3})", t)
    if m:
        r["xa"] = m.group(2).strip()
    m = re.search(r"\bhuyện\s+([A-ZĐ][\wÀ-ỹ]*(?:\s+[A-ZĐ][\wÀ-ỹ]*){0,2})", t)
    if m:
        r["dia_danh_cu"] = "huyện " + m.group(1)
    m = re.search(NUM + r"\s+(?:thửa|lô)(?:\s+đất)?", t)
    if m:
        r["so_thua"] = vnum(m.group(1))
    m = re.search(NUM + r"\s+(?:người|khách hàng|hồ sơ)[^.]{0,30}(?:tham gia|tham dự|đăng ký)", t)
    if m:
        r["nguoi_tham_gia"] = vnum(m.group(1))
    r["gia_khoi_diem"] = find_price(t, [r"giá khởi điểm"])
    r["gia_trung_max"] = find_price(t, [r"(?:trúng|giá)[^.]{0,30}cao nhất", r"cao nhất"])
    r["gia_trung_min"] = find_price(t, [r"(?:trúng|giá)[^.]{0,30}thấp nhất", r"thấp nhất"])
    m = re.search(r"tổng[^.]{0,40}?" + NUM + r"\s*tỷ", t, re.I)
    if m:
        r["tong_tien_ty"] = vnum(m.group(1))
    d = find_date(t, pub)
    if d:
        r["ngay"] = d.isoformat()
    won = bool(r["gia_trung_max"]) or re.search(r"trúng đấu giá|kết quả đấu giá", t, re.I)
    r["trang_thai"] = "Đã đấu giá" if won else ("Sắp đấu giá" if d and d >= TODAY else "Chờ kết quả" if d else "Sắp đấu giá")
    r["loai_dat"] = "Đất ở"
    return [r] if r["tinh"] else []


# ---------------- Bóc tách bằng AI (Claude API) ----------------
PROMPT = """Bạn là trợ lý trích xuất dữ liệu đấu giá quyền sử dụng đất từ một bài báo tiếng Việt.
Chỉ lấy các phiên đấu giá ĐẤT tại: {prov}. Bỏ qua đấu giá biển số, xe, tài sản thi hành án không phải đất, và phiên ở tỉnh khác.
Một bài có thể có nhiều phiên (nhiều khu/nhiều xã) → mỗi phiên một mục.
Trả về DUY NHẤT một JSON dạng {{"phien":[{{...}}]}} (không giải thích), mỗi mục có các khóa (chuỗi, không biết thì để ""):
ngay (YYYY-MM-DD, ngày tổ chức phiên; năm mặc định {yr}), tinh (một trong: {prov}), xa (tên xã/phường MỚI sau sáp nhập 7/2025 nếu bài nêu, không kèm chữ "xã"/"phường"),
dia_danh_cu (địa danh cũ, vd "xã Kim Nỗ, huyện Đông Anh"), khu (tên khu/điểm đấu giá), loai_dat ("Đất ở" | "Đất thương mại dịch vụ" | "Đất sản xuất kinh doanh" | "Đất nông nghiệp"),
so_thua (số thửa đưa ra đấu giá), so_thua_trung (số thửa trúng), gia_khoi_diem, gia_trung_min, gia_trung_max (đều quy ra TRIỆU đồng/m², số thập phân dùng dấu chấm),
tong_tien_ty (tổng tiền trúng, tỷ đồng), nguoi_tham_gia (số người/hồ sơ tham gia),
trang_thai ("Sắp đấu giá" nếu phiên chưa diễn ra; "Đã đấu giá" nếu đã có kết quả; "Chờ kết quả" nếu đã diễn ra mà chưa có kết quả; "Hủy/bỏ cọc" nếu bị hủy hoặc người trúng bỏ cọc),
ghi_chu (ngắn gọn, các ý cách nhau bằng "; ": khoảng giá khởi điểm nếu có nhiều mức, tổng diện tích, tổng giá khởi điểm, hạn nộp hồ sơ, giờ phiên, điểm đáng chú ý; tự viết lại, không chép nguyên văn bài).
PHẢI điền ĐỦ mọi khóa mà bài có thông tin (đọc cả bảng số liệu trong bài); giá trị ngắn gọn, đúng như bài nêu, không suy đoán.
Nếu bài không có phiên đấu giá đất phù hợp thì trả {{"phien":[]}}.
Hôm nay là {today}. Bài báo được đăng ngày {pub}.

TIÊU ĐỀ: {title}
NỘI DUNG:
{text}"""


def ai_extract(title, text, pub):
    body = json.dumps({"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": PROMPT.format(
        prov=", ".join(PROVINCES), yr=(pub or TODAY).year, today=TODAY.isoformat(), pub=(pub.isoformat() if pub else "không rõ"),
        title=title, text=text[:12000])}]}).encode()
    raw, _ = fetch("https://api.anthropic.com/v1/messages", data=body, timeout=90, headers={
        "x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    out = "".join(b.get("text", "") for b in json.loads(raw).get("content", []))
    m = re.search(r"\{.*\}", out, re.S)
    items = json.loads(m.group(0)).get("phien", []) if m else []
    rows = []
    for it in items:
        r = {k: nfc(str(it.get(k, "") or "")).strip() for k in COLS}
        if r["tinh"] in PROVINCES:
            for k in ["so_thua", "so_thua_trung", "gia_khoi_diem", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]:
                if r[k] and not re.fullmatch(r"\d+(\.\d+)?", r[k]):
                    m2 = re.search(r"\d+(?:[.,]\d+)?", r[k])
                    r[k] = vnum(m2.group(0)) if m2 else ""
            if r["ngay"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["ngay"]):
                r["ngay"] = ""
            rows.append(r)
    return rows


# ---------------- Tọa độ: tâm xã theo lớp ranh giới ----------------
def load_communes():
    try:
        gj = json.load(open(COMMUNES, encoding="utf-8"))
    except Exception:
        log("  (chưa có layers/ranh-gioi-xa.geojson → không tự đặt tọa độ)")
        return []
    out = []
    for f in gj.get("features", []):
        g, p = f.get("geometry") or {}, f.get("properties") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
        best, ba = None, 0
        for poly in polys:
            ring = poly[0] if poly else []
            a = cx = cy = 0
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                c = x1 * y2 - x2 * y1
                a += c; cx += (x1 + x2) * c; cy += (y1 + y2) * c
            if abs(a) > ba and a:
                ba, best = abs(a), (cy / (3 * a), cx / (3 * a))
        if best:
            out.append((fold(p.get("name")), p.get("tinh", ""), best))
    return out


def locate(r, communes):
    if not r["xa"]:
        return
    hits = [c for c in communes if c[0] == fold(r["xa"])]
    if len(hits) > 1 and r["tinh"]:
        hits = [c for c in hits if fold(c[1]) == fold(r["tinh"])] or hits
    if len(hits) == 1:
        r["lat"], r["lng"] = f"{hits[0][2][0]:.5f}", f"{hits[0][2][1]:.5f}"
        r["vi_tri"] = "gần đúng"


# ---------------- CSV ----------------
def read_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        return list(rd.fieldnames or []), list(rd)


def write_csv(path, head, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in head})
    open(path, "w", encoding="utf-8", newline="").write(buf.getvalue())


def dup_key(r):
    return (fold(r.get("tinh")), fold(r.get("xa")) or fold(r.get("khu")), (r.get("ngay") or "")[:10])


# ---------------- Chạy ----------------
def parse_pub(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s.strip(), fmt).astimezone(VN).date()
        except Exception:
            pass
    return None


def read_feeds():
    items = []
    for name, url in FEEDS:
        try:
            raw, _ = fetch(url)
            root = ET.fromstring(raw.encode("utf-8"))
            n = 0
            for it in root.iter("item"):
                title = nfc(html.unescape((it.findtext("title") or "").strip()))
                link = (it.findtext("link") or "").strip()
                desc = strip_tags(it.findtext("description") or "")
                src = it.find("source")
                srcname = src.text.strip() if src is not None and src.text else name
                items.append({"title": title, "link": link, "desc": nfc(desc), "pub": parse_pub(it.findtext("pubDate") or ""), "src": srcname})
                n += 1
            log(f"✓ {name}: {n} tin")
        except Exception as e:
            log(f"✗ {name}: {e}")
    return items


def main():
    seen = {}
    try:
        seen = json.load(open(SEEN, encoding="utf-8"))
    except Exception:
        pass
    cutoff = (TODAY - timedelta(days=200)).isoformat()
    seen = {u: d for u, d in seen.items() if d >= cutoff}

    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PENDING)
    head = COLS + [h for h in dhead if h not in COLS] + META
    known_links = {r.get("link", "").strip() for r in drows + prows if r.get("link")}
    known_keys = {dup_key(r) for r in drows + prows if r.get("ngay") and (r.get("xa") or r.get("khu"))}
    data_by_key = {dup_key(r): r for r in drows if r.get("ngay") and (r.get("xa") or r.get("khu"))}
    waiting = {p.get("_cap_nhat_id") for p in prows if p.get("_cap_nhat_id")}
    communes = load_communes()
    log(f"Chế độ bóc tách: {'AI (' + MODEL + ')' if API_KEY else 'regex (chưa có ANTHROPIC_API_KEY)'}")

    items, byl = read_feeds(), {}
    for it in items:                      # bỏ tin trùng link / trùng tiêu đề
        byl.setdefault(it["link"], it)
    titles, todo = set(), []
    for it in byl.values():
        k = fold(it["title"])[:80]
        if it["link"] in seen or it["link"] in known_links or k in titles:
            continue
        titles.add(k)
        if relevant(it["title"] + " " + it["desc"]):
            todo.append(it)
        else:
            seen[it["link"]] = TODAY.isoformat()
    log(f"Tin mới liên quan: {len(todo)} (xử lý tối đa {MAX_ARTICLES})")

    added = 0
    for it in todo[:MAX_ARTICLES]:
        link, title = it["link"], it["title"]
        if "news.google." in link:        # Google News → link báo gốc
            real = D.decode_google(link)
            if not real:
                log(f"  ! chưa giải được link gốc, để lần sau: {title[:70]}")
                continue                   # không đánh dấu đã xử lý → lần sau thử lại
            link = real
            if link in known_links or link in seen:
                seen[it["link"]] = TODAY.isoformat(); continue
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title) if "news.google." in it["link"] else title   # bỏ " - Tên báo" Google thêm vào cuối
        text = ""
        try:                               # r.jina.ai đọc bài thành chữ sạch (giữ cả bảng số liệu); lỗi thì tự bóc HTML
            text = D.body(D.fetch_text(link), title)
        except Exception as e:
            log(f"  ! không mở được bài: {e}")
        if len(text) < 300:
            try:
                page, final = fetch(link); text = article_text(page) if len(article_text(page)) > len(text) else text
            except Exception:
                pass
        if len(text) < 300:               # không đọc được bài → dùng tóm tắt RSS
            text = it["desc"]
        host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        try:
            rows = ai_extract(title, text, it["pub"]) if API_KEY else regex_extract(title, text, it["pub"], link)
            if API_KEY and len(rows) == 1:  # AI bỏ sót ô nào thì bù bằng quy tắc
                ex = D.extract(text, {"_tieu_de": title, "link": link})
                for k, v in ex.items():
                    if k in COLS and v and not rows[0].get(k): rows[0][k] = v
        except Exception as e:
            log(f"  ! lỗi bóc tách ({title[:60]}): {e}")
            continue                       # không đánh dấu đã xử lý → lần sau thử lại
        seen[it["link"]] = seen[link] = TODAY.isoformat()
        for r in rows:
            if r.get("ngay") and (r.get("xa") or r.get("khu")) and dup_key(r) in known_keys:
                old = data_by_key.get(dup_key(r))
                # bài KẾT QUẢ của một phiên đã có mà chưa có giá → dòng "cập nhật kết quả" (duyệt sẽ ghi vào phiên cũ)
                if old and not old.get("gia_trung_max") and (r.get("gia_trung_max") or r.get("so_thua_trung")) and old.get("id") not in waiting:
                    p = dict(old)
                    for k in RESULT_KEYS:
                        if r.get(k): p[k] = r[k]
                    p["trang_thai"] = "Đã đấu giá"
                    if r.get("ghi_chu"):
                        add = [x for x in r["ghi_chu"].split("; ") if x not in (p.get("ghi_chu") or "")]
                        if add: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), *add]))
                    p["link_thong_bao"] = old.get("link_thong_bao") or old.get("link", "")
                    p.update({"link": link, "nguon": SRC_NAME.get(host) or D.src_name(link), "_cap_nhat_id": old.get("id", ""), "_tieu_de": title,
                              "_ngay_lay": TODAY.isoformat(), "_cach_lay": "kết quả phiên đã có", "_da_doc": TODAY.isoformat(),
                              "_tu_dien": ",".join(k for k in RESULT_KEYS + ["trang_thai"] if r.get(k) or k == "trang_thai")})
                    prows.append(p); waiting.add(old.get("id")); added += 1
                    log(f"  ↻ kết quả cho phiên {old.get('id')}: {r.get('gia_trung_max') or '?'} tr/m²")
                    continue
                log(f"  = trùng phiên đã có: {r.get('xa') or r.get('khu')} {r.get('ngay')}")
                continue
            locate(r, communes)
            r.update({"link": link, "nguon": SRC_NAME.get(host) or D.src_name(link, it["src"] if it["src"] != "Google News" else host),
                      "_tieu_de": title, "_ngay_lay": TODAY.isoformat(), "_cach_lay": "AI" if API_KEY else "quy tắc", "_da_doc": TODAY.isoformat()})
            if not r.get("vi_tri"):
                r["vi_tri"] = "gần đúng"
            prows.append(r)
            known_keys.add(dup_key(r))
            added += 1
            log(f"  + {r.get('tinh')} · {r.get('xa') or r.get('khu') or '?'} · {r.get('ngay') or '?'} · {r.get('trang_thai')}")
        time.sleep(1)

    write_csv(PENDING, head if not phead else phead + [h for h in head if h not in phead], prows)
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    log(f"Xong: thêm {added} dòng chờ duyệt (tổng {len(prows)} dòng trong cho-duyet.csv).")


if __name__ == "__main__":
    sys.exit(main())
