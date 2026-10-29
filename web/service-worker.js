const CACHE = 'ivox-app-shell-v18';
const SHELL = ['/', '/index.html', '/style.css', '/online.css', '/player.css', '/sheet.css',
  '/app.js', '/player-controls.js', '/online.js', '/sheet.js', '/home.js', '/offline.js', '/crossfade.js', '/pwa.js', '/manifest.json', '/icon-192x192.png', '/icon-512x512.png', '/silence.wav',
  '/fonts/cinzel.woff2', '/fonts/inter.woff2'];
const ASSETS = new Set(SHELL);
const PAGES = new Set(['/', '/index.html', '/library', '/plan', '/files', '/privacy']);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(
    SHELL.map(path => new Request(path, {cache: 'reload'}))
  )).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => /^(onda|ivox)-app-shell-/.test(key) && key !== CACHE).map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  // Never intercept accounts, APIs, audio, third parties or the protected admin page.
  if (request.method !== 'GET' || url.origin !== self.location.origin ||
      request.headers.has('Range') || url.pathname.startsWith('/api/') ||
      url.pathname === '/admin') return;
  if (request.mode === 'navigate' && PAGES.has(url.pathname)) {
    event.respondWith(fetch(request).catch(async () => (await caches.match('/')) || Response.error()));
    return;
  }
  if (!ASSETS.has(url.pathname) || url.search) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(request);
      if (response.ok && response.type === 'basic') await cache.put(url.pathname, response.clone());
      return response;
    } catch {
      return (await cache.match(url.pathname)) || Response.error();
    }
  })());
});
