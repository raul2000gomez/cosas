/* Pruebas de netlify/lib/recordatorios.mjs (leer, guardar, «Hecho», la pasada programada, la pausa, la retención y
   la limpieza) sobre un almacén de mentira:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CADUCA, CESION, envioDe } from '../../netlify/lib/recordar.mjs';
import {
  despertar, guardarRecordatorios, leerRecordatorios, limpiar, marcarHecha, olvidarRecordatorios, pasada, tocaLimpiar,
} from '../../netlify/lib/recordatorios.mjs';
import { almacenFalso } from './almacen-falso.mjs';

const MADRID = 'Europe/Madrid';
const utc = (texto) => Date.parse(`${texto}Z`);
const DIARIO = { hora: '09:00', frec: 'dia' };
const A_LAS_21 = { hora: '21:00', frec: 'dia' };
const cosa = (texto, r = DIARIO, hecha = false) => ({ texto, hecha, r });
const LAS_8 = utc('2026-11-10T07:00'); // Las 8:00 en Madrid: a las 9:00 (08:00 UTC) toca lo diario.
const LAS_9 = utc('2026-11-10T08:00');
const MINUTOS = 60000;
const HOY_A_LAS_9 = 'agenda/202611100800/u1';
const MANANA_A_LAS_9 = 'agenda/202611110800/u1';
const SIN_DOCUMENTO = { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} };
const UN_MES_DESPUES = utc('2026-12-10T03:00:02'); // La limpieza del día en que caduca lo que nadie toca desde el 10 de noviembre.

const guardar = (store, uid, siV, cambio, ahora = LAS_8) => guardarRecordatorios(store, uid, envioDe({ siV, zona: MADRID, ...cambio }), ahora);
const conDispositivo = (store, uid, huella = 'h1') => store.setJSON(`dispositivos/${uid}/${huella}`, { endpoint: `https://fcm.googleapis.com/fcm/send/${uid}-${huella}`, desde: 1 });

/**
 * Una pasada con envíos de mentira: devuelve la cuenta y lo que ha salido («uid:id»). Los dispositivos de cada sesión
 * son los que haya en el almacén. «falla(uid, cosa)»: ese aviso no llega; «alEnviar»: algo que pasa mientras se envía;
 * «cortar»: la ejecución se muere justo después de enviar, sin llegar a confirmar.
 */
async function pasar(store, ahora, opciones = {}) {
  const enviados = [];
  const cuenta = await pasada(store, ahora, {
    dispositivos: opciones.dispositivos || (async (uid) => store.claves(`dispositivos/${uid}/`)),
    enviar: async (uid, destinos, cosas) => {
      assert.ok(destinos.length > 0);
      if (opciones.alEnviar) await opciones.alEnviar(uid, cosas);
      const llegan = cosas.filter((c) => !(opciones.falla && opciones.falla(uid, c)));
      enviados.push(...llegan.map((c) => `${uid}:${c.id}`));
      if (opciones.cortar) throw new Error('Se acabó el tiempo');
      return llegan.map(({ id, de }) => ({ id, de }));
    },
    seguir: opciones.seguir,
  });
  return { enviados: enviados.sort(), ...cuenta };
}

/** La regla de la agenda: un documento que espera algún aviso (y no está en pausa) tiene puesta la clave que dice tener. */
function enAgenda(store, uid) {
  const doc = store.json(`recordar/${uid}`);
  if (!doc || doc.pausado !== null || !Object.keys(doc.prox).length) return true;
  return typeof doc.agenda === 'string' && store.datos.has(`agenda/${doc.agenda}/${uid}`);
}

/** Ejecuta «hacer» sin que lo que escriba en la consola (los fallos que se esperan) ensucie la salida; devuelve cuántos fueron. */
async function callando(hacer) {
  const { error, warn } = console;
  let avisos = 0;
  console.error = () => { avisos += 1; };
  console.warn = () => { avisos += 1; };
  try {
    await hacer();
  } finally {
    Object.assign(console, { error, warn });
  }
  return avisos;
}

// ---------- Leer, guardar, «Hecho», olvidar ----------

test('leer: sin documento, nacido null y v 0; y no escribe nada', async () => {
  const store = almacenFalso();
  assert.deepEqual(await leerRecordatorios(store, 'u1'), { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} });
  assert.deepEqual(store.claves(), []);
});

test('guardar: documento nuevo con su «nacido», su versión y su clave en la agenda; repetirlo no cambia nada', async () => {
  const store = almacenFalso();
  const poner = { a: cosa('Leche'), b: cosa('Pan', { hora: '08:30', frec: 'dia' }) };
  const primero = await guardar(store, 'u1', 0, { poner, general: DIARIO });
  assert.match(primero.nacido, /^[A-Za-z0-9_-]{12}$/);
  assert.deepEqual(primero, { nacido: primero.nacido, v: 1, rechazadas: [] });
  assert.deepEqual(store.claves(), ['agenda/202611100730/u1', 'recordar/u1']);
  assert.equal(store.json('recordar/u1').agenda, '202611100730');
  assert.deepEqual(await leerRecordatorios(store, 'u1'), { nacido: primero.nacido, v: 1, zona: MADRID, general: DIARIO, cosas: poner });
  // Lo mismo otra vez (la respuesta se perdió y la app repite): ni versión nueva, ni otro nacido, ni claves nuevas.
  assert.deepEqual(await guardar(store, 'u1', 1, { poner, general: DIARIO }, LAS_8 + 1000), primero);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100730/u1']);
});

test('guardar: es un cambio (lo que no se nombra no se toca) y dice lo que rechaza', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan') }, general: DIARIO });
  const resultado = await guardar(store, 'u1', 1, { poner: { c: cosa('Sal'), mala: { texto: 'Sin hora', hecha: false } }, quitar: ['a'] });
  assert.deepEqual([resultado.v, resultado.rechazadas], [2, ['mala']]);
  const leido = await leerRecordatorios(store, 'u1');
  assert.deepEqual([leido.general, leido.cosas], [DIARIO, { b: cosa('Pan'), c: cosa('Sal') }]);
});

test('guardar: sobre una versión que ya no es la que hay, no escribe y dice lo que hay', async () => {
  const store = almacenFalso();
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const antes = store.json('recordar/u1');
  assert.deepEqual(await guardar(store, 'u1', 0, { poner: { z: cosa('Otra') } }), { conflicto: { nacido, v: 1, zona: MADRID, general: null, cosas: { a: cosa('Leche') } } });
  assert.deepEqual(store.json('recordar/u1'), antes);
  // Sobre un documento que ya no existe (se olvidó): lo que hay es nada, y la app vuelve a empezar.
  assert.deepEqual(await guardar(store, 'u2', 3, {}), { conflicto: { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} } });
  assert.deepEqual(store.claves('recordar/'), ['recordar/u1']);
});

test('guardar: pone siempre su clave de la agenda (aunque el minuto no cambie) y nunca quita ninguna', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]);
  // La clave se ha perdido: basta guardar otra vez, aunque no cambie nada, para que vuelva.
  await store.delete(HOY_A_LAS_9);
  assert.equal((await guardar(store, 'u1', 1, {})).v, 1);
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]);
  // Cambia de hora: clave nueva, y la de antes se queda (la quitará la pasada cuando la encuentre).
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche', A_LAS_21) } });
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9, 'agenda/202611102000/u1']);
  assert.equal(store.json('recordar/u1').agenda, '202611102000');
  // Sin avisos que esperar: el documento no tiene clave, y tampoco se quita ninguna.
  await guardar(store, 'u1', 2, { poner: { a: cosa('Leche', A_LAS_21, true) } });
  assert.equal(store.json('recordar/u1').agenda, null);
  assert.equal(store.claves('agenda/').length, 2);
});

test('guardar: dos sesiones no se pisan', async () => {
  const store = almacenFalso();
  const uno = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const otro = await guardar(store, 'u2', 0, { poner: { a: cosa('Pan', { hora: '10:00', frec: 'dia' }) } });
  assert.notEqual(uno.nacido, otro.nacido);
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9, 'agenda/202611100900/u2']);
  assert.equal((await leerRecordatorios(store, 'u1')).cosas.a.texto, 'Leche');
  assert.equal((await leerRecordatorios(store, 'u2')).cosas.a.texto, 'Pan');
});

