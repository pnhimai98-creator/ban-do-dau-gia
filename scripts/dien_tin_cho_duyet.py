#!/usr/bin/env python3
"""
Điền đầy đủ dữ liệu cho tin chờ duyệt  /  Fill in pending auction rows from the source articles

Chạy SAU scripts/fetch_news.py (workflow "Gom tin đấu giá từ báo"):
    python scripts/dien_tin_cho_duyet.py

Với mỗi dòng trong cho-duyet.csv còn ô trống, script đọc lại bài báo gốc (cột link) và điền
những gì bài có vào Ô CÒN TRỐNG (không ghi đè ô đã có):
  ngày đấu giá, tỉnh, xã/phường mới, địa danh cũ (xã…, huyện… cũ), khu đấu giá / thôn,
  loại đất, số thửa, số thửa trúng, giá khởi điểm, giá trúng thấp nhất / cao nhất (tr/m²),
  tổng tiền (tỷ), người tham gia, trạng thái (đã có giá trúng → "Đã đấu giá"),
  ghi chú (khoảng giá khởi điểm, tổng giá khởi điểm, tổng diện tích, hạn nộp hồ sơ, giờ phiên),
  tọa độ tâm xã (OpenStreetMap Nominatim) nếu chưa có.
Các ô được điền ghi vào cột _tu_dien (để admin tô màu cho bạn soát lại). Dòng đã đọc được
đánh dấu ở cột _da_doc nên lần chạy sau không đọc lại.

Không cần thư viện ngoài (chỉ Python 3 chuẩn).
"""
import csv, html, io, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import date
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEND = os.path.join(ROOT, "cho-duyet.csv")
UA = {"User-Agent": "Mozilla/5.0 (ban-do-dau-gia; +https://github.com)"}
KEYF = ["ngay", "xa", "dia_danh_cu", "khu", "so_thua", "gia_khoi_diem", "gia_trung_max",
        "so_thua_trung", "tong_tien_ty", "nguoi_tham_gia"]

# ------------------------------------------------------------------ tải bài
class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "button"}
    BLOCK = {"p", "h1", "h2", "h3", "li", "td", "br", "div", "tr"}
    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0; self.title = ""; self._t = False
    def handle_starttag(self, tag, a):
        if tag in self.SKIP: self.skip += 1
        if tag == "title": self._t = True
        if tag in self.BLOCK: self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1
        if tag == "title": self._t = False
    def handle_data(self, d):
        if self._t: self.title += d
        elif not self.skip: self.out.append(d)

