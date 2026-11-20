"""Chuyển file Excel báo cáo dự án thấp tầng (data/bao-cao-du-an.xlsx) thành:
  du-an.csv        – từng dự án (sơ cấp / thứ cấp / tương lai) kèm toạ độ
  du-an-huyen.csv  – số tổng hợp theo quận/huyện cũ (giá liền kề trung vị, nguồn cung tương lai)
File Excel cần 3 sheet: "Current Projects", "Secondary Projects", "Future Projects"
(đúng định dạng file đã tạo từ PDF). Đổi QUY bên dưới hoặc truyền: python scripts/nhap_du_an.py 2026-Q4
Muốn chỉ công khai số tổng hợp theo huyện: KHÔNG đưa du-an.csv lên repo (xoá file đó), chỉ giữ du-an-huyen.csv.

TOẠ ĐỘ – tìm theo TÊN DỰ ÁN trên mạng, lưu luôn trong file Excel, sheet "Toa do"
(ten | huyen_en | lat | lng | nguon | dia_chi). Dự án chưa có trong sheet sẽ được tự tìm:
  1. Gemini + Google Search (khoá GEMINI_API_KEY sẵn có của repo) tìm ĐỊA CHỈ của dự án theo tên + chủ đầu tư,
     rồi đổi địa chỉ thành toạ độ bằng OpenStreetMap      → nguon = "Web"       (đúng địa chỉ dự án)
  2. Tìm thẳng tên dự án trên OpenStreetMap               → nguon = "OSM"       (khu dự án có trên bản đồ)
  3. Chỉ xác định được xã/phường/thôn của dự án           → nguon = "Địa danh"  (gần đúng)
  4. Không tìm được                                       → nguon = "Không thấy" (đặt quanh tâm quận/huyện cũ)
  Hết hạn mức Gemini miễn phí trong ngày → các dự án còn lại để trống, lần chạy sau (bấm "Run workflow") tìm tiếp.
  SỬA TAY: điền lat, lng (Google Maps: chuột phải vào điểm → bấm dòng toạ độ) và ghi nguon = "Sửa tay" rồi tải
  file Excel lên lại – dòng "Sửa tay" không bị ghi đè. Muốn tìm lại một dự án: xoá dòng của nó trong sheet.
"""
import csv, json, math, os, re, statistics, subprocess, sys, time, unicodedata, urllib.parse, urllib.request
from openpyxl import load_workbook

SRC = "data/bao-cao-du-an.xlsx"
QUY = sys.argv[1] if len(sys.argv) > 1 else "2026-Q3"
NGUON = "Ha Noi Quarterly Market Report Q3/2026 – Villa/Townhouse (Appendix)"

# Quận/huyện cũ: tên tiếng Việt và toạ độ trung tâm (gần đúng)
HUYEN = {
    "Ba Dinh": ("Ba Đình", 21.034, 105.814), "Hoan Kiem": ("Hoàn Kiếm", 21.029, 105.852), "Tay Ho": ("Tây Hồ", 21.070, 105.818),
    "Long Bien": ("Long Biên", 21.047, 105.889), "Cau Giay": ("Cầu Giấy", 21.032, 105.790), "Dong Da": ("Đống Đa", 21.018, 105.829),
    "Hai Ba Trung": ("Hai Bà Trưng", 21.006, 105.858), "Hoang Mai": ("Hoàng Mai", 20.975, 105.861), "Thanh Xuan": ("Thanh Xuân", 20.994, 105.809),
    "Nam Tu Liem": ("Nam Từ Liêm", 21.012, 105.765), "Bac Tu Liem": ("Bắc Từ Liêm", 21.070, 105.765), "Ha Dong": ("Hà Đông", 20.963, 105.770),
    "Son Tay": ("Sơn Tây", 21.138, 105.505), "Ba Vi": ("Ba Vì", 21.200, 105.420), "Phuc Tho": ("Phúc Thọ", 21.105, 105.565),
    "Dan Phuong": ("Đan Phượng", 21.088, 105.668), "Hoai Duc": ("Hoài Đức", 21.022, 105.705), "Quoc Oai": ("Quốc Oai", 20.990, 105.640),
    "Thach That": ("Thạch Thất", 21.030, 105.560), "Chuong My": ("Chương Mỹ", 20.900, 105.680), "Thanh Oai": ("Thanh Oai", 20.860, 105.770),
    "Thuong Tin": ("Thường Tín", 20.858, 105.860), "Phu Xuyen": ("Phú Xuyên", 20.735, 105.905), "Ung Hoa": ("Ứng Hòa", 20.710, 105.780),
    "My Duc": ("Mỹ Đức", 20.690, 105.730), "Thanh Tri": ("Thanh Trì", 20.940, 105.840), "Gia Lam": ("Gia Lâm", 21.010, 105.950),
    "Dong Anh": ("Đông Anh", 21.135, 105.850), "Soc Son": ("Sóc Sơn", 21.255, 105.850), "Me Linh": ("Mê Linh", 21.180, 105.720),
}