test('guardar: si otro escribe entretanto, se vuelve a mirar (y entonces es un conflicto)', async () => {
  const store = almacenFalso();
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  store.antesDeEscribir(/^recordar\//, () => marcarHecha(store, 'u1', 'a', LAS_8)); // «Hecho» en un aviso justo entonces.
  const resultado = await guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } });
  assert.deepEqual(resultado, { conflicto: { nacido, v: 2, zona: MADRID, general: null, cosas: { a: cosa('Leche', DIARIO, true) } } });
  assert.deepEqual(store.json('recordar/u1').cosas, { a: cosa('Leche', DIARIO, true) }); // Lo hecho no se pierde.
});

test('leer: vuelve a poner la clave de la agenda (si se perdió, se arregla al abrir la app) sin tocar el documento', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const etiqueta = store.datos.get('recordar/u1').etag;
  await store.delete(HOY_A_LAS_9);
  assert.equal((await leerRecordatorios(store, 'u1')).v, 1);
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]);
  assert.equal(store.datos.get('recordar/u1').etag, etiqueta);
  // Y si al reponerla falla el almacén, la lectura vale igual.
  const poner = store.set;
  store.set = async () => { throw new Error('Almacén caído'); };
  assert.equal(await callando(async () => assert.equal((await leerRecordatorios(store, 'u1')).v, 1)), 1);
  store.set = poner;
});

test('marcarHecha: la cosa queda hecha y deja de esperar aviso; repetido o desconocido, nada', async () => {
  const store = almacenFalso();
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan', { hora: '10:00', frec: 'dia' }) } });
  assert.equal(await marcarHecha(store, 'u1', 'a', LAS_8), true);
  assert.deepEqual(await leerRecordatorios(store, 'u1'), { nacido, v: 2, zona: MADRID, general: null, cosas: { a: cosa('Leche', DIARIO, true), b: cosa('Pan', { hora: '10:00', frec: 'dia' }) } });
  assert.equal(store.json('recordar/u1').agenda, '202611100900'); // Ahora lo más cercano es «b».
  assert.ok(enAgenda(store, 'u1'));
  assert.equal(await marcarHecha(store, 'u1', 'a', LAS_8), false);
  assert.equal(await marcarHecha(store, 'u1', 'no-esta', LAS_8), false);
  assert.equal(await marcarHecha(store, 'nadie', 'a', LAS_8), false);
  assert.deepEqual(store.claves('recordar/'), ['recordar/u1']); // No se crea nada para quien no tiene.
  assert.equal(await marcarHecha(store, 'u1', 'b', LAS_8), true);
  assert.equal(store.json('recordar/u1').agenda, null);
});

test('marcarHecha: si otro escribe entretanto, se repite sobre lo nuevo', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  store.antesDeEscribir(/^recordar\//, () => guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } }));
  assert.equal(await marcarHecha(store, 'u1', 'a', LAS_8), true);
  assert.deepEqual((await leerRecordatorios(store, 'u1')).cosas, { a: cosa('Leche', DIARIO, true), b: cosa('Pan') });
});

test('olvidar: borra el documento y nada más; el siguiente es otro (otro «nacido», y la versión empieza de cero)', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  const antes = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  await guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } });
  await olvidarRecordatorios(store, 'u1');
  await olvidarRecordatorios(store, 'u1'); // Sin documento tampoco falla.
  assert.deepEqual(store.claves(), [HOY_A_LAS_9, 'dispositivos/u1/h1']);
  assert.deepEqual(await leerRecordatorios(store, 'u1'), { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} });
  const despues = await guardar(store, 'u1', 0, { poner: { c: cosa('Sal') } });
  assert.equal(despues.v, 1);
  assert.notEqual(despues.nacido, antes.nacido);
});

test('sin etiqueta no se escribe: nunca se guarda sin comparar', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const leer = store.getWithMetadata;
  store.getWithMetadata = async (...argumentos) => ({ ...(await leer(...argumentos)), etag: undefined });
  await assert.rejects(() => marcarHecha(store, 'u1', 'a', LAS_8), /Sin etiqueta/);
  store.getWithMetadata = leer;
  assert.equal((await leerRecordatorios(store, 'u1')).cosas.a.hecha, false);
});

// ---------- La pasada ----------

test('pasada: avisa de lo que toca, apunta el siguiente y no repite; lo de después no se toca', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan'), c: cosa('Sal', { hora: '09:05', frec: 'dia' }), d: cosa('Hecha', DIARIO, true) } });
  assert.deepEqual(await pasar(store, utc('2026-11-10T07:59:30')), { enviados: [], sesiones: 0, avisos: 0, pausadas: 0 });
  assert.deepEqual(await pasar(store, LAS_9 + 3000), { enviados: ['u1:a', 'u1:b'], sesiones: 1, avisos: 2, pausadas: 0 });
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100805/u1']); // La de las 9:00, ya atendida, se quita.
  assert.deepEqual(store.json('recordar/u1').turno, {});
  assert.deepEqual(await pasar(store, LAS_9 + 40000), { enviados: [], sesiones: 0, avisos: 0, pausadas: 0 }); // Otra ejecución en el mismo minuto.
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 3000)).enviados, ['u1:c']);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
  assert.equal((await leerRecordatorios(store, 'u1')).v, 1); // Avisar no cambia lo que la app comparte.
  // Al día siguiente, otra vez.
  assert.deepEqual((await pasar(store, utc('2026-11-11T08:00:03'))).enviados, ['u1:a', 'u1:b']);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611110805/u1']);
});

test('pasada: la que arranca unos segundos antes de su minuto atiende igual lo de ese minuto', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  assert.deepEqual((await pasar(store, utc('2026-11-10T07:59:54'))).enviados, []);
  assert.deepEqual((await pasar(store, utc('2026-11-10T07:59:57'))).enviados, ['u1:a']);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]); // Y el siguiente es mañana, no otra vez hoy.
  assert.deepEqual((await pasar(store, LAS_9 + 3000)).enviados, []);
});

test('pasada: «Hecho» y lo que se marca en la app dejan de avisarse; «Mantener» (no hacer nada) no', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan'), c: cosa('Sal') } });
  assert.equal((await pasar(store, LAS_9 + 3000)).avisos, 3);
  await marcarHecha(store, 'u1', 'a', LAS_9 + 60000); // «Hecho» en su aviso.
  await guardar(store, 'u1', 2, { poner: { b: cosa('Pan', DIARIO, true) } }, utc('2026-11-10T12:00')); // «b», en la app.
  assert.deepEqual((await pasar(store, utc('2026-11-11T08:00:03'))).enviados, ['u1:c']);
  // Se desmarca «b»: vuelve a avisarse.
  await guardar(store, 'u1', 3, { poner: { b: cosa('Pan') } }, utc('2026-11-11T12:00'));
  assert.deepEqual((await pasar(store, utc('2026-11-12T08:00:03'))).enviados, ['u1:b', 'u1:c']);
});

test('pasada: cada sesión a su hora, varias a la vez', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3']) await conDispositivo(store, uid);
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  await guardar(store, 'u2', 0, { poner: { a: cosa('Pan') } });
  await guardar(store, 'u3', 0, { poner: { a: cosa('Sal', { hora: '09:30', frec: 'dia' }) } });
  assert.deepEqual(await pasar(store, LAS_9 + 3000), { enviados: ['u1:a', 'u2:a'], sesiones: 2, avisos: 2, pausadas: 0 });
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100830/u3', MANANA_A_LAS_9, 'agenda/202611110800/u2']);
});

test('pasada: lo que no llega se repite cuando caduca su turno; lo que llegó, no', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan') } });
  // A las 9:00 el servicio push no admite el aviso de «a»: solo «b» se da por avisada.
  assert.deepEqual(await pasar(store, LAS_9 + 2000, { falla: (uid, c) => c.id === 'a' }), { enviados: ['u1:b'], sesiones: 1, avisos: 1, pausadas: 0 });
  let doc = store.json('recordar/u1');
  assert.deepEqual([doc.prox, doc.turno], [{ a: LAS_9, b: utc('2026-11-11T08:00') }, { a: { de: LAS_9, hasta: LAS_9 + 2000 + CESION } }]);
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]); // Sigue en la agenda, vencida.
  // Otra ejecución enseguida (Netlify la repite): el turno sigue vivo y no envía nada.
  assert.deepEqual((await pasar(store, LAS_9 + 40000)).enviados, []);
  // La de las 9:05: el turno caducó y «a» sale otra vez; «b», que llegó, no.
  assert.deepEqual(await pasar(store, LAS_9 + 5 * MINUTOS + 2000), { enviados: ['u1:a'], sesiones: 1, avisos: 1, pausadas: 0 });
  doc = store.json('recordar/u1');
  assert.deepEqual([doc.prox, doc.turno], [{ a: utc('2026-11-11T08:00'), b: utc('2026-11-11T08:00') }, {}]);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
  assert.deepEqual((await pasar(store, LAS_9 + 10 * MINUTOS + 2000)).enviados, []);
});

