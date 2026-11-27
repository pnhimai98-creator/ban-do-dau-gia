#!/usr/bin/env python3
"""
Tìm kết quả cho các phiên đã qua ngày (dùng Gemini)  /  Find results for past auctions (Gemini)

Chạy trong workflow "Tìm kết quả, bỏ cọc…" (.github/workflows/tim-ket-qua.yml):
    python scripts/tim_ket_qua.py

0. CHUẨN HÓA TRẠNG THÁI (ghi thẳng data.csv, không cần duyệt) – quy ước:
   - "Đã đấu giá"  = có GIÁ TRÚNG CAO NHẤT, hoặc đã có tin sau phiên (Bỏ cọc / Đã hủy kết quả / Đã nộp đủ tiền)
   - "Chờ kết quả" = đã qua ngày đấu giá nhưng CHƯA có giá trúng cao nhất
   - "Sắp đấu giá" = ngày đấu giá chưa tới (hôm nay hoặc sau)
   ("Đấu giá lại", "Hủy/bỏ cọc" giữ nguyên.)
   Phiên "Chờ kết quả" quá 90 ngày kể từ ngày đấu giá → XÓA khỏi data.csv, chuyển sang data/phien-da-xoa.csv
   (lưu trữ, khôi phục được bằng cách chép dòng về data.csv).
1. TÌM KẾT QUẢ – phiên "Chờ kết quả" (≤ 90 ngày, phiên lâu chưa tìm nhất làm trước):
   a. Google News theo tên xã mới/cũ (+ tên khu), chỉ nhận bài ĐĂNG từ ngày phiên đến 120 ngày sau.
   b. Gemini xem các tiêu đề cùng thông tin phiên → chọn tối đa 3 bài có khả năng nêu kết quả CHÍNH phiên này.
   c. Gemini đọc toàn văn từng bài, được cho biết NGÀY ĐĂNG BÀI và thông tin phiên (xã, khu, ngày, số thửa,
      giá khởi điểm) → trả "khop" + số liệu kết quả + ngày phiên ghi trong bài.
   d. Chỉ nhận khi: Gemini xác nhận đúng phiên, có giá trúng cao nhất hoặc số thửa trúng, và ngày phiên trong bài
      (nếu bài ghi) lệch ≤ 3 ngày so với ngày phiên trong dữ liệu.
   → thêm vào cho-duyet.csv một dòng "cập nhật kết quả" (_cap_nhat_id = mã phiên); Duyệt trong admin sẽ ghi vào phiên cũ.
2. TIN SAU ĐẤU GIÁ – phiên "Đã đấu giá" trong 180 ngày: tìm tin bỏ cọc / hủy kết quả / nộp đủ tiền, Gemini đọc và
   xác nhận đúng phiên → dòng cập nhật "Sau đấu giá".
Bài đã xem lưu ở data/ket-qua-da-xem.json (theo từng phiên) để không đọc lại.
Lần chạy đầu: xóa các dòng cập nhật do bản cũ tạo (cách lấy "tìm kết quả (…, điểm…)" / "tin sau đấu giá (…, điểm…)"), chưa duyệt.
Không có GEMINI_API_KEY → chỉ làm bước 0. Hết hạn mức Gemini → dừng, lần sau làm tiếp (không đoán bằng quy tắc).
Dùng chung hàm Gemini của scripts/quet_lich_su.py và hàm tải bài của scripts/dien_tin_cho_duyet.py. Chỉ thư viện chuẩn.
"""
import csv, html, io, json, os, re, sys, time, urllib.parse
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D
import quet_lich_su as Q             # gemini_json, num, fmt, iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
PEND = os.path.join(ROOT, "cho-duyet.csv")
SEEN = os.path.join(ROOT, "data", "ket-qua-da-xem.json")
ARCHIVE = os.path.join(ROOT, "data", "phien-da-xoa.csv")
CHO_KQ_MAX_DAYS = int(os.environ.get("CHO_KQ_MAX_DAYS", "90"))     # "Chờ kết quả" quá số ngày này → xóa khỏi data.csv
TODAY = datetime.now(timezone(timedelta(hours=7))).date()
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "25"))
MAX_AFTER = int(os.environ.get("MAX_AFTER", "20"))
BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "15"))
MAX_DAYS = CHO_KQ_MAX_DAYS
AFTER_DAYS = 180
PUB_WINDOW = 120                      # bài kết quả phải đăng trong 120 ngày sau phiên
READ_PER_SESSION = 3
FINAL_AFTER = {"Bỏ cọc", "Đã hủy kết quả", "Đã nộp đủ tiền"}
RESULT_KEYS = ["so_thua_trung", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]
GN = "https://news.google.com/rss/search?hl=vi&gl=VN&ceid=VN:vi&q="
STOP = set("khu diem o lo dat thua thon xom to dan pho lang xa phuong huyen quan thi tran va cac tai dau gia quyen su dung con lai khac moi cu so nhung cum ho tro dinh cu xen ket hoi truong ubnd phuc vu san xuat nong nghiep truc tuyen".split())
T0 = time.monotonic()


