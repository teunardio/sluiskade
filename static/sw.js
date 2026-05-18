/*
 * Sluiskade service worker
 *
 * Strategie:
 *   - HTML pagina's       → network-first (verse content wint, cache als
 *                            fallback bij offline)
 *   - Static assets       → cache-first met background revalidate
 *   - Foto thumbnails     → cache-first (zelden veranderend, kostbaar bij hertanken)
 *   - Auth/admin/sluis    → NEVER cache (gevoelige content moet altijd
 *                            actueel zijn en mag niet stale geserveerd worden
 *                            aan een ingelogde gebruiker)
 *
 * Bump CACHE_NAME bij een grote release zodat oude clients hun cache wissen
 * en met verse assets verder gaan. Stale-while-revalidate dekt de meeste
 * edge cases tussendoor.
 */
const CACHE_NAME = 'sluiskade-v1';

// App shell: vooraf cachen zodat eerste offline-load meteen werkt
const PRECACHE = [
  '/static/favicon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/manifest.webmanifest',
];

// Paden die NOOIT gecached worden (gevoelig of state-wijzigend)
const NEVER_CACHE = [
  /\/portaal\/login/,
  /\/portaal\/verify/,
  /\/portaal\/password/,
  /\/portaal\/logout/,
  /\/portaal\/aanvragen/,
  /\/portaal\/foto\/\d+\/(like|delete)/,
  /\/portaal\/upload$/,
  /\/portaal\/download\//,
  /\/admin\//,
  /\/sluis\//,
  /\/healthz/,
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Alleen GET cachen, alleen same-origin
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Auth/admin/sluis: doorlaten naar netwerk, nooit cachen
  if (NEVER_CACHE.some((re) => re.test(url.pathname))) {
    return; // SW grijpt niet in, browser doet z'n ding
  }

  // Foto-media: cache-first (thumbnails + originelen veranderen niet)
  if (url.pathname.startsWith('/media/')) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // Static assets: cache-first met background update
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // HTML pagina's: network-first, fallback naar cache bij offline
  if (req.mode === 'navigate' || req.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirst(req));
    return;
  }

  // Default: laat de browser het zelf doen
});

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) {
    // Background revalidate zodat de cache niet eeuwig stale blijft
    fetch(request)
      .then((resp) => {
        if (resp && resp.ok) cache.put(request, resp.clone());
      })
      .catch(() => {});
    return cached;
  }
  try {
    const resp = await fetch(request);
    if (resp && resp.ok) cache.put(request, resp.clone());
    return resp;
  } catch (err) {
    // Geen network én geen cache → laat browser falen
    return new Response('Offline en niet in cache', { status: 503 });
  }
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const resp = await fetch(request);
    if (resp && resp.ok) cache.put(request, resp.clone());
    return resp;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    // Geen netwerk én geen cache: minimale offline-pagina
    return new Response(
      '<!doctype html><meta charset=utf-8><title>Offline</title>' +
      '<body style="font-family:sans-serif;padding:2rem;text-align:center;color:#0c4a6e">' +
      '<h1>Offline</h1><p>Geen internet en deze pagina staat niet in de cache.</p>' +
      '<p><a href="/portaal" style="color:#0ea5e9">Probeer opnieuw</a></p>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}
