self.addEventListener('install', (event) => {
  console.log('⚡ Service Worker установлен');
  event.waitUntil(
    caches.open('v1').then((cache) => {
      return cache.addAll([
        '/',                 // главная страница
        '/static/manifest.webmanifest',
        '/static/icons/android-chrome-192x192.png',
        '/static/icons/android-chrome-512x512.png',
        '/static/icons/apple-touch-icon.png',
        '/static/icons/favicon-32x32.png',
        '/static/icons/favicon-16x16.png'
      ]);
    })
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});
