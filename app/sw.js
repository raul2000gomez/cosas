/* Cosas · service worker: la app funciona sin conexión tras la primera visita, y enseña los avisos
   (notificaciones push): los de Cosas con y Cosas de (alguien ha añadido una cosa a una de tus listas) y los
   recordatorios de las cosas del grupo Recordar, con sus botones «Hecho» y «Mantener». */
'use strict';

// Sello de versión: lo actualiza herramientas/sellar_version.py; ejecútalo antes de publicar cualquier cambio.
const VERSION_CACHE = 'cosas-v1.2.0-ad467d16';
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
// Lo que pasa con los avisos de Recordar mientras la app no mira se deja apuntado en esta caché, que la app lee al
// abrirse (app.js): un service worker no llega a su almacenamiento. Su nombre no empieza como las cachés de la app:
// no se borra con cada versión.
const DATOS_DE_AVISOS = 'recordar-de-cosas';
const ESPERA_HECHO = 12000; // Tras un aviso, el sistema da pocos segundos al service worker: la llamada no puede eternizarse.
const HECHO_PENDIENTE = 'recordar-hecho'; // La etiqueta con la que se pide repetir un «Hecho» al volver la conexión.

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

// ---------- Recordatorios (el grupo Recordar de la app) ----------

const urlDeDato = (tipo, id) => new URL(`__recordar/${tipo}/${encodeURIComponent(id)}`, self.registration.scope).href;

/**
 * Apunta algo: para la app, «hechas» (se pulsó «Hecho» en el aviso de esa cosa) y «avisadas» (se avisó y falta la
 * respuesta); para el propio service worker, «por-decir» (un «Hecho» que la función de avisos aún no sabe).
 */
async function apuntar(tipo, id, dato) {
  try {
    const cache = await caches.open(DATOS_DE_AVISOS);
    await cache.put(urlDeDato(tipo, id), new Response(JSON.stringify(dato), { headers: { 'Content-Type': 'application/json' } }));
  } catch (error) { /* Sin almacenamiento: la función de avisos lo sabrá igual. */ }
}

async function olvidar(tipo, id) {
  try {
    if (!(await caches.has(DATOS_DE_AVISOS))) return; // No se crea por mirar.
    const cache = await caches.open(DATOS_DE_AVISOS);
    await cache.delete(urlDeDato(tipo, id));
  } catch (error) { /* No había nada apuntado. */ }
}

/** Lo apuntado de ese tipo: [{ id, … }]. */
async function apuntados(tipo) {
  const datos = [];
  try {
    if (!(await caches.has(DATOS_DE_AVISOS))) return datos;
    const cache = await caches.open(DATOS_DE_AVISOS);
    const prefijo = new URL(`__recordar/${tipo}/`, self.registration.scope).pathname;
    for (const peticion of await cache.keys()) {
      if (!new URL(peticion.url).pathname.startsWith(prefijo)) continue;
      const respuesta = await cache.match(peticion);
      const dato = respuesta ? await respuesta.json().catch(() => null) : null;
      if (dato && typeof dato.id === 'string') datos.push(dato);
    }
  } catch (error) { /* Sin poder leerla: como si no hubiera nada. */ }
  return datos;
}

/** ¿Enseña este navegador botones en los avisos? (iPhone, iPad y Firefox no: allí se contesta en la app.) */
const conBotones = () => typeof Notification !== 'undefined' && 'maxActions' in Notification && Notification.maxActions >= 2;

/**
 * «Hecho»: se le dice a la función de avisos (deja de avisar de esa cosa, también en otros dispositivos). Queda
 * apuntado hasta que ella lo sabe. Devuelve si ya no queda nada que repetir.
 */
async function decirHecho(datos) {
  await apuntar('por-decir', datos.id, { id: datos.id, uid: datos.uid, ficha: datos.ficha });
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), ESPERA_HECHO);
  try {
    const respuesta = await fetch(new URL('api/avisos/hecho', self.registration.scope).href, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uid: datos.uid, id: datos.id, ficha: datos.ficha }),
      signal: control.signal,
    });
    // Un fallo de la función (o que pida calma) se repite; lo demás está dicho, o rechazado para siempre (la ficha ya no vale).
    if (respuesta.status >= 500 || respuesta.status === 429) return false;
    await olvidar('por-decir', datos.id);
    return true;
  } catch (error) {
    return false; // Sin conexión: se queda apuntado.
  } finally {
    clearTimeout(temporizador);
  }
}