def get(url, timeout=40, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(); cs = r.headers.get_content_charset() or "utf-8"
    return raw.decode(cs, errors="replace")

def fetch_text(url):
    """Ưu tiên r.jina.ai (trả về chữ sạch của bài), dự phòng tải thẳng trang rồi bỏ thẻ HTML."""
    for attempt in range(2):
        try:
            t = get("https://r.jina.ai/" + url, headers={"X-Return-Format": "markdown"})
            if len(t) > 300: return t
        except Exception as ex:
            print(f"    jina lỗi: {ex}"); time.sleep(3)
    h = get(url)
    m = re.search(r"<article\b.*?</article>", h, re.S | re.I)
    p = _Text(); p.feed(m.group(0) if m and len(m.group(0)) > 1500 else h)
    return "Title: " + html.unescape(p.title.strip()) + "\n" + html.unescape("".join(p.out))

# ------------------------------------------------------------------ link Google News → link báo gốc
SRC_NAME = {"vnexpress.net": "VnExpress", "dantri.com.vn": "Dân trí", "baodauthau.vn": "Báo Đấu thầu", "congly.vn": "Báo Công lý", "cafef.vn": "CafeF",
            "cafeland.vn": "CafeLand", "vietnamnet.vn": "VietNamNet", "tuoitre.vn": "Tuổi Trẻ", "thanhnien.vn": "Thanh Niên", "laodong.vn": "Lao Động",
            "tienphong.vn": "Tiền Phong", "nld.com.vn": "Người Lao Động", "znews.vn": "Znews", "vtv.vn": "VTV", "hanoimoi.vn": "Hà Nội Mới",
            "kinhtedothi.vn": "Kinh tế & Đô thị", "batdongsan.com.vn": "Batdongsan.com.vn", "dgts.moj.gov.vn": "Cổng ĐGTS quốc gia",
            "hanoionline.vn": "Hanoionline", "vov.vn": "VOV", "vietnamfinance.vn": "VietnamFinance", "baomoi.com": "Báo Mới",
            "nhandan.vn": "Nhân Dân", "baochinhphu.vn": "Báo Chính phủ", "vneconomy.vn": "VnEconomy", "plo.vn": "Pháp luật TP.HCM",
            "kinhtemoi.vn": "Kinh tế Mới", "baobacninhtv.vn": "Báo Bắc Ninh", "baohungyen.vn": "Báo Hưng Yên", "baophutho.vn": "Báo Phú Thọ"}

def src_name(url, fallback=""):
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    return SRC_NAME.get(host, fallback or host)

def decode_google(url):
    """news.google.com/rss/articles/<id> → link bài gốc.
    Google không còn chuyển hướng thẳng; phải lấy chữ ký trên trang bài rồi hỏi API batchexecute."""
    m = re.search(r"news\.google\.com/(?:rss/)?(?:articles|read)/([A-Za-z0-9_\-]+)", url or "")
    if not m: return url
    gid = m.group(1)
    # 1) dạng cũ: link nằm sẵn trong mã base64
    try:
        import base64
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
        mm = re.search(rb"https?://[\x21-\x7e]+", raw)
        if mm and b"news.google" not in mm.group(0): return mm.group(0).decode("ascii", "ignore").split("\xd2")[0]
    except Exception:
        pass
    # 2) dạng mới: chữ ký (data-n-a-sg) + thời điểm (data-n-a-ts) → batchexecute
    for page_url in (f"https://news.google.com/articles/{gid}", f"https://news.google.com/rss/articles/{gid}"):
        try:
            h = get(page_url, timeout=25)
            sg = re.search(r'data-n-a-sg="([^"]+)"', h); ts = re.search(r'data-n-a-ts="([^"]+)"', h)
            if not (sg and ts): continue
            inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                     f'"{gid}",{ts.group(1)},"{sg.group(1)}"]')
            data = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
            req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=data.encode(),
                                         headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            with urllib.request.urlopen(req, timeout=25) as r: resp = r.read().decode("utf-8", "replace")
            for line in resp.splitlines():
                if not line.startswith("[["): continue
                for item in json.loads(line):
                    if isinstance(item, list) and len(item) > 2 and item[0] == "wrb.fr" and item[2]:
                        real = json.loads(item[2])[1]
                        if isinstance(real, str) and real.startswith("http"): return real
        except Exception as ex:
            print(f"    giải link Google News lỗi: {ex}")
    return ""

# ------------------------------------------------------------------ bóc tách
NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
M2 = r"\s*/\s*m\s*(?:2|²)"
TR = r"\s*(?:triệu|tr\.?)\s*(?:đồng|đ)?"
WORD = r"[^\W\d_]+"
NAMEW = rf"{WORD}(?:[ -]{WORD}){{0,4}}"
HIST = re.compile(r"so với|trước đó|năm ngoái|cùng kỳ|đỉnh|năm 20(1\d|2[0-4])", re.I)

def cap(name):
    """Giữ các từ viết hoa đầu tiên: 'Thiên Lộc thành phố' → 'Thiên Lộc'."""
    out = []
    for w in re.split(r"(?<=\S)([ -])", name.strip()):
        if w in (" ", "-"): out.append(w); continue
        if w and w[0].isupper(): out.append(w)
        else: break
    return "".join(out).strip(" -")

def vn(s, kind="price"):
    s = str(s).strip().rstrip(".,")
    if "." in s and "," in s:
        return float(s.replace(".", "").replace(",", ".") if s.index(".") < s.index(",") else s.replace(",", ""))
    if "," in s:
        return float(s.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3}){2,}", s) else float(s.replace(",", "."))
    if "." in s:
        thou = re.fullmatch(r"\d{1,3}(\.\d{3})+", s)
        return float(s.replace(".", "")) if thou and (kind in ("m2", "dong") or s.count(".") > 1) else float(s)
    return float(s)