def num(v):
    if v is None or v == "": return None
    try: return float(v)
    except (TypeError, ValueError): return None

def rows(ws):
    for r in ws.iter_rows(min_row=6, values_only=True):
        if not r[0] or str(r[0]).startswith(("Total", "Note")): continue
        yield r

def huyen_key(s):
    s = " ".join(str(s or "").split()).title()
    return s if s in HUYEN else None

def spiral(n):
    """điểm thứ n quanh tâm huyện (rải đều để không chồng lên nhau)"""
    if n == 0: return 0.0, 0.0
    a = n * 2.39996; r = 0.0045 * math.sqrt(n)
    return r * math.sin(a), r * math.cos(a)

def fmt(x, d=2):
    if x is None: return ""
    return str(int(x)) if float(x).is_integer() else str(round(x, d))

# ======================= TOẠ ĐỘ (OpenStreetMap) =======================
UA = "ban-do-dau-gia/1.0 (https://github.com/pnhimai98-creator/ban-do-dau-gia)"
BAD_CAT = {"shop", "amenity", "office", "tourism", "water", "waterway", "man_made", "railway", "craft", "emergency", "natural", "historic"}
AREA = re.compile(r" (khu do thi|kdt|khu dan cu|khu nha|villa|villas|khu tai dinh cu) ")
STOP = re.compile(r"\b(N?UA|Urban|Area|New|Project|Residential|Resettlement|Mixed[- ]Use|Service|Eco|Functional|Function|Expanded|Extension|Remaining|Zone|Phase|Town|Communes?|Wards?|Development|Low[- ]rise|Office|Villas?|Shophouse|Townhouses?|Premium|Northern|Southern|Eastern|Western|North|South|East|West|Plots?|Land|Dwelling|Quarter|public|facilities|the|of|by|Complex|Centre|Center|Housing|Social|Ecological|Tourism|Golf|Industrial|Park|Garden|City|Village|Lake|Riverside|Luxury|Metropolitan|Areas?)\b", re.I)

def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")); s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " " + re.sub(r"[^a-z0-9]+", " ", s.replace("đ", "d").replace("Đ", "d").lower()).strip() + " "