/** Repite los «Hecho» que la función aún no sabe (a la vez: el sistema da poco tiempo). Devuelve si no queda ninguno. */
async function decirLoPendiente(pendientes) {
  const dichos = await Promise.all(pendientes.map((dato) => decirHecho(dato)));
  return dichos.every(Boolean);
}

/** Sin conexión, el «Hecho» se repetirá cuando vuelva (donde el navegador sabe avisar de eso) y, en todo caso, con el próximo aviso. */
async function repetirAlVolverLaConexion() {
  try {
    if (self.registration.sync) await self.registration.sync.register(HECHO_PENDIENTE);
  } catch (error) { /* Sin permiso o sin soporte: con el próximo aviso. */ }
}

/** El aviso de una cosa: título «Recordar» y, de texto, la cosa (lo que se despliega al abrirlo), con «Hecho» y «Mantener». */
async function ensenarRecordatorio(cosa, uid, suena) {
  const etiqueta = `recordar-${cosa.id}`;
  try {
    // Safari no sustituye un aviso por otro con la misma etiqueta: el de la vez anterior se quita a mano.
    for (const anterior of await self.registration.getNotifications({ tag: etiqueta })) anterior.close();
  } catch (error) { /* Sin poder leerlos: el sistema los sustituye donde sabe. */ }
  const botones = conBotones();
  await self.registration.showNotification('Recordar', {
    body: botones ? cosa.texto : `${cosa.texto}\nToca para marcarla como hecha o mantenerla.`,
    tag: etiqueta,
    renotify: suena, // Aunque sustituya al de la vez anterior, suena y se ve otra vez.
    silent: !suena, // De varias cosas a la vez solo suena la primera (y sonar y callar a la vez no se admite en todas partes).
    requireInteraction: true, // En el ordenador no se va solo: espera la respuesta.
    icon: 'icons/icon-192.png',
    badge: 'icons/aviso-96.png',
    lang: 'es',
    actions: botones ? [{ action: 'hecho', title: 'Hecho' }, { action: 'mantener', title: 'Mantener' }] : [],
    data: { tipo: 'recordar', id: cosa.id, uid, ficha: texto(cosa.ficha, ''), url: `./?recordada=${encodeURIComponent(cosa.id)}#grupo/recordar` },
  });
  // Después de enseñarlo (lo primero es lo primero): queda apuntado que falta contestarlo.
  await apuntar('avisadas', cosa.id, { id: cosa.id, en: Date.now() });
}

/**
 * Los recordatorios que tocan ahora: { tipo: 'recordar', uid, cosas: [{ id, texto, ficha }] } (lo demás que trae es
 * para un service worker de antes de Recordar). Un aviso por cosa, cada uno con su etiqueta. La función de avisos
 * (netlify/functions) los manda cada vez que toca, hasta que se le dice «Hecho».
 */
async function ensenarRecordatorios(aviso) {
  const cosas = aviso.cosas.filter((cosa) => cosa && typeof cosa === 'object' && texto(cosa.id, '') && texto(cosa.texto, ''));
  // Los «Hecho» que la función aún no sabe se le repiten ya (sin esperarla para enseñar nada)…
  const pendientes = await apuntados('por-decir');
  const diciendo = decirLoPendiente(pendientes);
  // …y de esas cosas no se vuelve a avisar: ya están contestadas. Salvo que no quede otra: cada envío tiene que enseñar algo.
  const contestadas = new Set(pendientes.map((dato) => dato.id));
  const sinContestar = cosas.filter((cosa) => !contestadas.has(cosa.id));
  const visibles = sinContestar.length > 0 ? sinContestar : cosas.slice(0, 1);
  for (const [indice, cosa] of visibles.entries()) await ensenarRecordatorio(cosa, texto(aviso.uid, ''), indice === 0);
  // Sin ninguna cosa que se entienda, lo que el envío trae para cualquier aviso (título, línea y enlace).
  if (visibles.length === 0) await ensenarAviso(aviso);
  await diciendo;
}

