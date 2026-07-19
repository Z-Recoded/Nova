// Nova Controller service worker (86baxahn7). Only truly static assets
// (icons) are cache-first -- /controller itself is network-first, always
// fetching the latest deployed code when online and only falling back to
// a cached copy if genuinely offline. Deliberately does NOT cache
// /escalations, /tier-proposals, /dispatch-log, /label-queue, or any other
// live data route -- a stale cached escalation would be actively dangerous
// to answer against. Those fetches always go straight to the network; if
// the network is down they fail visibly (an error/empty state in the
// page), they never silently serve old data.
//
// Cache-first for /controller was the original design and a real bug
// found live 2026-07-19: this page gets edited constantly during active
// development, but a browser only re-runs a service worker's install/
// activate cycle when sw.js itself changes byte-for-byte -- since sw.js
// wasn't touched between HTML deploys, already-installed clients kept
// serving an old cached /controller indefinitely, silently absorbing zero
// of several real fixes shipped the same night. Bumping CACHE_NAME here
// forces every already-installed client to detect this file as different
// and pick up the new (network-first) strategy on next load.

const CACHE_NAME = "nova-controller-shell-v2";
const STATIC_CACHE_URLS = ["/manifest.json", "/icon-192.png", "/icon-512.png"];
const NETWORK_FIRST_URL = "/controller";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_CACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (url.pathname === NETWORK_FIRST_URL) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  if (!STATIC_CACHE_URLS.includes(url.pathname)) {
    return; // not a cached asset -- let the browser handle it normally (network only)
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
