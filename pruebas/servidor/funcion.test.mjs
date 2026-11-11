/* La función de avisos (netlify/functions/avisos.mjs) y la programada (recordatorios.mjs), enteras, con un almacén,
   un Google y un servicio push de mentira:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { mock, test } from 'node:test';
import { almacenFalso } from './almacen-falso.mjs';

let store = almacenFalso(); // Uno nuevo en cada prueba (ver «empezar»).
const enviados = []; // Lo que sale hacia los servicios push: { endpoint, aviso, opciones }.
const respuestas = new Map(); // endpoint → el código de estado con el que ese dispositivo rechaza lo que se le envía.

mock.module('@netlify/blobs', { namedExports: { getStore: () => store } });
mock.module('jose', {
  namedExports: {
    createRemoteJWKSet: () => 'claves-de-google',
    // Un token «token-UID» es bueno y es de UID; cualquier otro, no.
    jwtVerify: async (token) => {
      if (!token.startsWith('token-')) throw new Error('Token no válido');
      return { payload: { sub: token.slice(6) } };
    },
  },
});
mock.module('web-push', {
  defaultExport: {
    generateVAPIDKeys: () => ({ publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }),
    sendNotification: async (suscripcion, aviso, opciones) => {
      if (respuestas.has(suscripcion.endpoint)) throw Object.assign(new Error('Rechazado'), { statusCode: respuestas.get(suscripcion.endpoint) });
      enviados.push({ endpoint: suscripcion.endpoint, bytes: Buffer.byteLength(aviso), aviso: JSON.parse(aviso), opciones });
    },
  },
});

const { default: avisos } = await import('../../netlify/functions/avisos.mjs');
const { default: programada, config } = await import('../../netlify/functions/recordatorios.mjs');

const MADRID = 'Europe/Madrid';
const utc = (texto) => Date.parse(`${texto}Z`);
const DIARIO = { hora: '09:00', frec: 'dia' };
const cosa = (texto, r = DIARIO, hecha = false) => ({ texto, hecha, r });
const MOVIL = 'https://fcm.googleapis.com/fcm/send/movil-1';
const TABLETA = 'https://web.push.apple.com/tableta-1';
const SIN_DOCUMENTO = { nacido: null, v: 0, zona: 'UTC', general: null, cosas: {} };

/** Una prueba con su almacén nuevo, el reloj en «ahora» y la consola callada (devuelve lo que se escribió en ella). */
function empezar(ahora) {
  store = almacenFalso();
  enviados.length = 0;
  respuestas.clear();
  mock.timers.enable({ apis: ['Date'], now: utc(ahora) });
  const consola = [];
  for (const nivel of ['log', 'warn', 'error']) mock.method(console, nivel, (...partes) => consola.push(`${nivel}: ${partes.join(' ')}`));
  return consola;
}

function acabar() {
  mock.timers.reset();
  mock.restoreAll();
}

const aLas = (ahora) => mock.timers.setTime(utc(ahora));

