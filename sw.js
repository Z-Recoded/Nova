// Nova Controller service worker (86baxahn7). Caches ONLY the app shell
// (this page's own HTML/CSS/JS + icons) so the PWA installs and launches
// full-screen without a network round-trip. Deliberately does NOT cache
// /escalations, /tier-proposals, /dispatch-log, /label-queue, or any other
// live data route -- a stale cached escalation would be actively dangerous
// to answer against. Those fetches always go to the network; if the
// network is down they fail visibly (an error/empty state in the page),
// they never silently serve old data.

const SHELL_CACHE = "nova-controller-shell-v1";
const SHELL_URLS = ["/controller", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (!SHELL_URLS.includes(url.pathname)) {
    return; // not a shell asset -- let the browser handle it normally (network only)
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