def nominatim(q, box, feature=""):
    prm = {"format": "jsonv2", "limit": 3, "countrycodes": "vn", "bounded": 1, "viewbox": ",".join(map(str, box)), "q": q}
    if feature: prm["featureType"] = feature
    req = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(prm), headers={"User-Agent": UA, "Accept-Language": "vi"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: res = json.load(r)
    except Exception as e:
        print("  lỗi tra cứu:", q, e); res = None
    time.sleep(1.1)   # quy định Nominatim: tối đa 1 yêu cầu/giây
    return res

def name_variants(name):
    """bước 1: tên dự án (đổi NUA/UA… sang tiếng Việt) – tìm đúng khu dự án"""
    n = re.sub(r"\([^)]*\)", "", name).replace("–", "-").strip(" -,")
    core = re.sub(r"\b(N?UA|Urban Area|New Urban Area|Resettlement Area|Residential Area|Mixed[- ]Use( Service)?( Area)?|Expanded|Extension|Remaining|Zone [A-Z0-9]+|Phase \d+)\b", "", n, flags=re.I)
    core = re.sub(r"\s+", " ", core).strip(" -,")
    v = []
    if re.search(r"\bNUA\b|New Urban Area", n, re.I): v.append("Khu đô thị mới " + core)
    elif re.search(r"\bUA\b|Urban Area", n, re.I): v.append("Khu đô thị " + core)
    elif re.search(r"Resettlement", n, re.I): v.append("Khu tái định cư " + core)
    elif re.search(r"Residential Area", n, re.I): v.append("Khu dân cư " + core)
    v.append(core or n)
    return list(dict.fromkeys(x for x in v if x))[:2]

def place_chunks(name):
    """bước 2 (cho dự án sắp triển khai, tên chung chung): tách tên thôn/xã/phường/phố trong tên dự án
       vd. "UA in Lien Quan Town and Thach That Commune" → Lien Quan, Thach That; "Van Khe NUA" → Van Khe; "103 Nguyen Xien" → phố Nguyen Xien"""
    out = []
    for p in re.split(r"\s+-\s+|–|,|&|/|\band\b|\bin\b|\bat\b", re.sub(r"\([^)]*\)", " ", name), flags=re.I):
        street = bool(re.match(r"^\s*\d+[A-Za-z0-9-]*\s+", p))
        p = re.sub(r"^\s*\d+[A-Za-z0-9-]*\s+", "", p); p = STOP.sub(" ", p)
        p = re.sub(r"\b[A-Z]?\d+[A-Za-z0-9-]*\b|\b(I|II|III|IV|V)\b", " ", p); p = re.sub(r"\s+", " ", p).strip()
        if 2 <= len(p.split()) <= 4 and re.fullmatch(r"[A-Za-z ]+", p): out.append((p, street))
    return out[:3]

AI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
AI_MODELS = [m.strip() for m in os.environ.get("GEMINI_MODEL", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
_ai = {"off": not AI_KEY, "model": 0, "last": 0.0, "calls": 0}

def web_address(name, dev, huyen_vn):
    """Gemini + Google Search: địa chỉ thật của dự án theo tên. Trả dict hoặc None (không có khoá / hết hạn mức)."""
    if _ai["off"]: return None
    prompt = (f'Dùng Google Search tìm địa chỉ thực tế của dự án bất động sản "{name}"' + (f' (chủ đầu tư: {dev})' if dev else "") +
              f' thuộc quận/huyện {huyen_vn} (địa giới cũ), Hà Nội. Ưu tiên trang của chủ đầu tư, báo chí, sàn BĐS uy tín. '
              'Chỉ trả về MỘT đối tượng JSON, không giải thích: {"dia_chi": "địa chỉ đầy đủ: lô/đường, xã/phường (cũ), quận/huyện" hoặc null, '
              '"duong": "tên đường/phố chính tiếp giáp" hoặc null, "xa_phuong": "xã/phường/thị trấn (tên cũ)" hoặc null, '
              '"lat": số hoặc null, "lng": số hoặc null, "chac_chan": "cao" | "trung bình" | "thấp"}. Không tìm thấy dự án thì mọi trường là null.')
    for attempt in range(3):
        wait = 6.5 - (time.monotonic() - _ai["last"])   # hạn mức miễn phí ~10 lượt/phút
        if wait > 0: time.sleep(wait)
        _ai["last"] = time.monotonic(); model = AI_MODELS[min(_ai["model"], len(AI_MODELS) - 1)]
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
                           "generationConfig": {"temperature": 0, "maxOutputTokens": 600}}).encode()
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                                         headers={"x-goog-api-key": AI_KEY, "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r: j = json.loads(r.read().decode("utf-8"))
            _ai["calls"] += 1
            txt = "".join(p.get("text", "") for p in (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []))
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0)) if m else {}
        except Exception as ex:
            code = getattr(ex, "code", 0)
            if code == 404 and _ai["model"] < len(AI_MODELS) - 1: _ai["model"] += 1; continue
            if code == 429:
                if attempt == 0: time.sleep(60); continue
                print("  → hết hạn mức Gemini miễn phí hôm nay; lần chạy sau sẽ tìm tiếp"); _ai["off"] = True; return None
            if code in (400, 401, 403): print("  → GEMINI_API_KEY sai/chưa bật:", ex); _ai["off"] = True; return None
            if code in (500, 502, 503, 504): time.sleep(10 * (attempt + 1)); continue
            print("  Gemini lỗi:", ex); return {}
    return {}