/** Llama a la función como lo haría Netlify: /api/avisos/ACCION. Devuelve [estado, cuerpo, respuesta, petición]. */
async function llamar(metodo, accion, { uid, cuerpo, bruto, cabeceras = {} } = {}) {
  const todas = { 'Content-Type': 'application/json', ...cabeceras };
  if (uid) todas.Authorization = `Bearer token-${uid}`;
  const peticion = new Request(`https://cosas.test/api/avisos/${accion}`, {
    method: metodo, headers: todas, body: bruto !== undefined ? bruto : cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
  const respuesta = await avisos(peticion, { params: { accion } });
  return [respuesta.status, respuesta.status === 204 ? null : await respuesta.json(), respuesta, peticion];
}

const alta = (uid, endpoint) => llamar('POST', 'alta', { uid, cuerpo: { suscripcion: { endpoint, keys: { p256dh: 'p', auth: 'a' } } } });
const leer = async (uid) => (await llamar('GET', 'recordar', { uid })).slice(0, 2);
const guardar = async (uid, siV, cambio = {}) => (await llamar('POST', 'recordar', { uid, cuerpo: { siV, zona: MADRID, ...cambio } })).slice(0, 2);
const hecho = async (cuerpo) => (await llamar('POST', 'hecho', { cuerpo })).slice(0, 2);
/** Lo enviado, resumido: «dispositivo:id+id». */
const salidas = () => enviados.map((e) => `${e.endpoint.split('/').pop()}:${e.aviso.cosas.map((c) => c.id).join('+')}`).sort();

test('la programada se declara cada cinco minutos, con un literal escrito en su archivo', async () => {
  assert.deepEqual(config, { schedule: '*/5 * * * *' });
  const fuente = await readFile(new URL('../../netlify/functions/recordatorios.mjs', import.meta.url), 'utf8');
  assert.ok(fuente.includes("export const config = { schedule: '*/5 * * * *' };"));
});

test('la programada: si no puede leer las claves, acaba sin haber tocado nada', async () => {
  // La primera de todas: después las claves ya están leídas y no se vuelven a pedir.
  empezar('2026-11-10T07:00');
  try {
    await alta('u1', MOVIL);
    await guardar('u1', 0, { poner: { a: cosa('Leche') } });
    aLas('2026-11-10T08:00:02');
    const antes = JSON.stringify([...store.datos]);
    const pedir = store.get;
    store.get = async (clave, opciones) => {
      if (clave === 'vapid' || clave === 'secreto') throw new Error('Almacén caído');
      return pedir(clave, opciones);
    };
    await assert.rejects(() => programada(), /Almacén caído/);
    store.get = pedir;
    assert.equal(JSON.stringify([...store.datos]), antes); // Ni un turno tomado ni una clave movida.
    assert.equal(enviados.length, 0);
    await programada(); // Y la siguiente, ya con claves, avisa.
    assert.deepEqual(salidas(), ['movil-1:a']);
  } finally {
    acabar();
  }
});

test('acciones y métodos: lo que no existe, 404; con otro método, 405; OPTIONS, para cualquiera', async () => {
  empezar('2026-11-10T07:00');
  try {
    for (const accion of ['nada', '__proto__', 'constructor', 'toString', '']) assert.equal((await llamar('GET', accion))[0], 404, accion);
    assert.equal((await llamar('POST', 'clave', { cuerpo: {} }))[0], 405);
    assert.equal((await llamar('GET', 'hecho'))[0], 405);
    assert.equal((await llamar('GET', 'olvidar', { uid: 'u1' }))[0], 405);
    assert.equal((await llamar('PUT', 'recordar', { uid: 'u1', cuerpo: {} }))[0], 405);
    const [estado, , respuesta] = await llamar('OPTIONS', 'recordar');
    assert.equal(estado, 204);
    assert.equal(respuesta.headers.get('access-control-allow-origin'), '*');
    assert.match(respuesta.headers.get('access-control-allow-methods'), /GET, POST/);
    assert.deepEqual((await llamar('GET', 'clave')).slice(0, 2), [200, { clave: 'CLAVE-PUBLICA' }]);
  } finally {
    acabar();
  }
});

test('recordar y olvidar: hace falta sesión', async () => {
  empezar('2026-11-10T07:00');
  try {
    assert.deepEqual((await llamar('GET', 'recordar')).slice(0, 2), [401, { error: 'sin-sesion' }]);
    const peticion = new Request('https://cosas.test/api/avisos/recordar', { headers: { Authorization: 'Bearer falso' } });
    assert.equal((await avisos(peticion, { params: { accion: 'recordar' } })).status, 401);
    assert.equal((await llamar('POST', 'recordar', { cuerpo: { siV: 0, zona: MADRID } }))[0], 401);
    assert.deepEqual((await llamar('POST', 'olvidar', { cuerpo: {} })).slice(0, 2), [401, { error: 'sin-sesion' }]);
    assert.deepEqual(store.claves(), []);
  } finally {
    acabar();
  }
});

test('GET recordar: sin documento, nacido null y v 0; con él, lo que comparte', async () => {
  empezar('2026-11-10T07:00');
  try {
    assert.deepEqual(await leer('u1'), [200, SIN_DOCUMENTO]);
    assert.deepEqual(store.claves(), []); // Leer no crea nada.
    const [, { nacido }] = await guardar('u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan') }, general: DIARIO });
    assert.deepEqual(await leer('u1'), [200, { nacido, v: 1, zona: MADRID, general: DIARIO, cosas: { a: cosa('Leche'), b: cosa('Pan') } }]);
    assert.deepEqual(await leer('u2'), [200, SIN_DOCUMENTO]); // Lo de otra sesión no se ve.
    // De paso repone la clave de la agenda, por si se hubiera perdido.
    await store.delete('agenda/202611100800/u1');
    await leer('u1');
    assert.deepEqual(store.claves('agenda/'), ['agenda/202611100800/u1']);
  } finally {
    acabar();
  }
});

