const CACHE_NAME = "mandi-bol-v1";
const APP_SHELL_URL = "/voice-test";

self.addEventListener("install", (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.add(APP_SHELL_URL).catch(() => {}))
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const { request } = event;
    if (request.method !== "GET") return; // never cache POSTs (voice-advisory, sms)

    const url = new URL(request.url);

    if (url.pathname === "/voice-test" || url.pathname === "/predict") {
        event.respondWith(networkFirstThenCache(request));
        return;
    }
});

// Network first for both the app shell and /predict: forecasts change daily,
// so showing a cached one before the fresh one (the old stale-while-revalidate
// behaviour) could show yesterday's numbers on every repeat visit. The cache
// is only a fallback when the network fails or the server returns an error,
// and only successful (2xx) responses are ever written to it, so a 502/503
// during a deploy can't overwrite a good cached copy.
async function networkFirstThenCache(request) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        if (response.ok) {
            cache.put(request, response.clone());
            return response;
        }
        const cached = await cache.match(request);
        return cached || response;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        if (new URL(request.url).pathname === "/predict") {
            return new Response(
                JSON.stringify({ error: "offline_no_cache" }),
                { status: 503, headers: { "Content-Type": "application/json" } }
            );
        }
        throw err;
    }
}
