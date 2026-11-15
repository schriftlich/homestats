// App-Shell offline verfügbar machen. Bibeltexte werden in der App selbst gespeichert.
const CACHE = "bibelvers-v2";
const SHELL = ["./", "index.html", "style.css", "books.js", "srs.js", "app.js", "manifest.webmanifest", "icon.svg", "icon-192.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin || e.request.method !== "GET") return;
  // Netzwerk zuerst (immer aktuelle Version), offline aus dem Cache
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});
