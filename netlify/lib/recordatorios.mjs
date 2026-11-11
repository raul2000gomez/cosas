/* Cosas · recordatorios: leer, guardar y avisar, sobre el almacén (Netlify Blobs) que se le pase.

     recordar/{uid}                el documento de esa sesión (ver recordar.mjs).
     agenda/{AAAAMMDDHHmm}/{uid}   índice (sin datos): el minuto (UTC) del aviso más cercano de esa sesión (o el de cuando se puso, si ya iba con retraso).
     limpieza/{AAAAMMDD}/{uid}     índice (sin datos): el día (UTC) desde el que se puede borrar su documento, si sigue en pausa.

   La función programada (netlify/functions/recordatorios.mjs) solo lista agenda/ cada vez y atiende a quien le
   toca: no recorre los documentos de todo el mundo. Cada cambio de un documento se hace comparando su etiqueta
   (etag): si alguien lo cambió entretanto, se vuelve a leer y se repite.

   La agenda: quien guarda un documento pone siempre su clave antes de guardarlo, aunque ya estuviera, y nunca quita
   ninguna. Solo la pasada programada las quita: las que ella misma acaba de dejar atrás al guardar y, de las demás
   que sobran, las que ya son viejas (una reciente puede ser de alguien que está a punto de guardar; y que es vieja se
   lee en su nombre, porque quien guarda nunca pone una clave de un minuto que ya pasó). Si algo se corta a medias o
   dos se cruzan, sobra una clave (se mira el documento, no toca nada y se quita), nunca falta: una sesión sin su
   clave se quedaría sin avisos y nadie se enteraría. Por si acaso, cada vez que la app lee se vuelve a poner.

   Los avisos: la pasada toma los que tocan (un turno, que caduca solo), los envía y solo a los que han llegado les
   apunta el siguiente. Lo que no llega a confirmarse (falló el envío, se acabó el tiempo) lo repite una pasada
   posterior: un aviso puede llegar dos veces, nunca perderse.

   Una sesión a la que le toca un aviso y no tiene dispositivos queda en pausa: sale de la agenda y, si en un mes no
   vuelve (un alta, o la app que lee o guarda, con algún dispositivo), su documento se borra. */

import { createHash, randomBytes } from 'node:crypto';
import { diaDe, minutoDe } from './horario.mjs';
import {
  TOLERANCIA, agendaDe, confirmarAvisos, diaDeLimpieza, documentoCon, documentoConHecha, documentoDe, documentoDespierto, documentoPausado, publicoDe,
  reclamarAvisos, tocaAvisar,
} from './recordar.mjs';

const INTENTOS = 5;
const A_LA_VEZ = 6; // Sesiones que una pasada atiende a la vez.
// Una clave de la agenda que sobra no se quita hasta que su minuto queda así de lejos: nadie tarda tanto en guardar.
const VIEJA = 10 * 60 * 1000;
const MAX_QUITAR = 50; // Claves que sobran de una sesión que una pasada quita de una vez (lo normal es una; las demás, en la siguiente).
const claveDe = (uid) => `recordar/${uid}`;
const claveDeAgenda = (minuto, uid) => `agenda/${minuto}/${uid}`;
const claveDeLimpieza = (dia, uid) => `limpieza/${dia}/${uid}`;

const leer = async (store, uid) => documentoDe(await store.get(claveDe(uid), { type: 'json' }));
// Las suscripciones las guardan la función de avisos y envio.mjs (dispositivos/{uid}/{huella}): aquí solo se mira si hay.
const hayDispositivos = async (store, uid) => (await store.list({ prefix: `dispositivos/${uid}/` })).blobs.length > 0;

/**
 * Lo guardado en esa clave con su etiqueta: { data, etag }, o null si no hay nada. Nunca sin etiqueta: sin ella, la
 * escritura que viene después no compararía nada y podría pisar un cambio ajeno.
 */
async function leerConEtiqueta(store, clave) {
  const actual = await store.getWithMetadata(clave, { type: 'json' });
  if (actual && !actual.etag) throw new Error(`Sin etiqueta para ${clave}`);
  return actual;
}

