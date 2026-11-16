#!/usr/bin/env python3
"""Lấy tỷ giá USD/VND của Vietcombank và ghi vào rate.json.
Fetch Vietcombank's USD/VND board rate and write it to rate.json.
Chạy hằng ngày bởi GitHub Actions (.github/workflows/update-rate.yml).
Nếu lấy thất bại: giữ nguyên rate.json cũ, không làm hỏng trang."""
import json, re, sys, urllib.request, datetime, pathlib

VCB_XML = "https://portal.vietcombank.com.vn/UserControls/TVPortal.TyGia/pXML.aspx"
# Loại tỷ giá dùng để quy đổi: "Transfer" (mua chuyển khoản – mặc định), "Buy" (mua tiền mặt), "Sell" (bán ra)
RATE_TYPE = "Transfer"
OUT = pathlib.Path(__file__).resolve().parent.parent / "rate.json"

def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None

def parse(xml):
    m = re.search(r'<Exrate\b[^>]*CurrencyCode="USD"[^>]*/?>', xml)
    if not m:
        raise ValueError("Không thấy dòng USD trong dữ liệu Vietcombank")
    tag = m.group(0)
    attr = dict(re.findall(r'(\w+)="([^"]*)"', tag))
    t = re.search(r"<DateTime>([^<]+)</DateTime>", xml)
    rates = {k.lower(): num(attr.get(k)) for k in ("Buy", "Transfer", "Sell")}
    rate = rates.get(RATE_TYPE.lower()) or rates.get("transfer") or rates.get("sell")
    if not rate or not (10000 < rate < 100000):
        raise ValueError(f"Tỷ giá không hợp lệ: {rates}")
    return {"usd_vnd": rate, "type": RATE_TYPE.lower(), **rates,
            "bank_time": t.group(1).strip() if t else ""}

def main():
    try:
        req = urllib.request.Request(VCB_XML, headers={"User-Agent": "Mozilla/5.0 (land-auction-map rate bot)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read().decode("utf-8", "replace")
        data = parse(xml)
    except Exception as e:  # giữ file cũ
        print(f"[!] Không cập nhật được tỷ giá, giữ nguyên rate.json: {e}")
        return 0
    vn = datetime.timezone(datetime.timedelta(hours=7))
    now = datetime.datetime.now(vn)
    data.update({"date": now.strftime("%Y-%m-%d"), "fetched_at": now.isoformat(timespec="seconds"),
                 "source": "Vietcombank", "source_url": VCB_XML})
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] 1 USD = {data['usd_vnd']:,.0f} VND ({RATE_TYPE}) — {data['bank_time']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
