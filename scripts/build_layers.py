#!/usr/bin/env python3
"""
Tạo 2 lớp bản đồ từ OpenStreetMap (qua Overpass API) cho trang bản đồ đấu giá:
  layers/ranh-gioi-xa.geojson  – ranh giới xã/phường mới (sau sáp nhập 2025)
  layers/ranh-gioi-tinh.geojson – ranh giới tỉnh/thành mới
  layers/ranh-gioi-huyen-cu.geojson – ranh giới huyện/quận cũ (nếu OpenStreetMap còn lưu)
  layers/duong-bo.geojson      – quốc lộ (QL) và đường tỉnh (ĐT)
  layers/ha-tang.geojson       – đường cao tốc; đường sắt đô thị; cầu qua sông Hồng; sân bay

Chạy tự động bằng GitHub Actions (.github/workflows/build-layers.yml) hoặc trên máy: python scripts/build_layers.py
Chỉ dùng thư viện có sẵn của Python, không cần cài thêm.
Dữ liệu © OpenStreetMap contributors (giấy phép ODbL).
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

# ===== CẤU HÌNH =====
# Tỉnh/thành lấy ranh giới xã/phường (và huyện cũ nếu có), đúng tên trong OpenStreetMap
PROVINCES = ["Hà Nội", "Bắc Ninh", "Hưng Yên", "Phú Thọ"]
# Tỉnh/thành vẽ đường ranh giới tỉnh
OUTLINE_PROVINCES = ["Hà Nội", "Bắc Ninh", "Hưng Yên", "Phú Thọ", "Thái Nguyên", "Ninh Bình", "Hải Phòng", "Tuyên Quang", "Lạng Sơn", "Quảng Ninh"]
# Vùng lấy dữ liệu hạ tầng: nam, tây, bắc, đông
BBOX = (20.20, 104.95, 21.80, 106.65)   # đủ rộng để lấy cả Vành đai 5
# Các cầu qua sông Hồng (tên bắt đầu bằng "Cầu …")
BRIDGES = ["Nhật Tân", "Thăng Long", "Long Biên", "Chương Dương", "Vĩnh Tuy", "Thanh Trì", "Vĩnh Thịnh", "Tứ Liên",
           "Trần Hưng Đạo", "Hồng Hà", "Mễ Sở", "Vân Phúc", "Thượng Cát", "Ngọc Hồi", "Phù Đổng"]
# Vùng của từng vành đai Hà Nội (nam, tây, bắc, đông). Đoạn "Vành đai …" nằm ngoài vùng này được gán cho
# đô thị gần nhất (Vĩnh Yên, Hải Dương, Bắc Giang… cũng có vành đai riêng) và vẽ màu khác
RING_BOX = {
    "1": (20.98, 105.79, 21.05, 105.88),
    "2": (20.95, 105.75, 21.12, 105.95),
    "2.5": (20.95, 105.75, 21.10, 105.95),
    "3": (20.90, 105.70, 21.30, 106.00),
    "3.5": (20.83, 105.62, 21.25, 106.02),
    "4": (20.72, 105.52, 21.40, 106.22),
    "5": (20.20, 104.95, 21.80, 106.65),
}
# Tâm các đô thị lân cận có vành đai riêng (vĩ độ, kinh độ)
CITIES = {"Vĩnh Yên": (21.31, 105.60), "Phúc Yên": (21.24, 105.70), "Việt Trì": (21.32, 105.40), "Thái Nguyên": (21.59, 105.84),
          "Bắc Giang": (21.27, 106.19), "Bắc Ninh": (21.18, 106.07), "Hải Dương": (20.94, 106.33), "Hưng Yên": (20.65, 106.05),
          "Phủ Lý": (20.54, 105.91), "Hòa Bình": (20.82, 105.34), "Nam Định": (20.43, 106.17), "Ninh Bình": (20.25, 105.97),
          "Thái Bình": (20.45, 106.34), "Sơn Tây": (21.14, 105.50)}
# Số xã/phường hợp lý của một tỉnh sau sáp nhập (để tự chọn đúng cấp hành chính trong OSM)
COMMUNE_RANGE = (40, 400)
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "layers")
ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
UA = "ban-do-dau-gia/1.0 (GitHub Actions; land auction map)"


def overpass(query):
    last = None
    for ep in ENDPOINTS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(ep, data=urllib.parse.urlencode({"data": query}).encode(), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=360) as r:
                    return json.load(r)
            except Exception as e:  # mạng chập chờn / máy chủ bận → thử lại, đổi máy chủ
                last = e
                print(f"  Overpass {ep} lỗi ({e}), thử lại…", file=sys.stderr)
                time.sleep(15)
    raise RuntimeError(f"Không lấy được dữ liệu Overpass: {last}")


# ---------- hình học ----------
def pt(p):
    return (round(p["lon"], 6), round(p["lat"], 6))


def simplify(line, tol):
    """Douglas–Peucker, tol tính bằng độ (0.00005 ≈ 5 m)."""
    if len(line) < 3:
        return line
    if line[0] == line[-1]:  # vòng khép kín: tách tại điểm xa nhất rồi rút gọn từng nửa
        x0, y0 = line[0]
        k = max(range(len(line)), key=lambda i: (line[i][0] - x0) ** 2 + (line[i][1] - y0) ** 2)
        if k in (0, len(line) - 1):
            return line
        return simplify(line[:k + 1], tol)[:-1] + simplify(line[k:], tol)
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = line[a], line[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        best, idx = 0.0, None
        for i in range(a + 1, b):
            x0, y0 = line[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / n
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(line, keep) if k]


def join_rings(ways):
    """Nối các đoạn đường biên (way) thành các vòng khép kín."""
    ways = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while ways:
        cur = ways.pop(0)
        while cur[0] != cur[-1]:
            for i, w in enumerate(ways):
                if w[0] == cur[-1]:
                    cur = cur + w[1:]; break
                if w[-1] == cur[-1]:
                    cur = cur + w[::-1][1:]; break
                if w[-1] == cur[0]:
                    cur = w + cur[1:]; break
                if w[0] == cur[0]:
                    cur = w[::-1] + cur[1:]; break
            else:
                break  # không nối tiếp được → bỏ vòng hở
            ways.pop(i)
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def in_ring(x, y, ring):
    ins = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def relation_polygon(el, tol):
    outer = [[pt(p) for p in m["geometry"]] for m in el.get("members", []) if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry")]
    inner = [[pt(p) for p in m["geometry"]] for m in el.get("members", []) if m.get("type") == "way" and m.get("role") == "inner" and m.get("geometry")]
    outers, inners = join_rings(outer), join_rings(inner)
    polys = [[o] for o in outers]
    for h in inners:  # gắn lỗ thủng vào vòng ngoài chứa nó
        for p in polys:
            if in_ring(h[0][0], h[0][1], p[0]):
                p.append(h); break
    polys = [[simplify(r, tol) for r in p] for p in polys]
    polys = [[r for r in p if len(r) >= 4] for p in polys]
    polys = [p for p in polys if p and len(p[0]) >= 4]
    if not polys:
        return None
    return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}


# ---------- ranh giới xã/phường ----------
def build_boundaries():
    """Trả về (xã/phường mới, huyện/quận cũ)."""
    feats, dist = [], []
    for prov in PROVINCES:
        print(f"Ranh giới: {prov}")
        q = f"""[out:json][timeout:300];
