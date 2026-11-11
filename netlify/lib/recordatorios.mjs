/* Cosas · recordatorios: leer, guardar y avisar, sobre el almacén (Netlify Blobs) que se le pase.

     recordar/{uid}                el documento de esa sesión (ver recordar.mjs).
     agenda/{AAAAMMDDHHmm}/{uid}   índice (sin datos): el minuto (UTC) del aviso más cercano de esa sesión (o el de cuando se puso, si ya iba con retraso).
     limpieza/{AAAAMMDD}/{uid}     índice (sin datos): el día (UTC) desde el que se puede borrar su documento, si sigue igual.

   La función programada (netlify/functions/recordatorios.mjs) solo lista agenda/ cada vez y atiende a quien le
   toca: no recorre los documentos de todo el mundo. Cada cambio de un documento se hace comparando su etiqueta
   (etag): si alguien lo cambió entretanto, se vuelve a leer y se repite.

   La agenda: quien guarda un documento pone siempre su clave antes de guardarlo, aunque ya estuviera, y nunca quita
   ninguna. Solo la pasada programada las quita: las que ella misma acaba de dejar atrás al guardar y, de las demás
   que sobran, las que ya son viejas (una reciente puede ser de alguien que está a punto de guardar; y que es vieja se
   lee en su nombre, porque quien guarda nunca pone una clave de un minuto que ya pasó). Y si entre las que quita hay
   alguna reciente, guarda el documento una vez más: a quien estuviera guardando con ella le obliga a mirar de nuevo,
   y al repetir la repone. Si algo se corta a medias o dos se cruzan, sobra una clave (se mira el documento, no toca
   nada y se quita), nunca falta: una sesión sin su clave se quedaría sin avisos y nadie se enteraría. Por si acaso,
   cada vez que la app lee se vuelve a poner.

   Los avisos: la pasada toma los que tocan (un turno, que caduca solo), los envía y solo a los que han llegado les
   apunta el siguiente. Lo que no llega a confirmarse (falló el envío, se acabó el tiempo) lo repite una pasada
   posterior: un aviso puede llegar dos veces, nunca perderse.

   Una sesión a la que le toca un aviso y no tiene dispositivos queda en pausa: sale de la agenda y, si en un mes no
   vuelve (un alta, o la app que lee o guarda, con algún dispositivo), su documento se borra.

   La limpieza: tampoco el documento que no espera ningún aviso (todas sus cosas hechas, o ya sin cosas) pasa por la
   agenda, así que nadie lo miraría nunca y sus textos se quedarían aquí para siempre. Quien guarda un documento en
   pausa o sin nada pendiente deja puesta, antes de guardarlo, la clave de limpieza del día en que se podrá borrar
   (treinta días después de la pausa, o de la última vez que su dueño lo cambió); la limpieza de cada día mira las
   claves a las que les ha llegado el suyo y borra el documento solo si sigue así. El que se queda sin cosas no se
   borra en el acto: vacío ya no guarda ningún texto, y es lo que dice a los demás dispositivos de la cuenta que
   aquello se quitó (sin documento, la app de otro dispositivo lo subiría todo otra vez; ver «nacido»).

   Borrar: el almacén no sabe borrar comparando, y entre mirar un documento y borrarlo alguien puede haberlo cambiado
   (la app guarda un recordatorio justo entonces, y se iría con él). Por eso son dos pasos: el documento se cambia,
   comparando, por una marca ({ borrando }) y luego se quita la marca. Una marca reciente no se toca: quien quiere
   guardar espera a que desaparezca. Una vieja es de una limpieza que se cortó a medias: vale por «no hay documento»
   y se escribe sobre ella. */

import { createHash, randomBytes } from 'node:crypto';
import { diaDe, minutoDe } from './horario.mjs';
import {
  CESION, TOLERANCIA, agendaDe, caducado, confirmarAvisos, documentoCon, documentoConHecha, documentoDe, documentoDespierto, documentoPausado, limpiezaDe,
  publicoDe, reclamarAvisos, tocaAvisar,
} from './recordar.mjs';

const INTENTOS = 5;
const A_LA_VEZ = 6; // Sesiones que una pasada atiende a la vez.
// Una clave de la agenda que sobra no se quita hasta que su minuto queda así de lejos: nadie tarda tanto en guardar.
const VIEJA = 10 * 60 * 1000;
const MAX_QUITAR = 50; // Claves que sobran de una sesión (de la agenda o de limpieza) que se quitan de una vez (lo normal es una; las demás, en la siguiente).
// Lo que espera, entre un intento y otro, quien quiere guardar un documento que la limpieza está borrando: borrarlo es
// cosa de un momento.
const ESPERA_DE_BORRADO = 200;
const claveDe = (uid) => `recordar/${uid}`;
const claveDeAgenda = (minuto, uid) => `agenda/${minuto}/${uid}`;
const claveDeLimpieza = (dia, uid) => `limpieza/${dia}/${uid}`;

