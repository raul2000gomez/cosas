/* Cosas · avisos: notificaciones cuando alguien añade una cosa a una lista de Cosas con o de Cosas de.

   Una función de Netlify (https://cosas-app.netlify.app/api/avisos/…), sin servidor propio ni claves que
   guardar a mano:
     GET  clave   → la clave pública (VAPID) con la que cada dispositivo se suscribe.
     POST alta    → este dispositivo avisa a esta sesión.                          { suscripcion }
     POST baja    → deja de avisarla.                                              { endpoint }
     POST avisar  → acabo de añadir esta cosa: avisa a la demás gente de la lista.  { lista, cosa }

   Quien llama se identifica con su sesión de Firebase (la de Cosas con y Cosas de, anónima o de Google):
   «Authorization: Bearer <token de Firebase>», comprobado con las claves públicas de Google. Para avisar,
   la lista y la cosa se leen de Firestore con ese mismo token, así que mandan las reglas: solo quien está
   en la lista puede leer sus cosas. Y solo se avisa de una cosa suya, recién añadida, y una sola vez.

   Lo guardado vive en Netlify Blobs (almacén «avisos»):
     vapid                         { publicKey, privateKey }: se crean solas la primera vez.
     dispositivos/{uid}/{huella}   la suscripción push de uno de los dispositivos de esa persona.
     suscripciones/{huella}        de quién es ahora ese dispositivo (avisa a una sola sesión).
     enviados/{lista}              lo último ya avisado de esa lista: { idCosa: cuándo }. */

import { createHash } from 'node:crypto';
import { getStore } from '@netlify/blobs';
import { createRemoteJWKSet, jwtVerify } from 'jose';
import webpush from 'web-push';

export const config = { path: '/api/avisos/:accion' };

const PROYECTO = 'cosas-info';
const FIRESTORE = `https://firestore.googleapis.com/v1/projects/${PROYECTO}/databases/(default)/documents`;
const CLAVES_DE_GOOGLE = createRemoteJWKSet(new URL('https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com'));
// Solo se envía a los servicios push de los navegadores (Chrome y Android, Safari, Firefox, Edge): a ninguna otra dirección.
const SERVICIOS_PUSH = ['fcm.googleapis.com', 'android.googleapis.com', 'push.apple.com', 'push.services.mozilla.com', 'notify.windows.com'];
const ID = /^[A-Za-z0-9]{1,40}$/;
const esId = (v) => typeof v === 'string' && ID.test(v);
const RECIENTE = 15 * 60 * 1000; // Una cosa se avisa si se añadió hace menos de esto.
const MAX_DISPOSITIVOS = 20;
const LINEA_MAX = 120;
const CORS = {
  'Access-Control-Allow-Origin': '*', // Sin cookies: lo que identifica es el token, y cosas.info llama desde otro dominio.
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Max-Age': '86400',
};

class Rechazo extends Error {
  constructor(estado, motivo) {
    super(motivo);
    this.estado = estado;
  }
}

