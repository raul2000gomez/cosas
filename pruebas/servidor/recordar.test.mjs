/* Pruebas de netlify/lib/recordar.mjs:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CADUCA, CESION, MAX_AL_DIA, MAX_COSAS, MAX_POR_TANDA, RETENCION, agendaDe, caducado, confirmarAvisos, cosaDe, diaDeLimpieza, documentoCon, documentoConHecha,
  documentoDe, documentoDespierto, documentoPausado, documentoVacio, envioDe, esIdDeCosa, limpiezaDe, publicoDe, reclamarAvisos, tocaAvisar,
} from '../../netlify/lib/recordar.mjs';

const MADRID = 'Europe/Madrid';
const utc = (texto) => Date.parse(`${texto}Z`);
const DIARIO = { hora: '09:00', frec: 'dia' };
const A_LAS_10 = { hora: '10:00', frec: 'dia' };
const cosa = (texto, r = DIARIO, hecha = false) => ({ texto, hecha, r });
const envio = (siV, cambio = {}, zona = MADRID) => envioDe({ siV, zona, ...cambio });
const LAS_8 = utc('2026-11-10T07:00'); // Las 8:00 en Madrid: lo diario de las 9:00 toca a las 08:00 UTC.
const LAS_9 = utc('2026-11-10T08:00');

/** Un documento con esas cosas, guardadas a las 8:00. */
const documento = (poner, general) => documentoCon(documentoVacio(), envio(0, { poner, general }), LAS_8).doc;
const muchas = (cuantas, prefijo = 'c') => Object.fromEntries(Array.from({ length: cuantas }, (_, i) => [`${prefijo}${i}`, cosa(`Cosa ${i}`)]));

test('cosaDe: texto, hecha y un recordatorio válido; nada más', () => {
  assert.deepEqual(cosaDe({ texto: '  Leche ', hecha: false, r: { frec: 'dia', hora: '09:00' }, sobra: 1 }), { texto: 'Leche', hecha: false, r: DIARIO });
  for (const mala of [null, {}, { texto: '', hecha: false, r: DIARIO }, { texto: '   ', hecha: false, r: DIARIO }, { texto: 'x'.repeat(501), hecha: false, r: DIARIO },
    { texto: 'Leche', hecha: 'no', r: DIARIO }, { texto: 'Leche', hecha: false }, { texto: 'Leche', hecha: false, r: { hora: '9', frec: 'dia' } }, { texto: 7, hecha: false, r: DIARIO },
    { texto: 'Cita', hecha: false, r: { hora: '09:00', frec: 'una', fecha: '2026-11-12' } }]) {
    assert.equal(cosaDe(mala), null, JSON.stringify(mala));
  }
});

test('esIdDeCosa: de 1 a 100, con sentido como texto y que no sea «__proto__»', () => {
  for (const id of ['a', 'a b/ñ', '語'.repeat(100), 'constructor', '😀']) assert.equal(esIdDeCosa(id), true, id);
  for (const id of ['', 'x'.repeat(101), '__proto__', '\ud83d', 7, null, undefined]) assert.equal(esIdDeCosa(id), false, String(id));
});

test('envioDe: un cambio (poner, quitar, general), no el documento entero', () => {
  assert.deepEqual(envio(0, { poner: { a: cosa('Leche') }, quitar: ['b'], general: DIARIO }), {
    siV: 0, zona: MADRID, general: DIARIO, poner: { a: cosa('Leche') }, quitar: ['b'], rechazadas: [],
  });
  // Sin nada que cambiar también vale; y «general» que no viaja no es lo mismo que «general: null» (quitarlo).
  assert.deepEqual(envio(3), { siV: 3, zona: MADRID, general: undefined, poner: {}, quitar: [], rechazadas: [] });
  assert.equal(envio(3, { general: null }).general, null);
  // En «quitar», lo que no puede ser un id no cuenta.
  assert.deepEqual(envio(0, { quitar: ['a', '', 7, null, 'x'.repeat(101), 'b'] }).quitar, ['a', 'b']);
});

test('envioDe: una cosa que no vale no tumba el envío: su id, tal cual, va en «rechazadas»', () => {
  const largo = 'x'.repeat(101);
  const resultado = envio(0, { poner: {
    a: cosa('Leche'), vacia: cosa('   '), larga: cosa('x'.repeat(501)), sinHora: { texto: 'Pan', hecha: false }, hora: cosa('Sal', { hora: '25:00', frec: 'dia' }),
    una: cosa('Cita', { hora: '09:00', frec: 'una', fecha: '2026-11-12' }), '': cosa('Sin id'), [largo]: cosa('Id largo'), b: cosa('Pan'),
  } });
  assert.deepEqual(resultado.poner, { a: cosa('Leche'), b: cosa('Pan') });
  assert.deepEqual(resultado.rechazadas.sort(), ['', 'hora', 'larga', largo, 'sinHora', 'una', 'vacia'].sort());
  // Un id con nombre de trampa se rechaza como otro que no vale: ni se guarda ni toca el prototipo.
  const trampa = envioDe(JSON.parse(`{"siV":0,"zona":"${MADRID}","poner":{"__proto__":{"texto":"Trampa","hecha":false,"r":{"hora":"09:00","frec":"dia"}}}}`));
  assert.deepEqual([Object.keys(trampa.poner), trampa.rechazadas], [[], ['__proto__']]);
  assert.equal({}.texto, undefined);
});