def in_box(lat, lng, box):
    try: return box[0] - .05 <= float(lng) <= box[2] + .05 and box[3] - .05 <= float(lat) <= box[1] + .05
    except (TypeError, ValueError): return False

def geocode(name, k, dev=""):
    """→ (lat, lng, nguon, dia_chi); nguon = None khi hết hạn mức Gemini (để lần sau tìm tiếp)"""
    vn, la, lo = HUYEN[k]; box = [round(lo - .12, 3), round(la + .12, 3), round(lo + .12, 3), round(la - .12, 3)]
    # 1. TÌM TRÊN MẠNG theo tên dự án → địa chỉ → toạ độ
    w = web_address(name, dev, vn)
    if w is None and AI_KEY: return None, None, None, ""
    w = w or {}; addr = (w.get("dia_chi") or "").strip()
    if addr:
        tries = [addr]
        if w.get("duong") and w.get("xa_phuong"): tries.append(f'{w["duong"]}, {w["xa_phuong"]}, Hà Nội')
        for q in tries:
            for r in nominatim(q, box) or []:
                if r.get("category") in ("highway", "building", "landuse", "place") and r.get("type") not in ("city", "state", "county") and in_box(r["lat"], r["lon"], box) \
                   and (r.get("category") != "place" or r.get("type") in ("neighbourhood", "quarter", "hamlet", "city_block")):
                    return float(r["lat"]), float(r["lon"]), "Web", addr
        if w.get("chac_chan") in ("cao", "trung bình") and in_box(w.get("lat"), w.get("lng"), box):
            return float(w["lat"]), float(w["lng"]), "Web", addr
    # 2. tên dự án trên OpenStreetMap
    for q in name_variants(name):
        j = nominatim(q, box)
        if not j: continue
        r = j[0]; disp = fold(r.get("display_name")); core = re.sub(r"^ (khu do thi moi|khu do thi|khu tai dinh cu|khu dan cu) ", " ", fold(q))
        if len(core.strip()) < 4 or core not in disp or " ha noi " not in disp: continue
        cat, ty = r.get("category"), r.get("type")
        if cat in BAD_CAT or ty in ("industrial", "pitch", "stadium", "motorway_junction"): continue
        if cat in ("landuse", "building") or (AREA.search(disp) and cat in ("highway", "place", "leisure")): return float(r["lat"]), float(r["lon"]), "OSM", addr
    # 3. xã/phường (từ kết quả tìm trên mạng hoặc từ tên dự án)
    cands = ([(w["xa_phuong"], False)] if w.get("xa_phuong") else []) + place_chunks(name)
    for c, street in cands:
        for ft in ([""] if street else ["settlement", ""]):
            for r in nominatim(c, box, ft) or []:
                disp = fold(r.get("display_name"))
                if fold(re.sub(r"^(xã|phường|thị trấn|thôn)\s+", "", c, flags=re.I)) not in disp or " ha noi " not in disp: continue
                if r.get("category") not in ("place", "boundary", "highway") or r.get("type") in ("motorway_junction", "bus_stop"): continue
                if not ft and r.get("category") != "highway": continue
                return float(r["lat"]), float(r["lon"]), "Địa danh", addr
    return None, None, "Không thấy", addr