def log(*a): print(*a, flush=True)


def fold(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn").lower()).strip()


def toks(s, own=""):
    o = set(re.split(r"[^a-z0-9]+", fold(own)))
    return {w for w in re.split(r"[^a-z0-9]+", fold(s)) if len(w) > 1 and not w.isdigit() and w not in STOP and w not in o}


def read_csv(path):
    if not os.path.exists(path): return [], []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh); return list(rd.fieldnames or []), list(rd)


def write_csv(path, head, rows):
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); [w.writerow({k: r.get(k, "") for k in head}) for r in rows]
    open(path, "w", encoding="utf-8", newline="").write(buf.getvalue())


def old_communes(r):
    """'xã Kim Nỗ, huyện Đông Anh; xã Kim Chung…' → ['Kim Nỗ', 'Kim Chung']"""
    return [m.group(2).strip() for m in re.finditer(r"(xã|phường|thị trấn)\s+([^,;()]+)", r.get("dia_danh_cu") or "")]


def ai_off(): return (not D.AI_KEY) or D._ai_state["off"]


def time_left(share=1.0): return time.monotonic() - T0 < BUDGET_MIN * 60 * share


def search(q):
    try:
        raw = D.get(GN + urllib.parse.quote(q), timeout=30)
        out = []
        for it in ET.fromstring(raw.encode("utf-8")).iter("item"):
            try: pd = datetime.strptime((it.findtext("pubDate") or "").strip(), "%a, %d %b %Y %H:%M:%S %Z").date()
            except Exception: pd = None
            src = it.find("source"); srcname = (src.text or "").strip() if src is not None else ""
            title = html.unescape(it.findtext("title") or "").strip()
            if srcname and title.endswith(" - " + srcname): title = title[: -len(srcname) - 3].strip()
            out.append({"title": title, "link": (it.findtext("link") or "").strip(), "pub": pd, "src": srcname})
        return out
    except Exception as ex:
        log(f"    tìm lỗi: {ex}"); return []


def mo_ta_phien(r):
    """Thông tin phiên để Gemini đối chiếu."""
    parts = [f"mã {r.get('id', '')}", f"ngày phiên {r.get('ngay', '')}", f"tỉnh {r.get('tinh', '')}", f"xã/phường (tên mới) {r.get('xa', '')}"]
    if r.get("dia_danh_cu"): parts.append(f"địa danh cũ {r['dia_danh_cu']}")
    if r.get("khu"): parts.append(f"khu {r['khu']}")
    if r.get("so_thua"): parts.append(f"{r['so_thua']} thửa")
    if r.get("gia_khoi_diem"): parts.append(f"giá khởi điểm {r['gia_khoi_diem']} tr/m²")
    if r.get("gia_trung_max"): parts.append(f"giá trúng cao nhất {r['gia_trung_max']} tr/m²")
    if r.get("loai_dat"): parts.append(r["loai_dat"])
    return "; ".join(parts)


