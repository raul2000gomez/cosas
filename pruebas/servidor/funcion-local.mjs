/* La función de avisos y la programada, las de verdad, servidas en este equipo: para las pruebas de extremo a
   extremo (pruebas/e2e.py), que les ponen la app delante y comprueban que las dos mitades hablan el mismo idioma.

     node --experimental-test-module-mocks pruebas/servidor/funcion-local.mjs [puerto]

   Monta netlify/functions/avisos.mjs en /api/avisos/ACCION (llamada como la llama Netlify: con una Request y
   { params: { accion } }) y netlify/functions/recordatorios.mjs, la programada, tal cual. De mentira solo es lo que
   da al mundo de fuera, como en las pruebas del servidor (funcion.test.mjs):
     @netlify/blobs   el almacén, en memoria (almacen-falso.mjs);
     jose             Google: un token «token-UID» es bueno y es de UID;
     web-push         el servicio push: no sale nada a la red; cada mensaje queda apuntado con su dispositivo, o lo
                      rechaza con el código que las pruebas digan para ese dispositivo.
   Y el reloj es el que digan las pruebas: nada depende de la hora a la que se ejecuten.

   Para mandar en todo eso, /__pruebas/… (que la app no llama nunca):
     POST   /__pruebas/reloj?ahora=MS       pone el reloj en ese instante (y ahí se queda).
     POST   /__pruebas/pasada?ahora=MS      pone el reloj y ejecuta una vez la función programada
                                            → { intentos: [los de esta ejecución], consola: [lo que ha escrito] }.
     GET    /__pruebas/intentos             lo que se ha querido enviar hasta ahora, en orden:
                                            [{ endpoint, estado, aviso, opciones }] («aviso», el texto tal cual salió;
                                            «estado», 201 si el servicio push lo aceptó o el código con el que lo rechazó).
     POST   /__pruebas/fallar               { endpoint, estado }: ese dispositivo rechaza con ese código lo que se le
                                            envíe (con estado null vuelve a aceptarlo).
     GET    /__pruebas/almacen?prefijo=P    lo guardado: { clave: valor }.
     PUT    /__pruebas/almacen?clave=C      guarda el cuerpo, tal cual, en esa clave.
     DELETE /__pruebas/almacen?clave=C      la borra.
     GET    /__pruebas/consola              lo que las funciones han escrito en la consola (aquí no se imprime).

   Al arrancar escribe una línea con su puerto ({"puerto": N}; sin argumento, uno libre) y se apaga cuando se cierra
   su entrada estándar: si quien lo arrancó desaparece sin apagarlo, no se queda ahí. */
import { createECDH } from 'node:crypto';
import { createServer } from 'node:http';
import { mock } from 'node:test';
import { format } from 'node:util';
import { almacenFalso } from './almacen-falso.mjs';

const store = almacenFalso();
const intentos = []; // Lo que sale hacia los servicios push: { endpoint, estado, aviso, opciones }.
const rechazos = new Map(); // endpoint → el código de estado con el que ese dispositivo rechaza lo que se le envía.
const consola = [];
const ACEPTADO = 201; // Lo que contesta un servicio push cuando se queda con el mensaje.

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
    // Unas claves como las de verdad (un punto de la curva P-256 y su secreto): un navegador se suscribiría con ellas.
    generateVAPIDKeys: () => {
      const curva = createECDH('prime256v1');
      curva.generateKeys();
      return { publicKey: curva.getPublicKey('base64url'), privateKey: curva.getPrivateKey('base64url') };
    },
    sendNotification: async (suscripcion, aviso, { vapidDetails, ...opciones }) => {
      const estado = rechazos.has(suscripcion.endpoint) ? rechazos.get(suscripcion.endpoint) : ACEPTADO;
      intentos.push({ endpoint: suscripcion.endpoint, estado, aviso: String(aviso), opciones });
      // Como el web-push de verdad cuando el servicio push no lo acepta: un error con su código de estado.
      if (estado !== ACEPTADO) throw Object.assign(new Error('Rechazado por el servicio push'), { statusCode: estado, body: '', endpoint: suscripcion.endpoint });
      return { statusCode: ACEPTADO, body: '', headers: {} };
    },
  },
});

// Lo que las funciones escriben en la consola se guarda para quien lo pida: por la salida solo va la línea del puerto.
for (const nivel of ['log', 'warn', 'error']) console[nivel] = (...partes) => consola.push(`${nivel}: ${format(...partes)}`);

const { default: avisos } = await import('../../netlify/functions/avisos.mjs');
const { default: programada } = await import('../../netlify/functions/recordatorios.mjs');

let conReloj = false;

/** Pone el reloj de las funciones (solo Date: los temporizadores siguen corriendo) en ese instante. */
function ponerReloj(ahora) {
  if (conReloj) mock.timers.setTime(ahora);
  else mock.timers.enable({ apis: ['Date'], now: ahora });
  conReloj = true;
}

class Mal extends Error {}

