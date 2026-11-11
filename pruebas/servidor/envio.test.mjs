/* Pruebas de netlify/lib/envio.mjs (las fichas, los mensajes push de los recordatorios y su envío), con un servicio
   push de mentira:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { almacenFalso } from './almacen-falso.mjs';

const enviados = []; // Lo que sale hacia los servicios push: { endpoint, aviso, opciones }.
// Lo que responde cada dispositivo: nada (lo acepta), un código de estado, 'tarde' (no contesta a tiempo), 'sin-red',
// o una función del aviso que dé una de esas cosas.
const respuestas = new Map();

mock.module('web-push', {
  defaultExport: {
    generateVAPIDKeys: () => ({ publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }),
    sendNotification: async (suscripcion, aviso, opciones) => {
      enviados.push({ endpoint: suscripcion.endpoint, aviso: JSON.parse(aviso), opciones });
      const dicho = respuestas.get(suscripcion.endpoint);
      const respuesta = typeof dicho === 'function' ? dicho(JSON.parse(aviso)) : dicho;
      if (respuesta === 'tarde') throw new Error('Socket timeout');
      if (respuesta === 'sin-red') throw Object.assign(new Error('getaddrinfo ENOTFOUND fcm.googleapis.com'), { code: 'ENOTFOUND' });
      if (respuesta) throw Object.assign(new Error('Received unexpected response code'), { statusCode: respuesta, body: 'no' });
      return { statusCode: 201 };
    },
  },
});

const {
  avisoDe, dispositivosDe, enviarA, enviarRecordatorios, fichaCon, fichaValida, limpiar, llavesDe, mensajesDe, secretoDe,
} = await import('../../netlify/lib/envio.mjs');
const { CADUCA } = await import('../../netlify/lib/recordar.mjs');

const CLAVE = 'clave-de-prueba';
const LLAVES = { claves: { publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }, secreto: CLAVE };
const MOVIL = 'https://fcm.googleapis.com/fcm/send/movil';
const TABLETA = 'https://web.push.apple.com/tableta';
const cosa = (id, texto = `Cosa ${id}`, de = 1000) => ({ id, texto, de });

/** Un almacén con esos dispositivos dados de alta en «u1», como los deja la función de avisos: [store, dispositivos]. */
async function conDispositivos(...endpoints) {
  const store = almacenFalso();
  for (const [i, endpoint] of endpoints.entries()) {
    await store.setJSON(`dispositivos/u1/h${i}`, { endpoint, keys: { p256dh: 'p', auth: 'a' }, desde: 100 - i });
    await store.set(`suscripciones/h${i}`, 'u1');
  }
  return [store, await dispositivosDe(store, 'u1')];
}

/** Envía los recordatorios de esas cosas y devuelve [los ids que se dan por avisados, lo que salió («dispositivo:ids»)]. */
async function enviar(store, dispositivos, cosas, seguir) {
  enviados.length = 0;
  const avisos = console.warn;
  console.warn = () => {};
  try {
    const entregadas = await enviarRecordatorios(store, LLAVES, 'u1', dispositivos, cosas, seguir);
    return [entregadas.map((c) => c.id), enviados.map((e) => `${e.endpoint.split('/').pop()}:${e.aviso.cosas.map((c) => c.id).join('+')}`)];
  } finally {
    console.warn = avisos;
  }
}

test('llavesDe: si el almacén no responde, lanza (la programada acaba sin haber tocado nada)', async () => {
  const caido = almacenFalso();
  caido.get = async () => { throw new Error('Almacén caído'); };
  await assert.rejects(() => llavesDe(caido), /Almacén caído/);
});

