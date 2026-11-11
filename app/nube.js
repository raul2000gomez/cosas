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
   (sesionDeAvisos): las notificaciones. Con esa misma sesión, los recordatorios del grupo Recordar se
   llevan a la función de avisos, que es quien avisa a su hora (sincronizarRecordatorios).

   Sin efectos al importarlo: Firebase se carga la primera vez que se pide (conectar, vigilarNovedades,
   sesionDeAvisos o sincronizarRecordatorios). */

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

// ---------- Recordatorios (el grupo fijo Recordar) ----------

/*
 * Lo que se apunta en Recordar puede llevar un recordatorio, y quien avisa a su hora es la función de avisos
 * (netlify/functions): guarda, de cada sesión, { nacido, v, zona, general, cosas: { id: { texto, hecha, r } } }
 * (/api/avisos/recordar). No van en Firestore (sus reglas solo admiten cosas, grupos y ajustes), así que este es
 * también el camino por el que los recordatorios llegan a otro dispositivo con la misma cuenta.
 *
 * Como con las cosas, se recuerda lo último en que este dispositivo y la función coincidían (la «base») y se junta
 * cosa a cosa: lo que solo cambió allí se trae (un «Hecho» pulsado en un aviso, un recordatorio puesto en otro
 * dispositivo); lo que cambió aquí se sube; si cambió en los dos, gana este dispositivo, pero campo a campo: cambiar
 * aquí la hora de una cosa no deshace el «Hecho» que se le dio en su aviso. A la función solo se le manda lo que
 * cambia (qué poner y qué quitar: lo que no se nombra, ni se toca), y solo lo acepta sobre su última versión
 * («siV»): si entretanto cambió (409), se vuelve a juntar con lo que tenga.
 *
 * «nacido» dice de qué documento es la base: si la función ya no tiene aquel (lo borró «olvidar», o caducó) y tiene
 * otro, o ninguno, la base no vale. Entonces se junta como la primera vez: lo de los dos sitios, y sin dar por
 * quitado aquí nada de lo que allí falte. De lo que está en los dos gana lo de aquí, menos lo hecho (lo es si lo
 * dice cualquiera) y el recordatorio que aquí sea solo el de todas (no pisa el que la función tiene de esa cosa).
 *
 * Un envío puede llegar a la función sin que su respuesta vuelva (se va la cobertura justo entonces). Por eso, antes
 * de enviar, queda apuntado en la base qué se envía («enDuda»: si va el de todas, y de qué cosas). Mientras siga
 * apuntado no se sabe qué tiene la función: la ronda siguiente empieza preguntándoselo, y de lo dudoso manda lo de
 * aquí, porque lo que ella tenga puede ser ese mismo envío y no algo que cambió otro dispositivo.
 */
const ESPERA_RECORDATORIOS = 15000;
const VUELTAS_RECORDATORIOS = 4;
// La función no admite envíos de más de 100000 bytes (netlify/functions/avisos.mjs): con este margen, lo que no quepa en uno se reparte.
const MAX_ENVIO = 90000;

const firmaDe = (valor) => JSON.stringify(valor === undefined ? null : valor);

/** Las cosas con recordatorio de un objeto { id: { texto, hecha, r } }, como Map y con su forma exacta (lo que no vale, fuera). */
function recordadasDe(brutas, recordatorio) {
  const cosas = new Map();
  for (const [id, bruta] of Object.entries(brutas && typeof brutas === 'object' ? brutas : {})) {
    if (!bruta || typeof bruta !== 'object' || !esTexto(bruta.texto) || typeof bruta.hecha !== 'boolean') continue;
    const r = recordatorio(bruta.r);
    if (r) cosas.set(id, { texto: bruta.texto, hecha: bruta.hecha, r });
  }
  return cosas;
}

