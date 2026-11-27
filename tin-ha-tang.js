/* Tin mới nhất cho hạ tầng (cầu, đường, metro…).
   - Bấm vào hạ tầng có popup  → thêm mục "📰 Tin mới nhất" vào cuối popup.
   - Bấm vào hạ tầng chỉ có nhãn rê chuột (tooltip) → mở khung tin nhỏ cạnh chỗ bấm.
   Dữ liệu: data/tin-ha-tang.json (workflow "Cập nhật tin hạ tầng" tạo mỗi sáng; chỉ gom tin hạ tầng đang xây / quy hoạch, lọc bằng Gemini).
   Không cần sửa code bản đồ – chỉ cần nhúng file này vào index.html. */
(function () {
  var URL = "data/tin-ha-tang.json", DATA = null, ds = [], box = null;
  var fold = function (s) {
    return String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/gi, "d")
      .toLowerCase().replace(/[–—]/g, "-").replace(/,/g, ".").replace(/\s+/g, " ").trim();
  };
  var esc = function (s) { return String(s || "").replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); };
  var vn = function (d) { return String(d || "").slice(0, 10).split("-").reverse().join("/"); };

  fetch(URL + "?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
    if (!j || !j.muc) return; DATA = j;
    Object.keys(j.muc).forEach(function (ten) {
      [ten].concat(j.muc[ten].tu_khoa || []).forEach(function (k) { ds.push({k: fold(k), ten: ten}); });
    });
    ds.sort(function (a, b) { return b.k.length - a.k.length; });   /* tên dài khớp trước */
    document.querySelectorAll(".leaflet-popup-content").forEach(ganPopup);
  }).catch(function () {});

  function timTen(txt) { txt = fold(txt); var h = ds.find(function (d) { return d.k && txt.indexOf(d.k) >= 0; }); return h ? h.ten : null; }

  function khoiTin(ten) {
    var m = DATA.muc[ten], tin = (m.tin || []).slice(0, 5);
    if (m.van_hanh) return '<b>📰 Tin hạ tầng</b><div style="color:#777">Đã đi vào vận hành – không theo dõi tin tự động.</div>' +
      '<a href="' + esc(m.tim_them) + '" target="_blank" rel="noopener" style="color:#777">Tìm tin ↗</a>';
    return '<b>📰 Tin mới nhất</b>' +
      (tin.length ? '<ul style="margin:4px 0 0;padding-left:16px">' + tin.map(function (t) {
        return '<li style="margin-bottom:4px"><a href="' + esc(t.link) + '" target="_blank" rel="noopener">' + esc(t.tieu_de) + '</a>' +
          '<br><span style="color:#777">' + esc(t.nguon) + (t.ngay ? " · " + vn(t.ngay) : "") + '</span></li>';
      }).join("") + '</ul>' : '<div style="color:#777">Chưa có tin trong 120 ngày qua.</div>') +
      '<a href="' + esc(m.tim_them) + '" target="_blank" rel="noopener" style="color:#777">Tìm thêm tin ↗</a>' +
      '<div style="color:#aaa;font-size:11px">Tự cập nhật ' + esc(vn(DATA.cap_nhat)) + '</div>';
  }

  /* 1) hạ tầng có popup */
  function ganPopup(el) {
    if (!DATA || !el || el.querySelector(".tin-ha-tang")) return;
    var ten = timTen(el.textContent); if (!ten) return;
    el.insertAdjacentHTML("beforeend", '<div class="tin-ha-tang" style="margin-top:8px;padding-top:6px;border-top:1px solid #ddd;font-size:12px;line-height:1.4">' + khoiTin(ten) + '</div>');
    dongKhung();
  }
  var tipMoi = null, tipLuc = 0;   /* nhãn rê chuột vừa hiện gần nhất */
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.classList && n.classList.contains("leaflet-tooltip")) { tipMoi = n; tipLuc = Date.now(); }
        if (n.classList && n.classList.contains("leaflet-popup-content")) ganPopup(n);
        n.querySelectorAll && n.querySelectorAll(".leaflet-popup-content").forEach(ganPopup);
      });
      if (m.target && m.target.closest) { var c = m.target.closest(".leaflet-popup-content"); if (c) ganPopup(c); }
    });
  }).observe(document.body, {childList: true, subtree: true});

  /* 2) hạ tầng chỉ có tooltip: bấm vào thì mở khung tin */
  function dongKhung() { if (box) { box.remove(); box = null; } }
  function moKhung(ten, x, y) {
    dongKhung();
    box = document.createElement("div");
    box.className = "tin-ha-tang-khung";
    box.style.cssText = "position:fixed;z-index:10000;max-width:340px;max-height:60vh;overflow:auto;background:#fff;color:#222;" +
      "border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,.25);padding:10px 12px;font:12px/1.4 system-ui,sans-serif";
    box.innerHTML = '<button type="button" aria-label="Đóng" style="float:right;border:0;background:none;font-size:16px;cursor:pointer;line-height:1">✕</button>' +
      '<div style="font-weight:700;font-size:13px;margin-bottom:4px">' + esc(ten) + '</div>' + khoiTin(ten);
    document.body.appendChild(box);
    var w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(8, Math.min(x + 12, innerWidth - w - 8)) + "px";
    box.style.top = Math.max(8, Math.min(y + 12, innerHeight - h - 8)) + "px";
    box.querySelector("button").onclick = dongKhung;
  }
  function moKhungTim(nhan, x, y) {
    var q = encodeURIComponent(nhan);
    DATA.muc["__tam__"] = {tin: [], tim_them: "https://news.google.com/search?q=" + q + "&hl=vi&gl=VN&ceid=VN:vi"};
    moKhung("__tam__", x, y);
    box.querySelector("div").textContent = nhan;
    var ul = box.querySelector("div[style*='color:#777']"); if (ul) ul.textContent = "Chưa có trong danh sách tự cập nhật – bấm “Tìm thêm tin” để xem tin mới.";
    delete DATA.muc["__tam__"];
  }
  document.addEventListener("click", function (e) {
    if (!DATA) return;
    if (box && box.contains(e.target)) return;
    var hitEl = e.target.closest && e.target.closest(".leaflet-interactive, .leaflet-marker-icon");
    if (!hitEl) { if (box && !e.target.closest(".leaflet-popup")) dongKhung(); return; }
    var x = e.clientX, y = e.clientY;
    setTimeout(function () {
      if (document.querySelector(".leaflet-popup .tin-ha-tang")) { dongKhung(); return; }   /* đã có popup kèm tin */
      var hien = function (t) { return t && t.isConnected && t.offsetParent !== null && getComputedStyle(t).opacity !== "0"; };
      /* ưu tiên nhãn vừa hiện khi rê chuột; nếu không có thì lấy nhãn gần chỗ bấm nhất (≤120px) */
      var tip = hien(tipMoi) && Date.now() - tipLuc < 15000 ? tipMoi : null;
      if (!tip) {
        var best = 121;
        [].slice.call(document.querySelectorAll(".leaflet-tooltip")).filter(hien).forEach(function (t) {
          var r = t.getBoundingClientRect(), dx = Math.max(r.left - x, 0, x - r.right), dy = Math.max(r.top - y, 0, y - r.bottom), d = Math.sqrt(dx * dx + dy * dy);
          if (d < best) { best = d; tip = t; }
        });
      }
      var chu = tip ? tip.textContent : (hitEl.getAttribute("title") || hitEl.getAttribute("aria-label") || "");
      var ten = timTen(chu);
      if (ten) { moKhung(ten, x, y); return; }
      /* hạ tầng chưa có trong danh sách tin: vẫn mở khung với link tìm tin */
      var nhan = (tip ? (tip.querySelector("b,strong") || tip).textContent : chu).split(/\s[–—-]\s|\n/)[0].replace(/\([^)]*\)/g, "").trim();
      if (tip && nhan && !document.querySelector(".leaflet-popup")) moKhungTim(nhan, x, y); else dongKhung();
    }, 80);
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") dongKhung(); });
})();
