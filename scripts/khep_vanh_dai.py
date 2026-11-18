#!/usr/bin/env python3
"""
Vẽ lại vành đai thành MỘT nét tim đường cho mỗi vành đai  /  One clean centre line per ring

Chạy SAU scripts/noi_vanh_dai.py:
    python scripts/khep_vanh_dai.py

Vì sao cần: OSM lưu một con đường thành nhiều nét (chiều đi, chiều về, cầu vượt, nhánh
lên xuống, phố trùng tên…) nên vành đai bị vẽ chồng chéo, đứt đoạn.

Cách làm (không cần thư viện ngoài):
  1. Lấy mọi điểm của từng vành đai, đổi sang toạ độ cực quanh Hồ Gươm.
  2. Chia vòng tròn thành các "lát" ~120 m. Trong mỗi lát chỉ giữ cụm điểm nằm liền
     mạch với lát bên cạnh (bỏ phố trùng tên, nhánh rẽ, đường song song) và lấy trung
     vị → được một điểm tim đường. Trạng thái của điểm = trạng thái đa số trong cụm.
  3. Lấp chỗ hở:
       - hở ≤ 300 m (đứt do dữ liệu): nối thẳng, giữ trạng thái hai bên;
       - hở dài hơn: nối theo cung, đánh dấu "quy_hoach" + approx (nét chấm, ghi
         "tuyến gần đúng") – tức là chưa có hướng tuyến chi tiết trong dữ liệu.
  4. Vành đai KHÉP KÍN (2, 3, 4, 5) luôn được nối thành vòng.
     Vành đai HỞ (1, 2.5, 3.5 – phục vụ phía Nam sông Hồng) để hở ở chỗ hở dài nhất,
     không vẽ đoạn nối qua sông.
  5. Ghi lại layers/ha-tang.geojson (thay toàn bộ đối tượng vành đai), mỗi vành đai
     thành vài LineString liên tiếp theo trạng thái.

Đổi danh sách vành đai khép kín / hở ở CLOSED_RINGS và OPEN_RINGS bên dưới.
"""
import json, math, os, statistics
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA = os.path.join(ROOT, "layers", "ha-tang.geojson")

CENTER = (105.8522, 21.0285)          # Hồ Gươm (lng, lat)
CLOSED_RINGS = {"2", "3", "4", "5"}
OPEN_RINGS = {"1", "2.5", "3.5"}
BIN_M = 120                           # độ dài một lát theo chu vi
SHORT_GAP_M = 300                     # hở ngắn hơn: nối thẳng, giữ trạng thái
OPEN_MIN_GAP_M = 1500                 # vành đai hở: chỉ để hở nếu chỗ hở dài hơn
PRIORITY = {"hien_huu": 0, "dang_xay": 1, "quy_hoach": 2}
MIN_RUN_M = 800                       # mẩu trạng thái ngắn hơn thì gộp vào mẩu bên cạnh

KX = 111320 * math.cos(math.radians(CENTER[1])); KY = 110540
def to_xy(p): return ((p[0] - CENTER[0]) * KX, (p[1] - CENTER[1]) * KY)
def to_ll(x, y): return [round(CENTER[0] + x / KX, 6), round(CENTER[1] + y / KY, 6)]

def lines_of(g):
    if g["type"] == "LineString": return [g["coordinates"]]
    if g["type"] == "MultiLineString": return g["coordinates"]
    return []