/**
 * Junta lo de aquí («local»), la base y lo de la función («servidor»): cada uno, { general, cosas: Map }; sin base
 * que valga (la primera vez, o si la función ya tiene otro documento), null. «conocidas» son las cosas que están en
 * Recordar en este dispositivo (Map: id → si aquí está hecha); «enDuda», lo que iba en un envío que se quedó sin respuesta ({ general, ids: Set }).
 * Devuelve:
 *   parche   → { general (solo si cambia; null lo quita), poner: { id: cosa }, quitar: [id] } con lo que hay que
 *              decirle a la función, o null si ya lo tiene todo;
 *   aplicar  → { general (undefined: sin cambio), cosas: [[id, cosa | null]] } con lo que hay que traer aquí;
 *   base     → { general, cosas: Map } con la base nueva: lo que la función tendrá de las cosas de este dispositivo.
 * Lo que la función tiene de cosas que aquí aún no han llegado (las puso otro dispositivo) ni se toca ni se apunta:
 * se adoptará cuando lleguen.
 */
export function reconciliarRecordatorios(local, base, servidor, conocidas, enDuda = null) {
  const poner = {};
  const quitar = [];
  const cosas = new Map();
  const aplicar = [];
  const ids = new Set([...local.cosas.keys(), ...(base ? base.cosas.keys() : []), ...servidor.cosas.keys()]);
  for (const id of ids) {
    const l = local.cosas.get(id);
    const b = base ? base.cosas.get(id) : undefined;
    const s = servidor.cosas.get(id);
    const dudosa = Boolean(enDuda) && enDuda.ids.has(id);
    if (dudosa && !(l && s)) {
      // Que la función la tenga, o que no, puede ser cosa de aquel envío: se queda como está aquí.
      if (l) {
        poner[id] = l;
        cosas.set(id, l);
      } else if (s) {
        quitar.push(id);
      }
    } else if (!l && !b) {
      // Solo la tiene la función: su recordatorio se puso en otro dispositivo. Sin base, hecha en cualquiera de los dos
      // es hecha (como abajo): la que aquí ya se marcó (su check, o «Hecho» en la hoja de un aviso mientras se
      // preguntaba a la función) no vuelve a pendiente por lo que ella diga, y se le cuenta.
      if (!conocidas.has(id)) continue;
      const traida = conocidas.get(id) === true && !s.hecha ? { ...s, hecha: true } : s;
      aplicar.push([id, traida]);
      cosas.set(id, traida);
      if (traida !== s) poner[id] = traida;
    } else if (!l) {
      // Aquí se le quitó el recordatorio (o la cosa ya no está en Recordar): se quita también allí, texto incluido.
      if (s) quitar.push(id);
    } else if (!s) {
      // La función no la tiene: o se lo quitaron en otro dispositivo (y aquí no ha cambiado), o es de aquí y se sube.
      if (b && firmaDe(l) === firmaDe(b)) {
        aplicar.push([id, null]);
      } else {
        poner[id] = l;
        cosas.set(id, l);
      }
    } else {
      // En los dos sitios: de cada campo, lo que aquí no ha cambiado desde la base toma lo de la función. Sin base
      // (la primera vez que este dispositivo ve esa cosa allí), hecha en cualquiera de los dos es hecha: un «Hecho»
      // pulsado en un aviso no se deshace por llegar de nuevas. Y gana el recordatorio de aquí, salvo que sea solo
      // el de todas: ese nadie lo eligió para ella, y el que la función tiene, sí. Si es dudosa, el de aquí siempre:
      // el de allí puede ser el de aquel envío.
      const mezcla = {
        texto: l.texto,
        hecha: b ? (l.hecha === b.hecha ? s.hecha : l.hecha) : l.hecha || s.hecha,
        r: !dudosa && (b ? firmaDe(l.r) === firmaDe(b.r) : firmaDe(l.r) === firmaDe(local.general)) ? s.r : l.r,
      };
      cosas.set(id, mezcla);
      if (firmaDe(mezcla) !== firmaDe(s)) poner[id] = mezcla;
      if (firmaDe(mezcla) !== firmaDe(l)) aplicar.push([id, mezcla]);
    }
  }
  // El de todas: con base, manda quien lo cambió desde ella (los dos: este dispositivo); sin ella, el de aquí y, si aquí no hay, el de
  // allí. Si es dudoso (iba en el envío sin respuesta), el de aquí, lo haya o no.
  let general = local.general || servidor.general;
  if (base) general = firmaDe(local.general) === firmaDe(base.general) ? servidor.general : local.general;
  if (enDuda && enDuda.general) general = local.general;
  const cambiaGeneral = firmaDe(general) !== firmaDe(servidor.general);
  const hayParche = cambiaGeneral || quitar.length > 0 || Object.keys(poner).length > 0;
  return {
    parche: hayParche ? { ...(cambiaGeneral ? { general } : {}), poner, quitar } : null,
    aplicar: { general: firmaDe(general) === firmaDe(local.general) ? undefined : general, cosas: aplicar },
    base: { general, cosas },
  };
}

