"""Chuyển file Excel báo cáo dự án thấp tầng (data/bao-cao-du-an.xlsx) thành:
  du-an.csv        – từng dự án (sơ cấp / thứ cấp / tương lai), vị trí GẦN ĐÚNG theo quận/huyện cũ
  du-an-huyen.csv  – số tổng hợp theo quận/huyện cũ (giá liền kề trung vị, nguồn cung tương lai)
File Excel cần 3 sheet: "Current Projects", "Secondary Projects", "Future Projects"
(đúng định dạng file đã tạo từ PDF). Đổi QUY bên dưới hoặc truyền: python scripts/nhap_du_an.py 2026-Q4
Muốn chỉ công khai số tổng hợp theo huyện: KHÔNG đưa du-an.csv lên repo (xoá file đó), chỉ giữ du-an-huyen.csv.
"""
import csv, math, statistics, sys
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

def main():
    wb = load_workbook(SRC, data_only=True)
    out, seen = [], {}
    def add(nhom, ten, cdt, h, **kw):
        k = huyen_key(h)
        if not k: print("Bỏ qua – không rõ huyện:", ten, h); return
        vn, la, lo = HUYEN[k]; i = seen[k] = seen.get(k, -1) + 1; dy, dx = spiral(i)
        out.append({"quy": QUY, "nhom": nhom, "ten": ten, "chu_dau_tu": cdt or "", "huyen": vn, "huyen_en": k,
                    "lat": f"{la + dy:.5f}", "lng": f"{lo + dx:.5f}", "vi_tri": "Theo huyện", "nguon": NGUON, **kw})
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
            "hap_thu", "gia_vl", "gia_th", "gia_sh", "mo_ban", "quy_mo_ha", "tinh_trang", "nguon"]
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