/**
 * Cambia el documento de esa sesión con «cambio» (documento → documento nuevo, o null si no hay nada que cambiar).
 * Al guardarlo le pone, si no lo tiene, su «nacido», y deja puesta su clave de la agenda antes de escribir, siempre:
 * aunque el minuto no cambie (por si la clave se perdió) y aunque luego la escritura no entre (sobraría, no faltaría).
 * Devuelve { antes, despues } (despues: null si no cambió).
 */
async function cambiar(store, uid, cambio, ahora) {
  for (let intento = 0; intento < INTENTOS; intento += 1) {
    const actual = await leerConEtiqueta(store, claveDe(uid));
    const antes = documentoDe(actual && actual.data);
    const nuevo = cambio(antes);
    if (!nuevo) return { antes, despues: null };
    const despues = { ...nuevo, nacido: nuevo.nacido || randomBytes(9).toString('base64url'), agenda: agendaDe(nuevo, ahora) };
    if (despues.agenda) await store.set(claveDeAgenda(despues.agenda, uid), '1');
    const { modified } = await store.setJSON(claveDe(uid), despues, actual ? { onlyIfMatch: actual.etag } : { onlyIfNew: true });
    if (modified) return { antes, despues };
    // Alguien lo ha cambiado entretanto: se vuelve a mirar.
  }
  throw new Error(`No se pudo guardar recordar/${uid}`);
}

/**
 * Saca de la pausa el documento de esa sesión, si lo está y la sesión tiene algún dispositivo al que avisar: vuelve
 * a la agenda. Lo que le tocó mientras tanto sale con retraso en la pasada siguiente, si aún no ha caducado.
 * Devuelve si lo ha despertado.
 */
export async function despertar(store, uid, ahora = Date.now()) {
  if ((await leer(store, uid)).pausado === null || !(await hayDispositivos(store, uid))) return false;
  return (await cambiar(store, uid, documentoDespierto, ahora)).despues !== null;
}

/**
 * Lo que esa sesión comparte con la app: { nacido, v, zona, general, cosas } (sin documento: nacido null y v 0).
 * De paso, y sin que un fallo estropee la lectura: vuelve a poner su clave de la agenda (si se hubiera perdido,
 * se arregla en cuanto se abre la app) y, si estaba en pausa, intenta despertarlo.
 */
export async function leerRecordatorios(store, uid, ahora = Date.now()) {
  const doc = await leer(store, uid);
  try {
    if (doc.pausado !== null) await despertar(store, uid, ahora);
    else if (doc.agenda) await store.set(claveDeAgenda(doc.agenda, uid), '1');
  } catch (error) {
    console.warn(`Al leer los recordatorios de ${uid}`, error);
  }
  return publicoDe(doc);
}

/**
 * Guarda lo que envía la app (ya comprobado con envioDe). Devuelve { nacido, v, rechazadas } o, si la app escribía
 * sobre una versión que ya no es la que hay, { conflicto: { nacido, v, zona, general, cosas } } con lo que hay ahora.
 * No envía nada: deja el documento en la agenda y de avisar se encarga la función programada.
 */
export async function guardarRecordatorios(store, uid, envio, ahora) {
  let conflicto = null;
  let rechazadas = [];
  const { despues } = await cambiar(store, uid, (doc) => {
    const resultado = documentoCon(doc, envio, ahora);
    conflicto = resultado ? null : publicoDe(doc);
    rechazadas = resultado ? resultado.rechazadas : [];
    return resultado && resultado.doc;
  }, ahora);
  if (conflicto) return { conflicto };
  // En pausa (no tenía a quién avisar): si ahora sí, vuelve a la agenda. Si no se puede, ya lo hará otra llamada.
  if (despues.pausado !== null) await despertar(store, uid, ahora).catch((error) => console.warn(`Al despertar los recordatorios de ${uid}`, error));
  return { nacido: despues.nacido, v: despues.v, rechazadas };
}

/** «Hecho» en el aviso de esa cosa: queda hecha y no se vuelve a avisar de ella. Devuelve si ha cambiado algo. */
export async function marcarHecha(store, uid, id, ahora = Date.now()) {
  return (await cambiar(store, uid, (doc) => documentoConHecha(doc, id), ahora)).despues !== null;
}