/** Avisa a las ventanas de la app que haya abiertas. */
async function decirALaApp(mensaje) {
  const ventanas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const ventana of ventanas) ventana.postMessage(mensaje);
}

/** Las ventanas en las que está abierta la app (no las de Cosas con, Cosas de o Tu cuenta, que son de este dominio). */
async function ventanasDeLaApp() {
  const raiz = new URL(self.registration.scope).pathname;
  const ventanas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  return ventanas.filter((ventana) => {
    const { origin, pathname } = new URL(ventana.url);
    return origin === self.location.origin && (pathname === raiz || pathname === `${raiz}index.html`);
  });
}

/**
 * Tocar el aviso en sí es querer contestarlo en la app, que pregunta «Hecho» o «Mantener». Si ya está abierta se le
 * pide sin recargarla ni cambiarla de sitio (lo que se estuviera escribiendo no se pierde); si no, se abre.
 */
async function preguntarEnLaApp(datos) {
  const ventanas = await ventanasDeLaApp();
  if (ventanas.length === 0) return self.clients.openWindow(new URL(texto(datos.url, './'), self.registration.scope).href);
  const ventana = ventanas.find((candidata) => candidata.focused) || ventanas.find((candidata) => candidata.visibilityState === 'visible') || ventanas[0];
  try {
    await ventana.focus();
  } catch (error) { /* No se deja traer al frente: al menos, que pregunte. */ }
  return ventana.postMessage({ tipo: 'recordar-abrir', id: datos.id });
}

/**
 * La respuesta a un recordatorio. «Hecho»: la cosa queda apuntada como hecha para la app (que marca su check: al
 * momento si está abierta, o al abrirse) y la función de avisos deja de avisar de ella. «Mantener»: nada que hacer,
 * sigue con su recordatorio y se avisará la próxima vez que toque.
 */
async function responderRecordatorio(accion, datos) {
  if (accion === 'hecho') {
    await apuntar('hechas', datos.id, { id: datos.id });
    await olvidar('avisadas', datos.id);
    const [, dicho] = await Promise.all([decirALaApp({ tipo: 'recordar-hecho', id: datos.id }).catch(() => {}), decirHecho(datos)]);
    if (!dicho) await repetirAlVolverLaConexion();
    return;
  }
  if (accion === 'mantener') {
    await olvidar('avisadas', datos.id);
    return;
  }
  await preguntarEnLaApp(datos);
}

// De uno en uno: dos avisos seguidos de la misma lista no se pisan al juntarse.
let turnoDeAvisos = Promise.resolve();

self.addEventListener('push', (evento) => {
  let aviso = {};
  try {
    aviso = (evento.data && evento.data.json()) || {};
  } catch (error) { /* Sin datos legibles: un aviso genérico. */ }
  const sonRecordatorios = aviso.tipo === 'recordar' && Array.isArray(aviso.cosas);
  turnoDeAvisos = turnoDeAvisos.catch(() => {}).then(() => (sonRecordatorios ? ensenarRecordatorios(aviso) : ensenarAviso(aviso)));
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
  if (datos.tipo === 'recordar' && typeof datos.id === 'string') {
    evento.waitUntil(responderRecordatorio(evento.action, datos));
    return;
  }
  evento.waitUntil(abrirDesdeAviso(texto(datos.url, './')));
});

/** Quitar de en medio un recordatorio sin contestarlo es mantenerlo: ya no hay que preguntar por él en la app. */
self.addEventListener('notificationclose', (evento) => {
  const datos = evento.notification.data || {};
  if (datos.tipo === 'recordar' && typeof datos.id === 'string') evento.waitUntil(olvidar('avisadas', datos.id));
});

/** Ha vuelto la conexión: los «Hecho» que quedaron sin decir. Si alguno sigue sin poder decirse, el navegador volverá a intentarlo. */
self.addEventListener('sync', (evento) => {
  if (evento.tag !== HECHO_PENDIENTE) return;
  evento.waitUntil(apuntados('por-decir').then(decirLoPendiente).then((dicho) => {
    if (!dicho) throw new Error('Quedan «Hecho» por decir');
  }));
});
