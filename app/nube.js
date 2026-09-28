/* Cosas · tus cosas en la nube.
   Con sesión (la cuenta de Google de Cosas con y Cosas de, que se inicia en cuenta/), las cosas, los
   grupos, el color y el nombre se guardan también en Firestore, en personales/{uid}/elementos, y
   llegan a cualquier dispositivo con la misma cuenta. El almacenamiento local sigue mandando: la app
   funciona igual sin conexión y esto solo reconcilia lo local con la nube cuando puede.

   Cada cosa, cada grupo y los ajustes son un documento. Para saber quién ha cambiado qué se recuerda
   la última versión en la que los dos lados coincidían (la «base», en localStorage): lo que cambió
   solo en la nube se trae; lo que cambió solo aquí se sube; si cambió en los dos, gana este
   dispositivo, salvo los ajustes la primera vez que se entra con una cuenta (mandan los de la cuenta).
   La base solo avanza con lo que la nube ha confirmado: lo pendiente de subir al cerrar la app se
   vuelve a subir la próxima vez.

   Aquí también se vigilan las novedades de Cosas con y Cosas de (vigilarNovedades): el punto verde de sus
   iconos en la pantalla principal. Y se da la sesión con la que este dispositivo recibe sus avisos
   (sesionDeAvisos): las notificaciones.

   Sin efectos al importarlo: Firebase se carga la primera vez que se pide (conectar, vigilarNovedades o
   sesionDeAvisos). */

const VERSION_SDK = '12.4.0';
const CDN = `https://www.gstatic.com/firebasejs/${VERSION_SDK}/`;
const CLAVE_BASE = 'cosas:nube';
const AJUSTES = 'ajustes';
const RETARDO = 400; // Los cambios seguidos (varios toques) viajan juntos.
const MAX_LOTE = 400; // Firestore admite 500 operaciones por lote.
const MAX_ID = 200;

// ---------- Documentos ----------

/** Id del documento de una cosa («c-…») o de un grupo («g-…»); null si el id no se puede usar. */
export function claveDe(tipo, id) {
  if (typeof id !== 'string' || id === '' || id.length > MAX_ID) return null;
  return `${tipo === 'grupo' ? 'g' : 'c'}-${encodeURIComponent(id)}`;
}

function idDeClave(clave) {
  try {
    return decodeURIComponent(clave.slice(2));
  } catch (error) {
    return null;
  }
}

const docCosa = (c) => ({ tipo: 'cosa', id: c.id, texto: c.texto, hecha: c.hecha, creada: c.creada, hechaEn: c.hechaEn, grupo: c.grupo });
// «abierto» (el ojo de la lista) es de cada dispositivo: no viaja.
const docGrupo = (g) => ({ tipo: 'grupo', id: g.id, nombre: g.nombre, color: g.color, creada: g.creada });
const docAjustes = (a) => ({ tipo: AJUSTES, colorFondo: a.colorFondo, nombre: a.nombre });

/** Lo local, como documentos: clave → documento. */
export function elementosDe(estado) {
  const elementos = new Map();
  for (const cosa of estado.cosas) {
    const clave = claveDe('cosa', cosa.id);
    if (clave) elementos.set(clave, docCosa(cosa));
  }
  for (const grupo of estado.grupos) {
    const clave = claveDe('grupo', grupo.id);
    if (clave) elementos.set(clave, docGrupo(grupo));
  }
  elementos.set(AJUSTES, docAjustes(estado.ajustes));
  return elementos;
}

const esTexto = (v) => typeof v === 'string';
const esNumero = (v) => typeof v === 'number' && Number.isFinite(v);