test('envioDe: lo que no se entiende es null (el sobre, no las cosas)', () => {
  const de = (cuantas) => Object.fromEntries(Array.from({ length: cuantas }, (_, i) => [`c${i}`, cosa('x')]));
  for (const malo of [null, 'x', [], {}, { zona: MADRID }, { siV: -1, zona: MADRID }, { siV: 1.5, zona: MADRID }, { siV: '0', zona: MADRID }, { siV: 0 },
    { siV: 0, zona: 'Marte/Olimpo' }, { siV: 0, zona: MADRID, general: { hora: 'x' } }, { siV: 0, zona: MADRID, general: 'dia' },
    { siV: 0, zona: MADRID, general: { hora: '09:00', frec: 'una', fecha: '2026-11-12' } }, { siV: 0, zona: MADRID, poner: [] }, { siV: 0, zona: MADRID, poner: 'x' },
    { siV: 0, zona: MADRID, poner: null }, { siV: 0, zona: MADRID, quitar: {} }, { siV: 0, zona: MADRID, quitar: 'a' }, { siV: 0, zona: MADRID, quitar: null },
    { siV: 0, zona: MADRID, poner: de(401) }, { siV: 0, zona: MADRID, quitar: Array.from({ length: 401 }, (_, i) => `c${i}`) }]) {
    assert.equal(envioDe(malo), null, JSON.stringify(malo).slice(0, 90));
  }
  assert.equal(Object.keys(envioDe({ siV: 0, zona: MADRID, poner: de(400), quitar: Array.from({ length: 400 }, (_, i) => `c${i}`) }).poner).length, 400);
});

test('documentoCon: la primera vez guarda, calcula cuándo toca y sube la versión', () => {
  const { doc, rechazadas } = documentoCon(documentoVacio(), envio(0, { poner: { a: cosa('Leche'), b: cosa('Pan', { hora: '07:30', frec: 'dia' }), c: cosa('Hecha', DIARIO, true) }, general: DIARIO }), LAS_8);
  assert.deepEqual(rechazadas, []);
  assert.deepEqual(publicoDe(doc), { nacido: null, v: 1, zona: MADRID, general: DIARIO, cosas: { a: cosa('Leche'), b: cosa('Pan', { hora: '07:30', frec: 'dia' }), c: cosa('Hecha', DIARIO, true) } });
  assert.deepEqual(doc.prox, { a: LAS_9, b: utc('2026-11-11T06:30') }); // La hecha no espera aviso.
  assert.equal(doc.actualizado, LAS_8);
  assert.equal(agendaDe(doc, LAS_8), '202611100800');
});

test('documentoCon: lo que no se nombra no se toca', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan', A_LAS_10), c: cosa('Sal') }, DIARIO);
  const ahora = utc('2026-11-10T07:30');
  // Solo cambia el texto de «a» y se quita «c»: «b» y el general siguen como estaban.
  const { doc: otro } = documentoCon(doc, envio(1, { poner: { a: cosa('Leche entera') }, quitar: ['c', 'no-esta'] }), ahora);
  assert.deepEqual(publicoDe(otro), { nacido: null, v: 2, zona: MADRID, general: DIARIO, cosas: { a: cosa('Leche entera'), b: cosa('Pan', A_LAS_10) } });
  assert.deepEqual(otro.prox, { a: doc.prox.a, b: doc.prox.b });
  // Solo el general: las cosas, intactas. Y null lo quita.
  const { doc: sinGeneral } = documentoCon(otro, envio(2, { general: null }), ahora);
  assert.deepEqual([sinGeneral.v, sinGeneral.general, sinGeneral.cosas], [3, null, otro.cosas]);
  const { doc: conGeneral } = documentoCon(sinGeneral, envio(3, { general: A_LAS_10 }), ahora);
  assert.deepEqual([conGeneral.v, conGeneral.general, conGeneral.cosas], [4, A_LAS_10, otro.cosas]);
  // Lo que se pone y se quita a la vez, se queda puesto.
  assert.deepEqual(Object.keys(documentoCon(conGeneral, envio(4, { poner: { a: cosa('Leche') }, quitar: ['a'] }), ahora).doc.cosas), ['b', 'a']);
});