def coord_sheet(wb, todo):
    """đọc / bổ sung sheet "Toa do"; trả về {ten|huyen_en: (lat, lng, vi_tri, dia_chi)}"""
    if "Toa do" not in wb.sheetnames:
        ws = wb.create_sheet("Toa do"); ws.append(["ten", "huyen_en", "lat", "lng", "nguon", "dia_chi"])
    ws = wb["Toa do"]; rows_, changed = {}, False
    for r in ws.iter_rows(min_row=2):
        if r[0].value: rows_[f"{r[0].value}|{r[1].value}"] = r
    online = os.environ.get("GEOCODE", "1") != "0"
    for ten, k, dev in todo:
        key = f"{ten}|{k}"
        if key in rows_ or not online: continue
        lat, lng, src, addr = geocode(ten, k, dev)
        if src is None: break   # hết hạn mức Gemini → dừng, lần sau tìm tiếp
        print(f"  {src:10} {ten} ({k}) {addr}")
        ws.append([ten, k, round(lat, 6) if lat else None, round(lng, 6) if lng else None, src, addr or None]); rows_[key] = ws[ws.max_row]; changed = True
    out = {}
    for key, r in rows_.items():
        lat, lng, src = r[2].value, r[3].value, str(r[4].value or "").strip()
        addr = str(r[5].value or "").strip() if len(r) > 5 else ""
        try: lat, lng = float(lat), float(lng)
        except (TypeError, ValueError): continue
        out[key] = (lat, lng, {"Web": "Theo địa chỉ", "OSM": "Theo OSM", "Địa danh": "Gần đúng theo địa danh"}.get(src, "Sửa tay"), addr)
    return out, changed

