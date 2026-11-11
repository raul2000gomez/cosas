/* Cosas · avisos: lo que comparten la función de avisos (netlify/functions/avisos.mjs) y la de los recordatorios
   (netlify/functions/recordatorios.mjs): el almacén, las claves y el envío a los dispositivos de una sesión.

   Todo vive en Netlify Blobs (almacén «avisos»), sin nada que configurar ni secretos en el repositorio:
     vapid                         { publicKey, privateKey }: se crean solas la primera vez.
     secreto                       la clave con la que se firman las fichas de «Hecho» (también se crea sola).
     dispositivos/{uid}/{huella}   la suscripción push de uno de los dispositivos de esa sesión.
     suscripciones/{huella}        de quién es ahora ese dispositivo (avisa a una sola sesión). */

import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { getStore } from '@netlify/blobs';
import webpush from 'web-push';
import { CADUCA } from './recordar.mjs';

const TEXTO_MAX = 300;
const LINEA_MAX = 120;
// Lo que ocupa, como mucho, un mensaje push con recordatorios (en bytes de UTF-8): cifrado no admite mucho más de 4 KB.
const MAX_BYTES = 3000;
// Dispositivos de una sesión a los que se envían sus recordatorios: quien dé de alta muchos no multiplica los envíos.
const MAX_DESTINOS = 10;

// Solo se envía a los servicios push de los navegadores (Chrome y Android, Safari, Firefox, Edge): a ninguna otra dirección.
export const SERVICIOS_PUSH = ['fcm.googleapis.com', 'android.googleapis.com', 'push.apple.com', 'push.services.mozilla.com', 'notify.windows.com'];

export const almacen = () => getStore({ name: 'avisos', consistency: 'strong' });
export const huella = (endpoint) => createHash('sha256').update(endpoint).digest('hex').slice(0, 40);

/** Un texto en una línea y sin pasar de «max» caracteres (con «…» si se corta). */
export function limpiar(texto, max = 40) {
  if (typeof texto !== 'string') return '';
  const t = texto.replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
}

let vapid = null;

/** Las del almacén; la primera vez se crean (si dos llamadas lo intentan a la vez, se quedan las que llegaron antes). */
export async function clavesVapid(store) {
  if (vapid) return vapid;
  let claves = await store.get('vapid', { type: 'json' });
  if (!claves) {
    await store.setJSON('vapid', webpush.generateVAPIDKeys(), { onlyIfNew: true });
    claves = await store.get('vapid', { type: 'json' });
  }
  vapid = claves;
  return claves;
}

let secreto = null;

/** La clave de las fichas: como las VAPID, se crea sola la primera vez y no sale del almacén. */
export async function secretoDe(store) {
  if (secreto) return secreto;
  let valor = await store.get('secreto');
  if (!valor) {
    await store.set('secreto', randomBytes(32).toString('base64url'), { onlyIfNew: true });
    valor = await store.get('secreto');
  }
  secreto = valor;
  return valor;
}

/**
 * La ficha de «Hecho» de una cosa: va dentro de su aviso (cifrado para ese dispositivo) y es lo que el service worker
 * enseña a la función al pulsarlo, sin sesión que valga a esas horas. Solo sirve para marcar hecha esa cosa de esa sesión.
 */
export const fichaCon = (clave, uid, id) => createHmac('sha256', clave).update(`${uid}\n${id}`).digest('base64url');

export async function fichaValida(store, uid, id, ficha) {
  if (typeof ficha !== 'string' || ficha.length > 100) return false;
  const debida = Buffer.from(fichaCon(await secretoDe(store), uid, id));
  const dada = Buffer.from(ficha);
  return dada.length === debida.length && timingSafeEqual(dada, debida);
}

/**
 * Lo que hace falta para enviar recordatorios: { claves, secreto }. La función programada lo pide lo primero, antes
 * de tomar ningún aviso: si el almacén no responde, lanza y la ejecución acaba sin haber tocado nada.
 */
