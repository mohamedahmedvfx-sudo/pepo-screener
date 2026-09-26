const CACHE_NAME = 'bybit-screener-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/style.css?v=11',
  '/app.js?v=11',
  '/manifest.json'
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(clients.claim());
});

self.addEventListener('fetch', (e) => {
  // Let network requests pass through normally for live data
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