def sample(line, step=40):
    """Chia đoạn thành điểm cách nhau ≤ step m (toạ độ xy)."""
    pts = [to_xy(p) for p in line]
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(d // step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out

def build_ring(feats, closed):
    pts = []   # (theta, r, status)
    for f in feats:
        st = f["properties"].get("status") or "hien_huu"
        for line in lines_of(f["geometry"]):
            if len(line) < 2: continue
            for x, y in sample(line):
                pts.append((math.atan2(y, x), math.hypot(x, y), st))
    if len(pts) < 20: return None
    rmed = statistics.median(p[1] for p in pts)
    nb = max(360, min(4000, int(2 * math.pi * rmed / BIN_M)))
    bins = [[] for _ in range(nb)]
    for th, r, st in pts:
        bins[int((th + math.pi) / (2 * math.pi) * nb) % nb].append((r, st))
    tol = lambda r: max(350.0, 0.10 * r)

    # hạt giống: lát nhiều điểm nhất có cụm gọn
    seed = max(range(nb), key=lambda i: len(bins[i]) if bins[i] and (max(b[0] for b in bins[i]) - min(b[0] for b in bins[i])) < tol(rmed) else -1)
    if not bins[seed]: return None
    rs = [b[0] for b in bins[seed]]
    r0 = statistics.median(rs)

    res = [None] * nb   # (r, status) hoặc None
    def clusters(i):
        bs = sorted(bins[i]); out, cur = [], []
        for b in bs:
            if cur and b[0] - cur[-1][0] > 150: out.append(cur); cur = []
            cur.append(b)
        if cur: out.append(cur)
        return out
    def track(order, r_prev):
        miss = 0
        for i in order:
            lim = tol(r_prev) + 0.6 * miss * 2 * math.pi * r_prev / nb   # sau chỗ hở cho phép bắt lại xa hơn
            cs = [c for c in clusters(i) if abs(statistics.median(x[0] for x in c) - r_prev) <= lim]
            if not cs: miss += 1; continue
            cand = min(cs, key=lambda c: abs(statistics.median(x[0] for x in c) - r_prev))
            r = statistics.median(c[0] for c in cand)
            st = Counter(c[1] for c in cand).most_common()
            top = max(n for _, n in st)
            st = min((s for s, n in st if n == top), key=lambda s: PRIORITY.get(s, 9))
            if res[i] is None: res[i] = (r, st)
            r_prev = r; miss = 0
        return r_prev
    fwd = [(seed + k) % nb for k in range(nb)]
    bwd = [(seed - k) % nb for k in range(nb)]
    track(fwd, r0)
    track(bwd, r0)   # đi ngược để bổ sung lát còn trống (không ghi đè)

    # làm mượt bán kính (trung vị trượt 5 lát)
    sm = list(res)
    for i in range(nb):
        if res[i] is None: continue
        win = [res[(i + k) % nb][0] for k in range(-2, 3) if res[(i + k) % nb] is not None]
        sm[i] = (statistics.median(win), res[i][1])
    res = sm

    filled = [i for i in range(nb) if res[i] is not None]
    if not filled: return None
    arc = lambda i, r: 2 * math.pi * r / nb   # độ dài một lát tại bán kính r

    # tìm các chỗ hở (dãy lát rỗng liên tiếp, xét vòng tròn)
    gaps = []   # (start, length_in_bins, a_idx, b_idx)
    k = 0
    start0 = filled[0]
    order = [(start0 + j) % nb for j in range(nb)]
    j = 0
    while j < nb:
        i = order[j]
        if res[i] is None:
            s = j
            while j < nb and res[order[j]] is None: j += 1
            a = order[s - 1]; b = order[j % nb]
            gaps.append((s, j - s, a, b))
        else:
            j += 1
    def gap_len(g):
        ra, rb = res[g[2]][0], res[g[3]][0]
        return (g[1] + 1) * 2 * math.pi * ((ra + rb) / 2) / nb

    cut = None   # chỗ để hở (vành đai hở)
    if not closed and gaps:
        g = max(gaps, key=gap_len)
        if gap_len(g) >= OPEN_MIN_GAP_M: cut = g

    out = list(res); approx = [False] * nb
    for g in gaps:
        if g is cut: continue
        s, n, a, b = g
        ra, sa = res[a]; rb, sb = res[b]
        L = gap_len(g)
        if L <= SHORT_GAP_M:
            st = sa if PRIORITY.get(sa, 9) >= PRIORITY.get(sb, 9) else sb; ap = False
        else:
            st, ap = "quy_hoach", True
        for t in range(n):
            i = order[s + t]
            out[i] = (ra + (rb - ra) * (t + 1) / (n + 1), st); approx[i] = ap

    # gộp các mẩu trạng thái quá ngắn (< MIN_RUN_M) vào mẩu bên cạnh – tránh nét chập chờn
    ring_order = [order[(cut[0] + cut[1] + t) % nb] for t in range(nb - cut[1])] if cut else order
    idx = [i for i in ring_order if out[i] is not None]
    for _ in range(3):
        runs, cur = [], []
        for i in idx:
            key = (out[i][1], approx[i])
            if cur and (out[cur[-1]][1], approx[cur[-1]]) != key: runs.append(cur); cur = []
            cur.append(i)
        if cur: runs.append(cur)
        changed = False
        for k, run in enumerate(runs):
            if approx[run[0]]: continue
            L = sum(2 * math.pi * out[i][0] / nb for i in run)
            if L >= MIN_RUN_M or len(runs) == 1: continue
            nbrs = [runs[k - 1]] if k > 0 else []
            if k + 1 < len(runs): nbrs.append(runs[k + 1])
            nbrs = [n for n in nbrs if not approx[n[0]]]
            if not nbrs: continue
            tgt = max(nbrs, key=len); st = out[tgt[0]][1]
            for i in run: out[i] = (out[i][0], st)
            changed = True
        if not changed: break

    # sắp thứ tự lát thành đường; vành đai hở bắt đầu ngay sau chỗ hở
    if cut:
        s, n, a, b = cut
        seq = [order[(s + n + t) % nb] for t in range(nb - n)]
    else:
        seq = order + [order[0]]
    seg = []   # các đoạn liên tiếp cùng (status, approx)
    for i in seq:
        if out[i] is None: continue
        r, st = out[i]
        th = -math.pi + (i + 0.5) * 2 * math.pi / nb
        p = to_ll(r * math.cos(th), r * math.sin(th))
        key = (st, approx[i])
        if seg and seg[-1][0] == key:
            seg[-1][1].append(p)
        else:
            if seg: seg[-1][1].append(p)          # nối liền với đoạn trước
            seg.append([key, [p]])
    if closed and seg:
        seg[-1][1][-1] = seg[0][1][0]            # khép vòng tuyệt đối
    return [(k, l) for k, l in seg if len(l) >= 2], cut is not None

def main():
    fc = json.load(open(INFRA, encoding="utf-8"))
    rings = defaultdict(list); others = []
    for f in fc["features"]:
        (rings[str(f["properties"].get("ring"))] if f["properties"].get("kind") == "vanh_dai" else others).append(f)
    new = []
    for ring in sorted(rings, key=lambda r: float(r) if r.replace(".", "").isdigit() else 99):
        feats = rings[ring]
        base = dict(feats[0]["properties"]); base.pop("via", None); base.pop("status", None); base.pop("approx", None)
        closed = ring in CLOSED_RINGS
        built = build_ring(feats, closed)
        if not built:
            new += feats; print(f"VĐ{ring}: ít dữ liệu, giữ nguyên"); continue
        segs, opened = built
        cnt = Counter()
        for (st, ap), line in segs:
            p = dict(base, status=st)
            if ap: p["approx"] = True
            new.append({"type": "Feature", "properties": p, "geometry": {"type": "LineString", "coordinates": line}})
            cnt[(st, ap)] += 1
        kind = "khép kín" if closed else ("hở" if opened else "hở (không có chỗ hở dài, để nguyên vòng)")
        print(f"VĐ{ring}: {kind}, {len(segs)} đoạn – " + ", ".join(f"{s}{' (gần đúng)' if a else ''}×{n}" for (s, a), n in cnt.items()))
    fc["features"] = others + new
    json.dump(fc, open(INFRA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Đã ghi {INFRA}")

if __name__ == "__main__":
    main()