/** Un documento de la nube con la forma que espera la app, o null si no la tiene (o su id no casa). */
export function deLaNube(clave, datos) {
  if (!datos || typeof datos !== 'object') return null;
  if (clave === AJUSTES) {
    return datos.tipo === AJUSTES && esTexto(datos.colorFondo) && esTexto(datos.nombre) ? docAjustes(datos) : null;
  }
  const tipo = clave.startsWith('g-') ? 'grupo' : 'cosa';
  if (datos.tipo !== tipo || claveDe(tipo, datos.id) !== clave || !esNumero(datos.creada)) return null;
  if (tipo === 'grupo') {
    if (!esTexto(datos.nombre)) return null;
    return docGrupo({ ...datos, color: esTexto(datos.color) ? datos.color : null });
  }
  if (!esTexto(datos.texto) || typeof datos.hecha !== 'boolean') return null;
  return docCosa({ ...datos, hechaEn: esNumero(datos.hechaEn) ? datos.hechaEn : null, grupo: esTexto(datos.grupo) ? datos.grupo : null });
}

const firma = (documento) => (documento ? JSON.stringify(documento) : '');

function poner(mapa, clave, documento) {
  if (documento) mapa.set(clave, documento);
  else mapa.delete(clave);
}

// ---------- Reconciliación ----------

/**
 * Compara lo local, la base y la nube documento a documento. Devuelve lo que hay que subir
 * («escribir»: clave → documento, o null para borrarlo), lo que hay que traer («aplicar», igual)
 * y la base nueva. Las claves de «ocupados» (una subida en curso o fallida) no se tocan.
 */
export function reconciliar(local, base, nube, ocupados = new Set()) {
  const escribir = new Map();
  const aplicar = new Map();
  const nuevaBase = new Map(base);
  for (const clave of new Set([...local.keys(), ...base.keys(), ...nube.keys()])) {
    if (ocupados.has(clave)) continue;
    const l = local.get(clave);
    const b = base.get(clave);
    const n = nube.get(clave);
    if (firma(l) === firma(n)) {
      poner(nuevaBase, clave, n); // Ya coinciden.
    } else if (firma(l) === firma(b) || (clave === AJUSTES && !b && n)) {
      // Solo ha cambiado la nube (o son los ajustes de una cuenta con la que aún no se había entrado aquí).
      aplicar.set(clave, n || null);
      poner(nuevaBase, clave, n);
    } else {
      escribir.set(clave, l || null); // Ha cambiado aquí (y, si también en la nube, gana este dispositivo).
    }
  }
  return { escribir, aplicar, base: nuevaBase };
}

/** El estado local con los documentos de la nube aplicados (null = borrado). */
export function estadoCon(estado, cambios) {
  let { cosas, grupos, ajustes } = estado;
  for (const [clave, documento] of cambios) {
    if (clave === AJUSTES) {
      if (documento) ajustes = { ...ajustes, colorFondo: documento.colorFondo, nombre: documento.nombre };
      continue;
    }
    const id = documento ? documento.id : idDeClave(clave);
    if (clave.startsWith('g-')) {
      const antes = grupos.find((grupo) => grupo.id === id);
      grupos = grupos.filter((grupo) => grupo.id !== id);
      if (documento) {
        const { tipo, ...grupo } = documento;
        grupos.push({ ...grupo, abierto: antes ? antes.abierto : false });
      }
    } else {
      cosas = cosas.filter((cosa) => cosa.id !== id);
      if (documento) {
        const { tipo, ...cosa } = documento;
        cosas.push(cosa);
      }
    }
  }
  return { ...estado, cosas, grupos, ajustes };
}

/** Orden de subida: primero los grupos nuevos, luego las cosas y al final los grupos borrados. Si hace falta más
 *  de un lote, ninguna cosa llega a la nube apuntando a un grupo que aún no está (o que ya se ha ido sin ella). */
function ordenDeSubida([clave, documento]) {
  if (!clave.startsWith('g-')) return 1;
  return documento ? 0 : 2;
}

// ---------- Base (lo último en que coincidían los dos lados) ----------

function leerBase(uid) {
  try {
    const guardada = JSON.parse(localStorage.getItem(CLAVE_BASE));
    if (!guardada || guardada.uid !== uid || !guardada.elementos || typeof guardada.elementos !== 'object') return new Map();
    const base = new Map();
    for (const [clave, datos] of Object.entries(guardada.elementos)) poner(base, clave, deLaNube(clave, datos));
    return base;
  } catch (error) {
    return new Map();
  }
}