test('pasada: si falla una y otra vez se sigue intentando seis horas; después se salta y mañana vuelve a tocar', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const falla = () => true;
  for (let i = 0; i < 4; i += 1) assert.equal((await pasar(store, LAS_9 + i * 5 * MINUTOS + 2000, { falla })).avisos, 0);
  assert.equal(store.json('recordar/u1').enviados.n, 1); // Los reintentos no gastan el tope del día.
  assert.ok(enAgenda(store, 'u1'));
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100815/u1']); // Siempre una sola clave, la de la última vez.
  // A las seis horas justas aún se intenta (y ahora llega)…
  const otro = almacenFalso();
  await conDispositivo(otro, 'u1');
  await guardar(otro, 'u1', 0, { poner: { a: cosa('Leche') } });
  await pasar(otro, LAS_9 + 2000, { falla });
  assert.deepEqual((await pasar(otro, LAS_9 + CADUCA)).enviados, ['u1:a']);
  // …y pasado ese tiempo ya no: se apunta el de mañana sin enviar nada.
  assert.deepEqual((await pasar(store, LAS_9 + CADUCA + 5 * MINUTOS)).enviados, []);
  assert.deepEqual(store.json('recordar/u1').prox, { a: utc('2026-11-11T08:00') });
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
  assert.deepEqual((await pasar(store, utc('2026-11-11T08:00:02'))).enviados, ['u1:a']);
});

test('pasada: si la ejecución se corta después de enviar y antes de confirmar, la siguiente repite (dos veces, nunca ninguna)', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  let primera;
  assert.equal(await callando(async () => { primera = await pasar(store, LAS_9 + 2000, { cortar: true }); }), 1);
  assert.deepEqual(primera, { enviados: ['u1:a'], sesiones: 0, avisos: 0, pausadas: 0 });
  assert.equal(store.json('recordar/u1').prox.a, LAS_9); // Sin confirmar: sigue pendiente.
  assert.ok(enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:a']);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
});

test('pasada: más de 40 cosas a la vez: 40 ahora y el resto en la siguiente', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: Object.fromEntries(Array.from({ length: 55 }, (_, i) => [`c${i}`, cosa(`Cosa ${i}`)])) });
  const primera = await pasar(store, LAS_9 + 2000);
  assert.deepEqual([primera.avisos, primera.enviados.length], [40, 40]);
  assert.ok(enAgenda(store, 'u1'));
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]); // Quedan 15 por avisar: sigue vencida.
  const segunda = await pasar(store, LAS_9 + 5 * MINUTOS + 2000);
  assert.deepEqual([segunda.avisos, segunda.enviados.length], [15, 15]);
  assert.equal(new Set([...primera.enviados, ...segunda.enviados]).size, 55);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
  assert.deepEqual((await pasar(store, LAS_9 + 10 * MINUTOS + 2000)).enviados, []);
});

test('pasada: si la app guarda mientras se toma el turno, se vuelve a mirar; y lo marcado mientras se envía no revive', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan'), c: cosa('Sal') } });
  const ahora = LAS_9 + 3000;
  // Justo cuando la pasada va a tomar el turno, la app marca «a» como hecha.
  store.antesDeEscribir(/^recordar\//, () => guardar(store, 'u1', 1, { poner: { a: cosa('Leche', DIARIO, true) } }, ahora));
  // Y mientras envía, alguien pulsa «Hecho» en el aviso de «b» (que ya le había llegado).
  const resultado = await pasar(store, ahora, { alEnviar: () => marcarHecha(store, 'u1', 'b', ahora) });
  assert.deepEqual(resultado.enviados, ['u1:b', 'u1:c']);
  const doc = store.json('recordar/u1');
  assert.deepEqual([doc.cosas.a.hecha, doc.cosas.b.hecha, doc.prox, doc.turno], [true, true, { c: utc('2026-11-11T08:00') }, {}]);
  assert.deepEqual((await pasar(store, ahora + 20000)).enviados, []);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
});

test('pasada: sin tiempo, lo que queda espera en la agenda; y no empieza siempre por la misma sesión', async () => {
  const preparar = async () => {
    const store = almacenFalso();
    for (const uid of ['u1', 'u2', 'u3', 'u4', 'u5']) {
      await conDispositivo(store, uid);
      await guardar(store, uid, 0, { poner: { a: cosa('Leche', { hora: '09:00', frec: 'dia' }) } });
    }
    return store;
  };
  // Solo da tiempo a una: las otras cuatro siguen vencidas y salen en la pasada siguiente.
  const store = await preparar();
  let turnos = 0;
  const corta = await pasar(store, LAS_9 + 2000, { seguir: () => (turnos += 1) <= 1 });
  assert.deepEqual([corta.sesiones, corta.avisos], [1, 1]);
  assert.equal(store.claves('agenda/202611100800/').length, 4);
  const resto = await pasar(store, LAS_9 + 5 * MINUTOS + 2000);
  assert.deepEqual([resto.sesiones, resto.avisos], [4, 4]);
  assert.equal(new Set([...corta.enviados, ...resto.enviados]).size, 5);
  // La primera en ser atendida cambia de una pasada a otra.
  const primeras = new Set();
  for (let minuto = 0; minuto < 12; minuto += 1) {
    const otro = await preparar();
    let cuenta = 0;
    const { enviados } = await pasar(otro, LAS_9 + minuto * MINUTOS + 2000, { seguir: () => (cuenta += 1) <= 1 });
    assert.equal(enviados.length, 1);
    primeras.add(enviados[0]);
  }
  assert.ok(primeras.size >= 3, [...primeras].join(' '));
});

test('pasada: atiende a varias sesiones a la vez, pero no a todas', async () => {
  const store = almacenFalso();
  for (let i = 0; i < 20; i += 1) {
    await conDispositivo(store, `u${i}`);
    await guardar(store, `u${i}`, 0, { poner: { a: cosa('Leche') } });
  }
  let ahoraMismo = 0;
  let maximo = 0;
  const resultado = await pasar(store, LAS_9 + 2000, {
    alEnviar: async () => {
      ahoraMismo += 1;
      maximo = Math.max(maximo, ahoraMismo);
      await new Promise((resolver) => { setTimeout(resolver, 5); });
      ahoraMismo -= 1;
    },
  });
  assert.deepEqual([resultado.sesiones, resultado.avisos], [20, 20]);
  assert.equal(maximo, 6);
});

test('pasada: un documento estropeado, o una sesión cuyos dispositivos no se pueden leer, no para a las demás', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3']) {
    await conDispositivo(store, uid);
    await guardar(store, uid, 0, { poner: { a: cosa('Leche') } });
  }
  await store.set('recordar/u1', '{esto no es JSON');
  const dispositivos = async (uid) => {
    if (uid === 'u3') throw new Error('Almacén caído');
    return store.claves(`dispositivos/${uid}/`);
  };
  let resultado;
  assert.equal(await callando(async () => { resultado = await pasar(store, LAS_9 + 3000, { dispositivos }); }), 2);
  assert.deepEqual(resultado, { enviados: ['u2:a'], sesiones: 1, avisos: 1, pausadas: 0 });
  // A «u3» no se le ha tocado nada (ni turno ni pausa: que falle la lectura no es no tener dispositivos) y sale en la siguiente.
  assert.deepEqual([store.json('recordar/u3').turno, store.json('recordar/u3').pausado], [{}, null]);
  await store.delete('recordar/u1');
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS)).enviados, ['u3:a']);
});

// ---------- La agenda: claves que sobran y cruces ----------

test('agenda: las claves de un documento que no existe solo se quitan cuando ya son viejas', async () => {
  const store = almacenFalso();
  await store.set('agenda/202611100755/fantasma', '1');
  await store.set('agenda/202611100700/fantasma', '1');
  await pasar(store, LAS_9 + 3000);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100755/fantasma']); // La de hace una hora, fuera; la de hace cinco minutos, aún no.
  await pasar(store, LAS_9 + 5 * MINUTOS + 3000);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100755/fantasma']);
  await pasar(store, LAS_9 + 10 * MINUTOS + 3000);
  assert.deepEqual(store.claves('agenda/'), []);
  assert.deepEqual(store.claves('recordar/'), []); // Y no se crea ningún documento por mirar.
});