test('POST recordar: un cambio sobre la versión que la app vio; 200 con nacido, v y rechazadas; 409 con lo que hay', async () => {
  empezar('2026-11-10T07:00');
  try {
    const [estado, primero] = await guardar('u1', 0, { poner: { a: cosa('Leche'), b: cosa('Pan') }, general: DIARIO });
    assert.equal(estado, 200);
    assert.match(primero.nacido, /^[A-Za-z0-9_-]{12}$/);
    assert.deepEqual(primero, { nacido: primero.nacido, v: 1, rechazadas: [] });
    // Repetido (la respuesta se perdió): lo mismo, sin versión nueva.
    assert.deepEqual(await guardar('u1', 1, { poner: { a: cosa('Leche'), b: cosa('Pan') }, general: DIARIO }), [200, primero]);
    // Un cambio: lo que no se nombra no se toca, y lo que no vale vuelve en «rechazadas» sin tumbar lo demás.
    const cambio = { poner: { c: cosa('Sal'), mala: { texto: '', hecha: false, r: DIARIO }, una: cosa('Cita', { hora: '09:00', frec: 'una', fecha: '2026-11-12' }) }, quitar: ['a'] };
    assert.deepEqual(await guardar('u1', 1, cambio), [200, { nacido: primero.nacido, v: 2, rechazadas: ['mala', 'una'] }]);
    const hay = { nacido: primero.nacido, v: 2, zona: MADRID, general: DIARIO, cosas: { b: cosa('Pan'), c: cosa('Sal') } };
    assert.deepEqual(await leer('u1'), [200, hay]);
    // Otro dispositivo escribe sobre lo que vio antes: 409 con lo mismo que da el GET, y no cambia nada.
    assert.deepEqual(await guardar('u1', 1, { poner: { z: cosa('Otra') } }), [409, hay]);
    assert.deepEqual(await guardar('u1', 7, { general: null }), [409, hay]);
    assert.deepEqual(await leer('u1'), [200, hay]);
    // El límite: 100 cosas por sesión. Las que no caben, rechazadas.
    const muchas = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`n${i}`, cosa(`Cosa ${i}`)]));
    const [, lleno] = await guardar('u1', 2, { poner: muchas });
    assert.deepEqual([lleno.v, lleno.rechazadas], [3, ['n98', 'n99']]);
    assert.equal(Object.keys((await leer('u1'))[1].cosas).length, 100);
    // Desde aquí no sale ningún aviso, ni aunque algo esté ya atrasado: eso es cosa de la programada.
    await alta('u1', MOVIL);
    aLas('2026-11-10T08:02:00');
    assert.equal((await guardar('u1', 3, { poner: { b: cosa('Pan de pueblo') } }))[0], 200);
    assert.equal(enviados.length, 0);
  } finally {
    acabar();
  }
});