area["boundary"="administrative"]["admin_level"="4"]["name"="{prov}"]->.p;
relation["boundary"="administrative"]["admin_level"~"^(6|7|8)$"](area.p);
out tags geom;"""
        els = [e for e in overpass(q).get("elements", []) if e.get("type") == "relation"]
        by_level = {}
        for e in els:
            by_level.setdefault(e["tags"].get("admin_level"), []).append(e)
        print("  số đơn vị theo admin_level:", {k: len(v) for k, v in by_level.items()})
        ok = [k for k, v in by_level.items() if COMMUNE_RANGE[0] <= len(v) <= COMMUNE_RANGE[1]]
        level = max(ok, key=lambda k: int(k)) if ok else (max(by_level, key=lambda k: len(by_level[k])) if by_level else None)
        if level is None:
            print(f"  ⚠ Không tìm thấy ranh giới cho {prov}", file=sys.stderr); continue
        if not ok:
            print(f"  ⚠ Không cấp nào có số xã hợp lý, tạm dùng admin_level={level}", file=sys.stderr)
        print(f"  dùng admin_level={level} ({len(by_level[level])} đơn vị)")
        for e in by_level[level]:
            g = relation_polygon(e, 0.00008)
            if not g:
                continue
            tg = e["tags"]
            feats.append({"type": "Feature", "properties": {"name": tg.get("name", ""), "tinh": prov, "admin_level": level, "osm_id": e["id"]}, "geometry": g})
        # huyện/quận cũ: cấp hành chính khác, số lượng 5–60 (VD Hà Nội có 30 quận/huyện/thị xã trước 7/2025)
        dl = [k for k, v in by_level.items() if k != level and 5 <= len(v) <= 60 and int(k) < int(level)]
        if dl:
            k = max(dl, key=lambda x: len(by_level[x]))
            print(f"  huyện/quận cũ: admin_level={k} ({len(by_level[k])} đơn vị)")
            for e in by_level[k]:
                g = relation_polygon(e, 0.00012)
                if g:
                    dist.append({"type": "Feature", "properties": {"name": e["tags"].get("name", ""), "tinh": prov, "osm_id": e["id"]}, "geometry": g})
        else:
            print("  (OpenStreetMap không còn ranh giới huyện/quận cũ cho tỉnh này)")
        time.sleep(3)
    return feats, dist


# ---------- ranh giới tỉnh ----------
def build_provinces():
    names = "|".join(OUTLINE_PROVINCES)
    q = f"""[out:json][timeout:300];