test('agenda: una clave de más (de un minuto que ya no es el del documento) no avisa de nada y se quita cuando es vieja', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche', A_LAS_21) } }); // Queda la clave de las 9:00, que ya no es la suya.
  assert.deepEqual(await pasar(store, LAS_9 + 3000), { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9, 'agenda/202611102000/u1']); // Reciente: podría ser de alguien que está guardando.
  await pasar(store, LAS_9 + 15 * MINUTOS);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611102000/u1']);
  assert.deepEqual((await pasar(store, utc('2026-11-10T20:00:02'))).enviados, ['u1:a']);
});

test('agenda: un documento cuya clave no está donde toca se recoloca', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', A_LAS_21) } });
  await store.delete('agenda/202611102000/u1');
  await store.set('agenda/202611100700/u1', '1'); // Queda una clave que no es la suya…
  await store.setJSON('recordar/u1', { ...store.json('recordar/u1'), agenda: '202611100700' }); // …y el documento cree que sí.
  assert.deepEqual(await pasar(store, LAS_9 + 3000), { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611102000/u1']);
  assert.equal(store.json('recordar/u1').agenda, '202611102000');
});

test('cruce 1: alguien guarda justo cuando pasa la programada (clave puesta, documento aún no): la clave no se pierde', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  // Un instante antes de las 9:00 se pone el primer recordatorio, para las 9:00. Entre la clave y el documento, pasa
  // la programada de las 9:00: ve la clave, no encuentra documento y no tiene nada que avisar.
  let enMedio;
  store.antesDeEscribir(/^recordar\//, async () => {
    assert.deepEqual(store.claves(), [HOY_A_LAS_9, 'dispositivos/u1/h1']);
    enMedio = await pasar(store, LAS_9 + 50);
  });
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } }, LAS_9 - 50);
  assert.deepEqual(enMedio, { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  assert.equal(store.json('recordar/u1').agenda, '202611100800');
  assert.ok(store.datos.has(HOY_A_LAS_9) && enAgenda(store, 'u1'));
  // Y la pasada siguiente avisa (cinco minutos tarde, pero avisa).
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:a']);
});

test('cruce 1, con el documento viejo: cambia la hora a «ahora mismo» mientras la programada mira', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', A_LAS_21) } });
  let enMedio;
  store.antesDeEscribir(/^recordar\//, async () => { enMedio = await pasar(store, LAS_9 + 50); });
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche') } }, LAS_9 - 50);
  assert.deepEqual(enMedio.enviados, []); // Leyó el documento de antes: a las 21:00, nada que avisar aún.
  assert.ok(store.datos.has(HOY_A_LAS_9) && enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:a']);
});

test('cruce 2: dos que guardan se cruzan moviendo la clave de un minuto a otro y de vuelta: la del documento está siempre', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  // Uno la lleva a las 21:00 y, cuando ya ha guardado, otro la devuelve a las 9:00: nadie quita la clave de las 9:00.
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche', A_LAS_21) } });
  await guardar(store, 'u1', 2, { poner: { a: cosa('Leche') } });
  assert.equal(store.json('recordar/u1').agenda, '202611100800');
  assert.ok(enAgenda(store, 'u1'));
  // Y cruzándose de verdad: el segundo guarda entero entre la clave y el documento del primero, que entonces se
  // encuentra con otra versión (409) y deja una clave de más, la de las 21:00; la del documento sigue ahí.
  store.antesDeEscribir(/^recordar\//, () => guardar(store, 'u1', 3, { poner: { a: cosa('Leche', { hora: '10:00', frec: 'dia' }) } }));
  assert.equal((await guardar(store, 'u1', 3, { poner: { a: cosa('Leche', A_LAS_21) } })).conflicto.v, 4);
  assert.equal(store.json('recordar/u1').agenda, '202611100900');
  assert.ok(enAgenda(store, 'u1'));
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9, 'agenda/202611100900/u1', 'agenda/202611102000/u1']);
  // Las que sobran no avisan de nada; solo la pasada las quita, y la del documento avisa a su hora.
  assert.deepEqual((await pasar(store, LAS_9 + 2000)).enviados, []);
  assert.deepEqual((await pasar(store, utc('2026-11-10T09:00:02'))).enviados, ['u1:a']);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611102000/u1', 'agenda/202611110900/u1']);
  assert.deepEqual((await pasar(store, utc('2026-11-10T20:00:02'))).enviados, []);
  await pasar(store, utc('2026-11-10T20:15:02'));
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611110900/u1']);
});

test('cruce 2, con la pasada: la app guarda mientras la pasada confirma y mueve la clave: la del documento está al acabar', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const ahora = LAS_9 + 2000;
  // Enviado el aviso de «a», y antes de que la pasada apunte el siguiente, la app añade «b» para las 9:05.
  const alEnviar = () => store.antesDeEscribir(/^recordar\//, () => guardar(store, 'u1', 1, { poner: { b: cosa('Pan', { hora: '09:05', frec: 'dia' }) } }, ahora));
  assert.deepEqual((await pasar(store, ahora, { alEnviar })).enviados, ['u1:a']);
  const doc = store.json('recordar/u1');
  assert.deepEqual([doc.prox, doc.agenda], [{ a: utc('2026-11-11T08:00'), b: utc('2026-11-10T08:05') }, '202611100805']);
  assert.ok(enAgenda(store, 'u1'));
  // La de las 9:00, atendida, se quita. Sobra la de mañana, que la pasada puso antes de saber que había algo antes.
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611100805/u1', MANANA_A_LAS_9]);
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:b']);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
});

test('cruce 3: alguien guarda justo después de que la pasada confirme y antes de que quite la clave atendida: la clave vuelve', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  // Dos segundos antes de las 9:00 la app añade «b», también para las 9:00: su clave es la misma de las 9:00 que la
  // pasada, que ya ha avisado de «a», está a punto de quitar por atendida. Y entra entera justo antes de que la quite.
  store.antesDeBorrar(/^agenda\/202611100800\/u1$/, () => guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } }, LAS_9 - 2000));
  assert.deepEqual((await pasar(store, LAS_9 + 2000)).enviados, ['u1:a']);
  const doc = store.json('recordar/u1');
  assert.deepEqual([doc.v, doc.prox, doc.agenda], [2, { a: utc('2026-11-11T08:00'), b: LAS_9 }, '202611100800']);
  // La pasada la quitó, pero como era reciente guardó otra vez: y guardar siempre pone la clave de lo que haya.
  assert.ok(store.datos.has(HOY_A_LAS_9) && enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:b']);
  assert.deepEqual(store.claves('agenda/'), [MANANA_A_LAS_9]);
});

test('cruce 3, a medias: quien ya había puesto esa clave y aún no había escrito no entra sin volver a mirar, y la repone', async () => {
  // También con un almacén cuya etiqueta sale del contenido: lo que la pasada guarda de más no es, byte a byte, lo que
  // ya había (lleva su marca), así que la etiqueta cambia igual y a quien iba a escribir se le hace mirar otra vez.
  for (const store of [almacenFalso(), almacenFalso({ etiquetaDelContenido: true })]) {
    await conDispositivo(store, 'u1');
    await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
    // Lo mismo, pero más fino: cuando la pasada va a quitar la clave, la app ya ha leído el documento (el de después de
    // confirmar) y ha puesto la suya, la misma; su documento no llega hasta que la pasada ha acabado del todo.
    let pasado;
    let app;
    store.antesDeBorrar(/^agenda\/202611100800\/u1$/, () => new Promise((yaHaPuestoSuClave) => {
      store.antesDeEscribir(/^recordar\//, () => {
        yaHaPuestoSuClave();
        return pasado;
      });
      app = guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } }, LAS_9 - 2000);
    }));
    pasado = pasar(store, LAS_9 + 2000);
    assert.deepEqual((await pasado).enviados, ['u1:a']);
    assert.deepEqual(await app, { nacido: store.json('recordar/u1').nacido, v: 2, rechazadas: [] });
    // Sin ese guardar de más de la pasada, el documento de la app habría entrado a la primera con su clave ya quitada.
    const doc = store.json('recordar/u1');
    assert.deepEqual([doc.prox, doc.agenda], [{ a: utc('2026-11-11T08:00'), b: LAS_9 }, '202611100800']);
    assert.equal(doc.reafirmado, undefined); // La marca de la pasada no sobrevive a la escritura siguiente.
    assert.ok(store.datos.has(HOY_A_LAS_9) && enAgenda(store, 'u1'));
    assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS + 2000)).enviados, ['u1:b']);
  }
});

