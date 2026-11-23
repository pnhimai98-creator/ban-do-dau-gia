#!/usr/bin/env python3
"""Gom tin báo mới nhất cho từng hạ tầng trên bản đồ.
Đọc  data/ha-tang-tu-khoa.json  (danh sách hạ tầng + từ khóa, tự sửa được)
Ghi  data/tin-ha-tang.json      (bản đồ đọc file này khi bấm vào hạ tầng)
Nguồn: Google News RSS tiếng Việt. Chỉ dùng thư viện chuẩn của Python."""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAO = os.path.join(GOC, "data", "ha-tang-tu-khoa.json")
RA = os.path.join(GOC, "data", "tin-ha-tang.json")
SO_TIN = 8          # số tin giữ lại cho mỗi hạ tầng
SO_NGAY = 120       # bỏ tin cũ hơn số ngày này
UU_TIEN = ["baodauthau.vn", "vnexpress.net", "dantri.com.vn", "laodong.vn", "tienphong.vn",
           "hanoimoi.vn", "hanoionline.vn", "kinhtedothi.vn", "cafef.vn", "congly.vn",
           "vietnamnet.vn", "tuoitre.vn", "thanhnien.vn", "baochinhphu.vn", "vneconomy.vn",
           "znews.vn", "nhandan.vn", "giaothong.vn", "baogiaothong.vn"]

def fold(s):
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = s.replace("–", "-").replace(",", ".")
    return re.sub(r"\s+", " ", s).strip()

def rss(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f'"{q}" when:{SO_NGAY}d', "hl": "vi", "gl": "VN", "ceid": "VN:vi"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ban-do-dau-gia bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def doc_rss(xml_bytes):
    out = []
    for it in ET.fromstring(xml_bytes).iter("item"):
        src = it.find("source")
        title = (it.findtext("title") or "").strip()
        nguon = (src.text or "").strip() if src is not None else ""
        if nguon and title.endswith(" - " + nguon):
            title = title[: -len(nguon) - 3].strip()
        try:
            ngay = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            ngay = None
        out.append({"tieu_de": title, "link": (it.findtext("link") or "").strip(),
                    "nguon": nguon, "web": src.get("url", "") if src is not None else "",
                    "ngay": ngay.strftime("%Y-%m-%d") if ngay else ""})
    return out

def main():
    ds = json.load(open(VAO, encoding="utf-8"))
    cu = {}
    if os.path.exists(RA):
        try: cu = json.load(open(RA, encoding="utf-8")).get("muc", {})
        except Exception: cu = {}
    han = time.strftime("%Y-%m-%d", time.gmtime(time.time() - SO_NGAY * 86400))
    muc, loi = {}, 0
    for h in ds:
        ten, tks = h["ten"], h.get("tu_khoa") or [h["ten"]]
        loai = [fold(x) for x in h.get("loai_tru", [])]
        gom = {}
        for t in list(cu.get(ten, {}).get("tin", [])):
            gom[fold(t["tieu_de"])] = t
        for tk in tks:
            try:
                tins = doc_rss(rss(tk))
            except Exception as e:
                loi += 1; print(f"! {ten} / {tk}: {e}", file=sys.stderr); continue
            for t in tins:
                ft = fold(t["tieu_de"])
                if fold(tk) not in ft or any(x in ft for x in loai):
                    continue          # chỉ nhận bài có đúng tên hạ tầng trong tiêu đề
                gom.setdefault(ft, t)
            time.sleep(1.5)
        tin = [t for t in gom.values() if not t.get("ngay") or t["ngay"] >= han]
        uu = lambda t: any(d in (t.get("web") or "") for d in UU_TIEN)
        tin.sort(key=lambda t: (t.get("ngay") or "", uu(t)), reverse=True)
        muc[ten] = {"tu_khoa": tks, "tin": tin[:SO_TIN],
                    "tim_them": "https://news.google.com/search?" + urllib.parse.urlencode(
                        {"q": tks[0], "hl": "vi", "gl": "VN", "ceid": "VN:vi"})}
        print(f"{ten}: {len(muc[ten]['tin'])} tin")
    if loi and loi >= sum(len(h.get('tu_khoa') or [1]) for h in ds):
        sys.exit("Không lấy được tin nào – giữ nguyên file cũ.")
    json.dump({"cap_nhat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "muc": muc},
              open(RA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
