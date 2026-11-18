#!/usr/bin/env python3
"""
Tìm kết quả cho các phiên đã qua ngày  /  Find results for auctions whose date has passed

Chạy hằng ngày trong workflow "Tìm kết quả đấu giá" (.github/workflows/tim-ket-qua.yml):
    python scripts/tim_ket_qua.py

1. Lấy trong data.csv các phiên "Chờ kết quả" (hoặc "Sắp đấu giá" nhưng đã qua ngày) chưa có giá trúng,
   trong vòng 120 ngày gần đây.
2. Với mỗi phiên, tìm bài báo trên Google News theo tên xã (mới và cũ) + khu, đăng sau ngày phiên.
   Link Google News được giải ra link báo gốc.
3. Đọc bài, bóc số liệu (scripts/dien_tin_cho_duyet.py). Chỉ nhận bài khi:
   - bài nêu kết quả (giá trúng / số thửa trúng / "đấu giá thành công"), và
   - đúng xã (tên mới hoặc cũ) và đúng khu (trùng tên khu/thôn) hoặc đúng ngày phiên.
4. Bài khớp → thêm vào cho-duyet.csv một dòng "cập nhật kết quả" (cột _cap_nhat_id = mã phiên).
   Trong admin, bấm Duyệt → kết quả được GHI VÀO PHIÊN CŨ (không tạo phiên mới); link thông báo cũ
   được giữ ở cột link_thong_bao, link bài kết quả thành link nguồn chính.
Bài đã xem lưu ở data/ket-qua-da-xem.json để không đọc lại.

Không cần thư viện ngoài (chỉ Python 3 chuẩn).
"""
import csv, html, io, json, os, re, sys, time, urllib.parse
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
PEND = os.path.join(ROOT, "cho-duyet.csv")
SEEN = os.path.join(ROOT, "data", "ket-qua-da-xem.json")
TODAY = datetime.now(timezone(timedelta(hours=7))).date()
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "40"))
MAX_DAYS = 120
GN = "https://news.google.com/rss/search?hl=vi&gl=VN&ceid=VN:vi&q="
RESULT_KEYS = ["so_thua_trung", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]
STOP = set("khu diem o lo dat thua thon xom to dan pho lang xa phuong huyen quan thi tran va cac tai dau gia quyen su dung con lai khac moi cu so nhung cum ho tro dinh cu xen ket hoi truong ubnd phuc vu san xuat nong nghiep truc tuyen".split())


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


def search(q):
    try:
        raw = D.get(GN + urllib.parse.quote(q), timeout=30)
        root = ET.fromstring(raw.encode("utf-8"))
        out = []
        for it in root.iter("item"):
            pub = it.findtext("pubDate") or ""
            try: pd = datetime.strptime(pub.strip(), "%a, %d %b %Y %H:%M:%S %Z").date()
            except Exception: pd = None
            out.append({"title": html.unescape(it.findtext("title") or ""), "link": (it.findtext("link") or "").strip(), "pub": pd})
        return out
    except Exception as ex:
        print(f"    tìm lỗi: {ex}"); return []


def matches(r, ex, text, sess_date):
    """Bài có đúng là kết quả của phiên r không? Trả về điểm (0 = không khớp)."""
    ft = fold(text)
    has_result = ex.get("gia_trung_max") or ex.get("so_thua_trung") or re.search(r"dau gia thanh cong|trung dau gia|ket qua dau gia", ft)
    if not has_result: return 0
    names = [r.get("xa")] + old_communes(r)
    if not any(n and fold(n) in ft for n in names): return 0
    score = 1
    kt = toks(r.get("khu"), r.get("xa"))
    if kt and len(kt & toks(text)) >= min(2, len(kt)): score += 2
    d = ex.get("ngay")
    if d:
        try:
            gap = abs((date.fromisoformat(d) - sess_date).days)
            if gap <= 3: score += 2
            elif gap > 20: return 0          # bài nói về phiên khác ngày
        except Exception: pass
    dm = f"{sess_date.day}/{sess_date.month}"
    if re.search(rf"(?<!\d){sess_date.day}\s*/\s*0?{sess_date.month}(?!\d)", text): score += 1
    if r.get("so_thua") and ex.get("so_thua") == r.get("so_thua"): score += 1
    if ex.get("gia_khoi_diem") and r.get("gia_khoi_diem"):
        try:
            if abs(float(ex["gia_khoi_diem"]) - float(r["gia_khoi_diem"])) < 0.01: score += 2
        except Exception: pass
    return score if score >= 3 else 0


