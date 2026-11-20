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
  SỬA / BỔ SUNG THÔNG TIN: dùng trang admin, tab "Dự án" (lưu ở data/du-an-bo-sung.json, luôn được ưu tiên).
  Hoặc sửa trong Excel: điền lat, lng (Google Maps: chuột phải vào điểm → bấm dòng toạ độ) và ghi nguon = "Sửa tay" rồi tải
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
GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()   # không bắt buộc: có khoá Google Maps (Places API) thì dùng thêm
AI_MODELS = [m.strip() for m in os.environ.get("GEMINI_MODEL", "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-flash-latest,gemini-flash-lite-latest,gemini-2.0-flash").split(",") if m.strip()]
_ai = {"off": not AI_KEY, "model": 0, "last": 0.0, "calls": 0, "log": 0}
T0 = time.monotonic(); TIME_BUDGET = float(os.environ.get("TIME_BUDGET_MIN", "100")) * 60   # dừng trước giới hạn 120' của workflow, lần sau tìm tiếp
JSON_SPEC = ('Chỉ trả về MỘT đối tượng JSON, không giải thích: {"dia_chi": "địa chỉ đầy đủ: số nhà/lô, đường/phố, xã/phường (tên cũ), quận/huyện" hoặc null, '
             '"duong": "tên đường/phố chính tiếp giáp dự án" hoặc null, "xa_phuong": "xã/phường/thị trấn (tên cũ)" hoặc null, '
             '"lat": số hoặc null, "lng": số hoặc null, "chac_chan": "cao" | "trung bình" | "thấp"}.')

