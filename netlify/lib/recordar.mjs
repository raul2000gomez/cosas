/* Cosas · recordatorios: lo que la función de avisos guarda de cada sesión y las cuentas que hace con ello.

   De cada sesión (uid) se guarda un documento, en Netlify Blobs (recordar/{uid}):

     { nacido, v, zona, general, cosas: { id: { texto, hecha, r } },
       prox: { id: instante }, turno: { id: { de, hasta } }, agenda, pausado, enviados: { dia, n }, actualizado }

   «zona», «general» y «cosas» son lo que comparte con la app: la zona horaria de quien puso los recordatorios, el
   recordatorio de todas y, de cada cosa del grupo Recordar que lo tiene, el texto que se enseña en el aviso, si ya
   está hecha y cuándo toca («r», ver horario.mjs). «v» es su versión: la app dice sobre cuál escribe y, si entretanto
   ha cambiado (otro dispositivo, o un «Hecho» pulsado en un aviso), vuelve a mirar antes de escribir. «nacido»
   distingue este documento de otro que se creara después de borrarlo: la app no confunde «ha vuelto a empezar» con
   «me lo han quitado todo».

   Lo demás es cosa de la función. «prox»: cuándo toca avisar de cada cosa pendiente. «turno»: los avisos que una
   ejecución está enviando (hasta que caduca, otra no los toca; si no llega a confirmarlos, la siguiente los repite:
   un aviso puede llegar dos veces, pero no se pierde). «agenda»: el minuto de su clave en agenda/, que es lo único
   que mira la función programada. «pausado»: desde cuándo no tiene dispositivos a los que avisar (no gasta
   ejecuciones y, si sigue así, se acaba borrando). «enviados»: cuántos avisos lleva hoy.

   Aquí no se lee ni se escribe nada: solo se calcula, para poder probarlo sin almacén ni reloj. */

import { diaDe, esZona, minutoDe, proximoAviso, recordatorioDe } from './horario.mjs';

export const MAX_COSAS = 100;
export const MAX_ID = 100; // El aviso lleva el id y tiene que caber, con otros, en un mensaje push.
export const MAX_TEXTO = 500;
// Un aviso que no se pudo enviar a su hora (la función programada estuvo parada, el servicio push no respondía, la
// sesión estaba en pausa) se sigue intentando hasta que pasa este tiempo; después se salta.
export const CADUCA = 6 * 60 * 60 * 1000;
// Lo que una ejecución tiene para enviar los avisos que ha tomado antes de que otra pueda repetirlos: más de lo que
// dura una ejecución (30 segundos) y menos de lo que tarda en llegar la siguiente (cinco minutos).
export const CESION = 2 * 60 * 1000;
// Una ejecución que arranca un instante antes de su minuto atiende igual lo de ese minuto.
export const TOLERANCIA = 5000;
export const MAX_POR_TANDA = 40; // Avisos de una sesión que una ejecución toma de una vez.
export const MAX_AL_DIA = 300; // Avisos de una sesión en un día: por encima, algo va mal (o alguien abusa) y se dejan de enviar.
// Lo que se guarda un documento en pausa (sin dispositivos a los que avisar) antes de borrarlo.
export const RETENCION = 30 * 24 * 60 * 60 * 1000;

// Con sentido como texto (sin mitades sueltas de un carácter: va en la dirección que abre el aviso) y que no sea
// «__proto__», la única clave que un objeto corriente no guarda como las demás.
export const esIdDeCosa = (v) => typeof v === 'string' && v.length >= 1 && v.length <= MAX_ID && v !== '__proto__' && v.isWellFormed();

const tiene = (objeto, clave) => Object.hasOwn(objeto, clave);
const firma = (valor) => JSON.stringify(valor === undefined ? null : valor);

export function documentoVacio() {
  return { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {}, prox: {}, turno: {}, agenda: null, pausado: null, enviados: { dia: '', n: 0 }, actualizado: 0 };
}

/** Una cosa con su recordatorio, con su forma exacta ({ texto, hecha, r }), o null si no la tiene. */
export function cosaDe(bruta) {
  if (!bruta || typeof bruta !== 'object' || typeof bruta.texto !== 'string' || typeof bruta.hecha !== 'boolean') return null;
  const texto = bruta.texto.trim();
  const r = recordatorioDe(bruta.r);
  if (!texto || texto.length > MAX_TEXTO || !r) return null;
  return { texto, hecha: bruta.hecha, r };
}