test('documentoCon: sobre otra versión, no escribe (409)', () => {
  const doc = documento({ a: cosa('Leche') });
  assert.equal(documentoCon(doc, envio(0, { poner: { z: cosa('Otra') } }), LAS_8 + 60000), null);
  assert.equal(documentoCon(doc, envio(2), LAS_8 + 60000), null);
  assert.notEqual(documentoCon(doc, envio(1), LAS_8 + 60000), null);
});

test('documentoCon: repetir un envío no sube la versión ni mueve lo que toca', () => {
  const poner = { a: cosa('Leche'), b: cosa('Pan') };
  const doc = documento(poner, DIARIO);
  // El mismo envío justo después de la hora (el aviso de las 9:00 aún no ha salido): no se lo salta.
  const { doc: igual, rechazadas } = documentoCon(doc, envio(1, { poner, general: DIARIO }), LAS_9 + 30000);
  assert.deepEqual([igual.v, igual.prox, rechazadas], [1, doc.prox, []]);
  assert.deepEqual(publicoDe(igual), publicoDe(doc));
  assert.equal(igual.actualizado, LAS_9 + 30000);
  // Y uno vacío (solo la zona, la misma), tampoco.
  assert.equal(documentoCon(doc, envio(1), LAS_9 + 30000).doc.v, 1);
});

test('documentoCon: lo que cambia de recordatorio se recalcula desde ahora; lo demás conserva su aviso', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan'), d: cosa('Agua') });
  const ahora = LAS_9 + 30000; // Las 9:00:30: lo de las 9:00 aún no ha salido.
  const { doc: otro } = documentoCon(doc, envio(1, { poner: { a: cosa('Leche', A_LAS_10), b: cosa('Pan', DIARIO, true), c: cosa('Sal') } }), ahora);
  assert.equal(otro.v, 2);
  assert.deepEqual(otro.prox, { a: utc('2026-11-10T09:00'), c: utc('2026-11-11T08:00'), d: LAS_9 }); // «d», sin nombrar, sigue pendiente de las 9:00.
  // Se desmarca «b»: vuelve a esperar aviso, desde ahora.
  const { doc: vuelta } = documentoCon(otro, envio(2, { poner: { b: cosa('Pan') } }), utc('2026-11-10T12:00'));
  assert.equal(vuelta.prox.b, utc('2026-11-11T08:00'));
  // Cambiar solo el texto no mueve su aviso.
  const { doc: texto } = documentoCon(vuelta, envio(3, { poner: { a: cosa('Leche de avena', A_LAS_10) } }), utc('2026-11-10T08:59'));
  assert.deepEqual([texto.v, texto.prox.a], [4, utc('2026-11-10T09:00')]);
  // Lo que se quita deja de esperar.
  assert.deepEqual(Object.keys(documentoCon(texto, envio(4, { quitar: ['a', 'd'] }), utc('2026-11-10T12:00')).doc.prox).sort(), ['b', 'c']);
});

test('documentoCon: el límite es de 100 cosas; la que no cabe va en «rechazadas» y lo demás entra', () => {
  const lleno = documento(muchas(MAX_COSAS));
  assert.equal(Object.keys(lleno.cosas).length, MAX_COSAS);
  // Sustituir una que ya está no cuenta; una nueva no cabe.
  const { doc, rechazadas } = documentoCon(lleno, envio(1, { poner: { c5: cosa('Otra vez'), nueva: cosa('No cabe') } }), LAS_8);
  assert.deepEqual([rechazadas, doc.cosas.c5.texto, Object.keys(doc.cosas).length, doc.v], [['nueva'], 'Otra vez', MAX_COSAS, 2]);
  // Quitando una en el mismo envío, sí.
  const hueco = documentoCon(lleno, envio(1, { poner: { nueva: cosa('Ahora sí') }, quitar: ['c0'] }), LAS_8);
  assert.deepEqual([hueco.rechazadas, hueco.doc.cosas.nueva.texto, Object.keys(hueco.doc.cosas).length], [[], 'Ahora sí', MAX_COSAS]);
  // De una vez, 105: entran las 100 primeras. Y lo rechazado por no valer se suma a lo rechazado por no caber.
  const deGolpe = documentoCon(documentoVacio(), envio(0, { poner: { ...muchas(MAX_COSAS + 5), mala: { texto: 'Sin recordatorio', hecha: false } } }), LAS_8);
  assert.equal(Object.keys(deGolpe.doc.cosas).length, MAX_COSAS);
  assert.deepEqual(deGolpe.rechazadas, ['mala', 'c100', 'c101', 'c102', 'c103', 'c104']);
  // Si nada de lo enviado entra, no hay versión nueva.
  assert.equal(documentoCon(lleno, envio(1, { poner: { nueva: cosa('No cabe') } }), LAS_8).doc.v, 1);
});