test('la pasada solo guarda de más si ha quitado una clave reciente; y si entretanto olvidaron el documento, no lo crea', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', A_LAS_21) } });
  // Nada que avisar y nada que quitar: ni una escritura.
  const etiqueta = store.datos.get('recordar/u1').etag;
  await store.set('agenda/202611100700/u1', '1'); // Una clave vieja que sobra: se quita sin más (nadie guarda con una de hace una hora).
  await pasar(store, LAS_9 + 2000);
  assert.deepEqual([store.claves('agenda/'), store.datos.get('recordar/u1').etag], [['agenda/202611102000/u1'], etiqueta]);
  // A su hora avisa y quita la clave atendida, que es reciente: guarda una vez más. Lo normal (nadie se ha cruzado)
  // le cuesta una sola escritura, sin volver a leer.
  let lecturas = 0;
  const leerCon = store.getWithMetadata;
  store.getWithMetadata = (...argumentos) => {
    lecturas += 1;
    return leerCon(...argumentos);
  };
  assert.deepEqual((await pasar(store, utc('2026-11-10T20:00:02'))).enviados, ['u1:a']);
  assert.equal(lecturas, 2); // Las de tomar el turno y confirmar.
  assert.notEqual(store.datos.get('recordar/u1').etag, etiqueta);
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611112000/u1']);
  // Y si justo antes de quitar la clave la sesión olvida sus recordatorios, no se crea un documento por guardar de más.
  store.antesDeBorrar(/^agenda\/202611112000\/u1$/, () => olvidarRecordatorios(store, 'u1'));
  assert.deepEqual((await pasar(store, utc('2026-11-11T20:00:02'))).enviados, ['u1:a']);
  assert.deepEqual(store.claves('recordar/'), []);
});

// ---------- Sin dispositivos: pausa, despertar y limpieza ----------

test('pausa: a quien le toca un aviso y no tiene dispositivos se le deja en pausa, con su clave de limpieza', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  assert.deepEqual(await pasar(store, LAS_9 + 3000), { enviados: [], sesiones: 1, avisos: 0, pausadas: 1 });
  const doc = store.json('recordar/u1');
  assert.deepEqual([doc.pausado, doc.agenda, doc.prox, doc.v], [LAS_9 + 3000, null, { a: LAS_9 }, 1]);
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1']); // Treinta días después.
  // Su clave de la agenda no se quita en el acto (un alta podría estar poniéndola): se quita cuando ya es vieja.
  assert.deepEqual(store.claves('agenda/'), [HOY_A_LAS_9]);
  assert.deepEqual(await pasar(store, LAS_9 + 5 * MINUTOS), { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  await pasar(store, LAS_9 + 15 * MINUTOS);
  assert.deepEqual(store.claves('agenda/'), []);
  // En pausa no gasta ejecuciones, y guardar o leer sin dispositivos no la despierta.
  assert.equal((await guardar(store, 'u1', 1, { poner: { b: cosa('Pan', A_LAS_21) } }, LAS_9 + 20 * MINUTOS)).v, 2);
  await leerRecordatorios(store, 'u1', LAS_9 + 20 * MINUTOS);
  assert.equal(await despertar(store, 'u1', LAS_9 + 20 * MINUTOS), false);
  assert.deepEqual([store.json('recordar/u1').pausado, store.claves('agenda/')], [LAS_9 + 3000, []]);
  assert.deepEqual(await pasar(store, utc('2026-11-10T20:00:02')), { enviados: [], sesiones: 0, avisos: 0, pausadas: 0 });
});

test('despertar: con un dispositivo vuelve a la agenda, y lo que le tocó hace poco sale con retraso', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan', { hora: '08:00', frec: 'dia' }) } }, utc('2026-11-10T05:00'));
  // «b» tocaba a las 8:00 (07:00 UTC) y «a» a las 9:00: sin dispositivos, en pausa desde las 8:00.
  assert.equal((await pasar(store, utc('2026-11-10T07:00:02'))).pausadas, 1);
  assert.equal(await despertar(store, 'u1', LAS_9 + 20 * MINUTOS), false);
  assert.equal(await despertar(store, 'nadie', LAS_9 + 20 * MINUTOS), false);
  // A las 14:30 (13:30 UTC) activa las notificaciones en un dispositivo.
  const ahora = utc('2026-11-10T13:30');
  await conDispositivo(store, 'u1');
  assert.equal(await despertar(store, 'u1', ahora), true);
  assert.equal(await despertar(store, 'u1', ahora), false); // Ya lo estaba.
  assert.deepEqual([store.json('recordar/u1').pausado, store.json('recordar/u1').agenda], [null, '202611101330']);
  assert.ok(enAgenda(store, 'u1'));
  // La pasada siguiente: «a» (cinco horas y media tarde) sale; «b» (más de seis) se salta hasta mañana.
  assert.deepEqual(await pasar(store, ahora + 2 * MINUTOS), { enviados: ['u1:a'], sesiones: 1, avisos: 1, pausadas: 0 });
  assert.deepEqual(store.json('recordar/u1').prox, { a: utc('2026-11-11T08:00'), b: utc('2026-11-11T07:00') });
  assert.deepEqual(store.claves('agenda/'), ['agenda/202611110700/u1']);
});

test('despertar tras días en pausa: lo que toca en ese mismo rato no se pierde; sale en la pasada siguiente', async () => {
  for (const volver of [
    (store, ahora) => despertar(store, 'u1', ahora),
    (store, ahora) => leerRecordatorios(store, 'u1', ahora),
  ]) {
    const store = almacenFalso();
    await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
    assert.equal((await pasar(store, LAS_9 + 1000)).pausadas, 1);
    // Dos días después, tres minutos antes de las 9:00, activa las notificaciones (o abre la app, ya con dispositivo).
    await conDispositivo(store, 'u1');
    await volver(store, utc('2026-11-12T07:57'));
    assert.deepEqual([store.json('recordar/u1').pausado, store.json('recordar/u1').prox], [null, { a: LAS_9 }]);
    // La pasada de las 9:00 se encuentra apuntado lo de hace dos días: lo salta y apunta lo de hoy, que es de ahora mismo.
    assert.deepEqual(await pasar(store, utc('2026-11-12T08:00:01')), { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
    assert.deepEqual(store.json('recordar/u1').prox, { a: utc('2026-11-12T08:00') });
    assert.ok(enAgenda(store, 'u1'));
    // Y la siguiente lo envía (cinco minutos tarde, en vez de perderlo hasta mañana).
    assert.deepEqual(await pasar(store, utc('2026-11-12T08:05:01')), { enviados: ['u1:a'], sesiones: 1, avisos: 1, pausadas: 0 });
    assert.deepEqual(store.json('recordar/u1').prox, { a: utc('2026-11-13T08:00') });
    assert.deepEqual(store.claves('agenda/'), ['agenda/202611130800/u1']);
  }
});

test('despertar: también al leer y al guardar, si la sesión ya tiene dispositivo', async () => {
  for (const volver of [
    (store, ahora) => leerRecordatorios(store, 'u1', ahora),
    (store, ahora) => guardar(store, 'u1', 1, { poner: { b: cosa('Pan', A_LAS_21) } }, ahora),
  ]) {
    const store = almacenFalso();
    await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
    await pasar(store, LAS_9 + 3000);
    await pasar(store, LAS_9 + 15 * MINUTOS);
    assert.deepEqual([store.json('recordar/u1').pausado, store.claves('agenda/')], [LAS_9 + 3000, []]);
    await conDispositivo(store, 'u1');
    const ahora = LAS_9 + 20 * MINUTOS;
    await volver(store, ahora);
    assert.deepEqual([store.json('recordar/u1').pausado, store.claves('agenda/')], [null, ['agenda/202611100820/u1']]);
    assert.deepEqual((await pasar(store, ahora + 5 * MINUTOS)).enviados, ['u1:a']);
  }
});

test('cruce al despertar: la programada pasa entre la clave y el documento de quien despierta: la clave no se pierde', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  await pasar(store, LAS_9 + 3000);
  await pasar(store, LAS_9 + 15 * MINUTOS);
  assert.deepEqual(store.claves('agenda/'), []);
  await conDispositivo(store, 'u1');
  const ahora = LAS_9 + 20 * MINUTOS;
  // Aunque lo pendiente es de hace veinte minutos, la clave que pone quien despierta es de ahora: la pasada, que aún
  // ve el documento en pausa, no la toma por una clave vieja que sobra.
  let enMedio;
  store.antesDeEscribir(/^recordar\//, async () => { enMedio = await pasar(store, ahora + 1000); });
  assert.equal(await despertar(store, 'u1', ahora), true);
  assert.deepEqual(enMedio, { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  assert.ok(store.datos.has('agenda/202611100820/u1') && enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, ahora + 5 * MINUTOS)).enviados, ['u1:a']);
});

test('cruce al pausar: un alta que llega mientras la pasada deja la sesión en pausa no se queda dormida', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  const ahora = LAS_9 + 3000;
  // La pasada ya ha visto que no hay dispositivos. Antes de que guarde la pausa llega el alta: su «despertar» aún no
  // encuentra nada en pausa.
  store.antesDeEscribir(/^recordar\//, async () => {
    await conDispositivo(store, 'u1');
    assert.equal(await despertar(store, 'u1', ahora), false);
  });
  assert.equal((await pasar(store, ahora)).pausadas, 1);
  assert.equal(store.json('recordar/u1').pausado, null); // La propia pasada vuelve a mirar y la despierta.
  assert.ok(store.datos.has(HOY_A_LAS_9) && enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, LAS_9 + 5 * MINUTOS)).enviados, ['u1:a']);
});