const bytesDe = (valor) => new TextEncoder().encode(JSON.stringify(valor)).length;

/**
 * Lo que hay que decirle a la función (el «parche» de reconciliarRecordatorios), en los envíos que hagan falta para
 * que ninguno pase de lo que ella admite: uno casi siempre, pero cien cosas de 500 letras con tilde ya no caben en
 * uno, y rechazado entero no avisaría de ninguna. Lo que se quita y el de todas van en el primero; lo que hay que
 * poner, repartido en orden. Sin parche (solo hay que decirle la zona), un envío sin nada más.
 */
function enviosDe(parche) {
  if (!parche) return [{}];
  const envios = [{ ...parche, poner: {} }];
  let bytes = bytesDe(envios[0]);
  for (const [id, cosa] of Object.entries(parche.poner)) {
    const suyos = bytesDe(id) + bytesDe(cosa) + 2; // Con sus dos puntos y su coma.
    if (bytes + suyos > MAX_ENVIO) {
      envios.push({ poner: {}, quitar: [] });
      bytes = 0;
    }
    envios[envios.length - 1].poner[id] = cosa;
    bytes += suyos;
  }
  return envios;
}

/** La base guardada, si es de esta sesión y se entiende; si no, null (la primera vez, o tras entrar con otra cuenta). */
function baseDeRecordatorios(guardada, uid, recordatorio) {
  const esSuya = guardada && typeof guardada === 'object' && guardada.uid === uid && Number.isInteger(guardada.v) && guardada.v >= 0;
  if (!esSuya) return null;
  return {
    nacido: esTexto(guardada.nacido) ? guardada.nacido : null,
    v: guardada.v,
    zona: esTexto(guardada.zona) ? guardada.zona : null,
    zonaDeLaFuncion: esTexto(guardada.zonaDeLaFuncion) ? guardada.zonaDeLaFuncion : null,
    general: recordatorio(guardada.general),
    cosas: recordadasDe(guardada.cosas, recordatorio),
  };
}

/** Lo que responde la función ({ nacido, v, zona, general, cosas }), o null si no es eso. */
function recordatoriosDelServidor(datos, recordatorio) {
  if (!datos || typeof datos !== 'object' || !Number.isInteger(datos.v) || datos.v < 0 || !datos.cosas || typeof datos.cosas !== 'object') return null;
  return {
    nacido: esTexto(datos.nacido) && datos.nacido ? datos.nacido : null,
    v: datos.v,
    zona: esTexto(datos.zona) && datos.zona ? datos.zona : null,
    general: recordatorio(datos.general),
    cosas: recordadasDe(datos.cosas, recordatorio),
  };
}

/** ¿Sigue valiendo la base ante lo que tiene la función? No, si aquel documento ya no es este o ha vuelto atrás. */
const baseAlDia = (base, servidor) => Boolean(base) && base.nacido === servidor.nacido && servidor.v >= base.v;

