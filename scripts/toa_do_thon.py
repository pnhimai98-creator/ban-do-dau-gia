#!/usr/bin/env python3
"""
Đặt tọa độ theo THÔN / KHU thay cho tâm xã  /  Place auctions at their village instead of the commune centre

    python scripts/toa_do_thon.py

Với các phiên trong data.csv có vị trí "gần đúng" (tâm xã): lấy tên thôn/xóm/tổ dân phố/khu trong cột
"khu" (và xã cũ trong "dia_danh_cu"), tìm trên OpenStreetMap (Nominatim) TRONG PHẠM VI 6 km quanh điểm hiện tại.
Tìm được → đổi tọa độ, ghi vi_tri = "theo thôn". Không tìm được → giữ nguyên (thử lại sau 30 ngày).
Ưu tiên các phiên đã có giá trúng. Phiên đã chỉnh tay ("chính xác") không bao giờ bị đổi.

Tọa độ chính xác tới từng khu đất thì báo chí không nêu: hãy chỉnh tay trong admin.html
(tab "Form & chọn vị trí", nền "Ảnh vệ tinh") và chọn "chính xác".
"""
import csv, io, json, math, os, re, sys, time, unicodedata, urllib.parse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dien_tin_cho_duyet as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.csv")
TRIED = os.path.join(ROOT, "data", "toa-do-da-thu.json")
MAX_ROWS = int(os.environ.get("MAX_GEOCODE", "60"))
RADIUS_KM = 6
PLACE = re.compile(r"(thôn|xóm|tổ dân phố|TDP|làng|khu|cụm|ấp|khu phố)\s+([^,;()–\-]+)", re.I)


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn").lower()).strip()


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 110.54)


def candidates(r):
    """Tên thôn/khu theo thứ tự ưu tiên: thôn/xóm/TDP trước, khu sau."""
    out = []
    for m in PLACE.finditer(r.get("khu") or ""):
        kind, name = m.group(1).lower(), m.group(2).strip()
        name = re.sub(r"\s+(và|cùng|tại)\b.*$", "", name).strip()
        if not name or not name[0].isupper() or re.fullmatch(r"[A-Z]?\d+[A-Z]?|X\d+|đấu giá.*|đất.*|vực.*", name, re.I): continue
        out.append((0 if kind in ("thôn", "xóm", "tổ dân phố", "tdp", "làng", "ấp") else 1, kind, name))
    out.sort(key=lambda x: x[0])
    return [(k, n) for _, k, n in out][:3]


def nominatim(q, center):
    lon, lat = center
    d = RADIUS_KM / 111.0
    url = ("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&countrycodes=vn&bounded=1"
           f"&viewbox={lon - d:.4f},{lat + d:.4f},{lon + d:.4f},{lat - d:.4f}&q={urllib.parse.quote(q)}")
    try:
        return json.loads(D.get(url, headers={"Accept-Language": "vi"}))
    except Exception as ex:
        print(f"    Nominatim lỗi: {ex}"); return None   # lỗi mạng → không tính là "đã thử"


def main():
    head, rows = [], []
    with open(DATA, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh); head = list(rd.fieldnames or []); rows = list(rd)
    try: tried = json.load(open(TRIED, encoding="utf-8"))
    except Exception: tried = {}
    retry = (date.today() - timedelta(days=30)).isoformat()
    todo = [r for r in rows if (r.get("vi_tri") or "gần đúng") == "gần đúng" and r.get("lat") and r.get("lng") and candidates(r)
            and tried.get(r.get("id"), "") < retry]
    todo.sort(key=lambda r: (not r.get("gia_trung_max"), r.get("ngay", "")), reverse=False)
    print(f"Phiên còn đặt ở tâm xã có tên thôn/khu: {len(todo)} (xử lý tối đa {MAX_ROWS})")
    moved = 0
    for r in todo[:MAX_ROWS]:
        center = (float(r["lng"]), float(r["lat"]))
        olds = [m.group(2).strip() for m in re.finditer(r"(xã|phường|thị trấn)\s+([^,;()]+)", r.get("dia_danh_cu") or "")]
        hit, neterr = None, False
        for kind, name in candidates(r):
            qs = [f"{kind} {name}, {o}" for o in olds[:1]] + [f"{kind} {name}, {r.get('xa', '')}", f"{name}, {r.get('tinh', 'Hà Nội')}"]
            for q in qs:
                found = nominatim(q, center)
                if found is None: neterr = True; found = []
                for res in found:
                    nm = fold(res.get("name") or res.get("display_name", "").split(",")[0])
                    if fold(name) not in nm and nm not in fold(name): continue
                    p = (float(res["lon"]), float(res["lat"]))
                    if km(p, center) > RADIUS_KM: continue
                    hit = (p, q, res.get("type")); break
                time.sleep(1.1)
                if hit: break
            if hit: break
        if not neterr: tried[r.get("id", "")] = date.today().isoformat()
        if hit:
            (lon, lat), q, typ = hit
            print(f"✓ {r.get('id')} {r.get('xa')}: «{q}» ({typ}) → {lat:.5f}, {lon:.5f} (cách tâm xã {km((lon, lat), center):.1f} km)")
            r["lat"], r["lng"], r["vi_tri"] = f"{lat:.5f}", f"{lon:.5f}", "theo thôn"; moved += 1
        else:
            print(f"· {r.get('id')} {r.get('xa')}: không tìm thấy {', '.join(n for _, n in candidates(r))}")
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=head, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); [w.writerow({k: r.get(k, "") for k in head}) for r in rows]
    if moved: open(DATA, "w", encoding="utf-8", newline="").write(buf.getvalue())
    os.makedirs(os.path.dirname(TRIED), exist_ok=True)
    json.dump(tried, open(TRIED, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Xong: đổi tọa độ {moved} phiên sang vị trí thôn/khu.")


if __name__ == "__main__":
    main()