test('documentoCon: al cambiar de zona se recalcula en la nueva, pero lo que ya tocaba y no ha salido no se pierde', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan', { hora: '21:00', frec: 'dia' }) });
  // A las 9:02 de Madrid el aviso de «a» (9:00) sigue sin salir y la app llega desde Nueva York.
  const ahora = LAS_9 + 120000;
  const { doc: lejos } = documentoCon(doc, envio(1, {}, 'America/New_York'), ahora);
  assert.deepEqual([lejos.v, lejos.zona], [2, 'America/New_York']);
  assert.equal(lejos.prox.a, LAS_9); // Atrasado: sale en cuanto pase la función…
  assert.equal(lejos.prox.b, utc('2026-11-11T02:00')); // …y lo que aún no tocaba, a las 21:00 de allí.
  // Una vez avisado, «a» sigue ya con el reloj de Nueva York.
  assert.equal(confirmarAvisos(reclamarAvisos(lejos, ahora).doc, [{ id: 'a', de: LAS_9 }], ahora).prox.a, utc('2026-11-10T14:00'));
  // Antes de la hora, en cambio, se recalcula todo.
  assert.equal(documentoCon(doc, envio(1, {}, 'America/New_York'), LAS_8).doc.prox.a, utc('2026-11-10T14:00'));
});

test('documentoCon: guardar no saca de la pausa (despertar es otra cosa)', () => {
  const pausado = documentoPausado(documento({ a: cosa('Leche') }), LAS_9);
  const { doc } = documentoCon(pausado, envio(1, { poner: { b: cosa('Pan') } }), LAS_9 + 60000);
  assert.deepEqual([doc.pausado, doc.v], [LAS_9, 2]);
  assert.equal(agendaDe(doc, LAS_9 + 60000), null);
});

test('documentoConHecha: «Hecho» en el aviso; una sola vez y solo si está', () => {
  const doc = reclamarAvisos(documento({ a: cosa('Leche'), b: cosa('Pan') }), LAS_9).doc;
  const hecha = documentoConHecha(doc, 'a', LAS_9 + 60000);
  assert.equal(hecha.v, 2);
  assert.equal(hecha.cosas.a.hecha, true);
  assert.deepEqual([Object.keys(hecha.prox), Object.keys(hecha.turno)], [['b'], ['b']]); // Ni espera aviso ni turno.
  assert.deepEqual([doc.actualizado, hecha.actualizado], [LAS_8, LAS_9 + 60000]); // Su dueño lo ha tocado: la retención cuenta desde ahora.
  assert.equal(doc.cosas.a.hecha, false); // El de antes no se toca.
  assert.equal(documentoConHecha(hecha, 'a', LAS_9 + 60000), null);
  assert.equal(documentoConHecha(doc, 'no-esta', LAS_9), null);
  assert.equal(documentoConHecha(doc, '__proto__', LAS_9), null);
  assert.equal(documentoConHecha(doc, 'constructor', LAS_9), null);
  // La app, que aún la tenía pendiente, escribe sobre la versión vieja: no puede (tiene que mirar antes).
  assert.equal(documentoCon(hecha, envio(1, { poner: { a: cosa('Leche') } }), LAS_9), null);
});

test('reclamarAvisos y confirmarAvisos: se toma con un turno, y el siguiente no se apunta hasta confirmar', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan', { hora: '09:05', frec: 'dia' }), d: cosa('Hecha', DIARIO, true) });
  assert.equal(tocaAvisar(doc, utc('2026-11-10T07:59:50')), false);
  assert.equal(reclamarAvisos(doc, utc('2026-11-10T07:59:50')), null);
  const ahora = LAS_9 + 4000;
  assert.equal(tocaAvisar(doc, ahora), true);
  const reclamo = reclamarAvisos(doc, ahora);
  assert.deepEqual(reclamo.avisar, [{ id: 'a', texto: 'Leche', de: LAS_9 }]);
  assert.deepEqual(reclamo.doc.prox, doc.prox); // Sigue pendiente: aún no se sabe si llega.
  assert.deepEqual(reclamo.doc.turno, { a: { de: LAS_9, hasta: ahora + CESION } });
  assert.deepEqual(reclamo.doc.enviados, { dia: '20261110', n: 1 });
  assert.equal(reclamo.doc.v, doc.v); // Lo compartido no cambia.
  assert.equal(agendaDe(reclamo.doc, ahora), '202611100800'); // Y sigue en la agenda, vencido.
  // Confirmado: el siguiente, mañana; el turno se suelta.
  const confirmado = confirmarAvisos(reclamo.doc, [{ id: 'a', de: LAS_9 }], ahora);
  assert.deepEqual(confirmado.prox, { a: utc('2026-11-11T08:00'), b: utc('2026-11-10T08:05') });
  assert.deepEqual(confirmado.turno, {});
  assert.equal(agendaDe(confirmado, ahora), '202611100805');
  // Confirmar otra vez, o algo que no se tomó, no cambia nada.
  assert.equal(confirmarAvisos(confirmado, [{ id: 'a', de: LAS_9 }], ahora), null);
  assert.equal(confirmarAvisos(confirmado, [{ id: 'no-esta', de: LAS_9 }, { id: '__proto__', de: 1 }], ahora), null);
  assert.equal(confirmarAvisos(doc, [], ahora), null);
});

