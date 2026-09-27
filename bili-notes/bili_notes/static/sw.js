// 网络优先；断网时回退到看过的页面缓存，地铁里也能读已打开过的笔记。
const CACHE = "bili-notes-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  event.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp.ok && !resp.redirected) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return resp;
      })
      .catch(async () => (await caches.match(req)) ||
        new Response("离线中，这个页面还没有缓存。", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } }))
  );
});