# ---------------------------------------------------------------- Gemini: chọn tiêu đề
PICK = """Phiên đấu giá quyền sử dụng đất cần tìm tin: {phien}.
Mục tiêu: tìm bài báo nêu {muc_tieu} của CHÍNH phiên này.
Dưới đây là các tiêu đề tin (đánh số). Chọn TỐI ĐA 3 tiêu đề có khả năng cao nói về đúng phiên này (đúng xã/khu, đúng đợt),
xếp theo khả năng giảm dần. Bỏ tiêu đề về xã/tỉnh khác, phiên khác, thông báo trước phiên, chính sách chung.
Trả về DUY NHẤT JSON {{"chon":[số…]}} (không có thì {{"chon":[]}}).

{ds}"""


def pick_titles(r, cands, muc_tieu):
    if not cands: return []
    ds = "\n".join(f"{i + 1}. {c['title']} ({c['src']}, đăng {c['pub'] or '?'})" for i, c in enumerate(cands))
    j = Q.gemini_json(PICK.format(phien=mo_ta_phien(r), muc_tieu=muc_tieu, ds=ds))
    if not j: return None
    out = []
    for x in j.get("chon", []):
        try:
            i = int(x) - 1
            if 0 <= i < len(cands) and cands[i] not in out: out.append(cands[i])
        except Exception: pass
    return out[:READ_PER_SESSION]


# ---------------------------------------------------------------- Gemini: đọc bài
READ_RESULT = """Bạn kiểm tra một bài báo có nêu KẾT QUẢ của đúng phiên đấu giá quyền sử dụng đất sau không.
PHIÊN CẦN TÌM: {phien}.
BÀI ĐĂNG NGÀY: {pub} (dd/mm/yyyy). TIÊU ĐỀ: {title}

Trả về DUY NHẤT JSON với các khóa (không biết thì ""):
- khop: "co" nếu bài nêu kết quả của CHÍNH phiên này (đúng xã – tên mới hoặc cũ, đúng khu/đợt), "khong" nếu nói phiên/khu/xã/đợt khác
  hoặc chỉ là thông báo trước phiên.
- ly_do: tối đa 15 chữ, vì sao khớp / không khớp.
- ngay_phien: ngày tổ chức phiên ghi trong bài, YYYY-MM-DD (suy ra năm sao cho không sau ngày đăng bài); bài không ghi → "".
  KHÔNG dùng ngày đăng bài hay ngày phê duyệt kết quả.
- so_thua_trung, nguoi_tham_gia: số nguyên.  gia_trung_min, gia_trung_max: TRIỆU đồng/m² (VD 125.2; "1,5 tỷ đồng/m²" → 1500).
- tong_tien_ty: tổng tiền trúng, tỷ đồng.
- dau_gia_lai: "co" nếu phiên không thành / còn thửa ế phải đấu lại, không thì "".
- bo_coc: "co" nếu bài nói có người trúng phiên này bỏ cọc / không nộp tiền.
- ghi_chu: tối đa 3 ý rất ngắn, cách nhau "; " (VD số thửa ế, chênh bao nhiêu lần giá khởi điểm). Tự viết lại, không chép.
Chỉ ghi số liệu của CHÍNH phiên này, không lấy số liệu phiên khác hay năm trước để so sánh. Không suy đoán.

NỘI DUNG:
{text}"""

READ_AFTER = """Bạn kiểm tra một bài báo có nêu tình hình SAU PHIÊN của đúng phiên đấu giá quyền sử dụng đất sau không.
PHIÊN: {phien}.
BÀI ĐĂNG NGÀY: {pub} (dd/mm/yyyy). TIÊU ĐỀ: {title}

Trả về DUY NHẤT JSON (không biết thì ""):
- khop: "co" nếu bài nói về CHÍNH phiên này (đúng xã – tên mới hoặc cũ, đúng khu/đợt, đúng thời điểm), "khong" nếu khác.
- ly_do: tối đa 15 chữ.
- sau_dau_gia: "Bỏ cọc" (có người trúng bỏ cọc / không nộp tiền) | "Đã hủy kết quả" | "Đã nộp đủ tiền" | "Đã nộp một phần" | "".
- huy_ca_phien: "co" nếu toàn bộ phiên bị hủy hoặc mọi thửa trúng đều bị bỏ cọc, không thì "".
- ghi_chu: tối đa 2 ý rất ngắn (VD "Bỏ cọc 12/40 thửa"). Tự viết lại, không chép.
Không suy đoán.

NỘI DUNG:
{text}"""


