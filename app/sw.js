/* Cosas · service worker: la app funciona sin conexión tras la primera visita, y enseña los avisos de
   Cosas con y Cosas de (notificaciones push: alguien ha añadido una cosa a una de tus listas). */
'use strict';

// Sello de versión: lo actualiza herramientas/sellar_version.py; ejecútalo antes de publicar cualquier cambio.
const VERSION_CACHE = 'cosas-v1.2.0-865183a0';
const PREFIJO_CACHE = 'cosas-';
const PORTADA = './index.html';
// Rutas propias del alojamiento (Netlify), no de la app: ni se sirven ni se guardan desde aquí. /api/ es la
// función de los avisos (netlify/functions/avisos.mjs).
const RUTAS_DEL_ALOJAMIENTO = ['/.netlify/', '/api/'];
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
  './icons/aviso-96.png', // La silueta de los avisos en la barra de estado de Android.
];
const LINEAS_POR_AVISO = 5;

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

// ---------- Avisos ----------

const texto = (v, otro) => (typeof v === 'string' && v ? v : otro);

/** El enlace de un aviso, si es de este dominio (ruta, búsqueda y ancla); si no, la app. */
function enlaceDeAviso(url) {
  if (typeof url === 'string' && url) {
    try {
      const destino = new URL(url, self.location.origin);
      if (destino.origin === self.location.origin) return destino.pathname + destino.search + destino.hash;
    } catch (error) { /* Abajo. */ }
  }
  return './';
}

/**
 * Un aviso de la función de avisos: { titulo, linea, url, etiqueta }. Los de una misma lista se juntan en uno
 * solo (misma etiqueta), con las últimas líneas, la más nueva arriba.
 */
async function ensenarAviso(aviso) {
  const etiqueta = texto(aviso.etiqueta, 'cosas');
  let anteriores = [];
  try {
    anteriores = await self.registration.getNotifications({ tag: etiqueta });
  } catch (error) { /* Sin poder leerlos: solo el nuevo. */ }
  const lineas = [texto(aviso.linea, 'Hay cosas nuevas')];
  for (const anterior of anteriores) {
    const suyas = anterior.data && Array.isArray(anterior.data.lineas) ? anterior.data.lineas : [];
    lineas.push(...suyas.filter((linea) => typeof linea === 'string'));
  }
  const url = enlaceDeAviso(aviso.url);
  const lineasVisibles = lineas.slice(0, LINEAS_POR_AVISO);
  await self.registration.showNotification(texto(aviso.titulo, 'Cosas'), {
    body: lineasVisibles.join('\n'),
    tag: etiqueta,
    renotify: true, // Aunque sustituya al anterior, suena y se ve otra vez.
    icon: 'icons/icon-192.png',
    badge: 'icons/aviso-96.png',
    lang: 'es',
    data: { url, lineas: lineasVisibles },
  });
}

// De uno en uno: dos avisos seguidos de la misma lista no se pisan al juntarse.
let turnoDeAvisos = Promise.resolve();

self.addEventListener('push', (evento) => {
  let aviso = {};
  try {
    aviso = (evento.data && evento.data.json()) || {};
  } catch (error) { /* Sin datos legibles: un aviso genérico. */ }
  turnoDeAvisos = turnoDeAvisos.catch(() => {}).then(() => ensenarAviso(aviso));
  evento.waitUntil(turnoDeAvisos);
});

/** Tocar un aviso abre esa lista: en la ventana de la app que ya haya abierta o en una nueva. */
async function abrirDesdeAviso(url) {
  const destino = new URL(url, self.location.href).href;
  const ventanas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const misma = ventanas.find((ventana) => ventana.url === destino);
  if (misma) return misma.focus();
  const ventana = ventanas.find((v) => new URL(v.url).origin === self.location.origin && typeof v.navigate === 'function');
  if (ventana) {
    try {
      await ventana.focus();
      return await ventana.navigate(destino);
    } catch (error) { /* Una ventana que no controla este service worker: se abre otra. */ }
  }
  return self.clients.openWindow(destino);
}

self.addEventListener('notificationclick', (evento) => {
  evento.notification.close();
  const datos = evento.notification.data || {};
  evento.waitUntil(abrirDesdeAviso(texto(datos.url, './')));
});