relation["boundary"="administrative"]["admin_level"="4"]["name"~"^({names})$"];
out tags geom;"""
    feats = []
    for e in overpass(q).get("elements", []):
        if e.get("type") != "relation":
            continue
        g = relation_polygon(e, 0.0004)
        if g:
            feats.append({"type": "Feature", "properties": {"name": e["tags"].get("name", ""), "osm_id": e["id"]}, "geometry": g})
    print(f"Ranh giới tỉnh: {len(feats)} tỉnh/thành")
    return feats


# ---------- quốc lộ & đường tỉnh ----------
def norm_road_ref(ref):
    """'QL 32' → ('ql', 'QL.32'); 'DT419' → ('dt', 'ĐT.419'); 'TL.70' → ('dt', 'ĐT.70')"""
    for r in (ref or "").split(";"):
        m = re.match(r"\s*(QL|ĐT|DT|TL)\s*\.?\s*(\d+[A-Z]?)", r.strip(), re.I)
        if m:
            cls = "ql" if m.group(1).upper() == "QL" else "dt"
            return cls, ("QL." if cls == "ql" else "ĐT.") + m.group(2).upper()
    return None, None


def build_roads():
    s, w, n, e = BBOX
    bb = f"{s},{w},{n},{e}"
    q = f"""[out:json][timeout:900];
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|construction|proposed)$"]["ref"~"^ *(QL|ĐT|DT|TL)",i]({bb});
out tags geom;"""
    lines = {}
    for el in overpass(q).get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        tg = el.get("tags", {})
        cls, ref = norm_road_ref(tg.get("ref"))
        if not cls:
            continue
        hw = tg.get("highway", "")
        status = "dang_xay" if hw == "construction" else "quy_hoach" if hw == "proposed" else "hien_huu"
        v = lines.setdefault((cls, ref, status), {"props": {"kind": "quoc_lo" if cls == "ql" else "duong_tinh", "ref": ref, "status": status}, "parts": []})
        v["parts"].append(simplify([pt(p) for p in el["geometry"]], 0.0001))
    feats = [{"type": "Feature", "properties": v["props"], "geometry": {"type": "MultiLineString", "coordinates": v["parts"]}} for v in lines.values()]
    from collections import Counter
    c = Counter((f["properties"]["kind"], f["properties"]["status"]) for f in feats)
    print("Đường bộ:", dict(c))
    return feats


# ---------- hạ tầng ----------
def ring_no(*texts):
    m = re.search(r"(?:Vành\s*[ĐđDd]ai|V[ĐD])\s*(\d+(?:[.,]\d)?)", " ".join(t or "" for t in texts), re.I)
    return m.group(1).replace(",", ".") if m else None


def metro_line(*texts):
    s = " ".join(t or "" for t in texts)
    m = re.search(r"(?:tuyến|line)\s*(?:số\s*)?(\d{1,2}[AB]?)(?!\d)", s, re.I)
    return m.group(1).upper() if m else None


def ring_region(ring, geom):
    """Vành đai này thuộc đâu: "Hà Nội", "Vùng Thủ đô" (VĐ4, VĐ5) hay tên đô thị lân cận."""
    if ring in ("4", "5") and in_ring_box(ring, geom):
        return "Vùng Thủ đô"
    if in_ring_box(ring, geom):
        return "Hà Nội"
    p = geom[len(geom) // 2]
    name, d = min(((c, math.hypot(p["lat"] - y, p["lon"] - x)) for c, (y, x) in CITIES.items()), key=lambda z: z[1])
    return name if d < 0.2 else "Khác"


def in_ring_box(ring, geom):
    b = RING_BOX.get(ring)
    if not b or not geom:
        return False
    p = geom[len(geom) // 2]
    return b[0] <= p["lat"] <= b[2] and b[1] <= p["lon"] <= b[3]


def build_infra():
    s, w, n, e = BBOX
    bb = f"{s},{w},{n},{e}"
    br = "|".join(BRIDGES)
    q = f"""[out:json][timeout:600];