test('POST recordar: lo que no se entiende, 400; lo que no cabe, 413 (por lo que dice ocupar, sin leerlo, y por lo que ocupa)', async () => {
  empezar('2026-11-10T07:00');
  try {
    await guardar('u1', 0, { poner: { a: cosa('Leche') } });
    const de = (cuantas) => Object.fromEntries(Array.from({ length: cuantas }, (_, i) => [`c${i}`, cosa('x')]));
    for (const malo of [{ zona: MADRID }, { siV: 1 }, { siV: 1, zona: 'Marte/Olimpo' }, { siV: '1', zona: MADRID }, { siV: 1, zona: MADRID, general: { hora: '25:00', frec: 'dia' } },
      { siV: 1, zona: MADRID, general: { hora: '09:00', frec: 'una', fecha: '2026-11-12' } }, { siV: 1, zona: MADRID, poner: [] }, { siV: 1, zona: MADRID, quitar: 'a' },
      { siV: 1, zona: MADRID, poner: de(401) }, { siV: 1, zona: MADRID, quitar: Array.from({ length: 401 }, (_, i) => `c${i}`) }]) {
      assert.deepEqual((await llamar('POST', 'recordar', { uid: 'u1', cuerpo: malo })).slice(0, 2), [400, { error: 'recordatorios' }], JSON.stringify(malo).slice(0, 80));
    }
    assert.deepEqual((await llamar('POST', 'recordar', { uid: 'u1', bruto: '{no es json' })).slice(0, 2), [400, { error: 'json' }]);
    assert.deepEqual((await llamar('POST', 'recordar', { uid: 'u1', bruto: '"texto"' })).slice(0, 2), [400, { error: 'json' }]);
    // Dice que ocupa más de 100000 bytes: 413 sin llegar a leerlo.
    const [estado, error, , peticion] = await llamar('POST', 'recordar', { uid: 'u1', cuerpo: { siV: 1, zona: MADRID }, cabeceras: { 'Content-Length': '100001' } });
    assert.deepEqual([estado, error, peticion.bodyUsed], [413, { error: 'demasiado-grande' }, false]);
    // No lo dice, pero lo ocupa.
    const relleno = (letras) => JSON.stringify({ siV: 1, zona: MADRID, relleno: letras });
    assert.deepEqual((await llamar('POST', 'recordar', { uid: 'u1', bruto: relleno('x'.repeat(100001)) })).slice(0, 2), [413, { error: 'demasiado-grande' }]);
    // Se cuentan bytes, no letras: 40000 eñes son 80000 bytes y caben; 40000 letras japonesas son 120000 y no.
    assert.equal((await llamar('POST', 'recordar', { uid: 'u1', bruto: relleno('ñ'.repeat(40000)) }))[0], 200);
    assert.equal((await llamar('POST', 'recordar', { uid: 'u1', bruto: relleno('語'.repeat(40000)) }))[0], 413);
    // Y justo en el límite, cabe.
    const justo = relleno('x'.repeat(100000 - Buffer.byteLength(relleno(''))));
    assert.equal(Buffer.byteLength(justo), 100000);
    assert.equal((await llamar('POST', 'recordar', { uid: 'u1', bruto: justo, cabeceras: { 'Content-Length': '100000' } }))[0], 200);
    assert.equal((await leer('u1'))[1].v, 1); // Nada de todo eso ha cambiado nada.
    // Las demás llamadas siguen con su límite de siempre.
    assert.equal((await llamar('POST', 'alta', { uid: 'u1', bruto: JSON.stringify({ suscripcion: {}, relleno: 'x'.repeat(5000) }) }))[0], 413);
  } finally {
    acabar();
  }
});