def r3(x): return round(x, 3)
def fmt(x): return f"{r3(x):g}".replace(".", ",")
def iso(d, m, y): return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

def body(text, title):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_#>`|]+", " ", t); t = re.sub(r"[ \t ]+", " ", t)
    i = t.find("Markdown Content:")
    if i >= 0: t = t[i + 17:]
    k = (title or "")[:40].strip()
    if len(k) > 15 and k in t: t = t[t.index(k):]
    m = re.search(r"\n\s*(Tin liên quan|Bài liên quan|Tin cùng chuyên mục|Xem thêm|Đọc thêm|Tin mới nhất|Có thể bạn quan tâm|Bình luận|TIN LIÊN QUAN|Tags?:|Từ khóa)", t)
    if m and m.start() > 300: t = t[:m.start()]
    return t[:9000]

def sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+(?=[A-ZÀ-ỸĐ0-9\"“(])|\n+", t) if len(s.strip()) > 3]

def date_from_url(u):
    m = re.search(r"ngay-(\d{7,8})-", u or "")
    if not m: return ""
    s = m.group(1)
    for a, b in ([(2, 2)] if len(s) == 8 else [(1, 2), (2, 1)]):
        d, mo, y = int(s[:a]), int(s[a:a + b]), int(s[a + b:])
        if 1 <= d <= 31 and 1 <= mo <= 12 and y > 2000: return iso(d, mo, y)
    return ""

PRICE = re.compile(rf"(?:từ\s*)?({NUM})(?:{TR}(?:{M2})?)?\s*(?:đến|tới|–|-|—)\s*({NUM}){TR}{M2}|({NUM}){TR}{M2}|(\d{{1,3}}(?:\.\d{{3}}){{2,}})\s*(?:đồng|đ|VNĐ|VND){M2}", re.I)