/**
 * La zona horaria que tiene que quedar en la función: las horas de los recordatorios son las del reloj de quien los
 * puso. La pone el primer dispositivo que guarda algo y solo la cambia un dispositivo cuya propia zona ha cambiado
 * desde la última vez (un viaje con el móvil): otro dispositivo, en otra zona o con un navegador que no dice la suya,
 * no mueve la hora de todos los avisos por guardar un recordatorio.
 */
function zonaParaLaFuncion(base, servidor, zona) {
  if (servidor.nacido === null) return zona; // Aún no hay documento (su «UTC» es de relleno): la pone este dispositivo.
  if (base && base.zona !== null && base.zona !== zona) return zona;
  return servidor.zona || zona;
}

/** Una llamada con la sesión a la función de avisos: { estado, datos }. Lanza si no hay red o tarda demasiado. */
async function pedirAvisos(api, accion, sesion, metodo, cuerpo) {
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), ESPERA_RECORDATORIOS);
  try {
    const respuesta = await fetch(`${api}${accion}`, {
      method: metodo,
      headers: { Authorization: `Bearer ${await sesion.token()}`, ...(cuerpo ? { 'Content-Type': 'application/json' } : {}) },
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
      signal: control.signal,
    });
    return { estado: respuesta.status, datos: await respuesta.json().catch(() => null) };
  } finally {
    clearTimeout(temporizador);
  }
}

/**
 * Una ronda con la función de avisos. «app» da:
 *   api                 → la dirección de la función (…/api/avisos/);
 *   leer                → si hay que preguntarle antes qué tiene (si no, se parte de la base y solo se le escribe);
 *   zona                → la zona horaria de este dispositivo (ver zonaParaLaFuncion);
 *   recordatorio(bruto) → un recordatorio con su forma exacta, o null;
 *   local()             → lo de aquí ahora: { general, cosas: { id: { texto, hecha, r } } };
 *   firma()             → una firma de lo de aquí (se guarda con la base: mientras no cambie, no hay nada que contar);
 *   conocidas()         → Map con las cosas que están en Recordar en este dispositivo (id → si está hecha);
 *   base() y guardarBase(base) → lo último en que coincidían este dispositivo y la función (y, con un envío por el
 *                         camino, qué lleva: «enDuda»);
 *   aplicar({ general, cosas }) → lo que llega de la función (ver reconciliarRecordatorios).
 * Devuelve { uid } (la sesión con la que se ha hablado) si la ronda acaba bien, o null si no (se repetirá otra vez).
 */