const respuesta = (estado, datos) => new Response(JSON.stringify(datos), {
  status: estado,
  headers: { ...CORS, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
});

const almacen = () => getStore({ name: 'avisos', consistency: 'strong' });
const huella = (endpoint) => createHash('sha256').update(endpoint).digest('hex').slice(0, 40);

function limpiar(texto, max = 40) {
  if (typeof texto !== 'string') return '';
  const t = texto.replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
}

async function cuerpo(peticion) {
  const texto = await peticion.text();
  if (texto.length > 5000) throw new Rechazo(413, 'demasiado-grande');
  try {
    const datos = JSON.parse(texto);
    if (datos && typeof datos === 'object') return datos;
  } catch (error) { /* Abajo. */ }
  throw new Rechazo(400, 'json');
}

/** Quién llama: su uid y su token, si el token de Firebase es bueno (firmado por Google, de este proyecto, vigente). */
async function quienLlama(peticion) {
  const cabecera = peticion.headers.get('authorization') || '';
  const token = cabecera.startsWith('Bearer ') ? cabecera.slice(7).trim() : '';
  if (!token) throw new Rechazo(401, 'sin-sesion');
  try {
    const { payload } = await jwtVerify(token, CLAVES_DE_GOOGLE, { issuer: `https://securetoken.google.com/${PROYECTO}`, audience: PROYECTO });
    if (!esId(payload.sub)) throw new Error('uid');
    return { uid: payload.sub, token };
  } catch (error) {
    throw new Rechazo(401, 'sesion-no-valida');
  }
}

// ---------- Firestore (como quien llama) ----------

/** Un valor de la API REST de Firestore, como JavaScript normal (las fechas, en milisegundos). */
function valorDe(v) {
  if (!v || typeof v !== 'object') return null;
  if ('stringValue' in v) return v.stringValue;
  if ('booleanValue' in v) return v.booleanValue;
  if ('integerValue' in v) return Number(v.integerValue);
  if ('doubleValue' in v) return v.doubleValue;
  if ('timestampValue' in v) return Date.parse(v.timestampValue);
  if ('arrayValue' in v) return (v.arrayValue.values || []).map(valorDe);
  if ('mapValue' in v) return Object.fromEntries(Object.entries(v.mapValue.fields || {}).map(([k, x]) => [k, valorDe(x)]));
  return null;
}

/** Un documento, leído con el token de quien llama (con sus reglas); null si no existe o no puede verlo. */
async function leer(ruta, token) {
  const r = await fetch(`${FIRESTORE}/${ruta}`, { headers: { Authorization: `Bearer ${token}` } });
  if (r.status === 403 || r.status === 404) return null;
  if (!r.ok) throw new Error(`Firestore respondió ${r.status} al leer ${ruta}`);
  return valorDe({ mapValue: { fields: (await r.json()).fields || {} } });
}

// ---------- Claves VAPID ----------

let vapid = null;

/** Las del almacén; la primera vez se crean (si dos llamadas lo intentan a la vez, se quedan las que llegaron antes). */
async function clavesVapid(store) {
  if (vapid) return vapid;
  let claves = await store.get('vapid', { type: 'json' });
  if (!claves) {
    await store.setJSON('vapid', webpush.generateVAPIDKeys(), { onlyIfNew: true });
    claves = await store.get('vapid', { type: 'json' });
  }
  vapid = claves;
  return claves;
}

// ---------- Acciones ----------

async function clave() {
  const { publicKey } = await clavesVapid(almacen());
  return respuesta(200, { clave: publicKey });
}

function suscripcionValida(s) {
  let url = null;
  try { url = s && typeof s.endpoint === 'string' ? new URL(s.endpoint) : null; } catch (error) { /* Abajo. */ }
  const conocido = url && url.protocol === 'https:' && SERVICIOS_PUSH.some((d) => url.hostname === d || url.hostname.endsWith(`.${d}`));
  const claves = s && s.keys;
  if (!conocido || s.endpoint.length > 1000 || !claves || typeof claves.p256dh !== 'string' || typeof claves.auth !== 'string'
      || claves.p256dh.length > 200 || claves.auth.length > 100) {
    throw new Rechazo(400, 'suscripcion');
  }
  return { endpoint: s.endpoint, keys: { p256dh: claves.p256dh, auth: claves.auth } };
}

async function alta(peticion) {
  const { uid } = await quienLlama(peticion);
  const suscripcion = suscripcionValida((await cuerpo(peticion)).suscripcion);
  const store = almacen();
  const h = huella(suscripcion.endpoint);
  // Un dispositivo avisa a una sola sesión: si antes era de otra (se entró con otra cuenta), deja de avisarla.
  const antes = await store.get(`suscripciones/${h}`);
  if (antes && antes !== uid) await store.delete(`dispositivos/${antes}/${h}`);
  const { blobs } = await store.list({ prefix: `dispositivos/${uid}/` });
  if (blobs.length >= MAX_DISPOSITIVOS && !blobs.some((b) => b.key.endsWith(`/${h}`))) throw new Rechazo(429, 'demasiados-dispositivos');
  await store.setJSON(`dispositivos/${uid}/${h}`, { ...suscripcion, desde: Date.now() });
  await store.set(`suscripciones/${h}`, uid);
  return respuesta(200, { ok: true });
}

async function olvidar(store, uid, h) {
  await store.delete(`dispositivos/${uid}/${h}`);
  if ((await store.get(`suscripciones/${h}`)) === uid) await store.delete(`suscripciones/${h}`);
}

async function baja(peticion) {
  const { uid } = await quienLlama(peticion);
  const { endpoint } = await cuerpo(peticion);
  if (typeof endpoint !== 'string' || !endpoint || endpoint.length > 1000) throw new Rechazo(400, 'endpoint');
  await olvidar(almacen(), uid, huella(endpoint));
  return respuesta(200, { ok: true });
}

/** Apunta que esa cosa ya se ha avisado; false si ya lo estaba (una llamada repetida no avisa dos veces). */
async function marcarAvisada(store, lista, cosa) {
  for (let intento = 0; intento < 5; intento += 1) {
    const actual = await store.getWithMetadata(`enviados/${lista}`, { type: 'json' });
    const ahora = Date.now();
    // Basta con recordar lo reciente: lo de antes ya no se avisaría (su «creada» queda lejos).
    const hechas = Object.fromEntries(Object.entries((actual && actual.data) || {}).filter(([, cuando]) => ahora - cuando < 3 * RECIENTE));
    if (hechas[cosa]) return false;
    hechas[cosa] = ahora;
    const { modified } = await store.setJSON(`enviados/${lista}`, hechas, actual ? { onlyIfMatch: actual.etag } : { onlyIfNew: true });
    if (modified) return true;
  }
  throw new Error('No se pudo apuntar el aviso');
}

async function enviarA(store, claves, uid, aviso) {
  const { blobs } = await store.list({ prefix: `dispositivos/${uid}/` });
  const resultados = await Promise.all(blobs.map(async ({ key }) => {
    const suscripcion = await store.get(key, { type: 'json' });
    if (!suscripcion) return 0;
    try {
      await webpush.sendNotification(suscripcion, aviso, {
        vapidDetails: { subject: 'https://cosas.info', publicKey: claves.publicKey, privateKey: claves.privateKey },
        TTL: 24 * 60 * 60,
        urgency: 'normal',
        timeout: 8000,
      });
      return 1;
    } catch (error) {
      // 404 y 410: esa suscripción ya no existe (se desactivaron o se borró la app): se olvida.
      if (error && (error.statusCode === 404 || error.statusCode === 410)) await olvidar(store, uid, key.split('/').pop());
      else console.warn('No se pudo enviar un aviso', error && error.statusCode, error && error.body);
      return 0;
    }
  }));
  return resultados.reduce((a, b) => a + b, 0);
}

async function avisar(peticion) {
  const { uid, token } = await quienLlama(peticion);
  const { lista: id, cosa: cid } = await cuerpo(peticion);
  if (!esId(id) || !esId(cid)) throw new Rechazo(400, 'ids');
  const [lista, cosa] = await Promise.all([leer(`listas/${id}`, token), leer(`listas/${id}/cosas/${cid}`, token)]);
  if (!lista || !cosa || !Array.isArray(lista.uids) || !lista.uids.includes(uid)) throw new Rechazo(404, 'no-existe');
  if (cosa.por !== uid) throw new Rechazo(403, 'no-es-tuya');
  if (!(Math.abs(Date.now() - cosa.creada) < RECIENTE)) throw new Rechazo(409, 'no-es-reciente');
  const destino = lista.uids.filter((u) => u !== uid && esId(u));
  if (!destino.length) return respuesta(200, { enviados: 0 });
  const store = almacen();
  if (!(await marcarAvisada(store, id, cid))) return respuesta(200, { enviados: 0, repetido: true });

  const tipo = lista.tipo === 'de' ? 'de' : 'con';
  const grupo = esId(cosa.grupo) ? await leer(`listas/${id}/grupos/${cosa.grupo}`, token) : null;
  const autor = limpiar(lista.miembros && lista.miembros[uid] && lista.miembros[uid].nombre) || 'Alguien';
  const donde = grupo && limpiar(grupo.nombre) ? ` en ${limpiar(grupo.nombre)}` : '';
  const aviso = JSON.stringify({
    // Como el título de la lista en Cosas con y Cosas de: en «Cosas con», el nombre de la otra persona.
    titulo: tipo === 'con' ? `Cosas con ${autor}` : `Cosas de ${limpiar(lista.nombre) || '…'}`,
    linea: `${autor} ha añadido «${limpiar(cosa.texto, LINEA_MAX)}»${donde}`,
    url: `/${tipo}/${id}${grupo ? `#grupo/${cosa.grupo}` : ''}`,
    etiqueta: `lista-${id}`,
  });
  const claves = await clavesVapid(store);
  const enviados = (await Promise.all(destino.map((u) => enviarA(store, claves, u, aviso)))).reduce((a, b) => a + b, 0);
  return respuesta(200, { enviados });
}

const ACCIONES = { clave: ['GET', clave], alta: ['POST', alta], baja: ['POST', baja], avisar: ['POST', avisar] };

export default async function (peticion, contexto) {
  if (peticion.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
  const accion = ACCIONES[contexto.params && contexto.params.accion];
  if (!accion) return respuesta(404, { error: 'no-existe' });
  if (peticion.method !== accion[0]) return respuesta(405, { error: 'metodo' });
  try {
    return await accion[1](peticion);
  } catch (error) {
    if (error instanceof Rechazo) return respuesta(error.estado, { error: error.message });
    console.error(error);
    return respuesta(500, { error: 'interno' });
  }
}