test('reclamarAvisos: un turno vivo no se vuelve a tomar; caducado, sí (y se repite el aviso)', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan') });
  const primera = reclamarAvisos(doc, LAS_9 + 2000);
  assert.deepEqual(primera.avisar.map((c) => c.id), ['a', 'b']);
  // Otra ejecución mientras dura el turno (Netlify la lanzó dos veces): nada que tocar.
  assert.equal(reclamarAvisos(primera.doc, LAS_9 + 30000), null);
  assert.equal(reclamarAvisos(primera.doc, LAS_9 + 2000 + CESION - 1), null);
  // Solo «a» llegó a confirmarse. Caducado el turno de «b», la siguiente lo toma otra vez.
  const medio = confirmarAvisos(primera.doc, [{ id: 'a', de: LAS_9 }], LAS_9 + 2000);
  const despues = LAS_9 + 5 * 60000;
  const segunda = reclamarAvisos(medio, despues);
  assert.deepEqual(segunda.avisar, [{ id: 'b', texto: 'Pan', de: LAS_9 }]);
  assert.deepEqual(segunda.doc.turno, { b: { de: LAS_9, hasta: despues + CESION } });
  // Repetir no cuenta dos veces para el tope del día.
  assert.deepEqual(segunda.doc.enviados, { dia: '20261110', n: 2 });
  // Ahora sí llega: el siguiente se cuenta desde ahora, no desde su hora (un solo aviso atrasado, no dos).
  assert.deepEqual(confirmarAvisos(segunda.doc, segunda.avisar, despues).prox, { a: utc('2026-11-11T08:00'), b: utc('2026-11-11T08:00') });
});

test('confirmarAvisos: si entretanto la cosa cambió, no se le pisa lo nuevo', () => {
  const reclamo = reclamarAvisos(documento({ a: cosa('Leche'), b: cosa('Pan'), c: cosa('Sal') }), LAS_9);
  // Mientras se envía: «a» cambia de hora, «b» se marca hecha y «c» se quita.
  const { doc } = documentoCon(reclamo.doc, envio(1, { poner: { a: cosa('Leche', A_LAS_10), b: cosa('Pan', DIARIO, true) }, quitar: ['c'] }), LAS_9 + 1000);
  assert.deepEqual([doc.prox, doc.turno], [{ a: utc('2026-11-10T09:00') }, {}]);
  assert.equal(confirmarAvisos(doc, reclamo.avisar, LAS_9 + 2000), null);
  // Y si solo cambió el texto, el turno sigue y la confirmación vale.
  const { doc: texto } = documentoCon(reclamo.doc, envio(1, { poner: { a: cosa('Leche entera') } }), LAS_9 + 1000);
  assert.deepEqual(texto.turno, reclamo.doc.turno);
  assert.equal(confirmarAvisos(texto, [{ id: 'a', de: LAS_9 }], LAS_9 + 2000).prox.a, utc('2026-11-11T08:00'));
});

test('reclamarAvisos: con retraso se avisa igual; con más de seis horas, se salta sin enviar', () => {
  const doc = documento({ a: cosa('Leche') });
  const tarde = reclamarAvisos(doc, LAS_9 + CADUCA);
  assert.deepEqual(tarde.avisar, [{ id: 'a', texto: 'Leche', de: LAS_9 }]);
  const caducado = reclamarAvisos(doc, LAS_9 + CADUCA + 1000);
  assert.deepEqual(caducado.avisar, []);
  assert.deepEqual([caducado.doc.prox, caducado.doc.turno, caducado.doc.enviados.n], [{ a: utc('2026-11-11T08:00') }, {}, 0]);
  // Tras varios días parada: ni un aviso por día ni uno viejísimo; se apunta el siguiente.
  const dias = reclamarAvisos(doc, utc('2026-11-13T18:00'));
  assert.deepEqual([dias.avisar, dias.doc.prox], [[], { a: utc('2026-11-14T08:00') }]);
  // Un turno que caducó sin confirmarse y ya es demasiado viejo también se salta.
  const tomado = reclamarAvisos(doc, LAS_9).doc;
  const viejo = reclamarAvisos(tomado, LAS_9 + CADUCA + 1000);
  assert.deepEqual([viejo.avisar, viejo.doc.turno, viejo.doc.prox], [[], {}, { a: utc('2026-11-11T08:00') }]);
});

