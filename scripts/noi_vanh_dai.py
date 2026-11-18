#!/usr/bin/env python3
"""
Nối vành đai liền mạch, không chồng lớp  /  Seamless, non-overlapping ring roads

Chạy SAU scripts/build_layers.py (trong GitHub Actions hoặc trên máy):
    python scripts/noi_vanh_dai.py

Việc script làm với layers/ha-tang.geojson (ghi đè tại chỗ):
  1. Bỏ trùng giữa các trạng thái: đoạn "đang xây"/"quy hoạch" nào đã trùng (≤ 30 m)
     với đoạn "hiện hữu" thì bỏ phần trùng → không còn hai nét chồng lên nhau.
  2. Nối các mẩu OSM rời: hai mẩu cùng vành đai, cùng trạng thái có đầu mút cách nhau
     ≤ 15 m được ghép thành một đường liền.
  3. Vá chỗ hở bằng hướng tuyến vẽ tay (layers/vanh-dai-quy-hoach.geojson, nếu có):
     chỉ giữ phần vẽ tay nằm NGOÀI hành lang 40 m quanh đường OSM, và nối chạm vào đầu
     đoạn OSM. Khi OSM vẽ thêm đường, phần vẽ tay tự co lại – không bao giờ đè lên.
  4. In danh sách chỗ hở còn lại (> 150 m) để biết cần vẽ tay ở đâu.
  0. (Chạy trước bước 1) Bổ sung các phố cũ mà OSM không gắn số vành đai, theo danh sách
     tên trong layers/vanh-dai-ten-pho.json (VD Vành đai 1: Lạc Long Quân, Âu Cơ, Yên Phụ…).
     Đường được tải từ Overpass API; chỉ giữ đoạn nối liền được với vành đai đó (tránh
     phố trùng tên ở nơi khác). Trạng thái lấy theo OSM: construction → đang xây,
     proposed → quy hoạch, còn lại → đang vận hành. Kết quả tải được lưu đệm vào
     layers/vanh-dai-ten-pho-cache.json; nếu Overpass lỗi thì dùng bản đệm.

    python scripts/noi_vanh_dai.py            # có tải Overpass (dùng trong GitHub Actions)
    python scripts/noi_vanh_dai.py --offline  # chỉ dùng bản đệm, không cần mạng

Không cần thư viện ngoài (chỉ Python 3 chuẩn).
"""
import json, math, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA = os.path.join(ROOT, "layers", "ha-tang.geojson")
HAND = os.path.join(ROOT, "layers", "vanh-dai-quy-hoach.geojson")
GAP_REPORT = os.path.join(ROOT, "layers", "vanh-dai-cho-ho.geojson")
NAMES = os.path.join(ROOT, "layers", "vanh-dai-ten-pho.json")
NAMES_CACHE = os.path.join(ROOT, "layers", "vanh-dai-ten-pho-cache.json")
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
BBOX = (20.55, 105.25, 21.42, 106.05)   # Hà Nội (nam, tây, bắc, đông)
TOUCH_M = 40       # đoạn theo tên phải chạm (≤ 40 m) vào vành đai đã có mới được nhận

SNAP_M = 15        # nối hai mẩu nếu đầu mút cách nhau ≤ 15 m
OVERLAP_M = 30     # coi là trùng nếu cách đoạn ưu tiên hơn ≤ 30 m
CORRIDOR_M = 40    # hành lang quanh đường OSM: vẽ tay trong hành lang bị cắt bỏ
STEP_M = 15        # khoảng chia điểm khi kiểm tra khoảng cách
MIN_PIECE_M = 25   # bỏ mẩu vụn ngắn hơn sau khi cắt
GAP_M = 150        # báo cáo chỗ hở lớn hơn
PRIORITY = {"hien_huu": 0, "dang_xay": 1, "quy_hoach": 2}

