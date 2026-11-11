/* Lo mismo que hace el almacén de mentira, contra el cliente de verdad de Netlify Blobs y su servidor local
   (BlobsServer, sobre una carpeta temporal): que las escrituras condicionadas, las listas y las lecturas se comportan
   como las funciones esperan:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { getStore } from '@netlify/blobs';
import { BlobsServer } from '@netlify/blobs/server';
import { dispositivosDe, fichaCon, fichaValida, llavesDe } from '../../netlify/lib/envio.mjs';
import { envioDe } from '../../netlify/lib/recordar.mjs';
import { despertar, guardarRecordatorios, leerRecordatorios, limpiar, marcarHecha, olvidarRecordatorios, pasada } from '../../netlify/lib/recordatorios.mjs';

const MADRID = 'Europe/Madrid';
const utc = (texto) => Date.parse(`${texto}Z`);
const DIARIO = { hora: '09:00', frec: 'dia' };
const cosa = (texto, r = DIARIO, hecha = false) => ({ texto, hecha, r });
const LAS_8 = utc('2026-11-10T07:00');
const LAS_9 = utc('2026-11-10T08:00');

let carpeta;
let servidor;
let store;

before(async () => {
  carpeta = await mkdtemp(join(tmpdir(), 'cosas-blobs-'));
  servidor = new BlobsServer({ directory: carpeta, token: 'de-prueba' });
  const { port } = await servidor.start();
  const url = `http://localhost:${port}`;
  store = conEtiquetas(getStore({ name: 'avisos', consistency: 'strong', edgeURL: url, uncachedEdgeURL: url, token: 'de-prueba', siteID: 'sitio-de-prueba' }));
});

/**
 * El servidor local no devuelve la etiqueta (etag) al leer, como sí hace Netlify: aquí se le pone la de la lista,
 * comprobando que no ha cambiado mientras se leía (si cambió, se lee otra vez).
 */
function conEtiquetas(real) {
  const etiquetaDe = async (clave) => ((await real.list({ prefix: clave })).blobs.find((blob) => blob.key === clave) || {}).etag;
  const leer = real.getWithMetadata.bind(real);
  real.getWithMetadata = async (clave, opciones) => {
    for (;;) {
      const antes = await etiquetaDe(clave);
      const leido = await leer(clave, opciones);
      if (!leido) return null;
      if (antes && antes === (await etiquetaDe(clave))) return { ...leido, etag: antes };
    }
  };
  return real;
}

after(async () => {
  await servidor.stop();
  await rm(carpeta, { recursive: true, force: true });
});

const claves = async (prefix) => (await store.list({ prefix })).blobs.map((blob) => blob.key).sort();
const guardar = (uid, siV, cambio, ahora = LAS_8) => guardarRecordatorios(store, uid, envioDe({ siV, zona: MADRID, ...cambio }), ahora);

/** Una pasada con los dispositivos de verdad del almacén y un envío de mentira al que le llega todo menos «falla». */
async function pasar(ahora, falla = () => false) {
  const enviados = [];
  const cuenta = await pasada(store, ahora, {
    dispositivos: (uid) => dispositivosDe(store, uid),
    enviar: async (uid, dispositivos, cosas) => {
      const llegan = cosas.filter((c) => !falla(c));
      enviados.push(...llegan.map((c) => `${uid}:${c.id}@${dispositivos.length}`));
      return llegan.map(({ id, de }) => ({ id, de }));
    },
  });
  return { enviados: enviados.sort(), ...cuenta };
}