function guardarBase(uid, base) {
  try {
    localStorage.setItem(CLAVE_BASE, JSON.stringify({ uid, elementos: Object.fromEntries(base) }));
  } catch (error) { /* Sin almacenamiento: la próxima vez se reconcilia desde cero (sin perder nada). */ }
}

// ---------- Firebase (una sola vez para todo lo de la nube) ----------

let firebase = null; // Promesa de { sdkAuth, sdkFs, autenticacion, db } (o de null, sin configuración).

/**
 * Carga Firebase la primera vez que se pide y lo comparte después. «intento» cambia las URL al reintentar:
 * un import() fallido se queda fallido para esa URL.
 */
function cargarFirebase(intento) {
  if (!firebase) {
    firebase = (async () => {
      const sufijo = intento ? `?reintento=${intento}` : '';
      await import(`./firebase-config.js${sufijo}`); // La de cosas.info (netlify.toml la sirve aquí): define window.COSAS_FIREBASE.
      const config = window.COSAS_FIREBASE;
      if (!config || !config.apiKey || !config.projectId) return null;
      const [sdkApp, sdkAuth, sdkFs] = await Promise.all([
        import(`${CDN}firebase-app.js${sufijo}`),
        import(`${CDN}firebase-auth.js${sufijo}`),
        import(`${CDN}firebase-firestore.js${sufijo}`),
      ]);
      // Mismo nombre de app y misma clave que las páginas de cosas.info: así se encuentra la sesión que
      // dejaron guardada en este dominio. Sin ventanas ni redirecciones: aquí no se entra, solo se lee.
      const aplicacion = sdkApp.initializeApp(config);
      const autenticacion = sdkAuth.initializeAuth(aplicacion, {
        persistence: [sdkAuth.indexedDBLocalPersistence, sdkAuth.browserLocalPersistence],
      });
      // Caché en memoria: lo local ya vive en localStorage, y la caché persistente es de Cosas con y Cosas de.
      const db = sdkFs.initializeFirestore(aplicacion, { localCache: sdkFs.memoryLocalCache() });
      return { sdkAuth, sdkFs, autenticacion, db };
    })();
    firebase.catch(() => {
      firebase = null; // Sin conexión, por ejemplo: se podrá volver a intentar.
    });
  }
  return firebase;
}

// ---------- Conexión ----------

/**
 * Conecta la app con la nube. «app» da:
 *   leer()           → el estado local actual (no se modifica);
 *   aplicar(estado)  → sustituye el estado local por ese (lo guarda y repinta);
 *   estado(texto)    → '' (sin sesión), 'conectando', 'guardando', 'al-dia' o 'error'.
 * Devuelve { cambio() } para avisar de cada cambio local, o null si no hay configuración de Firebase.
 * «intento» cambia las URL al reintentar: un import() fallido se queda fallido para esa URL.
 */
