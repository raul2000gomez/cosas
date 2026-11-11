/* «avisar» (netlify/functions/avisos.mjs): lo llaman Cosas con y Cosas de (repositorio Web, js/almacen-firebase.js)
   en cuanto una cosa nueva está guardada, desde cosas.info o desde la app. La función entera, con un almacén, un
   Google, un Firestore y un servicio push de mentira:
   node --test --experimental-test-module-mocks pruebas/servidor/avisar.test.mjs */
import assert from 'node:assert/strict';
import { createECDH, randomBytes } from 'node:crypto';
import { mock, test } from 'node:test';
import { almacenFalso } from './almacen-falso.mjs';

let store = almacenFalso();
const enviados = []; // Lo que sale hacia los servicios push: { endpoint, aviso }.

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
    sendNotification: async (suscripcion, aviso) => {
      enviados.push({ endpoint: suscripcion.endpoint, aviso: JSON.parse(aviso) });
    },
  },
});

const { default: avisos } = await import('../../netlify/functions/avisos.mjs');

const DOCUMENTOS = 'https://firestore.googleapis.com/v1/projects/cosas-info/databases/(default)/documents/';
const CLAVES = () => ({ p256dh: createECDH('prime256v1').generateKeys().toString('base64url'), auth: randomBytes(16).toString('base64url') });
const MOVIL_DE_RAUL = 'https://fcm.googleapis.com/fcm/send/raul-1';
const MOVIL_DE_ANA = 'https://web.push.apple.com/ana-1';
const MOVIL_DE_LUCIA = 'https://updates.push.services.mozilla.com/wpush/v2/lucia-1';

/** Un valor como lo da la API REST de Firestore (las fechas, en milisegundos dentro de { fecha }). */
function campo(v) {
  if (v === null) return { nullValue: null };
  if (typeof v === 'string') return { stringValue: v };
  if (typeof v === 'boolean') return { booleanValue: v };
  if (typeof v === 'number') return { integerValue: String(v) };
  if (Array.isArray(v)) return { arrayValue: { values: v.map(campo) } };
  if ('fecha' in v) return { timestampValue: new Date(v.fecha).toISOString().replace('Z', '123Z') }; // Con microsegundos, como Firestore.
  return { mapValue: { fields: Object.fromEntries(Object.entries(v).map(([k, x]) => [k, campo(x)])) } };
}

/**
 * Firestore de mentira: ruta → documento. Lee con las reglas de firestore.rules (repositorio Web): una lista la puede
 * leer cualquiera con sesión; sus cosas y sus grupos, solo quien está en ella. Apunta cada lectura: [ruta, token].
 */
function firestore(documentos) {
  const lecturas = [];
  mock.method(globalThis, 'fetch', async (url, opciones = {}) => {
    assert.ok(String(url).startsWith(DOCUMENTOS), `Solo se habla con Firestore, no con ${url}`);
    const ruta = String(url).slice(DOCUMENTOS.length);
    const token = (opciones.headers && opciones.headers.Authorization || '').replace('Bearer ', '');
    lecturas.push([ruta, token]);
    if (!token.startsWith('token-')) return new Response('{}', { status: 403 });
    const partes = ruta.split('/');
    const lista = documentos[`listas/${partes[1]}`];
    if (partes.length > 2 && !(lista && lista.uids.includes(token.slice(6)))) return new Response('{}', { status: 403 });
    const documento = documentos[ruta];
    if (!documento) return new Response('{}', { status: 404 });
    return Response.json({ name: ruta, fields: campo(documento).mapValue.fields });
  });
  return lecturas;
}

function empezar() {
  store = almacenFalso();
  enviados.length = 0;
  const consola = [];
  for (const nivel of ['log', 'warn', 'error']) mock.method(console, nivel, (...partes) => consola.push(`${nivel}: ${partes.join(' ')}`));
  return consola;
}

