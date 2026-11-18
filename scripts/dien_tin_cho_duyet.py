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
BASIC = ["ngay", "xa", "khu", "so_thua", "gia_khoi_diem"]          # bài thông báo trước phiên luôn phải có
RESULT = ["gia_trung_max", "so_thua_trung", "tong_tien_ty"]        # chỉ đòi khi phiên đã có kết quả
MAX_TRIES = 3
BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "12"))
T0 = time.monotonic()
JINA_KEY = os.environ.get("JINA_API_KEY", "").strip()
# Gemini API (gói miễn phí của Google AI Studio): có khóa → Gemini đọc bài; không có / hết hạn mức → quy tắc
AI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
AI_MODELS = [m.strip() for m in os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest,gemini-flash-latest").split(",") if m.strip()]
AI_NAME = "Gemini"
AI_GAP_S = float(os.environ.get("GEMINI_GAP_S", "6"))   # giãn cách giữa 2 lần gọi để nằm trong hạn mức miễn phí (~10 lượt/phút)
_ai_state = {"last": 0.0, "off": False, "model": 0, "calls": 0}

def missing_keys(p):
    need = BASIC + (RESULT if p.get("trang_thai") in ("Đã đấu giá", "Đã nộp tiền") else [])
    return [k for k in need if not (p.get(k) or "").strip()]

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
    timeout = min(timeout, 30)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(); cs = r.headers.get_content_charset() or "utf-8"
    return raw.decode(cs, errors="replace")

def fetch_text(url):
    """Ưu tiên r.jina.ai (trả về chữ sạch của bài), dự phòng tải thẳng trang rồi bỏ thẻ HTML."""
    for attempt in range(3):
        try:
            hd = {"X-Return-Format": "markdown"}
            if JINA_KEY: hd["Authorization"] = "Bearer " + JINA_KEY
            t = get("https://r.jina.ai/" + url, headers=hd)
            if len(t) > 300: return t
            break
        except Exception as ex:
            code = getattr(ex, "code", 0)
            print(f"    jina lỗi: {ex}")
            if code == 429: time.sleep(15 * (attempt + 1)); continue    # vượt hạn mức miễn phí (~20 bài/phút) → chờ rồi thử lại
            if code in (400, 401, 403, 404, 422, 451): break
            time.sleep(3)
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
    if (m := re.search(r"((?:Khu|khu|(?<!khởi )Điểm|(?<!khởi )điểm|Ô đất|ô đất|Lô đất|lô đất|Cụm|cụm)\s+(?!vực|từ\b|\d)(?:[^.;:\n]|\.(?=\d)){2,90}?)(?=\s*(?:,\s*(?:xã|phường|thị trấn)\s|\.(?!\d)|;|\n|\(|$))", t)) \
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
    # sau đấu giá: bỏ cọc / hủy kết quả / đã nộp đủ tiền (chỉ câu không nói chuyện năm trước)
    now = " ".join(x for x in S if not HIST.search(x))
    if re.search(r"hủy (bỏ )?kết quả|hủy quyết định công nhận kết quả|kết quả .{0,20}bị hủy", now, re.I): out["sau_dau_gia"] = "Đã hủy kết quả"
    elif re.search(r"bỏ cọc|không nộp tiền trúng|không nộp (đủ )?tiền|mất (tiền )?cọc", now, re.I): out["sau_dau_gia"] = "Bỏ cọc"
    elif re.search(r"(đã )?nộp đủ tiền (trúng đấu giá|sử dụng đất)", now, re.I): out["sau_dau_gia"] = "Đã nộp đủ tiền"
    if (m := re.search(r"(\d+)\s*(?:thửa|lô|suất)[^.]{0,40}?bỏ cọc|bỏ cọc[^.]{0,30}?(\d+)\s*(?:thửa|lô|suất)", now, re.I)):
        note.append(f"Bỏ cọc {m[1] or m[2]} thửa")
    # ghi chú
    if (m := re.search(r"tổng diện tích[^.\d]{0,25}(\d[\d.,]*)\s*m\s*(?:2|²)", alltxt, re.I)): note.append(f"Tổng diện tích {m[1].rstrip('.,')} m²")
    if (m := re.search(r"hạn\s*(?:cuối\s*)?(?:nộp|bán|đăng ký)\s*(hồ sơ|tiền đặt trước|tiền)?[^.]{0,60}?(\d{1,2}\s*/\s*\d{1,2}(?:\s*/\s*\d{4})?)", alltxt, re.I)):
        note.append(f"Hạn nộp {m[1] or 'hồ sơ'} " + re.sub(r"/\d{4}$", "", re.sub(r"\s", "", m[2])))
    for s in S:
        if not re.search(r"tổ chức|phiên đấu giá|thời gian đấu giá", s, re.I) or re.search(r"hạn|nộp|bán hồ sơ|đặt trước", s, re.I): continue
        if (m := re.search(r"(\d{1,2})\s*(?:h|giờ)\s*(\d{2})?(?![\d/])", s, re.I)) and int(m[1]) < 24: note.append(f"Phiên lúc {m[1]}h{m[2] or ''}"); break
    if out.get("khu") and len(out["khu"]) > 90: out["khu"] = out["khu"][:90].rsplit(",", 1)[0].rsplit(" ", 1)[0] + "…"
    if not any(x.startswith("Hạn nộp") for x in note):
        for s in S:   # "Thời gian bán hồ sơ, nộp tiền đặt trước: từ … đến 17 giờ ngày 18/11/2026"
            if re.search(r"hồ sơ|đặt trước|đăng ký", s, re.I) and (m := re.search(r"đến\s*(?:\d{1,2}\s*(?:giờ|h)\s*\d{0,2}\s*(?:phút)?\s*)?(?:,\s*)?ngày\s*(\d{1,2}\s*/\s*\d{1,2})", s, re.I)):
                note.append("Hạn nộp hồ sơ " + re.sub(r"\s", "", m[1])); break
    if (m := re.search(r"tiền đặt trước[^.\d]{0,20}(\d{1,2})\s*%", alltxt, re.I)): note.append(f"Đặt trước {m[1]}%")
    if note: out["ghi_chu"] = "; ".join(note[:6])
    return {k: (f"{v:g}" if isinstance(v, float) else str(v)) for k, v in out.items() if v not in ("", None)}

# ------------------------------------------------------------------ AI (Gemini miễn phí) đọc bài
FIELDS = ["ngay", "tinh", "xa", "dia_danh_cu", "khu", "loai_dat", "so_thua", "so_thua_trung", "gia_khoi_diem",
          "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia", "trang_thai", "sau_dau_gia", "ghi_chu"]
AI_PROMPT = """Bạn trích xuất số liệu một phiên đấu giá quyền sử dụng đất từ bài báo tiếng Việt, để điền vào bảng dữ liệu.
{hint}
Trả về DUY NHẤT một JSON (không giải thích) với các khóa sau, giá trị là chuỗi ngắn gọn; bài không nêu thì để "":
- khop: "co" nếu bài có nói về phiên cần tìm ở trên (hoặc không có thông tin phiên cần tìm), "khong" nếu bài nói về phiên/khu/xã khác
- ngay: ngày tổ chức phiên, dạng YYYY-MM-DD (năm mặc định {yr})
- tinh: tỉnh/thành phố (VD "Hà Nội")
- xa: tên xã/phường MỚI (sau sáp nhập 7/2025), không kèm chữ "xã"/"phường"
- dia_danh_cu: địa danh cũ, VD "xã Kim Nỗ, huyện Đông Anh"
- khu: tên khu/điểm đấu giá kèm thôn, ngắn gọn, VD "Khu X1, thôn Thọ Đa"
- loai_dat: "Đất ở" | "Đất thương mại dịch vụ" | "Đất sản xuất kinh doanh" | "Đất nông nghiệp" | "Đất ở – dự án nhà ở cao tầng"
- so_thua: số thửa/lô đưa ra đấu giá (số nguyên)
- so_thua_trung: số thửa có người trúng (số nguyên)
- gia_khoi_diem: giá khởi điểm THẤP NHẤT, quy ra TRIỆU đồng/m², số thập phân dùng dấu chấm (VD 14.35)
- gia_trung_min, gia_trung_max: giá trúng thấp nhất / cao nhất, TRIỆU đồng/m² (không lấy số so sánh với năm trước)
- tong_tien_ty: tổng tiền trúng đấu giá, TỶ đồng
- nguoi_tham_gia: số người/hồ sơ tham gia
- trang_thai: "Sắp đấu giá" | "Chờ kết quả" (đã diễn ra, chưa có kết quả) | "Đã đấu giá" (đã có kết quả) | "Hủy/bỏ cọc" (cả phiên bị hủy, hoặc mọi người trúng đều bỏ cọc)
- sau_dau_gia: tình hình SAU phiên của chính phiên này nếu bài nêu: "Bỏ cọc" (có người trúng bỏ cọc/không nộp tiền) | "Đã hủy kết quả" | "Đã nộp đủ tiền" | "Đã nộp một phần" | ""
- ghi_chu: tối đa 5 ý cực ngắn cách nhau "; " – số thửa bị bỏ cọc nếu có, khoảng giá khởi điểm nếu nhiều mức, tổng diện tích, tổng giá khởi điểm, hạn nộp hồ sơ, tiền đặt trước, giờ phiên, điểm đáng chú ý. Tự viết lại, không chép nguyên văn.
Chỉ ghi điều bài nêu rõ, không suy đoán. Hôm nay là {today}.

TIÊU ĐỀ: {title}
NỘI DUNG:
{text}"""

def _gemini(prompt):
    """Gọi Gemini, trả chuỗi JSON. Hết hạn mức (429) → tắt AI cho lượt chạy này, quay về quy tắc."""
    if not AI_KEY or _ai_state["off"]: return None
    for attempt in range(3):
        wait = AI_GAP_S - (time.monotonic() - _ai_state["last"])
        if wait > 0: time.sleep(wait)
        _ai_state["last"] = time.monotonic()
        model = AI_MODELS[min(_ai_state["model"], len(AI_MODELS) - 1)]
        body_ = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 1200}}).encode()
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body_,
                                         headers={"x-goog-api-key": AI_KEY, "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r: j = json.loads(r.read().decode("utf-8"))
            _ai_state["calls"] += 1
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return "".join(p.get("text", "") for p in parts)
        except Exception as ex:
            code = getattr(ex, "code", 0); print(f"    Gemini lỗi ({model}): {ex}")
            if code == 404 and _ai_state["model"] < len(AI_MODELS) - 1: _ai_state["model"] += 1; continue   # tên model đổi → thử model sau
            if code == 429:
                if attempt == 0: time.sleep(40); continue
                print("    → hết hạn mức miễn phí của Gemini hôm nay: phần còn lại dùng quy tắc"); _ai_state["off"] = True; return None
            if code in (500, 502, 503, 504): time.sleep(8 * (attempt + 1)); continue
            if code in (400, 401, 403): print("    → khóa GEMINI_API_KEY sai hoặc chưa bật API: dùng quy tắc"); _ai_state["off"] = True
            return None
    return None

def ai_extract(text, title, row=None):
    """AI đọc bài, trả dict các ô (chỉ ô có giá trị) + '_khop' (True/False). Không có AI / lỗi → None."""
    if not AI_KEY or _ai_state["off"]: return None
    hint = ""
    if row and (row.get("xa") or row.get("khu")):
        hint = ("Phiên cần tìm (bài có thể nói nhiều phiên – CHỈ lấy số liệu của phiên này): "
                f"xã/phường {row.get('xa', '')}; địa danh cũ {row.get('dia_danh_cu', '')}; khu {row.get('khu', '')}; "
                f"ngày {row.get('ngay', '') or 'chưa rõ'}; số thửa {row.get('so_thua', '') or 'chưa rõ'}; giá khởi điểm {row.get('gia_khoi_diem', '') or 'chưa rõ'} tr/m².")
    out = _gemini(AI_PROMPT.format(hint=hint, yr=date.today().year, today=date.today().isoformat(), title=title, text=text[:12000]))
    if not out: return None
    m = re.search(r"\{.*\}", out, re.S)
    if not m: return None
    try: d = json.loads(m.group(0))
    except Exception: return None
    if isinstance(d, list): d = d[0] if d else {}
    res = {"_khop": str(d.get("khop", "co")).strip().lower() not in ("khong", "không", "false", "no")}
    for k in FIELDS:
        v = str(d.get(k, "") or "").strip()
        if not v: continue
        if k in ("so_thua", "so_thua_trung", "nguoi_tham_gia", "gia_khoi_diem", "gia_trung_min", "gia_trung_max", "tong_tien_ty"):
            mm = re.search(r"\d+(?:[.,]\d+)?", v)
            if not mm: continue
            v = f"{vn(mm.group(0)):g}" if k not in ("so_thua", "so_thua_trung", "nguoi_tham_gia") else str(int(vn(mm.group(0), "m2")))
        if k == "ngay" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): continue
        res[k] = v
    return res