test('limpiar: borra solo los documentos que siguen en pausa y sin dispositivos cuando les llega el día', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3', 'u4', 'u5']) await guardar(store, uid, 0, { poner: { a: cosa('Leche') } });
  assert.equal((await pasar(store, LAS_9 + 3000)).pausadas, 5); // Ninguna tiene dispositivos: todas en pausa.
  // «u2» vuelve (activa las notificaciones); «u3» borra sus recordatorios; «u5» tiene dispositivo pero nadie la ha despertado.
  await conDispositivo(store, 'u2');
  assert.equal(await despertar(store, 'u2', LAS_9 + 60 * MINUTOS), true);
  await olvidarRecordatorios(store, 'u3');
  await conDispositivo(store, 'u5');
  // «u4» vuelve al día siguiente y se queda otra vez sin dispositivos: su pausa de verdad empieza entonces.
  await conDispositivo(store, 'u4');
  assert.equal(await despertar(store, 'u4', utc('2026-11-11T10:00')), true);
  await store.delete('dispositivos/u4/h1');
  assert.equal((await pasar(store, utc('2026-11-11T10:05:03'))).pausadas, 1);
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261210/u2', 'limpieza/20261210/u3', 'limpieza/20261210/u4', 'limpieza/20261210/u5', 'limpieza/20261211/u4']);

  // El día antes no toca a nadie.
  assert.equal(await limpiar(store, utc('2026-12-09T03:00:02')), 0);
  assert.equal(store.claves('limpieza/').length, 6);
  // El día 10: solo «u1» sigue en pausa, sin dispositivos y desde hace un mes.
  assert.equal(await limpiar(store, utc('2026-12-10T03:00:02')), 1);
  assert.deepEqual(store.claves('recordar/'), ['recordar/u2', 'recordar/u4', 'recordar/u5']);
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261211/u4']);
  // A «u5», que tenía a quién avisar y seguía dormida, la despierta: no se queda en pausa sin clave de limpieza.
  assert.deepEqual([store.json('recordar/u5').pausado, store.json('recordar/u5').agenda], [null, '202612100300']);
  assert.ok(enAgenda(store, 'u5'));
  assert.notEqual(store.json('recordar/u4').pausado, null);
  assert.equal(await limpiar(store, utc('2026-12-10T03:00:02')), 0); // Repetida (Netlify la lanza dos veces): nada más.
  // El día 11 le llega a «u4».
  assert.equal(await limpiar(store, utc('2026-12-11T03:00:02')), 1);
  assert.deepEqual(store.claves('recordar/'), ['recordar/u2', 'recordar/u5']);
  assert.deepEqual(store.claves('limpieza/'), []);
});

test('limpiar: una sesión que falla no para a las demás, y sin tiempo lo que queda espera a mañana', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3']) await guardar(store, uid, 0, { poner: { a: cosa('Leche') } });
  await pasar(store, LAS_9 + 3000);
  await store.set('recordar/u1', '{esto no es JSON');
  let turnos = 0;
  let borrados;
  assert.equal(await callando(async () => { borrados = await limpiar(store, utc('2026-12-10T03:00:02'), () => (turnos += 1) <= 2); }), 1);
  assert.equal(borrados, 1); // «u1» falla, «u2» se borra y para «u3» ya no hay tiempo.
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261210/u3']);
  assert.deepEqual(store.claves('recordar/'), ['recordar/u1', 'recordar/u3']);
});

// ---------- Sin nada pendiente: la retención ----------

test('retención: quien se queda sin nada pendiente (todo hecho, o sin cosas) deja su clave de limpieza; quien espera avisos, no', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  assert.deepEqual(store.claves('limpieza/'), []);
  // La app la marca hecha: ya no espera ningún aviso, y nadie lo miraría nunca. Clave para dentro de treinta días.
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche', DIARIO, true) } });
  assert.deepEqual([store.json('recordar/u1').agenda, store.claves('limpieza/')], [null, ['limpieza/20261210/u1']]);
  // «Hecho» en el aviso de lo último pendiente: igual, y cuenta desde ese momento.
  await guardar(store, 'u2', 0, { poner: { a: cosa('Pan') } });
  assert.equal(await marcarHecha(store, 'u2', 'a', utc('2026-11-12T08:01')), true);
  assert.equal(store.json('recordar/u2').actualizado, utc('2026-11-12T08:01'));
  // Y el que se queda sin cosas (se les quitó el recordatorio a todas).
  await guardar(store, 'u3', 0, { poner: { a: cosa('Sal') }, general: DIARIO });
  await guardar(store, 'u3', 1, { quitar: ['a'], general: null }, utc('2026-11-13T10:00'));
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261212/u2', 'limpieza/20261213/u3']);
  // Cada vez que se guarda sin nada pendiente, la clave del día que toque: una por día, no una por vez.
  await guardar(store, 'u1', 2, { poner: { z: cosa('Otra', DIARIO, true) } }, utc('2026-11-20T10:00'));
  await guardar(store, 'u1', 3, { poner: { z: cosa('Otra más', DIARIO, true) } }, utc('2026-11-20T18:00'));
  assert.deepEqual(store.claves('limpieza/').filter((clave) => clave.endsWith('/u1')), ['limpieza/20261210/u1', 'limpieza/20261220/u1']);
  // Vuelve a esperar avisos: ni clave nueva ni se quita ninguna (las quitará la limpieza cuando les llegue el día).
  await guardar(store, 'u1', 4, { poner: { a: cosa('Leche') } }, utc('2026-11-21T10:00'));
  assert.equal(store.claves('limpieza/').length, 4);
  assert.ok(enAgenda(store, 'u1'));
});

