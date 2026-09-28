/* Cosas · service worker: la app funciona sin conexión tras la primera visita. */
'use strict';

// Sello de versión: lo actualiza herramientas/sellar_version.py; ejecútalo antes de publicar cualquier cambio.
const VERSION_CACHE = 'cosas-v1.2.0-e4b3a28e';
const PREFIJO_CACHE = 'cosas-';
const PORTADA = './index.html';
// Rutas propias del alojamiento (Netlify), no de la app: ni se sirven ni se guardan desde aquí.
const RUTAS_DEL_ALOJAMIENTO = ['/.netlify/'];
// «Cosas con», «Cosas de» y «Tu cuenta», que netlify.toml trae de cosas.info, y el ayudante de acceso
// de Firebase (/__/): siempre de la red, al día, y nunca en la caché de la app (con una versión
// guardada se quedarían viejas hasta el siguiente sello).
const RUTAS_DE_COSAS_INFO = ['/con/', '/de/', '/cuenta/', '/__/', '/css/', '/js/', '/firebase-config.js', '/icons/favicon.svg'];
const RECURSOS = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './nube.js', // Solo se carga con sesión, pero sin conexión también tiene que estar.
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png',
  './icons/favicon-32.png',
];

/** Una respuesta redirigida (p. ej. /index.html → /) no sirve para navegar: se copia limpia. */
async function sinRedireccion(respuesta) {
  if (!respuesta.redirected) return respuesta;
  return new Response(await respuesta.blob(), {
    status: respuesta.status,
    statusText: respuesta.statusText,
    headers: respuesta.headers,
  });
}

/** Precarga la app completa, saltándose la caché HTTP del navegador. */
async function precargar() {
  const cache = await caches.open(VERSION_CACHE);
  await Promise.all(RECURSOS.map(async (url) => {
    const respuesta = await fetch(new Request(url, { cache: 'reload' }));
    if (!respuesta.ok) throw new Error(`No se pudo precargar ${url}`);
    await cache.put(url, await sinRedireccion(respuesta));
  }));
}

/** Borra solo las cachés antiguas de Cosas (el dominio puede alojar otras apps). */
async function limpiarCachesAntiguas() {
  const nombres = await caches.keys();
  const antiguas = nombres.filter((nombre) => nombre.startsWith(PREFIJO_CACHE) && nombre !== VERSION_CACHE);
  await Promise.all(antiguas.map((nombre) => caches.delete(nombre)));
}

/** Primero caché; si no está, red (y se guarda la respuesta válida). */
async function responder(peticion) {
  const cache = await caches.open(VERSION_CACHE);
  const esNavegacion = peticion.mode === 'navigate';
  const guardada = await cache.match(peticion, { ignoreSearch: esNavegacion });
  if (guardada) return guardada;

  try {
    const respuesta = await fetch(peticion);
    if (respuesta.ok && respuesta.type === 'basic') {
      cache.put(peticion, respuesta.clone()).catch(() => {});
    }
    return respuesta;
  } catch (error) {
    // Sin red: cualquier navegación dentro de la app cae en la portada guardada.
    const portada = esNavegacion ? await cache.match(PORTADA) : undefined;
    if (portada) return portada;
    throw error;
  }
}

self.addEventListener('install', (evento) => {
  evento.waitUntil(precargar().then(() => self.skipWaiting()));
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(limpiarCachesAntiguas().then(() => self.clients.claim()));
});

self.addEventListener('fetch', (evento) => {
  const peticion = evento.request;
  if (peticion.method !== 'GET') return;
  const url = new URL(peticion.url);
  if (url.origin !== self.location.origin) return;
  if ([...RUTAS_DEL_ALOJAMIENTO, ...RUTAS_DE_COSAS_INFO].some((ruta) => url.pathname.startsWith(ruta))) return;
  evento.respondWith(responder(peticion));
});