def main():
    wb = load_workbook(SRC)
    todo = {}
    for ws_, c, d in (("Current Projects", 2, 1), ("Secondary Projects", 2, 1), ("Future Projects", 1, 2)):
        for r in rows(wb[ws_]):
            k = huyen_key(r[c])
            if k: todo.setdefault((str(r[0]), k), "" if r[d] in (None, "N/A") else str(r[d]))
    coords, changed = coord_sheet(wb, [(t, k, d) for (t, k), d in todo.items()])
    print(f"Toạ độ: {len(coords)}/{len(todo)} dự án có vị trí" + (f" · Gemini {_ai['calls']} lượt" if AI_KEY else " · (không có GEMINI_API_KEY: bỏ qua bước tìm trên mạng)"))
    if changed:
        wb.save(SRC); print("Đã ghi toạ độ mới vào sheet 'Toa do'")
        if os.environ.get("GITHUB_ACTIONS"): subprocess.run(["git", "add", SRC])   # để workflow lưu luôn file Excel
    out, seen, used = [], {}, {}
    def add(nhom, ten, cdt, h, **kw):
        k = huyen_key(h)
        if not k: print("Bỏ qua – không rõ huyện:", ten, h); return
        vn, la, lo = HUYEN[k]
        if f"{ten}|{k}" in coords:
            y, x, vt, addr = coords[f"{ten}|{k}"]; kw["dia_chi"] = addr
            used.setdefault((round(y, 5), round(x, 5)), [])
            if ten not in used[(round(y, 5), round(x, 5))]: used[(round(y, 5), round(x, 5))].append(ten)
            dy, dx = spiral(used[(round(y, 5), round(x, 5))].index(ten)); lat, lng = f"{y + dy * .5:.5f}", f"{x + dx * .5:.5f}"   # dự án khác trùng điểm → lệch nhẹ
        else:
            i = seen[k] = seen.get(k, -1) + 1; dy, dx = spiral(i); lat, lng, vt = f"{la + dy:.5f}", f"{lo + dx:.5f}", "Theo huyện"
        out.append({"quy": QUY, "nhom": nhom, "ten": ten, "chu_dau_tu": cdt or "", "huyen": vn, "huyen_en": k,
                    "lat": lat, "lng": lng, "vi_tri": vt, "nguon": NGUON, **kw})
    for r in rows(wb["Current Projects"]):
        add("so_cap", r[0], r[1], r[2], can_tong=fmt(num(r[3])), can_vl=fmt(num(r[4])), can_th=fmt(num(r[5])), can_sh=fmt(num(r[6])),
            hap_thu=fmt(num(r[7]), 4), gia_vl=fmt(num(r[14])), gia_th=fmt(num(r[15])), gia_sh=fmt(num(r[16])))
    for r in rows(wb["Secondary Projects"]):
        add("thu_cap", r[0], r[1], r[2], can_tong=fmt(num(r[3])), can_vl=fmt(num(r[4])), can_th=fmt(num(r[5])), can_sh=fmt(num(r[6])),
            gia_vl=fmt(num(r[10])), gia_th=fmt(num(r[11])), gia_sh=fmt(num(r[12])))
    for r in rows(wb["Future Projects"]):
        add("tuong_lai", r[0], r[2] if r[2] != "N/A" else "", r[1], can_tong=fmt(num(r[5])), mo_ban=str(r[3] or ""),
            quy_mo_ha=fmt(num(r[4])), tinh_trang=str(r[6] or ""))
    cols = ["quy", "nhom", "ten", "chu_dau_tu", "huyen", "huyen_en", "lat", "lng", "vi_tri", "can_tong", "can_vl", "can_th", "can_sh",
            "hap_thu", "gia_vl", "gia_th", "gia_sh", "mo_ban", "quy_mo_ha", "tinh_trang", "dia_chi", "nguon"]
    with open("du-an.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n"); w.writeheader(); w.writerows(out)

    # tổng hợp theo huyện
    agg = []
    for k, (vn, la, lo) in HUYEN.items():
        ps = [p for p in out if p["huyen_en"] == k]
        if not ps: continue
        def med(nhom, col):
            v = [float(p[col]) for p in ps if p["nhom"] == nhom and p.get(col)]
            return (round(statistics.median(v), 1) if v else None), len(v)
        tc_th, n_tc_th = med("thu_cap", "gia_th"); sc_th, n_sc_th = med("so_cap", "gia_th")
        tc_vl, n_tc_vl = med("thu_cap", "gia_vl"); sc_vl, n_sc_vl = med("so_cap", "gia_vl")
        fut = [p for p in ps if p["nhom"] == "tuong_lai"]
        agg.append({"quy": QUY, "huyen": vn, "huyen_en": k, "lat": la, "lng": lo,
                    "thu_cap_th": fmt(tc_th), "thu_cap_th_n": n_tc_th, "thu_cap_vl": fmt(tc_vl), "thu_cap_vl_n": n_tc_vl,
                    "so_cap_th": fmt(sc_th), "so_cap_th_n": n_sc_th, "so_cap_vl": fmt(sc_vl), "so_cap_vl_n": n_sc_vl,
                    "tuong_lai_du_an": len(fut), "tuong_lai_can": fmt(sum(float(p["can_tong"]) for p in fut if p.get("can_tong"))),
                    "tuong_lai_ha": fmt(sum(float(p["quy_mo_ha"]) for p in fut if p.get("quy_mo_ha")), 1)})
    with open("du-an-huyen.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(agg[0].keys()), lineterminator="\n"); w.writeheader(); w.writerows(agg)
    print(f"du-an.csv: {len(out)} dự án · du-an-huyen.csv: {len(agg)} quận/huyện")

if __name__ == "__main__":
    main()