test('de punta a punta: a su hora, un mensaje por dispositivo con todas las cosas; «Hecho» con la ficha que venía en él', async () => {
  const consola = empezar('2026-11-10T07:30');
  try {
    assert.equal((await alta('u1', MOVIL))[0], 200);
    assert.equal((await alta('u1', TABLETA))[0], 200);
    assert.equal((await alta('u2', 'https://fcm.googleapis.com/fcm/send/de-otro'))[0], 200);
    await guardar('u1', 0, { poner: { a: cosa('  Comprar \n leche '), b: cosa('Pan'), c: cosa('Sal', { hora: '09:05', frec: 'dia' }) }, general: DIARIO });
    await guardar('u2', 0, { poner: { z: cosa('Lo de otra sesión', { hora: '09:05', frec: 'dia' }) } });

    aLas('2026-11-10T07:55:02');
    await programada();
    assert.equal(enviados.length, 0); // Aún no toca.

    aLas('2026-11-10T08:00:02'); // Las 9:00 en Madrid.
    assert.equal(await programada(), undefined);
    assert.deepEqual(salidas(), ['movil-1:a+b', 'tableta-1:a+b']);
    // Lo que recibe el service worker (contrato): las cosas con su ficha y, para uno de antes, título, línea, enlace y etiqueta.
    const { aviso, opciones, bytes } = enviados.find((e) => e.endpoint === MOVIL);
    const fichas = Object.fromEntries(aviso.cosas.map((c) => [c.id, c.ficha]));
    assert.match(fichas.a, /^[A-Za-z0-9_-]{43}$/);
    assert.notEqual(fichas.a, fichas.b);
    assert.deepEqual(aviso, {
      tipo: 'recordar',
      uid: 'u1',
      cosas: [{ id: 'a', texto: 'Comprar leche', ficha: fichas.a }, { id: 'b', texto: 'Pan', ficha: fichas.b }],
      titulo: 'Recordar',
      linea: '«Comprar leche» y 1 más',
      url: '/#grupo/recordar',
      etiqueta: 'recordar',
    });
    assert.ok(bytes <= 3000);
    assert.deepEqual(opciones, { vapidDetails: { subject: 'https://cosas.info', publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }, TTL: 21600, urgency: 'high', timeout: 5000 });
    assert.deepEqual(consola.filter((linea) => linea.startsWith('log:')).pop(), 'log: Recordatorios: 1 sesiones atendidas, 2 cosas avisadas, 0 sesiones en pausa, 0 documentos borrados');

    // Netlify la ejecuta otra vez (reintento): no se repite nada.
    enviados.length = 0;
    await programada();
    assert.equal(enviados.length, 0);

    // A las 9:05, una sola cosa de cada sesión: el mensaje lleva su id en el enlace y en la etiqueta.
    aLas('2026-11-10T08:05:01');
    await programada();
    assert.deepEqual(salidas(), ['de-otro:z', 'movil-1:c', 'tableta-1:c']);
    const sola = enviados.find((e) => e.endpoint === TABLETA).aviso;
    assert.deepEqual(sola, {
      tipo: 'recordar', uid: 'u1', cosas: [{ id: 'c', texto: 'Sal', ficha: sola.cosas[0].ficha }], titulo: 'Recordar', linea: 'Sal', url: '/?recordada=c#grupo/recordar', etiqueta: 'recordar-c',
    });
    const deOtro = enviados.find((e) => e.endpoint.endsWith('de-otro')).aviso;
    assert.deepEqual([deOtro.uid, deOtro.cosas.map((c) => c.id)], ['u2', ['z']]);

    // «Hecho» en el aviso de «a»: solo con su ficha, la que venía en el mensaje.
    for (const mala of [{ uid: 'u1', id: 'a', ficha: 'inventada' }, { uid: 'u1', id: 'b', ficha: fichas.a }, { uid: 'u2', id: 'a', ficha: fichas.a }, { uid: 'u1', id: 'a' },
      { uid: 'u1', id: 'a', ficha: 7 }, { uid: '../u1', id: 'a', ficha: fichas.a }, { uid: 'u1', id: '', ficha: fichas.a }, { uid: 'u1', id: 'no-esta', ficha: fichas.a }, {}, []]) {
      assert.deepEqual(await hecho(mala), [403, { error: 'ficha' }], JSON.stringify(mala));
    }
    assert.equal((await leer('u1'))[1].cosas.a.hecha, false);
    assert.deepEqual(await hecho({ uid: 'u1', id: 'a', ficha: fichas.a }), [200, { ok: true }]);
    assert.deepEqual(await hecho({ uid: 'u1', id: 'a', ficha: fichas.a }), [200, { ok: true }]); // Dos toques: igual.
    const [, tras] = await leer('u1');
    assert.deepEqual([tras.v, tras.cosas.a.hecha, tras.cosas.b.hecha], [2, true, false]);
    // La app, que aún tenía «a» pendiente, quiere guardar sobre la versión de antes: tiene que mirar primero.
    assert.deepEqual(await guardar('u1', 1, { poner: { a: cosa('Comprar leche') } }), [409, tras]);

    // Al día siguiente: «a» (hecha) ya no sale; «b» (nadie pulsó nada, o «Mantener») sale otra vez, ahora sola.
    enviados.length = 0;
    aLas('2026-11-11T08:00:04');
    await programada();
    assert.deepEqual(salidas(), ['movil-1:b', 'tableta-1:b']);
    assert.equal(enviados[0].aviso.cosas[0].ficha, fichas.b); // La ficha de una cosa es siempre la misma.

    // Un dispositivo que ya no existe (410) se olvida; el otro sigue recibiendo.
    respuestas.set(TABLETA, 410);
    enviados.length = 0;
    aLas('2026-11-12T08:00:04');
    await programada();
    assert.deepEqual(salidas(), ['movil-1:b']);
    assert.equal(store.claves('dispositivos/u1/').length, 1);
  } finally {
    acabar();
  }
});

