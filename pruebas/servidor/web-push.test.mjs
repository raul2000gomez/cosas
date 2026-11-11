/* netlify/lib/envio.mjs y las dos funciones con el web-push de verdad (el de node_modules, sin sustituirlo): lo que
   pasa con una suscripción a la que no se puede enviar nada. Sin red: todo lo que aquí se envía lo rechaza web-push
   antes de salir, y si algo intentara salir la prueba falla:
   node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { createECDH, randomBytes } from 'node:crypto';
import https from 'node:https';
import { mock, test } from 'node:test';
import webpush from 'web-push';
import { almacenFalso } from './almacen-falso.mjs';

let store = almacenFalso();
mock.module('@netlify/blobs', { namedExports: { getStore: () => store } });
mock.module('jose', {
  namedExports: {
    createRemoteJWKSet: () => 'claves-de-google',
    jwtVerify: async (token) => ({ payload: { sub: token.slice(6) } }), // «token-UID» es de UID.
  },
});

const { dispositivosDe, enviarRecordatorios, llavesDe, suscripcionRota } = await import('../../netlify/lib/envio.mjs');
const { CADUCA } = await import('../../netlify/lib/recordar.mjs');
const { default: avisos } = await import('../../netlify/functions/avisos.mjs');
const { default: programada } = await import('../../netlify/functions/recordatorios.mjs');

const utc = (texto) => Date.parse(`${texto}Z`);
const MOVIL = 'https://fcm.googleapis.com/fcm/send/movil-1';
const INVENTADO = 'https://fcm.googleapis.com/fcm/send/inventado';
// Unas claves como las que da un navegador: un punto de la curva P-256 (65 bytes) y un secreto de 16.
const CLAVES_DE_NAVEGADOR = { p256dh: createECDH('prime256v1').generateKeys().toString('base64url'), auth: randomBytes(16).toString('base64url') };
const CLAVES_INVENTADAS = { p256dh: 'p', auth: 'a' };
const VAPID = webpush.generateVAPIDKeys();
const opciones = (claves = VAPID) => ({ vapidDetails: { subject: 'https://cosas.info', ...claves }, TTL: CADUCA / 1000, urgency: 'high', timeout: 5000 });

/** Nada de esto tiene que llegar a pedirle nada a nadie: si web-push abre una conexión, se nota. Devuelve cuántas ha intentado. */
function sinRed() {
  const salidas = mock.method(https, 'request', () => { throw new Error('Esto no tenía que salir a la red'); });
  return () => salidas.mock.callCount();
}

/** ¿Rechaza web-push esa suscripción él solo, antes de enviar nada? (Y, si la rechaza, sin código de estado.) */
function laRechazaWebPush(suscripcion, claves) {
  try {
    webpush.generateRequestDetails(suscripcion, '{"tipo":"recordar"}', opciones(claves));
    return false;
  } catch (error) {
    assert.equal(error.statusCode, undefined);
    return true;
  }
}

test('suscripcionRota dice lo mismo que el web-push de verdad sobre las claves del navegador', () => {
  const buena = { endpoint: MOVIL, keys: CLAVES_DE_NAVEGADOR };
  const comprimida = createECDH('prime256v1');
  comprimida.generateKeys();
  const casos = {
    buena,
    'con más campos y un secreto más largo': { ...buena, desde: 7, expirationTime: null, keys: { ...CLAVES_DE_NAVEGADOR, auth: randomBytes(32).toString('base64url') } },
    'en base64 corriente': { ...buena, keys: { p256dh: Buffer.from(CLAVES_DE_NAVEGADOR.p256dh, 'base64url').toString('base64'), auth: Buffer.from(CLAVES_DE_NAVEGADOR.auth, 'base64url').toString('base64') } },
    'claves inventadas': { ...buena, keys: CLAVES_INVENTADAS },
    'sin claves': { endpoint: MOVIL },
    'claves vacías': { ...buena, keys: { p256dh: '', auth: '' } },
    'claves que no son texto': { ...buena, keys: { p256dh: 7, auth: 12345678901234567890 } },
    '65 bytes que no son un punto de la curva': { ...buena, keys: { ...CLAVES_DE_NAVEGADOR, p256dh: Buffer.alloc(65, 7).toString('base64url') } },
    'un 4 y ceros': { ...buena, keys: { ...CLAVES_DE_NAVEGADOR, p256dh: Buffer.concat([Buffer.from([4]), Buffer.alloc(64)]).toString('base64url') } },
    'pública comprimida (33 bytes)': { ...buena, keys: { ...CLAVES_DE_NAVEGADOR, p256dh: comprimida.getPublicKey('base64url', 'compressed') } },
    'pública con un byte de más': { ...buena, keys: { ...CLAVES_DE_NAVEGADOR, p256dh: `${CLAVES_DE_NAVEGADOR.p256dh}AA` } },
    'secreto corto': { ...buena, keys: { ...CLAVES_DE_NAVEGADOR, auth: randomBytes(15).toString('base64url') } },
    'sin dirección': { endpoint: '', keys: CLAVES_DE_NAVEGADOR },
  };
  for (const [caso, suscripcion] of Object.entries(casos)) assert.equal(suscripcionRota(suscripcion), laRechazaWebPush(suscripcion), caso);
  assert.deepEqual(Object.values(casos).map(suscripcionRota).filter((rota) => !rota).length, 3); // Solo valen las tres primeras.
  // Con las claves de la función mal, web-push rechaza igual (y con un error igual de mudo) la suscripción buena:
  // por eso se mira la suscripción y no el error.
  assert.equal(laRechazaWebPush(buena, { ...VAPID, privateKey: 'mal' }), true);
  assert.equal(suscripcionRota(buena), false);
});

