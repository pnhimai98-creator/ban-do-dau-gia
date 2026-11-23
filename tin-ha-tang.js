/* Tin mới nhất cho hạ tầng: khi mở popup của một hạ tầng trên bản đồ,
   tự thêm danh sách link báo từ data/tin-ha-tang.json (workflow cập nhật mỗi ngày).
   Không cần sửa code bản đồ – chỉ cần nhúng file này vào index.html. */
(function () {
  var URL = "data/tin-ha-tang.json", DATA = null, ds = [];
  var fold = function (s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/gi, "d")
      .toLowerCase().replace(/[–—]/g, "-").replace(/,/g, ".").replace(/\s+/g, " ").trim();
  };
  var esc = function (s) { return String(s || "").replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); };
  fetch(URL + "?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
    if (!j || !j.muc) return; DATA = j;
    Object.keys(j.muc).forEach(function (ten) {
      [ten].concat(j.muc[ten].tu_khoa || []).forEach(function (k) { ds.push({k: fold(k), ten: ten}); });
    });
    ds.sort(function (a, b) { return b.k.length - a.k.length; });   /* tên dài khớp trước */
    document.querySelectorAll(".leaflet-popup-content").forEach(gan);
  }).catch(function () {});

  function gan(el) {
    if (!DATA || !el || el.querySelector(".tin-ha-tang")) return;
    var txt = fold(el.textContent), hit = ds.find(function (d) { return d.k && txt.indexOf(d.k) >= 0; });
    if (!hit) return;
    var m = DATA.muc[hit.ten], tin = (m.tin || []).slice(0, 5);
    var html = '<div class="tin-ha-tang" style="margin-top:8px;padding-top:6px;border-top:1px solid #ddd;font-size:12px;line-height:1.4">' +
      '<b>📰 Tin mới nhất</b>' +
      (tin.length ? '<ul style="margin:4px 0 0;padding-left:16px">' + tin.map(function (t) {
        return '<li style="margin-bottom:3px"><a href="' + esc(t.link) + '" target="_blank" rel="noopener">' + esc(t.tieu_de) + '</a>' +
          '<br><span style="color:#777">' + esc(t.nguon) + (t.ngay ? " · " + t.ngay.split("-").reverse().join("/") : "") + '</span></li>';
      }).join("") + '</ul>' : '<div style="color:#777">Chưa có tin trong 120 ngày qua.</div>') +
      '<a href="' + esc(m.tim_them) + '" target="_blank" rel="noopener" style="color:#777">Tìm thêm tin ↗</a>' +
      '<div style="color:#aaa;font-size:11px">Tự cập nhật ' + esc((DATA.cap_nhat || "").slice(0, 10).split("-").reverse().join("/")) + '</div></div>';
    el.insertAdjacentHTML("beforeend", html);
    var p = el.closest(".leaflet-popup"); if (p && window.dispatchEvent) setTimeout(function () { window.dispatchEvent(new Event("resize")); }, 0);
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.classList && n.classList.contains("leaflet-popup-content")) gan(n);
        n.querySelectorAll && n.querySelectorAll(".leaflet-popup-content").forEach(gan);
      });
      if (m.target && m.target.closest) { var c = m.target.closest(".leaflet-popup-content"); if (c) gan(c); }
    });
  }).observe(document.body, {childList: true, subtree: true});
})();