relation["route"="road"]["name"~"Vành [ĐđDd]ai",i]({bb})->.rr;
.rr out body;
relation["route"~"^(subway|light_rail)$"]({bb})->.mr;
.mr out body;
(
  way(r.rr);
  way["highway"]["name"~"Vành [ĐđDd]ai",i]({bb});
  way["highway"]["ref"~"^V[ĐD] ?[0-9]"]({bb});
  way["railway"~"^(subway|light_rail)$"]({bb});
  way["railway"~"^(construction|proposed)$"]["construction"~"subway|light_rail"]({bb});
  way["railway"="proposed"]["proposed"~"subway|light_rail"]({bb});
  way["bridge"]["name"~"^Cầu ({br})"]({bb});
  way["highway"="motorway"]({bb});
  way["highway"~"^(construction|proposed)$"]["construction"="motorway"]({bb});
  way["highway"="proposed"]["proposed"="motorway"]({bb});
  way["aeroway"="aerodrome"]({bb});
  node["railway"="station"]["station"~"subway|light_rail"]({bb});
  node["public_transport"="station"]["subway"="yes"]({bb});
  node["public_transport"="station"]["light_rail"="yes"]({bb});
  node["railway"="construction"]["construction"="station"]({bb});
  relation["aeroway"="aerodrome"]({bb});
);
out tags geom;"""
    lines, feats, seen_st = {}, [], set()
    els = overpass(q).get("elements", [])
    # quan hệ (relation) tuyến vành đai / metro → biết mỗi đoạn đường thuộc vành đai / tuyến nào
    way_ring, way_line = {}, {}
    for el in els:
        if el.get("type") != "relation" or "members" not in el or el.get("tags", {}).get("aeroway"):
            continue
        tg = el.get("tags", {})
        if tg.get("route") == "road":
            r = ring_no(tg.get("name"), tg.get("ref"))
            for m in el["members"]:
                if m.get("type") == "way" and r:
                    way_ring[m["ref"]] = r
        elif tg.get("route") in ("subway", "light_rail"):
            ln = tg.get("ref") if re.fullmatch(r"\d{1,2}[AB]?", tg.get("ref", "") or "") else metro_line(tg.get("name"), tg.get("ref"))
            for m in el["members"]:
                if m.get("type") == "way" and ln:
                    way_line[m["ref"]] = (ln.upper(), tg.get("colour", ""), tg.get("name", ""))
    for el in els:
        if el.get("type") == "relation" and not el.get("tags", {}).get("aeroway"):
            continue
        if el.get("type") == "node":   # ga metro
            tg = el.get("tags", {})
            if "lat" not in el:
                continue
            st = "dang_xay" if tg.get("railway") == "construction" else "hien_huu"
            key = (round(el["lon"], 4), round(el["lat"], 4))
            if key in seen_st:
                continue
            seen_st.add(key)
            raw = (tg.get("line") or "").strip()
            ln = raw.upper() if re.fullmatch(r"\d{1,2}[AB]?", raw, re.I) else metro_line(tg.get("line"), tg.get("ref"), tg.get("network"), tg.get("name"))
            feats.append({"type": "Feature", "properties": {"kind": "ga_metro", "name": tg.get("name", "Ga"), "status": st, **({"line": ln} if ln else {})},
                          "geometry": {"type": "Point", "coordinates": [round(el["lon"], 6), round(el["lat"], 6)]}})
            continue
        tg = el.get("tags", {})
        name = tg.get("name", "")
        if tg.get("aeroway") == "aerodrome":
            if not (tg.get("iata") or "quốc tế" in name.lower() or any(k in name for k in ("Nội Bài", "Gia Bình", "Gia Lâm", "Hòa Lạc"))):
                continue
            if el["type"] == "way" and el.get("geometry"):
                ring = simplify([pt(p) for p in el["geometry"]], 0.00005)
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                g = {"type": "Polygon", "coordinates": [ring]} if len(ring) >= 4 else None
            else:
                g = relation_polygon(el, 0.00005)
            if g:
                feats.append({"type": "Feature", "properties": {"kind": "san_bay", "name": name, "status": "hien_huu"}, "geometry": g})
            continue
        if el["type"] != "way" or not el.get("geometry"):
            continue
        rw, hw = tg.get("railway", ""), tg.get("highway", "")
        status = "dang_xay" if hw == "construction" or rw == "construction" else "quy_hoach" if hw == "proposed" or rw == "proposed" else "hien_huu"
        extra = {}
        ring = way_ring.get(el["id"]) or ring_no(name, tg.get("ref"))
        if rw:
            kind = "metro"
            ln, col, rname = way_line.get(el["id"], (None, "", ""))
            ln = ln or metro_line(name, tg.get("ref"), tg.get("construction:name"))
            label = f"Tuyến {ln}" if ln else (name or tg.get("construction:name") or "Đường sắt đô thị")
            extra = {"line": ln or "", "colour": col or tg.get("colour", ""), "name": rname or name or label}
            gkey = label
        elif tg.get("bridge") and name.startswith("Cầu"):
            kind, gkey, extra = "cau", name, {"name": name}
        else:   # ĐÃ BỎ lớp vành đai – không tạo đối tượng "vanh_dai" nữa
            kind = None
        # đường cao tốc (có thể trùng một phần với vành đai 3, 4 → vẽ ở cả hai lớp)
        if hw == "motorway" or tg.get("construction") == "motorway" or tg.get("proposed") == "motorway":
            ref = (tg.get("ref") or "").split(";")[0].strip()
            mref = re.match(r"CT\.?\s*(\d+)", ref, re.I)
            ref = f"CT.{int(mref.group(1)):02d}" if mref else ref.replace(" ", "")
            cname = "" if (tg.get("name") or "").startswith("Cầu") else (tg.get("name") or "")
            ckey = ref or cname or "Cao tốc"
            v = lines.setdefault(("cao_toc", ckey, status), {"props": {"kind": "cao_toc", "status": status, **({"ref": ref} if ref else {}), "name": cname or ref or "Đường cao tốc"}, "parts": []})
            v["parts"].append(simplify([pt(p) for p in el["geometry"]], 0.00004))
        if kind is None:
            continue
        part = simplify([pt(p) for p in el["geometry"]], 0.00004)
        todo = [(kind, gkey, extra)]
        for kd, gk, ex in todo:
            v = lines.setdefault((kd, gk, status), {"props": {"kind": kd, "status": status, **{k: x for k, x in ex.items() if x}}, "parts": []})
            v["parts"].append(part)
    for v in lines.values():
        feats.append({"type": "Feature", "properties": v["props"], "geometry": {"type": "MultiLineString", "coordinates": v["parts"]}})
    # tóm tắt để xem trong nhật ký GitHub Actions
    from collections import Counter
    print("  Hạ tầng lấy được:")
    for k, c in sorted(Counter((f["properties"]["kind"], f["properties"].get("region", ""), f["properties"].get("ring") or f["properties"].get("line") or f["properties"].get("ref") or f["properties"].get("name", ""), f["properties"]["status"]) for f in feats).items()):
        print("   ", *k)
    return feats


def save(name, feats, title):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    fc = {"type": "FeatureCollection", "name": title, "attribution": "© OpenStreetMap contributors (ODbL)",
          "generated": time.strftime("%Y-%m-%d"), "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"→ {path}: {len(feats)} đối tượng, {os.path.getsize(path) // 1024} KB")


def step(title, fn):
    """Chạy một bước; lỗi ở bước này không làm hỏng các bước khác (file cũ được giữ nguyên)."""
    try:
        r = fn()
        time.sleep(5)
        return r
    except Exception as e:
        print(f"⚠ {title}: lỗi {e} – giữ nguyên file cũ (nếu có)", file=sys.stderr)
        return None


def main():
    ok = 0
    r = step("Ranh giới xã/huyện", build_boundaries)
    if r:
        b, d = r
        if b:
            save("ranh-gioi-xa.geojson", b, "Ranh giới xã/phường"); ok += 1
        if d:
            save("ranh-gioi-huyen-cu.geojson", d, "Ranh giới huyện/quận cũ (trước 7/2025)"); ok += 1
    p = step("Ranh giới tỉnh", build_provinces)
    if p:
        save("ranh-gioi-tinh.geojson", p, "Ranh giới tỉnh/thành"); ok += 1
    i = step("Hạ tầng", build_infra)
    if i:
        save("ha-tang.geojson", i, "Hạ tầng giao thông"); ok += 1
    rd = step("Quốc lộ & đường tỉnh", build_roads)
    if rd:
        save("duong-bo.geojson", rd, "Quốc lộ & đường tỉnh"); ok += 1
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