/**
 * Lo que envía la app, comprobado: { siV, zona, general, poner: { id: cosa }, quitar: [id], rechazadas: [id] }.
 * Es un cambio, no el documento entero: «poner» añade o sustituye esas cosas, «quitar» las quita y de lo que no se
 * nombra no se toca nada (una versión de la app que no entienda algo de lo guardado no lo borra por no nombrarlo).
 * «general» solo viaja si cambia (null lo quita). Una cosa que no vale no tumba el envío: su id va en «rechazadas»,
 * tal cual llegó (la app lo apunta como enviado y no lo repite sin fin). null si el envío en sí no se entiende.
 */
export function envioDe(bruto) {
  if (!bruto || typeof bruto !== 'object' || !Number.isInteger(bruto.siV) || bruto.siV < 0 || !esZona(bruto.zona)) return null;
  const poner = bruto.poner === undefined ? {} : bruto.poner;
  const quitar = bruto.quitar === undefined ? [] : bruto.quitar;
  if (!poner || typeof poner !== 'object' || Array.isArray(poner) || !Array.isArray(quitar)) return null;
  let general;
  if (bruto.general !== undefined) {
    general = bruto.general === null ? null : recordatorioDe(bruto.general);
    if (bruto.general !== null && !general) return null;
  }
  const entradas = Object.entries(poner);
  if (entradas.length > 4 * MAX_COSAS || quitar.length > 4 * MAX_COSAS) return null;
  const validas = [];
  const rechazadas = [];
  for (const [id, bruta] of entradas) {
    const cosa = cosaDe(bruta);
    if (esIdDeCosa(id) && cosa) validas.push([id, cosa]);
    else rechazadas.push(id);
  }
  return { siV: bruto.siV, zona: bruto.zona, general, poner: Object.fromEntries(validas), quitar: quitar.filter(esIdDeCosa), rechazadas };
}

/** Lo que el documento comparte con la app. */
export const publicoDe = (doc) => ({ nacido: doc.nacido, v: doc.v, zona: doc.zona, general: doc.general, cosas: doc.cosas });

/** Cuándo toca después de haber avisado (o de haberse saltado) el de «instante». */
const siguienteAviso = (r, zona, ahora, instante) => proximoAviso(r, zona, Math.max(ahora, instante));

/**
 * El documento tras lo que envía la app (ya comprobado con envioDe): { doc, rechazadas }, o null si la app escribía
 * sobre una versión que ya no es la que hay (tiene que volver a mirar). La versión solo sube si cambia lo compartido.
 * Cuándo toca cada cosa: la que sigue pendiente con el mismo recordatorio conserva su próximo aviso (guardar otra vez
 * justo a la hora no se lo salta, y lo que ya tocaba y aún no ha salido tampoco se pierde al cambiar de zona); a la
 * nueva, o a la que cambia, se le calcula desde ahora. Si estaba en pausa, sigue en pausa: despertarlo es otra cosa
 * (hace falta un dispositivo al que avisar).
 */
export function documentoCon(doc, envio, ahora) {
  if (envio.siV !== doc.v) return null;
  const cosas = { ...doc.cosas };
  for (const id of envio.quitar) delete cosas[id];
  const rechazadas = [...envio.rechazadas];
  let cuantas = Object.keys(cosas).length;
  for (const [id, cosa] of Object.entries(envio.poner)) {
    if (!tiene(cosas, id)) {
      if (cuantas >= MAX_COSAS) {
        rechazadas.push(id); // No caben más.
        continue;
      }
      cuantas += 1;
    }
    cosas[id] = cosa;
  }
  const general = envio.general === undefined ? doc.general : envio.general;
  const mismaZona = envio.zona === doc.zona;
  const prox = {};
  const turno = {};
  for (const [id, cosa] of Object.entries(cosas)) {
    if (cosa.hecha) continue;
    const antes = tiene(doc.cosas, id) ? doc.cosas[id] : null;
    const instanteDeAntes = tiene(doc.prox, id) ? doc.prox[id] : null;
    const sigueIgual = antes && !antes.hecha && firma(antes.r) === firma(cosa.r) && Number.isFinite(instanteDeAntes) && (mismaZona || instanteDeAntes <= ahora);
    const instante = sigueIgual ? instanteDeAntes : proximoAviso(cosa.r, envio.zona, ahora);
    if (instante === null) continue;
    prox[id] = instante;
    if (sigueIgual && tiene(doc.turno, id)) turno[id] = doc.turno[id];
  }
  const cambia = envio.zona !== doc.zona || firma(general) !== firma(doc.general) || firma(cosas) !== firma(doc.cosas);
  return {
    doc: { ...doc, v: cambia ? doc.v + 1 : doc.v, zona: envio.zona, general, cosas, prox, turno, actualizado: ahora },
    rechazadas,
  };
}