test('de punta a punta: lo que el servicio push no admite ahora se repite en la ejecución siguiente; muchas cosas, en varios mensajes', async () => {
  const consola = empezar('2026-11-10T07:30');
  try {
    await alta('u1', MOVIL);
    const poner = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`c${i}`, cosa(`${i} ${'Llamar al médico y pedir cita '.repeat(9)}`)]));
    await guardar('u1', 0, { poner });
    // A las 9:00 el servicio push está caído (503): no se da nada por avisado.
    respuestas.set(MOVIL, 503);
    aLas('2026-11-10T08:00:02');
    await programada();
    assert.equal(enviados.length, 0);
    assert.ok(consola.includes('log: Recordatorios: 1 sesiones atendidas, 0 cosas avisadas, 0 sesiones en pausa, 0 documentos borrados'));
    // A las 9:05 ya responde: salen las treinta, repartidas en varios mensajes que caben cada uno en un push.
    respuestas.clear();
    aLas('2026-11-10T08:05:02');
    await programada();
    assert.ok(enviados.length > 1 && enviados.length < 30, String(enviados.length));
    for (const { bytes } of enviados) assert.ok(bytes <= 3000, String(bytes));
    assert.deepEqual(enviados.flatMap((e) => e.aviso.cosas.map((c) => c.id)).sort(), Object.keys(poner).sort());
    assert.ok(consola.includes('log: Recordatorios: 1 sesiones atendidas, 30 cosas avisadas, 0 sesiones en pausa, 0 documentos borrados'));
    // Y ya no se repiten.
    enviados.length = 0;
    aLas('2026-11-10T08:10:02');
    await programada();
    assert.equal(enviados.length, 0);
  } finally {
    acabar();
  }
});