const esperar = (ms) => new Promise((resolver) => { setTimeout(resolver, ms); });

// La marca que deja la limpieza mientras borra un documento. Quien la lee como documento no encuentra nada en ella
// (documentoDe da uno vacío), que es lo que hay. Reciente (la limpieza no vive más que una ejecución, mucho menos que
// la cesión de un turno), no se toca.
const marcaDeBorrado = (ahora) => ({ borrando: ahora });
const borrandose = (guardado, ahora) => Boolean(guardado) && Number.isFinite(guardado.borrando) && Math.abs(ahora - guardado.borrando) < CESION;

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
 * Al guardarlo le pone, si no lo tiene, su «nacido», y deja puestas sus claves antes de escribir, siempre: la de la
 * agenda si espera algún aviso y la de limpieza si no, o si está en pausa. Aunque no cambien (por si se perdieron) y
 * aunque luego la escritura no entre (sobrarían, no faltarían). Si la limpieza lo está borrando, espera a que acabe.
 * Devuelve { antes, despues, etiqueta } (despues: null si no cambió; etiqueta: la de lo que ha quedado guardado).
 */
async function cambiar(store, uid, cambio, ahora) {
  for (let intento = 0; intento < INTENTOS; intento += 1) {
    const actual = await leerConEtiqueta(store, claveDe(uid));
    const antes = documentoDe(actual && actual.data);
    const nuevo = cambio(antes);
    if (!nuevo) return { antes, despues: null };
    if (actual && borrandose(actual.data, ahora)) {
      // Lo que se escribiera sobre la marca se iría con ella en cuanto la limpieza la quite. (Quien no tiene nada
      // que escribir no espera: para él ya no hay documento, y es verdad.)
      await esperar(ESPERA_DE_BORRADO);
      continue;
    }
    const despues = { ...nuevo, nacido: nuevo.nacido || randomBytes(9).toString('base64url'), agenda: agendaDe(nuevo, ahora) };
    if (despues.agenda) await store.set(claveDeAgenda(despues.agenda, uid), '1');
    const limpieza = limpiezaDe(despues);
    if (limpieza) await store.set(claveDeLimpieza(limpieza, uid), '1');
    const { modified, etag } = await store.setJSON(claveDe(uid), despues, actual ? { onlyIfMatch: actual.etag } : { onlyIfNew: true });
    if (modified) return { antes, despues, etiqueta: etag };
    // Alguien lo ha cambiado entretanto: se vuelve a mirar.
  }
  throw new Error(`No se pudo guardar recordar/${uid}`);
}

/**
 * Guarda otra vez el documento de esa sesión, tal cual, después de que la pasada haya quitado de la agenda alguna
 * clave reciente. Esa misma clave pudo ponerla un instante antes alguien que estaba guardando: si su documento aún
 * no había entrado, con esto ya no entra sin volver a mirar (y al repetir pone la clave otra vez); si entró justo
 * después de lo último que guardó la pasada («escrito»), guardar de nuevo repone la clave de lo que haya. Lo normal
 * es lo primero, y sale por una sola escritura: lo que la pasada guardó, comparando con su etiqueta. Sin documento
 * (lo olvidaron entretanto) no hay nada que guardar.
 */
async function reafirmar(store, uid, escrito, ahora) {
  if (escrito.etiqueta && (await store.setJSON(claveDe(uid), escrito.despues, { onlyIfMatch: escrito.etiqueta })).modified) return;
  await cambiar(store, uid, (actual) => (actual.nacido ? actual : null), ahora);
}

/**
 * Borra el documento de esa sesión si sigue siendo el que se leyó («actual», con su etiqueta) y devuelve si lo ha
 * hecho. Primero lo cambia, comparando, por la marca: si alguien lo ha tocado desde que se miró, no entra y no se
 * borra nada. Desde ese momento sus textos ya no están; quitar la marca es lo de menos.
 */
async function borrar(store, uid, actual, ahora) {
  const { modified } = await store.setJSON(claveDe(uid), marcaDeBorrado(ahora), { onlyIfMatch: actual.etag });
  if (!modified) return false;
  await store.delete(claveDe(uid));
  return true;
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
  return (await cambiar(store, uid, (doc) => documentoConHecha(doc, id, ahora), ahora)).despues !== null;
}