/** Llama a la función como lo haría Netlify. Devuelve [estado, cuerpo, respuesta]. */
async function llamar(metodo, accion, { uid, cuerpo, cabeceras = {} } = {}) {
  const todas = { 'Content-Type': 'application/json', Origin: 'https://cosas.info', ...cabeceras };
  if (uid) todas.Authorization = `Bearer token-${uid}`;
  const peticion = new Request(`https://cosas-app.netlify.app/api/avisos/${accion}`, {
    method: metodo, headers: todas, body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
  const respuesta = await avisos(peticion, { params: { accion } });
  return [respuesta.status, respuesta.status === 204 ? null : await respuesta.json(), respuesta];
}

const alta = async (uid, endpoint) => {
  const [estado] = await llamar('POST', 'alta', { uid, cuerpo: { suscripcion: { endpoint, keys: CLAVES() } } });
  assert.equal(estado, 200, `alta de ${uid}`);
};
const avisar = (uid, lista, cosa) => llamar('POST', 'avisar', { uid, cuerpo: { lista, cosa } });

const hace = (ms) => ({ fecha: Date.now() - ms });
const MIEMBROS = { ana: { nombre: 'Ana', color: '#EF5B5B' }, raul: { nombre: 'Raúl', color: '#2F6FED' }, lucia: { nombre: 'Lucía', color: '#7A4FD6' } };

test('Cosas con: avisa a la otra persona de la lista (no a quien la añade) con su nombre y la cosa; repetirlo no avisa otra vez', async (t) => {
  t.after(() => mock.restoreAll());
  const consola = empezar();
  const lecturas = firestore({
    'listas/conana1': { nombre: '', tipo: 'con', uids: ['ana', 'raul'], miembros: { ana: MIEMBROS.ana, raul: MIEMBROS.raul } },
    'listas/conana1/cosas/C0sa1': { texto: '  Comprar   leche ', hecha: false, creada: hace(2000), por: 'ana', grupo: null },
  });
  await alta('raul', MOVIL_DE_RAUL);
  await alta('ana', MOVIL_DE_ANA);

  const [estado, cuerpo, respuesta] = await avisar('ana', 'conana1', 'C0sa1');
  assert.deepEqual([estado, cuerpo], [200, { enviados: 1 }]);
  assert.equal(respuesta.headers.get('access-control-allow-origin'), '*', 'cosas.info llama desde otro dominio');
  assert.deepEqual(enviados, [{
    endpoint: MOVIL_DE_RAUL,
    aviso: { titulo: 'Cosas con Ana', linea: 'Ana ha añadido «Comprar leche»', url: '/con/conana1', etiqueta: 'lista-conana1' },
  }]);
  assert.deepEqual(lecturas.map(([, token]) => token), ['token-ana', 'token-ana'], 'Firestore se lee con la sesión de quien avisa');

  assert.deepEqual((await avisar('ana', 'conana1', 'C0sa1')).slice(0, 2), [200, { enviados: 0, repetido: true }]);
  assert.equal(enviados.length, 1, 'Una sola vez');
  assert.deepEqual(consola, []);
});

test('Cosas de: a toda la demás gente, con el nombre de la lista y el grupo donde entra la cosa; el aviso abre ese grupo', async (t) => {
  t.after(() => mock.restoreAll());
  empezar();
  firestore({
    'listas/deviaje1': { nombre: 'Viaje', tipo: 'de', uids: ['ana', 'raul', 'lucia'], miembros: MIEMBROS },
    'listas/deviaje1/grupos/G1': { nombre: 'Hotel', color: null, por: 'ana' },
    'listas/deviaje1/cosas/C0sa2': { texto: 'Reservar', hecha: false, creada: hace(1000), por: 'raul', grupo: 'G1' },
  });
  await alta('ana', MOVIL_DE_ANA);
  await alta('lucia', MOVIL_DE_LUCIA);
  await alta('raul', MOVIL_DE_RAUL);

  assert.deepEqual((await avisar('raul', 'deviaje1', 'C0sa2')).slice(0, 2), [200, { enviados: 2 }]);
  const aviso = { titulo: 'Cosas de Viaje', linea: 'Raúl ha añadido «Reservar» en Hotel', url: '/de/deviaje1#grupo/G1', etiqueta: 'lista-deviaje1' };
  // A Ana y a Lucía, no a Raúl, que es quien la ha añadido.
  const porDispositivo = (a, b) => a.endpoint.localeCompare(b.endpoint);
  assert.deepEqual(enviados.sort(porDispositivo), [MOVIL_DE_ANA, MOVIL_DE_LUCIA].map((endpoint) => ({ endpoint, aviso })).sort(porDispositivo));
});

test('lo que no se avisa: sin sesión, ids que no lo son, quien no está en la lista, una cosa ajena o vieja, y una lista sin nadie más', async (t) => {
  t.after(() => mock.restoreAll());
  empezar();
  firestore({
    'listas/conana1': { nombre: '', tipo: 'con', uids: ['ana', 'raul'], miembros: { ana: MIEMBROS.ana, raul: MIEMBROS.raul } },
    'listas/conana1/cosas/DeRaul': { texto: 'Pan', hecha: false, creada: hace(1000), por: 'raul', grupo: null },
    'listas/conana1/cosas/Vieja': { texto: 'Sal', hecha: false, creada: hace(16 * 60 * 1000), por: 'ana', grupo: null },
    'listas/conana1/cosas/Nueva': { texto: 'Café', hecha: false, creada: hace(1000), por: 'ana', grupo: null },
    'listas/sola1': { nombre: '', tipo: 'con', uids: ['ana'], miembros: { ana: MIEMBROS.ana } },
    'listas/sola1/cosas/Nueva': { texto: 'Té', hecha: false, creada: hace(1000), por: 'ana', grupo: null },
  });
  await alta('raul', MOVIL_DE_RAUL);

  assert.deepEqual((await llamar('POST', 'avisar', { cuerpo: { lista: 'conana1', cosa: 'Nueva' } })).slice(0, 2), [401, { error: 'sin-sesion' }]);
  assert.deepEqual((await avisar('ana', 'con/ana', 'Nueva')).slice(0, 2), [400, { error: 'ids' }]);
  assert.deepEqual((await avisar('lucia', 'conana1', 'Nueva')).slice(0, 2), [404, { error: 'no-existe' }], 'Quien no está en la lista no puede leer sus cosas');
  assert.deepEqual((await avisar('ana', 'conana1', 'DeRaul')).slice(0, 2), [403, { error: 'no-es-tuya' }]);
  assert.deepEqual((await avisar('ana', 'conana1', 'Vieja')).slice(0, 2), [409, { error: 'no-es-reciente' }]);
  assert.deepEqual((await avisar('ana', 'conana1', 'NoExiste')).slice(0, 2), [404, { error: 'no-existe' }]);
  assert.deepEqual((await avisar('ana', 'sola1', 'Nueva')).slice(0, 2), [200, { enviados: 0 }]);
  assert.deepEqual(enviados, []);
});

test('desde cosas.info: la pregunta previa del navegador (otro dominio, con «Authorization») recibe permiso', async () => {
  const [estado, , respuesta] = await llamar('OPTIONS', 'avisar', { cabeceras: { 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type' } });
  assert.equal(estado, 204);
  assert.equal(respuesta.headers.get('access-control-allow-origin'), '*');
  assert.match(respuesta.headers.get('access-control-allow-methods'), /POST/);
  assert.match(respuesta.headers.get('access-control-allow-headers'), /Authorization/i);
});