test('limpiar: borra el documento que lleva un mes sin nada pendiente y sin que nadie lo toque, tenga o no dispositivos; la app lo sube de nuevo', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', DIARIO, true), b: cosa('Pan') } });
  assert.equal((await pasar(store, LAS_9 + 2000)).avisos, 1);
  await marcarHecha(store, 'u1', 'b', LAS_9 + MINUTOS); // «Hecho» en su aviso: ya no queda nada pendiente.
  // Para la pasada ya no existe: no está en la agenda (la clave de mañana que quedó se quita al llegar su hora).
  assert.deepEqual(await pasar(store, utc('2026-11-11T08:00:02')), { enviados: [], sesiones: 1, avisos: 0, pausadas: 0 });
  await pasar(store, utc('2026-11-11T08:15:02'));
  assert.deepEqual(store.claves(), ['dispositivos/u1/h1', 'limpieza/20261210/u1', 'recordar/u1']);
  // Sus textos siguen ahí un mes, por si su dueño vuelve…
  assert.equal(await limpiar(store, utc('2026-12-09T03:00:02')), 0);
  assert.equal(store.json('recordar/u1').cosas.b.texto, 'Pan');
  // …y al mes se borran: ni documento, ni marca, ni claves. El dispositivo no se toca (es de los demás avisos).
  assert.equal(await limpiar(store, UN_MES_DESPUES), 1);
  assert.deepEqual(store.claves(), ['dispositivos/u1/h1']);
  assert.equal(await limpiar(store, UN_MES_DESPUES), 0); // Repetida: nada más.
  // Si su dueño vuelve, la app lee que ya no hay documento («nacido» null): lo que sabía de él no vale, y lo sube todo.
  const vuelta = utc('2026-12-20T10:00');
  assert.deepEqual(await leerRecordatorios(store, 'u1', vuelta), SIN_DOCUMENTO);
  assert.deepEqual(await guardar(store, 'u1', 2, { poner: { b: cosa('Pan') } }, vuelta), { conflicto: SIN_DOCUMENTO }); // Sobre la versión que recordaba: no.
  const otro = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', DIARIO, true), b: cosa('Pan') } }, vuelta);
  assert.equal(otro.v, 1);
  assert.notEqual(otro.nacido, nacido);
  assert.deepEqual((await pasar(store, utc('2026-12-21T08:00:02'))).enviados, ['u1:b']);
});

test('limpiar: no borra al que se tocó después ni al que volvió a esperar avisos; las claves que sobran se van al llegar su día', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3']) await guardar(store, uid, 0, { poner: { a: cosa('Leche', DIARIO, true) } });
  // «u1»: el 25 la app guarda otra cosa, también hecha. «u2»: el 20 desmarca la suya. «u3»: el 15 olvida sus recordatorios.
  await guardar(store, 'u1', 1, { poner: { b: cosa('Pan', DIARIO, true) } }, utc('2026-11-25T10:00'));
  await guardar(store, 'u2', 1, { poner: { a: cosa('Leche') } }, utc('2026-11-20T10:00'));
  await olvidarRecordatorios(store, 'u3');
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261210/u2', 'limpieza/20261210/u3', 'limpieza/20261225/u1']);
  // El día 10 no se borra nada: uno se tocó, otro espera avisos y el tercero ya no existe. Sus claves de ese día, fuera.
  assert.equal(await limpiar(store, UN_MES_DESPUES), 0);
  assert.deepEqual([store.claves('recordar/'), store.claves('limpieza/')], [['recordar/u1', 'recordar/u2'], ['limpieza/20261225/u1']]);
  // «u2» da por hecha la suya el 12 de diciembre: vuelve a contar desde entonces.
  await marcarHecha(store, 'u2', 'a', utc('2026-12-12T08:01'));
  // El 25 le llega el día a «u1»; a «u2», el 11 de enero.
  assert.equal(await limpiar(store, utc('2026-12-25T03:00:02')), 1);
  assert.deepEqual([store.claves('recordar/'), store.claves('limpieza/')], [['recordar/u2'], ['limpieza/20270111/u2']]);
  assert.equal(await limpiar(store, utc('2027-01-10T03:00:02')), 0);
  assert.equal(await limpiar(store, utc('2027-01-11T03:00:02')), 1);
  assert.deepEqual(store.claves().filter((clave) => !clave.startsWith('agenda/')), []);
});

test('el que se queda sin cosas no se borra en el acto: vacío ya no guarda textos, y dice a otro dispositivo que aquello se quitó', async () => {
  const store = almacenFalso();
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Comprar leche') }, general: DIARIO });
  assert.deepEqual(await guardar(store, 'u1', 1, { quitar: ['a'], general: null }, LAS_8 + MINUTOS), { nacido, v: 2, rechazadas: [] });
  // El mismo documento («nacido»), una versión más y sin la cosa: el otro dispositivo de la cuenta entiende que se le
  // quitó el recordatorio. Sin documento («nacido» null) entendería que hay que subirla otra vez.
  assert.deepEqual(await leerRecordatorios(store, 'u1', LAS_8 + 2 * MINUTOS), { nacido, v: 2, zona: MADRID, general: null, cosas: {} });
  assert.equal(JSON.stringify(store.json('recordar/u1')).includes('leche'), false); // El texto sí se ha ido ya.
  // Y al mes de no tocarlo nadie, también el documento.
  assert.equal(await limpiar(store, utc('2026-12-09T03:00:02')), 0);
  assert.equal(await limpiar(store, UN_MES_DESPUES), 1);
  assert.deepEqual(store.claves('recordar/'), []);
});

test('limpiar: en pausa y ya sin nada pendiente: sin dispositivos se borra al mes de la pausa; con ellos despierta y cuenta lo que lleve sin tocarse', async () => {
  const store = almacenFalso();
  for (const uid of ['u1', 'u2', 'u3']) await guardar(store, uid, 0, { poner: { a: cosa('Leche') } });
  assert.equal((await pasar(store, LAS_9 + 3000)).pausadas, 3);
  await pasar(store, LAS_9 + 15 * MINUTOS); // Sus claves de la agenda, ya viejas, se quitan.
  // Las tres marcan hecha su cosa desde la app, sin dispositivos (siguen en pausa): «u1» y «u2», el día 12; «u3», el mismo 10.
  await guardar(store, 'u1', 1, { poner: { a: cosa('Leche', DIARIO, true) } }, utc('2026-11-12T10:00'));
  await guardar(store, 'u2', 1, { poner: { a: cosa('Leche', DIARIO, true) } }, utc('2026-11-12T10:00'));
  await guardar(store, 'u3', 1, { poner: { a: cosa('Leche', DIARIO, true) } }, utc('2026-11-10T10:00'));
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261210/u2', 'limpieza/20261210/u3']);
  // «u2» y «u3» tienen dispositivo desde hace poco, pero nadie las despertó.
  await conDispositivo(store, 'u2');
  await conDispositivo(store, 'u3');
  // El día 10: «u1» (sin dispositivos) se borra. «u2» y «u3» despiertan; «u3» no espera nada ni se toca desde hace un
  // mes, y se borra en la misma limpieza (su clave de ahora es la misma que se está mirando); a «u2» le faltan dos días.
  assert.equal(await limpiar(store, UN_MES_DESPUES), 2);
  assert.deepEqual([store.claves('recordar/'), store.claves('limpieza/')], [['recordar/u2'], ['limpieza/20261212/u2']]);
  assert.equal(store.json('recordar/u2').pausado, null);
  assert.equal(await limpiar(store, utc('2026-12-12T03:00:02')), 1);
  assert.deepEqual(store.claves().filter((clave) => !clave.startsWith('dispositivos/')), []);
});

test('limpiar: atiende a varias sesiones a la vez, pero no a todas; y de cada una mira el documento una vez, tenga las claves vencidas que tenga', async () => {
  const store = almacenFalso();
  for (let i = 0; i < 20; i += 1) await guardar(store, `u${i}`, 0, { poner: { a: cosa('Leche', DIARIO, true) } });
  // «u0» se guardó además los dos días siguientes: tres claves, y solo la última dice cuándo caduca.
  await guardar(store, 'u0', 1, {}, utc('2026-11-11T10:00'));
  await guardar(store, 'u0', 1, {}, utc('2026-11-12T10:00'));
  let ahoraMismo = 0;
  let maximo = 0;
  const lecturas = new Map();
  const leerCon = store.getWithMetadata;
  store.getWithMetadata = async (clave, opciones) => {
    lecturas.set(clave, (lecturas.get(clave) || 0) + 1);
    ahoraMismo += 1;
    maximo = Math.max(maximo, ahoraMismo);
    await new Promise((resolver) => { setTimeout(resolver, 5); });
    ahoraMismo -= 1;
    return leerCon(clave, opciones);
  };
  assert.equal(await limpiar(store, UN_MES_DESPUES), 19);
  assert.equal(maximo, 6);
  assert.deepEqual([store.claves('recordar/'), store.claves('limpieza/')], [['recordar/u0'], ['limpieza/20261211/u0', 'limpieza/20261212/u0']]);
  lecturas.clear();
  assert.equal(await limpiar(store, utc('2026-12-12T03:00:02')), 1);
  assert.equal(lecturas.get('recordar/u0'), 1); // Dos claves vencidas, una sola mirada.
  assert.deepEqual(store.claves(), []);
});

// ---------- Borrar: lo que se cruza con la limpieza ----------