test('reclamarAvisos: como mucho 40 de una vez, lo más atrasado primero; el resto sigue pendiente', () => {
  const doc = documento({ ...muchas(MAX_POR_TANDA + 15), antes: cosa('La primera', { hora: '08:55', frec: 'dia' }) });
  const reclamo = reclamarAvisos(doc, LAS_9);
  assert.equal(reclamo.avisar.length, MAX_POR_TANDA);
  assert.equal(reclamo.avisar[0].id, 'antes');
  assert.equal(Object.keys(reclamo.doc.turno).length, MAX_POR_TANDA);
  assert.equal(reclamo.doc.enviados.n, MAX_POR_TANDA);
  // Confirmadas esas, la tanda siguiente toma las 16 que quedaban.
  const resto = reclamarAvisos(confirmarAvisos(reclamo.doc, reclamo.avisar, LAS_9), LAS_9 + 5 * 60000);
  assert.equal(resto.avisar.length, 16);
  assert.equal(new Set([...reclamo.avisar, ...resto.avisar].map((c) => c.id)).size, MAX_POR_TANDA + 16);
  assert.equal(reclamarAvisos(doc, LAS_9, 3).avisar.length, 3);
});

test('reclamarAvisos: el tope del día; por encima, se saltan (y al día siguiente vuelve a contar)', () => {
  const doc = { ...documento({ a: cosa('Leche'), b: cosa('Pan'), c: cosa('Sal') }), enviados: { dia: '20261110', n: MAX_AL_DIA - 2 } };
  const reclamo = reclamarAvisos(doc, LAS_9);
  assert.deepEqual(reclamo.avisar.map((c) => c.id), ['a', 'b']);
  assert.deepEqual(reclamo.doc.enviados, { dia: '20261110', n: MAX_AL_DIA });
  assert.equal(reclamo.doc.prox.c, utc('2026-11-11T08:00')); // «c» ya no cabe hoy: sin aviso, a mañana.
  assert.equal(Object.hasOwn(reclamo.doc.turno, 'c'), false);
  // Lo que ya estaba tomado y no llegó a confirmarse se repite aunque el tope esté cubierto.
  const repetido = reclamarAvisos(reclamo.doc, LAS_9 + 5 * 60000);
  assert.deepEqual([repetido.avisar.map((c) => c.id), repetido.doc.enviados.n], [['a', 'b'], MAX_AL_DIA]);
  // Mañana, la cuenta empieza de cero.
  const manana = reclamarAvisos(confirmarAvisos(repetido.doc, repetido.avisar, LAS_9 + 5 * 60000), utc('2026-11-11T08:00'));
  assert.deepEqual([manana.avisar.length, manana.doc.enviados], [3, { dia: '20261111', n: 3 }]);
});

test('reclamarAvisos: lo que sobra en «prox» (cosas hechas o que ya no están) se limpia', () => {
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan') });
  const raro = { ...doc, cosas: { a: cosa('Leche', DIARIO, true) }, turno: { b: { de: LAS_9, hasta: LAS_9 + CESION } } };
  const reclamo = reclamarAvisos(raro, LAS_8);
  assert.deepEqual([reclamo.avisar, reclamo.doc.prox, reclamo.doc.turno], [[], {}, {}]);
});

test('pausa: sale de la agenda y suelta los turnos; al despertar, lo reciente sale con retraso y lo viejo se salta', () => {
  const doc = reclamarAvisos(documento({ a: cosa('Leche'), b: cosa('Pan', { hora: '15:00', frec: 'dia' }) }), LAS_9).doc;
  const pausado = documentoPausado(doc, LAS_9 + 1000);
  assert.deepEqual([pausado.pausado, pausado.turno, pausado.prox, pausado.v], [LAS_9 + 1000, {}, doc.prox, doc.v]);
  assert.equal(agendaDe(pausado, LAS_9 + 1000), null);
  assert.equal(documentoPausado(pausado, LAS_9 + 2000), null);
  assert.equal(documentoDespierto(doc), null);
  // Despierta a las 15:10 de Madrid: lo de las 15:00 sale (diez minutos tarde); lo de las 9:00, que ya pasa de seis horas, no.
  const ahora = utc('2026-11-10T14:10');
  const despierto = documentoDespierto(pausado);
  assert.equal(despierto.pausado, null);
  assert.equal(agendaDe(despierto, ahora), '202611101410');
  const reclamo = reclamarAvisos(despierto, ahora);
  assert.deepEqual(reclamo.avisar, [{ id: 'b', texto: 'Pan', de: utc('2026-11-10T14:00') }]);
  assert.equal(reclamo.doc.prox.a, utc('2026-11-11T08:00'));
  // El día de la limpieza: treinta días después.
  assert.equal(RETENCION, 30 * 24 * 3600 * 1000);
  assert.equal(diaDeLimpieza(utc('2026-11-10T08:00')), '20261210');
});