test('hecho: lo primero, el tamaño; y sin sesión, solo vale la ficha', async () => {
  empezar('2026-11-10T07:30');
  try {
    await alta('u1', MOVIL);
    const id = '語'.repeat(100); // El id más largo que cabe, con letras de tres bytes.
    await guardar('u1', 0, { poner: { [id]: cosa('Leche') } });
    aLas('2026-11-10T08:00:02');
    await programada();
    const { ficha } = enviados[0].aviso.cosas[0];
    // Más de 1000 bytes: 413, lo diga la cabecera (y entonces ni se lee) o no.
    const largo = JSON.stringify({ uid: 'u1', id, ficha, relleno: 'x'.repeat(1000) });
    assert.deepEqual((await llamar('POST', 'hecho', { bruto: largo })).slice(0, 2), [413, { error: 'demasiado-grande' }]);
    const [estado, , , peticion] = await llamar('POST', 'hecho', { cuerpo: { uid: 'u1', id, ficha }, cabeceras: { 'Content-Length': '1001' } });
    assert.deepEqual([estado, peticion.bodyUsed], [413, false]);
    assert.deepEqual((await llamar('POST', 'hecho', { bruto: 'no es json' })).slice(0, 2), [400, { error: 'json' }]);
    assert.equal((await leer('u1'))[1].cosas[id].hecha, false);
    // El «Hecho» de verdad, con el id más largo, cabe de sobra; y no hace falta sesión.
    assert.ok(Buffer.byteLength(JSON.stringify({ uid: 'u1', id, ficha })) < 1000);
    assert.deepEqual(await hecho({ uid: 'u1', id, ficha }), [200, { ok: true }]);
    assert.equal((await leer('u1'))[1].cosas[id].hecha, true);
    // Hecha, ya no se avisa de ella.
    enviados.length = 0;
    aLas('2026-11-11T08:00:02');
    await programada();
    assert.equal(enviados.length, 0);
    // Una ficha sigue valiendo aunque el documento ya no exista: 200 y no pasa nada (no dice si existe o no).
    await llamar('POST', 'olvidar', { uid: 'u1', cuerpo: {} });
    assert.deepEqual(await hecho({ uid: 'u1', id, ficha }), [200, { ok: true }]);
    assert.deepEqual(store.claves('recordar/'), []);
  } finally {
    acabar();
  }
});

test('olvidar: borra los recordatorios de esta sesión y nada más', async () => {
  empezar('2026-11-10T07:30');
  try {
    await alta('u1', MOVIL);
    await alta('u2', TABLETA);
    const [, antes] = await guardar('u1', 0, { poner: { a: cosa('Leche') } });
    await guardar('u2', 0, { poner: { z: cosa('Lo de otra sesión') } });
    assert.deepEqual((await llamar('POST', 'olvidar', { uid: 'u1', cuerpo: {} })).slice(0, 2), [200, { ok: true }]);
    assert.deepEqual(await leer('u1'), [200, SIN_DOCUMENTO]);
    assert.deepEqual(store.claves('recordar/'), ['recordar/u2']);
    assert.equal(store.claves('dispositivos/').length, 2); // Los dispositivos siguen: los demás avisos no se tocan.
    assert.deepEqual((await llamar('POST', 'olvidar', { uid: 'u1' })).slice(0, 2), [200, { ok: true }]); // Sin nada que borrar, y sin cuerpo: igual.
    // A su hora ya no se le avisa de nada (y a la otra sesión, sí).
    aLas('2026-11-10T08:00:02');
    await programada();
    assert.deepEqual(salidas(), ['tableta-1:z']);
    // Si vuelve a guardar, es un documento nuevo: otro «nacido», y la versión empieza otra vez.
    assert.deepEqual((await guardar('u1', 1, { poner: { a: cosa('Leche') } }))[0], 409);
    const [, despues] = await guardar('u1', 0, { poner: { a: cosa('Leche') } });
    assert.equal(despues.v, 1);
    assert.notEqual(despues.nacido, antes.nacido);
  } finally {
    acabar();
  }
});