def gemini(prompt, search):
    """gọi Gemini (search=True: bật Google Search). Trả dict, {} nếu không có kết quả, None nếu không dùng được."""
    if _ai["off"]: return None
    for attempt in range(8):
        wait = 6.5 - (time.monotonic() - _ai["last"])   # hạn mức miễn phí ~10 lượt/phút
        if wait > 0: time.sleep(wait)
        _ai["last"] = time.monotonic(); model = AI_MODELS[min(_ai["model"], len(AI_MODELS) - 1)]
        gen = {"temperature": 0, "maxOutputTokens": 2048}
        if "2.5" in model: gen["thinkingConfig"] = {"thinkingBudget": 0}   # không để phần "suy nghĩ" ăn hết chỗ của câu trả lời
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
        if search: body["tools"] = [{"google_search": {}}]
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=json.dumps(body).encode(),
                                         headers={"x-goog-api-key": AI_KEY, "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r: j = json.loads(r.read().decode("utf-8"))
            _ai["calls"] += 1
            txt = "".join(p.get("text", "") for p in (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []))
            if _ai["log"] < 3: _ai["log"] += 1; print(f"  [Gemini {model}{' + Google Search' if search else ''}] {txt[:200]!r}")
            m = re.search(r"\{.*\}", txt, re.S)
            try: return json.loads(m.group(0)) if m else {}
            except ValueError: return {}
        except Exception as ex:
            code = getattr(ex, "code", 0)
            try: detail = ex.read().decode("utf-8", "ignore")[:300]
            except Exception: detail = str(ex)
            print(f"  Gemini {model} lỗi {code}: {detail}")
            if code == 429 and attempt < 2: time.sleep(60); continue
            if code in (400, 403, 404, 429) and _ai["model"] < len(AI_MODELS) - 1: _ai["model"] += 1; print("  → thử model", AI_MODELS[_ai["model"]]); continue
            if code in (400, 401, 403, 404, 429): print("  → tạm dừng Gemini cho lượt chạy này"); _ai["off"] = True; return None
            time.sleep(10 * (attempt + 1))
    return {}

def ask_web(name, dev, huyen_vn):
    """hỏi Gemini có Google Search: tìm địa chỉ dự án trên mạng"""
    return gemini(f'Dùng Google Search tìm địa chỉ thực tế của dự án bất động sản "{name}"' + (f' (chủ đầu tư: {dev})' if dev else "") +
                  f' thuộc quận/huyện {huyen_vn} (địa giới cũ), Hà Nội. Tên dự án có thể là tên tiếng Anh trong báo cáo thị trường; hãy tìm cả tên tiếng Việt tương ứng '
                  '(NUA/UA = khu đô thị mới/khu đô thị, Resettlement Area = khu tái định cư, Villa = biệt thự). Ưu tiên trang chủ đầu tư, báo chí, sàn BĐS, Google Maps. '
                  + JSON_SPEC + ' Không tìm thấy thì mọi trường là null.', True)

def ask_street(name, dev, huyen_vn):
    """hỏi Gemini (kiến thức sẵn có, không cần tìm kiếm): dự án nằm trên phố / xã nào"""
    return gemini(f'Dự án bất động sản "{name}"' + (f' của {dev}' if dev else "") + f' ở quận/huyện {huyen_vn} (cũ), Hà Nội nằm trên đường/phố nào, '
                  'thuộc xã/phường nào? Nếu là tên tiếng Anh thì quy đổi sang tên dự án tiếng Việt. Chỉ trả lời điều bạn biết chắc. ' + JSON_SPEC, False)

def google_places(name, huyen_vn, box):
    """(không bắt buộc) Google Maps Places API – chỉ chạy khi có GOOGLE_MAPS_API_KEY"""
    if not GMAPS_KEY: return None
    body = {"textQuery": f"{name} {huyen_vn} Hà Nội", "languageCode": "vi", "regionCode": "VN",
            "locationRestriction": {"rectangle": {"low": {"latitude": box[3], "longitude": box[0]}, "high": {"latitude": box[1], "longitude": box[2]}}}}
    try:
        req = urllib.request.Request("https://places.googleapis.com/v1/places:searchText", data=json.dumps(body).encode(),
                                     headers={"X-Goog-Api-Key": GMAPS_KEY, "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r: pl = (json.load(r).get("places") or [])
    except Exception as e: print("  Google Maps lỗi:", e); return None
    for p_ in pl:
        nm = fold((p_.get("displayName") or {}).get("text")); core = fold(re.sub(r"\b(N?UA|Phase \d+|Remaining)\b|\([^)]*\)", " ", name))
        if core.strip() and (core.strip() in nm or nm.strip() in core): return p_["location"]["latitude"], p_["location"]["longitude"], p_.get("formattedAddress", "")
    return None

def reverse_addr(lat, lng):
    """toạ độ → địa chỉ (OpenStreetMap)"""
    req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode({"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 17, "accept-language": "vi"}),
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: j = json.load(r)
    except Exception: j = {}
    time.sleep(1.1)
    a = j.get("address") or {}
    parts = [a.get("road"), a.get("quarter") or a.get("neighbourhood") or a.get("hamlet") or a.get("village"), a.get("suburb") or a.get("town") or a.get("city_district")]
    return ", ".join(dict.fromkeys(x for x in parts if x)) + (", Hà Nội" if parts else "")

def in_box(lat, lng, box):
    try: return box[0] - .05 <= float(lng) <= box[2] + .05 and box[3] - .05 <= float(lat) <= box[1] + .05
    except (TypeError, ValueError): return False

def geo_addr(info, box):
    """địa chỉ (dict từ Gemini) → toạ độ. Trả (lat, lng, mức) với mức "Web" (đến đường/khu) hoặc "Địa danh" (đến xã/phường)"""
    addr, duong, xa = (info.get("dia_chi") or "").strip(), (info.get("duong") or "").strip(), (info.get("xa_phuong") or "").strip()
    for q, lv in ((addr, "Web"), (f"{duong}, {xa}" if duong and xa else "", "Web"), (duong, "Web"), (xa, "Địa danh")):
        if not q: continue
        for r in nominatim(q + ", Hà Nội", box) or []:
            if not in_box(r["lat"], r["lon"], box) or r.get("type") in ("city", "state", "county", "administrative") and lv == "Web": continue
            if lv == "Web" and duong and fold(duong).strip() and fold(re.sub(r"^(đường|phố)\s+", "", duong, flags=re.I)) not in fold(r.get("display_name")) and q != addr: continue
            return float(r["lat"]), float(r["lon"]), lv
    if info.get("chac_chan") in ("cao", "trung bình") and in_box(info.get("lat"), info.get("lng"), box): return float(info["lat"]), float(info["lng"]), "Web"
    return None

def osm_name(name, box):
    for q in name_variants(name):
        j = nominatim(q, box)
        if not j: continue
        r = j[0]; disp = fold(r.get("display_name")); core = re.sub(r"^ (khu do thi moi|khu do thi|khu tai dinh cu|khu dan cu) ", " ", fold(q))
        if len(core.strip()) < 4 or core not in disp or " ha noi " not in disp: continue
        cat, ty = r.get("category"), r.get("type")
        if cat in BAD_CAT or ty in ("industrial", "pitch", "stadium", "motorway_junction"): continue
        if cat in ("landuse", "building") or (AREA.search(disp) and cat in ("highway", "place", "leisure")): return float(r["lat"]), float(r["lon"])
    return None

def osm_place(cands, box):
    for c, street in cands:
        for ft in ([""] if street else ["settlement", ""]):
            for r in nominatim(c, box, ft) or []:
                disp = fold(r.get("display_name"))
                if fold(re.sub(r"^(xã|phường|thị trấn|thôn)\s+", "", c, flags=re.I)) not in disp or " ha noi " not in disp: continue
                if r.get("category") not in ("place", "boundary", "highway") or r.get("type") in ("motorway_junction", "bus_stop"): continue
                if not ft and r.get("category") != "highway": continue
                return float(r["lat"]), float(r["lon"])
    return None

def geocode(name, k, dev="", prev=None):
    """Kết hợp mọi nguồn để ra ĐỊA CHỈ + toạ độ. → (lat, lng, nguon, dia_chi); nguon None = hết thời gian/hạn mức, để lần sau.
       Thứ tự: Google Maps (nếu có khoá) → Gemini + Google Search → OpenStreetMap theo tên → hỏi Gemini tên phố/xã
               → địa danh trong tên dự án → (đã có toạ độ mà chưa có địa chỉ) đổi toạ độ ra địa chỉ."""
    vn, la, lo = HUYEN[k]; box = [round(lo - .12, 3), round(la + .12, 3), round(lo + .12, 3), round(la - .12, 3)]
    if time.monotonic() - T0 > TIME_BUDGET: return None, None, None, ""
    g = google_places(name, vn, box)
    if g: return g[0], g[1], "Web", g[2]
    found = []   # (lat, lng, nguon, dia_chi) theo độ tin cậy giảm dần
    w = ask_web(name, dev, vn)
    if w:
        hit = geo_addr(w, box)
        if hit: return hit[0], hit[1], hit[2], w.get("dia_chi") or ", ".join(x for x in (w.get("duong"), w.get("xa_phuong"), vn) if x)
    o = osm_name(name, box)
    if o: found.append((o[0], o[1], "OSM", (w or {}).get("dia_chi") or ""))
    if not found:
        s_ = ask_street(name, dev, vn)
        if s_:
            hit = geo_addr(s_, box)
            if hit: return hit[0], hit[1], hit[2], s_.get("dia_chi") or ", ".join(x for x in (s_.get("duong"), s_.get("xa_phuong"), vn) if x)
            w = w or s_
    if not found:
        pl = osm_place(([((w or {}).get("xa_phuong"), False)] if (w or {}).get("xa_phuong") else []) + place_chunks(name), box)
        if pl: found.append((pl[0], pl[1], "Địa danh", ""))
    if not found and prev and prev[0]: found.append(prev)   # giữ vị trí cũ (vd. địa danh) nếu lần này không tìm thêm được
    if found:
        lat, lng, src, addr = found[0]
        if not addr: addr = reverse_addr(lat, lng) + (" (gần đúng)" if src == "Địa danh" else "")
        return lat, lng, src, addr
    if _ai["off"] and AI_KEY: return None, None, "SKIP", ""   # Gemini tạm dừng → bỏ qua dự án này, lần sau thử lại (không kết luận "không thấy")
    return None, None, "Không thấy", ""

def coord_sheet(wb, todo):
    """đọc / bổ sung sheet "Toa do"; trả về {ten|huyen_en: (lat, lng, vi_tri, dia_chi)}.
       Dự án nào chưa có ĐỊA CHỈ (trừ "Sửa tay") sẽ được tìm lại mỗi ngày một lần cho tới khi có."""
    if "Toa do" not in wb.sheetnames:
        ws = wb.create_sheet("Toa do"); ws.append(["ten", "huyen_en", "lat", "lng", "nguon", "dia_chi", "tim_web"])
    ws = wb["Toa do"]; rows_, changed = {}, False
    if ws.cell(1, 7).value != "tim_web": ws.cell(1, 7, "tim_web")
    for r in ws.iter_rows(min_row=2):
        if r[0].value: rows_[f"{r[0].value}|{r[1].value}"] = r
    online, today, n_new = os.environ.get("GEOCODE", "1") != "0", time.strftime("%Y-%m-%d"), 0
    # chưa có địa chỉ trước, dự án mới trước
    order = sorted(todo, key=lambda t: (f"{t[0]}|{t[1]}" in rows_,))
    for ten, k, dev in order:
        if not online: break
        key = f"{ten}|{k}"; old = rows_.get(key); prev = None
        if old is not None:
            src0 = str(old[4].value or ""); addr0 = str(old[5].value or "").strip()
            if src0 == "Sửa tay" or (addr0 and "(gần đúng)" not in addr0 and src0 in ("Web", "OSM")) or str(ws.cell(old[0].row, 7).value or "") == today: continue
            if old[2].value: prev = (old[2].value, old[3].value, src0, addr0)
        lat, lng, src, addr = geocode(ten, k, dev, prev)
        if src is None:
            print("  → hết thời gian của lượt chạy; lần chạy sau tìm tiếp"); break
        if src == "SKIP": continue
        print(f"  {src:10} {ten} ({k}) {addr}")
        vals = [ten, k, round(float(lat), 6) if lat else None, round(float(lng), 6) if lng else None, src, addr or None, today]
        if old is None: ws.append(vals); rows_[key] = ws[ws.max_row]
        else:
            for ci, v in enumerate(vals, 1): ws.cell(old[0].row, ci, v)
            rows_[key] = ws[old[0].row]
        changed = True; n_new += 1
        if n_new % 25 == 0: wb.save(SRC)   # lưu dần
    out = {}
    for key, r in rows_.items():
        lat, lng, src = r[2].value, r[3].value, str(r[4].value or "").strip()
        addr = str(r[5].value or "").strip() if len(r) > 5 else ""
        try: lat, lng = float(lat), float(lng)
        except (TypeError, ValueError): continue
        out[key] = (lat, lng, {"Web": "Theo địa chỉ", "OSM": "Theo OSM", "Địa danh": "Gần đúng theo địa danh"}.get(src, "Sửa tay"), addr)
    miss = sum(1 for r in rows_.values() if not str(r[5].value or "").strip())
    print(f"Còn {miss} dự án chưa có địa chỉ (sẽ tìm lại ở lần chạy sau)")
    return out, changed

BOSUNG = "data/du-an-bo-sung.json"
COLS = ["quy", "nhom", "ten", "chu_dau_tu", "huyen", "huyen_en", "lat", "lng", "vi_tri", "can_tong", "can_vl", "can_th", "can_sh",
        "hap_thu", "gia_vl", "gia_th", "gia_sh", "mo_ban", "quy_mo_ha", "tinh_trang", "dia_chi", "link", "ghi_chu", "nguon"]

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
    # thông tin bổ sung / sửa tay từ trang admin (tab "Dự án") – luôn được ưu tiên
    if os.path.exists(BOSUNG):
        bs = json.load(open(BOSUNG, encoding="utf-8")).get("du_an", {})
        idx = {f'{r["ten"]}|{r["huyen_en"]}|{r["nhom"]}': r for r in out}
        for key, item in bs.items():
            r = idx.get(key)
            if r is None:
                if not item.get("moi"): continue
                t_, k_, n_ = key.split("|"); r = {"quy": QUY, "nhom": n_, "ten": t_, "huyen_en": k_, "huyen": HUYEN.get(k_, (k_,))[0], "nguon": "Bổ sung thủ công"}; out.append(r)
            for f_, v in item.items():
                if f_ in COLS and v not in (None, ""): r[f_] = str(v)
            if item.get("lat") and item.get("lng"): r["vi_tri"] = item.get("vi_tri") or "Sửa tay"
            if not r.get("lat"):   # dự án mới chưa có toạ độ → quanh tâm huyện
                _, la, lo = HUYEN.get(r["huyen_en"], ("", 21.03, 105.85)); r["lat"], r["lng"], r["vi_tri"] = f"{la:.5f}", f"{lo:.5f}", "Theo huyện"
        print(f"Áp dụng {len(bs)} mục bổ sung từ admin ({BOSUNG})")
    cols = COLS
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