def extract_best(raw, row):
    """Gemini (nếu có khóa) + quy tắc bù chỗ AI để trống. Trả (dict, cách đọc)."""
    rx = extract(raw, row)
    title = row.get("_tieu_de") or (re.search(r"^Title:\s*(.+)$", raw, re.M) or [None, ""])[1]
    ai = ai_extract(body(raw, title), title, row)
    if ai is None: return rx, "quy tắc"
    if not ai.pop("_khop", True): return {"_khong_khop": "1"}, AI_NAME
    for k, v in rx.items():
        if k not in ai and k != "ghi_chu": ai[k] = v
    if rx.get("ghi_chu") and not ai.get("ghi_chu"): ai["ghi_chu"] = rx["ghi_chu"]
    return ai, AI_NAME

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
    ex, how = extract_best(raw, p); done = []
    p["_cach_doc"] = how
    if ex.get("_khong_khop"):
        p["_loi_doc"] = "AI: bài không nói về phiên này – kiểm tra lại link"; return done
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
    for h in ["link", "vi_tri", "_tu_dien", "_da_doc", "_so_lan_doc", "_loi_doc", "_cach_doc"]:
        if h not in head: head.append(h)
    force = "--all" in sys.argv; n_read = n_cells = 0
    for p in rows:
        if "news.google." in (p.get("link") or ""):   # tin cũ còn link Google News → đổi sang link báo gốc
            real = decode_google(p["link"]); time.sleep(1)
            if real:
                print(f"Link gốc: {real}"); p["link"] = real; p["nguon"] = src_name(real, p.get("nguon")); p["_da_doc"] = ""
        if time.monotonic() - T0 > BUDGET_MIN * 60:
            print(f"⏱ Hết {BUDGET_MIN:g} phút – dừng, lần sau đọc tiếp."); break
        miss = missing_keys(p)
        tries = int(p.get("_so_lan_doc") or 0)
        # đọc khi còn thiếu ô cơ bản; đã đọc rồi mà vẫn thiếu thì thử lại (tối đa 3 lần, mỗi ngày một lần)
        if not p.get("link") or "news.google." in p["link"] or not miss: continue
        if not force and (tries >= MAX_TRIES or p.get("_da_doc") == date.today().isoformat()): continue
        print(f"Đọc: {p['link']} (thiếu {len(miss)} ô)")
        try:
            raw = fetch_text(p["link"]); done = fill_row(p, raw)
            p["_tu_dien"] = ",".join(sorted(set(filter(None, (p.get("_tu_dien") or "").split(",") + done))))
            print(f"    điền {len(done)} ô: {', '.join(done) or '—'}"); n_cells += len(done)
            p["_da_doc"] = date.today().isoformat(); n_read += 1
            left = missing_keys(p)
            if not p.get("_loi_doc", "").startswith("AI:"): p["_loi_doc"] = ("bài không nêu: " + ", ".join(left)) if left else ""
        except Exception as ex:
            print(f"    không đọc được bài (sẽ thử lại lần sau): {ex}"); p["_loi_doc"] = f"không tải được bài: {str(ex)[:80]}"
        p["_so_lan_doc"] = str(tries + 1)
        time.sleep(1.5 if JINA_KEY else 3.5)   # không có khóa Jina: giữ dưới ~20 bài/phút
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); [w.writerow({h: p.get(h, "") for h in head}) for p in rows]
    open(PEND, "w", encoding="utf-8").write(buf.getvalue())
    print(f"Đã đọc {n_read} bài ({f'Gemini {_ai_state["calls"]} lượt' + (' – đã hết hạn mức, phần sau dùng quy tắc' if _ai_state['off'] else '') if AI_KEY else 'quy tắc – chưa có GEMINI_API_KEY'}), điền thêm {n_cells} ô vào {PEND}")

if __name__ == "__main__":
    main()
