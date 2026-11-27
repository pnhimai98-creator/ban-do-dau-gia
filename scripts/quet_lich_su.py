#!/usr/bin/env python3
"""
Quét lịch sử: tìm các phiên ĐÃ ĐẤU GIÁ (có kết quả) từ 1/1/2024 đến 29/5/2026
History backfill: find past land auctions with results (default 2024-01-01 → 2026-05-29)

Chạy trong workflow .github/workflows/quet-lich-su.yml (nhiều lần, mỗi lần làm tiếp phần còn lại):
    python scripts/quet_lich_su.py

Cách làm
1. Chia khoảng thời gian thành các khung 15 ngày (từ mới về cũ). Với mỗi khung, hỏi Google News
   (after:/before:) bằng vài câu tìm "trúng đấu giá"… theo Hà Nội, huyện cũ của Hà Nội và các tỉnh
   (tính cả tên tỉnh trước sáp nhập 7/2025: Bắc Giang, Vĩnh Phúc, Hòa Bình, Thái Bình, Hà Nam, Nam Định, Bắc Kạn).
   Tiêu đề có dấu hiệu kết quả → đưa vào hàng đợi (data/quet-lich-su.json).
2. Đọc từng bài trong hàng đợi (Gemini miễn phí nếu có GEMINI_API_KEY, không thì quy tắc),
   chỉ giữ bài có KẾT QUẢ (giá trúng / số thửa trúng / tổng tiền) hoặc phiên "Đấu giá lại" / "Hủy/bỏ cọc",
   ngày phiên nằm trong khoảng cần quét, đúng tỉnh trong dự án, không trùng phiên/link đã có.
3. Thêm vào cho-duyet.csv (khung ② trong admin) để DUYỆT TAY. Không ghi thẳng data.csv.
   - Tỉnh cũ → tỉnh mới (VD Bắc Giang → Bắc Ninh), tên cũ giữ ở cột dia_danh_cu.
   - Bài trước 1/7/2025 chỉ có tên xã cũ: tra bảng data/xa-cu-moi.json (tự học từ data.csv, sửa/bổ sung tay được);
     không tra được thì để trống cột xa và ghi chú "Cần điền xã mới".
   - Năm của ngày phiên được đối chiếu với ngày đăng bài (bài kết quả không thể đăng trước phiên).
Hết hạn mức Gemini trong ngày → dừng, lần sau làm tiếp (không đọc bừa bằng quy tắc).
Không cần thư viện ngoài (chỉ Python 3 chuẩn).
"""
import csv, html, io, json, os, re, sys, time, unicodedata, urllib.parse
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
PEND = os.path.join(ROOT, "cho-duyet.csv")
STATE = os.path.join(ROOT, "data", "quet-lich-su.json")
XAMAP = os.path.join(ROOT, "data", "xa-cu-moi.json")
VN = timezone(timedelta(hours=7))
TODAY = datetime.now(VN).date()
T0 = time.monotonic()

START = date.fromisoformat(os.environ.get("QUET_TU") or "2024-01-01")
END = date.fromisoformat(os.environ.get("QUET_DEN") or "2026-05-29")
WINDOW = int(os.environ.get("KHUNG_NGAY", "15"))
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "80"))
BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "30"))
GATHER_SHARE = 0.35                     # tối đa 35% thời gian cho việc tìm bài, phần còn lại để đọc bài
MERGE_DAY = date(2025, 7, 1)            # ngày sáp nhập xã/tỉnh

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
    '"đấu giá" đất ("bỏ cọc" OR "hủy kết quả" OR "không có người tham gia") ' + OR(["Hà Nội", "Bắc Ninh", "Bắc Giang", "Hưng Yên", "Phú Thọ", "Vĩnh Phúc"]),
]
RESULT_TITLE = r"trung dau gia|gia trung|ket qua dau gia|dau gia thanh cong|thu ve|trieu dong ?/ ?m|tr/m|bo coc|huy ket qua|\be\b|that bai|khong co nguoi|cao gap|chenh|kich san|ty dong|nghin ty"
NOT_LAND = r"bien so|o to|xe may|khoang san|\bmo cat\b|co phan|tai san thi hanh an|tau bien|tang vat"
RESULT_KEYS = ["so_thua_trung", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]
# tiêu đề kiểu thông báo trước phiên / tin tổng hợp / tin chính sách → không phải kết quả MỘT phiên
ANNOUNCE = r"\b(sap|chuan bi|du kien|ke hoach|se dau gia|len ke hoach|tung)\b.{0,40}dau gia|de xuat|sua luat|nghi dinh|thong tu|chuyen gia|vi sao|tu vu|\d+ du an|\d+ ha dat|nghin ty|trung thau"
# tỉnh/thành ngoài vùng dự án hay lẫn vào tin
OTHER_PLACES = ["Khánh Hòa", "Nha Trang", "TP.HCM", "TPHCM", "Hồ Chí Minh", "Thủ Thiêm", "Thủ Đức", "Đà Nẵng", "Hải Phòng", "Hải Dương",
                "Thanh Hóa", "Nghệ An", "Hà Tĩnh", "Quảng Ninh", "Lâm Đồng", "Đồng Nai", "Bình Dương", "Long An", "Cần Thơ", "Quảng Nam",
                "Quảng Ngãi", "Bình Định", "Phú Yên", "Đắk Lắk", "Gia Lai", "Kiên Giang", "Phú Quốc", "Lạng Sơn", "Lào Cai", "Yên Bái",
                "Tuyên Quang", "Sơn La", "Điện Biên", "Cao Bằng", "Hà Giang", "Quảng Bình", "Quảng Trị", "Huế", "Bà Rịa", "Vũng Tàu"]