def read_article(prompt_tpl, r, c, link, texts):
    """Tải bài + hỏi Gemini. Trả dict / None (lỗi, để lần sau) / {} (không đọc được bài)."""
    try: raw = texts.get(link) or D.fetch_text(link); texts[link] = raw
    except Exception as ex: log(f"    không đọc được {link}: {ex}"); return {}
    text = D.body(raw, c["title"])
    if len(text) < 200: return {}
    pub = c["pub"].strftime("%d/%m/%Y") if c["pub"] else "không rõ"
    return Q.gemini_json(prompt_tpl.format(phien=mo_ta_phien(r), pub=pub, title=c["title"], text=text[:14000]))


def candidates(r, d, qs, sk, seen, pub_to):
    cands = []
    for q in qs:
        for it in search(q)[:8]:
            if sk(it["link"]) in seen or any(x["link"] == it["link"] for x in cands): continue
            if not it["pub"] or it["pub"] < d or it["pub"] > pub_to: continue        # bài phải đăng sau phiên, trong khoảng cho phép
            cands.append(it)
        time.sleep(1.2)
    return cands[:20]


def resolve(c, links):
    link = links.get(c["link"]) or (D.decode_google(c["link"]) if "news.google." in c["link"] else c["link"])
    links[c["link"]] = link
    return link if link and "news.google." not in link else None


# ---------------------------------------------------------------- 0. chuẩn hóa trạng thái + xóa phiên chờ quá lâu
AUTO_ST = ("Sắp đấu giá", "Chờ kết quả", "Đã đấu giá")


def trang_thai_dung(r):
    """Trạng thái đúng theo quy ước; None = không tự đổi (Đấu giá lại, Hủy/bỏ cọc, chưa có ngày…)."""
    st = r.get("trang_thai", "")
    if st not in AUTO_ST: return None
    if Q.num(r.get("gia_trung_max")) or r.get("sau_dau_gia") in FINAL_AFTER: return "Đã đấu giá"
    d = Q.iso(r.get("ngay"))
    if not d: return None
    return "Sắp đấu giá" if d >= TODAY else "Chờ kết quả"


def chuan_hoa(drows):
    """Đổi trạng thái theo quy ước; trả (danh sách thay đổi, dòng giữ lại, dòng bị xóa)."""
    doi, giu, xoa = [], [], []
    for r in drows:
        st = trang_thai_dung(r)
        if st and st != r.get("trang_thai"):
            doi.append(f"{r.get('id')}: {r.get('trang_thai')} → {st}"); r["trang_thai"] = st
        d = Q.iso(r.get("ngay"))
        if r.get("trang_thai") == "Chờ kết quả" and d and (TODAY - d).days > CHO_KQ_MAX_DAYS: xoa.append(r)
        else: giu.append(r)
    return doi, giu, xoa


def luu_tru(head, rows):
    """Ghi thêm các phiên bị xóa vào data/phien-da-xoa.csv (kèm ngày xóa, lý do)."""
    ah, arows = read_csv(ARCHIVE)
    h = ah or (head + ["_ngay_xoa", "_ly_do_xoa"])
    for x in head + ["_ngay_xoa", "_ly_do_xoa"]:
        if x not in h: h.append(x)
    for r in rows:
        arows.append({**r, "_ngay_xoa": TODAY.isoformat(), "_ly_do_xoa": f"Chờ kết quả quá {CHO_KQ_MAX_DAYS} ngày"})
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    write_csv(ARCHIVE, h, arows)


