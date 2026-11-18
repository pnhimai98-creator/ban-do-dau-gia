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
BIN_M = 40                            # độ dài một lát theo chu vi (nhỏ để bám sát đường)
SHORT_GAP_M = 300                     # hở ngắn hơn: nối thẳng, giữ trạng thái
OPEN_MIN_GAP_M = 1500                 # vành đai hở: chỉ để hở nếu chỗ hở dài hơn
PRIORITY = {"hien_huu": 0, "dang_xay": 1, "quy_hoach": 2}
ANCHOR_M = 250                        # khoảng cách giữa các điểm neo khi dò đường theo mạng OSM
MIN_RUN_M = 800                       # mẩu trạng thái ngắn hơn thì gộp vào mẩu bên cạnh

KX = 111320 * math.cos(math.radians(CENTER[1])); KY = 110540
def to_xy(p): return ((p[0] - CENTER[0]) * KX, (p[1] - CENTER[1]) * KY)
def to_ll(x, y): return [round(CENTER[0] + x / KX, 6), round(CENTER[1] + y / KY, 6)]

def lines_of(g):
    if g["type"] == "LineString": return [g["coordinates"]]
    if g["type"] == "MultiLineString": return g["coordinates"]
    return []

def sample(line, step=15):
    """Chia đoạn thành điểm cách nhau ≤ step m (toạ độ xy)."""
    pts = [to_xy(p) for p in line]
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(d // step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


class SegIndex:
    """Lưới chỉ mục các đoạn thẳng OSM gốc để bám điểm vào mặt đường."""
    def __init__(self, cell=60):
        self.c = cell; self.g = defaultdict(list)
    def add(self, a, b):
        c = self.c
        for gx in range(int(min(a[0], b[0]) // c), int(max(a[0], b[0]) // c) + 1):
            for gy in range(int(min(a[1], b[1]) // c), int(max(a[1], b[1]) // c) + 1):
                self.g[(gx, gy)].append((a, b))
    def snap(self, p, maxd):
        c = self.c; gx, gy = int(p[0] // c), int(p[1] // c); best, bd = None, maxd
        k = int(maxd // c) + 1
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                for a, b in self.g.get((gx + dx, gy + dy), ()):
                    vx, vy = b[0] - a[0], b[1] - a[1]; L2 = vx * vx + vy * vy
                    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2))
                    q = (a[0] + t * vx, a[1] + t * vy); d = math.hypot(p[0] - q[0], p[1] - q[1])
                    if d < bd: best, bd = q, d
        return best

def simplify(pts, eps=1.5):
    if len(pts) < 3: return pts
    a, b = pts[0], pts[-1]; vx, vy = b[0] - a[0], b[1] - a[1]; L = math.hypot(vx, vy) or 1e-9
    i, dm = 0, -1
    for k in range(1, len(pts) - 1):
        d = abs((pts[k][0] - a[0]) * vy - (pts[k][1] - a[1]) * vx) / L
        if d > dm: i, dm = k, d
    if dm <= eps: return [a, b]
    return simplify(pts[:i + 1], eps)[:-1] + simplify(pts[i:], eps)

def snap_line(xy, idx, step=8, maxd=45):
    """Chia nhỏ rồi kéo từng điểm vào đoạn OSM gần nhất → nét nằm đúng trên mặt đường."""
    out = []
    for a, b in zip(xy, xy[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(d // step))
        for k in range(n):
            p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            out.append(idx.snap(p, maxd) or p)
    out.append(idx.snap(xy[-1], maxd) or xy[-1])
    return simplify(out)

class RoadGraph:
    """Mạng đường của một vành đai (từ chính các nét OSM) để đi theo đúng mặt đường."""
    def __init__(self, feats, join_m=30, cell=50):
        self.xy, self.adj, key = [], [], {}
        def node(p):
            k = (round(p[0], 1), round(p[1], 1))
            if k not in key:
                key[k] = len(self.xy); self.xy.append(p); self.adj.append([])
            return key[k]
        for f in feats:
            for line in lines_of(f["geometry"]):
                raw = [to_xy(q) for q in line]; xy = [raw[0]]
                for a, b in zip(raw, raw[1:]):   # chia cạnh dài ≤ 30 m để điểm nào trên đường cũng gần một nút
                    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // 30))
                    xy += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(1, n + 1)]
                for a, b in zip(xy, xy[1:]):
                    u, v = node(a), node(b); d = math.hypot(b[0] - a[0], b[1] - a[1])
                    if u != v: self.adj[u].append((v, d)); self.adj[v].append((u, d))
        self.c = cell; self.g = defaultdict(list)
        for i, p in enumerate(self.xy): self.g[(int(p[0] // cell), int(p[1] // cell))].append(i)
        # nối các đầu mút rời (way OSM bị cắt, nút giao lệch) với nút gần nhất ≤ join_m
        for i, p in enumerate(self.xy):
            if len(self.adj[i]) != 1: continue
            for j in self.near(p, join_m):
                if j != i and all(v != j for v, _ in self.adj[i]):
                    d = math.hypot(self.xy[j][0] - p[0], self.xy[j][1] - p[1])
                    self.adj[i].append((j, d)); self.adj[j].append((i, d))
    def near(self, p, r):
        c = self.c; out = []
        for dx in range(-int(r // c) - 1, int(r // c) + 2):
            for dy in range(-int(r // c) - 1, int(r // c) + 2):
                for i in self.g.get((int(p[0] // c) + dx, int(p[1] // c) + dy), ()):
                    q = self.xy[i]
                    if math.hypot(q[0] - p[0], q[1] - p[1]) <= r: out.append(i)
        return out
    def nearest(self, p, r=80):
        ns = self.near(p, r)
        return min(ns, key=lambda i: math.hypot(self.xy[i][0] - p[0], self.xy[i][1] - p[1])) if ns else None
    def route(self, src, tgt_xy, tgt_r=40, k=3.0, extra=400):
        """Đường ngắn nhất từ nút src tới BẤT KỲ nút nào cách tgt_xy ≤ tgt_r (chọn đúng làn/nhánh)."""
        targets = set(self.near(tgt_xy, tgt_r))
        if not targets: return None
        sx, sy = self.xy[src]
        limit = k * math.hypot(tgt_xy[0] - sx, tgt_xy[1] - sy) + extra
        import heapq
        dist = {src: 0.0}; prev = {}; h = [(0.0, src)]
        while h:
            d, u = heapq.heappop(h)
            if d > dist.get(u, 1e18): continue
            if u in targets:
                out = [u]
                while out[-1] in prev: out.append(prev[out[-1]])
                return out[::-1]
            if d > limit: break
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, 1e18): dist[v] = nd; prev[v] = u; heapq.heappush(h, (nd, v))
        return None

def off_road(a, b, segidx, tol=15, step=20):
    """Đoạn thẳng a→b có điểm nào cách mạng đường OSM quá tol không?"""
    d = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(d // step))
    for k in range(1, n):
        p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
        if segidx.snap(p, tol) is None: return True
    return False

def repair_path(path, G, segidx):
    """Rà toàn tuyến: mọi cạnh nét liền lệch khỏi đường → đi lại theo mạng đường;
    không đi được → chuyển thành đoạn nối gần đúng. Sau đó gỡ các gai quay đầu."""
    out = [path[0]] if path else []
    for q in path[1:]:
        p = out[-1]
        if q[3] or not off_road(p[:2], q[:2], segidx, step=5):
            out.append(q); continue
        a = G.nearest(p[:2], 60); b = G.nearest(q[:2], 60); rt = None
        if a is not None and b is not None:
            rt = G.route(a, G.xy[b], 25, k=4.0, extra=3000)
        if rt and all(not off_road(G.xy[u], G.xy[v], segidx) for u, v in zip(rt, rt[1:])):
            for n, nd in enumerate(rt):
                pt = G.xy[nd]
                if math.hypot(out[-1][0] - pt[0], out[-1][1] - pt[1]) < 0.5: continue
                hop_off = n == 0 and off_road(out[-1][:2], pt, segidx, step=5)   # bước nhảy từ điểm nối gần đúng vào mạng đường
                out.append((pt[0], pt[1], "quy_hoach" if hop_off else q[2], hop_off))
            if math.hypot(out[-1][0] - q[0], out[-1][1] - q[1]) >= 0.5:
                hop_off = off_road(out[-1][:2], q[:2], segidx, step=5)
                out.append((q[0], q[1], "quy_hoach", True) if hop_off else q)
        else:
            out.append((q[0], q[1], "quy_hoach", True))
    # gỡ gai: đi ra rồi quay lại gần đúng chỗ cũ
    changed = True
    while changed:
        changed = False; k = 1
        while k < len(out) - 1:
            a, m, b = out[k - 1], out[k], out[k + 1]
            dab = math.hypot(b[0] - a[0], b[1] - a[1])
            dam = math.hypot(m[0] - a[0], m[1] - a[1]); dmb = math.hypot(b[0] - m[0], b[1] - m[1])
            if dab < 0.35 * min(dam, dmb) and min(dam, dmb) > 8 and not off_road(a[:2], b[:2], segidx):
                del out[k]; changed = True; k = max(1, k - 1)
            else: k += 1
    return out

def build_ring(feats, closed):
    pts = []   # (theta, r, status, x, y)
    segidx = SegIndex()
    for f in feats:
        for line in lines_of(f["geometry"]):
            xy = [to_xy(p) for p in line]
            for a, b in zip(xy, xy[1:]): segidx.add(a, b)
    for f in feats:
        st = f["properties"].get("status") or "hien_huu"
        for line in lines_of(f["geometry"]):
            if len(line) < 2: continue
            for x, y in sample(line):
                pts.append((math.atan2(y, x), math.hypot(x, y), st, x, y))
    if len(pts) < 20: return None
    rmed = statistics.median(p[1] for p in pts)
    nb = max(720, min(12000, int(2 * math.pi * rmed / BIN_M)))
    bins = [[] for _ in range(nb)]
    for th, r, st, x, y in pts:
        bins[int((th + math.pi) / (2 * math.pi) * nb) % nb].append((r, st, x, y))
    tol = lambda r: max(350.0, 0.10 * r)

    # hạt giống: lát nhiều điểm nhất có cụm gọn
    seed = max(range(nb), key=lambda i: len(bins[i]) if bins[i] and (max(b[0] for b in bins[i]) - min(b[0] for b in bins[i])) < tol(rmed) else -1)
    if not bins[seed]: return None
    rs = [b[0] for b in bins[seed]]  # bán kính các điểm ở lát hạt giống
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
        miss = 0; pxy = None
        for i in order:
            lim = tol(r_prev) + 0.6 * miss * 2 * math.pi * r_prev / nb   # sau chỗ hở cho phép bắt lại xa hơn
            cs = [c for c in clusters(i) if abs(statistics.median(x[0] for x in c) - r_prev) <= lim]
            if not cs: miss += 1; continue
            cand = min(cs, key=lambda c: abs(statistics.median(x[0] for x in c) - r_prev))
            rm = statistics.median(c[0] for c in cand)
            st = Counter(c[1] for c in cand).most_common()
            top = max(n for _, n in st)
            st = min((s for s, n in st if n == top), key=lambda s: PRIORITY.get(s, 9))
            # điểm THẬT trên đường OSM, gần trung vị nhất (gần với điểm trước nhất nếu có)
            if pxy and miss == 0: rep = min(cand, key=lambda c: (c[2] - pxy[0]) ** 2 + (c[3] - pxy[1]) ** 2)
            else: rep = min(cand, key=lambda c: abs(c[0] - rm))
            if res[i] is None: res[i] = (rep[0], st, rep[2], rep[3])
            pxy = (res[i][2], res[i][3]); r_prev = rm; miss = 0
        return r_prev
    fwd = [(seed + k) % nb for k in range(nb)]
    bwd = [(seed - k) % nb for k in range(nb)]
    track(fwd, r0)
    track(bwd, r0)   # đi ngược để bổ sung lát còn trống (không ghi đè)

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
        ra, sa, xa, ya = res[a]; rb, sb, xb, yb = res[b]
        L = gap_len(g)
        if L <= SHORT_GAP_M:
            st = sa if PRIORITY.get(sa, 9) >= PRIORITY.get(sb, 9) else sb; ap = False
        else:
            st, ap = "quy_hoach", True
        for t in range(n):
            i = order[s + t]; f = (t + 1) / (n + 1)
            if ap:   # chưa có dữ liệu: nối theo cung quanh tâm
                th = -math.pi + (i + 0.5) * 2 * math.pi / nb; r = ra + (rb - ra) * f
                out[i] = (r, st, r * math.cos(th), r * math.sin(th))
            else:    # hở ngắn: nối thẳng giữa hai điểm thật
                out[i] = (ra + (rb - ra) * f, st, xa + (xb - xa) * f, ya + (yb - ya) * f)
            approx[i] = ap

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
            for i in run: out[i] = (out[i][0], st, out[i][2], out[i][3])
            changed = True
        if not changed: break

    # sắp thứ tự lát thành đường; vành đai hở bắt đầu ngay sau chỗ hở
    if cut:
        s, n, a, b = cut
        seq = [order[(s + n + t) % nb] for t in range(nb - n)]
    else:
        seq = order + [order[0]]
    # ---- dựng hình học: đoạn có dữ liệu thì ĐI THEO ĐÚNG MẠNG ĐƯỜNG OSM, chỗ hở thì nối ----
    G = RoadGraph(feats)
    pieces = []   # [("road"|"gap", [bin...])]
    for i in seq:
        if out[i] is None: continue
        kind = "road" if res[i] is not None else "gap"
        if pieces and pieces[-1][0] == kind: pieces[-1][1].append(i)
        else: pieces.append([kind, [i]])
    path = []     # [(x, y, status, approx)] – trạng thái gán cho cạnh đi TỚI điểm đó
    cur_node = None
    def push(pt, st, ap):
        if path and math.hypot(path[-1][0] - pt[0], path[-1][1] - pt[1]) < 0.5: return
        path.append((pt[0], pt[1], st, ap))
    for kind, bl in pieces:
        if kind == "gap":
            for i in bl:
                r, st, x, y = out[i]; push((x, y), st, approx[i])
            cur_node = None
            continue
        # chọn điểm neo dọc đoạn (chỉ số trong bl), khoảng ANCHOR_M một điểm
        anchors, acc = [0], 0.0
        for k in range(1, len(bl)):
            acc += math.hypot(out[bl[k]][2] - out[bl[k - 1]][2], out[bl[k]][3] - out[bl[k - 1]][3])
            if acc >= ANCHOR_M: anchors.append(k); acc = 0.0
        if anchors[-1] != len(bl) - 1: anchors.append(len(bl) - 1)
        for n, k in enumerate(anchors):
            i = bl[k]; x, y = out[i][2], out[i][3]
            if cur_node is None or n == 0 and not path:
                cur_node = G.nearest((x, y))
                push(G.xy[cur_node] if cur_node is not None else (x, y), out[i][1], False)
                continue
            span = bl[(anchors[n - 1] + 1 if n else k): k + 1] or [i]
            st = Counter(out[j][1] for j in span).most_common(1)[0][0]
            route = G.route(cur_node, (x, y))
            if route:
                for nd in route[1:]: push(G.xy[nd], st, False)
                cur_node = route[-1]
            else:   # không đi được theo mạng đường (đứt dữ liệu): nối qua các điểm thật
                for j in span: push((out[j][2], out[j][3]), st, False)
                cur_node = G.nearest((x, y))
    if closed and path: path.append((path[0][0], path[0][1], path[-1][2], path[-1][3]))
    path = repair_path(path, G, segidx)
    # tách thành các đoạn theo (trạng thái, gần đúng)
    res_seg = []
    for k in range(1, len(path)):
        key = (path[k][2], path[k][3])
        if res_seg and res_seg[-1][0] == key: res_seg[-1][1].append(path[k][:2])
        else: res_seg.append([key, [path[k - 1][:2], path[k][:2]]])
    final = []
    for (st, ap), l in res_seg:
        l = simplify(l, 1.0)
        if len(l) >= 2: final.append(((st, ap), [to_ll(x, y) for x, y in l]))
    return final, cut is not None

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