def valid_iso(d):
    try: date.fromisoformat(d); return True
    except Exception: return False


def reject_reason(title, text, tinh, how):
    """Lý do loại một bài/dòng quét lịch sử (dùng cả để dọn các dòng cũ đã thêm nhầm). '' = giữ."""
    ft, fti = fold(text), fold(title)
    if any(fold(p) in fti for p in OTHER_PLACES): return "tin tỉnh khác"
    if re.search(ANNOUNCE, fti) and not re.search(r"thanh cong|trung (cao nhat|gia|dau gia)|gia trung", fti): return "tin thông báo / tổng hợp"
    if tinh == "Hà Nội" and not ("ha noi" in ft or any(fold(h) in ft for h in HN_OLD)): return "không thấy Hà Nội trong bài"
    if tinh and tinh != "Hà Nội" and fold(tinh) not in ft and not any(fold(o) in ft for o, n in OLD_PROV.items() if n == tinh): return "không thấy tên tỉnh trong bài"
    if how == "quy tắc" and re.search(r"\b(tong hop|loat|don dap|nhieu xa|cac xa|vung ven)\b", fti): return "tin tổng hợp nhiều phiên (quy tắc không tách được)"
    return ""


def log(*a): print(*a, flush=True)


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"^(xa|phuong|thi tran|dac khu)\s+", "", s.strip())
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
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


def windows():
    """Các khung [a, b) từ mới về cũ."""
    out, b = [], END + timedelta(days=1)
    while b > START:
        a = max(START, b - timedelta(days=WINDOW)); out.append((a, b)); b = a
    return out


