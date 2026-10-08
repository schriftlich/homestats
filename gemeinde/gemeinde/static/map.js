/* Karten-Helfer für Leaflet (lokal eingebunden). Kartenbilder von OpenStreetMap. */
(function () {
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function base(el, opts) {
    var map = L.map(el, Object.assign({ scrollWheelZoom: true, zoomControl: true }, opts || {}));
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
    }).addTo(map);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    return map;
  }

  function pinIcon(kind) {
    return L.divIcon({
      className: "pin pin-" + kind,
      html: '<svg viewBox="0 0 24 32" width="24" height="32"><path d="M12 31s10-11.2 10-18.5A10 10 0 0 0 2 12.5C2 19.8 12 31 12 31z"/><circle cx="12" cy="12.5" r="4"/></svg>',
      iconSize: [24, 32], iconAnchor: [12, 31], popupAnchor: [0, -28],
    });
  }

  function churchIcon() {
    return L.divIcon({
      className: "pin pin-church",
      html: '<svg viewBox="0 0 32 32" width="26" height="26"><circle cx="16" cy="16" r="14"/><path d="M16 7v18M10 13h12"/></svg>',
      iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -12],
    });
  }

  window.GemeindeMap = { base: base, pinIcon: pinIcon, churchIcon: churchIcon, esc: esc };
})();