test('sin dispositivos: en pausa; el alta la despierta y lo que tocaba hace poco sale con retraso', async () => {
  const consola = empezar('2026-11-10T07:30');
  try {
    await guardar('u1', 0, { poner: { a: cosa('Leche') } }); // Recordatorios, pero ningún dispositivo.
    aLas('2026-11-10T08:00:02');
    await programada();
    assert.equal(enviados.length, 0);
    assert.equal(store.json('recordar/u1').pausado, utc('2026-11-10T08:00:02'));
    assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1']);
    assert.ok(consola.includes('log: Recordatorios: 1 sesiones atendidas, 0 cosas avisadas, 1 sesiones en pausa, 0 documentos borrados'));
    // Leer o guardar sin dispositivos no la despierta: sigue en pausa, y lo que comparte con la app no cambia.
    assert.equal((await leer('u1'))[1].v, 1);
    assert.equal((await guardar('u1', 1, { poner: { b: cosa('Pan') } }))[1].v, 2);
    assert.notEqual(store.json('recordar/u1').pausado, null);
    // A las 9:20 activa las notificaciones en el móvil: el alta la despierta.
    aLas('2026-11-10T08:20:00');
    assert.deepEqual((await alta('u1', MOVIL)).slice(0, 2), [200, { ok: true }]);
    assert.equal(store.json('recordar/u1').pausado, null);
    assert.deepEqual(store.claves('agenda/').filter((clave) => clave.endsWith('/u1')).pop(), 'agenda/202611100820/u1');
    // La ejecución siguiente avisa de lo de las 9:00, con retraso («b», puesta después de las 9:00, es para mañana).
    aLas('2026-11-10T08:25:01');
    await programada();
    assert.deepEqual(salidas(), ['movil-1:a']);
    assert.deepEqual(store.json('recordar/u1').prox, { a: utc('2026-11-11T08:00'), b: utc('2026-11-11T08:00') });
  } finally {
    acabar();
  }
});

test('sin dispositivos: también la despierta la app al leer; y el alta no falla aunque despertar no pueda', async () => {
  const consola = empezar('2026-11-10T07:30');
  try {
    await guardar('u1', 0, { poner: { a: cosa('Leche') } });
    aLas('2026-11-10T08:00:02');
    await programada();
    assert.notEqual(store.json('recordar/u1').pausado, null);
    // El alta llega, pero al ir a despertar falla el almacén: el dispositivo queda dado de alta igual.
    const leerCon = store.getWithMetadata;
    store.getWithMetadata = async () => { throw new Error('Almacén caído'); };
    assert.deepEqual((await alta('u1', MOVIL)).slice(0, 2), [200, { ok: true }]);
    store.getWithMetadata = leerCon;
    assert.ok(consola.some((linea) => linea.startsWith('warn: Al despertar')));
    assert.notEqual(store.json('recordar/u1').pausado, null);
    // La siguiente vez que la app lee sus recordatorios, despierta.
    aLas('2026-11-10T08:10:00');
    assert.equal((await leer('u1'))[1].v, 1);
    assert.equal(store.json('recordar/u1').pausado, null);
    aLas('2026-11-10T08:10:03');
    await programada();
    assert.deepEqual(salidas(), ['movil-1:a']);
  } finally {
    acabar();
  }
});

test('la limpieza: en la ejecución de las 03:00 (UTC) se borran los documentos que llevan un mes en pausa y sin dispositivos', async () => {
  const consola = empezar('2026-11-10T07:30');
  try {
    await guardar('u1', 0, { poner: { a: cosa('Leche') } }); // Nadie volverá a por estos.
    await guardar('u2', 0, { poner: { a: cosa('Pan') } }); // Esta sesión vuelve antes del mes.
    aLas('2026-11-10T08:00:02');
    await programada();
    assert.deepEqual(store.claves('limpieza/'), ['limpieza/20261210/u1', 'limpieza/20261210/u2']);
    aLas('2026-11-20T10:00:00');
    await alta('u2', MOVIL);
    // El día llega, pero la limpieza solo va en la ejecución de las 03:00.
    aLas('2026-12-10T02:55:01');
    await programada();
    aLas('2026-12-10T03:05:01');
    await programada();
    assert.deepEqual(store.claves('recordar/'), ['recordar/u1', 'recordar/u2']);
    aLas('2026-12-10T02:59:58'); // La de las 03:00, que arranca un par de segundos antes.
    await programada();
    assert.deepEqual(store.claves('recordar/'), ['recordar/u2']);
    assert.deepEqual(store.claves('limpieza/'), []);
    assert.ok(consola.pop().endsWith('1 documentos borrados'));
    assert.deepEqual(await leer('u1'), [200, SIN_DOCUMENTO]);
    assert.equal((await leer('u2'))[1].v, 1);
  } finally {
    acabar();
  }
});