export async function llavesDe(store) {
  const [claves, clave] = await Promise.all([clavesVapid(store), secretoDe(store)]);
  if (!claves || !claves.publicKey || !claves.privateKey || !clave) throw new Error('Faltan las claves de los avisos');
  return { claves, secreto: clave };
}

export async function olvidar(store, uid, h) {
  await store.delete(`dispositivos/${uid}/${h}`);
  if ((await store.get(`suscripciones/${h}`)) === uid) await store.delete(`suscripciones/${h}`);
}

/** Envía ese aviso (el de una cosa nueva en una lista) a todos los dispositivos de esa sesión y devuelve a cuántos ha llegado. */
export async function enviarA(store, claves, uid, aviso) {
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

// ---------- Recordatorios ----------

/**
 * Los dispositivos de esa sesión a los que se envían sus recordatorios: [{ huella, suscripcion }], del último que se
 * dio de alta al primero y, como mucho, «maximo». Si el almacén falla, lanza: que no parezca que no tiene ninguno
 * (a una sesión sin dispositivos se la pone en pausa).
 */
export async function dispositivosDe(store, uid, maximo = MAX_DESTINOS) {
  const { blobs } = await store.list({ prefix: `dispositivos/${uid}/` });
  const leidos = await Promise.all(blobs.map(async ({ key }) => ({ huella: key.split('/').pop(), suscripcion: await store.get(key, { type: 'json' }) })));
  return leidos
    .filter(({ suscripcion }) => suscripcion && typeof suscripcion === 'object' && typeof suscripcion.endpoint === 'string')
    .sort((a, b) => (Number(b.suscripcion.desde) || 0) - (Number(a.suscripcion.desde) || 0))
    .slice(0, maximo);
}

/**
 * El texto de una cosa para su aviso: en una línea, sin caracteres de control ni mitades sueltas de un carácter (en
 * JSON ocuparían seis bytes cada uno) y sin pasar de «max» (con «…» si se corta, sin partir un emoji).
 */
function enUnaLinea(texto, max) {
  const t = String(texto).toWellFormed().replace(/[\s\u0000-\u001f\u007f-\u009f]+/g, ' ').trim();
  if (t.length <= max) return t;
  const corte = /[\ud800-\udbff]/.test(t[max - 2]) ? max - 2 : max - 1;
  return `${t.slice(0, corte).trimEnd()}…`;
}

/**
 * Un mensaje push con los recordatorios de esas cosas ([{ id, texto }]), como lo recibe el service worker
 * (app/sw.js), que enseña un aviso por cosa: cada una con su texto y con la ficha con la que su «Hecho» la marca.
 * «titulo», «linea», «url» y «etiqueta» son para un service worker de antes (un dispositivo que aún no se ha
 * actualizado): lo enseña como un aviso más, sin botones, y al tocarlo abre Recordar. «apretar» acorta los textos
 * (a la mitad, a la cuarta parte…) para el mensaje que, aun con una sola cosa, no cabe.
 */
export function avisoDe(uid, cosas, clave, apretar = 1) {
  const textoDe = (cosa, max) => enUnaLinea(cosa.texto, Math.max(1, Math.floor(max / apretar)));
  const [primera] = cosas;
  const sola = cosas.length === 1;
  const mas = ` y ${cosas.length - 1} más`;
  return JSON.stringify({
    tipo: 'recordar',
    uid,
    cosas: cosas.map((cosa) => ({ id: cosa.id, texto: textoDe(cosa, TEXTO_MAX), ficha: fichaCon(clave, uid, cosa.id) })),
    titulo: 'Recordar',
    linea: sola ? textoDe(primera, LINEA_MAX) : `«${textoDe(primera, LINEA_MAX - mas.length - 2)}»${mas}`,
    url: sola ? `/?recordada=${encodeURIComponent(primera.id)}#grupo/recordar` : '/#grupo/recordar',
    etiqueta: sola ? `recordar-${primera.id}` : 'recordar',
  });
}

/**
 * Los mensajes push con los que se avisa de esas cosas ([{ id, texto, de }]): [{ cosas: [{ id, de }], aviso }].
 * Viajan juntas mientras el mensaje no pase de MAX_BYTES; las que no caben, en el siguiente. Así cien cosas a la
 * misma hora son unos pocos envíos por dispositivo, no cien.
 */
export function mensajesDe(uid, cosas, clave) {
  const tandas = [];
  for (const cosa of cosas) {
    const ultima = tandas[tandas.length - 1];
    if (ultima && Buffer.byteLength(avisoDe(uid, [...ultima, cosa], clave)) <= MAX_BYTES) ultima.push(cosa);
    else tandas.push([cosa]);
  }
  return tandas.map((tanda) => {
    let apretar = 1;
    let aviso = avisoDe(uid, tanda, clave);
    // Una sola cosa, con el id y el texto más largos que se admiten y en letras de tres bytes, se pasa por poco.
    while (Buffer.byteLength(aviso) > MAX_BYTES && apretar < TEXTO_MAX) {
      apretar *= 2;
      aviso = avisoDe(uid, tanda, clave, apretar);
    }
    return { cosas: tanda.map(({ id, de }) => ({ id, de })), aviso };
  });
}

// Con prisa (es para esa hora), sin que el servicio push lo guarde más allá de lo que tarda en caducar y sin tema:
// cada mensaje lleva cosas distintas y ninguno sustituye a otro.
const OPCIONES_DE_RECORDATORIO = { TTL: CADUCA / 1000, urgency: 'high', timeout: 5000 };

/**
 * Envía ese mensaje a un dispositivo y dice cómo ha ido: 'ok'; 'baja' (404 y 410: esa suscripción ya no existe, y se
 * olvida); 'no' (el servicio push lo rechaza y lo seguirá rechazando); 'luego' (429, 5xx, sin respuesta o sin red:
 * merece otro intento). Nunca lanza.
 */
async function enviarUno(store, claves, uid, dispositivo, aviso) {
  try {
    await webpush.sendNotification(dispositivo.suscripcion, aviso, {
      vapidDetails: { subject: 'https://cosas.info', publicKey: claves.publicKey, privateKey: claves.privateKey },
      ...OPCIONES_DE_RECORDATORIO,
    });
    return 'ok';
  } catch (error) {
    const estado = error && error.statusCode;
    if (estado === 404 || estado === 410) {
      await olvidar(store, uid, dispositivo.huella).catch((fallo) => console.warn('No se pudo olvidar un dispositivo', fallo));
      return 'baja';
    }
    console.warn('No se pudo enviar un recordatorio', estado || (error && error.message), error && error.body);
    return Number.isInteger(estado) && estado < 500 && estado !== 429 ? 'no' : 'luego';
  }
}

/**
 * Envía los recordatorios de esas cosas ([{ id, texto, de }]) a esos dispositivos de la sesión (los de
 * dispositivosDe) y devuelve las que se dan por avisadas ([{ id, de }]): quien llama confirma solo esas, y las demás
 * las repite una pasada posterior. Los mensajes van uno detrás de otro (muchos seguidos al mismo móvil, todos a la
 * vez, los puede frenar su servicio push) y cada uno, a todos los dispositivos a la vez. Un mensaje se da por
 * entregado si llega a algún dispositivo, o si ninguno de los que quedan lo va a admitir por más que se repita. Si
 * no queda ninguno (todos se han dado de baja), no: esas cosas siguen pendientes, la sesión se pondrá en pausa y, si
 * vuelve a tiempo, le llegan con retraso. «seguir()» dice si queda tiempo para otro mensaje.
 */
export async function enviarRecordatorios(store, { claves, secreto: clave }, uid, dispositivos, cosas, seguir = () => true) {
  const entregadas = [];
  let destinos = dispositivos;
  for (const mensaje of mensajesDe(uid, cosas, clave)) {
    if (!destinos.length || !seguir()) break;
    const resultados = await Promise.all(destinos.map((dispositivo) => enviarUno(store, claves, uid, dispositivo, mensaje.aviso)));
    if (resultados.includes('ok') || (resultados.includes('no') && !resultados.includes('luego'))) entregadas.push(...mensaje.cosas);
    destinos = destinos.filter((dispositivo, i) => resultados[i] !== 'baja');
  }
  return entregadas;
}
