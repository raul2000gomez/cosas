/* Cosas · avisos: notificaciones cuando alguien añade una cosa a una lista de Cosas con o de Cosas de, y los
   recordatorios de las cosas del grupo Recordar de la app.

   Una función de Netlify (https://cosas-app.netlify.app/api/avisos/…), sin servidor propio ni claves que
   guardar a mano:
     GET  clave     → la clave pública (VAPID) con la que cada dispositivo se suscribe.
     POST alta      → este dispositivo avisa a esta sesión.                            { suscripcion }
     POST baja      → deja de avisarla.                                                { endpoint }
     POST avisar    → acabo de añadir esta cosa: avisa a la demás gente de la lista.    { lista, cosa }
     GET  recordar  → los recordatorios de esta sesión.                                → { nacido, v, zona, general, cosas }
     POST recordar  → lo que cambia en ellos, si la app los ha visto en su última versión.
                      { siV, zona, general?, poner?, quitar? }                         → { nacido, v, rechazadas }
     POST hecho     → «Hecho» en el aviso de una cosa (lo llama el service worker).     { uid, id, ficha }
     POST olvidar   → borra los recordatorios de esta sesión.

   Quien llama se identifica con su sesión de Firebase (la de Cosas con y Cosas de, anónima o de Google):
   «Authorization: Bearer <token de Firebase>», comprobado con las claves públicas de Google. Para avisar,
   la lista y la cosa se leen de Firestore con ese mismo token, así que mandan las reglas: solo quien está
   en la lista puede leer sus cosas. Y solo se avisa de una cosa suya, recién añadida, y una sola vez.

   Los recordatorios son de cada sesión: la app guarda aquí los de las cosas de su grupo Recordar (qué cosa, a qué
   hora y cada cuánto) y la función programada (recordatorios.mjs) avisa de cada una a su hora, a los dispositivos de
   esa sesión. Desde aquí no sale ningún recordatorio: guardar solo deja la sesión en la agenda de la programada.
   - «recordar» (POST) es un cambio, no el documento entero: «poner» ({ id: { texto, hecha, r } }) añade o sustituye
     esas cosas, «quitar» ([id]) las quita y «general» solo viaja si cambia (null lo quita); lo que no se nombra no
     se toca. Si «siV» no es la versión que hay, 409 con lo mismo que da el GET, y no cambia nada. Una cosa que no
     vale (o que no cabe: hasta 100 por sesión) no tumba el envío: su id vuelve en «rechazadas». «v» solo sube si
     cambia algo, así que repetir un envío no hace daño. «nacido» distingue un documento del que se creara después
     de borrarlo.
   - «hecho» no lleva sesión (el service worker no la tiene): lleva la ficha que venía en el aviso. Como puede
     llamarlo cualquiera, lo primero que se mira es lo que ocupa (más de 1000 bytes, 413); y todo lo que no sea una
     ficha buena es un 403, sin más pistas (no dice si esa sesión o esa cosa existen).
   - «olvidar» borra el documento de la sesión y nada más (la app lo pide al desactivar las notificaciones en un
     dispositivo sin cuenta de Google). Una sesión que se queda sin dispositivos a los que avisar se pone sola en
     pausa; «alta», y leer o guardar los recordatorios, la despiertan.
   - Nada de esto se guarda para siempre: el texto de una cosa se va en cuanto la app la quita («quitar»), y el
     documento entero, a los treinta días de seguir en pausa o de no esperar ningún aviso sin que nadie lo toque (lo
     borra la función programada). Si su dueño vuelve, GET dice que ya no hay documento («nacido» null) y la app lo
     sube de nuevo.

   Lo guardado vive en Netlify Blobs (almacén «avisos»; ver también ../lib/envio.mjs y ../lib/recordatorios.mjs):
     enviados/{lista}              lo último ya avisado de esa lista: { idCosa: cuándo }. */

import { createRemoteJWKSet, jwtVerify } from 'jose';
import { SERVICIOS_PUSH, almacen, clavesVapid, enviarA, fichaValida, huella, limpiar, olvidar } from '../lib/envio.mjs';
import { envioDe, esIdDeCosa } from '../lib/recordar.mjs';
import { despertar, guardarRecordatorios, leerRecordatorios, marcarHecha, olvidarRecordatorios } from '../lib/recordatorios.mjs';