def parse_pub(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try: return datetime.strptime((s or "").strip(), fmt).date()
        except Exception: pass
    return None


def search(q):
    raw = D.get(GN + urllib.parse.quote(q), timeout=30)
    root = ET.fromstring(raw.encode("utf-8"))
    for it in root.iter("item"):
        src, pd = it.find("source"), parse_pub(it.findtext("pubDate"))
        yield {"title": unicodedata.normalize("NFC", html.unescape(it.findtext("title") or "")).strip(),
               "link": (it.findtext("link") or "").strip(), "pub": pd.isoformat() if pd else "",
               "src": (src.text or "").strip() if src is not None else ""}


def title_ok(t):
    f = fold(t)
    if "dau gia" not in f and "trung" not in f: return False
    if re.search(NOT_LAND, f) and "dat" not in f: return False
    if not re.search(RESULT_TITLE, f): return False
    return True


# ---------------------------------------------------------------- bảng xã cũ → xã mới
def learn_map(drows, xmap):
    """Học thêm từ data.csv: dia_danh_cu chỉ có MỘT xã cũ → xa (mới)."""
    n = 0
    for r in drows:
        olds = re.findall(r"(?:xã|phường|thị trấn)\s+([^,;()]+)", r.get("dia_danh_cu") or "")
        if len(olds) != 1 or not r.get("xa") or not r.get("tinh"): continue
        k = f"{fold(r['tinh'])}|{fold(olds[0])}"
        if k not in xmap: xmap[k] = r["xa"].strip(); n += 1
    return n


def fix_year(d, pub):
    """Bài thường chỉ ghi "ngày 5/11" không kèm năm → AI/quy tắc hay lấy năm nay (2026).
    Chọn năm (năm đăng bài −1 / = / +1) sao cho ngày phiên GẦN ngày đăng bài nhất,
    trong khoảng 1 năm trước đến 3 tháng sau ngày đăng. Không chọn được → bỏ trống."""
    if not d or not pub: return d
    try: d, p = date.fromisoformat(d), date.fromisoformat(pub)
    except Exception: return d
    best = None
    for y in (p.year - 1, p.year, p.year + 1):
        try: c = d.replace(year=y)
        except ValueError: continue
        if p - timedelta(days=366) <= c <= p + timedelta(days=92) and (best is None or abs((c - p).days) < abs((best - p).days)): best = c
    return best.isoformat() if best else ""


def dup_key(r):
    return (fold(r.get("tinh")), fold(r.get("xa")) or fold(r.get("dia_danh_cu")) or fold(r.get("khu")), (r.get("ngay") or "")[:10])


# ---------------------------------------------------------------- 1. tìm bài
def gather(st):
    done = set(st["khung_da_quet"])
    queue, qkeys = st["hang_doi"], {fold(x["title"])[:80] for x in st["hang_doi"]} | {x["link"] for x in st["hang_doi"]}
    for a, b in windows():
        if a.isoformat() in done: continue
        if time.monotonic() - T0 > BUDGET_MIN * 60 * GATHER_SHARE:
            log("⏱ Hết phần thời gian tìm bài – các khung còn lại để lần sau."); return
        n_new, ok = 0, True
        for q in QUERIES:
            full = f"{q} after:{(a - timedelta(days=1)).isoformat()} before:{b.isoformat()}"
            try:
                for it in search(full):
                    k = fold(it["title"])[:80]
                    if it["link"] in st["da_xu_ly"] or k in qkeys or it["link"] in qkeys or not title_ok(it["title"]): continue
                    qkeys |= {k, it["link"]}; queue.append({**it, "khung": a.isoformat(), "lan": 0}); n_new += 1
            except Exception as ex:
                log(f"    tìm lỗi ({ex}) – khung này để lần sau"); ok = False; break
            time.sleep(1.2)
        log(f"• Khung {a.isoformat()} → {(b - timedelta(days=1)).isoformat()}: +{n_new} bài")
        if ok: st["khung_da_quet"].append(a.isoformat())


# ---------------------------------------------------------------- 2. đọc bài
def build_row(it, link, raw, xmap):
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", it["title"])
    ex, how = D.extract_best(raw, {"_tieu_de": title, "link": link})
    if ex.pop("_khong_khop", None): ex, how = D.extract(raw, {"_tieu_de": title, "link": link}), "quy tắc"
    text = title + " " + D.body(raw, title)
    ft = fold(text)
    has_res = any(ex.get(k) for k in ("gia_trung_max", "so_thua_trung", "tong_tien_ty"))
    st = ex.get("trang_thai", "")
    if st not in ("Đã đấu giá", "Đấu giá lại", "Hủy/bỏ cọc"):
        if not has_res: return None, "không có kết quả"
        st = "Đã đấu giá"
    if st == "Đã đấu giá" and not has_res: return None, "không có số liệu kết quả"
    ngay = fix_year(ex.get("ngay", "") if valid_iso(ex.get("ngay", "")) else "", it.get("pub"))
    if ngay and it.get("pub") and ngay > (date.fromisoformat(it["pub"]) + timedelta(days=1)).isoformat():
        return None, "bài đăng trước ngày phiên (thông báo, chưa có kết quả)"
    ref = ngay or it.get("pub") or ""
    if not ref or not (START.isoformat() <= ref <= END.isoformat()): return None, f"ngoài khoảng ({ref or 'không rõ ngày'})"
    # tỉnh
    notes, tinh, old = [], (ex.get("tinh") or "").strip(), (ex.get("dia_danh_cu") or "").strip()
    tinh = re.sub(r"^(tỉnh|thành phố|TP\.?)\s+", "", tinh, flags=re.I)
    if not tinh:                                    # quy tắc không nhận ra tỉnh → tìm tên tỉnh (mới/cũ) xuất hiện sớm nhất trong bài
        pos = [(ft.find(fold(p)), p) for p in PROVINCES + list(OLD_PROV) if fold(p) in ft]
        if pos: tinh = min(pos)[1]
    if tinh in OLD_PROV:
        if fold(tinh) not in fold(old): old = ", ".join(filter(None, [old, "tỉnh " + tinh]))
        tinh = OLD_PROV[tinh]
    if not tinh and any(fold(h) in ft for h in HN_OLD): tinh = "Hà Nội"
    if tinh not in PROVINCES: return None, f"ngoài vùng ({tinh or 'không rõ tỉnh'})"
    why = reject_reason(title, text, tinh, how)
    if why: return None, why
    if how == "quy tắc" and (not ngay or not ex.get("gia_trung_max")):   # quy tắc dễ đọc nhầm → đòi đủ ngày + giá trúng
        return None, "quy tắc: thiếu ngày phiên hoặc giá trúng"
    # xã
    xa = (ex.get("xa") or "").strip()
    if ref < MERGE_DAY.isoformat():
        olds = re.findall(r"(?:xã|phường|thị trấn)\s+([^,;()]+)", old)
        old_xa = olds[0].strip() if olds else xa
        if not old and xa: old = f"xã {xa}"
        new = xmap.get(f"{fold(tinh)}|{fold(old_xa)}") if old_xa else ""
        if new: xa = new
        elif xa and old_xa and fold(xa) != fold(old_xa): notes.append("Tên xã mới do AI suy ra – kiểm tra lại")
        else: xa = ""; notes.append(f"Cần điền xã mới (địa danh cũ: {old or '?'})")
    if not ngay: notes.append(f"Chưa rõ ngày phiên (bài đăng {it.get('pub')})")
    r = {k: v for k, v in ex.items() if k not in ("trang_thai", "ngay", "tinh", "xa", "dia_danh_cu", "ghi_chu")}
    r.update({"ngay": ngay, "tinh": tinh, "xa": xa, "dia_danh_cu": old, "trang_thai": st, "loai_dat": ex.get("loai_dat") or "Đất ở"})
    r["ghi_chu"] = "; ".join(filter(None, [ex.get("ghi_chu", ""), *notes]))
    src = D.src_name(link, it.get("src") or "")
    r.update({"link": link, "nguon": src, "link_ket_qua": link, "nguon_ket_qua": src, "vi_tri": "gần đúng",
              "_khung": "2", "_tieu_de": title, "_ngay_lay": TODAY.isoformat(), "_cach_lay": f"quét lịch sử ({how})",
              "_tu_dien": ",".join(k for k in ex if k != "ghi_chu"), "_da_doc": TODAY.isoformat(), "_so_lan_doc": "3", "_ngay_dang": it.get("pub") or ""})
    # tọa độ: thử xã mới rồi xã cũ
    for q in filter(None, [xa, re.sub(r"^(xã|phường|thị trấn)\s+", "", old.split(";")[0]).strip() if old else ""]):
        g = D.geocode(q, tinh); time.sleep(1.1)
        if g: r["lat"], r["lng"] = g; break
    return r, how


def process(st, drows, prows, xmap):
    known_links = {x.get(k, "").strip() for x in drows + prows for k in ("link", "link_ket_qua", "link_thong_bao") if x.get(k)}
    known_keys = {dup_key(x) for x in drows + prows if x.get("ngay")}
    added, kept, n = 0, [], 0
    queue = sorted(st["hang_doi"], key=lambda x: x.get("pub") or x.get("khung") or "", reverse=True)
    for it in queue:
        if n >= MAX_ARTICLES or time.monotonic() - T0 > BUDGET_MIN * 60 or (D.AI_KEY and D._ai_state["off"]):
            kept.append(it); continue
        link = D.decode_google(it["link"]) if "news.google." in it["link"] else it["link"]
        if not link or "news.google." in link:
            it["lan"] = it.get("lan", 0) + 1
            if it["lan"] < 3: kept.append(it)
            continue
        if link in known_links or link in st["da_xu_ly"]:
            st["da_xu_ly"][it["link"]] = TODAY.isoformat(); continue
        n += 1
        try: raw = D.fetch_text(link)
        except Exception as ex:
            log(f"  ! không đọc được {link}: {ex}"); it["lan"] = it.get("lan", 0) + 1
            if it["lan"] < 3: kept.append(it)
            continue
        if D.AI_KEY and D._ai_state["off"]:          # vừa hết hạn mức Gemini → để bài này lần sau
            kept.append(it); continue
        try: r, why = build_row(it, link, raw, xmap)
        except Exception as ex: r, why = None, f"lỗi bóc tách: {ex}"
        st["da_xu_ly"][it["link"]] = st["da_xu_ly"][link] = TODAY.isoformat()
        st["thong_ke"][why if not r else "thêm"] = st["thong_ke"].get(why if not r else "thêm", 0) + 1
        title = it["title"][:75]
        if not r: log(f"  · {why}: {title}"); continue
        if dup_key(r) in known_keys and r.get("ngay"):
            log(f"  = trùng phiên đã có: {r.get('xa') or r.get('dia_danh_cu')} {r['ngay']}"); continue
        prows.append(r); known_keys.add(dup_key(r)); known_links.add(link); added += 1
        log(f"  + {r['tinh']} · {r.get('xa') or r.get('dia_danh_cu') or '?'} · {r.get('ngay') or '?'} · {r['trang_thai']} · "
            f"trúng {r.get('gia_trung_max') or '?'} tr/m²")
        time.sleep(1.5 if D.JINA_KEY else 3.5)
    st["hang_doi"] = kept
    return added, n


def main():
    st = load_json(STATE, {})
    scope = f"v2|{START.isoformat()}|{END.isoformat()}|{WINDOW}"
    if st.get("pham_vi") != scope:
        # v2: sửa lỗi năm phiên → quét và đọc lại từ đầu; đổi khoảng quét thì giữ danh sách bài đã đọc
        keep = st.get("da_xu_ly", {}) if str(st.get("pham_vi", "")).startswith("v2|") else {}
        st = {"pham_vi": scope, "khung_da_quet": [], "hang_doi": [], "da_xu_ly": keep, "thong_ke": {}}
    for k, v in (("khung_da_quet", []), ("hang_doi", []), ("da_xu_ly", {}), ("thong_ke", {})): st.setdefault(k, v)
    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PEND)
    # dọn các dòng quét lịch sử đã thêm nhầm ở lượt trước (chỉ dòng do script này tạo, chưa duyệt)
    before = len(prows)
    def bad(p):
        if not (p.get("_cach_lay") or "").startswith("quét lịch sử"): return False
        if not p.get("_ngay_dang"): return True          # dòng do bản cũ thêm (có thể sai năm) → xóa, bản mới đọc lại
        how = "quy tắc" if "quy tắc" in p["_cach_lay"] else D.AI_NAME
        if reject_reason(p.get("_tieu_de", ""), (p.get("_tieu_de", "") + " " + p.get("tinh", "")), p.get("tinh", ""), how) not in ("", "không thấy Hà Nội trong bài", "không thấy tên tỉnh trong bài"): return True
        return how == "quy tắc" and (not p.get("ngay") or not p.get("gia_trung_max"))
    prows = [p for p in prows if not bad(p)]
    if before - len(prows): log(f"Đã dọn {before - len(prows)} dòng quét lịch sử thêm nhầm ở lượt trước")
    xmap = load_json(XAMAP, {})
    nl = learn_map(drows + prows, xmap)
    log(f"Khoảng quét {START} → {END} · bảng xã cũ→mới: {len(xmap)} mục (+{nl} học từ dữ liệu)")
    if not D.AI_KEY: log("⚠ CHƯA CÓ GEMINI_API_KEY – đang đọc bằng quy tắc, kết quả kém chính xác. Nên thêm khóa rồi mới quét.")
    total_w = len(windows())
    gather(st)
    log(f"Đã tìm {len(st['khung_da_quet'])}/{total_w} khung · hàng đợi {len(st['hang_doi'])} bài")
    added, n = process(st, drows, prows, xmap)
    head = phead or dhead
    for h in dhead + ["link_ket_qua", "nguon_ket_qua", "_khung", "_tieu_de", "_ngay_lay", "_cach_lay", "_tu_dien", "_da_doc", "_so_lan_doc", "_ngay_dang"]:
        if h not in head: head.append(h)
    write_csv(PEND, head, prows)
    save_json(STATE, st); save_json(XAMAP, xmap)
    left_w = total_w - len(st["khung_da_quet"])
    log(f"Xong lượt này: đọc {n} bài, thêm {added} phiên vào cho-duyet.csv (khung ②). "
        f"Còn {left_w} khung chưa tìm, {len(st['hang_doi'])} bài chờ đọc."
        + (f" Gemini: {D._ai_state['calls']} lượt" + (" – đã hết hạn mức hôm nay." if D._ai_state["off"] else ".") if D.AI_KEY else ""))
    log("Thống kê dồn: " + ", ".join(f"{k}: {v}" for k, v in sorted(st["thong_ke"].items(), key=lambda x: -x[1])))
    if not left_w and not st["hang_doi"]:
        log("✅ ĐÃ QUÉT XONG toàn bộ khoảng thời gian – có thể tắt workflow 'Quét lịch sử đấu giá'.")


if __name__ == "__main__":
    main()