test('Netlify Blobs de verdad: guardar, conflicto, agenda, pasada con turnos, «Hecho» y olvidar', async () => {
  assert.deepEqual(await leerRecordatorios(store, 'u1', LAS_8), { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} });
  await store.setJSON('dispositivos/u1/h1', { endpoint: 'https://fcm.googleapis.com/fcm/send/1', keys: { p256dh: 'p', auth: 'a' }, desde: 1 });
  await store.setJSON('dispositivos/u1/h2', { endpoint: 'https://fcm.googleapis.com/fcm/send/2', keys: { p256dh: 'p', auth: 'a' }, desde: 2 });
  assert.deepEqual((await dispositivosDe(store, 'u1')).map((d) => d.huella), ['h2', 'h1']);

  const primero = await guardar('u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan') } });
  assert.match(primero.nacido, /^[A-Za-z0-9_-]{12}$/);
  assert.deepEqual(primero, { nacido: primero.nacido, v: 1, rechazadas: [] });
  assert.deepEqual(await claves('agenda/'), ['agenda/202611100800/u1']);
  const hay = { nacido: primero.nacido, v: 1, zona: MADRID, general: null, cosas: { a: cosa('Leche'), b: cosa('Pan') } };
  assert.deepEqual(await guardar('u1', 0, { quitar: ['a'] }), { conflicto: hay });
  assert.deepEqual(await leerRecordatorios(store, 'u1', LAS_8), hay);

  // La pasada: aún no toca; a su hora, «a» no llega y «b» sí; la siguiente repite solo «a».
  assert.deepEqual(await pasar(utc('2026-11-10T07:55:02')), { enviados: [], sesiones: 0, avisos: 0, pausadas: 0 });
  assert.deepEqual(await pasar(LAS_9 + 2000, (c) => c.id === 'a'), { enviados: ['u1:b@2'], sesiones: 1, avisos: 1, pausadas: 0 });
  assert.deepEqual(await claves('agenda/'), ['agenda/202611100800/u1']);
  assert.deepEqual((await pasar(LAS_9 + 30000)).enviados, []); // Una segunda ejecución enseguida: el turno sigue vivo.
  assert.deepEqual((await pasar(LAS_9 + 5 * 60000 + 2000)).enviados, ['u1:a@2']);
  assert.deepEqual(await claves('agenda/'), ['agenda/202611110800/u1']);

  assert.equal(await marcarHecha(store, 'u1', 'a', LAS_9 + 6 * 60000), true);
  assert.equal(await marcarHecha(store, 'u1', 'a', LAS_9 + 6 * 60000), false);
  assert.deepEqual((await leerRecordatorios(store, 'u1', LAS_9 + 6 * 60000)).cosas.a, cosa('Leche', DIARIO, true));
  assert.deepEqual((await pasar(utc('2026-11-11T08:00:02'))).enviados, ['u1:b@2']);

  // Las escrituras condicionadas, tal cual las usan las funciones.
  const actual = await store.getWithMetadata('recordar/u1', { type: 'json' });
  assert.equal(typeof actual.etag, 'string');
  assert.equal((await store.setJSON('recordar/u1', actual.data, { onlyIfMatch: '"otra"' })).modified, false);
  assert.equal((await store.setJSON('recordar/u1', actual.data, { onlyIfNew: true })).modified, false);
  assert.equal((await store.setJSON('recordar/u1', actual.data, { onlyIfMatch: actual.etag })).modified, true);
  assert.equal(await store.get('no-existe'), null);
  assert.equal(await store.get('no-existe', { type: 'json' }), null);
  assert.equal(await store.getWithMetadata('no-existe', { type: 'json' }), null);
  await store.delete('no-existe'); // Borrar lo que no existe no falla.
  assert.deepEqual(await claves('no-hay-nada/'), []);

  // Un cambio ajeno entre la lectura y la escritura no se pisa: se vuelve a leer.
  const original = store.getWithMetadata.bind(store);
  let colado = false;
  store.getWithMetadata = async (...argumentos) => {
    const leido = await original(...argumentos);
    if (!colado) {
      colado = true;
      store.getWithMetadata = original;
      await guardar('u1', (await leerRecordatorios(store, 'u1', LAS_9)).v, { poner: { c: cosa('Sal') } }, utc('2026-11-11T09:00'));
    }
    return leido;
  };
  assert.equal(await marcarHecha(store, 'u1', 'b', utc('2026-11-11T09:00')), true);
  assert.deepEqual((await leerRecordatorios(store, 'u1', LAS_9)).cosas, { a: cosa('Leche', DIARIO, true), b: cosa('Pan', DIARIO, true), c: cosa('Sal') }); // «c», que llegó entretanto, sigue ahí.

  // Las fichas, con el secreto guardado en el almacén.
  const { claves: vapid, secreto } = await llavesDe(store);
  assert.deepEqual([typeof vapid.publicKey, typeof vapid.privateKey], ['string', 'string']);
  assert.match(await store.get('secreto'), /^[A-Za-z0-9_-]{43}$/);
  assert.equal(await store.get('secreto'), secreto);
  assert.equal(await fichaValida(store, 'u1', 'b', fichaCon(secreto, 'u1', 'b')), true);
  assert.equal(await fichaValida(store, 'u1', 'a', fichaCon(secreto, 'u1', 'b')), false);

  // Olvidar: fuera el documento; el siguiente es otro.
  await olvidarRecordatorios(store, 'u1');
  assert.deepEqual(await leerRecordatorios(store, 'u1', LAS_9), { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} });
  const otro = await guardar('u1', 0, { poner: { a: cosa('Leche') } }, utc('2026-11-11T09:00'));
  assert.deepEqual([otro.v, otro.nacido === primero.nacido], [1, false]);
});

test('Netlify Blobs de verdad: sin dispositivos, pausa y clave de limpieza; despertar; y la limpieza de cada día', async () => {
  await guardar('u2', 0, { poner: { a: cosa('Leche') } });
  await guardar('u3', 0, { poner: { a: cosa('Pan') } });
  const primera = await pasar(LAS_9 + 2000);
  assert.deepEqual([primera.enviados, primera.pausadas], [[], 2]);
  assert.deepEqual(await claves('limpieza/'), ['limpieza/20261210/u2', 'limpieza/20261210/u3']);
  assert.equal((await store.get('recordar/u2', { type: 'json' })).pausado, LAS_9 + 2000);
  // «u2» da de alta un dispositivo y despierta: lo de las 9:00 sale con retraso. «u3» no vuelve.
  assert.equal(await despertar(store, 'u2', LAS_9 + 20 * 60000), false);
  await store.setJSON('dispositivos/u2/h1', { endpoint: 'https://fcm.googleapis.com/fcm/send/3', keys: { p256dh: 'p', auth: 'a' }, desde: 3 });
  assert.equal(await despertar(store, 'u2', LAS_9 + 20 * 60000), true);
  // Su clave nueva es la de ahora; la de las 9:00, que la pausa no quitó, la quita la pasada que la atiende.
  assert.deepEqual((await claves('agenda/')).filter((clave) => clave.endsWith('/u2')), ['agenda/202611100800/u2', 'agenda/202611100820/u2']);
  assert.deepEqual((await pasar(LAS_9 + 25 * 60000)).enviados, ['u2:a@1']);
  assert.deepEqual((await claves('agenda/')).filter((clave) => clave.endsWith('/u2')), ['agenda/202611110800/u2']);
  // Al mes: se borra el documento de «u3» y las dos claves de limpieza; el de «u2» sigue.
  assert.equal(await limpiar(store, utc('2026-12-09T03:00:01')), 0);
  assert.equal(await limpiar(store, utc('2026-12-10T03:00:01')), 1);
  assert.deepEqual(await claves('limpieza/'), []);
  assert.deepEqual((await claves('recordar/')).filter((clave) => clave !== 'recordar/u1'), ['recordar/u2']);
});