def main():
    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PEND)
    try: seen = json.load(open(SEEN, encoding="utf-8"))
    except Exception: seen = {}
    seen = {k: v for k, v in seen.items() if v >= (TODAY - timedelta(days=200)).isoformat()}
    waiting = {p.get("_cap_nhat_id") for p in prows if p.get("_cap_nhat_id")}
    todo = []
    for r in drows:
        try: d = date.fromisoformat((r.get("ngay") or "")[:10])
        except Exception: continue
        st = r.get("trang_thai", "")
        if r.get("gia_trung_max") or r.get("id") in waiting: continue
        if not (st == "Chờ kết quả" or (st == "Sắp đấu giá" and d < TODAY)): continue
        if not (0 <= (TODAY - d).days <= MAX_DAYS): continue
        todo.append((d, r))
    todo.sort(key=lambda x: x[0], reverse=True)
    print(f"Phiên đã qua ngày chưa có kết quả: {len(todo)} (xử lý tối đa {MAX_SESSIONS})")
    found, LINKS, TEXTS = 0, {}, {}
    for d, r in todo[:MAX_SESSIONS]:
        xa = r.get("xa") or ""
        kt = " ".join(sorted(toks(r.get("khu"), xa), key=len, reverse=True)[:2])
        after, before = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=75)).isoformat()
        qs = [f'"{xa}" "đấu giá" {kt} after:{after} before:{before}'.replace("  ", " "),
              f'"{xa}" "trúng đấu giá" after:{after} before:{before}']
        qs += [f'"{o}" "đấu giá" {kt} after:{after} before:{before}' for o in old_communes(r)[:2]]
        print(f"• {r.get('id')} {xa} {d.isoformat()} – {r.get('khu', '')[:50]}")
        cands, best = [], (0, None, None, None)
        sk = lambda u: f"{r.get('id')}|{u}"          # đã xem bài này CHO PHIÊN NÀY (một bài tổng hợp có thể khớp phiên khác)
        for q in qs:
            for it in search(q)[:6]:
                if sk(it["link"]) in seen or (it["pub"] and it["pub"] < d) or any(c["link"] == it["link"] for c in cands): continue
                cands.append(it)
            time.sleep(1.2)
        for it in cands[:8]:
            link = LINKS.get(it["link"]) or (D.decode_google(it["link"]) if "news.google." in it["link"] else it["link"])
            LINKS[it["link"]] = link
            if not link: continue                       # chưa giải được link gốc → lần sau thử lại
            if link == r.get("link") or sk(link) in seen:
                seen[sk(it["link"])] = TODAY.isoformat(); continue
            try: raw = TEXTS.get(link) or D.fetch_text(link); TEXTS[link] = raw
            except Exception as ex: print(f"    không đọc được {link}: {ex}"); continue   # lỗi tải → lần sau thử lại
            seen[sk(it["link"])] = seen[sk(link)] = TODAY.isoformat()
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", it["title"])
            text = D.body(raw, title); ex = D.extract(raw, {"_tieu_de": title, "link": link})
            sc = matches(r, ex, title + " " + text, d)
            print(f"    {'✓' if sc else '·'} {sc} {title[:80]}")
            if sc > best[0]: best = (sc, link, ex, title)
            time.sleep(1.5)
        if not best[1]: continue
        sc, link, ex, title = best
        p = {k: r.get(k, "") for k in dhead}
        for k in RESULT_KEYS + ["so_thua"]:
            if ex.get(k) and (k in RESULT_KEYS or not p.get(k)): p[k] = ex[k]
        p["trang_thai"] = "Đã đấu giá"
        if ex.get("ghi_chu"):
            add = [x for x in ex["ghi_chu"].split("; ") if x not in (p.get("ghi_chu") or "")]
            if add: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), *add]))
        p["link_thong_bao"] = r.get("link_thong_bao") or r.get("link", "")
        p["link"], p["nguon"] = link, D.src_name(link, p.get("nguon"))
        p.update({"_cap_nhat_id": r.get("id", ""), "_tieu_de": title, "_ngay_lay": TODAY.isoformat(), "_cach_lay": f"tìm kết quả (điểm khớp {sc})",
                  "_tu_dien": ",".join(k for k in RESULT_KEYS + ["trang_thai"] if p.get(k)), "_da_doc": TODAY.isoformat()})
        prows.append(p); found += 1
        print(f"    → thêm dòng cập nhật kết quả cho {r.get('id')}: {', '.join(f'{k}={p[k]}' for k in RESULT_KEYS if p.get(k))}")
    head = phead or dhead
    for h in dhead + ["link_thong_bao", "_cap_nhat_id", "_tieu_de", "_ngay_lay", "_cach_lay", "_tu_dien", "_da_doc"]:
        if h not in head: head.append(h)
    write_csv(PEND, head, prows)
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Xong: tìm được kết quả cho {found} phiên → cho-duyet.csv (duyệt trong admin).")


if __name__ == "__main__":
    main()