test('cruce al borrar: la app guarda entre que la limpieza mira el documento y lo borra: no se borra, y su aviso sale', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', DIARIO, true) } });
  // La limpieza ya ha leído el documento (un mes sin nada pendiente) y va a borrarlo. Justo entonces la app desmarca «a».
  store.antesDeEscribir(/^recordar\//, async () => assert.equal((await guardar(store, 'u1', 1, { poner: { a: cosa('Leche') } }, UN_MES_DESPUES)).v, 2));
  assert.equal(await limpiar(store, UN_MES_DESPUES), 0);
  const doc = store.json('recordar/u1');
  assert.deepEqual([doc.nacido, doc.v, doc.cosas, doc.prox], [nacido, 2, { a: cosa('Leche') }, { a: utc('2026-12-10T08:00') }]);
  assert.ok(enAgenda(store, 'u1'));
  // A su hora, el aviso sale. Y la clave de limpieza, que se quedó, no borra nada al día siguiente: se quita y ya.
  assert.deepEqual((await pasar(store, utc('2026-12-10T08:00:02'))).enviados, ['u1:a']);
  assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1']);
  assert.equal(await limpiar(store, utc('2026-12-11T03:00:02')), 0);
  assert.deepEqual([store.claves('limpieza/'), store.claves('recordar/')], [[], ['recordar/u1']]);
});

test('cruce al borrar, en pausa: un alta que despierta la sesión cuando la limpieza va a borrarla la salva', async () => {
  const store = almacenFalso();
  await guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } });
  assert.equal((await pasar(store, LAS_9 + 3000)).pausadas, 1);
  // Un mes después, la limpieza ya ha visto que sigue en pausa y sin dispositivos. Antes de que la borre, vuelve.
  store.antesDeEscribir(/^recordar\//, async () => {
    await conDispositivo(store, 'u1');
    assert.equal(await despertar(store, 'u1', UN_MES_DESPUES), true);
  });
  assert.equal(await limpiar(store, UN_MES_DESPUES), 0);
  assert.deepEqual([store.json('recordar/u1').pausado, store.json('recordar/u1').cosas], [null, { a: cosa('Leche') }]);
  assert.ok(enAgenda(store, 'u1'));
  // El aviso de hace un mes ya no sale (caducó); el de hoy, sí.
  assert.deepEqual((await pasar(store, utc('2026-12-10T03:05:02'))).enviados, []);
  assert.deepEqual((await pasar(store, utc('2026-12-10T08:00:02'))).enviados, ['u1:a']);
});

test('cruce al borrar: quien llega mientras la limpieza borra espera a que acabe en vez de escribir sobre lo que se va a quitar', async () => {
  const store = almacenFalso();
  await conDispositivo(store, 'u1');
  const { nacido } = await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', DIARIO, true) } });
  // La limpieza ha cambiado el documento por su marca y va a quitarla. En ese instante se abre la app: lee que ya no
  // hay documento y lo sube todo de nuevo, con «a» otra vez pendiente.
  let app;
  store.antesDeBorrar(/^recordar\/u1$/, async () => {
    assert.deepEqual(store.json('recordar/u1'), { borrando: UN_MES_DESPUES }); // De los textos ya no queda nada.
    assert.deepEqual(await leerRecordatorios(store, 'u1', UN_MES_DESPUES + 500), SIN_DOCUMENTO);
    app = guardar(store, 'u1', 0, { poner: { a: cosa('Leche') } }, UN_MES_DESPUES + 500);
    await new Promise((resolver) => { setTimeout(resolver, 30); });
    // La app espera. Si hubiera escrito sobre la marca, la limpieza se lo llevaría ahora por delante, y con un 200 en la mano.
    assert.deepEqual(store.json('recordar/u1'), { borrando: UN_MES_DESPUES });
  });
  assert.equal(await limpiar(store, UN_MES_DESPUES), 1);
  // Borrado del todo, lo de la app entra: es otro documento, está en la agenda y avisa.
  const otro = await app;
  assert.deepEqual([otro.v, otro.rechazadas], [1, []]);
  assert.notEqual(otro.nacido, nacido);
  assert.deepEqual([store.json('recordar/u1').nacido, store.json('recordar/u1').cosas], [otro.nacido, { a: cosa('Leche') }]);
  assert.ok(enAgenda(store, 'u1'));
  assert.deepEqual((await pasar(store, utc('2026-12-10T08:00:02'))).enviados, ['u1:a']);
});

test('una limpieza que se corta entre la marca y el borrado: reciente, nadie escribe sobre ella; vieja, vale por «no hay documento»', async () => {
  const cortada = async () => {
    const store = almacenFalso();
    await conDispositivo(store, 'u1');
    await guardar(store, 'u1', 0, { poner: { a: cosa('Leche', DIARIO, true) } });
    const borrar = store.delete;
    store.delete = async () => { throw new Error('Se acabó el tiempo'); };
    assert.equal(await callando(async () => assert.equal(await limpiar(store, UN_MES_DESPUES), 0)), 1);
    store.delete = borrar;
    assert.deepEqual([store.json('recordar/u1'), store.claves('limpieza/')], [{ borrando: UN_MES_DESPUES }, ['limpieza/20261210/u1']]);
    return store;
  };
  // Mientras la marca es reciente (esa limpieza podría seguir viva), quien guarda espera y, si no desaparece, falla:
  // la app lo repetirá. Ni «Hecho», ni otra limpieza a la vez, ni la pasada escriben sobre ella.
  let store = await cortada();
  const pronto = UN_MES_DESPUES + 30000;
  await assert.rejects(() => guardar(store, 'u1', 0, { poner: { b: cosa('Pan') } }, pronto), /No se pudo guardar recordar\/u1/);
  // Quien no tiene nada que escribir no espera: para él ya no hay documento (un «Hecho» que llega tarde, la app que escribía sobre su versión).
  assert.equal(await marcarHecha(store, 'u1', 'a', pronto), false);
  assert.deepEqual(await guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } }, pronto), { conflicto: SIN_DOCUMENTO });
  assert.equal(await limpiar(store, pronto), 0);
  assert.deepEqual(await pasar(store, pronto), { enviados: [], sesiones: 0, avisos: 0, pausadas: 0 });
  assert.deepEqual([store.json('recordar/u1'), store.claves('limpieza/')], [{ borrando: UN_MES_DESPUES }, ['limpieza/20261210/u1']]);
  // Cinco minutos después esa limpieza ya no existe: la marca vale por «no hay documento» y se escribe sobre ella.
  const tarde = UN_MES_DESPUES + 5 * MINUTOS;
  assert.deepEqual(await leerRecordatorios(store, 'u1', tarde), SIN_DOCUMENTO);
  assert.equal(await marcarHecha(store, 'u1', 'a', tarde), false);
  assert.deepEqual(await guardar(store, 'u1', 1, { poner: { b: cosa('Pan') } }, tarde), { conflicto: SIN_DOCUMENTO });
  const nuevo = await guardar(store, 'u1', 0, { poner: { b: cosa('Pan') } }, tarde);
  assert.equal(nuevo.v, 1);
  assert.deepEqual([store.json('recordar/u1').nacido, store.json('recordar/u1').cosas], [nuevo.nacido, { b: cosa('Pan') }]);
  assert.ok(enAgenda(store, 'u1'));
  // La clave de limpieza que quedó no borra el documento nuevo: al día siguiente se quita y ya.
  assert.equal(await limpiar(store, utc('2026-12-11T03:00:02')), 0);
  assert.deepEqual([store.claves('recordar/'), store.claves('limpieza/')], [['recordar/u1'], []]);
  // Y si nadie escribe, la limpieza del día siguiente acaba lo que la otra dejó a medias.
  store = await cortada();
  assert.equal(await limpiar(store, utc('2026-12-11T03:00:02')), 1);
  assert.deepEqual(store.claves(), ['dispositivos/u1/h1']);
});

test('tocaLimpiar: solo la pasada de las 03:00 (UTC), aunque arranque unos segundos antes o después', () => {
  for (const hora of ['02:59:55', '02:59:59', '03:00:00', '03:00:20', '03:04:54']) assert.equal(tocaLimpiar(utc(`2026-11-10T${hora}`)), true, hora);
  for (const hora of ['02:55:00', '02:59:54', '03:04:55', '03:05:00', '15:00:00', '00:00:00']) assert.equal(tocaLimpiar(utc(`2026-11-10T${hora}`)), false, hora);
});
