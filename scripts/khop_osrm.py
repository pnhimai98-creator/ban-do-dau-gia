#!/usr/bin/env python3
"""
Khớp các đoạn vành đai "gần đúng" vào đường thật bằng OSRM  /  Snap approximate ring gaps to real roads

Chạy SAU scripts/khep_vanh_dai.py:
    python scripts/khop_osrm.py             # gọi OSRM (dùng trong GitHub Actions)
    python scripts/khop_osrm.py --offline   # chỉ dùng kết quả đã lưu đệm

Với mỗi đoạn nối gần đúng (approx: true) trong layers/ha-tang.geojson:
  1. Hỏi OSRM đường đi ngắn nhất cho ô tô giữa HAI ĐẦU đoạn hở (hai đầu này luôn nằm trên
     đường vành đai thật đã có trong OSM).
  2. Chỉ nhận kết quả nếu nó đúng là "đi tiếp vành đai", không phải đi vòng đường khác:
       - dài không quá 1,35 lần đoạn nối gần đúng, và
       - không lệch khỏi hướng tuyến gần đúng quá max(400 m, 12% chiều dài).
     Đoạn vành đai chưa xây thì thường không có đường đi thẳng như vậy → bị loại, giữ nét chấm.
  3. Đoạn được nhận: vẽ nét liền theo đúng mặt đường, trạng thái "hiện hữu", ghi chú
     "theo đường hiện có (OSRM)" trong tooltip để biết là đoạn suy ra, nên kiểm tra lại.
Kết quả lưu đệm vào layers/vanh-dai-osrm-cache.json (OSRM lỗi vẫn chạy được bằng bản đệm).

Không cần thư viện ngoài. Máy chủ OSRM mặc định là bản demo công khai; có thể đổi bằng biến
môi trường OSRM_URL (VD máy chủ OSRM/GraphHopper riêng có API tương thích OSRM).
"""
import json, math, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA = os.path.join(ROOT, "layers", "ha-tang.geojson")
CACHE = os.path.join(ROOT, "layers", "vanh-dai-osrm-cache.json")
OSRM = os.environ.get("OSRM_URL", "https://router.project-osrm.org").rstrip("/")

MIN_GAP_M = 150        # đoạn hở ngắn hơn thì bỏ qua (khép kín bằng nét chấm là đủ)
MAX_GAP_M = 20000      # đoạn hở dài hơn thì không thử (thường là đoạn chưa có dữ liệu thật)
MAX_RATIO = 1.35       # đường OSRM dài tối đa 1,35 lần đoạn gần đúng
DEV_MIN_M, DEV_PCT = 400, 0.12
NOTE = "Theo đường hiện có (OSRM) – đoạn suy ra, cần kiểm tra"

KX = 111320 * math.cos(math.radians(21.03)); KY = 110540
def dist(a, b): return math.hypot((a[0] - b[0]) * KX, (a[1] - b[1]) * KY)
def length(l): return sum(dist(l[i - 1], l[i]) for i in range(1, len(l)))

def seg_dist(p, a, b):
    ax, ay = (a[0] - p[0]) * KX, (a[1] - p[1]) * KY
    bx, by = (b[0] - p[0]) * KX, (b[1] - p[1]) * KY
    dx, dy = bx - ax, by - ay; L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)

def max_dev(line, ref):
    """Khoảng lệch lớn nhất của line so với đường ref (mét)."""
    return max(min(seg_dist(p, a, b) for a, b in zip(ref, ref[1:])) for p in line)

def key(a, b): return f"{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"

def osrm_route(a, b):
    url = f"{OSRM}/route/v1/driving/{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}?overview=full&geometries=geojson&alternatives=false&continue_straight=true"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ban-do-dau-gia/1.0"})
            j = json.load(urllib.request.urlopen(req, timeout=30))
            if j.get("code") == "Ok" and j.get("routes"):
                r = j["routes"][0]; wp = j.get("waypoints", [])
                # điểm đầu/cuối bị OSRM kéo quá xa (không có đường ô tô ở đó) → coi như không có
                if any(w.get("distance", 0) > 60 for w in wp): return None
                return [[round(x, 6), round(y, 6)] for x, y in r["geometry"]["coordinates"]]
            return None
        except Exception as ex:
            print(f"  OSRM lỗi: {ex}"); time.sleep(3 * (attempt + 1))
    raise ConnectionError

def main():
    offline = "--offline" in sys.argv
    fc = json.load(open(INFRA, encoding="utf-8"))
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    used, ok, kept, online = {}, 0, 0, not offline
    for f in fc["features"]:
        p = f["properties"]
        if p.get("kind") != "vanh_dai" or not p.get("approx") or f["geometry"]["type"] != "LineString": continue
        line = f["geometry"]["coordinates"]; L = length(line)
        if not (MIN_GAP_M <= L <= MAX_GAP_M): kept += 1; continue
        a, b = line[0], line[-1]; k = key(a, b)
        if k in cache: route = cache[k]
        elif online:
            try: route = osrm_route(a, b); time.sleep(1.1)   # lịch sự với máy chủ demo
            except ConnectionError: print("  OSRM không phản hồi – dùng bản đệm cho phần còn lại"); online = False; route = None; k = None
        else: route = None; k = None
        if k: used[k] = route
        if route and len(route) > 1:
            R = length(route); dev = max_dev(route, line)
            if R <= MAX_RATIO * max(L, dist(a, b)) and dev <= max(DEV_MIN_M, DEV_PCT * L):
                f["geometry"]["coordinates"] = [a] + route[1:-1] + [b]   # chạm đúng hai đầu vành đai
                p.pop("approx", None); p["status"] = "hien_huu"; p["via"] = NOTE
                ok += 1
                print(f"VĐ{p.get('ring')}: khớp đoạn hở {L / 1000:.1f} km → {R / 1000:.1f} km theo đường (lệch ≤ {dev:.0f} m)")
                continue
            print(f"VĐ{p.get('ring')}: bỏ kết quả OSRM cho đoạn {L / 1000:.1f} km (dài {R / 1000:.1f} km, lệch {dev:.0f} m) – giữ nét chấm")
        kept += 1
    cache.update(used)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(fc, open(INFRA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Khớp được {ok} đoạn theo đường thật; còn {kept} đoạn gần đúng (chưa có đường / chưa xây). Đã ghi {INFRA}")

if __name__ == "__main__":
    main()