# ---------------------------------------------------------------- 1. tìm kết quả
def find_results(drows, dhead, prows, seen, waiting):
    todo = []
    for r in drows:
        d = Q.iso(r.get("ngay"))
        if not d or r.get("gia_trung_max") or r.get("id") in waiting: continue
        if r.get("trang_thai") != "Chờ kết quả" or not (0 <= (TODAY - d).days <= MAX_DAYS): continue
        todo.append((d, r))
    last = lambda r: seen.get(f"{r.get('id')}|_lan_tim", "")
    todo.sort(key=lambda x: x[0], reverse=True); todo.sort(key=lambda x: last(x[1]))
    log(f"Phiên đã qua ngày chưa có kết quả: {len(todo)} (xử lý tối đa {MAX_SESSIONS})")
    found, links, texts = 0, {}, {}
    for d, r in todo[:MAX_SESSIONS]:
        if ai_off() or not time_left(0.7): log("⏱ Dừng phần tìm kết quả (hết giờ / hết hạn mức Gemini) – lần sau làm tiếp."); break
        xa = r.get("xa") or ""
        kt = " ".join(sorted(toks(r.get("khu"), xa), key=len, reverse=True)[:2])
        after, before = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=PUB_WINDOW)).isoformat()
        qs = [f'"{xa}" "đấu giá" {kt} after:{after} before:{before}'.replace("  ", " "), f'"{xa}" "trúng đấu giá" after:{after} before:{before}']
        qs += [f'"{o}" "đấu giá" after:{after} before:{before}' for o in old_communes(r)[:2]]
        log(f"• {r.get('id')} {xa} {d} – {r.get('khu', '')[:50]}")
        seen[f"{r.get('id')}|_lan_tim"] = TODAY.isoformat()
        sk = lambda u: f"{r.get('id')}|{u}"
        cands = candidates(r, d, qs, sk, seen, d + timedelta(days=PUB_WINDOW))
        if not cands: log("    (không có bài mới)"); continue
        picked = pick_titles(r, cands, "kết quả (giá trúng, số thửa trúng)")
        if picked is None: continue                               # lỗi / hết hạn mức → lần sau
        for c in cands:
            if c not in picked: seen[sk(c["link"])] = TODAY.isoformat()      # tiêu đề Gemini đã loại → không xét lại
        best = None
        for c in picked:
            link = resolve(c, links)
            if not link: continue
            if link in (r.get("link"), r.get("link_thong_bao")): seen[sk(c["link"])] = TODAY.isoformat(); continue
            j = read_article(READ_RESULT, r, c, link, texts)
            if j is None:
                if ai_off(): break
                continue
            seen[sk(c["link"])] = seen[sk(link)] = TODAY.isoformat()
            if not j: continue
            ok = str(j.get("khop", "")).lower() in ("co", "có")
            gmax, sttr = Q.num(j.get("gia_trung_max")), Q.num(j.get("so_thua_trung"))
            dd = Q.iso(j.get("ngay_phien"))
            why = j.get("ly_do", "")
            if ok and dd and abs((dd - d).days) > 3: ok, why = False, f"ngày phiên trong bài {dd} khác {d}"
            if ok and not (gmax or sttr) and str(j.get("dau_gia_lai", "")).lower() not in ("co", "có"): ok, why = False, "bài chưa có số liệu kết quả"
            log(f"    {'✓' if ok else '·'} {c['title'][:75]} – {why}")
            if ok:
                best = (c, link, j); break
            time.sleep(1.5)
        if not best: continue
        c, link, j = best
        p = {k: r.get(k, "") for k in dhead}
        for k in ("gia_trung_min", "gia_trung_max", "tong_tien_ty"):
            v = Q.num(j.get(k)); p[k] = Q.fmt(v) if v and v > 0 else p.get(k, "")
        for k in ("so_thua_trung", "nguoi_tham_gia"):
            v = Q.num(j.get(k)); p[k] = str(int(v)) if v and v > 0 else p.get(k, "")
        lai = str(j.get("dau_gia_lai", "")).lower() in ("co", "có")
        p["trang_thai"] = "Đã đấu giá" if (p.get("gia_trung_max") or p.get("so_thua_trung")) else ("Đấu giá lại" if lai else "Đã đấu giá")
        if str(j.get("bo_coc", "")).lower() in ("co", "có"): p["sau_dau_gia"] = "Bỏ cọc"
        add = [x.strip() for x in str(j.get("ghi_chu", "")).split(";") if x.strip() and x.strip() not in (p.get("ghi_chu") or "")]
        if add: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), *add]))
        p["link_thong_bao"] = r.get("link_thong_bao") or r.get("link", "")
        p["link"], p["nguon"] = link, D.src_name(link, c.get("src") or p.get("nguon"))
        p["link_ket_qua"], p["nguon_ket_qua"] = link, p["nguon"]
        p.update({"_cap_nhat_id": r.get("id", ""), "_tieu_de": c["title"], "_ngay_dang": c["pub"].isoformat() if c["pub"] else "",
                  "_ngay_lay": TODAY.isoformat(), "_cach_lay": "tìm kết quả (Gemini)", "_da_doc": TODAY.isoformat(),
                  "_tu_dien": ",".join(k for k in RESULT_KEYS + ["trang_thai", "sau_dau_gia"] if p.get(k))})
        prows.append(p); waiting.add(r.get("id")); found += 1
        log(f"    → dòng cập nhật kết quả cho {r.get('id')}: " + ", ".join(f"{k}={p[k]}" for k in RESULT_KEYS if p.get(k)))
    return found