test('con el web-push de verdad: a un dispositivo de claves inventadas no se le envía nada y se olvida; con las claves de la función mal, no se olvida a nadie', async () => {
  const salidas = sinRed();
  const avisados = mock.method(console, 'warn', () => {});
  try {
    const local = almacenFalso();
    await local.setJSON('dispositivos/u1/h1', { endpoint: INVENTADO, keys: CLAVES_INVENTADAS, desde: 2 });
    await local.set('suscripciones/h1', 'u1');
    const llaves = await llavesDe(local);
    const cosas = [{ id: 'a', texto: 'Leche', de: 1000 }];
    assert.deepEqual(await enviarRecordatorios(local, llaves, 'u1', await dispositivosDe(local, 'u1'), cosas), []);
    assert.deepEqual(local.claves('dispositivos/'), []);
    assert.deepEqual(local.claves('suscripciones/'), []);
    // Un dispositivo de verdad y las claves VAPID estropeadas: falla igual de pronto, pero es cosa de la función. Ni se
    // da por entregado ni se toca el dispositivo: se repetirá.
    await local.setJSON('dispositivos/u1/h2', { endpoint: MOVIL, keys: CLAVES_DE_NAVEGADOR, desde: 3 });
    await local.set('suscripciones/h2', 'u1');
    const rotas = { ...llaves, claves: { ...llaves.claves, privateKey: 'mal' } };
    assert.deepEqual(await enviarRecordatorios(local, rotas, 'u1', await dispositivosDe(local, 'u1'), cosas), []);
    assert.deepEqual(local.claves('dispositivos/'), ['dispositivos/u1/h2']);
    assert.equal(avisados.mock.callCount(), 1);
    assert.equal(salidas(), 0);
  } finally {
    mock.restoreAll();
  }
});

test('de punta a punta: un alta con claves inventadas se admite, pero al primer aviso se olvida y la sesión queda en pausa, sin reintentos', async () => {
  const salidas = sinRed();
  store = almacenFalso();
  mock.timers.enable({ apis: ['Date'], now: utc('2026-11-10T07:30') });
  const consola = [];
  for (const nivel of ['log', 'warn', 'error']) mock.method(console, nivel, (...partes) => consola.push(`${nivel}: ${partes.join(' ')}`));
  const llamar = async (accion, cuerpo) => {
    const peticion = new Request(`https://cosas.test/api/avisos/${accion}`, { method: 'POST', headers: { Authorization: 'Bearer token-u1', 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) });
    return (await avisos(peticion, { params: { accion } })).status;
  };
  try {
    // El alta solo mira que la suscripción tenga sus claves (un navegador no da otras): entra.
    assert.equal(await llamar('alta', { suscripcion: { endpoint: INVENTADO, keys: CLAVES_INVENTADAS } }), 200);
    assert.equal(await llamar('recordar', { siV: 0, zona: 'Europe/Madrid', poner: { a: { texto: 'Leche', hecha: false, r: { hora: '09:00', frec: 'dia' } } } }), 200);
    // A las 9:00: no hay a quién enviárselo. El dispositivo se olvida y el aviso sigue pendiente, sin darse por entregado.
    mock.timers.setTime(utc('2026-11-10T08:00:02'));
    await programada();
    assert.deepEqual(store.claves('dispositivos/'), []);
    assert.deepEqual([store.json('recordar/u1').prox, store.json('recordar/u1').pausado], [{ a: utc('2026-11-10T08:00') }, null]);
    assert.equal(consola.pop(), 'log: Recordatorios: 1 sesiones atendidas, 0 cosas avisadas, 0 sesiones en pausa, 0 documentos borrados');
    // A las 9:05, en vez de reintentarlo (así seis horas), la sesión queda en pausa: ya no tiene dispositivos. Si
    // vuelve con uno de verdad, despierta; si no, al mes se borra.
    mock.timers.setTime(utc('2026-11-10T08:05:02'));
    await programada();
    assert.equal(store.json('recordar/u1').pausado, utc('2026-11-10T08:05:02'));
    assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1']);
    assert.equal(consola.pop(), 'log: Recordatorios: 1 sesiones atendidas, 0 cosas avisadas, 1 sesiones en pausa, 0 documentos borrados');
    assert.deepEqual(consola.filter((linea) => !linea.startsWith('log:')), []); // Sin quejas: no ha fallado nada.
    assert.equal(salidas(), 0);
  } finally {
    mock.timers.reset();
    mock.restoreAll();
  }
});
