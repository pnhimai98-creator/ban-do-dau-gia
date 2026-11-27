#!/usr/bin/env python3
"""
Tìm kết quả cho các phiên đã qua ngày  /  Find results for auctions whose date has passed

Chạy hằng ngày trong workflow "Tìm kết quả đấu giá" (.github/workflows/tim-ket-qua.yml):
    python scripts/tim_ket_qua.py

0. Phiên "Sắp đấu giá" đã qua ngày → GHI THẲNG "Chờ kết quả" vào data.csv (chỉ đổi cột trang_thai,
   không cần duyệt vì chỉ dựa vào ngày).
1. Lấy trong data.csv các phiên "Chờ kết quả" (hoặc "Sắp đấu giá" nhưng đã qua ngày) chưa có giá trúng,
   trong vòng 240 ngày gần đây. Phiên lâu chưa được tìm nhất được tìm trước (xoay vòng).
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
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "25"))
BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "15"))   # dừng êm sau 15 phút để kịp lưu, lần sau chạy tiếp
T0 = time.monotonic()
MAX_DAYS = int(os.environ.get("MAX_DAYS", "240"))
SENTENCE_RESULT = r"dau gia thanh cong|trung dau gia|ket qua dau gia|gia trung|nguoi trung"
SENTENCE_AFTER = r"bo coc|huy (bo )?ket qua|khong nop (du )?tien|nop du tien trung|mat (tien )?coc"
GN = "https://news.google.com/rss/search?hl=vi&gl=VN&ceid=VN:vi&q="
RESULT_KEYS = ["so_thua_trung", "gia_trung_min", "gia_trung_max", "tong_tien_ty", "nguoi_tham_gia"]
MAX_AFTER = int(os.environ.get("MAX_AFTER", "20"))     # số phiên "Đã đấu giá" kiểm tra bỏ cọc mỗi lần
AFTER_DAYS = 180
FINAL_AFTER = {"Bỏ cọc", "Đã hủy kết quả", "Đã nộp đủ tiền"}
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


def near(ft, names, pat, win=400):
    """Tên xã và cụm từ khóa xuất hiện gần nhau (trong ~400 ký tự) – tránh bài tổng hợp nhắc xã ở chỗ khác."""
    for n in names:
        n = fold(n)
        if not n: continue
        for m in re.finditer(re.escape(n), ft):
            if re.search(pat, ft[max(0, m.start() - win): m.end() + win]): return True
    return False


def matches(r, ex, text, sess_date, pat=SENTENCE_RESULT):
    """Bài có đúng là kết quả của phiên r không? Trả về điểm (0 = không khớp).
    Bắt buộc: (1) có tên xã (mới/cũ) GẦN cụm từ kết quả; (2) có thêm ít nhất 1 dấu hiệu riêng của phiên:
    trùng tên khu/thôn, hoặc đúng ngày phiên, hoặc đúng giá khởi điểm."""
    ft = fold(text)
    names = [r.get("xa")] + old_communes(r)
    if not near(ft, names, pat): return 0
    if pat == SENTENCE_RESULT and not (ex.get("gia_trung_max") or ex.get("so_thua_trung") or re.search(r"dau gia thanh cong|trung dau gia", ft)): return 0
    score, strong = 1, False
    kt = toks(r.get("khu"), r.get("xa"))
    if kt and len(kt & toks(text)) >= min(2, len(kt)): score += 2; strong = True
    d = ex.get("ngay")
    if d:
        try:
            gap = abs((date.fromisoformat(d) - sess_date).days)
            if gap <= 3: score += 2; strong = True
            elif gap > 20: return 0          # bài nói về phiên khác ngày
        except Exception: pass
    if re.search(rf"(?<!\d){sess_date.day}\s*/\s*0?{sess_date.month}(?!\d)", text): score += 1; strong = True
    if r.get("so_thua") and ex.get("so_thua") == r.get("so_thua"): score += 1
    if ex.get("gia_khoi_diem") and r.get("gia_khoi_diem"):
        try:
            if abs(float(ex["gia_khoi_diem"]) - float(r["gia_khoi_diem"])) < 0.01: score += 2; strong = True
        except Exception: pass
    return score if (strong and score >= 3) else 0


def chuyen_qua_ngay(drows):
    """'Sắp đấu giá' mà ngày phiên đã qua → 'Chờ kết quả' (ghi thẳng data.csv)."""
    out = []
    for r in drows:
        if r.get("trang_thai") != "Sắp đấu giá": continue
        try: d = date.fromisoformat((r.get("ngay") or "")[:10])
        except Exception: continue               # chưa có ngày → để nguyên
        if d < TODAY:
            r["trang_thai"] = "Chờ kết quả"; out.append(r.get("id") or r.get("xa"))
    return out


def check_after(drows, dhead, prows, seen, waiting):
    """Lượt 2: phiên 'Đã đấu giá' trong 180 ngày → tìm tin bỏ cọc / hủy kết quả / nộp đủ tiền."""
    todo = []
    for r in drows:
        try: d = date.fromisoformat((r.get("ngay") or "")[:10])
        except Exception: continue
        if r.get("trang_thai") != "Đã đấu giá" or r.get("sau_dau_gia") in FINAL_AFTER or r.get("id") in waiting: continue
        if 0 <= (TODAY - d).days <= AFTER_DAYS: todo.append((d, r))
    todo.sort(key=lambda x: x[0], reverse=True)
    print(f"Phiên đã đấu giá cần kiểm tra bỏ cọc/hủy kết quả: {len(todo)} (tối đa {MAX_AFTER})")
    n, TEXTS = 0, {}
    for d, r in todo[:MAX_AFTER]:
        if time.monotonic() - T0 > BUDGET_MIN * 60:
            print("⏱ Hết giờ – phần kiểm tra bỏ cọc còn lại để lần sau."); break
        xa = r.get("xa") or ""
        after = (d - timedelta(days=1)).isoformat()
        qs = [f'"{xa}" đấu giá "bỏ cọc" after:{after}', f'"{xa}" đấu giá "hủy kết quả" after:{after}']
        qs += [f'"{o}" đấu giá "bỏ cọc" after:{after}' for o in old_communes(r)[:1]]
        sk = lambda u: f"{r.get('id')}|sau|{u}"
        print(f"• [sau ĐG] {r.get('id')} {xa} {d.isoformat()}")
        cands = []
        for q in qs:
            for it in search(q)[:5]:
                if sk(it["link"]) in seen or (it["pub"] and it["pub"] < d) or any(c["link"] == it["link"] for c in cands): continue
                cands.append(it)
            time.sleep(1.2)
        best = (0, None, None, None)
        for it in cands[:4]:
            link = D.decode_google(it["link"]) if "news.google." in it["link"] else it["link"]
            if not link: continue
            try: raw = TEXTS.get(link) or D.fetch_text(link); TEXTS[link] = raw
            except Exception as ex: print(f"    không đọc được {link}: {ex}"); continue
            seen[sk(it["link"])] = seen[sk(link)] = TODAY.isoformat()
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", it["title"])
            ex, how = D.extract_best(raw, {**r, "_tieu_de": title, "link": link})
            if ex.get("_khong_khop") or not ex.get("sau_dau_gia"): print(f"    · {title[:80]}"); continue
            text = title + " " + D.body(raw, title)
            sc = matches(r, ex, text, d, pat=SENTENCE_AFTER)   # tên xã phải đứng gần chữ "bỏ cọc / hủy kết quả…"
            if how == D.AI_NAME and not sc: sc = 3
            if sc and r.get("gia_trung_max") and r["gia_trung_max"].replace(".", ",") in text: sc += 2   # bài nhắc đúng giá trúng của phiên
            print(f"    {'✓' if sc else '·'} {sc} {ex.get('sau_dau_gia')} – {title[:70]}")
            if sc > best[0]: best = (sc, link, ex, title)
            time.sleep(1.5)
        if not best[1]: continue
        sc, link, ex, title = best
        p = {k: r.get(k, "") for k in dhead}
        p["sau_dau_gia"], p["sau_dau_gia_ngay"] = ex["sau_dau_gia"], TODAY.isoformat()
        if ex.get("trang_thai") == "Hủy/bỏ cọc": p["trang_thai"] = "Hủy/bỏ cọc"
        if ex.get("ghi_chu"):
            add = [x for x in ex["ghi_chu"].split("; ") if x.startswith("Bỏ cọc") and x not in (p.get("ghi_chu") or "")]
            if add: p["ghi_chu"] = "; ".join(filter(None, [p.get("ghi_chu"), *add]))
        p.update({"link": link, "nguon": D.src_name(link, p.get("nguon")), "_giu_link": "1", "_cap_nhat_id": r.get("id", ""),
                  "_tieu_de": title, "_ngay_lay": TODAY.isoformat(), "_cach_lay": f"tin sau đấu giá ({D.AI_NAME if D.AI_KEY else 'quy tắc'}, điểm {sc})",
                  "_tu_dien": "sau_dau_gia,sau_dau_gia_ngay" + (",trang_thai" if p["trang_thai"] == "Hủy/bỏ cọc" else ""), "_da_doc": TODAY.isoformat()})
        prows.append(p); waiting.add(r.get("id")); n += 1
        print(f"    → cập nhật sau đấu giá cho {r.get('id')}: {p['sau_dau_gia']}")
    return n


def main():
    dhead, drows = read_csv(DATA)
    phead, prows = read_csv(PEND)
    try: seen = json.load(open(SEEN, encoding="utf-8"))
    except Exception: seen = {}
    seen = {k: v for k, v in seen.items() if v >= (TODAY - timedelta(days=260)).isoformat()}
    if not D.AI_KEY:
        print("⚠ CHƯA CÓ GEMINI_API_KEY (Settings → Secrets and variables → Actions). Đang dùng quy tắc – dễ sót/nhầm kết quả.")
    moved = chuyen_qua_ngay(drows)
    if moved:
        write_csv(DATA, dhead, drows)
        print(f"Đã chuyển {len(moved)} phiên quá ngày 'Sắp đấu giá' → 'Chờ kết quả': {', '.join(moved)}")
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
    # Xoay vòng: phiên lâu chưa tìm nhất lên trước; cùng lần tìm thì phiên mới hơn lên trước
    last = lambda r: seen.get(f"{r.get('id')}|_lan_tim", "")
    todo.sort(key=lambda x: x[0], reverse=True)
    todo.sort(key=lambda x: last(x[1]))
    print(f"Phiên đã qua ngày chưa có kết quả: {len(todo)} (xử lý tối đa {MAX_SESSIONS})")
    found, LINKS, TEXTS = 0, {}, {}
    for d, r in todo[:MAX_SESSIONS]:
        if time.monotonic() - T0 > BUDGET_MIN * 60 * 0.7:      # 70% thời gian cho kết quả, 30% cho bỏ cọc
            print(f"⏱ Hết thời gian phần tìm kết quả – các phiên còn lại để lần chạy sau."); break
        xa = r.get("xa") or ""
        kt = " ".join(sorted(toks(r.get("khu"), xa), key=len, reverse=True)[:2])
        after, before = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=75)).isoformat()
        qs = [f'"{xa}" "đấu giá" {kt} after:{after} before:{before}'.replace("  ", " "),
              f'"{xa}" "trúng đấu giá" after:{after} before:{before}']
        qs += [f'"{o}" "đấu giá" {kt} after:{after} before:{before}' for o in old_communes(r)[:2]]
        print(f"• {r.get('id')} {xa} {d.isoformat()} – {r.get('khu', '')[:50]}")
        seen[f"{r.get('id')}|_lan_tim"] = TODAY.isoformat()
        cands, best = [], (0, None, None, None)
        sk = lambda u: f"{r.get('id')}|{u}"          # đã xem bài này CHO PHIÊN NÀY (một bài tổng hợp có thể khớp phiên khác)
        for q in qs:
            for it in search(q)[:6]:
                if sk(it["link"]) in seen or (it["pub"] and it["pub"] < d) or any(c["link"] == it["link"] for c in cands): continue
                cands.append(it)
            time.sleep(1.2)
        for it in cands[:5]:
            link = LINKS.get(it["link"]) or (D.decode_google(it["link"]) if "news.google." in it["link"] else it["link"])
            LINKS[it["link"]] = link
            if not link: continue                       # chưa giải được link gốc → lần sau thử lại
            if link == r.get("link") or sk(link) in seen:
                seen[sk(it["link"])] = TODAY.isoformat(); continue
            try: raw = TEXTS.get(link) or D.fetch_text(link); TEXTS[link] = raw
            except Exception as ex: print(f"    không đọc được {link}: {ex}"); continue   # lỗi tải → lần sau thử lại
            seen[sk(it["link"])] = seen[sk(link)] = TODAY.isoformat()
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", it["title"])
            text = D.body(raw, title)
            ex, how = D.extract_best(raw, {**r, "_tieu_de": title, "link": link})   # AI được báo phiên cần tìm
            if ex.get("_khong_khop"): sc = 0                                       # AI: bài nói về phiên khác
            else:
                sc = matches(r, ex, title + " " + text, d)
                if how == D.AI_NAME and not sc and (ex.get("gia_trung_max") or ex.get("so_thua_trung")): sc = 3   # AI xác nhận đúng phiên
            ex.pop("_khong_khop", None)
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
        p.update({"_cap_nhat_id": r.get("id", ""), "_tieu_de": title, "_ngay_lay": TODAY.isoformat(), "_cach_lay": f"tìm kết quả ({D.AI_NAME if D.AI_KEY else 'quy tắc'}, điểm khớp {sc})",
                  "_tu_dien": ",".join(k for k in RESULT_KEYS + ["trang_thai"] if p.get(k)), "_da_doc": TODAY.isoformat()})
        prows.append(p); found += 1
        print(f"    → thêm dòng cập nhật kết quả cho {r.get('id')}: {', '.join(f'{k}={p[k]}' for k in RESULT_KEYS if p.get(k))}")
    found += check_after(drows, dhead, prows, seen, waiting)
    head = phead or dhead
    for h in dhead + ["sau_dau_gia", "sau_dau_gia_ngay", "link_sau_dau_gia", "_giu_link", "link_thong_bao", "_cap_nhat_id", "_tieu_de", "_ngay_lay", "_cach_lay", "_tu_dien", "_da_doc"]:
        if h not in head: head.append(h)
    write_csv(PEND, head, prows)
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Xong: {found} dòng cập nhật (kết quả / bỏ cọc) → cho-duyet.csv, duyệt trong admin.")


if __name__ == "__main__":
    main()