def extract(raw, row):
    title = row.get("_tieu_de") or ""
    t = body(raw, title)
    if not title:
        m = re.search(r"^Title:\s*(.+)$", raw, re.M); title = m.group(1).strip() if m else ""
    alltxt = title + ". " + t; S = sentences(alltxt); out = {}; note = []
    starts, wins, wmax, wmin = [], [], [], []
    for s in S:
        has_win = re.search(r"trúng|thành công|kết quả", s, re.I); has_start = re.search(r"khởi điểm", s, re.I)
        if not has_win and not has_start: continue
        for m in PRICE.finditer(s):
            if m.group(1): vals = [vn(m.group(1)), vn(m.group(2))]
            elif m.group(3): vals = [vn(m.group(3))]
            else: vals = [vn(m.group(4), "dong") / 1e6]
            vals = [x / 1000 if x > 2000 and not m.group(4) else x for x in vals]
            vals = [x for x in vals if 0.2 < x < 2000]
            if not vals: continue
            pre = s[max(0, m.start() - 90):m.start()].lower()
            ks, kw = pre.rfind("khởi điểm"), max(pre.rfind("trúng"), pre.rfind("thành công"))
            kh, kl = pre.rfind("cao nhất"), pre.rfind("thấp nhất")
            if has_start and ks >= max(kw, kh, kl): starts += vals; continue
            if not has_win or HIST.search(s):
                if has_start and ks >= 0: starts += vals
                continue
            if kh > kl and kh >= 0: wmax += vals
            elif kl >= 0: wmin += vals
            elif kw >= 0 or ks < 0: wins += vals
    if starts:
        lo, hi = min(starts), max(starts); out["gia_khoi_diem"] = r3(lo)
        if hi > lo: note.append(f"KĐ {fmt(lo)}–{fmt(hi)} tr/m²")
    hv, lv = wmax + wins, wmin + (wins if len(wins) > 1 else [])
    if hv: out["gia_trung_max"] = r3(max(hv))
    if lv and (not hv or min(lv) < max(hv)): out["gia_trung_min"] = r3(min(lv))
    # số thửa
    tt = title + " " + alltxt
    if (m := re.search(r"(\d+)\s*/\s*(\d+)\s*(?:thửa|lô|suất)", alltxt, re.I)): out["so_thua_trung"], out["so_thua"] = int(m[1]), int(m[2])
    if "so_thua_trung" not in out and (m := re.search(r"(\d+)\s*(?:thửa|lô|suất)(?:\s*đất)?[^.]{0,40}?(?:trúng đấu giá|đấu giá thành công|có người trúng|được trúng|thành công)", alltxt, re.I)): out["so_thua_trung"] = int(m[1])
    if "so_thua_trung" not in out and (m := re.search(r"(?:trúng đấu giá|thành công)\s*(?:đối với\s*|cả\s*|toàn bộ\s*)?(\d+)\s*(?:/\s*\d+\s*)?(?:thửa|lô|suất)", alltxt, re.I)): out["so_thua_trung"] = int(m[1])
    if "so_thua" not in out and (m := re.search(r"(?:quyền sử dụng(?: đất)?(?: đối với)?|đấu giá|gồm|tổng số|tổng cộng|đưa ra)\s*(\d+)\s*(?:thửa|lô|suất)", tt, re.I)): out["so_thua"] = int(m[1])
    if "so_thua" not in out and (m := re.search(r"(\d+)\s*(?:thửa|lô|suất)\s*đất", tt, re.I)): out["so_thua"] = int(m[1])
    if out.get("so_thua_trung", 0) > out.get("so_thua", 10**6): out.pop("so_thua_trung")
    # người tham gia
    m = re.search(r"(\d+)\s*(?:người|khách hàng|hồ sơ|nhà đầu tư|cá nhân|lượt khách)[^.\d]{0,25}?(?:tham gia|tham dự|đăng ký|nộp hồ sơ|đủ điều kiện)", alltxt, re.I) \
        or re.search(r"(?:có|thu hút|với)\s*(\d+)\s*(?:người|khách hàng|hồ sơ|nhà đầu tư)", alltxt, re.I)
    if m: out["nguoi_tham_gia"] = int(m[1])
    # tổng tiền
    for s in S:
        mm = re.search(rf"(?:tổng|thu về|thu|mang về|đạt)[^.]{{0,70}}?({NUM})\s*tỷ", s, re.I)
        if not mm: continue
        v = vn(mm[1])
        if re.search("khởi điểm", s, re.I): note.append(f"Tổng KĐ {fmt(v)} tỷ"); continue
        if re.search(r"trúng|thành công|thu về|mang về|tiền sử dụng đất", s, re.I) and not HIST.search(s):
            out["tong_tien_ty"] = r3(v); break
    # ngày
    d = date_from_url(row.get("link"))
    if not d and (m := re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})", title)): d = iso(m[1], m[2], m[3])
    BAD = r"hạn|nộp|bán hồ sơ|đặt trước|xem tài sản|niêm yết|đăng ký|tiếp nhận|phát hành"
    if not d:
        for s in S:
            if not re.search(r"đấu giá|phiên", s, re.I) or re.search(BAD, s, re.I): continue
            if (m := re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})", s)): d = iso(m[1], m[2], m[3]); break
    if not d:
        ys = [int(y) for y in re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)", " ".join(x for x in S if not HIST.search(x))) if int(y) <= date.today().year + 1]
        y = max(ys) if ys else date.today().year
        for s in S:
            if not re.search(r"đấu giá|phiên", s, re.I) or re.search(BAD, s, re.I): continue
            if (m := re.search(r"ngày\s*(\d{1,2})\s*/\s*(\d{1,2})(?!\s*/\s*\d)", s, re.I)) and int(m[2]) <= 12: d = iso(m[1], m[2], y); break
    if d: out["ngay"] = d
    # địa danh
    if (m := re.search(rf"(?:xã|phường|đặc khu)\s+({NAMEW})\s*,\s*(?:thành phố|TP\.?|tỉnh)\s+({NAMEW})", alltxt)) and cap(m[1]):
        out["xa"] = cap(m[1]); out["tinh"] = "Hà Nội" if cap(m[2]).startswith("Hà Nội") else cap(m[2])
    elif (m := re.search(rf"(?:[xX]ã|[pP]hường|[đĐ]ặc khu)\s+({NAMEW})", title)) and cap(m[1]):
        out["xa"] = cap(m[1])
    olds = []
    for m in re.finditer(rf"(xã|phường|thị trấn)\s+({NAMEW})\s*,\s*(huyện|quận|thị xã)\s+({NAMEW})", alltxt):
        a, b = cap(m[2]), cap(m[4])
        if a and b and f"{m[1]} {a}, {m[3]} {b}" not in olds: olds.append(f"{m[1]} {a}, {m[3]} {b}")
    if olds: out["dia_danh_cu"] = "; ".join(olds[:3])
    elif (m := re.search(rf"(huyện|quận|thị xã)\s+({NAMEW})\s*\(cũ\)", alltxt)) and cap(m[2]): out["dia_danh_cu"] = f"{m[1]} {cap(m[2])}"
    if "tinh" not in out and (m := re.search(r"(?:thành phố|TP\.?|tỉnh)\s+(Hà Nội|Bắc Ninh|Hưng Yên|Phú Thọ|Thái Nguyên|Ninh Bình|Hải Phòng|Quảng Ninh)", alltxt)): out["tinh"] = m[1]
    # khu / thôn
    if (m := re.search(r"((?:Khu|khu|Điểm|điểm|Ô đất|ô đất|Lô đất|lô đất|Cụm|cụm)\s+(?!vực)[^.;:\n]{2,90}?)(?=\s*(?:,\s*(?:xã|phường|thị trấn)\s|\.|;|\n|\(|$))", t)) \
            and not re.match(r"(khu)\s+(vực|đất này|đất trên)|\S+\s+đất\s+tại\s+(xã|phường|thị trấn)\s", m[1], re.I):
        out["khu"] = re.sub(r"\s+", " ", m[1]).strip()
    if (m := re.search(rf"(?:thôn|xóm|tổ dân phố|TDP|làng)\s+({NAMEW})", alltxt)) and cap(m[1]):
        th = m[0].split()[0] + " " + cap(m[1])
        if th not in out.get("khu", ""): out["khu"] = f"{out['khu']}, {th}" if out.get("khu") else th
    # loại đất, trạng thái
    out["loai_dat"] = ("Đất nông nghiệp công ích" if re.search("đất nông nghiệp công ích", alltxt, re.I) else
                       "Đất nông nghiệp" if re.search("đất nông nghiệp|sản xuất nông nghiệp", alltxt, re.I) else
                       "Đất thương mại dịch vụ" if re.search(r"thương mại,? dịch vụ", alltxt, re.I) else
                       "Đất sản xuất kinh doanh" if re.search("sản xuất kinh doanh", alltxt, re.I) else "Đất ở")
    if out.get("gia_trung_max") or out.get("so_thua_trung") or re.search("đấu giá thành công|trúng đấu giá", alltxt, re.I):
        out["trang_thai"] = "Đã đấu giá"
    # ghi chú
    if (m := re.search(r"tổng diện tích[^.\d]{0,25}(\d[\d.,]*)\s*m\s*(?:2|²)", alltxt, re.I)): note.append(f"Tổng diện tích {m[1].rstrip('.,')} m²")
    if (m := re.search(r"hạn\s*(?:cuối\s*)?(?:nộp|bán|đăng ký)\s*(hồ sơ|tiền đặt trước|tiền)?[^.]{0,60}?(\d{1,2}\s*/\s*\d{1,2}(?:\s*/\s*\d{4})?)", alltxt, re.I)):
        note.append(f"Hạn nộp {m[1] or 'hồ sơ'} " + re.sub(r"/\d{4}$", "", re.sub(r"\s", "", m[2])))
    for s in S:
        if not re.search(r"tổ chức|phiên đấu giá|thời gian đấu giá", s, re.I) or re.search(r"hạn|nộp|bán hồ sơ|đặt trước", s, re.I): continue
        if (m := re.search(r"(\d{1,2})\s*(?:h|giờ)\s*(\d{2})?(?![\d/])", s, re.I)) and int(m[1]) < 24: note.append(f"Phiên lúc {m[1]}h{m[2] or ''}"); break
    if out.get("khu") and len(out["khu"]) > 90: out["khu"] = out["khu"][:90].rsplit(",", 1)[0].rsplit(" ", 1)[0] + "…"
    if note: out["ghi_chu"] = "; ".join(note[:5])
    return {k: (f"{v:g}" if isinstance(v, float) else str(v)) for k, v in out.items() if v not in ("", None)}