test('pausa de días: al volver, lo que tocaba en las últimas seis horas sale (también lo del minuto que se atiende); lo anterior, no', () => {
  const despierto = documentoDespierto(documentoPausado(documento({ a: cosa('Leche') }), LAS_9 + 1000));
  assert.deepEqual(despierto.prox, { a: LAS_9 }); // Lo que tenía apuntado al quedarse en pausa: lo del día 10.
  const HOY = utc('2026-11-12T08:00'); // Dos días después, a las 9:00 de Madrid.
  // [cuándo lo atiende la primera pasada desde que volvió, lo que queda apuntado, ¿sale después?]
  const casos = [
    [HOY + 1000, HOY, true], // Volvió tres minutos antes de las 9:00: la pasada de las 9:00 ya lo ve.
    [HOY + 300, HOY, true], // Aunque la pasada arranque unas décimas después del minuto.
    [HOY + 3600 * 1000, HOY, true], // Una hora después: sale con retraso.
    [HOY + CADUCA - 10 * 60 * 1000, HOY, true], // Casi seis horas después: todavía (a la pasada siguiente aún no ha caducado).
    [HOY + CADUCA + 1000, utc('2026-11-13T08:00'), false], // Más de seis horas después: hasta mañana.
    [HOY - 4000, HOY, true], // Y la que arranca un instante antes de su minuto lo apunta para ese minuto.
  ];
  for (const [ahora, apuntado, sale] of casos) {
    const visto = reclamarAvisos(despierto, ahora);
    // Lo caducado se salta sin avisar ni gastar el tope del día, y lo siguiente se cuenta desde hace seis horas.
    assert.deepEqual([visto.avisar, visto.doc.prox, visto.doc.turno, visto.doc.enviados.n], [[], { a: apuntado }, {}, 0], String(ahora - HOY));
    const despues = reclamarAvisos(visto.doc, ahora + 5 * 60 * 1000);
    assert.deepEqual(despues ? despues.avisar : [], sale ? [{ id: 'a', texto: 'Leche', de: HOY }] : [], String(ahora - HOY));
    if (!sale) continue;
    // Una vez confirmado, lo siguiente es lo de mañana: uno solo por la pausa, no uno por cada día que faltó.
    assert.deepEqual(confirmarAvisos(despues.doc, despues.avisar, ahora + 5 * 60 * 1000).prox, { a: utc('2026-11-13T08:00') });
  }
  // Lo de cada semana, igual: el martes siguiente una hora tarde, sale; el miércoles ya no, hasta el otro martes.
  const semanal = documentoDespierto(documentoPausado(documento({ a: cosa('Basura', { hora: '09:00', frec: 'semana', dia: 2 }) }), LAS_9 + 1000));
  assert.deepEqual(reclamarAvisos(semanal, utc('2026-11-17T09:00')).doc.prox, { a: utc('2026-11-17T08:00') });
  assert.deepEqual(reclamarAvisos(semanal, utc('2026-11-18T09:00')).doc.prox, { a: utc('2026-11-24T08:00') });
});