# ---------------------------------------------------------------- 2. tin sau đấu giá
def check_after(drows, dhead, prows, seen, waiting):
    todo = []
    for r in drows:
        d = Q.iso(r.get("ngay"))
        if not d or r.get("trang_thai") != "Đã đấu giá" or r.get("sau_dau_gia") in FINAL_AFTER or r.get("id") in waiting: continue
        if 0 <= (TODAY - d).days <= AFTER_DAYS: todo.append((d, r))
    last = lambda r: seen.get(f"{r.get('id')}|_lan_sau", "")
    todo.sort(key=lambda x: x[0], reverse=True); todo.sort(key=lambda x: last(x[1]))
    log(f"Phiên đã đấu giá cần kiểm tra bỏ cọc / hủy kết quả: {len(todo)} (tối đa {MAX_AFTER})")
    n, links, texts = 0, {}, {}
    for d, r in todo[:MAX_AFTER]:
        if ai_off() or not time_left(): log("⏱ Dừng phần tin sau đấu giá – lần sau làm tiếp."); break
        xa = r.get("xa") or ""
        after = (d - timedelta(days=1)).isoformat()
        qs = [f'"{xa}" đấu giá "bỏ cọc" after:{after}', f'"{xa}" đấu giá "hủy kết quả" after:{after}']
        qs += [f'"{o}" đấu giá "bỏ cọc" after:{after}' for o in old_communes(r)[:1]]
        log(f"• [sau ĐG] {r.get('id')} {xa} {d}")
        seen[f"{r.get('id')}|_lan_sau"] = TODAY.isoformat()
        sk = lambda u: f"{r.get('id')}|sau|{u}"
        cands = candidates(r, d, qs, sk, seen, TODAY)
        if not cands: continue
        picked = pick_titles(r, cands, "tình hình sau phiên (bỏ cọc, hủy kết quả, nộp đủ tiền)")
        if picked is None: continue
        for c in cands:
            if c not in picked: seen[sk(c["link"])] = TODAY.isoformat()
        best = None
        for c in picked:
            link = resolve(c, links)
            if not link: continue
            j = read_article(READ_AFTER, r, c, link, texts)
            if j is None:
                if ai_off(): break
                continue
            seen[sk(c["link"])] = seen[sk(link)] = TODAY.isoformat()
            if not j: continue
            ok = str(j.get("khop", "")).lower() in ("co", "có") and j.get("sau_dau_gia") in ("Bỏ cọc", "Đã hủy kết quả", "Đã nộp đủ tiền", "Đã nộp một phần")
            log(f"    {'✓' if ok else '·'} {j.get('sau_dau_gia') or '-'} – {c['title'][:70]} – {j.get('ly_do', '')}")
            if ok: best = (c, link, j); break
            time.sleep(1.5)
        if not best: continue
        c, link, j = best
        p = {k: r.get(k, "") for k in dhead}
        p["sau_dau_gia"], p["sau_dau_gia_ngay"] = j["sau_dau_gia"], (c["pub"] or TODAY).isoformat()
        if str(j.get("huy_ca_phien", "")).lower() in ("co", "có"): p["trang_thai"] = "Hủy/bỏ cọc"
        add = [x.strip() for x in str(j.get("ghi_chu", "")).split(";") if x.strip() and x.strip() not in (p.get("ghi_chu") or "")]
        if add: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), *add]))
        p.update({"link": link, "nguon": D.src_name(link, c.get("src") or p.get("nguon")), "link_sau_dau_gia": link, "_giu_link": "1",
                  "_cap_nhat_id": r.get("id", ""), "_tieu_de": c["title"], "_ngay_dang": c["pub"].isoformat() if c["pub"] else "",
                  "_ngay_lay": TODAY.isoformat(), "_cach_lay": "tin sau đấu giá (Gemini)", "_da_doc": TODAY.isoformat(),
                  "_tu_dien": "sau_dau_gia,sau_dau_gia_ngay" + (",trang_thai" if p["trang_thai"] == "Hủy/bỏ cọc" else "")})
        prows.append(p); waiting.add(r.get("id")); n += 1
        log(f"    → cập nhật sau đấu giá cho {r.get('id')}: {p['sau_dau_gia']}")
    return n