/**
 * Borra el documento de esa sesión, porque lo pide ella: sin mirar ni comparar (si otro dispositivo suyo guarda justo
 * entonces, manda quien llegue el último). Sus claves de la agenda y de la limpieza se quedan: las quita quien las
 * encuentre.
 */
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
  let guardado = null; // Lo último que ha guardado esta pasada ({ despues, etiqueta }): entonces sabe cuál es su clave y puede quitar ya las demás.
  let avisos = 0;
  let pausada = false;
  const anotar = ({ antes, despues, etiqueta }) => {
    vigente = (despues || antes).agenda;
    if (despues) guardado = { despues, etiqueta };
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
      // Sin dispositivos: en pausa (al guardarla queda puesta su clave de limpieza, ver cambiar).
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
  // Una reciente podía ser también la de alguien que estaba guardando (las viejas no: nadie tarda tanto).
  if (guardado && quitar.some((minuto) => minuto >= viejo)) await reafirmar(store, uid, guardado, ahora);
  return { avisos, pausada };
}

/** ¿Es la pasada de las 03:00 (UTC)? Con margen: vale aunque arranque unos segundos antes o después, dentro de sus cinco minutos. */
export function tocaLimpiar(ahora) {
  const reloj = new Date(ahora + TOLERANCIA);
  return reloj.getUTCHours() === 3 && reloj.getUTCMinutes() < 5;
}

/**
 * Mira el documento de una sesión a cuyas claves de limpieza les ha llegado el día y, si sigue como estaba cuando se
 * pusieron, lo borra. Devuelve 'borrado'; 'nada' (no hay nada que borrar: esas claves sobran); o 'luego' (no se ha
 * podido saber o hacer ahora: las claves se quedan, y mañana se mira otra vez).
 */
async function repasar(store, uid, hoy, ahora) {
  let actual = await leerConEtiqueta(store, claveDe(uid));
  if (actual && borrandose(actual.data, ahora)) return 'luego'; // Otra ejecución lo está borrando: que acabe ella.
  let doc = documentoDe(actual && actual.data);
  // Los dispositivos solo importan si está en pausa (sin nada pendiente no habría qué enviarles): si los tiene, se
  // le despierta. Dormido y sin clave de limpieza se quedaría así para siempre.
  const dormida = doc.pausado !== null && (await hayDispositivos(store, uid));
  if (dormida) {
    if (!(await despertar(store, uid, ahora))) return 'luego'; // Justo se ha quedado sin ellos, o alguien se ha adelantado.
    actual = await leerConEtiqueta(store, claveDe(uid));
    doc = documentoDe(actual && actual.data); // Despierto puede no esperar nada desde hace un mes: se mira como a los demás.
  }
  // Una marca vieja (una limpieza que se cortó a medias) se lee como un documento vacío de siempre: se acaba de borrar.
  if (!actual || !caducado(doc, hoy, !dormida)) return 'nada';
  return (await borrar(store, uid, actual, ahora)) ? 'borrado' : 'luego'; // 'luego': alguien lo ha cambiado justo ahora.
}

/**
 * La limpieza de cada día: de las claves de limpieza a las que les ha llegado el día, borra el documento que sigue
 * como estaba cuando se puso la clave. En pausa y con su sesión aún sin dispositivos (la app se desinstaló, o esa
 * sesión ya no existe: nadie va a volver a por él); o sin nada pendiente y sin que su dueño lo haya tocado desde
 * entonces. Si entretanto cambió (despertó, volvió a esperar avisos, se guardó otra vez), no: su clave de ahora, si
 * le hace falta, es otra, y la puso quien lo guardó. Como la pasada, atiende a varias sesiones a la vez, una que
 * falla no para a las demás y lo que no dé tiempo («seguir») espera a mañana. Devuelve cuántos documentos ha borrado.
 */
export async function limpiar(store, ahora, seguir = () => true) {
  const hoy = diaDe(ahora);
  const { blobs } = await store.list({ prefix: 'limpieza/' });
  const vencidas = new Map(); // uid → sus claves de limpieza a las que ya les ha llegado el día.
  for (const { key } of blobs) {
    const [, dia, uid, ...resto] = key.split('/');
    if (!dia || !uid || resto.length || dia > hoy) continue;
    if (!vencidas.has(uid)) vencidas.set(uid, []);
    vencidas.get(uid).push(key);
  }
  const cola = [...vencidas.keys()];
  let borrados = 0;
  const atender = async () => {
    while (cola.length && seguir()) {
      const uid = cola.shift();
      try {
        const repaso = await repasar(store, uid, hoy, ahora);
        if (repaso === 'luego') continue;
        borrados += repaso === 'borrado' ? 1 : 0;
        // Visto el documento una vez, sobran todas las suyas a las que les ha llegado el día.
        await Promise.all(vencidas.get(uid).slice(0, MAX_QUITAR).map((clave) => store.delete(clave)));
      } catch (error) {
        console.error(`Limpieza de ${uid}`, error);
      }
    }
  };
  await Promise.all(Array.from({ length: A_LA_VEZ }, atender));
  return borrados;
}