/** El instante que pide la dirección (?ahora=MS). */
function instanteDe(url) {
  const ahora = Number(url.searchParams.get('ahora'));
  if (!url.searchParams.has('ahora') || !Number.isSafeInteger(ahora) || ahora < 0) throw new Mal('Falta «ahora» (milisegundos)');
  return ahora;
}

function claveDe(url) {
  const clave = url.searchParams.get('clave');
  if (!clave) throw new Mal('Falta «clave»');
  return clave;
}

/** Lo guardado bajo ese prefijo: lo que es JSON, como objeto; lo demás, como texto. */
function guardadoCon(prefijo) {
  return Object.fromEntries(store.claves(prefijo).map((clave) => {
    const { valor } = store.datos.get(clave);
    try {
      return [clave, JSON.parse(valor)];
    } catch (error) {
      return [clave, valor];
    }
  }));
}

/** Lo que piden las pruebas: [estado, datos]. */
async function deLasPruebas(metodo, url, cuerpo) {
  const que = `${metodo} ${url.pathname.slice('/__pruebas/'.length)}`;
  if (que === 'POST reloj') {
    ponerReloj(instanteDe(url));
    return [200, { ahora: Date.now() }];
  }
  if (que === 'POST pasada') {
    const [desde, dicho] = [intentos.length, consola.length];
    ponerReloj(instanteDe(url));
    await programada();
    return [200, { intentos: intentos.slice(desde), consola: consola.slice(dicho) }];
  }
  if (que === 'GET intentos') return [200, intentos];
  if (que === 'POST fallar') {
    const { endpoint, estado } = JSON.parse(cuerpo.toString());
    if (typeof endpoint !== 'string' || !(estado === null || Number.isInteger(estado))) throw new Mal('Hace falta { endpoint, estado }');
    if (estado === null) rechazos.delete(endpoint);
    else rechazos.set(endpoint, estado);
    return [200, { ok: true }];
  }
  if (que === 'GET almacen') return [200, guardadoCon(url.searchParams.get('prefijo') || '')];
  if (que === 'PUT almacen') {
    await store.set(claveDe(url), cuerpo.toString());
    return [200, { ok: true }];
  }
  if (que === 'DELETE almacen') {
    await store.delete(claveDe(url));
    return [200, { ok: true }];
  }
  if (que === 'GET consola') return [200, consola];
  return [404, { error: 'no-existe' }];
}

// Cabeceras que son de cada tramo de la conexión, no de la petición: la función no las vería en Netlify.
const DEL_TRAMO = ['connection', 'keep-alive', 'transfer-encoding', 'host'];

/** Llama a la función de avisos como lo haría Netlify y devuelve su Response. */
function deLaFuncion(metodo, url, cabeceras, cuerpo) {
  const accion = decodeURIComponent(url.pathname.slice('/api/avisos/'.length));
  const suyas = new Headers();
  for (const [nombre, valor] of Object.entries(cabeceras)) if (typeof valor === 'string' && !DEL_TRAMO.includes(nombre)) suyas.set(nombre, valor);
  const peticion = new Request(`https://cosas.test${url.pathname}${url.search}`, {
    method: metodo, headers: suyas, body: metodo === 'GET' || metodo === 'HEAD' ? undefined : cuerpo,
  });
  return avisos(peticion, { params: { accion } });
}

async function atender(peticion, respuesta) {
  const url = new URL(peticion.url, 'http://127.0.0.1');
  const trozos = [];
  for await (const trozo of peticion) trozos.push(trozo);
  const cuerpo = Buffer.concat(trozos);
  if (url.pathname.startsWith('/api/avisos/')) {
    const suya = await deLaFuncion(peticion.method, url, peticion.headers, cuerpo);
    respuesta.writeHead(suya.status, Object.fromEntries(suya.headers));
    respuesta.end(Buffer.from(await suya.arrayBuffer()));
    return;
  }
  const [estado, datos] = url.pathname.startsWith('/__pruebas/') ? await deLasPruebas(peticion.method, url, cuerpo) : [404, { error: 'no-existe' }];
  respuesta.writeHead(estado, { 'Content-Type': 'application/json; charset=utf-8' });
  respuesta.end(JSON.stringify(datos));
}

const servidor = createServer((peticion, respuesta) => {
  atender(peticion, respuesta).catch((error) => {
    // Lo que se pidió mal (400) o lo que ha fallado aquí o en la programada (500), con su porqué: lo lee quien prueba.
    if (!respuesta.headersSent) respuesta.writeHead(error instanceof Mal || error instanceof SyntaxError ? 400 : 500, { 'Content-Type': 'application/json; charset=utf-8' });
    respuesta.end(JSON.stringify({ error: String((error && error.stack) || error) }));
  });
});

servidor.listen(Number(process.argv[2]) || 0, '127.0.0.1', () => {
  process.stdout.write(`${JSON.stringify({ puerto: servidor.address().port })}\n`);
});
process.stdin.on('end', () => process.exit(0));
process.stdin.on('error', () => process.exit(0));
process.stdin.resume();