def geocode(xa, tinh):
    try:
        q = urllib.parse.quote(f"{xa}, {tinh or 'Hà Nội'}")
        j = json.loads(get(f"https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=vn&q={q}", headers={"Accept-Language": "vi"}))
        if j: return f"{float(j[0]['lat']):.4f}", f"{float(j[0]['lon']):.4f}"
    except Exception as ex:
        print(f"    geocode lỗi: {ex}")
    return None

# ------------------------------------------------------------------ chạy
def fill_row(p, raw):
    ex = extract(raw, p); done = []
    for k, v in ex.items():
        cur = (p.get(k) or "").strip()
        if k == "ghi_chu" and cur:
            add = [x for x in v.split("; ") if x not in cur]
            if add: p[k] = cur + "; " + "; ".join(add); done.append(k)
        elif k == "trang_thai" and cur in ("Sắp đấu giá", "Chờ kết quả") and v == "Đã đấu giá":
            p[k] = v; done.append(k)
        elif not cur:
            p[k] = v; done.append(k)
    if (not (p.get("lat") or "").strip() or not (p.get("lng") or "").strip()) and p.get("xa"):
        g = geocode(p["xa"], p.get("tinh")); time.sleep(1.1)
        if g: p["lat"], p["lng"] = g; p["vi_tri"] = p.get("vi_tri") or "gần đúng"; done += ["lat", "lng"]
    return done