# ---------- hình học đơn giản (độ → mét, xấp xỉ phẳng quanh vĩ độ 21°) ----------
KX = 111320 * math.cos(math.radians(21)); KY = 110540
def dist(a, b): return math.hypot((a[0] - b[0]) * KX, (a[1] - b[1]) * KY)
def length(line): return sum(dist(line[i - 1], line[i]) for i in range(1, len(line)))

def densify(line, step=STEP_M):
    out = [line[0]]
    for a, b in zip(line, line[1:]):
        n = max(1, int(dist(a, b) // step))
        out += [[a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n] for k in range(1, n + 1)]
    return out

class Grid:
    """Lưới ô ~100 m để tìm nhanh điểm gần."""
    C = 0.001
    def __init__(self): self.g = defaultdict(list)
    def key(self, p): return (int(p[0] // self.C), int(p[1] // self.C))
    def add_line(self, line):
        for p in densify(line): self.g[self.key(p)].append(p)
    def near(self, p, r):
        kx, ky = self.key(p); n = int(r / 100) + 1
        for i in range(kx - n, kx + n + 1):
            for j in range(ky - n, ky + n + 1):
                for q in self.g.get((i, j), ()):
                    if dist(p, q) <= r: return True
        return False
    def nearest(self, p, r):
        kx, ky = self.key(p); n = int(r / 100) + 1; best = None
        for i in range(kx - n, kx + n + 1):
            for j in range(ky - n, ky + n + 1):
                for q in self.g.get((i, j), ()):
                    d = dist(p, q)
                    if d <= r and (best is None or d < best[0]): best = (d, q)
        return best[1] if best else p

def lines_of(geom):
    t = geom["type"]
    return [geom["coordinates"]] if t == "LineString" else list(geom["coordinates"]) if t == "MultiLineString" else []

def cut_outside(line, grid, r):
    """Giữ các đoạn của line nằm ngoài bán kính r quanh grid; mỗi đoạn giữ thêm 1 điểm
    biên ở hai đầu để chạm vào đường đã có (nối liền, không hở)."""
    pts = densify(line); inside = [grid.near(p, r) for p in pts]
    runs, cur = [], []
    for i, p in enumerate(pts):
        if not inside[i]:
            if not cur and i > 0: cur.append(grid.nearest(pts[i - 1], r))   # điểm chạm đầu, hít vào đường OSM
            cur.append(p)
        elif cur:
            cur.append(grid.nearest(p, r)); runs.append(cur); cur = []   # điểm chạm cuối
    if cur: runs.append(cur)
    return [simplify(r_) for r_ in runs if len(r_) > 1 and length(r_) >= MIN_PIECE_M]

def simplify(line, tol=3):
    """Douglas–Peucker (tol mét) để file không phình vì densify."""
    if len(line) < 3: return line
    a, b = line[0], line[-1]; ab = dist(a, b) or 1e-9
    def off(p):
        return abs((b[0] - a[0]) * KX * (a[1] - p[1]) * KY - (a[0] - p[0]) * KX * (b[1] - a[1]) * KY) / ab
    i, d = max(((i, off(p)) for i, p in enumerate(line[1:-1], 1)), key=lambda x: x[1])
    if d <= tol: return [a, b]
    return simplify(line[:i + 1], tol)[:-1] + simplify(line[i:], tol)

def merge(lines):
    """Ghép các mẩu có đầu mút trùng nhau (≤ SNAP_M) thành đường dài."""
    lines = [l[:] for l in lines if len(l) > 1]
    changed = True
    while changed:
        changed = False
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                a, b = lines[i], lines[j]
                if dist(a[-1], b[0]) <= SNAP_M: m = a + b[1:]
                elif dist(a[-1], b[-1]) <= SNAP_M: m = a + b[-2::-1]
                elif dist(a[0], b[-1]) <= SNAP_M: m = b + a[1:]
                elif dist(a[0], b[0]) <= SNAP_M: m = b[::-1] + a[1:]
                else: continue
                lines[i] = m; del lines[j]; changed = True; break
            if changed: break
    return lines

def r6(lines): return [[[round(x, 6), round(y, 6)] for x, y in l] for l in lines]

# ---------- bổ sung phố cũ theo tên / add plain-named streets ----------
def norm(n):
    n = unicodedata.normalize("NFC", (n or "").strip())
    return re.sub(r"^(Đường|Phố|Đại lộ)\s+", "", n, flags=re.I).strip().lower()

def status_of(tags):
    hw = tags.get("highway", "")
    if hw == "construction" or "construction" in tags: return "dang_xay"
    if hw == "proposed" or "proposed" in tags: return "quy_hoach"
    return "hien_huu"

def fetch_named(names):
    """Tải mọi đường có tên thuộc danh sách (kể cả đang xây / quy hoạch) trong khung Hà Nội."""
    alts = sorted({re.escape(unicodedata.normalize("NFC", n)) for n in names})
    rx = "^((Đường|Phố|Đại lộ) )?(" + "|".join(alts) + ")$"
    q = f'''[out:json][timeout:240];
way["highway"]["name"~"{rx}",i]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
out tags geom;'''
    for url in OVERPASS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": "ban-do-dau-gia/1.0"})
                j = json.load(urllib.request.urlopen(req, timeout=300))
                out = [{"name": e["tags"].get("name", ""), "status": status_of(e["tags"]),
                        "coords": [[round(p["lon"], 6), round(p["lat"], 6)] for p in e.get("geometry", [])]}
                       for e in j.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
                if out: return out
            except Exception as ex:
                print(f"  Overpass lỗi ({url}): {ex}"); time.sleep(10)
    return None

def named_features(by_ring, offline):
    if not os.path.exists(NAMES): return []
    cfg = {k: v for k, v in json.load(open(NAMES, encoding="utf-8")).items() if not k.startswith("_")}
    allnames = {n for v in cfg.values() for n in v}
    ways = None if offline else fetch_named(allnames)
    if ways is not None:
        json.dump(ways, open(NAMES_CACHE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"Tải {len(ways)} đoạn đường theo tên từ Overpass")
    elif os.path.exists(NAMES_CACHE):
        ways = json.load(open(NAMES_CACHE, encoding="utf-8")); print(f"Dùng bản đệm: {len(ways)} đoạn đường theo tên")
    else:
        print("Không có dữ liệu đường theo tên (chưa tải được Overpass, chưa có bản đệm)"); return []
    feats = []
    for ring, names in cfg.items():
        want = {norm(n) for n in names}
        cand = [w for w in ways if norm(w["name"]) in want and len(w["coords"]) > 1]
        # chỉ nhận đoạn nối liền dần dần với vành đai hiện có (loại phố trùng tên ở nơi khác)
        grid = Grid()
        for f in by_ring.get(ring, []):
            for l in lines_of(f["geometry"]): grid.add_line(l)
        if not grid.g: continue
        acc, changed = [], True
        while changed:
            changed = False
            for w in cand:
                if w.get("_ok" + ring): continue
                c = w["coords"]
                if grid.near(c[0], TOUCH_M) or grid.near(c[-1], TOUCH_M):
                    w["_ok" + ring] = True; acc.append(w); grid.add_line(c); changed = True
        base = by_ring[ring][0]["properties"]
        by_st = defaultdict(list)
        for w in acc: by_st[w["status"]].append(w)
        for st, ws in by_st.items():
            feats.append({"type": "Feature", "properties": {"kind": "vanh_dai", "ring": ring, "region": base.get("region", "Hà Nội"),
                "name": base.get("name") or f"Vành đai {ring}", "status": st,
                "via": ", ".join(sorted({w["name"] for w in ws}))},
                "geometry": {"type": "MultiLineString", "coordinates": [w["coords"] for w in ws]}})
        print(f"  VĐ{ring}: nhận {len(acc)}/{len(cand)} đoạn theo tên ({', '.join(sorted({w['name'] for w in acc})) or '—'})")
    return feats

# ---------------------------------------------------------------------------
def main():
    fc = json.load(open(INFRA, encoding="utf-8"))
    rings = [f for f in fc["features"] if f["properties"].get("kind") == "vanh_dai"]
    others = [f for f in fc["features"] if f["properties"].get("kind") != "vanh_dai"]
    hand = json.load(open(HAND, encoding="utf-8"))["features"] if os.path.exists(HAND) else []

    by_ring = defaultdict(list)
    for f in rings: by_ring[str(f["properties"].get("ring"))].append(f)
    for f in named_features(by_ring, "--offline" in sys.argv):   # bước 0
        by_ring[f["properties"]["ring"]].append(f)
    for f in hand: f["properties"].setdefault("kind", "vanh_dai")
    hand_by_ring = defaultdict(list)
    for f in hand:
        if f["properties"].get("kind") == "vanh_dai": hand_by_ring[str(f["properties"].get("ring"))].append(f)

    out, gaps, report = [], [], []
    for ring in sorted(set(by_ring) | set(hand_by_ring), key=lambda s: float(s) if s.replace(".", "").isdigit() else 99):
        feats = sorted(by_ring.get(ring, []), key=lambda f: PRIORITY.get(f["properties"].get("status"), 9))
        grid = Grid(); kept = []
        # 1 + 2: bỏ trùng theo thứ tự ưu tiên, rồi nối mẩu
        for f in feats:
            ls = [l for l in lines_of(f["geometry"]) if len(l) > 1]
            if kept: ls = [p for l in ls for p in cut_outside(l, grid, OVERLAP_M)]
            ls = merge(ls)
            if not ls: continue
            for l in ls: grid.add_line(l)
            kept.append((f["properties"], ls))
        base = next((p for p, _ in kept), None) or (hand_by_ring[ring][0]["properties"] if hand_by_ring.get(ring) else {})
        for props, ls in kept:
            out.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": r6(ls)}})
        # 3: vá bằng vẽ tay, chỉ phần nằm ngoài hành lang OSM
        patch_ends = []
        for f in hand_by_ring.get(ring, []):
            ls = [p for l in lines_of(f["geometry"]) for p in cut_outside(l, grid, CORRIDOR_M)]
            if not ls: continue
            props = {"kind": "vanh_dai", "ring": ring, "region": f["properties"].get("region") or base.get("region", "Hà Nội"),
                     "name": f["properties"].get("name") or base.get("name") or f"Vành đai {ring}",
                     "status": f["properties"].get("status", "quy_hoach"), "approx": True}
            out.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": r6(ls)}})
            for l in ls: patch_ends += [l[0], l[-1]]
        # 4: tìm chỗ hở (đầu mút không chạm mẩu nào khác)
        all_lines = [l for _, ls in kept for l in ls]
        ends = [(i, p) for i, l in enumerate(all_lines) for p in (l[0], l[-1])]
        for i, p in ends:
            if any(dist(p, q) <= SNAP_M * 2 for q in patch_ends): continue
            others_ = [(dist(p, q), q) for j, q in ends if j != i]
            if not others_: continue
            d, q = min(others_)
            if GAP_M < d < 15000 and not grid.near([(p[0] + q[0]) / 2, (p[1] + q[1]) / 2], 30):
                mid = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]
                if any(dist(mid, g) < 400 for g in gaps): continue   # bỏ trùng (đường đôi 2 chiều)
                gaps.append(mid)
                report.append({"type": "Feature", "properties": {"ring": ring, "gap_m": round(d)},
                               "geometry": {"type": "LineString", "coordinates": r6([[p, q]])[0]}})
        n_osm = sum(len(ls) for _, ls in kept)
        print(f"VĐ{ring}: {n_osm} đoạn OSM sau khi nối, {sum(1 for r in report if r['properties']['ring'] == ring)} chỗ hở > {GAP_M} m")

    fc["features"] = others + out
    json.dump(fc, open(INFRA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"type": "FeatureCollection", "features": report}, open(GAP_REPORT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Đã ghi {INFRA}\nDanh sách chỗ hở: {GAP_REPORT} (mở bằng geojson.io để xem)")

if __name__ == "__main__":
    sys.exit(main())