def main():
    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PEND)
    seen = Q.load_json(SEEN, {})
    seen = {k: v for k, v in seen.items() if v >= (TODAY - timedelta(days=260)).isoformat()}
    doi, drows, xoa = chuan_hoa(drows)
    if doi: log(f"Chuẩn hóa trạng thái {len(doi)} phiên: " + "; ".join(doi))
    if xoa:
        luu_tru(dhead, xoa)
        ids = {r.get("id") for r in xoa}
        log(f"Xóa {len(xoa)} phiên 'Chờ kết quả' quá {CHO_KQ_MAX_DAYS} ngày (đã lưu ở data/phien-da-xoa.csv): " + ", ".join(sorted(ids)))
        n0 = len(prows); prows = [p for p in prows if p.get("_cap_nhat_id") not in ids]     # dòng cập nhật cho phiên đã xóa → bỏ
        if n0 - len(prows): log(f"  bỏ {n0 - len(prows)} dòng chờ duyệt đang ghép vào các phiên đã xóa")
    if doi or xoa: write_csv(DATA, dhead, drows)
    if not D.AI_KEY:
        log("⚠ Chưa có GEMINI_API_KEY – bỏ qua phần tìm kết quả / bỏ cọc (tránh ghép nhầm).")
        if xoa: write_csv(PEND, phead or dhead, prows)
        return
    # dọn dòng cập nhật do bản cũ (đọc bằng quy tắc) tạo, chưa duyệt – hay ghép nhầm và chặn việc tìm lại phiên đó
    n0 = len(prows)
    prows = [p for p in prows if not (re.match(r"(tìm kết quả|tin sau đấu giá) \((quy tắc|Gemini), điểm", p.get("_cach_lay") or ""))]
    if n0 - len(prows): log(f"Đã xóa {n0 - len(prows)} dòng cập nhật cũ (bản chấm điểm bằng quy tắc) – các phiên đó sẽ được tìm lại bằng Gemini")
    waiting = {p.get("_cap_nhat_id") for p in prows if p.get("_cap_nhat_id")}
    found = find_results(drows, dhead, prows, seen, waiting)
    found += check_after(drows, dhead, prows, seen, waiting)
    head = phead or dhead
    for h in dhead + ["sau_dau_gia", "sau_dau_gia_ngay", "link_sau_dau_gia", "link_ket_qua", "nguon_ket_qua", "_giu_link", "link_thong_bao",
                      "_cap_nhat_id", "_tieu_de", "_ngay_dang", "_ngay_lay", "_cach_lay", "_tu_dien", "_da_doc"]:
        if h not in head: head.append(h)
    write_csv(PEND, head, prows)
    Q.save_json(SEEN, seen)
    log(f"Xong: {found} dòng cập nhật (kết quả / sau đấu giá) → cho-duyet.csv · Gemini {D._ai_state['calls']} lượt"
        + (" – đã hết hạn mức, lần sau làm tiếp" if D._ai_state["off"] else ""))


if __name__ == "__main__":
    main()