export async function conectar(app, intento = 0) {
  const firebaseCargado = await cargarFirebase(intento);
  if (!firebaseCargado) return null;
  const { sdkAuth, sdkFs, autenticacion, db } = firebaseCargado;

  let uid = null;
  let coleccion = null;
  let dejarDeEscuchar = null;
  let nube = null; // Lo último que ha dicho el servidor (null hasta la primera respuesta suya).
  let base = new Map();
  const ocupados = new Set(); // Subidas en curso.
  // Subidas aceptadas que aún no se han visto en una instantánea: hasta la siguiente, la última que se
  // tiene puede ser de antes de subirlas y parecería que otro dispositivo las ha deshecho.
  const confirmados = new Set();
  const fallidos = new Set(); // Subidas rechazadas: no se reintentan hasta la próxima vez.
  let temporizador = 0;
  let esperaConfirmacion = 0;

  const avisarEstado = () => app.estado(!uid ? '' : !nube ? 'conectando' : ocupados.size || confirmados.size ? 'guardando' : fallidos.size ? 'error' : 'al-dia');

  function programar(retardo = RETARDO) {
    clearTimeout(temporizador);
    temporizador = setTimeout(sincronizar, retardo);
  }

  function subir(cambios) {
    const lista = [...cambios].sort((a, b) => ordenDeSubida(a) - ordenDeSubida(b));
    const deQuien = uid;
    for (let desde = 0; desde < lista.length; desde += MAX_LOTE) {
      const trozo = lista.slice(desde, desde + MAX_LOTE);
      const lote = sdkFs.writeBatch(db);
      for (const [clave, documento] of trozo) {
        const referencia = sdkFs.doc(coleccion, clave);
        if (documento) lote.set(referencia, documento);
        else lote.delete(referencia);
        ocupados.add(clave);
      }
      lote.commit().then(() => {
        if (uid !== deQuien) return; // Se cerró la sesión mientras tanto.
        for (const [clave, documento] of trozo) {
          ocupados.delete(clave);
          confirmados.add(clave);
          poner(base, clave, documento);
        }
        guardarBase(uid, base);
        // Firestore avisa en cuanto se confirma (includeMetadataChanges); si no llegara, se sigue igual.
        clearTimeout(esperaConfirmacion);
        esperaConfirmacion = setTimeout(() => {
          confirmados.clear();
          programar(0);
        }, 2000);
        avisarEstado();
      }, () => {
        if (uid !== deQuien) return;
        for (const [clave] of trozo) {
          ocupados.delete(clave);
          fallidos.add(clave);
        }
        avisarEstado();
      });
    }
  }

  function sincronizar() {
    if (!uid || !nube) return;
    const estado = app.leer();
    const resultado = reconciliar(elementosDe(estado), base, nube, new Set([...ocupados, ...confirmados, ...fallidos]));
    base = resultado.base;
    guardarBase(uid, base);
    if (resultado.aplicar.size) app.aplicar(estadoCon(estado, resultado.aplicar));
    if (resultado.escribir.size) subir(resultado.escribir);
    avisarEstado();
  }

  function parar() {
    if (dejarDeEscuchar) dejarDeEscuchar();
    dejarDeEscuchar = null;
    uid = null;
    coleccion = null;
    nube = null;
    ocupados.clear();
    confirmados.clear();
    fallidos.clear();
    clearTimeout(temporizador);
    clearTimeout(esperaConfirmacion);
    avisarEstado();
  }

  function empezar(nuevo) {
    uid = nuevo;
    base = leerBase(uid);
    coleccion = sdkFs.collection(db, 'personales', uid, 'elementos');
    avisarEstado();
    dejarDeEscuchar = sdkFs.onSnapshot(coleccion, { includeMetadataChanges: true }, (instantanea) => {
      // Hasta que responde el servidor no se sabe qué hay en la nube: una caché vacía no es «lo han borrado todo».
      if (instantanea.metadata.fromCache && !nube) return;
      nube = new Map();
      for (const documento of instantanea.docs) poner(nube, documento.id, deLaNube(documento.id, documento.data()));
      confirmados.clear(); // Esta ya incluye lo confirmado hasta ahora.
      clearTimeout(esperaConfirmacion);
      programar(0);
    }, () => {
      nube = null;
      app.estado('error');
    });
  }

  sdkAuth.onAuthStateChanged(autenticacion, (usuario) => {
    // Solo con Google: una sesión anónima es de este dispositivo y no llevaría las cosas a ningún otro.
    const nuevo = usuario && !usuario.isAnonymous ? usuario.uid : null;
    if (nuevo === uid) return;
    parar();
    if (nuevo) empezar(nuevo);
  });

  return { cambio: () => programar() };
}

// ---------- Novedades de Cosas con y Cosas de ----------

const milis = (v) => (v && typeof v.toMillis === 'function' ? v.toMillis() : typeof v === 'number' ? v : 0);