test('limpiezaDe y caducado: lo que espera avisos no se borra; en pausa o sin nada pendiente, a los treinta días de seguir igual', () => {
  const pendiente = documento({ a: cosa('Leche'), b: cosa('Pan', DIARIO, true) });
  assert.equal(limpiezaDe(pendiente), null);
  assert.equal(caducado(pendiente, '20991231', true), false); // Ni sin dispositivos: de eso se ocupa la pausa.
  // Sin nada pendiente (todo hecho; sin cosas; solo el recordatorio de todas): desde la última vez que su dueño lo cambió.
  const hecho = documentoConHecha(pendiente, 'a', utc('2026-11-12T23:30'));
  const vacio = documentoCon(pendiente, envio(1, { quitar: ['a', 'b'] }), utc('2026-11-12T23:30')).doc;
  const soloGeneral = documentoCon(documentoVacio(), envio(0, { general: DIARIO }), utc('2026-11-12T23:30')).doc;
  for (const doc of [hecho, vacio, soloGeneral]) {
    assert.deepEqual(doc.prox, {});
    assert.equal(limpiezaDe(doc), '20261212');
    assert.deepEqual([caducado(doc, '20261211', true), caducado(doc, '20261212', true), caducado(doc, '20270101', true)], [false, true, true]);
    assert.equal(caducado(doc, '20261212', false), true); // Tener dispositivos le da igual: no hay nada que enviarles.
  }
  // Se guarda otra vez sin que nada cambie: vuelve a contar. Y si algo vuelve a estar pendiente, deja de contar.
  assert.equal(limpiezaDe(documentoCon(hecho, envio(2), utc('2026-11-20T10:00')).doc), '20261220');
  assert.equal(limpiezaDe(documentoCon(hecho, envio(2, { poner: { a: cosa('Leche') } }), utc('2026-11-20T10:00')).doc), null);
  // En pausa: desde que empezó la pausa, y solo si la sesión sigue sin dispositivos (con ellos, lo que toca es despertarla).
  const pausado = documentoPausado(pendiente, LAS_9);
  assert.equal(limpiezaDe(pausado), '20261210');
  assert.deepEqual([caducado(pausado, '20261209', true), caducado(pausado, '20261210', true), caducado(pausado, '20261210', false)], [false, true, false]);
  // En pausa y, además, ya sin nada pendiente: manda la pausa mientras dure; al despertar, lo que lleve sin tocarse.
  const pausadoYHecho = documentoConHecha(pausado, 'a', utc('2026-11-12T10:00'));
  assert.deepEqual([limpiezaDe(pausadoYHecho), limpiezaDe(documentoDespierto(pausadoYHecho))], ['20261210', '20261212']);
  assert.equal(caducado(documentoVacio(), '20261210', true), true); // Lo que no se entiende como documento (o una marca vieja) no se guarda.
});

test('agendaDe: el minuto del aviso más cercano; si ya pasó, el de ahora (una clave recién puesta nunca es vieja)', () => {
  assert.equal(agendaDe(documentoVacio(), LAS_8), null);
  const doc = documento({ a: cosa('Leche'), b: cosa('Pan', { hora: '08:15', frec: 'dia' }) });
  assert.equal(agendaDe(doc, LAS_8), '202611100715');
  assert.equal(agendaDe(doc, utc('2026-11-10T07:14:59')), '202611100715');
  assert.equal(agendaDe(doc, utc('2026-11-10T07:15:00')), '202611100715');
  assert.equal(agendaDe(doc, utc('2026-11-10T07:42:10')), '202611100742'); // Atrasado: el minuto de ahora.
  assert.equal(agendaDe(documento({ a: cosa('Hecha', DIARIO, true) }), LAS_8), null); // Sin avisos que esperar.
});

test('documentoDe: lo guardado, saneado; lo raro no rompe', () => {
  assert.deepEqual(documentoDe(null), documentoVacio());
  assert.deepEqual(documentoDe('x'), documentoVacio());
  assert.deepEqual(documentoDe([1, 2]), documentoVacio());
  const doc = { ...reclamarAvisos(documento({ a: cosa('Leche'), b: cosa('Pan', DIARIO, true) }, DIARIO), LAS_9).doc, nacido: 'abc123', agenda: '202611100800', pausado: null };
  assert.deepEqual(documentoDe(JSON.parse(JSON.stringify(doc))), doc); // Ida y vuelta por el almacén: igual.
  assert.deepEqual(documentoDe(JSON.parse(JSON.stringify({ ...doc, pausado: LAS_9 }))).pausado, LAS_9);
  const raro = documentoDe({
    nacido: 7, v: -3, zona: 'Marte/Olimpo', general: { hora: '09:00', frec: 'una', fecha: '2026-11-12' },
    cosas: { a: cosa('Leche'), mala: { texto: 3 }, b: cosa('Pan', DIARIO, true), una: cosa('Cita', { hora: '09:00', frec: 'una', fecha: '2026-11-12' }), ['x'.repeat(101)]: cosa('Id largo') },
    prox: { a: 'ayer', b: 5, c: 7, una: 9 }, turno: { a: { de: 1, hasta: 2 }, b: 'x' }, agenda: 9, pausado: 'ayer', enviados: { dia: 3, n: -1 }, actualizado: 'x',
  });
  assert.deepEqual(raro, { ...documentoVacio(), cosas: { a: cosa('Leche'), b: cosa('Pan', DIARIO, true) } });
  // Los turnos y los avisos pendientes solo valen para cosas que siguen esperando.
  const medio = documentoDe({ cosas: { a: cosa('Leche'), b: cosa('Pan') }, prox: { a: LAS_9 }, turno: { a: { de: LAS_9, hasta: LAS_9 + 1, sobra: 1 }, b: { de: 1, hasta: 2 }, c: { de: 1, hasta: 2 } }, cosasDeMas: 1 });
  assert.deepEqual([medio.prox, medio.turno], [{ a: LAS_9 }, { a: { de: LAS_9, hasta: LAS_9 + 1 } }]);
  assert.equal(Object.hasOwn(medio, 'cosasDeMas'), false);
});