/** El documento con esa cosa hecha («Hecho» en su aviso): ya no se avisa de ella. null si no hay nada que cambiar. */
export function documentoConHecha(doc, id) {
  const cosa = tiene(doc.cosas, id) ? doc.cosas[id] : null;
  if (!cosa || cosa.hecha) return null;
  const prox = { ...doc.prox };
  const turno = { ...doc.turno };
  delete prox[id];
  delete turno[id];
  return { ...doc, v: doc.v + 1, cosas: { ...doc.cosas, [id]: { ...cosa, hecha: true } }, prox, turno };
}

/**
 * El documento en pausa (le toca un aviso y no tiene dispositivos a los que enviarlo): sale de la agenda y suelta los
 * turnos. Cuándo tocaba cada cosa no se mueve: si despierta a tiempo, sale con retraso. null si ya lo estaba.
 */
export function documentoPausado(doc, ahora) {
  return doc.pausado === null ? { ...doc, pausado: ahora, turno: {} } : null;
}

/** El documento despierto (vuelve a tener a quién avisar). null si no estaba en pausa. */
export function documentoDespierto(doc) {
  return doc.pausado === null ? null : { ...doc, pausado: null };
}

/** El día (UTC, «AAAAMMDD») a partir del cual se puede borrar un documento en pausa desde ese instante. */
export const diaDeLimpieza = (pausado) => diaDe(pausado + RETENCION);

/**
 * Los avisos que esta ejecución toma para enviar: { doc, avisar: [{ id, texto, de }] }, o null si no hay nada que
 * tocar. De cada cosa cuyo momento ha llegado (y que no esté ya tomada por otra ejecución) se apunta el turno; su
 * próximo aviso no se mueve hasta que se confirma el envío (confirmarAvisos). Lo que caducó, y lo que pasa del tope
 * del día, se salta: se le apunta el siguiente sin avisar. Como mucho, «maximo» de una vez (lo demás, en otra tanda).
 */
export function reclamarAvisos(doc, ahora, maximo = MAX_POR_TANDA) {
  const prox = { ...doc.prox };
  const turno = { ...doc.turno };
  const hoy = diaDe(ahora);
  let enviadosHoy = doc.enviados.dia === hoy ? doc.enviados.n : 0;
  const avisar = [];
  let cambia = false;
  const vencidas = Object.entries(doc.prox).sort((a, b) => a[1] - b[1]); // Lo más atrasado, primero.
  for (const [id, instante] of vencidas) {
    const cosa = tiene(doc.cosas, id) ? doc.cosas[id] : null;
    if (!cosa || cosa.hecha) { // No debería quedar ninguno así: se limpia.
      delete prox[id];
      delete turno[id];
      cambia = true;
      continue;
    }
    if (!(instante <= ahora + TOLERANCIA)) continue;
    const tomado = tiene(doc.turno, id) && doc.turno[id].de === instante ? doc.turno[id] : null;
    if (tomado && tomado.hasta > ahora) continue; // Otra ejecución lo está enviando.
    // El que ya se tomó y no llegó a confirmarse se repite sin contar otra vez: que el servicio push falle un rato
    // no puede gastar el tope del día y dejar sin avisos a quien no ha hecho nada raro.
    if (ahora - instante > CADUCA || (!tomado && enviadosHoy >= MAX_AL_DIA)) {
      const siguiente = siguienteAviso(cosa.r, doc.zona, ahora, instante);
      if (siguiente === null) delete prox[id];
      else prox[id] = siguiente;
      delete turno[id];
      cambia = true;
      continue;
    }
    if (avisar.length >= maximo) continue;
    turno[id] = { de: instante, hasta: ahora + CESION };
    avisar.push({ id, texto: cosa.texto, de: instante });
    if (!tomado) enviadosHoy += 1;
    cambia = true;
  }
  return cambia ? { doc: { ...doc, prox, turno, enviados: { dia: hoy, n: enviadosHoy } }, avisar } : null;
}