/** Borra el documento de esa sesión. Sus claves de la agenda y de la limpieza se quedan: las quita quien las encuentre. */
export async function olvidarRecordatorios(store, uid) {
  await store.delete(claveDe(uid));
}

/**
 * Una pasada de la función programada: avisa de lo que toca a quien le toca. Lo que no depende del almacén se le
 * pasa: «dispositivos(uid)» da los dispositivos de una sesión, «enviar(uid, dispositivos, cosas)» les envía los
 * recordatorios de esas cosas ([{ id, texto, de }]) y devuelve las que han llegado ([{ id, de }]), y «seguir()» dice
 * si queda tiempo (lo que no dé tiempo se queda en la agenda para la pasada siguiente). Atiende a varias sesiones a
 * la vez y una que falla no para a las demás. Devuelve { sesiones, avisos, pausadas }: las atendidas, las cosas de
 * las que ha avisado y las que ha dejado en pausa.
 */
export async function pasada(store, ahora, { dispositivos, enviar, seguir = () => true }) {
  const corte = minutoDe(ahora + TOLERANCIA);
  const { blobs } = await store.list({ prefix: 'agenda/' });
  const vencidas = new Map(); // uid → los minutos de sus claves de la agenda que ya tocan.
  for (const { key } of blobs) {
    const [, minuto, uid, ...resto] = key.split('/');
    if (!minuto || !uid || resto.length || minuto > corte) continue;
    if (!vencidas.has(uid)) vencidas.set(uid, []);
    vencidas.get(uid).push(minuto);
  }
  // Cada pasada empieza por una sesión distinta (la que salga de revolver el minuto): si no da tiempo a todas, que
  // no sean siempre las mismas las que esperan.
  const uids = [...vencidas.keys()];
  const primera = uids.length ? createHash('sha256').update(corte).digest().readUInt32BE(0) % uids.length : 0;
  const cola = [...uids.slice(primera), ...uids.slice(0, primera)];
  const cuenta = { sesiones: 0, avisos: 0, pausadas: 0 };
  const atender = async () => {
    while (cola.length && seguir()) {
      const uid = cola.shift();
      try {
        const { avisos, pausada } = await atenderA(store, uid, vencidas.get(uid), ahora, dispositivos, enviar);
        cuenta.sesiones += 1;
        cuenta.avisos += avisos;
        cuenta.pausadas += pausada ? 1 : 0;
      } catch (error) {
        console.error(`Recordatorios de ${uid}`, error);
      }
    }
  };
  await Promise.all(Array.from({ length: A_LA_VEZ }, atender));
  return cuenta;
}

/**
 * Atiende a una sesión con claves vencidas en la agenda («minutos»): le avisa de lo que le toca, o la deja en pausa
 * si no tiene a quién, o solo recoloca su clave. Devuelve { avisos, pausada }.
 */