/**
 * ¿Hay cosas nuevas de otra persona sin ver en tus listas de Cosas con y en las de Cosas de? { con, de }. Las
 * mismas cuentas que esas páginas (js/con.js en cosas.info): la última cosa nueva de cada lista («ultima»)
 * frente a lo último que viste en ella (usuarios/{uid}.vistos), cuándo entraste y cuándo se empezó a
 * contar («_desde»; sin él, nada es nuevo todavía).
 */
export function novedadesDe(listas, vistos, uid) {
  const hay = { con: false, de: false };
  if (!uid || !vistos || !vistos._desde) return hay;
  for (const lista of listas) {
    const ultima = lista && lista.ultima;
    if (!ultima || !ultima.por || ultima.por === uid) continue;
    const yo = lista.miembros && lista.miembros[uid];
    const desde = Math.max(milis(vistos[lista.id]), milis(vistos._desde), milis(yo && yo.desde));
    if (milis(ultima.en) > desde) hay[lista.tipo === 'de' ? 'de' : 'con'] = true;
  }
  return hay;
}

/**
 * Vigila tus listas de Cosas con y Cosas de con la sesión que esas páginas dejaron en este dominio (anónima
 * o de Google) y llama a avisar({ con, de }) cada vez que cambia lo que hay. Devuelve null sin configuración.
 */
export async function vigilarNovedades(avisar, intento = 0) {
  const firebaseCargado = await cargarFirebase(intento);
  if (!firebaseCargado) return null;
  const { sdkAuth, sdkFs, autenticacion, db } = firebaseCargado;
  let uid = null;
  let listas = [];
  let vistos = null;
  let dejarDeMirar = [];
  const calcular = () => avisar(novedadesDe(listas, vistos, uid));

  sdkAuth.onAuthStateChanged(autenticacion, (usuario) => {
    const nuevo = usuario ? usuario.uid : null;
    if (nuevo === uid) return;
    for (const parar of dejarDeMirar) parar();
    dejarDeMirar = [];
    uid = nuevo;
    listas = [];
    vistos = null;
    calcular();
    if (!uid) return;
    const mias = sdkFs.query(sdkFs.collection(db, 'listas'), sdkFs.where('uids', 'array-contains', uid));
    dejarDeMirar.push(sdkFs.onSnapshot(mias, (instantanea) => {
      listas = instantanea.docs.map((documento) => ({ id: documento.id, ...documento.data({ serverTimestamps: 'estimate' }) }));
      calcular();
    }, () => {}));
    const perfil = sdkFs.doc(db, 'usuarios', uid);
    dejarDeMirar.push(sdkFs.onSnapshot(perfil, (instantanea) => {
      vistos = (instantanea.exists() && instantanea.data({ serverTimestamps: 'estimate' }).vistos) || {};
      // La primera vez se empieza a contar ahora: lo de antes cuenta como visto.
      if (!vistos._desde && !instantanea.metadata.fromCache) {
        sdkFs.setDoc(perfil, { vistos: { _desde: sdkFs.serverTimestamp() } }, { merge: true }).catch(() => {});
      }
      calcular();
    }, () => {}));
  });
  return { vigilando: true };
}

// ---------- Avisos (notificaciones) de Cosas con y Cosas de ----------

/**
 * La sesión de Cosas con y Cosas de en este dominio, para darse de alta en sus avisos: { uid, token() }. Si aún
 * no hay ninguna (no se han abierto aquí nunca), se entra de forma anónima, como harían ellas al abrirse: las
 * listas que se creen o a las que se entre después serán de esta misma sesión. null sin configuración.
 */
export async function sesionDeAvisos(intento = 0) {
  const firebaseCargado = await cargarFirebase(intento);
  if (!firebaseCargado) return null;
  const { sdkAuth, autenticacion } = firebaseCargado;
  await autenticacion.authStateReady();
  if (!autenticacion.currentUser) await sdkAuth.signInAnonymously(autenticacion);
  const usuario = autenticacion.currentUser;
  return { uid: usuario.uid, token: () => usuario.getIdToken() };
}