/**
 * El documento tras enviar esos avisos ([{ id, de }]): a cada cosa se le apunta su siguiente aviso (si entretanto no
 * ha cambiado: una hora nueva, o hecha) y se suelta su turno. null si no hay nada que cambiar. De los que no se
 * pudieron enviar no se dice nada aquí: su turno caduca solo y la siguiente ejecución los repite.
 */
export function confirmarAvisos(doc, enviados, ahora) {
  const prox = { ...doc.prox };
  const turno = { ...doc.turno };
  let cambia = false;
  for (const { id, de } of enviados) {
    const tomado = tiene(doc.turno, id) ? doc.turno[id] : null;
    if (tomado && tomado.de === de) {
      delete turno[id];
      cambia = true;
    }
    const cosa = tiene(doc.cosas, id) ? doc.cosas[id] : null;
    if (!cosa || cosa.hecha || !tiene(doc.prox, id) || doc.prox[id] !== de) continue;
    const siguiente = siguienteAviso(cosa.r, doc.zona, ahora, de);
    if (siguiente === null) delete prox[id];
    else prox[id] = siguiente;
    cambia = true;
  }
  return cambia ? { ...doc, prox, turno } : null;
}

/** ¿Le toca ya algún aviso? (Con la misma tolerancia con la que se toman.) */
export const tocaAvisar = (doc, ahora) => Object.values(doc.prox).some((instante) => instante <= ahora + TOLERANCIA);

/**
 * El minuto de su clave en la agenda: el de su aviso más cercano o, si ese ya ha pasado, el de ahora; null si está
 * en pausa o no espera ninguno. Nunca un minuto anterior a «ahora»: así una clave recién puesta nunca parece vieja,
 * y la función programada puede quitar sin miedo las que sí lo son (ver recordatorios.mjs).
 */
export function agendaDe(doc, ahora) {
  if (doc.pausado !== null) return null;
  const instantes = Object.values(doc.prox).filter(Number.isFinite);
  return instantes.length ? minutoDe(Math.max(Math.min(...instantes), ahora)) : null;
}

/** Un documento guardado, con lo que le falte por defecto (nunca lanza: un documento raro no para a los demás). */
export function documentoDe(guardado) {
  const vacio = documentoVacio();
  if (!guardado || typeof guardado !== 'object') return vacio;
  const objeto = (valor) => (valor && typeof valor === 'object' && !Array.isArray(valor) ? valor : {});
  const cosas = {};
  for (const [id, bruta] of Object.entries(objeto(guardado.cosas))) {
    const cosa = cosaDe(bruta);
    if (esIdDeCosa(id) && cosa) cosas[id] = cosa;
  }
  const prox = {};
  for (const [id, instante] of Object.entries(objeto(guardado.prox))) {
    if (tiene(cosas, id) && !cosas[id].hecha && Number.isFinite(instante)) prox[id] = instante;
  }
  const turno = {};
  for (const [id, tomado] of Object.entries(objeto(guardado.turno))) {
    if (tiene(prox, id) && tomado && Number.isFinite(tomado.de) && Number.isFinite(tomado.hasta)) turno[id] = { de: tomado.de, hasta: tomado.hasta };
  }
  const enviados = objeto(guardado.enviados);
  return {
    nacido: typeof guardado.nacido === 'string' && guardado.nacido ? guardado.nacido : null,
    v: Number.isInteger(guardado.v) && guardado.v >= 0 ? guardado.v : 0,
    zona: esZona(guardado.zona) ? guardado.zona : vacio.zona,
    general: recordatorioDe(guardado.general),
    cosas,
    prox,
    turno,
    agenda: typeof guardado.agenda === 'string' ? guardado.agenda : null,
    pausado: Number.isFinite(guardado.pausado) ? guardado.pausado : null,
    enviados: typeof enviados.dia === 'string' && Number.isInteger(enviados.n) && enviados.n >= 0 ? { dia: enviados.dia, n: enviados.n } : vacio.enviados,
    actualizado: Number.isFinite(guardado.actualizado) ? guardado.actualizado : 0,
  };
}