test('llavesDe y las fichas: las claves se crean solas, viven en el almacén y solo vale la ficha de esa cosa de esa sesión', async () => {
  const store = almacenFalso();
  const llaves = await llavesDe(store);
  assert.deepEqual(llaves.claves, { publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' });
  assert.match(llaves.secreto, /^[A-Za-z0-9_-]{43}$/);
  assert.deepEqual([await store.get('secreto'), store.json('vapid')], [llaves.secreto, llaves.claves]);
  assert.equal(await secretoDe(store), llaves.secreto);
  const ficha = fichaCon(llaves.secreto, 'u1', 'a');
  assert.match(ficha, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(fichaCon(llaves.secreto, 'u1', 'a'), ficha);
  assert.notEqual(fichaCon('otra-clave', 'u1', 'a'), ficha);
  assert.equal(await fichaValida(store, 'u1', 'a', ficha), true);
  for (const [uid, id, dada] of [['u2', 'a', ficha], ['u1', 'b', ficha], ['u1', 'a', `${ficha}x`], ['u1', 'a', ''], ['u1', 'a', null], ['u1', 'a', 'x'.repeat(500)], ['u1\na', '', ficha], ['u', '1\na', ficha]]) {
    assert.equal(await fichaValida(store, uid, id, dada), false, `${uid} ${id}`);
  }
});

test('dispositivosDe: los de esa sesión, del último que se dio de alta al primero, y como mucho diez', async () => {
  const store = almacenFalso();
  assert.deepEqual(await dispositivosDe(store, 'u1'), []);
  for (let i = 0; i < 12; i += 1) await store.setJSON(`dispositivos/u1/h${i}`, { endpoint: `https://fcm.googleapis.com/fcm/send/${i}`, keys: {}, desde: 1000 + ((i * 7) % 12) });
  await store.setJSON('dispositivos/u1/sin-fecha', { endpoint: 'https://fcm.googleapis.com/fcm/send/viejo', keys: {} });
  await store.set('dispositivos/u1/raro', '"no es una suscripción"');
  await store.setJSON('dispositivos/u10/h1', { endpoint: 'https://fcm.googleapis.com/fcm/send/de-otra', keys: {}, desde: 9999 });
  const todos = await dispositivosDe(store, 'u1');
  assert.equal(todos.length, 10);
  assert.deepEqual(todos.map((d) => d.suscripcion.desde), [1011, 1010, 1009, 1008, 1007, 1006, 1005, 1004, 1003, 1002]);
  assert.deepEqual(todos[0], { huella: 'h5', suscripcion: { endpoint: 'https://fcm.googleapis.com/fcm/send/5', keys: {}, desde: 1011 } });
  assert.equal((await dispositivosDe(store, 'u1', 3)).length, 3);
  assert.equal((await dispositivosDe(store, 'u1', 20)).length, 13); // Sin fecha, el último; lo que no es una suscripción no cuenta.
  assert.equal((await dispositivosDe(store, 'u1', 20)).pop().huella, 'sin-fecha');
  // Si una lectura falla, lanza: no puede parecer que la sesión no tiene dispositivos.
  const leer = store.get;
  store.get = async (clave, opciones) => {
    if (clave.endsWith('/h3')) throw new Error('Almacén caído');
    return leer(clave, opciones);
  };
  await assert.rejects(() => dispositivosDe(store, 'u1'), /Almacén caído/);
});

test('el mensaje de una cosa: lo que necesita el service worker, y lo que enseña uno de antes', () => {
  const aviso = JSON.parse(avisoDe('u1', [cosa('a b/ñ', '  Comprar \n\t leche  ')], CLAVE));
  assert.deepEqual(aviso, {
    tipo: 'recordar',
    uid: 'u1',
    cosas: [{ id: 'a b/ñ', texto: 'Comprar leche', ficha: fichaCon(CLAVE, 'u1', 'a b/ñ') }],
    titulo: 'Recordar',
    linea: 'Comprar leche',
    url: '/?recordada=a%20b%2F%C3%B1#grupo/recordar',
    etiqueta: 'recordar-a b/ñ',
  });
  assert.deepEqual(Object.keys(aviso), ['tipo', 'uid', 'cosas', 'titulo', 'linea', 'url', 'etiqueta']);
  // El texto, en una línea y hasta 300 caracteres; la línea de un service worker de antes, hasta 120.
  const largo = JSON.parse(avisoDe('u1', [cosa('a', `Comprar leche ${'x'.repeat(400)}`)], CLAVE));
  assert.ok(largo.cosas[0].texto.startsWith('Comprar leche x') && largo.cosas[0].texto.length === 300 && largo.cosas[0].texto.endsWith('…'));
  assert.ok(largo.linea.length === 120 && largo.linea.endsWith('…'));
  // Ni caracteres de control ni medio emoji: al cortar, el emoji entero o nada.
  const raro = JSON.parse(avisoDe('u1', [cosa('a', `Uno\u0000dos\u0007\u001b tres cuatro \ud83d ${'😀'.repeat(200)}`)], CLAVE));
  assert.ok(raro.cosas[0].texto.startsWith('Uno dos tres cuatro � 😀'));
  assert.ok(raro.cosas[0].texto.length <= 300 && raro.cosas[0].texto.endsWith('😀…') && raro.cosas[0].texto.isWellFormed());
  assert.ok(raro.linea.length <= 120 && raro.linea.endsWith('😀…') && raro.linea.isWellFormed());
});

test('el mensaje de varias cosas: todas con su ficha, y una línea y un enlace para el service worker de antes', () => {
  const aviso = JSON.parse(avisoDe('u1', [cosa('a', 'Leche'), cosa('b', 'Pan'), cosa('c', 'Sal')], CLAVE));
  assert.deepEqual(aviso, {
    tipo: 'recordar',
    uid: 'u1',
    cosas: [{ id: 'a', texto: 'Leche', ficha: fichaCon(CLAVE, 'u1', 'a') }, { id: 'b', texto: 'Pan', ficha: fichaCon(CLAVE, 'u1', 'b') }, { id: 'c', texto: 'Sal', ficha: fichaCon(CLAVE, 'u1', 'c') }],
    titulo: 'Recordar',
    linea: '«Leche» y 2 más',
    url: '/#grupo/recordar',
    etiqueta: 'recordar',
  });
  assert.equal(JSON.parse(avisoDe('u1', [cosa('a', 'Leche'), cosa('b', 'Pan')], CLAVE)).linea, '«Leche» y 1 más');
  const largo = JSON.parse(avisoDe('u1', [cosa('a', 'x'.repeat(400)), cosa('b', 'Pan')], CLAVE));
  assert.ok(largo.linea.length === 120 && largo.linea.endsWith('…» y 1 más'));
});

test('mensajesDe: cada mensaje cabe en 3000 bytes, también con letras de varios bytes; lo que no cabe va en el siguiente', () => {
  const tamano = (mensaje) => Buffer.byteLength(mensaje.aviso);
  // Pocas y cortas: un solo mensaje.
  const pocas = mensajesDe('u1', [cosa('a', 'Leche', 1), cosa('b', 'Pan', 2)], CLAVE);
  assert.equal(pocas.length, 1);
  assert.deepEqual(pocas[0].cosas, [{ id: 'a', de: 1 }, { id: 'b', de: 2 }]);
  assert.equal(JSON.parse(pocas[0].aviso).etiqueta, 'recordar');
  // Cuarenta con textos largos en japonés, emojis y eñes: varios mensajes, todos dentro del límite y sin perder ni repetir ninguna.
  const textos = ['牛乳を買う'.repeat(40), '😀 Llamar al médico '.repeat(12), 'Año nuevo, ñandú y cigüeña '.repeat(9), 'Pan'];
  const muchas = Array.from({ length: 40 }, (_, i) => cosa(`cosa-${i}`, textos[i % textos.length], 1000 + i));
  const mensajes = mensajesDe('u'.repeat(28), muchas, CLAVE);
  assert.ok(mensajes.length > 3 && mensajes.length < 40, String(mensajes.length));
  for (const mensaje of mensajes) assert.ok(tamano(mensaje) <= 3000, String(tamano(mensaje)));
  assert.ok(mensajes.some((mensaje) => tamano(mensaje) > 2000)); // Y se aprovechan: no va una cosa por mensaje.
  assert.deepEqual(mensajes.flatMap((mensaje) => mensaje.cosas), muchas.map(({ id, de }) => ({ id, de })));
  for (const mensaje of mensajes) {
    const aviso = JSON.parse(mensaje.aviso);
    assert.deepEqual(aviso.cosas.map((c) => c.id), mensaje.cosas.map((c) => c.id));
    assert.deepEqual([aviso.etiqueta, aviso.url], aviso.cosas.length > 1 ? ['recordar', '/#grupo/recordar'] : [`recordar-${aviso.cosas[0].id}`, `/?recordada=${aviso.cosas[0].id}#grupo/recordar`]);
    for (const c of aviso.cosas) assert.equal(c.ficha, fichaCon(CLAVE, 'u'.repeat(28), c.id));
  }
  assert.deepEqual(mensajesDe('u1', [], CLAVE), []);
  // El peor caso de una sola cosa (id de 100 letras de tres bytes, o de control, y texto largo): cabe, acortando el texto.
  for (const id of ['語'.repeat(100), '\u0001'.repeat(100), '"'.repeat(100), '😀'.repeat(50)]) {
    const peores = mensajesDe('u'.repeat(40), [cosa(id, '語'.repeat(500))], CLAVE);
    assert.equal(peores.length, 1);
    assert.ok(tamano(peores[0]) <= 3000, `${id.slice(0, 2)} ${tamano(peores[0])}`);
    const aviso = JSON.parse(peores[0].aviso);
    assert.deepEqual([aviso.cosas.length, aviso.cosas[0].id, aviso.etiqueta, aviso.url], [1, id, `recordar-${id}`, `/?recordada=${encodeURIComponent(id)}#grupo/recordar`]);
    assert.ok(aviso.cosas[0].texto.length >= 100 && aviso.cosas[0].texto.length <= 300);
    // Con otra cosa al lado ocupa menos (el id ya no va en la etiqueta ni en el enlace): viajan juntas, y enteras.
    const juntas = mensajesDe('u'.repeat(40), [cosa(id, '語'.repeat(500)), cosa('b', 'Pan')], CLAVE);
    assert.equal(juntas.length, 1);
    assert.ok(tamano(juntas[0]) <= 3000);
    assert.equal(JSON.parse(juntas[0].aviso).cosas[0].texto.length, 300);
  }
});

test('enviarRecordatorios: a todos los dispositivos, con prisa, seis horas de vida y sin tema', async () => {
  const [store, dispositivos] = await conDispositivos(MOVIL, TABLETA);
  respuestas.clear();
  assert.deepEqual(await enviar(store, dispositivos, [cosa('a', 'Leche', 5), cosa('b', 'Pan', 6)]), [['a', 'b'], ['movil:a+b', 'tableta:a+b']]);
  const { aviso, opciones } = enviados[0];
  assert.deepEqual(aviso, JSON.parse(avisoDe('u1', [cosa('a', 'Leche'), cosa('b', 'Pan')], CLAVE)));
  assert.deepEqual(opciones, { vapidDetails: { subject: 'https://cosas.info', publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }, TTL: CADUCA / 1000, urgency: 'high', timeout: 5000 });
  assert.equal(opciones.TTL, 6 * 3600);
  // Devuelve cada cosa con el instante por el que se la tomó: es lo que quien llama confirma.
  enviados.length = 0;
  assert.deepEqual(await enviarRecordatorios(store, LLAVES, 'u1', dispositivos, [cosa('a', 'Leche', 5), cosa('b', 'Pan', 6)]), [{ id: 'a', de: 5 }, { id: 'b', de: 6 }]);
  assert.deepEqual(await enviar(store, dispositivos, []), [[], []]);
});

test('enviarRecordatorios: qué se da por avisado según lo que responda cada dispositivo', async () => {
  const casos = [
    // [lo que responde el móvil, lo que responde la tableta, ¿avisado?, dispositivos que quedan]
    [null, null, true, 2],
    [null, 500, true, 2], // Con que llegue a uno, vale.
    [null, 410, true, 1],
    [500, 500, false, 2], // Los que merecen otro intento: no se confirma, y se repite.
    [503, null, true, 2],
    [429, 429, false, 2],
    ['tarde', 'tarde', false, 2],
    ['sin-red', 500, false, 2],
    [403, 400, true, 2], // Lo rechazan y lo seguirán rechazando: no hay nada que repetir.
    [413, 'tarde', false, 2], // …pero mientras uno merezca otro intento, se repite.
    [410, 404, false, 0], // Ya no existen: se olvidan, y lo pendiente espera a que la sesión vuelva.
    [410, 403, true, 1],
    [404, 500, false, 1],
  ];
  for (const [movil, tableta, avisado, quedan] of casos) {
    const [store, dispositivos] = await conDispositivos(MOVIL, TABLETA);
    respuestas.clear();
    respuestas.set(MOVIL, movil);
    respuestas.set(TABLETA, tableta);
    const [entregadas, salieron] = await enviar(store, dispositivos, [cosa('a')]);
    const caso = `${movil} ${tableta}`;
    assert.deepEqual(entregadas, avisado ? ['a'] : [], caso);
    assert.deepEqual(salieron, ['movil:a', 'tableta:a'], caso);
    assert.equal(store.claves('dispositivos/u1/').length, quedan, caso);
    assert.equal(store.claves('suscripciones/').length, quedan, caso); // Al que se olvida, del todo.
  }
});

test('enviarRecordatorios: un solo dispositivo, y sin ninguno no se da nada por avisado', async () => {
  respuestas.clear();
  const [store, dispositivos] = await conDispositivos(MOVIL);
  assert.deepEqual(await enviar(store, dispositivos, [cosa('a')]), [['a'], ['movil:a']]);
  respuestas.set(MOVIL, 502);
  assert.deepEqual(await enviar(store, dispositivos, [cosa('a')]), [[], ['movil:a']]);
  respuestas.set(MOVIL, 401);
  assert.deepEqual(await enviar(store, dispositivos, [cosa('a')]), [['a'], ['movil:a']]);
  assert.deepEqual(await enviar(store, [], [cosa('a')]), [[], []]);
});

test('enviarRecordatorios: varios mensajes van uno detrás de otro, y vuelve solo lo de los que han llegado', async () => {
  const largas = Array.from({ length: 12 }, (_, i) => cosa(`c${i}`, `${i} ${'x'.repeat(400)}`, 2000 + i));
  const mensajes = mensajesDe('u1', largas, CLAVE).map((mensaje) => mensaje.cosas.map((c) => c.id));
  assert.equal(mensajes.length, 2);
  const [primero, segundo] = mensajes.map((ids) => ids.join('+'));
  // Todo va bien: cada mensaje a los dos dispositivos, y el segundo mensaje después del primero.
  respuestas.clear();
  let [store, dispositivos] = await conDispositivos(MOVIL, TABLETA);
  assert.deepEqual(await enviar(store, dispositivos, largas), [largas.map((c) => c.id), [`movil:${primero}`, `tableta:${primero}`, `movil:${segundo}`, `tableta:${segundo}`]]);
  // El segundo mensaje no llega a nadie: solo se dan por avisadas las cosas del primero.
  const fallaElSegundo = (aviso) => (aviso.cosas[0].id === mensajes[1][0] ? 500 : null);
  respuestas.set(MOVIL, fallaElSegundo);
  respuestas.set(TABLETA, fallaElSegundo);
  assert.deepEqual((await enviar(store, dispositivos, largas))[0], mensajes[0]);
  // Un dispositivo que ya no existe se olvida y no se le envía lo siguiente.
  respuestas.clear();
  respuestas.set(TABLETA, 410);
  assert.deepEqual(await enviar(store, dispositivos, largas), [largas.map((c) => c.id), [`movil:${primero}`, `tableta:${primero}`, `movil:${segundo}`]]);
  assert.deepEqual(store.claves('dispositivos/u1/'), ['dispositivos/u1/h0']);
  // Si el único que había se da de baja con el primer mensaje, el segundo ni sale ni se da por avisado.
  [store, dispositivos] = await conDispositivos(MOVIL);
  respuestas.clear();
  respuestas.set(MOVIL, 410);
  assert.deepEqual(await enviar(store, dispositivos, largas), [[], [`movil:${primero}`]]);
  // Sin tiempo para más mensajes: lo que no ha salido no se da por avisado (lo repetirá otra pasada).
  [store, dispositivos] = await conDispositivos(MOVIL);
  respuestas.clear();
  let turnos = 0;
  assert.deepEqual(await enviar(store, dispositivos, largas, () => (turnos += 1) <= 1), [mensajes[0], [`movil:${primero}`]]);
  assert.deepEqual(await enviar(store, dispositivos, largas, () => false), [[], []]);
});

test('enviarRecordatorios: si al olvidar un dispositivo falla el almacén, el envío sigue', async () => {
  const [store, dispositivos] = await conDispositivos(MOVIL, TABLETA);
  respuestas.clear();
  respuestas.set(TABLETA, 410);
  store.delete = async () => { throw new Error('Almacén caído'); };
  assert.deepEqual(await enviar(store, dispositivos, [cosa('a')]), [['a'], ['movil:a', 'tableta:a']]);
});

test('enviarA (los avisos de Cosas con y Cosas de): como siempre, a todos los dispositivos de la sesión', async () => {
  const [store] = await conDispositivos(MOVIL, TABLETA);
  respuestas.clear();
  respuestas.set(TABLETA, 410);
  enviados.length = 0;
  assert.equal(await enviarA(store, LLAVES.claves, 'u1', JSON.stringify({ titulo: 'Cosas con Ana', cosas: [] })), 1);
  assert.deepEqual(enviados.map((e) => e.endpoint).sort(), [MOVIL, TABLETA]);
  assert.deepEqual(enviados[0].opciones, { vapidDetails: { subject: 'https://cosas.info', publicKey: 'CLAVE-PUBLICA', privateKey: 'CLAVE-PRIVADA' }, TTL: 86400, urgency: 'normal', timeout: 8000 });
  assert.deepEqual(store.claves('dispositivos/u1/'), ['dispositivos/u1/h0']); // El que ya no existe se olvida.
  assert.equal(await enviarA(store, LLAVES.claves, 'nadie', '{}'), 0);
  assert.equal(limpiar('  Comprar \n leche  '), 'Comprar leche');
  assert.equal(limpiar('x'.repeat(50)), `${'x'.repeat(39)}…`);
});