async function atenderA(store, uid, minutos, ahora, dispositivos, enviar) {
  const sobran = new Set(minutos);
  let vigente = null; // El minuto de la clave que el documento tiene ahora por suya.
  let guardado = false; // ¿Lo ha guardado esta pasada? Entonces sabe cuál es su clave y puede quitar ya las demás.
  let avisos = 0;
  let pausada = false;
  const anotar = ({ antes, despues }) => {
    vigente = (despues || antes).agenda;
    if (despues) guardado = true;
    if (vigente) sobran.add(vigente); // La que acaba de poner esta pasada también sobrará si el documento sigue adelante.
    return despues;
  };

  const doc = await leer(store, uid);
  vigente = doc.agenda;
  if (doc.pausado === null && tocaAvisar(doc, ahora)) {
    const destinos = await dispositivos(uid); // Una vez por sesión, no una por aviso.
    if (destinos.length) {
      // Se toman con un turno; el siguiente aviso de cada cosa no se apunta hasta saber que este ha llegado.
      let cosas = [];
      anotar(await cambiar(store, uid, (actual) => {
        const reclamo = actual.pausado === null ? reclamarAvisos(actual, ahora) : null;
        cosas = reclamo ? reclamo.avisar : [];
        return reclamo && reclamo.doc;
      }, ahora));
      const entregadas = cosas.length ? await enviar(uid, destinos, cosas) : [];
      if (entregadas.length) anotar(await cambiar(store, uid, (actual) => confirmarAvisos(actual, entregadas, ahora), ahora));
      avisos = entregadas.length;
    } else {
      // Sin dispositivos: en pausa. La clave de la limpieza, antes de guardar (que sobre, no que falte).
      await store.set(claveDeLimpieza(diaDeLimpieza(ahora), uid), '1');
      const pausa = await cambiar(store, uid, (actual) => (tocaAvisar(actual, ahora) ? documentoPausado(actual, ahora) : null), ahora);
      pausada = pausa.despues !== null;
      vigente = (pausa.despues || pausa.antes).agenda;
      // Un alta que llegara justo ahora pudo mirar antes de la pausa y no despertar nada: se mira otra vez. Y sus
      // claves de la agenda no se quitan aún (ya serán viejas): puede que ese alta acabe de poner una de ellas.
      if (pausada) await despertar(store, uid, ahora);
    }
  } else if (doc.pausado === null && doc.agenda !== agendaDe(doc, ahora)) {
    // Nada que avisar y la clave no está donde toca: se guarda otra vez, que es lo que la pone en su sitio.
    const descolocado = (actual) => actual.pausado === null && !tocaAvisar(actual, ahora) && actual.agenda !== agendaDe(actual, ahora);
    anotar(await cambiar(store, uid, (actual) => (descolocado(actual) ? actual : null), ahora));
  }

  // Las claves vencidas que sobran. Recién guardado el documento, todas las que no sean la suya; si no, solo las
  // viejas: una reciente puede ser de quien está guardando ahora mismo (puso su clave y aún no ha escrito).
  const viejo = minutoDe(ahora - VIEJA);
  const quitar = [...sobran].filter((minuto) => minuto !== vigente && (guardado || minuto < viejo)).slice(0, MAX_QUITAR);
  await Promise.all(quitar.map((minuto) => store.delete(claveDeAgenda(minuto, uid))));
  return { avisos, pausada };
}

/** ¿Es la pasada de las 03:00 (UTC)? Con margen: vale aunque arranque unos segundos antes o después, dentro de sus cinco minutos. */
export function tocaLimpiar(ahora) {
  const reloj = new Date(ahora + TOLERANCIA);
  return reloj.getUTCHours() === 3 && reloj.getUTCMinutes() < 5;
}

/**
 * La limpieza de cada día: borra los documentos que llevan en pausa lo que dura la retención y cuya sesión sigue sin
 * dispositivos (la app se desinstaló, o esa sesión ya no existe: nadie va a volver a por ellos), y quita las claves
 * de limpieza a las que ya les ha llegado el día. Al que sigue en pausa teniendo ya dispositivos lo despierta: sin
 * clave de limpieza y dormido, se quedaría así para siempre. Devuelve cuántos documentos ha borrado.
 */
export async function limpiar(store, ahora, seguir = () => true) {
  const hoy = diaDe(ahora);
  const { blobs } = await store.list({ prefix: 'limpieza/' });
  let borrados = 0;
  for (const { key } of blobs) {
    const [, dia, uid, ...resto] = key.split('/');
    if (!dia || !uid || resto.length || dia > hoy) continue;
    if (!seguir()) break;
    try {
      // Primero los dispositivos y lo último el documento: que entre mirarlo y borrarlo no quepa casi nada.
      const sola = !(await hayDispositivos(store, uid));
      const doc = await leer(store, uid);
      // Que siga en pausa no basta: si despertó y volvió a pararse, la que cuenta es su clave de limpieza nueva.
      if (sola && doc.pausado !== null && diaDeLimpieza(doc.pausado) <= hoy) {
        await store.delete(claveDe(uid));
        borrados += 1;
      } else if (!sola && doc.pausado !== null && !(await despertar(store, uid, ahora))) {
        continue; // No ha despertado (justo se quedó sin dispositivos): su clave sigue, y mañana se mira otra vez.
      }
      await store.delete(key);
    } catch (error) {
      console.error(`Limpieza de ${uid}`, error);
    }
  }
  return borrados;
}
