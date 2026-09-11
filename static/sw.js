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

    if (url.pathname === "/voice-test") {
        event.respondWith(networkFirstThenCache(request));
        return;
    }

    if (url.pathname === "/predict") {
        event.respondWith(staleWhileRevalidate(request));
        return;
    }
});

async function networkFirstThenCache(request) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        cache.put(request, response.clone());
        return response;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        throw err;
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    const networkFetch = fetch(request)
        .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
        })
        .catch(() => null);

    const fresh = cached ? null : await networkFetch;
    return cached || fresh || new Response(
        JSON.stringify({ error: "offline_no_cache" }),
        { status: 503, headers: { "Content-Type": "application/json" } }
    );
}