export async function sincronizarRecordatorios(app, intento = 0) {
  const sesion = await sesionDeAvisos(intento);
  if (!sesion) return null;
  const preguntar = async () => {
    const leido = await pedirAvisos(app.api, 'recordar', sesion, 'GET');
    return leido.estado === 200 ? recordatoriosDelServidor(leido.datos, app.recordatorio) : null;
  };
  const guardada = app.base();
  const suya = Boolean(guardada) && typeof guardada === 'object' && guardada.uid === sesion.uid;
  let base = baseDeRecordatorios(guardada, sesion.uid, app.recordatorio);
  // Lo que iba en un envío de esta sesión que se quedó sin respuesta.
  let enDuda = suya && guardada.enDuda && Array.isArray(guardada.enDuda.ids) ? { general: guardada.enDuda.general === true, ids: new Set(guardada.enDuda.ids) } : null;
  const dudasAlEmpezar = enDuda;
  // Sin preguntar, la función tiene lo que dice la base; sin base, o con algo en duda, no queda otra que preguntarle.
  const sinPreguntar = base && !app.leer && !enDuda;
  let servidor = sinPreguntar ? { nacido: base.nacido, v: base.v, zona: base.zonaDeLaFuncion, general: base.general, cosas: base.cosas } : await preguntar();
  // Un check tocado aquí mientras se preguntaba queda dicho en la base (ponerHecha, en app.js): se lee otra vez.
  if (!sinPreguntar) base = baseDeRecordatorios(app.base(), sesion.uid, app.recordatorio);
  for (let vuelta = 0; vuelta < VUELTAS_RECORDATORIOS && servidor; vuelta += 1) {
    if (!baseAlDia(base, servidor)) base = null;
    const { general, cosas } = app.local();
    const resultado = reconciliarRecordatorios({ general, cosas: new Map(Object.entries(cosas)) }, base, servidor, app.conocidas(), enDuda);
    if (resultado.aplicar.cosas.length > 0 || resultado.aplicar.general !== undefined) app.aplicar(resultado.aplicar);
    // La firma de lo de aquí tal como se va a enviar: lo que cambie mientras se espera la respuesta es para la ronda siguiente.
    const firma = app.firma();
    const zona = zonaParaLaFuncion(base, servidor, app.zona);
    // Aunque no cambie nada más, si este dispositivo ha cambiado de zona horaria la función tiene que saberlo.
    const cambiaZona = servidor.nacido !== null && zona !== servidor.zona;
    let { nacido, v } = servidor;
    let otraVuelta = false;
    // Antes de enviar queda apuntado qué se envía, y sin firma: hasta que haya respuesta, hay algo que contar. (Sin nada
    // que enviar, ya no hay duda: la función acaba de decir lo que tiene.)
    enDuda = resultado.parche ? { general: 'general' in resultado.parche, ids: new Set([...Object.keys(resultado.parche.poner), ...resultado.parche.quitar]) } : null;
    if (enDuda) app.guardarBase({ ...(suya ? app.base() : null), uid: sesion.uid, firma: '', enDuda: { general: enDuda.general, ids: [...enDuda.ids] } });
    // En un envío o, si en uno no cabe, en varios seguidos: cada uno sobre la versión que deja el anterior.
    for (const envio of resultado.parche || cambiaZona ? enviosDe(resultado.parche) : []) {
      const respuesta = await pedirAvisos(app.api, 'recordar', sesion, 'POST', { siV: v, zona, ...envio });
      if (respuesta.estado === 409) {
        // Entretanto ha cambiado allí (otro dispositivo, o un «Hecho» en un aviso): se junta otra vez con lo que tiene.
        servidor = recordatoriosDelServidor(respuesta.datos, app.recordatorio);
        // Ese envío no se aplicó: lo que llevaba no queda en duda, y se junta campo a campo con lo que haya allí.
        enDuda = dudasAlEmpezar;
        otraVuelta = true;
        break;
      }
      if (respuesta.estado !== 200 || !respuesta.datos || !Number.isInteger(respuesta.datos.v)) return null;
      const escrito = esTexto(respuesta.datos.nacido) && respuesta.datos.nacido ? respuesta.datos.nacido : null;
      if (nacido !== null && escrito !== nacido) {
        // Se ha escrito sobre un documento que no era el que se creía (aquel se borró y hay otro): se mira qué tiene y se junta de nuevas.
        base = null;
        servidor = await preguntar();
        otraVuelta = true;
        break;
      }
      nacido = escrito;
      v = respuesta.datos.v;
      // Lo que la función no admite («rechazadas»: no le caben más) queda en la base como enviado: no se le repite sin fin.
    }
    if (otraVuelta) continue;
    app.guardarBase({
      uid: sesion.uid,
      nacido,
      v,
      zona: app.zona,
      zonaDeLaFuncion: resultado.parche || cambiaZona ? zona : servidor.zona,
      firma,
      general: resultado.base.general,
      cosas: Object.fromEntries(resultado.base.cosas),
    });
    return { uid: sesion.uid };
  }
  return null;
}

/**
 * Borra de la función de avisos los recordatorios de esta sesión (y nada más). Lo pide la app al desactivar las
 * notificaciones sin cuenta de Google: ya no hay a quién avisar, y el texto de las cosas no tiene por qué seguir allí.
 * Devuelve si la función lo ha hecho.
 */
export async function olvidarRecordatorios(api, intento = 0) {
  const sesion = await sesionDeAvisos(intento);
  if (!sesion) return false;
  const respuesta = await pedirAvisos(api, 'olvidar', sesion, 'POST', {});
  return respuesta.estado === 200;
}