def main():
    if not os.path.exists(PEND): print("Chưa có cho-duyet.csv – bỏ qua"); return
    txt = open(PEND, encoding="utf-8-sig").read()
    rd = csv.DictReader(io.StringIO(txt)); head = list(rd.fieldnames or []); rows = list(rd)
    for h in ["link", "vi_tri", "_tu_dien", "_da_doc"]:
        if h not in head: head.append(h)
    force = "--all" in sys.argv; n_read = n_cells = 0
    for p in rows:
        if "news.google." in (p.get("link") or ""):   # tin cũ còn link Google News → đổi sang link báo gốc
            real = decode_google(p["link"]); time.sleep(1)
            if real:
                print(f"Link gốc: {real}"); p["link"] = real; p["nguon"] = src_name(real, p.get("nguon")); p["_da_doc"] = ""
        miss = [k for k in KEYF if not (p.get(k) or "").strip()]
        if not p.get("link") or "news.google." in p["link"] or (p.get("_da_doc") and not force) or not miss: continue
        print(f"Đọc: {p['link']} (thiếu {len(miss)} ô)")
        try:
            raw = fetch_text(p["link"]); done = fill_row(p, raw)
            p["_tu_dien"] = ",".join(sorted(set(filter(None, (p.get("_tu_dien") or "").split(",") + done))))
            print(f"    điền {len(done)} ô: {', '.join(done) or '—'}"); n_cells += len(done)
            p["_da_doc"] = date.today().isoformat(); n_read += 1   # chỉ đánh dấu khi đọc được, lỗi thì lần sau thử lại
        except Exception as ex:
            print(f"    không đọc được bài (sẽ thử lại lần sau): {ex}")
        time.sleep(1.5)
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); [w.writerow({h: p.get(h, "") for h in head}) for p in rows]
    open(PEND, "w", encoding="utf-8").write(buf.getvalue())
    print(f"Đã đọc {n_read} bài, điền thêm {n_cells} ô vào {PEND}")

if __name__ == "__main__":
    main()