export const config = { path: '/api/avisos/:accion' };

const PROYECTO = 'cosas-info';
const FIRESTORE = `https://firestore.googleapis.com/v1/projects/${PROYECTO}/databases/(default)/documents`;
const CLAVES_DE_GOOGLE = createRemoteJWKSet(new URL('https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com'));
const ID = /^[A-Za-z0-9]{1,40}$/;
const esId = (v) => typeof v === 'string' && ID.test(v);
const RECIENTE = 15 * 60 * 1000; // Una cosa se avisa si se añadió hace menos de esto.
const MAX_DISPOSITIVOS = 20;
const LINEA_MAX = 120;
// Lo que puede ocupar lo que se envía, en bytes.
const MAX_CUERPO = 5000;
const MAX_CUERPO_RECORDAR = 100000; // Hasta 100 cosas con su texto.
const MAX_CUERPO_HECHO = 1000; // Una sesión, un id y una ficha: la única llamada que no pide sesión, la más corta.
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

/** Lo enviado, como objeto. Si dice lo que ocupa y es demasiado, ni se lee; y leído, se mide de verdad. */
async function cuerpo(peticion, maximo = MAX_CUERPO) {
  if (Number(peticion.headers.get('content-length')) > maximo) throw new Rechazo(413, 'demasiado-grande');
  const texto = await peticion.text();
  if (Buffer.byteLength(texto) > maximo) throw new Rechazo(413, 'demasiado-grande');
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
  // Si sus recordatorios estaban en pausa por no tener a quién avisar, ya lo tienen. Si esto falla, el alta vale igual.
  await despertar(store, uid).catch((error) => console.warn('Al despertar los recordatorios', error));
  return respuesta(200, { ok: true });
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

// ---------- Recordatorios ----------

async function leerRecordar(peticion) {
  const { uid } = await quienLlama(peticion);
  return respuesta(200, await leerRecordatorios(almacen(), uid));
}

async function guardarRecordar(peticion) {
  const { uid } = await quienLlama(peticion);
  const envio = envioDe(await cuerpo(peticion, MAX_CUERPO_RECORDAR));
  if (!envio) throw new Rechazo(400, 'recordatorios');
  const resultado = await guardarRecordatorios(almacen(), uid, envio, Date.now());
  // 409: la app escribía sobre una versión que ya no es la que hay; lleva lo que hay ahora para que vuelva a mirar.
  if (resultado.conflicto) return respuesta(409, resultado.conflicto);
  return respuesta(200, resultado);
}

/**
 * Lo llama el service worker al pulsar «Hecho» en un aviso: sin sesión, con la ficha que venía en ese aviso. Como
 * puede llamarlo cualquiera, lo primero es el tamaño; y repetirlo no cambia nada.
 */
async function hecho(peticion) {
  const { uid, id, ficha } = await cuerpo(peticion, MAX_CUERPO_HECHO);
  const store = almacen();
  if (!esId(uid) || !esIdDeCosa(id) || !(await fichaValida(store, uid, id, ficha))) throw new Rechazo(403, 'ficha');
  await marcarHecha(store, uid, id);
  return respuesta(200, { ok: true });
}

async function olvidarRecordar(peticion) {
  const { uid } = await quienLlama(peticion);
  await olvidarRecordatorios(almacen(), uid);
  return respuesta(200, { ok: true });
}

const ACCIONES = {
  clave: { GET: clave },
  alta: { POST: alta },
  baja: { POST: baja },
  avisar: { POST: avisar },
  recordar: { GET: leerRecordar, POST: guardarRecordar },
  hecho: { POST: hecho },
  olvidar: { POST: olvidarRecordar },
};

export default async function (peticion, contexto) {
  if (peticion.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
  const nombre = contexto.params && contexto.params.accion;
  const accion = Object.hasOwn(ACCIONES, nombre) ? ACCIONES[nombre] : null;
  if (!accion) return respuesta(404, { error: 'no-existe' });
  if (!Object.hasOwn(accion, peticion.method)) return respuesta(405, { error: 'metodo' });
  try {
    return await accion[peticion.method](peticion);
  } catch (error) {
    if (error instanceof Rechazo) return respuesta(error.estado, { error: error.message });
    console.error(error);
    return respuesta(500, { error: 'interno' });
  }
}
