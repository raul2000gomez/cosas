/* Cosas · lógica de la aplicación (sin dependencias). */
(() => {
  'use strict';

  // ---------- Constantes ----------

  const CLAVE = 'cosas:v1';
  const CLAVE_RESPALDO = 'cosas:grupos'; // Solo la 1.2 la conoce: una 1.1 aún abierta no la toca.
  const VERSION = 2;
  const COLOR_DEFECTO = '#2F6FED';
  const TINTA_OSCURA = '#0A0A0A';
  const TINTA_CLARA = '#FFFFFF';
  const MAX_TEXTO = 500;
  const MAX_NOMBRE = 60;
  // La cuenta de Google con la que se ha entrado ({ nombre, correo }). La escriben las páginas de
  // Cosas con, Cosas de y Tu cuenta (mismo dominio); la app solo la lee para enseñarla.
  const CLAVE_CUENTA = 'cosascon:cuenta';
  // Las listas de Cosas con y Cosas de abiertas en este dispositivo (las apuntan esas páginas).
  const CLAVE_LISTAS = 'cosascon:ids';
  // Las notificaciones de este dispositivo ({ uid, endpoint, en }); sin ellas, no existe. Ver «Notificaciones».
  const CLAVE_AVISOS = 'cosas:avisos';
  // Los recordatorios de las cosas del grupo Recordar: { general, cosas: { idCosa: recordatorio }, base, activar }. Van
  // en su propia clave: una versión anterior de la app aún abierta (que no los conoce) no los pierde al guardar lo suyo.
  const CLAVE_RECORDAR = 'cosas:recordar';
  // El grupo fijo. Para lo guardado y para la nube es un grupo más, con este id (una versión anterior lo enseña como
  // tal y no pierde lo que tiene dentro); aquí va aparte, arriba de la lista, y ni se borra ni se edita. Solo se
  // guarda desde que tiene algo: mientras tanto, la app lo da por puesto (grupoPorId).
  const ID_RECORDAR = 'recordar';
  const GRUPO_RECORDAR = Object.freeze({ id: ID_RECORDAR, nombre: 'Recordar', color: null, creada: 1, abierto: false });
  const FRECUENCIAS = ['dia', 'laborables', 'semana', 'mes'];
  const HORA_POR_DEFECTO = '09:00';
  // La función de avisos mira cada cinco minutos a quién le toca: las horas se eligen de cinco en cinco.
  const PASO_MINUTOS = 5;
  const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
  const DIAS_CORTOS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb']; // En la fila de una cosa, cuando son tres o más.
  const ULTIMO_DIA_DE_TODOS = 28; // Del 29 en adelante no todos los meses llegan: avisa el último día.
  // Lo que admite la función de avisos de una sesión (netlify/lib/recordar.mjs).
  const MAX_RECORDADAS = 100;
  const MAX_ID_RECORDADA = 100;
  // La caché donde el service worker deja lo que pasa con los avisos (sw.js): la app lo lee al abrirse.
  const DATOS_DE_AVISOS = 'recordar-de-cosas';
  const AVISADA_RECIENTE = 24 * 60 * 60 * 1000; // Pasado un día, ya no se pregunta por un aviso sin contestar.
  const RETARDO_RECORDATORIOS = 700; // Los cambios seguidos viajan juntos a la función de avisos.
  const ENTRE_LECTURAS = 60 * 1000; // Al volver a la app, no se le pregunta a la función más que una vez por minuto.
  const DURACION_AVISO = 1800;
  const DURACION_DESHACER = 4000;
  const DURACION_SALIDA = 220;
  const DURACION_SALIDA_CORTA = 160;
  const DURACION_DESLIZAMIENTO = 220;
  const MAX_FILAS_PLEGADO = 150;
  const RETARDO_AUTOGUARDADO = 600;
  // Tras borrar o cambiar de vista, lo que queda bajo el dedo es otro botón: durante
  // este tiempo (un doble toque humano) el segundo toque no debe accionarlo.
  const GUARDA_DOBLE_TOQUE = 350;
  const UMBRAL_TECLADO = 80;
  // Deslizar de lado: recorrido horizontal que abre una esquina o vuelve (o la mitad, si es un golpe
  // rápido), y franja de los bordes que es del sistema («atrás» en iOS y Android).
  const UMBRAL_DESLIZAR = 64;
  const GOLPE_RAPIDO = 200;
  const BORDE_DEL_SISTEMA = 24;
  // Por debajo de esta luminancia (negro y casi negros) «más oscuro» ya no se distingue.
  const LUZ_PROFUNDA = 0.03;
  // Tras stop(), el navegador entrega lo reconocido y avisa con «end»; si no lo hace, se corta.
  const ESPERA_FIN_DICTADO = 2500;
  // Cuando el dictado acaba por su cuenta, «Guardar» aparece donde estaba el micrófono: el toque que
  // iba a detenerlo no debe guardar lo dictado sin haberlo leído (reaccionar lleva más que un doble toque).
  const GUARDA_FIN_DICTADO = 500;
  const AVISOS_DICTADO = new Map([
    ['not-allowed', 'Permite el micrófono para dictar'],
    ['no-speech', 'No te he oído'],
    ['network', 'El dictado necesita conexión'],
    ['audio-capture', 'No encuentro el micrófono'],
  ]);
  const VISTAS = ['inicio', 'lista', 'ajustes', 'grupo'];
  const PALETA = [
    { nombre: 'Azul', color: '#2F6FED' },
    { nombre: 'Azul noche', color: '#1E2A44' },
    { nombre: 'Turquesa', color: '#1BA39C' },
    { nombre: 'Verde', color: '#2E8B57' },
    { nombre: 'Amarillo', color: '#F2B705' },
    { nombre: 'Naranja', color: '#F2711C' },
    { nombre: 'Coral', color: '#EF5B5B' },
    { nombre: 'Rosa', color: '#E85D9E' },
    { nombre: 'Morado', color: '#7A4FD6' },
    { nombre: 'Arena', color: '#EADFCB' },
    { nombre: 'Blanco', color: '#FFFFFF' },
    { nombre: 'Negro', color: '#111111' },
  ];
  const SIN_COLOR = { nombre: 'Sin color', color: '' };

  // ---------- Elementos ----------

  const $ = (selector) => document.querySelector(selector);
  const raiz = document.documentElement;
  const principal = $('main');
  const metaTema = $('meta[name="theme-color"]');
  const vistas = {
    inicio: $('#vista-inicio'),
    lista: $('#vista-lista'),
    ajustes: $('#vista-ajustes'),
    grupo: $('#vista-grupo'),
  };
  const titulos = { lista: $('#titulo-lista'), ajustes: $('#titulo-ajustes'), grupo: $('#titulo-grupo') };
  const abridores = { lista: $('#abrir-lista'), ajustes: $('#abrir-ajustes') };
  const accesoRecordar = $('#acceso-recordar');
  const cuentaRecordar = $('#cuenta-recordar');
  const zonaLista = $('#zona-lista');
  const listaEl = $('#lista-cosas');
  const vacio = $('#vacio');
  const formularioGrupo = $('#formulario-grupo');
  const botonCrearGrupo = $('#crear-grupo');
  const campoGrupo = $('#campo-grupo');
  const botonConfirmarGrupo = $('#confirmar-grupo');
  const botonEditarGrupo = $('#editar-grupo');
  const botonConfigurar = $('#configurar-recordar');
  const notaRecordar = $('#nota-recordar');
  const textoNotaRecordar = $('#texto-nota-recordar');
  const botonNotaRecordar = $('#boton-nota-recordar');
  const zonaGrupo = $('#zona-grupo');
  const cuerpoGrupo = $('#cuerpo-grupo');
  const botonAnadir = $('#anadir-cosas');
  const candidatas = $('#candidatas');
  const listaCandidatas = $('#lista-candidatas');
  const sinCandidatas = $('#sin-candidatas');
  const listaGrupo = $('#lista-grupo');
  const grupoVacio = $('#grupo-vacio');
  const muestras = $('#muestras');
  const personalizado = $('#personalizado');
  const selectorColor = $('#color-personalizado');
  const formularioDatos = $('#datos');
  const campoNombre = $('#nombre');
  const botonSesion = $('#sesion');
  const ayudaSesion = $('#ayuda-sesion');
  const ayudaDictado = $('#ayuda-dictado');
  const botonInstalar = $('#instalar');
  const ayudaInstalar = $('#ayuda-instalar');
  const botonCompartir = $('#compartir');
  const botonAvisos = $('#avisos');
  const ayudaAvisos = $('#ayuda-avisos');
  const hoja = $('#hoja');
  const veloHoja = $('#velo-hoja');
  const tituloHoja = $('#titulo-hoja');
  const botonCerrarHoja = $('#cerrar-hoja');
  const pasosIos = $('#pasos-ios');
  const avisoDatosIos = $('#aviso-datos-ios');
  const pasosGenericos = $('#pasos-genericos');
  const hojaGrupo = $('#hoja-grupo');
  const veloHojaGrupo = $('#velo-hoja-grupo');
  const tituloHojaGrupo = $('#titulo-hoja-grupo');
  const botonCerrarHojaGrupo = $('#cerrar-hoja-grupo');
  const formularioEdicion = $('#formulario-edicion');
  const campoNombreGrupo = $('#nombre-grupo');
  const muestrasGrupo = $('#muestras-grupo');
  const hojaRecordar = $('#hoja-recordar');
  const veloHojaRecordar = $('#velo-hoja-recordar');
  const tituloHojaRecordar = $('#titulo-hoja-recordar');
  const botonCerrarHojaRecordar = $('#cerrar-hoja-recordar');
  const formularioRecordar = $('#formulario-recordar');
  const cosaRecordar = $('#cosa-recordar');
  const alcanceRecordar = $('#alcance-recordar');
  const elegirRecordar = $('#elegir-recordar');
  const selectorHora = $('#hora-recordar');
  const selectorMinuto = $('#minuto-recordar');
  const diaRecordar = $('#dia-recordar');
  const mesRecordar = $('#mes-recordar');
  const selectorDiaMes = $('#dia-mes-recordar');
  const ayudaDiaMes = $('#ayuda-dia-mes');
  const resumenRecordar = $('#resumen-recordar');
  const botonGuardarRecordar = $('#guardar-recordar');
  const botonQuitarRecordar = $('#quitar-recordar');
  const ayudaRecordar = $('#ayuda-recordar');
  const hojaAvisada = $('#hoja-avisada');
  const veloHojaAvisada = $('#velo-hoja-avisada');
  const tituloHojaAvisada = $('#titulo-hoja-avisada');
  const botonCerrarHojaAvisada = $('#cerrar-hoja-avisada');
  const listaAvisadas = $('#lista-avisadas');
  const toast = $('#toast');
  const plantillaFila = $('#plantilla-fila');
  const plantillaGrupo = $('#plantilla-grupo');
  const plantillaMiembro = $('#plantilla-miembro');
  const plantillaRecordada = $('#plantilla-recordada');
  const plantillaElegible = $('#plantilla-elegible');
  const plantillaAvisada = $('#plantilla-avisada');
  const plantillaCandidata = $('#plantilla-candidata');
  const plantillaMuestra = $('#plantilla-muestra');
  const plantillaToast = $('#plantilla-toast');
  const menosMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)');

  /** Una barra de escribir: campo, micrófono y enviar (los dos últimos comparten hueco). */
  function barraDeEscribir(formulario) {
    const campoBarra = formulario.querySelector('.campo');
    return {
      formulario,
      pildora: formulario.querySelector('.pildora'),
      campo: campoBarra,
      micro: formulario.querySelector('.micro'),
      enviar: formulario.querySelector('.enviar'),
      marcador: campoBarra.placeholder,
    };
  }

  // La de la pantalla principal y la de la página de un grupo (lo que se apunta ahí entra en el grupo).
  const escrituraInicio = barraDeEscribir($('#formulario'));
  const escrituraGrupo = barraDeEscribir($('#formulario-en-grupo'));
  const escrituras = [escrituraInicio, escrituraGrupo];

  // ---------- Color y contraste ----------

  const esColor = (valor) => typeof valor === 'string' && /^#[0-9a-f]{6}$/i.test(valor);

  /** Luminancia relativa WCAG de un color #RRGGBB. */
  function luminancia(hex) {
    const canal = (desde) => {
      const v = parseInt(hex.slice(desde, desde + 2), 16) / 255;
      return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * canal(1) + 0.7152 * canal(3) + 0.0722 * canal(5);
  }

  const LUZ_TINTA_OSCURA = luminancia(TINTA_OSCURA) + 0.05;

  /** «oscuro» si el blanco contrasta más que el casi negro sobre ese fondo; si no, «claro». */
  function tonoDe(hex) {
    const luz = luminancia(hex) + 0.05;
    return 1.05 / luz >= luz / LUZ_TINTA_OSCURA ? 'oscuro' : 'claro';
  }

  /** La tinta que contrasta con ese color. */
  const tintaSobre = (hex) => (tonoDe(hex) === 'claro' ? TINTA_OSCURA : TINTA_CLARA);

  /** Tono de un elemento (la raíz o la vista de un grupo) según el color de su fondo. */
  function pintarTono(elemento, color) {
    elemento.dataset.tono = tonoDe(color);
    if (luminancia(color) < LUZ_PROFUNDA) elemento.setAttribute('data-fondo', 'profundo');
    else elemento.removeAttribute('data-fondo');
  }

  function aplicarColor(color) {
    raiz.style.setProperty('--fondo', color);
    pintarTono(raiz, color);
    actualizarTema();
  }

  /** La barra del sistema y el aviso siguen al color de la app o, en la página de un grupo con color, al del grupo. */
  function actualizarTema() {
    const grupo = vistaActual === 'grupo' ? grupoMostrado() : null;
    const color = (grupo && grupo.color) || estado.ajustes.colorFondo;
    pintarTono(toast, color); // El aviso vive fuera de las vistas: toma el tono de lo que tiene debajo.
    if (metaTema) metaTema.setAttribute('content', color);
  }

  // ---------- Estado y almacenamiento ----------

  function estadoPorDefecto() {
    return {
      version: VERSION,
      cosas: [],
      grupos: [],
      ajustes: { colorFondo: COLOR_DEFECTO, nombre: '' },
    };
  }

  function nuevoId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    const azar = () => Math.random().toString(36).slice(2, 10);
    return `${Date.now().toString(36)}-${azar()}-${azar()}`;
  }

  const textoLimpio = (valor, maximo) =>
    (typeof valor === 'string' ? valor.trim().slice(0, maximo) : '');

  /** Como textoLimpio, con los espacios interiores reducidos a uno (nombres de grupo). */
  const nombreLimpio = (valor, maximo) =>
    textoLimpio(typeof valor === 'string' ? valor.replace(/\s+/g, ' ') : '', maximo);

  const porFecha = (a, b) => b.creada - a.creada; // De más nueva a más antigua.

  /** Un id válido y no repetido, o uno nuevo. */
  function idDe(bruto, idsVistos) {
    const valido = typeof bruto.id === 'string' && bruto.id !== '' && !idsVistos.has(bruto.id);
    const id = valido ? bruto.id : nuevoId();
    idsVistos.add(id);
    return id;
  }

  /** Devuelve una cosa válida o null; nunca lanza. */
  function normalizarCosa(bruta, idsVistos) {
    if (!bruta || typeof bruta !== 'object') return null;
    const texto = textoLimpio(bruta.texto, MAX_TEXTO);
    if (!texto) return null;
    const hecha = bruta.hecha === true;
    const creada = Number.isFinite(bruta.creada) ? bruta.creada : Date.now();
    return {
      id: idDe(bruta, idsVistos),
      texto,
      hecha,
      creada,
      // Las hechas de la versión 1 no saben cuándo se marcaron: se toma la creación (orden estable).
      hechaEn: hecha ? (Number.isFinite(bruta.hechaEn) ? bruta.hechaEn : creada) : null,
      grupo: typeof bruta.grupo === 'string' && bruta.grupo !== '' ? bruta.grupo : null,
    };
  }

  /** Devuelve un grupo válido o null; nunca lanza. */
  function normalizarGrupo(bruto, idsVistos) {
    if (!bruto || typeof bruto !== 'object') return null;
    const nombre = nombreLimpio(bruto.nombre, MAX_NOMBRE);
    if (!nombre) return null;
    return {
      id: idDe(bruto, idsVistos),
      nombre,
      color: esColor(bruto.color) ? bruto.color.toUpperCase() : null,
      creada: Number.isFinite(bruto.creada) ? bruto.creada : Date.now(),
      abierto: bruto.abierto === true,
    };
  }

  /** Convierte cualquier JSON (versión 1 o 2) en un estado válido (lo inválido se descarta). */
  function normalizarEstado(bruto) {
    const estadoNuevo = estadoPorDefecto();
    if (!bruto || typeof bruto !== 'object' || Array.isArray(bruto)) return estadoNuevo;

    if (Array.isArray(bruto.grupos)) {
      const idsVistos = new Set();
      estadoNuevo.grupos = bruto.grupos
        .map((brutoGrupo) => normalizarGrupo(brutoGrupo, idsVistos))
        .filter(Boolean)
        // Recordar es siempre Recordar, aunque una versión anterior lo haya renombrado o coloreado.
        .map((grupo) => (grupo.id === ID_RECORDAR ? { ...GRUPO_RECORDAR } : grupo))
        .sort(porFecha);
    }
    if (Array.isArray(bruto.cosas)) {
      const idsVistos = new Set();
      const idsGrupos = new Set(estadoNuevo.grupos.map((grupo) => grupo.id));
      estadoNuevo.cosas = bruto.cosas
        .map((bruta) => normalizarCosa(bruta, idsVistos))
        .filter(Boolean)
        .sort(porFecha);
      // Recordar existe siempre: si llegan cosas suyas antes que él (de la nube, por partes), se pone.
      if (!idsGrupos.has(ID_RECORDAR) && estadoNuevo.cosas.some((cosa) => cosa.grupo === ID_RECORDAR)) {
        estadoNuevo.grupos.push({ ...GRUPO_RECORDAR });
        estadoNuevo.grupos.sort(porFecha);
        idsGrupos.add(ID_RECORDAR);
      }
      // Una cosa cuyo grupo ya no existe vuelve al nivel superior: no se pierde.
      for (const cosa of estadoNuevo.cosas) if (cosa.grupo !== null && !idsGrupos.has(cosa.grupo)) cosa.grupo = null;
    }

    const ajustes = bruto.ajustes && typeof bruto.ajustes === 'object' ? bruto.ajustes : {};
    if (esColor(ajustes.colorFondo)) estadoNuevo.ajustes.colorFondo = ajustes.colorFondo.toUpperCase();
    estadoNuevo.ajustes.nombre = textoLimpio(ajustes.nombre, MAX_NOMBRE);
    // El correo que guardaba la 1.2 ya no se usa: no se copia (se olvida al guardar).
    return estadoNuevo;
  }

  /**
   * Respaldo de lo que la versión 1.1 no conoce (grupos, pertenencia y momento de marcado). Una pestaña
   * con la 1.1 todavía abierta reescribe la clave principal sin nada de eso; el respaldo lo devuelve.
   */
  function respaldo() {
    return {
      grupos: estado.grupos,
      pertenencia: estado.cosas.filter((cosa) => cosa.grupo !== null).map((cosa) => [cosa.id, cosa.grupo]),
      hechas: estado.cosas.filter((cosa) => cosa.hechaEn !== null).map((cosa) => [cosa.id, cosa.hechaEn]),
    };
  }

  /** Pares [id, valor] válidos de una lista del respaldo. */
  const paresDe = (lista) => new Map(Array.isArray(lista) ? lista.filter((par) => Array.isArray(par) && typeof par[0] === 'string') : []);

  /** Si la clave principal la escribió la 1.1 (cosas sin «grupos»), recupera del respaldo lo que perdió. Devuelve si lo hizo. */
  function restaurarRespaldo(bruto, estadoNuevo) {
    if (!bruto || typeof bruto !== 'object' || !Array.isArray(bruto.cosas) || Array.isArray(bruto.grupos)) return false;
    let copia;
    try {
      copia = JSON.parse(window.localStorage.getItem(CLAVE_RESPALDO));
    } catch (error) {
      return false;
    }
    if (!copia || typeof copia !== 'object' || !Array.isArray(copia.grupos)) return false;
    const idsVistos = new Set();
    estadoNuevo.grupos = copia.grupos.map((brutoGrupo) => normalizarGrupo(brutoGrupo, idsVistos)).filter(Boolean).sort(porFecha);
    const idsGrupos = new Set(estadoNuevo.grupos.map((grupo) => grupo.id));
    const pertenencia = paresDe(copia.pertenencia);
    const hechas = paresDe(copia.hechas);
    for (const cosa of estadoNuevo.cosas) {
      const grupo = pertenencia.get(cosa.id);
      if (typeof grupo === 'string' && idsGrupos.has(grupo)) cosa.grupo = grupo;
      const hechaEn = hechas.get(cosa.id);
      if (cosa.hecha && Number.isFinite(hechaEn)) cosa.hechaEn = hechaEn;
    }
    return true;
  }

  let gruposRescatados = false; // La última lectura tuvo que recuperar los grupos del respaldo.

  function leerEstado() {
    gruposRescatados = false;
    try {
      const bruto = JSON.parse(window.localStorage.getItem(CLAVE));
      const estadoNuevo = normalizarEstado(bruto);
      gruposRescatados = restaurarRespaldo(bruto, estadoNuevo);
      return estadoNuevo;
    } catch (error) {
      return estadoPorDefecto();
    }
  }

  let nube = null; // Con sesión, la conexión con la nube (nube.js): se le avisa de cada cambio.

  /** Guarda el estado; devuelve false si el almacenamiento falla (modo privado, cuota…). */
  function guardarEstado() {
    if (nube) nube.cambio(); // Aunque aquí no se pueda guardar, la nube sí puede llevárselo.
    programarRecordatorios(); // Y si cambia algo de Recordar (una cosa hecha, una nueva), la función de avisos lo sabrá.
    try {
      window.localStorage.setItem(CLAVE, JSON.stringify(estado));
    } catch (error) {
      return false;
    }
    try {
      window.localStorage.setItem(CLAVE_RESPALDO, JSON.stringify(respaldo()));
    } catch (error) { /* El respaldo es opcional: sin él, lo guardado sigue siendo válido. */ }
    return true;
  }

  /** Lo rescatado del respaldo vuelve al almacenamiento como versión 2 sin esperar al próximo cambio. */
  function consolidarRescate() {
    if (!gruposRescatados) return;
    gruposRescatados = false;
    guardarEstado();
  }

  let persistenciaPedida = false;

  /** Pide al navegador que no borre los datos por falta de espacio (si se puede). */
  function pedirPersistencia() {
    if (persistenciaPedida) return;
    persistenciaPedida = true;
    try {
      if (navigator.storage && typeof navigator.storage.persist === 'function') {
        navigator.storage.persist().catch(() => {});
      }
    } catch (error) { /* Sin soporte: no pasa nada. */ }
  }

  let estado = leerEstado();

  /** Marca de tiempo estrictamente mayor que «ultima», aunque el reloj del sistema retroceda. */
  const marcaDeTiempo = (ultima) => Math.max(Date.now(), ultima + 1);

  const ultimaCreacion = () => Math.max(
    estado.cosas.length ? estado.cosas[0].creada : 0,
    estado.grupos.length ? estado.grupos[0].creada : 0,
  );

  const ultimaHecha = () => estado.cosas.reduce((mayor, cosa) => (cosa.hechaEn > mayor ? cosa.hechaEn : mayor), 0);

  /** El grupo con ese id. Recordar, el grupo fijo, existe siempre: aunque aún no se haya guardado (no tiene nada). */
  const grupoPorId = (id) => estado.grupos.find((grupo) => grupo.id === id) || (id === ID_RECORDAR ? GRUPO_RECORDAR : null);

  const esRecordar = (grupo) => Boolean(grupo) && grupo.id === ID_RECORDAR;

  /** Los grupos que crea quien usa la app (todos menos Recordar, que va aparte). */
  const gruposPropios = () => estado.grupos.filter((grupo) => grupo.id !== ID_RECORDAR);

  const cosasDe = (grupo) => estado.cosas.filter((cosa) => cosa.grupo === grupo);

  /** Recordar pasa a guardarse (y a viajar a la nube) como un grupo más: va a tener algo dentro. */
  function asegurarRecordar() {
    if (estado.grupos.some((grupo) => grupo.id === ID_RECORDAR)) return;
    estado.grupos.push({ ...GRUPO_RECORDAR });
    estado.grupos.sort(porFecha);
  }

  // ---------- Recordatorios (el grupo fijo Recordar) ----------

  /*
   * Lo que se apunta en Recordar puede llevar un recordatorio: a qué hora y cada cuánto avisar. Se repite siempre,
   * hasta que la cosa está hecha:
   *   { hora: 'HH:MM', frec: 'dia' } | { …, frec: 'laborables' } | { …, frec: 'semana', dia: 0..6 } (0 = domingo)
   *   | { …, frec: 'semana', dias: [0..6] } (dos días o más, sin repetir y de menor a mayor)
   *   | { …, frec: 'mes', diaMes: 1..31 } (en los meses más cortos, el último día)
   * Avisa la función de avisos (netlify/functions), con una notificación por cosa: nube.js le lleva los
   * recordatorios (y trae los que se pusieron en otro dispositivo con la misma sesión) y sw.js enseña cada aviso
   * con sus botones «Hecho» y «Mantener».
   *
   * «general» es el que se puso para todas: lo sigue toda cosa de Recordar que no tenga otra cosa dicha, también
   * las que se apunten aquí después. «cosas» guarda solo lo que una cosa tiene de propio: su recordatorio, o false
   * si no lo tiene aunque haya uno para todas (se le quitó a ella sola, o llegó de otro dispositivo y la función de
   * avisos aún no ha dicho cuál tiene: ver aplicarDeLaNube).
   */

  /**
   * Un recordatorio con su forma exacta (la misma que en la función de avisos, netlify/lib/horario.mjs: mismos campos,
   * mismo orden), o null. Uno semanal lleva sus días sin repetir y de menor a mayor; si es uno solo, en «dia», la
   * forma de siempre (la que entienden también las versiones de antes de poder elegir varios).
   */
  function recordatorioDe(bruto) {
    if (!bruto || typeof bruto !== 'object' || typeof bruto.hora !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(bruto.hora) || !FRECUENCIAS.includes(bruto.frec)) return null;
    const { hora, frec } = bruto;
    if (frec === 'semana') {
      const lista = Array.isArray(bruto.dias) ? bruto.dias : [bruto.dia];
      if (lista.length === 0 || lista.length > 7 || !lista.every((dia) => Number.isInteger(dia) && dia >= 0 && dia <= 6)) return null;
      const dias = [...new Set(lista)].sort((a, b) => a - b);
      return dias.length === 1 ? { hora, frec, dia: dias[0] } : { hora, frec, dias };
    }
    if (frec === 'mes') return Number.isInteger(bruto.diaMes) && bruto.diaMes >= 1 && bruto.diaMes <= 31 ? { hora, frec, diaMes: bruto.diaMes } : null;
    return { hora, frec };
  }

  function recordarPorDefecto() {
    return { general: null, cosas: {}, base: null, activar: false };
  }

  /** Los recordatorios guardados (lo que no vale se descarta; nunca lanza). */
  function leerRecordar() {
    const leido = recordarPorDefecto();
    try {
      const bruto = JSON.parse(window.localStorage.getItem(CLAVE_RECORDAR));
      if (!bruto || typeof bruto !== 'object') return leido;
      leido.general = recordatorioDe(bruto.general);
      for (const [id, brutoR] of Object.entries(bruto.cosas && typeof bruto.cosas === 'object' ? bruto.cosas : {})) {
        const r = brutoR === false ? false : recordatorioDe(brutoR);
        if (r !== null) leido.cosas[id] = r;
      }
      // La base es cosa de nube.js (lo último en que este dispositivo y la función de avisos coincidían): aquí solo se guarda.
      leido.base = bruto.base && typeof bruto.base === 'object' ? bruto.base : null;
      // Se dio permiso para las notificaciones pero el alta de este dispositivo se quedó a medias (ver reintentarAvisos).
      leido.activar = bruto.activar === true;
    } catch (error) { /* Sin almacenamiento o datos corruptos: sin recordatorios. */ }
    return leido;
  }

  let recordar = leerRecordar();

  /** Guarda los recordatorios; devuelve false si el almacenamiento falla. */
  function escribirRecordar() {
    try {
      window.localStorage.setItem(CLAVE_RECORDAR, JSON.stringify(recordar));
      return true;
    } catch (error) {
      return false;
    }
  }

  /** Los guarda y, si hace falta, se los cuenta a la función de avisos. */
  function guardarRecordar() {
    const correcto = escribirRecordar();
    programarRecordatorios();
    return correcto;
  }

  /** ¿Hay algo dicho de esa cosa en particular? (Sin Object.hasOwn: Safari anterior a 15.4 no lo conoce.) */
  const tieneLoSuyo = (id) => Object.prototype.hasOwnProperty.call(recordar.cosas, id);

  /** El recordatorio de una cosa de Recordar: el suyo, si tiene uno propio (o ninguno, si se le quitó); si no, el de todas. */
  function recordatorioDeCosa(id) {
    if (tieneLoSuyo(id)) return recordar.cosas[id] || null;
    return recordar.general;
  }

  /** Quita el recordatorio de una cosa sola: si hay uno para todas, queda dicho que ella no lo sigue. */
  function quitarRecordatorioDe(id) {
    if (recordar.general) recordar.cosas[id] = false;
    else delete recordar.cosas[id];
  }

  /** Deja dicho en la propia cosa el recordatorio que tiene ahora (o que no tiene): cambiar el de todas ya no la toca. */
  function fijarRecordatorioDe(id) {
    const r = recordatorioDeCosa(id);
    recordar.cosas[id] = r ? { ...r } : false;
  }

  /** Las cosas de Recordar (el orden de siempre: pendientes primero). */
  const recordadas = () => ordenar(cosasDe(ID_RECORDAR));

  /** Las que siguen pendientes: de las hechas no se avisa, y lo que se diga para varias cosas no las toca. */
  const recordadasPendientes = () => recordadas().filter((cosa) => !cosa.hecha);

  /** ¿Queda en Recordar algo pendiente de lo que haya que avisar? */
  const hayRecordatoriosPendientes = () => cosasDe(ID_RECORDAR).some((cosa) => !cosa.hecha && recordatorioDeCosa(cosa.id) !== null);

  /**
   * Olvida lo propio de las cosas que ya no existen. Solo al arrancar: mientras tanto se conserva (lo que sale de
   * Recordar y vuelve aquí —con «Deshacer», o con «+»— recupera su recordatorio; si lo devuelve otro dispositivo,
   * vale el que él le ponga: ver aplicarDeLaNube; fuera de Recordar no cuenta).
   */
  function olvidarRecordatoriosSinCosa() {
    const existen = new Set(estado.cosas.map((cosa) => cosa.id));
    const sobran = Object.keys(recordar.cosas).filter((id) => !existen.has(id));
    for (const id of sobran) delete recordar.cosas[id];
    if (sobran.length > 0) escribirRecordar();
  }

  const dosCifras = (n) => String(n).padStart(2, '0');

  /** «9:05» (la hora, sin el cero de delante). */
  const horaLegible = (hora) => `${Number(hora.slice(0, 2))}:${hora.slice(3)}`;

  /** Los días de un recordatorio semanal, de lunes a domingo (como se ven en la hoja). */
  const diasDe = (r) => (r.dias || [r.dia]).slice().sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7));

  /** «lunes», «lunes y jueves», «lunes, miércoles y viernes». */
  const enLista = (palabras) => (palabras.length < 2 ? palabras.join('') : `${palabras.slice(0, -1).join(', ')} y ${palabras[palabras.length - 1]}`);

  /**
   * Cada cuánto avisa: «cada día», «de lunes a viernes», «cada lunes», «cada lunes y jueves», «el día 1 de cada
   * mes». Con «cortos», los días de la semana abreviados cuando son tres o más («cada lun, mié y vie»).
   */
  function cadaCuanto(r, cortos = false) {
    if (r.frec === 'laborables') return 'de lunes a viernes';
    if (r.frec === 'semana') {
      const dias = diasDe(r);
      if (dias.length === 7) return 'todos los días';
      if (dias.join() === '1,2,3,4,5') return 'de lunes a viernes';
      return `cada ${enLista(dias.map((dia) => (cortos && dias.length > 2 ? DIAS_CORTOS : DIAS)[dia]))}`;
    }
    if (r.frec === 'mes') return `el día ${r.diaMes} de cada mes`;
    return 'cada día';
  }

  /** Cuándo avisa, dicho de corrido: «cada día a las 9:05» («a la 1:00»). */
  const cuandoDe = (r) => `${cadaCuanto(r)} ${Number(r.hora.slice(0, 2)) === 1 ? 'a la' : 'a las'} ${horaLegible(r.hora)}`;

  /** Y en corto, para la fila de la cosa: «9:00 · Cada día», «9:00 · Día 1 de cada mes», «9:00 · Cada lun, mié y vie». */
  function resumenDe(r) {
    const cada = r.frec === 'mes' ? `día ${r.diaMes} de cada mes` : cadaCuanto(r, true);
    return `${horaLegible(r.hora)} · ${cada.charAt(0).toLocaleUpperCase('es-ES')}${cada.slice(1)}`;
  }

  // ---------- Aviso (toast) ----------

  let temporizadorAviso = 0;
  let temporizadorVaciado = 0;
  let accionAviso = null;

  function avisar(texto, opciones = {}) {
    const { icono = false, accion = null, duracion = DURACION_AVISO, entero = false } = opciones;
    clearTimeout(temporizadorAviso);
    clearTimeout(temporizadorVaciado);

    const contenido = plantillaToast.content.cloneNode(true);
    contenido.querySelector('.toast-texto').textContent = texto;
    if (!icono) contenido.querySelector('.toast-icono').remove();
    const boton = contenido.querySelector('.toast-accion');
    if (accion) boton.textContent = accion.texto;
    else boton.remove();
    accionAviso = accion ? accion.alPulsar : null;
    if (!accion) eliminados = []; // Un aviso sin «Deshacer» sustituye al anterior: ya no hay nada que deshacer.

    toast.replaceChildren(contenido);
    toast.classList.toggle('con-icono', icono);
    toast.classList.toggle('solo-texto', !icono && !accion);
    toast.classList.toggle('entero', entero); // Con «Deshacer» y un texto largo que hay que leer entero: en dos líneas antes que recortado.
    // Reinicia la transición para que avisos seguidos se vean de nuevo.
    toast.classList.remove('visible');
    void toast.offsetWidth;
    toast.classList.add('visible');
    temporizadorAviso = setTimeout(ocultarAviso, duracion);
  }

  function ocultarAviso() {
    clearTimeout(temporizadorAviso);
    clearTimeout(temporizadorVaciado);
    // El foco no puede quedarse en un botón que está a punto de desaparecer.
    if (toast.contains(document.activeElement) && titulos[vistaActual]) {
      titulos[vistaActual].focus({ preventScroll: true });
    }
    accionAviso = null;
    eliminados = [];
    toast.classList.remove('visible');
    temporizadorVaciado = setTimeout(() => toast.replaceChildren(), 200);
  }

  /** Con el foco o el puntero sobre «Deshacer», el aviso espera. */
  function pausarAviso() {
    if (accionAviso) clearTimeout(temporizadorAviso);
  }

  function reanudarAviso() {
    if (!accionAviso || toast.contains(document.activeElement)) return;
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(ocultarAviso, DURACION_DESHACER);
  }

  const avisarFallo = () => avisar('No se ha podido guardar');

  const avisarGuardado = (correcto) => (correcto ? avisar('Guardado', { icono: true }) : avisarFallo());

  function alPulsarAviso(evento) {
    if (!evento.target.closest('.toast-accion') || !accionAviso) return;
    const accion = accionAviso;
    accionAviso = null;
    accion();
  }

  // ---------- Navegación entre vistas ----------

  let vistaActual = 'inicio';
  let abridor = null; // El botón de inicio que abrió la lista o los ajustes.
  let grupoEnPagina = null; // Id del grupo que enseña la vista «grupo».
  let grupoAbridor = null; // Id del grupo abierto desde la lista: al volver, el foco va a su enlace.
  let desplazamientoLista = 0; // Dónde estaba la lista al pasar a un grupo: al volver se recupera.
  let volviendo = false;
  let cambiandoVista = false;
  let temporizadorCambio = 0;

  const grupoMostrado = () => grupoPorId(grupoEnPagina);

  const hashDeGrupo = (id) => `#grupo/${encodeURIComponent(id)}`;

  const entradaDeHistorial = (vista, grupo = null) => ({ cosas: true, vista, grupo });

  /** Lo que pide la URL: una vista y, si es la de un grupo, cuál. Un grupo que no existe cae en la lista. */
  function destinoDesdeHash() {
    const hash = window.location.hash.slice(1);
    const enlace = /^grupo\/(.+)$/.exec(hash);
    if (enlace) {
      let id = enlace[1];
      try {
        id = decodeURIComponent(id);
      } catch (error) { /* Se prueba tal cual. */ }
      return grupoPorId(id) ? { vista: 'grupo', grupo: id } : { vista: 'lista', corregir: true };
    }
    const vista = hash !== 'inicio' && hash !== 'grupo' && VISTAS.includes(hash) ? hash : 'inicio';
    return { vista, grupo: null };
  }

  const urlSinHash = () => window.location.pathname + window.location.search;

  const tieneHistorialPropio = () => Boolean(window.history.state && window.history.state.cosas === true);

  /** Ejecuta una operación de historial sin romper la app si el navegador la rechaza. */
  function conHistorial(operacion) {
    try {
      operacion();
    } catch (error) { /* Sin historial propio, «volver» sustituye la entrada actual. */ }
  }

  function mostrarVista(nombre, opciones = {}) {
    const { animar = true, enfocar = true } = opciones;
    const anterior = vistaActual;
    vistaActual = nombre;

    if (anterior === 'inicio' && nombre !== 'inicio') escrituraInicio.campo.blur();
    if (anterior === 'lista') cancelarEdicionGrupo();
    if (anterior === 'lista' && nombre === 'grupo') desplazamientoLista = zonaLista.scrollTop; // Se lee antes de ocultarla.
    if (anterior === 'grupo') cerrarCandidatas();
    if (accionAviso) ocultarAviso();
    cancelarDictado();
    // Lo que quedó a medio escribir en la página de un grupo era para ese grupo: no pasa a otro.
    if (anterior === 'grupo') vaciarBarra(escrituraGrupo);
    cerrarHoja(false); // El foco lo coloca la vista que llega.
    cerrarHojaGrupo(false);
    cerrarHojaRecordar(false);
    cerrarHojaAvisada(false);

    for (const clave of VISTAS) {
      const activa = clave === nombre;
      vistas[clave].hidden = !activa;
      vistas[clave].toggleAttribute('inert', !activa);
    }
    raiz.dataset.vista = nombre;

    if (nombre === 'lista') pintarLista(anterior === 'grupo' ? desplazamientoLista : 0);
    if (nombre === 'grupo') pintarGrupo();
    if (nombre === 'ajustes') sincronizarAjustes();
    if (anterior === 'ajustes' && nombre !== 'ajustes') guardarDatosPersonales();
    actualizarTema();

    // El arranque (sin animación) no es un cambio de vista: nadie acaba de tocar nada.
    if (animar && anterior !== nombre) {
      animarEntrada(vistas[nombre]);
      protegerCambioDeVista();
    }
    if (enfocar) enfocarVista(nombre, anterior);
  }

  /**
   * «Ver lista» y «Volver» ocupan la misma esquina: el segundo toque de un doble toque
   * caería sobre el botón de la vista recién mostrada. Durante un instante los botones
   * redondos no reciben toques (CSS) y abrir/volver no hacen nada (teclado incluido).
   */
  function protegerCambioDeVista() {
    cambiandoVista = true;
    raiz.toggleAttribute('data-cambiando', true);
    clearTimeout(temporizadorCambio);
    temporizadorCambio = setTimeout(() => {
      cambiandoVista = false;
      raiz.removeAttribute('data-cambiando');
    }, GUARDA_DOBLE_TOQUE);
  }

  function animarEntrada(vista) {
    vista.classList.remove('entrando');
    void vista.offsetWidth;
    vista.classList.add('entrando');
    vista.addEventListener('animationend', () => vista.classList.remove('entrando'), { once: true });
  }

  /** Al entrar, el foco va al título; al volver, al botón (o al enlace del grupo) que abrió la vista. */
  function enfocarVista(nombre, anterior) {
    let destino = titulos[nombre];
    if (nombre === 'inicio') destino = abridor || abridores[anterior];
    else if (nombre === 'lista' && anterior === 'grupo') destino = enlaceDeGrupo(grupoAbridor) || titulos.lista;
    if (destino) destino.focus({ preventScroll: true });
    // (La fila de Recordar no está en la zona que se desplaza: siempre se ve.)
    if (nombre === 'lista' && destino && zonaLista.contains(destino)) asegurarVisibleEnLista(destino);
    if (nombre === 'inicio') abridor = null;
  }

  /**
   * Desplaza la lista lo justo para que el elemento enfocado se vea (si la lista cambió mientras se miraba
   * un grupo, la posición recuperada puede no bastar). Sin scrollIntoView ni focus() con desplazamiento:
   * en iOS moverían el cuerpo fijo, y «nearest» ignora la barra que tapa el final de la zona.
   */
  function asegurarVisibleEnLista(elemento) {
    const zona = zonaLista.getBoundingClientRect();
    const caja = elemento.getBoundingClientRect();
    const techo = zona.top + 16; // La máscara de la cabecera desvanece los primeros 16px.
    const suelo = formularioGrupo.getBoundingClientRect().top; // La barra de crear grupos tapa el final de la zona.
    if (caja.top < techo) zonaLista.scrollTop -= techo - caja.top;
    else if (caja.bottom > suelo) zonaLista.scrollTop += caja.bottom - suelo;
  }

  function abrirVista(nombre, boton) {
    if (vistaActual !== 'inicio' || volviendo || cambiandoVista) return; // Defensa ante dobles toques.
    abridor = boton;
    conHistorial(() => window.history.pushState(entradaDeHistorial(nombre), '', `#${nombre}`));
    mostrarVista(nombre);
  }

  /** Abre la página de un grupo desde su enlace de la lista. */
  function abrirGrupo(id) {
    if (vistaActual !== 'lista' || volviendo || cambiandoVista || !grupoPorId(id)) return;
    grupoEnPagina = id;
    grupoAbridor = id;
    conHistorial(() => window.history.pushState(entradaDeHistorial('grupo', id), '', hashDeGrupo(id)));
    mostrarVista('grupo');
  }

  /** Retrocede una entrada del historial propio (la app la enseña al recibir «popstate»). */
  function retroceder() {
    volviendo = true;
    setTimeout(() => { volviendo = false; }, 600);
    window.history.back();
  }

  function volver() {
    if (vistaActual === 'inicio' || volviendo || cambiandoVista) return;
    if (tieneHistorialPropio()) {
      // Debajo siempre hay una entrada de inicio (y, bajo un grupo, la lista): retroceder no apila historial.
      retroceder();
      return;
    }
    if (vistaActual === 'grupo') {
      irALaLista();
      return;
    }
    conHistorial(() => window.history.replaceState(null, '', urlSinHash()));
    mostrarVista('inicio');
  }

  /**
   * La entrada actual es la de un grupo que ya no existe: se abandona retrocediendo, porque debajo siempre
   * está la de la lista (la puso abrirGrupo o la sembró iniciarNavegacion). Sustituirla por otra de la lista
   * dejaría dos seguidas y «atrás» parecería no hacer nada. Devuelve si había una entrada que abandonar.
   */
  function abandonarEntradaDeGrupo() {
    if (!tieneHistorialPropio() || window.history.state.vista !== 'grupo') return false;
    retroceder();
    return true;
  }

  /** Vuelve a la lista desde un grupo borrado o desconocido (sin historial propio, sustituyendo la entrada). */
  function irALaLista() {
    if (!abandonarEntradaDeGrupo()) conHistorial(() => window.history.replaceState(entradaDeHistorial('lista'), '', '#lista'));
    mostrarVista('lista');
  }

  function alCambiarHistorial() {
    volviendo = false;
    const destino = destinoDesdeHash();
    if (destino.corregir) {
      if (abandonarEntradaDeGrupo()) {
        // El «popstate» que sigue cae en la entrada de la lista, que ya se enseña: no hará nada.
        if (vistaActual !== 'lista') mostrarVista('lista');
        return;
      }
      conHistorial(() => window.history.replaceState(entradaDeHistorial('lista'), '', '#lista'));
    }
    const cambia = destino.vista !== vistaActual || (destino.vista === 'grupo' && destino.grupo !== grupoEnPagina);
    if (destino.vista === 'grupo') grupoEnPagina = destino.grupo;
    if (cambia) mostrarVista(destino.vista);
  }

  function iniciarNavegacion() {
    const destino = destinoDesdeHash();
    if (destino.vista === 'grupo') grupoEnPagina = destino.grupo;
    if (destino.vista !== 'inicio' && !tieneHistorialPropio()) {
      // Carga directa con #lista, #ajustes o #grupo/ID: se siembra «inicio» debajo (y la lista bajo un
      // grupo) para que el botón/gesto de atrás recorra la app hacia atrás y no la cierre.
      conHistorial(() => {
        window.history.replaceState(entradaDeHistorial('inicio'), '', urlSinHash());
        if (destino.vista === 'grupo') window.history.pushState(entradaDeHistorial('lista'), '', '#lista');
        const hash = destino.vista === 'grupo' ? hashDeGrupo(destino.grupo) : `#${destino.vista}`;
        window.history.pushState(entradaDeHistorial(destino.vista, destino.grupo), '', hash);
      });
    } else if (destino.corregir) {
      // Recarga con la URL de un grupo borrado: la entrada se sanea (por si el navegador no navega mientras
      // carga) y, si era la de un grupo, se abandona para no dejar dos entradas de la lista seguidas.
      const eraGrupo = tieneHistorialPropio() && window.history.state.vista === 'grupo';
      conHistorial(() => window.history.replaceState(entradaDeHistorial('lista'), '', '#lista'));
      if (eraGrupo) retroceder();
    }
    window.addEventListener('popstate', alCambiarHistorial);
    // Primero se destapan las vistas: el foco no entra en un árbol con visibility: hidden.
    raiz.classList.remove('arranque-directo');
    mostrarVista(destino.vista, { animar: false, enfocar: destino.vista !== 'inicio' });
  }

  // ---------- Barras de escribir (pantalla principal y página de un grupo) ----------

  /**
   * El hueco del final de la barra: micrófono con el campo vacío (o mientras escucha) y
   * enviar en cuanto hay texto. Nunca los dos a la vez; desactivado = invisible (CSS).
   */
  function actualizarBarra(barra) {
    const escuchando = reconocimiento !== null && dictando === barra;
    const conTexto = barra.campo.value.trim() !== '';
    const focoEnMicro = document.activeElement === barra.micro;
    const focoDeTeclado = focoEnMicro && conFocoVisible(barra.micro);
    barra.enviar.disabled = escuchando || !conTexto;
    barra.micro.disabled = conTexto && !escuchando;
    barra.micro.setAttribute('aria-pressed', String(escuchando));
    barra.micro.setAttribute('aria-label', escuchando ? 'Detener dictado' : 'Dictar');
    barra.pildora.classList.toggle('escuchando', escuchando);
    barra.campo.placeholder = escuchando ? 'Te escucho…' : barra.marcador;
    if (!focoEnMicro || escuchando) return;
    // Al acabar de dictar, el foco puesto con el dedo (Android) se suelta: la barra en reposo no se
    // queda resaltada. El puesto con el teclado se conserva y, sin micrófono, pasa a «Guardar».
    if (!focoDeTeclado) barra.micro.blur();
    else if (barra.micro.disabled) barra.enviar.focus({ preventScroll: true });
  }

  function vaciarBarra(barra) {
    if (barra.campo.value === '') return;
    barra.campo.value = '';
    actualizarBarra(barra);
  }

  /** ¿Tiene el foco por el teclado? (Safari anterior a 15.4 no conoce :focus-visible.) */
  function conFocoVisible(elemento) {
    try {
      return elemento.matches(':focus-visible');
    } catch (error) {
      return false;
    }
  }

  function alEscribir(barra) {
    cancelarDictado(); // Quien teclea ya no dicta; lo reconocido hasta ahora se queda en el campo.
    actualizarBarra(barra);
  }

  function alEnviar(evento, barra) {
    evento.preventDefault();
    // «Guardar» acaba de ocupar el sitio del micrófono que escuchaba: ese toque (o Intro) era un «detener».
    if (!reconocimiento && performance.now() - finDictado < GUARDA_FIN_DICTADO) return;
    cancelarDictado();
    const texto = textoLimpio(barra.campo.value, MAX_TEXTO);
    if (!texto) return;
    // En la página de un grupo, la cosa nace dentro de él.
    const grupo = barra === escrituraGrupo ? grupoMostrado() : null;
    if (barra === escrituraGrupo && !grupo) return;

    const id = nuevoId();
    if (esRecordar(grupo)) asegurarRecordar();
    estado.cosas.unshift({ id, texto, hecha: false, creada: marcaDeTiempo(ultimaCreacion()), hechaEn: null, grupo: grupo ? grupo.id : null });
    const correcto = guardarEstado();

    barra.campo.value = '';
    ultimoEnvio = performance.now();
    actualizarBarra(barra);
    barra.campo.blur(); // Cierra el teclado.
    if (grupo) sincronizarGrupo();
    avisarGuardado(correcto);
    pedirPersistencia();
  }

  // ---------- Dictado ----------

  const Reconocimiento = window.SpeechRecognition || window.webkitSpeechRecognition;
  let reconocimiento = null; // Como mucho hay uno vivo.
  let dictando = null; // La barra en la que escribe.
  let parandoDictado = false; // Ya se le ha pedido stop(): solo queda esperar su «end».
  let inicioDictado = -Infinity;
  let finDictado = -Infinity; // Último dictado que acabó por su cuenta (final, error o corte por espera).
  let ultimoEnvio = -Infinity;
  let temporizadorDictado = 0;

  /** Lo reconocido, listo para el campo: espacios simples, mayúscula inicial y sin pasar del máximo. */
  function textoDictado(bruto) {
    const texto = bruto.replace(/\s+/g, ' ').trim();
    return (texto.charAt(0).toLocaleUpperCase('es-ES') + texto.slice(1)).slice(0, MAX_TEXTO).trim();
  }

  /** Lo que se va entendiendo se escribe en vivo; al terminar queda el texto definitivo. */
  function alResultadoDictado(evento) {
    const trozos = Array.from(evento.results, (resultado) => resultado[0].transcript);
    dictando.campo.value = textoDictado(trozos.join(' '));
    // El campo no tiene el foco y no sigue al texto por sí solo: se enseña lo último que se ha entendido.
    dictando.campo.scrollLeft = dictando.campo.scrollWidth;
  }

  function alErrorDictado(evento) {
    acabarDictado(true);
    if (evento.error === 'service-not-allowed') avisarDictadoNoDisponible();
    // «aborted» es un corte (propio o del sistema), no un fallo que haya que contar.
    else if (evento.error !== 'aborted') avisar(AVISOS_DICTADO.get(evento.error) || 'No se ha podido dictar');
  }

  /**
   * «service-not-allowed»: el servicio de voz del sistema no está disponible (no es el permiso del
   * micrófono). En la app instalada en iPhone o iPad no lo está nunca: allí el micrófono se retira
   * hasta el próximo arranque y queda el dictado del teclado. En los demás casos suele ser un ajuste.
   */
  function avisarDictadoNoDisponible() {
    if (window.navigator.standalone !== true) {
      avisar('El dictado no está disponible: revisa los ajustes del móvil', { duracion: DURACION_DESHACER });
      return;
    }
    avisar('Aquí no se puede dictar: usa el micrófono del teclado', { duracion: DURACION_DESHACER });
    for (const barra of escrituras) {
      if (document.activeElement === barra.micro) barra.campo.focus({ preventScroll: true }); // El foco no se queda en un botón que desaparece.
      barra.micro.hidden = true;
    }
    ayudaDictado.hidden = true;
  }

  /** Suelta el reconocimiento (sin oyentes no queda nada vivo) y devuelve la barra al reposo. */
  function soltarDictado(abortar) {
    if (!reconocimiento) return;
    const usado = reconocimiento;
    const barra = dictando;
    reconocimiento = null;
    dictando = null;
    parandoDictado = false;
    clearTimeout(temporizadorDictado);
    usado.removeEventListener('result', alResultadoDictado);
    usado.removeEventListener('error', alErrorDictado);
    usado.removeEventListener('end', terminarDictado);
    if (abortar) {
      try {
        usado.abort();
      } catch (error) { /* Ya había terminado. */ }
    }
    // Lo dictado se revisa desde el principio (si el corte viene de teclear, el cursor manda).
    if (document.activeElement !== barra.campo) barra.campo.scrollLeft = 0;
    actualizarBarra(barra);
  }

  /**
   * El dictado acaba por su cuenta (final, error o corte por espera): el hueco cambia de botón sin que
   * nadie lo haya tocado, y el toque que llegue enseguida todavía iba dirigido al micrófono que escuchaba.
   */
  function acabarDictado(abortar) {
    soltarDictado(abortar);
    finDictado = performance.now();
  }

  /** Fin natural: el texto reconocido se queda en el campo y se confirma con «Guardar». */
  function terminarDictado() {
    acabarDictado(false);
  }

  /** Corte silencioso porque el usuario ya está en otra cosa: cambio de vista, página oculta, envío o tecleo. */
  function cancelarDictado() {
    soltarDictado(true);
  }

  function empezarDictado(barra) {
    barra.campo.blur(); // Dictar no abre el teclado (y lo cierra si estaba abierto).
    try {
      reconocimiento = new Reconocimiento();
      dictando = barra;
      reconocimiento.lang = 'es-ES';
      reconocimiento.interimResults = true;
      reconocimiento.continuous = false;
      reconocimiento.maxAlternatives = 1;
      reconocimiento.addEventListener('result', alResultadoDictado);
      reconocimiento.addEventListener('error', alErrorDictado);
      reconocimiento.addEventListener('end', terminarDictado);
      reconocimiento.start();
    } catch (error) {
      // start() lanza InvalidStateError si el navegador aún no ha soltado un reconocimiento anterior.
      cancelarDictado();
      avisar('No se ha podido dictar');
      return;
    }
    inicioDictado = performance.now();
    actualizarBarra(barra);
  }

  function alPulsarMicro(barra) {
    const ahora = performance.now();
    if (!reconocimiento) {
      // El micrófono en reposo acaba de aparecer bajo el dedo (tras guardar, o tras un dictado que se
      // quedó sin texto): el segundo toque de un doble toque no empieza a dictar.
      if (ahora - ultimoEnvio < GUARDA_DOBLE_TOQUE || ahora - finDictado < GUARDA_DOBLE_TOQUE) return;
      empezarDictado(barra);
      return;
    }
    // El segundo toque de un doble toque no es un «detener»; y quien insiste mientras para no alarga la espera.
    if (ahora - inicioDictado < GUARDA_DOBLE_TOQUE || parandoDictado) return;
    parandoDictado = true;
    try {
      reconocimiento.stop(); // Entrega lo reconocido hasta ahora y después avisa con «end».
    } catch (error) { /* Ya estaba parando. */ }
    temporizadorDictado = setTimeout(acabarDictado, ESPERA_FIN_DICTADO, true);
  }

  // ---------- Filas (comunes a la lista y a la página de un grupo) ----------

  const filasPorId = new Map(); // Filas de cosas de la vista que se enseña (lista o página de grupo).
  const filasDeGrupo = new Map(); // Filas de grupos (vista de la lista).
  let eliminados = []; // Lo borrado mientras el aviso «Eliminado» sigue a la vista: { cosa } o { grupo, miembros }.
  let ultimoBorrado = -Infinity;
  let contadorPliegues = 0;

  const esGrupo = (elemento) => typeof elemento.nombre === 'string';

  /** Pendientes (y grupos) primero, de más nueva a más antigua; las hechas al final, por el momento en que se marcaron. */
  /** Orden de una lista: primero los grupos (el más nuevo arriba), luego las cosas pendientes (la más nueva arriba) y al final las hechas. */
  function ordenar(elementos) {
    return elementos.sort((a, b) => {
      const grupoA = 'nombre' in a; // Los grupos tienen nombre; las cosas, texto.
      const grupoB = 'nombre' in b;
      if (grupoA !== grupoB) return grupoA ? -1 : 1;
      const hechaA = a.hecha === true;
      const hechaB = b.hecha === true;
      if (hechaA !== hechaB) return hechaA ? 1 : -1;
      return hechaA ? a.hechaEn - b.hechaEn : b.creada - a.creada;
    });
  }

  function pintarFila(fila, hecha) {
    const boton = fila.querySelector('.hecho');
    fila.classList.toggle('hecha', hecha);
    if (!boton) return; // Las candidatas no tienen check.
    boton.setAttribute('aria-pressed', String(hecha));
    boton.setAttribute('aria-label', hecha ? 'Marcar como pendiente' : 'Marcar como hecha');
  }

  function crearFila(cosa, plantilla) {
    const fila = plantilla.content.firstElementChild.cloneNode(true);
    fila.dataset.id = cosa.id;
    fila.querySelector('.texto').textContent = cosa.texto; // Nunca innerHTML con texto del usuario.
    pintarFila(fila, cosa.hecha);
    return fila;
  }

  function aparecer(fila) {
    fila.classList.add('nueva');
    fila.addEventListener('animationend', () => fila.classList.remove('nueva'), { once: true });
  }

  /** La fila de una cosa dentro de ese contenedor: se reutiliza si ya estaba ahí; si no, se crea. */
  function filaDeCosa(cosa, contenedor, plantilla, nuevas) {
    const existente = filasPorId.get(cosa.id);
    if (existente && existente.parentElement === contenedor && !existente.classList.contains('saliendo')) {
      pintarFila(existente, cosa.hecha);
      return existente;
    }
    // Estaba en otro sitio (otro grupo, el desplegable…): si no se está yendo con su animación, se quita.
    if (existente && !existente.closest('.saliendo')) existente.remove();
    const fila = crearFila(cosa, plantilla);
    filasPorId.set(cosa.id, fila);
    if (nuevas) aparecer(fila);
    return fila;
  }

  /**
   * Coloca «elementos» en ese orden dentro del contenedor: mueve solo lo que no está en su sitio,
   * respeta las filas que están saliendo y quita las que ya no tocan.
   */
  function colocar(contenedor, elementos) {
    const deseados = new Set(elementos);
    for (const hijo of Array.from(contenedor.children)) {
      if (!deseados.has(hijo) && !hijo.classList.contains('saliendo')) hijo.remove();
    }
    let cursor = contenedor.firstElementChild;
    for (const elemento of elementos) {
      while (cursor && cursor !== elemento && cursor.classList.contains('saliendo')) cursor = cursor.nextElementSibling;
      if (cursor === elemento) {
        cursor = elemento.nextElementSibling;
        continue;
      }
      contenedor.insertBefore(elemento, cursor);
    }
  }

  /** Olvida las filas que ya no están en el documento. */
  function podarFilas() {
    for (const [id, fila] of filasPorId) if (!fila.isConnected) filasPorId.delete(id);
    for (const [id, fila] of filasDeGrupo) if (!fila.isConnected) filasDeGrupo.delete(id);
  }

  const deslizamientos = new WeakMap(); // El deslizamiento en curso de cada fila (Web Animations).

  /** Posición de cada fila tal como se ve antes de un cambio (solo en listas cortas y con movimiento). */
  function medirFilas(contenedor) {
    if (menosMovimiento.matches || typeof contenedor.animate !== 'function') return null;
    const filas = contenedor.querySelectorAll('li');
    if (filas.length > MAX_FILAS_PLEGADO) return null;
    const medidas = new Map();
    for (const fila of filas) medidas.set(fila, fila.getBoundingClientRect().top);
    return medidas;
  }

  /** Tras recolocar, cada fila arranca donde se veía y se desliza a su sitio nuevo (FLIP). */
  function deslizarFilas(antes) {
    // Un deslizamiento aún en curso (de la fila o de su grupo) la tiene desplazada: se cancela antes de
    // medir dónde le toca estar, y la fila arrancará desde donde se la veía, sin saltos.
    for (const fila of antes.keys()) {
      const enCurso = deslizamientos.get(fila);
      if (!enCurso) continue;
      enCurso.cancel();
      deslizamientos.delete(fila);
    }
    const desplazamientos = new Map();
    for (const [fila, top] of antes) {
      if (!fila.isConnected || fila.classList.contains('saliendo')) continue;
      desplazamientos.set(fila, top - fila.getBoundingClientRect().top);
    }
    for (const [fila, desplazamiento] of desplazamientos) {
      // Una fila anidada ya se mueve con su grupo: solo cuenta lo que se desplaza dentro de él.
      const grupo = fila.parentElement.closest('li');
      const propio = desplazamiento - (grupo && desplazamientos.has(grupo) ? desplazamientos.get(grupo) : 0);
      if (Math.abs(propio) < 1) continue;
      // Sin relleno al acabar: no queda ningún transform (ni temporizador que limpiar).
      const animacion = fila.animate([{ transform: `translateY(${propio}px)` }, { transform: 'none' }], { duration: DURACION_DESLIZAMIENTO, easing: 'ease' });
      deslizamientos.set(fila, animacion);
      animacion.onfinish = () => deslizamientos.delete(fila);
    }
  }

  /** Repinta lo justo de la vista que se enseña para que refleje el estado; con «deslizar», las filas que cambian de sitio se deslizan. */
  function sincronizar(deslizar = false) {
    if (vistaActual !== 'lista' && vistaActual !== 'grupo') return;
    const contenedor = vistaActual === 'lista' ? listaEl : listaGrupo;
    const medidas = deslizar ? medirFilas(contenedor) : null;
    if (vistaActual === 'lista') sincronizarLista();
    else sincronizarGrupo();
    if (medidas) deslizarFilas(medidas);
  }

  /** Anima la salida de la fila y la quita del DOM al terminar. */
  function retirarFila(fila) {
    const quitar = () => fila.remove();
    if (menosMovimiento.matches) {
      quitar();
      return;
    }
    // En listas largas solo se desvanece: plegar la altura recolocaría cientos de filas por fotograma.
    const plegar = fila.parentElement.childElementCount <= MAX_FILAS_PLEGADO;
    if (plegar) fila.style.height = `${fila.offsetHeight}px`;
    fila.classList.add('saliendo');
    fila.classList.toggle('plegada', plegar);
    if (plegar) {
      void fila.offsetHeight;
      fila.style.height = '0px';
    }
    setTimeout(quitar, plegar ? DURACION_SALIDA : DURACION_SALIDA_CORTA);
  }

  function hermana(fila, direccion) {
    let candidata = fila[direccion];
    while (candidata && candidata.classList.contains('saliendo')) candidata = candidata[direccion];
    return candidata;
  }

  /** Cuando una fila se va, el foco pasa al mismo botón de la fila vecina (o a «respaldo» si no queda ninguna). */
  function enfocarVecina(fila, respaldo) {
    const vecina = hermana(fila, 'nextElementSibling') || hermana(fila, 'previousElementSibling');
    // (En Recordar la fila tiene dos botones en ese sitio y solo enseña uno.)
    (vecina ? vecina.querySelector('.borrar:not([hidden])') : respaldo).focus({ preventScroll: true });
  }

  /**
   * Marca o desmarca una cosa (con su check, o con «Hecho» en su recordatorio) y repinta lo que se ve. De una cosa
   * de Recordar hecha ya no se avisa (su aviso, si sigue a la vista, sobra); desmarcada, se le vuelve a avisar con
   * el recordatorio que tenía. Devuelve si se ha podido guardar.
   */
  function ponerHecha(cosa, hecha) {
    cosa.hecha = hecha;
    cosa.hechaEn = hecha ? marcaDeTiempo(ultimaHecha()) : null;
    // Un check tocado aquí lo decide este dispositivo, también cuando vuelve a lo que dice la base: si la función
    // tiene otra cosa (un «Hecho» pulsado en un aviso que la base aún no recoge), que no parezca que aquí no ha
    // cambiado. La base pasa a decir lo contrario, y sin firma: hay algo que contar.
    const deLaBase = cosa.grupo === ID_RECORDAR && recordar.base && recordar.base.cosas && recordar.base.cosas[cosa.id];
    if (deLaBase && deLaBase.hecha === hecha) {
      deLaBase.hecha = !hecha;
      recordar.base.firma = '';
      escribirRecordar();
    }
    const correcto = guardarEstado();
    if (!correcto) avisarFallo();
    if (cosa.grupo === ID_RECORDAR) {
      if (hecha) cerrarAvisoDe(cosa.id);
      else olvidarHechoDe(cosa.id);
    }
    sincronizar(true); // La hecha baja al final (o la pendiente vuelve a su sitio) deslizándose.
    return correcto;
  }

  function alternarHecha(id) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    if (!cosa || !filasPorId.has(id)) return;
    ponerHecha(cosa, !cosa.hecha);
  }

  /** Intro mantenido sobre un botón de una lista no repite la pulsación fila tras fila. */
  function alTeclearEnLista(evento) {
    if (evento.repeat && evento.key === 'Enter' && evento.target.closest('button')) evento.preventDefault();
  }

  // ---------- Lista ----------

  const ultimosOjos = new WeakMap(); // Último toque en el ojo de cada fila de grupo.
  const temporizadoresPliegue = new WeakMap();

  /** Sin cosas sueltas ni grupos propios, la lista lo dice; y si es porque todo está en Recordar (que va aparte, arriba), dice eso. */
  function actualizarVacio() {
    vacio.hidden = cosasDe(null).length > 0 || gruposPropios().length > 0;
    vacio.textContent = cosasDe(ID_RECORDAR).length > 0 ? 'Lo que tienes apuntado está en Recordar.' : 'No tienes cosas apuntadas.';
  }

  /** La fila fija de Recordar: cuántas cosas suyas siguen pendientes (sin número si no hay ninguna). */
  function actualizarAccesoRecordar() {
    const pendientes = cosasDe(ID_RECORDAR).filter((cosa) => !cosa.hecha).length;
    cuentaRecordar.hidden = pendientes === 0;
    cuentaRecordar.textContent = pendientes ? String(pendientes) : '';
    if (pendientes) accesoRecordar.setAttribute('aria-label', `Recordar. ${pendientes === 1 ? 'Una cosa pendiente' : `${pendientes} cosas pendientes`}`);
    else accesoRecordar.removeAttribute('aria-label');
  }

  function actualizarTituloLista() {
    const { nombre } = estado.ajustes;
    titulos.lista.textContent = nombre ? `Cosas de ${nombre}` : 'Cosas';
  }

  function pintarOjo(ojo, abierto) {
    ojo.setAttribute('aria-expanded', String(abierto));
    ojo.setAttribute('aria-label', abierto ? 'Ocultar cosas del grupo' : 'Ver cosas del grupo');
    // Los <svg> no tienen la propiedad «hidden»: se toca el atributo.
    ojo.querySelector('.ojo-abierto').toggleAttribute('hidden', abierto);
    ojo.querySelector('.ojo-cerrado').toggleAttribute('hidden', !abierto);
  }

  /** Nombre, enlace, color y ojo de la fila de un grupo (sin tocar el pliegue). */
  function pintarFilaGrupo(fila, grupo) {
    const enlace = fila.querySelector('.enlace-grupo');
    enlace.textContent = grupo.nombre;
    enlace.setAttribute('href', hashDeGrupo(grupo.id));
    const ojo = fila.querySelector('.ojo');
    ojo.classList.toggle('con-color', grupo.color !== null);
    fila.style.setProperty('--color-grupo', grupo.color || 'transparent');
    fila.style.setProperty('--tinta-grupo', grupo.color ? tintaSobre(grupo.color) : TINTA_CLARA);
    pintarOjo(ojo, grupo.abierto);
  }

  function crearFilaGrupo(grupo) {
    const fila = plantillaGrupo.content.firstElementChild.cloneNode(true);
    fila.dataset.id = grupo.id;
    fila.querySelector('.fila-grupo').dataset.id = grupo.id;
    const pliegue = fila.querySelector('.pliegue');
    contadorPliegues += 1;
    pliegue.id = `pliegue-${contadorPliegues}`;
    fila.querySelector('.ojo').setAttribute('aria-controls', pliegue.id);
    pliegue.hidden = !grupo.abierto;
    pintarFilaGrupo(fila, grupo);
    return fila;
  }

  function filaDeGrupo(grupo, nuevas) {
    const existente = filasDeGrupo.get(grupo.id);
    if (existente && existente.parentElement === listaEl && !existente.classList.contains('saliendo')) {
      pintarFilaGrupo(existente, grupo);
      return existente;
    }
    const fila = crearFilaGrupo(grupo);
    filasDeGrupo.set(grupo.id, fila);
    if (nuevas) aparecer(fila);
    return fila;
  }

  const enlaceDeGrupo = (id) => {
    if (id === ID_RECORDAR) return accesoRecordar;
    const fila = filasDeGrupo.get(id);
    return fila && fila.isConnected ? fila.querySelector('.enlace-grupo') : null;
  };

  /** Mueve, crea y retira filas para que la lista (grupos incluidos) refleje el estado. Recordar va aparte, en su fila fija. */
  function sincronizarLista(nuevas = true) {
    const grupos = gruposPropios();
    const nivel = ordenar([...grupos, ...cosasDe(null)]);
    colocar(listaEl, nivel.map((elemento) => (esGrupo(elemento)
      ? filaDeGrupo(elemento, nuevas)
      : filaDeCosa(elemento, listaEl, plantillaFila, nuevas))));
    for (const grupo of grupos) {
      const fila = filasDeGrupo.get(grupo.id);
      const anidada = fila.querySelector('.anidada');
      const miembros = ordenar(cosasDe(grupo.id));
      colocar(anidada, miembros.map((cosa) => filaDeCosa(cosa, anidada, plantillaFila, nuevas)));
      fila.querySelector('.grupo-vacio').hidden = miembros.length > 0;
    }
    podarFilas();
    actualizarVacio();
    actualizarAccesoRecordar();
  }

  /** La fila fija de Recordar abre su pantalla, como el nombre de cualquier grupo. */
  function alPulsarRecordar(evento) {
    // Con una tecla modificadora o el botón central se deja al navegador (pestaña nueva).
    if (evento.metaKey || evento.ctrlKey || evento.shiftKey || evento.altKey || evento.button !== 0) return;
    evento.preventDefault();
    abrirGrupo(ID_RECORDAR);
  }

  /** Pinta la lista de cero y la deja desplazada donde se pide (arriba, o donde estaba al pasar a un grupo). */
  function pintarLista(desplazamiento = 0) {
    filasPorId.clear();
    filasDeGrupo.clear();
    listaEl.replaceChildren();
    sincronizarLista(false);
    zonaLista.scrollTop = desplazamiento;
    actualizarTituloLista();
  }

  function eliminarCosa(id) {
    const indice = estado.cosas.findIndex((candidata) => candidata.id === id);
    const fila = filasPorId.get(id);
    if (indice < 0 || !fila) return;

    const [cosa] = estado.cosas.splice(indice, 1);
    ultimoBorrado = performance.now();
    guardarEstado();
    if (cosa.grupo === ID_RECORDAR) cerrarAvisoDe(id); // Ya no hay de qué avisar.
    filasPorId.delete(id);
    const grupo = fila.parentElement.closest('li');
    const respaldo = vistaActual === 'grupo' ? botonAnadir : titulos.lista; // En Recordar, sus cosas hechas se eliminan desde su página.
    enfocarVecina(fila, grupo ? grupo.querySelector('.ojo') : respaldo);
    retirarFila(fila);
    sincronizar();

    eliminados.push({ cosa });
    avisar('Eliminado', {
      accion: { texto: 'Deshacer', alPulsar: deshacerEliminado },
      duracion: DURACION_DESHACER,
    });
  }

  /** Borra el grupo; sus cosas vuelven al nivel superior (no se pierde nada). */
  function eliminarGrupo(id) {
    const indice = estado.grupos.findIndex((candidato) => candidato.id === id);
    const fila = filasDeGrupo.get(id);
    if (indice < 0 || !fila) return;

    const [grupo] = estado.grupos.splice(indice, 1);
    const miembros = [];
    for (const cosa of estado.cosas) {
      if (cosa.grupo !== id) continue;
      cosa.grupo = null;
      miembros.push(cosa.id);
    }
    ultimoBorrado = performance.now();
    guardarEstado();
    filasDeGrupo.delete(id);
    enfocarVecina(fila, titulos.lista);
    retirarFila(fila);
    sincronizar();

    eliminados.push({ grupo, miembros });
    avisar('Eliminado', {
      accion: { texto: 'Deshacer', alPulsar: deshacerEliminado },
      duracion: DURACION_DESHACER,
    });
  }

  /** Devuelve una cosa borrada al estado (si su grupo ya no existe, al nivel superior). */
  function reponerCosa(cosa) {
    if (estado.cosas.some((otra) => otra.id === cosa.id)) return false;
    if (cosa.grupo !== null && !grupoPorId(cosa.grupo)) cosa.grupo = null;
    estado.cosas.push(cosa);
    estado.cosas.sort(porFecha);
    return true;
  }

  /** Devuelve un grupo borrado al estado con las cosas que tenía dentro. */
  function reponerGrupo(grupo, miembros) {
    if (grupoPorId(grupo.id)) return false;
    estado.grupos.push(grupo);
    estado.grupos.sort(porFecha);
    for (const cosa of estado.cosas) if (miembros.includes(cosa.id)) cosa.grupo = grupo.id;
    return true;
  }

  /** Devuelve a su grupo una cosa quitada de él (si sigue suelta y el grupo existe). A Recordar vuelve con su recordatorio. */
  function devolverAlGrupo(id, grupo) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    if (!cosa || cosa.grupo !== null || !grupoPorId(grupo)) return false;
    if (grupo === ID_RECORDAR) asegurarRecordar();
    cosa.grupo = grupo;
    return true;
  }

  /** Devuelve a su sitio y estado originales todo lo eliminado (o quitado de su grupo) con el aviso a la vista. */
  function deshacerEliminado() {
    const pendientes = eliminados;
    ocultarAviso();

    let repuesto = null;
    // Del último borrado al primero: cada cosa vuelve a encontrar su grupo ya repuesto.
    for (const borrado of [...pendientes].reverse()) {
      let ok;
      if (borrado.grupo) ok = reponerGrupo(borrado.grupo, borrado.miembros);
      else if (borrado.quitada) ok = devolverAlGrupo(borrado.quitada, borrado.deGrupo);
      else ok = reponerCosa(borrado.cosa);
      if (ok) repuesto = borrado;
    }
    if (!repuesto) return;

    guardarEstado();
    sincronizar();
    const fila = repuesto.grupo ? filasDeGrupo.get(repuesto.grupo.id) : filasPorId.get(repuesto.quitada || repuesto.cosa.id);
    const boton = fila && fila.querySelector(repuesto.grupo ? '.ojo' : '.hecho');
    if (boton) boton.focus({ preventScroll: true });
  }

  /** Despliega o pliega las cosas de un grupo (altura y opacidad; al instante con menos movimiento). */
  function plegar(pliegue, abrir) {
    clearTimeout(temporizadoresPliegue.get(pliegue));
    pliegue.classList.remove('plegando', 'oculto');
    pliegue.style.height = '';
    if (menosMovimiento.matches) {
      pliegue.hidden = !abrir;
      return;
    }
    pliegue.hidden = false;
    const alto = `${pliegue.scrollHeight}px`;
    pliegue.style.height = abrir ? '0px' : alto;
    pliegue.classList.add('plegando');
    pliegue.classList.toggle('oculto', abrir);
    void pliegue.offsetHeight;
    pliegue.style.height = abrir ? alto : '0px';
    pliegue.classList.toggle('oculto', !abrir);
    temporizadoresPliegue.set(pliegue, setTimeout(() => {
      pliegue.classList.remove('plegando', 'oculto');
      pliegue.style.height = '';
      pliegue.hidden = !abrir;
    }, DURACION_SALIDA + 20));
  }

  function alternarOjo(id) {
    const grupo = grupoPorId(id);
    const fila = filasDeGrupo.get(id);
    if (!grupo || !fila) return;
    const ahora = performance.now();
    // El segundo toque de un doble toque no lo vuelve a cerrar (solo el de ese ojo: otro grupo va aparte).
    if (ahora - (ultimosOjos.get(fila) || -Infinity) < GUARDA_DOBLE_TOQUE) return;
    ultimosOjos.set(fila, ahora);
    grupo.abierto = !grupo.abierto;
    if (!guardarEstado()) avisarFallo();
    pintarOjo(fila.querySelector('.ojo'), grupo.abierto);
    plegar(fila.querySelector('.pliegue'), grupo.abierto);
  }

  function alPulsarLista(evento) {
    const enlace = evento.target.closest('.enlace-grupo');
    if (enlace) {
      // Con una tecla modificadora o el botón central se deja al navegador (pestaña nueva).
      if (evento.metaKey || evento.ctrlKey || evento.shiftKey || evento.altKey || evento.button !== 0) return;
      evento.preventDefault();
      abrirGrupo(enlace.closest('.grupo-fila').dataset.id);
      return;
    }
    const boton = evento.target.closest('button[data-accion]');
    const fila = boton && boton.closest('.fila');
    if (!fila || boton.closest('.saliendo')) return; // Ignora toques sobre filas que ya salen.
    const { accion } = boton.dataset;
    if (accion === 'alternar') {
      alternarHecha(fila.dataset.id);
      return;
    }
    if (accion === 'ver') {
      alternarOjo(fila.dataset.id);
      return;
    }
    // Al borrar, la fila vecina sube y su botón queda bajo el dedo: el segundo toque
    // de un doble toque (o un Intro repetido) no debe llevársela también.
    if (performance.now() - ultimoBorrado < GUARDA_DOBLE_TOQUE) return;
    if (accion === 'eliminar-grupo') eliminarGrupo(fila.dataset.id);
    else eliminarCosa(fila.dataset.id);
  }

  // ---------- Barra «Crear grupo de cosas» ----------

  let editandoGrupo = false;
  let ultimaCreacionGrupo = -Infinity;

  function actualizarBarraGrupo() {
    botonConfirmarGrupo.disabled = campoGrupo.value.trim() === '';
  }

  /** La píldora pasa de botón a campo con «Crear» al final, y el teclado se abre. */
  function empezarEdicionGrupo() {
    // El botón acaba de reaparecer bajo el dedo tras crear un grupo: ese toque era el segundo de un doble toque.
    if (editandoGrupo || performance.now() - ultimaCreacionGrupo < GUARDA_DOBLE_TOQUE) return;
    editandoGrupo = true;
    formularioGrupo.classList.add('editando');
    botonCrearGrupo.hidden = true;
    campoGrupo.hidden = false;
    botonConfirmarGrupo.hidden = false;
    actualizarBarraGrupo();
    campoGrupo.focus({ preventScroll: true });
  }

  /** Vuelve la píldora al reposo; con «enfocar», el foco pasa al botón (Escape con teclado). */
  function cancelarEdicionGrupo(enfocar = false) {
    if (!editandoGrupo) return;
    editandoGrupo = false;
    campoGrupo.value = '';
    formularioGrupo.classList.remove('editando');
    campoGrupo.hidden = true;
    botonConfirmarGrupo.hidden = true;
    botonConfirmarGrupo.disabled = true;
    botonCrearGrupo.hidden = false;
    if (enfocar) botonCrearGrupo.focus({ preventScroll: true });
  }

  function alTeclearEnCampoGrupo(evento) {
    if (evento.key !== 'Escape') return;
    evento.preventDefault();
    cancelarEdicionGrupo(true);
  }

  /** Salir del campo sin haber escrito nada es cancelar. */
  function alSalirDeCampoGrupo() {
    if (campoGrupo.value.trim() === '') cancelarEdicionGrupo();
  }

  function alCrearGrupo(evento) {
    evento.preventDefault();
    const nombre = nombreLimpio(campoGrupo.value, MAX_NOMBRE);
    if (!nombre) return;

    estado.grupos.unshift({ id: nuevoId(), nombre, color: null, creada: marcaDeTiempo(ultimaCreacion()), abierto: false });
    const correcto = guardarEstado();

    ultimaCreacionGrupo = performance.now();
    campoGrupo.value = '';
    campoGrupo.blur(); // Cierra el teclado (y, con el campo vacío, devuelve la barra al reposo).
    cancelarEdicionGrupo();
    sincronizarLista();
    if (correcto) avisar('Grupo creado', { icono: true });
    else avisarFallo();
    pedirPersistencia();
  }

  // ---------- Página de un grupo ----------

  let candidatasAbiertas = false;
  let ultimaAlternancia = -Infinity; // Último toque en «Añadir cosas».
  let ultimoMovimiento = -Infinity; // Último «añadir» o «quitar»: la fila vecina sube bajo el dedo.
  let cierreHoja = -Infinity; // Al cerrarse la hoja, lo que hay debajo aparece bajo el dedo.
  let temporizadorCandidatas = 0; // Fin de la animación de cierre del desplegable.

  /** El color del grupo (o el de la app) pinta la vista y la hoja de edición, cada una con su tono. */
  function pintarColorGrupo(grupo) {
    const color = grupo.color || estado.ajustes.colorFondo;
    for (const elemento of [vistas.grupo, hojaGrupo]) {
      elemento.style.setProperty('--fondo', color);
      pintarTono(elemento, color);
    }
  }

  /** Mueve, crea y retira filas para que la página del grupo (y su desplegable) reflejen el estado. */
  function sincronizarGrupo(nuevas = true) {
    const grupo = grupoMostrado();
    if (!grupo) return;
    const miembros = ordenar(cosasDe(grupo.id));
    // En Recordar, cada fila lleva además su recordatorio.
    const plantilla = esRecordar(grupo) ? plantillaRecordada : plantillaMiembro;
    colocar(listaGrupo, miembros.map((cosa) => filaDeCosa(cosa, listaGrupo, plantilla, nuevas)));
    if (esRecordar(grupo)) for (const cosa of miembros) pintarRecordada(filasPorId.get(cosa.id), cosa);
    pintarNotaRecordar();
    if (candidatasAbiertas) {
      // Solo las cosas sueltas: una cosa que ya está en otro grupo no se ofrece (antes hay que quitarla de él con «−»).
      const resto = ordenar(estado.cosas.filter((cosa) => cosa.grupo === null));
      colocar(listaCandidatas, resto.map((cosa) => filaDeCosa(cosa, listaCandidatas, plantillaCandidata, nuevas)));
      sinCandidatas.hidden = resto.length > 0;
    }
    grupoVacio.hidden = miembros.length > 0 || candidatasAbiertas;
    cuerpoGrupo.classList.toggle('centrado', miembros.length === 0 && !candidatasAbiertas);
    podarFilas();
  }

  function pintarGrupo() {
    const grupo = grupoMostrado();
    if (!grupo) return;
    titulos.grupo.textContent = grupo.nombre;
    pintarColorGrupo(grupo);
    // Recordar no tiene nombre ni color que editar: en el sitio del lápiz va la campana de sus recordatorios.
    const fijo = esRecordar(grupo);
    vistas.grupo.toggleAttribute('data-recordar', fijo);
    botonEditarGrupo.hidden = fijo;
    botonConfigurar.hidden = !fijo;
    grupoVacio.textContent = fijo ? 'Apunta aquí lo que quieras que te recuerde y ponle hora.' : 'Este grupo está vacío.';
    filasPorId.clear();
    listaGrupo.replaceChildren();
    if (candidatasAbiertas) listaCandidatas.replaceChildren(); // Se vuelve a llenar al sincronizar.
    else ocultarCandidatas(); // Remata un cierre que se quedó a medias al salir de la página.
    sincronizarGrupo(false);
    zonaGrupo.scrollTop = 0;
  }

  /** Quita de la vista el desplegable (ya cerrado) y vacía sus filas. */
  function ocultarCandidatas() {
    clearTimeout(temporizadorCandidatas);
    candidatas.hidden = true;
    candidatas.classList.remove('saliendo');
    listaCandidatas.replaceChildren();
    podarFilas();
  }

  /**
   * Cierra el desplegable con su animación (al instante si ya no se ve la página o con menos movimiento).
   * El foco vuelve a «Añadir cosas», salvo que el toque que lo cierra haya caído sobre otro control de la página.
   */
  function cerrarCandidatas(enfocar = false) {
    if (!candidatasAbiertas) return;
    candidatasAbiertas = false;
    botonAnadir.setAttribute('aria-expanded', 'false');
    const foco = document.activeElement;
    if (enfocar || !vistas.grupo.contains(foco) || candidatas.contains(foco)) botonAnadir.focus({ preventScroll: true });
    if (vistaActual !== 'grupo' || menosMovimiento.matches) {
      ocultarCandidatas();
    } else {
      candidatas.classList.add('saliendo');
      temporizadorCandidatas = setTimeout(ocultarCandidatas, DURACION_SALIDA_CORTA);
    }
    if (vistaActual === 'grupo') sincronizarGrupo(false); // Vuelven la nota de grupo vacío y su centrado.
  }

  /** Despliega sobre la barra las cosas que faltan y lleva el foco al «+» de la primera que se queda (o a la nota, si no queda ninguna). */
  function abrirCandidatas() {
    clearTimeout(temporizadorCandidatas);
    candidatasAbiertas = true;
    botonAnadir.setAttribute('aria-expanded', 'true');
    candidatas.classList.remove('saliendo');
    candidatas.hidden = false;
    aparecer(candidatas);
    sincronizarGrupo(false);
    candidatas.scrollTop = 0; // Siempre por el principio: el desplazamiento de la vez anterior no cuenta.
    // Una fila que se está yendo sigue en la lista (invisible) hasta que acaba su animación: no vale para el foco.
    (listaCandidatas.querySelector('li:not(.saliendo) .mas') || sinCandidatas).focus({ preventScroll: true });
  }

  function alternarCandidatas() {
    const ahora = performance.now();
    if (ahora - ultimaAlternancia < GUARDA_DOBLE_TOQUE || ahora - cierreHoja < GUARDA_DOBLE_TOQUE) return;
    ultimaAlternancia = ahora;
    if (candidatasAbiertas) cerrarCandidatas(true);
    else abrirCandidatas();
  }

  function alTeclearEnGrupo(evento) {
    if (evento.key !== 'Escape' || !candidatasAbiertas) return;
    cerrarCandidatas(true);
  }

  /** Cambia el grupo de una cosa desde la página: su fila se va y aparece donde le toca. Devuelve si la ha movido. */
  function moverCosa(id, grupoDestino, respaldo) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    const fila = filasPorId.get(id);
    if (!cosa || !fila || cosa.grupo === grupoDestino) return false;
    if (grupoDestino === ID_RECORDAR) asegurarRecordar();
    cosa.grupo = grupoDestino;
    ultimoMovimiento = performance.now();
    const correcto = guardarEstado();
    if (!correcto) avisarFallo();
    filasPorId.delete(id);
    enfocarVecina(fila, respaldo);
    retirarFila(fila);
    sincronizarGrupo();
    return correcto;
  }

  /** «−»: la cosa vuelve al nivel superior de la lista, con «Deshacer» mientras se ve el aviso. */
  function quitarDelGrupo(id) {
    const grupo = grupoEnPagina;
    if (!moverCosa(id, null, botonAnadir)) return;
    // Lo que sale de Recordar deja de avisarse (su aviso, si sigue a la vista, sobra); si vuelve, sigue con su recordatorio.
    if (grupo === ID_RECORDAR) cerrarAvisoDe(id);
    eliminados.push({ quitada: id, deGrupo: grupo });
    avisar(grupo === ID_RECORDAR ? 'Quitado de Recordar' : 'Quitado del grupo', {
      accion: { texto: 'Deshacer', alPulsar: deshacerEliminado },
      duracion: DURACION_DESHACER,
    });
  }

  function alPulsarEnGrupo(evento) {
    const ahora = performance.now();
    if (ahora - cierreHoja < GUARDA_DOBLE_TOQUE) return; // Lo que hay bajo el dedo acaba de aparecer al cerrarse la hoja.
    // Un toque fuera del desplegable (y de su botón) lo cierra; lo tocado se acciona igualmente. Al añadir una cosa el
    // panel encoge por arriba y el segundo toque de un doble toque cae fuera de él: ese no lo cierra.
    const fuera = !candidatas.contains(evento.target) && !botonAnadir.contains(evento.target);
    if (candidatasAbiertas && fuera && ahora - ultimoMovimiento >= GUARDA_DOBLE_TOQUE) cerrarCandidatas();
    const boton = evento.target.closest('button[data-accion]');
    const fila = boton && boton.closest('.fila');
    if (!fila || fila.classList.contains('saliendo')) return;
    const { accion } = boton.dataset;
    if (accion === 'alternar') {
      alternarHecha(fila.dataset.id);
      return;
    }
    // Al añadir, quitar o eliminar, la fila vecina sube y su botón queda bajo el dedo: el segundo toque no se la lleva.
    if (ahora - ultimoMovimiento < GUARDA_DOBLE_TOQUE || ahora - ultimoBorrado < GUARDA_DOBLE_TOQUE) return;
    if (accion === 'recordatorio') {
      abrirHojaRecordar(fila.dataset.id);
      return;
    }
    if (accion === 'anadir') moverCosa(fila.dataset.id, grupoEnPagina, botonAnadir);
    else if (accion === 'quitar') quitarDelGrupo(fila.dataset.id);
    else if (accion === 'eliminar') eliminarCosa(fila.dataset.id); // En Recordar, las hechas: como en la lista, con su «Deshacer».
  }

  // ---------- Hoja de edición del grupo ----------

  function abrirHojaGrupo() {
    const grupo = grupoMostrado();
    if (!grupo || !hojaGrupo.hidden || performance.now() - cierreHoja < GUARDA_DOBLE_TOQUE) return;
    cerrarCandidatas(); // Antes de que la hoja tome el foco: así su cierre no lo devuelve a «Añadir cosas».
    campoNombreGrupo.value = grupo.nombre;
    // Las muestras solo existen mientras la hoja está abierta: en reposo no hay dos grupos de colores en la página.
    construirMuestras(muestrasGrupo, 'color-grupo', [SIN_COLOR, ...PALETA]);
    for (const radio of muestrasGrupo.querySelectorAll('input')) radio.checked = radio.value === (grupo.color || '');
    abrirPanel(hojaGrupo, tituloHojaGrupo);
  }

  /** Cerrar sin guardar descarta los cambios (los campos se rellenan de nuevo al abrir). */
  function cerrarHojaGrupo(devolverFoco = true) {
    if (hojaGrupo.hidden) return;
    cerrarPanel(hojaGrupo);
    muestrasGrupo.replaceChildren();
    cierreHoja = performance.now();
    if (devolverFoco) botonEditarGrupo.focus({ preventScroll: true });
  }

  function guardarEdicionGrupo(evento) {
    evento.preventDefault();
    const grupo = grupoMostrado();
    if (!grupo || hojaGrupo.hidden) return;
    const nombre = nombreLimpio(campoNombreGrupo.value, MAX_NOMBRE);
    if (nombre) grupo.nombre = nombre; // Vacío: se queda el nombre de antes.
    const elegido = muestrasGrupo.querySelector('input:checked');
    grupo.color = elegido && esColor(elegido.value) ? elegido.value.toUpperCase() : null;
    if (!guardarEstado()) avisarFallo();
    titulos.grupo.textContent = grupo.nombre;
    pintarColorGrupo(grupo);
    actualizarTema();
    cerrarHojaGrupo();
  }

  // ---------- Recordar: el recordatorio de cada cosa y su hoja ----------

  let recordandoA = null; // La cosa cuyo recordatorio se edita en la hoja (null: el de todas, o el de las que se elijan).

  /** Pone el texto de una región viva (role="status", aria-live) solo si cambia: repetido, se volvería a leer en voz alta. */
  function decir(elemento, texto) {
    if (elemento.textContent !== texto) elemento.textContent = texto;
  }

  /**
   * La fila de una cosa en Recordar: su recordatorio (o el botón para ponérselo) y, a la derecha, «−» si sigue
   * pendiente (sale de Recordar) o la papelera si está hecha (se elimina: no hay otro sitio desde el que borrarla).
   */
  function pintarRecordada(fila, cosa) {
    if (!fila) return;
    const r = recordatorioDeCosa(cosa.id);
    const cuando = fila.querySelector('.cuando');
    cuando.classList.toggle('sin', !r);
    cuando.querySelector('.cuando-texto').textContent = r ? resumenDe(r) : 'Poner recordatorio';
    cuando.setAttribute('aria-label', r ? `Recordatorio: ${cuandoDe(r)}. Cambiar` : 'Poner recordatorio');
    const quitar = fila.querySelector('[data-accion="quitar"]');
    const eliminar = fila.querySelector('[data-accion="eliminar"]');
    const conFoco = document.activeElement === quitar || document.activeElement === eliminar;
    quitar.hidden = cosa.hecha;
    eliminar.hidden = !cosa.hecha;
    if (conFoco) (cosa.hecha ? eliminar : quitar).focus({ preventScroll: true }); // El foco no se queda en el botón que se esconde.
  }

  /** ¿Enseña este navegador botones en los avisos? (iPhone, iPad y Firefox no: allí se contesta en la app.) */
  const avisosConBotones = () => 'Notification' in window && 'maxActions' in window.Notification && window.Notification.maxActions >= 2;

  // Por qué este dispositivo no va a avisar de lo que hay en Recordar, y lo que se puede hacer aquí mismo para arreglarlo.
  const PEGAS_DE_RECORDAR = {
    apagados: 'Para que te avise, activa las notificaciones en este dispositivo.',
    instalar: `En ${/iPhone|iPod/.test(window.navigator.userAgent) ? 'iPhone' : 'iPad'}, los recordatorios solo llegan a la aplicación: convierte Cosas en aplicación y ponlos desde ella.`,
    bloqueados: 'Las notificaciones de Cosas están bloqueadas en este dispositivo. Para que te avise, permítelas en los ajustes del móvil o del navegador.',
    imposible: 'Este navegador no puede recibir notificaciones: aquí no te avisará.',
    'sin-conexion': 'Sin conexión: tus recordatorios empezarán a avisar cuando vuelva.',
    tope: `Recordar avisa de ${MAX_RECORDADAS} cosas como mucho, las más recientes: de las demás no te avisará.`,
  };
  const ARREGLOS_DE_RECORDAR = { apagados: 'Activar notificaciones', instalar: 'Convertir en aplicación' };

  /**
   * La pega de este dispositivo para avisar ('' si va a hacerlo): el estado de sus notificaciones (las de Ajustes) o,
   * con el permiso ya dado, 'sin-conexion': el alta se quedó a medias o los recordatorios no han podido subirse.
   * Eso se reintenta solo (al volver la conexión, al volver a la app y al abrirla): no hay nada que pulsar.
   * Con todo eso en orden queda 'tope': la función admite MAX_RECORDADAS cosas y le llegan las pendientes más
   * recientes (recordatoriosLocales), así que de las demás no avisará. Va detrás de las otras pegas, que son de
   * todas las cosas y no solo de las que no caben.
   */
  function pegaDeRecordar() {
    const avisos = estadoAvisos();
    if (avisos === 'activos') {
      if (recordatoriosSinSubir() && (rondaFallida || window.navigator.onLine === false)) return 'sin-conexion';
      return cosasDe(ID_RECORDAR).filter((cosa) => !cosa.hecha && recordatorioDeCosa(cosa.id) !== null).length > MAX_RECORDADAS ? 'tope' : '';
    }
    return avisos === 'apagados' && recordar.activar ? 'sin-conexion' : avisos;
  }

  /**
   * La nota de arriba de Recordar: mientras haya algo pendiente con recordatorio y este dispositivo no vaya a avisar,
   * dice por qué y, si se arregla aquí, ofrece el botón. Un recordatorio que se ve puesto pero nunca llega es peor
   * que no tenerlo: por eso no es un aviso que se va, sino una nota que se queda.
   */
  function pintarNotaRecordar() {
    const pega = esRecordar(grupoMostrado()) && hayRecordatoriosPendientes() ? pegaDeRecordar() : '';
    const arreglo = ARREGLOS_DE_RECORDAR[pega] || '';
    decir(textoNotaRecordar, PEGAS_DE_RECORDAR[pega] || '');
    notaRecordar.hidden = pega === '';
    notaRecordar.dataset.pega = pega;
    // El foco no puede quedarse en un botón que va a desaparecer.
    if (!arreglo && document.activeElement === botonNotaRecordar) titulos.grupo.focus({ preventScroll: true });
    botonNotaRecordar.hidden = !arreglo;
    if (!cambiandoAvisos) botonNotaRecordar.textContent = arreglo; // Mientras se activan, el botón dice lo que está haciendo.
  }

  function alPulsarNotaRecordar() {
    if (notaRecordar.dataset.pega === 'instalar') abrirHoja(botonNotaRecordar);
    else alPulsarAvisos(); // El mismo botón que en Ajustes: el permiso se pide dentro de este toque.
  }

  /** Las filas de Recordar (si se están viendo) vuelven a decir cuándo avisa cada cosa. */
  function repintarRecordatorios() {
    if (vistaActual === 'grupo' && esRecordar(grupoMostrado())) sincronizarGrupo(false);
  }

  /** Los desplegables de la hoja: las horas (0 a 23), los minutos (de cinco en cinco) y los días del mes. */
  function construirSelectores() {
    for (let hora = 0; hora < 24; hora += 1) selectorHora.append(new Option(String(hora), dosCifras(hora)));
    for (let minuto = 0; minuto < 60; minuto += PASO_MINUTOS) selectorMinuto.append(new Option(dosCifras(minuto), dosCifras(minuto)));
    for (let dia = 1; dia <= 31; dia += 1) selectorDiaMes.append(new Option(String(dia), String(dia)));
  }

  function radioElegido(nombre) {
    const radio = formularioRecordar.querySelector(`input[name="${nombre}"]:checked`);
    return radio ? radio.value : '';
  }

  function elegirRadio(nombre, valor) {
    for (const radio of formularioRecordar.querySelectorAll(`input[name="${nombre}"]`)) radio.checked = radio.value === String(valor);
  }

  /** Los días de la semana marcados en la hoja (0 = domingo). */
  const diasElegidos = () => Array.from(formularioRecordar.querySelectorAll('input[name="dia"]:checked'), (casilla) => Number(casilla.value));

  function elegirDias(dias) {
    for (const casilla of formularioRecordar.querySelectorAll('input[name="dia"]')) casilla.checked = dias.includes(Number(casilla.value));
  }

  /** Al cambiar algo en la hoja. Los días se marcan y desmarcan, pero siempre queda alguno: el último no se desmarca. */
  function alCambiarLaHoja(evento) {
    if (evento.target.name === 'dia' && diasElegidos().length === 0) evento.target.checked = true;
    pintarHojaRecordar();
  }

  /** ¿Se están eligiendo las cosas una a una? (Solo desde la campana: desde una fila, la cosa ya está dicha.) */
  const eligiendo = () => recordandoA === null && radioElegido('alcance') === 'elegir';

  /** El recordatorio que dice la hoja ahora mismo. */
  function recordatorioDeLaHoja() {
    return recordatorioDe({
      hora: `${selectorHora.value}:${selectorMinuto.value}`,
      frec: radioElegido('frecuencia'),
      dias: diasElegidos(),
      diaMes: Number(selectorDiaMes.value),
    });
  }

  /** Las cosas a las que se aplica lo que diga la hoja: esa, las elegidas o todas las pendientes de Recordar (las hechas no se tocan). */
  function cosasDeLaHoja() {
    if (recordandoA !== null) return [recordandoA];
    if (eligiendo()) return Array.from(elegirRecordar.querySelectorAll('input:checked'), (casilla) => casilla.value);
    return recordadasPendientes().map((cosa) => cosa.id);
  }

  /** Las cosas pendientes de Recordar, cada una con su casilla (ninguna marcada: se elige de cero). */
  function llenarElegibles() {
    const fragmento = document.createDocumentFragment();
    for (const cosa of recordadasPendientes()) {
      const elemento = plantillaElegible.content.firstElementChild.cloneNode(true);
      elemento.querySelector('input').value = cosa.id;
      elemento.querySelector('.elegible-texto').textContent = cosa.texto; // Nunca innerHTML con texto del usuario.
      fragmento.append(elemento);
    }
    elegirRecordar.replaceChildren(fragmento);
  }

  /** La frase de la hoja: lo que va a pasar al guardar. Para todas, también a quién más alcanza y a cuántas les cambia el que tenían. */
  function resumenDeLaHoja(r, ids) {
    if (!r) return '';
    const frase = `Te lo recordaré ${cuandoDe(r)}.`;
    if (recordandoA !== null) {
      // De una cosa hecha no se avisa hasta que se desmarque: la hoja no promete lo que no va a pasar.
      const cosa = estado.cosas.find((candidata) => candidata.id === recordandoA);
      return cosa && cosa.hecha ? `Ya está hecha: no te lo recordaré hasta que la desmarques. Después, ${cuandoDe(r)}.` : frase;
    }
    if (eligiendo()) return ids.length > 0 ? frase : 'Elige al menos una cosa.';
    if (ids.length === 0) return 'Te recordaré a esa hora lo que apuntes aquí.'; // Aún no hay nada pendiente: es para lo que venga.
    const firma = JSON.stringify(r);
    const pisadas = ids.map(recordatorioDeCosa).filter((suyo) => suyo !== null && JSON.stringify(suyo) !== firma).length;
    const cambio = pisadas === 0 ? '' : ` Cambiará el recordatorio de ${pisadas === 1 ? '1 cosa que tenía' : `${pisadas} cosas que tenían`} otro.`;
    return `${frase} También lo que apuntes después.${cambio}`;
  }

  /** Lo que la hoja dice antes de «Guardar»: cómo llegará el aviso, o por qué aquí no llegará. (Dónde se guarda lo dice la propia hoja.) */
  function ayudaDeLaHoja() {
    const avisos = estadoAvisos();
    if (avisos !== 'activos' && avisos !== 'apagados') return PEGAS_DE_RECORDAR[avisos];
    const como = avisosConBotones()
      ? 'Te llegará una notificación por cada cosa, con «Hecho» y «Mantener».'
      : 'Te llegará una notificación por cada cosa: al tocarla, dices si ya está hecha o si la mantienes.';
    if (avisos === 'activos') return como;
    // Sin activar aquí: se activan al guardar. Si el permiso ya está dado, el navegador no vuelve a preguntar.
    const alGuardar = window.Notification.permission === 'granted' ? 'Al guardar se activarán las notificaciones en este dispositivo.' : 'Al guardar te pediremos permiso para enviarte notificaciones.';
    return `${alGuardar} ${como}`;
  }

  /** La hoja, según lo elegido: el día (de la semana o del mes), las cosas que se pueden elegir, la frase y los botones. */
  function pintarHojaRecordar() {
    const frecuencia = radioElegido('frecuencia');
    diaRecordar.hidden = frecuencia !== 'semana';
    mesRecordar.hidden = frecuencia !== 'mes';
    // Del 29 al 31 se avisa de lo que pasa en los meses cortos; la nota solo describe al desplegable mientras se ve
    // (una descripción escondida, un lector de pantalla la leería igual).
    const hastaElFinal = Number(selectorDiaMes.value) > ULTIMO_DIA_DE_TODOS;
    ayudaDiaMes.hidden = !hastaElFinal;
    if (hastaElFinal) selectorDiaMes.setAttribute('aria-describedby', ayudaDiaMes.id);
    else selectorDiaMes.removeAttribute('aria-describedby');
    const eligen = eligiendo();
    if (eligen && elegirRecordar.hidden) llenarElegibles();
    else if (!eligen) elegirRecordar.replaceChildren();
    elegirRecordar.hidden = !eligen;
    const ids = cosasDeLaHoja();
    decir(resumenRecordar, resumenDeLaHoja(recordatorioDeLaHoja(), ids));
    // Sin ninguna cosa elegida no hay nada que guardar.
    botonGuardarRecordar.disabled = eligen && ids.length === 0;
    // Quitar: el de esa cosa, si lo tiene; desde la campana, todos de una vez (el de todas y el de cada cosa pendiente).
    const deTodas = recordandoA === null;
    botonQuitarRecordar.textContent = deTodas ? 'Quitar todos los recordatorios' : 'Quitar recordatorio';
    botonQuitarRecordar.hidden = eligen || !(ids.some((id) => recordatorioDeCosa(id) !== null) || (deTodas && recordar.general !== null));
    ayudaRecordar.textContent = ayudaDeLaHoja();
  }

  /** Abre la hoja del recordatorio: el de esa cosa (desde su fila) o, sin «id» (desde la campana), el de todas o el de las que se elijan. */
  function abrirHojaRecordar(id = null) {
    if (panelAbierto || !esRecordar(grupoMostrado()) || performance.now() - cierreHoja < GUARDA_DOBLE_TOQUE) return;
    const cosa = id === null ? null : estado.cosas.find((candidata) => candidata.id === id && candidata.grupo === ID_RECORDAR);
    if (id !== null && !cosa) return;
    cerrarCandidatas(); // Antes de que la hoja tome el foco: así su cierre no lo devuelve a «Añadir cosas».
    recordandoA = cosa ? cosa.id : null;
    const hoy = new Date();
    const r = (cosa ? recordatorioDeCosa(cosa.id) : recordar.general) || { hora: HORA_POR_DEFECTO, frec: 'dia' };
    cosaRecordar.textContent = cosa ? cosa.texto : '';
    cosaRecordar.hidden = !cosa;
    // «Todas» o «Elegir», solo desde la campana y si hay alguna cosa pendiente (las hechas no se tocan).
    alcanceRecordar.hidden = Boolean(cosa) || recordadasPendientes().length === 0;
    elegirRadio('alcance', 'todas');
    elegirRecordar.hidden = true;
    selectorHora.value = r.hora.slice(0, 2);
    // Los minutos van de cinco en cinco: uno que no lo sea (puesto de otra forma) baja al anterior.
    selectorMinuto.value = dosCifras(Math.floor(Number(r.hora.slice(3)) / PASO_MINUTOS) * PASO_MINUTOS);
    elegirRadio('frecuencia', r.frec);
    // Lo que no dice el recordatorio que se edita, por hoy: el día de la semana y el del mes.
    elegirDias(r.frec === 'semana' ? diasDe(r) : [hoy.getDay()]);
    selectorDiaMes.value = String(r.frec === 'mes' ? r.diaMes : hoy.getDate());
    pintarHojaRecordar();
    abrirPanel(hojaRecordar, tituloHojaRecordar);
    hojaRecordar.querySelector('.hoja-panel').scrollTop = 0; // Siempre por el principio.
  }

  /** Cerrar sin guardar descarta lo elegido. El foco vuelve a lo que abrió la hoja: la fila de la cosa o la campana. */
  function cerrarHojaRecordar(devolverFoco = true) {
    if (hojaRecordar.hidden) return;
    const id = recordandoA;
    cerrarPanel(hojaRecordar);
    elegirRecordar.replaceChildren();
    recordandoA = null;
    cierreHoja = performance.now();
    protegerPaginaTrasLaHoja();
    if (!devolverFoco) return;
    const fila = id === null ? null : filasPorId.get(id);
    const boton = fila && fila.isConnected ? fila.querySelector('.cuando') : null;
    (boton || botonConfigurar).focus({ preventScroll: true });
  }

  /**
   * Lo que se cambia de una vez en los recordatorios de varias cosas se puede deshacer mientras se ve el aviso:
   * todo vuelve a como estaba («previo»: el de todas y lo propio de cada cosa).
   */
  function avisarConVuelta(texto, previo) {
    eliminados = []; // Lo que quedara por deshacer de un aviso anterior ya no se ofrece.
    avisar(texto, { accion: { texto: 'Deshacer', alPulsar: () => deshacerRecordatorios(previo) }, duracion: DURACION_DESHACER, entero: true });
  }

  function deshacerRecordatorios(previo) {
    ocultarAviso();
    recordar.general = previo.general;
    recordar.cosas = previo.cosas;
    if (!guardarRecordar()) avisarFallo();
    repintarRecordatorios();
    if (vistaActual === 'grupo' && !botonConfigurar.hidden) botonConfigurar.focus({ preventScroll: true });
  }

  function guardarHojaRecordar(evento) {
    evento.preventDefault();
    if (hojaRecordar.hidden) return;
    const r = recordatorioDeLaHoja();
    const ids = cosasDeLaHoja();
    const deVarias = recordandoA === null;
    const todas = deVarias && !eligiendo();
    if (!r || (!todas && ids.length === 0)) return;
    const previo = { general: recordar.general, cosas: { ...recordar.cosas } };
    const sinNota = notaRecordar.hidden;
    if (todas) {
      // El de todas: lo siguen las pendientes y las que se apunten después. Las hechas se quedan como están.
      for (const cosa of cosasDe(ID_RECORDAR)) if (cosa.hecha) fijarRecordatorioDe(cosa.id);
      recordar.general = { ...r };
      for (const id of ids) delete recordar.cosas[id];
    } else {
      for (const id of ids) recordar.cosas[id] = { ...r };
    }
    const correcto = guardarRecordar();
    const sinActivar = estadoAvisos() === 'apagados';
    cerrarHojaRecordar();
    repintarRecordatorios();
    // Si la nota acaba de salir (este dispositivo no va a avisar) y la lista está desplazada, se sube hasta ella: es la
    // que lo explica. No mientras se pide el permiso: quien lo está dando no tiene por qué perder de vista su cosa. Ni
    // por la de «sin conexión»: se va sola en cuanto se suben (a veces en menos de un segundo) y el aviso sí llegará.
    if (sinNota && !notaRecordar.hidden && !sinActivar && notaRecordar.dataset.pega !== 'sin-conexion') zonaGrupo.scrollTop = 0;
    // Solo se dice que está guardado: si este dispositivo no va a avisar, lo explica la nota de Recordar, que no se va.
    if (!correcto) avisarFallo();
    else if (deVarias && ids.length > 0) avisarConVuelta(`Recordatorio puesto a ${ids.length === 1 ? '1 cosa' : `${ids.length} cosas`}`, previo);
    else avisar('Recordatorio guardado', { icono: true });
    pedirPersistencia();
    // El permiso se pide aquí mismo, mientras dura el toque en «Guardar»: iPhone y iPad no lo preguntan después.
    if (sinActivar) activarAvisosAlRecordar();
  }

  function quitarDesdeLaHoja() {
    if (hojaRecordar.hidden) return;
    const ids = cosasDeLaHoja();
    const deTodas = recordandoA === null;
    const previo = { general: recordar.general, cosas: { ...recordar.cosas } };
    if (deTodas) {
      // Todos de una vez: el de todas y el de cada cosa pendiente. Las hechas se quedan como están.
      for (const cosa of cosasDe(ID_RECORDAR)) if (cosa.hecha) fijarRecordatorioDe(cosa.id);
      recordar.general = null;
      for (const id of ids) delete recordar.cosas[id];
      // Sin uno para todas, «esta no lo sigue» ya no dice nada.
      for (const [id, suyo] of Object.entries(recordar.cosas)) if (suyo === false) delete recordar.cosas[id];
    } else {
      for (const id of ids) quitarRecordatorioDe(id);
    }
    const correcto = guardarRecordar();
    for (const id of ids) cerrarAvisoDe(id);
    cerrarHojaRecordar();
    repintarRecordatorios();
    if (!correcto) avisarFallo();
    else if (deTodas) avisarConVuelta('Recordatorios quitados', previo);
    else avisar('Recordatorio quitado');
  }

  /**
   * Al guardar un recordatorio con las notificaciones sin activar aquí, se activan (lo mismo que su botón), sin
   * más avisos: lo que pase lo dice la nota de Recordar.
   */
  async function activarAvisosAlRecordar() {
    if (cambiandoAvisos) return;
    ocuparBotonesDeAvisos('Activando…');
    try {
      await activarAvisos();
    } catch (error) {
      guardarAvisos(null); // Con el permiso dado, el alta se reintenta sola (reintentarAvisos).
    } finally {
      soltarBotonesDeAvisos();
      programarRecordatorios(true); // Ya hay sesión con la que contarle los recordatorios a la función de avisos.
    }
  }

  // ---------- Recordar: «Hecho» o «Mantener» en la app ----------

  /*
   * Donde los avisos no tienen botones (iPhone, iPad, Firefox), y siempre que se toca un aviso en vez de sus
   * botones, se contesta aquí: una hoja con cada cosa de la que se ha avisado y sus dos respuestas. Se abre sobre lo
   * que haya en pantalla, sin cambiar de vista (lo que se estuviera escribiendo sigue ahí); si hay otra hoja abierta,
   * espera a que se cierre.
   */
  let contadorAvisadas = 0;
  let focoAntesDeAvisadas = null; // Lo que tenía el foco cuando la hoja apareció: al cerrarla, vuelve ahí.
  let preguntaAplazada = null; // { tocada }: había otra hoja abierta cuando tocaba preguntar.
  let ultimaRespuesta = -Infinity;

  const pendienteEnRecordar = (id) => estado.cosas.find((cosa) => cosa.id === id && cosa.grupo === ID_RECORDAR && !cosa.hecha) || null;

  function elementoDeAvisada(cosa) {
    const elemento = plantillaAvisada.content.firstElementChild.cloneNode(true);
    const texto = elemento.querySelector('.avisada-texto');
    contadorAvisadas += 1;
    texto.id = `avisada-${contadorAvisadas}`;
    texto.textContent = cosa.texto; // Nunca innerHTML con texto del usuario.
    elemento.dataset.id = cosa.id;
    // Los botones se llaman igual en todas: cada uno dice de qué cosa es.
    for (const boton of elemento.querySelectorAll('button')) boton.setAttribute('aria-describedby', texto.id);
    return elemento;
  }

  function pintarTituloAvisadas() {
    tituloHojaAvisada.textContent = listaAvisadas.childElementCount === 1 ? 'Recordatorio' : 'Recordatorios';
  }

  /**
   * Pregunta por esas cosas («Hecho» o «Mantener»); las que ya están hechas, borradas o fuera de Recordar se saltan.
   * «tocada» es la del aviso que se acaba de tocar: va la primera.
   */
  function preguntarPorAvisadas(ids, tocada) {
    const cosas = ids.map(pendienteEnRecordar).filter(Boolean);
    if (cosas.length === 0) return;
    if (panelAbierto && panelAbierto !== hojaAvisada) {
      preguntaAplazada = { tocada }; // Cuando se cierre esa hoja (cerrarPanel).
      return;
    }
    const preguntadas = new Set(Array.from(listaAvisadas.children, (elemento) => elemento.dataset.id));
    for (const cosa of cosas) {
      if (preguntadas.has(cosa.id)) continue;
      if (cosa.id === tocada) listaAvisadas.prepend(elementoDeAvisada(cosa));
      else listaAvisadas.append(elementoDeAvisada(cosa));
    }
    pintarTituloAvisadas();
    if (panelAbierto === hojaAvisada) return; // Ya estaba preguntando: solo se añaden las que faltaban.
    focoAntesDeAvisadas = document.activeElement;
    abrirPanel(hojaAvisada, tituloHojaAvisada);
    hojaAvisada.querySelector('.hoja-panel').scrollTop = 0;
  }

  /** Cerrar sin contestar es mantener: de esos avisos ya no se vuelve a preguntar (cada cosa sigue con su recordatorio). */
  function cerrarHojaAvisada(devolverFoco = true) {
    if (hojaAvisada.hidden) return;
    for (const elemento of listaAvisadas.children) cerrarAvisoDe(elemento.dataset.id);
    cerrarPanel(hojaAvisada);
    listaAvisadas.replaceChildren();
    cierreHoja = performance.now();
    protegerPaginaTrasLaHoja();
    const previo = focoAntesDeAvisadas;
    focoAntesDeAvisadas = null;
    if (!devolverFoco) return;
    const destino = previo && previo !== document.body && previo.isConnected && principal.contains(previo) ? previo : titulos[vistaActual];
    if (destino) destino.focus({ preventScroll: true });
  }

  /** Quita de la hoja lo que ya no hay que preguntar (se contestó en el aviso, o la cosa ya no está pendiente en Recordar). */
  function podarHojaAvisada() {
    if (hojaAvisada.hidden) return;
    for (const elemento of Array.from(listaAvisadas.children)) if (!pendienteEnRecordar(elemento.dataset.id)) elemento.remove();
    if (listaAvisadas.childElementCount === 0) {
      cerrarHojaAvisada();
      return;
    }
    pintarTituloAvisadas();
    if (!hojaAvisada.contains(document.activeElement)) tituloHojaAvisada.focus({ preventScroll: true });
  }

  function alResponderAvisada(evento) {
    const boton = evento.target.closest('button[data-respuesta]');
    const elemento = boton && boton.closest('.avisada');
    // Al contestar una, la siguiente sube y sus botones quedan bajo el dedo: el segundo toque de un doble toque no la contesta también.
    if (!elemento || performance.now() - ultimaRespuesta < GUARDA_DOBLE_TOQUE) return;
    ultimaRespuesta = performance.now();
    const { id } = elemento.dataset;
    const cosa = pendienteEnRecordar(id);
    // «Hecho» es tocar su check; «Mantener», dejarla como está (se avisará la próxima vez). En los dos casos, su aviso ya está contestado.
    if (boton.dataset.respuesta === 'hecho' && cosa && ponerHecha(cosa, true)) avisar('Hecho', { icono: true });
    cerrarAvisoDe(id);
    elemento.remove();
    // Con más cosas por contestar, el foco pasa a la siguiente; sin ninguna, la hoja se cierra.
    const siguiente = listaAvisadas.querySelector('button');
    if (!siguiente) {
      cerrarHojaAvisada();
      return;
    }
    pintarTituloAvisadas();
    siguiente.focus({ preventScroll: true });
  }

  // ---------- Ajustes ----------

  let temporizadorDatos = 0;
  let datosEditados = false;

  /** Muestras de color: las de la paleta y, si se pide, «Sin color» (un anillo vacío). */
  function construirMuestras(contenedor, nombreRadio, paleta) {
    const fragmento = document.createDocumentFragment();
    for (const { nombre, color } of paleta) {
      const muestra = plantillaMuestra.content.firstElementChild.cloneNode(true);
      const radio = muestra.querySelector('input');
      if (color) {
        muestra.classList.toggle('tinta-oscura', tonoDe(color) === 'claro');
        muestra.style.setProperty('--muestra', color);
      } else {
        muestra.classList.add('sin-color');
      }
      radio.name = nombreRadio;
      radio.value = color;
      radio.setAttribute('aria-label', nombre);
      fragmento.append(muestra);
    }
    contenedor.append(fragmento);
  }

  function sincronizarColor() {
    const { colorFondo } = estado.ajustes;
    let enPaleta = false;
    for (const radio of muestras.querySelectorAll('input')) {
      radio.checked = radio.value === colorFondo;
      enPaleta = enPaleta || radio.checked;
    }
    personalizado.classList.toggle('activo', !enPaleta);
    selectorColor.value = colorFondo.toLowerCase();
  }

  function cambiarColor(valor) {
    if (!esColor(valor)) return;
    const color = valor.toUpperCase();
    if (color === estado.ajustes.colorFondo) return;
    estado.ajustes.colorFondo = color;
    aplicarColor(color);
    if (!guardarEstado()) avisarFallo();
    sincronizarColor();
  }

  function sincronizarAjustes() {
    sincronizarColor();
    if (document.activeElement !== campoNombre) campoNombre.value = estado.ajustes.nombre;
    pintarSesion();
    pintarAvisos(); // El permiso ha podido cambiar en los ajustes del móvil.
  }

  /** Guarda el nombre si el usuario lo ha cambiado (autoguardado). */
  function guardarDatosPersonales() {
    clearTimeout(temporizadorDatos);
    // Sin ediciones no hay nada que volcar: el campo puede no reflejar el estado
    // (Ajustes sin abrir en esta sesión, o datos recién llegados de otra pestaña).
    if (!datosEditados) return;
    datosEditados = false;
    const nombre = textoLimpio(campoNombre.value, MAX_NOMBRE);
    if (nombre === estado.ajustes.nombre) return;
    estado.ajustes.nombre = nombre;
    avisarGuardado(guardarEstado());
    actualizarTituloLista();
  }

  function programarGuardadoDatos() {
    datosEditados = true;
    clearTimeout(temporizadorDatos);
    temporizadorDatos = setTimeout(guardarDatosPersonales, RETARDO_AUTOGUARDADO);
  }

  // ---------- Sesión (la de Cosas con y Cosas de) ----------

  /** La cuenta apuntada por esas páginas, o null (sin sesión, o un valor que no se entiende). */
  function leerCuenta() {
    try {
      const cuenta = JSON.parse(localStorage.getItem(CLAVE_CUENTA) || 'null');
      if (!cuenta || typeof cuenta !== 'object') return null;
      const nombre = typeof cuenta.nombre === 'string' ? textoLimpio(cuenta.nombre, MAX_NOMBRE) : '';
      const correo = typeof cuenta.correo === 'string' ? textoLimpio(cuenta.correo, 254) : '';
      return nombre || correo ? { nombre, correo } : null;
    } catch (error) {
      return null;
    }
  }

  /** «Iniciar sesión» o «Cerrar sesión», y a quién pertenece la sesión. La página cuenta/ hace el resto. */
  function pintarSesion() {
    const cuenta = leerCuenta();
    botonSesion.textContent = cuenta ? 'Cerrar sesión' : 'Iniciar sesión';
    botonSesion.href = rutaDeCosasInfo(`cuenta/?desde=app&accion=${cuenta ? 'salir' : 'entrar'}`);
    if (!cuenta) {
      ayudaSesion.textContent = 'Inicia sesión con Google para tener tus cosas, tus grupos y tus listas de Cosas con y Cosas de en todos tus dispositivos. Sin sesión, todo se queda en este dispositivo, menos lo que tenga recordatorio en Recordar: eso se guarda también en el servidor de Cosas para poder avisarte.';
    } else {
      const quien = cuenta.nombre && cuenta.correo ? `${cuenta.nombre} (${cuenta.correo})` : cuenta.nombre || cuenta.correo;
      ayudaSesion.textContent = `Has iniciado sesión como ${quien}: tus cosas, tus grupos, tu nombre y tu color te siguen a cualquier dispositivo, igual que tus listas de Cosas con y Cosas de.`;
    }
  }

  // ---------- Nube (tus cosas en todos tus dispositivos) ----------

  let cargandoNube = false;
  let intentosNube = 0;

  /**
   * Con sesión, nube.js sube y trae las cosas, los grupos y los ajustes (Firestore, con la misma cuenta
   * de Google). Solo en la app publicada (https): netlify.toml sirve ahí la configuración de Firebase de
   * cosas.info. Sin conexión no se puede cargar: se reintenta al volver la conexión.
   */
  function cargarNube() {
    if (nube || cargandoNube || window.location.protocol !== 'https:' || !leerCuenta()) return;
    cargandoNube = true;
    import('./nube.js')
      .then((modulo) => modulo.conectar({ leer: () => estado, aplicar: aplicarDeLaNube, estado: marcarNube }, intentosNube))
      .then((conexion) => {
        nube = conexion;
      })
      .catch(() => {
        intentosNube += 1;
      })
      .finally(() => {
        cargandoNube = false;
      });
  }

  /** Estado de la sincronización ('' sin sesión): solo para quien depure (data-nube en <html>). */
  function marcarNube(texto) {
    if (texto) raiz.dataset.nube = texto;
    else raiz.removeAttribute('data-nube');
  }

  /** Llegan cambios de otro dispositivo: se guardan y se repinta lo que se ve, sin perder el sitio. */
  function aplicarDeLaNube(bruto) {
    const deAntes = new Set(cosasDe(ID_RECORDAR).map((cosa) => cosa.id));
    estado = normalizarEstado(bruto);
    // Lo que entra en Recordar desde otro dispositivo no sigue aquí el de todas ni recupera el recordatorio que tuvo:
    // cuándo avisa (o que no avisa) lo decidió quien lo metió, y lo sabe la función de avisos. Se le pregunta. Si la
    // base aún la tiene (salió y ha vuelto sin una ronda por medio), lleva el de la base: en ese coincidían este
    // dispositivo y la función, así que aquí no ha cambiado y la ronda la deja con el que ella tenga.
    let llegadas = 0;
    for (const cosa of cosasDe(ID_RECORDAR)) {
      if (deAntes.has(cosa.id)) continue;
      const deLaBase = recordar.base && recordar.base.cosas && recordar.base.cosas[cosa.id];
      const r = deLaBase ? recordatorioDe(deLaBase.r) : null;
      if (!r) quitarRecordatorioDe(cosa.id);
      else if (JSON.stringify(r) === JSON.stringify(recordar.general)) delete recordar.cosas[cosa.id];
      else recordar.cosas[cosa.id] = r;
      llegadas += 1;
    }
    if (llegadas > 0) escribirRecordar();
    guardarEstado();
    programarRecordatorios(true);
    aplicarColor(estado.ajustes.colorFondo);
    actualizarTituloLista();
    if (vistaActual === 'ajustes') sincronizarAjustes();
    if (vistaActual === 'grupo') {
      const grupo = grupoMostrado();
      if (!grupo) {
        irALaLista(); // Lo han borrado en otro dispositivo.
        return;
      }
      titulos.grupo.textContent = grupo.nombre;
      pintarColorGrupo(grupo);
      actualizarTema();
    }
    sincronizar(true);
    revisarAvisosEnEspera();
  }

  // ---------- Novedades de Cosas con y Cosas de (el punto verde de sus iconos) ----------

  let novedades = null;
  let cargandoNovedades = false;
  let intentosNovedades = 0;

  function tieneListasCompartidas() {
    try {
      const ids = JSON.parse(localStorage.getItem(CLAVE_LISTAS) || '[]');
      return Array.isArray(ids) && ids.length > 0;
    } catch (error) {
      return false;
    }
  }

  /**
   * Si otra persona ha apuntado algo que aún no has visto en una de tus listas, un punto verde en el icono
   * de Cosas con o de Cosas de. nube.js lo vigila con la sesión de esas páginas; solo en la app publicada
   * (https) y si en este dispositivo hay listas suyas. Sin conexión se reintenta al volver.
   */
  function cargarNovedades() {
    if (novedades || cargandoNovedades || window.location.protocol !== 'https:' || !tieneListasCompartidas()) return;
    cargandoNovedades = true;
    import('./nube.js')
      .then((modulo) => modulo.vigilarNovedades(pintarNovedades, intentosNovedades))
      .then((vigilancia) => {
        novedades = vigilancia;
      })
      .catch(() => {
        intentosNovedades += 1;
      })
      .finally(() => {
        cargandoNovedades = false;
      });
  }

  function pintarNovedades(hay) {
    for (const acceso of document.querySelectorAll('#accesos a[data-tipo]')) {
      const nueva = Boolean(hay[acceso.dataset.tipo]);
      acceso.toggleAttribute('data-novedad', nueva);
      if (nueva) acceso.setAttribute('aria-label', `${acceso.textContent.trim()}. Hay cosas nuevas`);
      else acceso.removeAttribute('aria-label');
    }
  }

  /** «Hecho»/Intro en el teclado: guarda (al perder el foco) y cierra el teclado. */
  function alPulsarTeclaEnDatos(evento) {
    if (evento.key !== 'Enter') return;
    evento.preventDefault();
    evento.target.blur();
  }

  // ---------- Deslizar de lado ----------

  /*
   * Deslizar el dedo de lado hace lo mismo que los botones. En la pantalla principal abre el de la esquina
   * de la que se tira: hacia la derecha, la lista (la esquina izquierda); hacia la izquierda, los ajustes.
   * En la lista y en los ajustes, el gesto contrario (de vuelta) es «Volver»: hacia la izquierda en la
   * lista, hacia la derecha en los ajustes. Sin nada que se mueva ni se marque mientras tanto: decide dónde
   * se suelta, así que volver atrás con el dedo no hace nada. Con eventos táctiles, que siguen llegando
   * aunque el navegador tome el gesto (los de puntero se cancelarían); styles.css le quita al navegador el
   * deslizar horizontal en estas vistas.
   */
  const AL_DESLIZAR = {
    inicio: { derecha: () => abrirVista('lista', abridores.lista), izquierda: () => abrirVista('ajustes', abridores.ajustes) },
    lista: { izquierda: volver },
    ajustes: { derecha: volver },
  };

  let tiron = null; // { vista, x, y, inicio, horizontal }

  function soltarTiron() {
    tiron = null;
  }

  /** Con el zoom de pellizco puesto, arrastrar mueve la vista ampliada: no es un gesto de la app. */
  const conZoom = () => Boolean(window.visualViewport && window.visualViewport.scale > 1.01);

  function alEmpezarTiron(evento) {
    soltarTiron();
    if (evento.touches.length !== 1 || evento.currentTarget !== vistas[vistaActual] || !AL_DESLIZAR[vistaActual]) return;
    if (cambiandoVista || volviendo || panelAbierto || conZoom()) return;
    // En las barras de abajo y en los campos de texto (el nombre), el dedo es para escribir.
    if (evento.target.closest('.barra') || esCampoDeTexto(evento.target)) return;
    const { clientX: x, clientY: y } = evento.touches[0];
    if (x < BORDE_DEL_SISTEMA || x > window.innerWidth - BORDE_DEL_SISTEMA) return;
    tiron = { vista: vistaActual, x, y, inicio: performance.now(), horizontal: null };
  }

  function alMoverTiron(evento) {
    if (!tiron) return;
    if (evento.touches.length !== 1) {
      soltarTiron(); // Un segundo dedo: es un pellizco.
      return;
    }
    if (tiron.horizontal !== null) return;
    const dx = evento.touches[0].clientX - tiron.x;
    const dy = evento.touches[0].clientY - tiron.y;
    if (Math.hypot(dx, dy) < 10) return; // Aún no se sabe hacia dónde va.
    tiron.horizontal = Math.abs(dx) > Math.abs(dy) * 1.5;
    if (!tiron.horizontal) soltarTiron();
  }

  function alSoltarTiron(evento) {
    const hecho = tiron;
    soltarTiron();
    if (!hecho || !hecho.horizontal || evento.touches.length > 0 || hecho.vista !== vistaActual) return;
    const dx = evento.changedTouches[0].clientX - hecho.x;
    const dy = evento.changedTouches[0].clientY - hecho.y;
    const rapido = performance.now() - hecho.inicio < GOLPE_RAPIDO;
    const recorrido = Math.abs(dx);
    if (recorrido < (rapido ? UMBRAL_DESLIZAR / 2 : UMBRAL_DESLIZAR) || recorrido <= Math.abs(dy)) return;
    const accion = AL_DESLIZAR[vistaActual][dx > 0 ? 'derecha' : 'izquierda'];
    if (accion) accion();
  }

  // ---------- Hojas (paneles inferiores) ----------

  let panelAbierto = null; // Como mucho hay una hoja abierta: la de pasos, la de edición o una de las de Recordar.
  let aperturaHoja = -Infinity;
  let temporizadorApertura = 0;
  let temporizadorCierre = 0;

  /**
   * Mientras una hoja está abierta el resto de la página no cuenta: inerte donde se puede y, donde no
   * (iOS 15), oculta a los lectores de pantalla, tapada por el velo y con el tabulador retenido dentro.
   */
  function abrirPanel(panel, titulo) {
    panel.hidden = false;
    panelAbierto = panel;
    principal.toggleAttribute('inert', true);
    principal.setAttribute('aria-hidden', 'true');
    document.addEventListener('keydown', alTeclearConHoja);
    aperturaHoja = performance.now();
    // La hoja sube bajo el dedo que la abrió (o aparece sola, si pregunta por un recordatorio): durante un instante
    // su panel no recibe toques (CSS), y el segundo toque de un doble toque no acciona lo que acaba de aparecer.
    panel.toggleAttribute('data-abriendo', true);
    clearTimeout(temporizadorApertura);
    temporizadorApertura = setTimeout(() => panel.removeAttribute('data-abriendo'), GUARDA_DOBLE_TOQUE);
    titulo.focus({ preventScroll: true });
  }

  function cerrarPanel(panel) {
    panel.hidden = true;
    panel.removeAttribute('data-abriendo');
    clearTimeout(temporizadorApertura);
    panelAbierto = null;
    principal.removeAttribute('inert');
    principal.removeAttribute('aria-hidden');
    document.removeEventListener('keydown', alTeclearConHoja);
    // Había que preguntar por un recordatorio y esta hoja estaba en medio: ahora sí.
    if (preguntaAplazada && panel !== hojaAvisada) {
      const { tocada } = preguntaAplazada;
      preguntaAplazada = null;
      revisarAvisos(tocada);
    }
  }

  /**
   * Las hojas de Recordar se cierran con un toque en su parte baja («Guardar», «Hecho», «Mantener»), justo encima de la
   * barra de escribir, y la de los avisos sale sobre cualquier vista: al cerrarse, lo que tenían debajo queda bajo el
   * dedo. Durante un instante la página no recibe toques (CSS): el segundo toque de un doble toque no enfoca el campo,
   * ni empieza a dictar, ni sigue un enlace. (Se llama después de anotar «cierreHoja»: cuando la página vuelve a
   * recibir toques, esa guarda también ha pasado.)
   */
  function protegerPaginaTrasLaHoja() {
    raiz.toggleAttribute('data-hoja-cerrada', true);
    clearTimeout(temporizadorCierre);
    temporizadorCierre = setTimeout(() => raiz.removeAttribute('data-hoja-cerrada'), GUARDA_DOBLE_TOQUE);
  }

  function cerrarHojaAbierta() {
    if (panelAbierto === hoja) cerrarHoja();
    else if (panelAbierto === hojaGrupo) cerrarHojaGrupo();
    else if (panelAbierto === hojaRecordar) cerrarHojaRecordar();
    else if (panelAbierto === hojaAvisada) cerrarHojaAvisada();
  }

  function alTeclearConHoja(evento) {
    if (evento.key === 'Escape') {
      cerrarHojaAbierta();
      return;
    }
    if (evento.key !== 'Tab' || !panelAbierto) return;
    // El tabulador da vueltas dentro de la hoja y no sale a la página. De cada grupo de radios solo cuenta el elegido,
    // que es donde se detiene: si el último control fuese un radio sin elegir (la hoja del recordatorio con «Guardar»
    // apagado), nunca se llegaría a él y el foco se escaparía.
    const focables = Array.from(panelAbierto.querySelectorAll('button, input, select'))
      .filter((elemento) => !elemento.disabled && elemento.offsetParent !== null && (elemento.type !== 'radio' || elemento.checked));
    if (!focables.length) return;
    const indice = focables.indexOf(document.activeElement);
    if (evento.shiftKey && indice <= 0) {
      evento.preventDefault();
      focables[focables.length - 1].focus();
    } else if (!evento.shiftKey && indice === focables.length - 1) {
      evento.preventDefault();
      focables[0].focus();
    }
  }

  function alPulsarCierreDeHoja() {
    // El velo y «Cerrar» aparecen bajo el dedo que abrió la hoja: el segundo toque de un doble toque no la cierra. Ni
    // el que sigue a contestar un aviso: la hoja encoge por arriba y «Cerrar» baja hasta donde estaba el botón pulsado.
    const ahora = performance.now();
    if (ahora - aperturaHoja >= GUARDA_DOBLE_TOQUE && ahora - ultimaRespuesta >= GUARDA_DOBLE_TOQUE) cerrarHojaAbierta();
  }

  // ---------- Aplicación (añadir a la pantalla de inicio) ----------

  const enModoAplicacion = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  // iPadOS se presenta como un Mac: lo delata la pantalla táctil.
  const esIOS = /iPhone|iPad|iPod/.test(window.navigator.userAgent)
    || (/Macintosh/.test(window.navigator.userAgent) && window.navigator.maxTouchPoints > 1);
  let eventoInstalar = null; // El «beforeinstallprompt» retenido: con él se abre el diálogo del sistema.
  let abridorDeHoja = botonInstalar; // El botón que abrió la hoja de pasos: el de Ajustes o el de la nota de Recordar.
  let instalando = false;
  let recienInstalada = false;

  function ayudaDeInstalacion() {
    if (enModoAplicacion) return 'Ya la estás usando como aplicación.';
    if (recienInstalada) return 'Ya está en tu pantalla de inicio.';
    if (eventoInstalar) return 'Se añadirá a tu pantalla de inicio.';
    if (esIOS) return `En ${/iPhone|iPod/.test(window.navigator.userAgent) ? 'iPhone' : 'iPad'} se hace en tres pasos.`;
    return 'Se hace desde el menú del navegador.';
  }

  function pintarInstalacion() {
    const sinBoton = enModoAplicacion || recienInstalada;
    // El foco no puede quedarse en un botón que va a desaparecer.
    if (sinBoton && document.activeElement === botonInstalar) titulos.ajustes.focus({ preventScroll: true });
    botonInstalar.hidden = sinBoton;
    ayudaInstalar.textContent = ayudaDeInstalacion();
  }

  /** Hoja con los pasos a mano (iPhone y iPad no tienen otra forma; otros navegadores, a veces). */
  function abrirHoja(desde = botonInstalar) {
    abridorDeHoja = desde;
    pasosIos.hidden = !esIOS;
    pasosGenericos.hidden = esIOS;
    // En iOS la app instalada guarda sus datos aparte de Safari: quien ya tiene algo apuntado debe saberlo antes.
    const conDatos = estado.cosas.length > 0 || estado.grupos.length > 0 || estado.ajustes.nombre !== '';
    avisoDatosIos.hidden = !(esIOS && conDatos);
    abrirPanel(hoja, tituloHoja);
  }

  function cerrarHoja(devolverFoco = true) {
    if (hoja.hidden) return;
    cerrarPanel(hoja);
    if (devolverFoco) abridorDeHoja.focus({ preventScroll: true });
    abridorDeHoja = botonInstalar;
  }

  function alPoderInstalar(evento) {
    evento.preventDefault(); // Sin la barra del navegador: la instalación se ofrece desde Ajustes.
    eventoInstalar = evento;
    pintarInstalacion();
  }

  /** Instalada desde el botón o desde el menú del navegador («appinstalled» llega en los dos casos). */
  function marcarInstalada() {
    if (recienInstalada) return;
    recienInstalada = true;
    eventoInstalar = null;
    cerrarHoja();
    pintarInstalacion();
    avisar('Aplicación añadida');
  }

  async function alPulsarInstalar() {
    if (instalando) return; // El diálogo del sistema ya está abierto (doble toque).
    if (!eventoInstalar) {
      abrirHoja();
      return;
    }
    const evento = eventoInstalar;
    eventoInstalar = null; // Solo sirve una vez: hasta que llegue otro, el botón enseña los pasos.
    instalando = true;
    try {
      await evento.prompt();
      const eleccion = await evento.userChoice;
      if (eleccion && eleccion.outcome === 'accepted') marcarInstalada();
    } catch (error) {
      // El navegador no ha abierto su diálogo: quedan los pasos a mano.
      if (vistaActual === 'ajustes') abrirHoja();
    }
    instalando = false;
    pintarInstalacion();
  }

  // ---------- Compartir la aplicación ----------

  let compartiendo = false;

  /** Copia el enlace a mano (sin permiso para el portapapeles, a la antigua). */
  async function copiarEnlace(enlace) {
    try {
      await window.navigator.clipboard.writeText(enlace);
      return true;
    } catch (error) { /* Se intenta a la antigua. */ }
    const area = document.createElement('textarea');
    area.value = enlace;
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.append(area);
    area.select();
    let copiado = false;
    try { copiado = document.execCommand('copy'); } catch (error) { /* Tampoco. */ }
    area.remove();
    botonCompartir.focus({ preventScroll: true });
    return copiado;
  }

  /**
   * El enlace de la app (su portada, donde esté publicada) con el menú de compartir del sistema; un
   * mensaje corto, que la vista previa (las etiquetas og: de index.html) pone el resto. Sin ese menú, o
   * si el sistema no lo abre, se copia el enlace.
   */
  async function alPulsarCompartir() {
    if (compartiendo) return; // El menú del sistema ya está abierto (doble toque).
    compartiendo = true;
    const enlace = new URL('./', window.location.href).href;
    try {
      if (window.navigator.share) {
        try {
          await window.navigator.share({ title: 'Cosas', text: 'Te recomiendo Cosas', url: enlace });
          return;
        } catch (error) {
          if (error && error.name === 'AbortError') return; // Cerrado sin elegir: no pasa nada.
        }
      }
      if (await copiarEnlace(enlace)) avisar('Enlace copiado', { icono: true });
      else avisar('No se pudo copiar el enlace');
    } finally {
      compartiendo = false;
    }
  }

  // ---------- Notificaciones (recordatorios de Recordar y avisos de Cosas con y Cosas de) ----------

  /*
   * Con las notificaciones activadas, la función de avisos (netlify/functions/avisos.mjs, en /api/avisos/ de
   * este dominio) avisa a este dispositivo a la hora de cada recordatorio de Recordar y cuando alguien añade una
   * cosa a una de tus listas de Cosas con o de Cosas de, y sw.js enseña el aviso. El dispositivo se suscribe
   * (push) con la clave pública de la función y se da de alta con la sesión de esas páginas (nube.js). Lo
   * recuerda en CLAVE_AVISOS ({ uid, endpoint, en }), que también leen ellas: si allí se entra con otra cuenta,
   * los avisos pasan a la nueva.
   */
  const API_AVISOS = new URL('api/avisos/', new URL('./', window.location.href)).href;
  const RENOVAR_AVISOS = 24 * 60 * 60 * 1000; // Cada día, la función vuelve a oír que este dispositivo sigue ahí.
  const ESPERA_AVISOS = 15000;
  const puedeAvisar = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  let cambiandoAvisos = false;
  let claveAvisos = null; // Promesa de la clave pública de la función, en bytes.

  function leerAvisos() {
    try {
      const guardado = JSON.parse(localStorage.getItem(CLAVE_AVISOS));
      return guardado && typeof guardado.endpoint === 'string' ? guardado : null;
    } catch (error) {
      return null;
    }
  }

  function guardarAvisos(valor) {
    try {
      if (valor) localStorage.setItem(CLAVE_AVISOS, JSON.stringify(valor));
      else localStorage.removeItem(CLAVE_AVISOS);
    } catch (error) { /* Sin almacenamiento: la próxima vez se vuelve a dar de alta. */ }
  }

  /** 'activos', 'apagados', 'bloqueados' (sin permiso), 'instalar' (iPhone o iPad en Safari) o 'imposible'. */
  function estadoAvisos() {
    if (!puedeAvisar) return esIOS && !enModoAplicacion ? 'instalar' : 'imposible';
    if (Notification.permission === 'denied') return 'bloqueados';
    return Notification.permission === 'granted' && leerAvisos() ? 'activos' : 'apagados';
  }

  const PARA_QUE_AVISOS = 'te recordamos lo que tengas en Recordar y te avisamos cuando alguien añada una cosa a tus listas de Cosas con y Cosas de.';

  function pintarAvisos() {
    if (cambiandoAvisos) return; // Hasta que termine, el botón dice lo que está haciendo.
    const actual = estadoAvisos();
    const boton = { apagados: 'Activar notificaciones', activos: 'Desactivar notificaciones' }[actual];
    // El foco no puede quedarse en un botón que va a desaparecer.
    if (!boton && document.activeElement === botonAvisos) titulos.ajustes.focus({ preventScroll: true });
    botonAvisos.hidden = !boton;
    if (boton) botonAvisos.textContent = boton;
    const dispositivo = /iPhone|iPod/.test(window.navigator.userAgent) ? 'iPhone' : 'iPad';
    ayudaAvisos.textContent = {
      apagados: `Si las activas, ${PARA_QUE_AVISOS}`,
      activos: `Activadas en este dispositivo: ${PARA_QUE_AVISOS}`,
      bloqueados: 'Las notificaciones de Cosas están bloqueadas en este dispositivo. Para recibirlas, permítelas en los ajustes del móvil o del navegador.',
      instalar: `En ${dispositivo}, las notificaciones llegan a la aplicación: pulsa «Convertir en aplicación» y actívalas desde ella.`,
      imposible: 'Este navegador no puede recibir notificaciones.',
    }[actual];
  }

  /** «promesa», o un error si tarda más de «ms» (un service worker que no llega a estar listo, por ejemplo). */
  function conTiempo(promesa, ms = ESPERA_AVISOS) {
    let temporizador = 0;
    const tarde = new Promise((_, rechazar) => {
      temporizador = setTimeout(() => rechazar(new Error('Tiempo agotado')), ms);
    });
    return Promise.race([promesa, tarde]).finally(() => clearTimeout(temporizador));
  }

  function bytesDeBase64(texto) {
    const base64 = texto.replace(/-/g, '+').replace(/_/g, '/');
    const binario = window.atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4));
    return Uint8Array.from(binario, (letra) => letra.charCodeAt(0));
  }

  /** La clave pública de la función (una vez por sesión; si falla, se volverá a pedir). */
  function pedirClaveAvisos() {
    if (!claveAvisos) {
      claveAvisos = conTiempo(fetch(`${API_AVISOS}clave`))
        .then((respuesta) => (respuesta.ok ? respuesta.json() : Promise.reject(new Error(`Clave de avisos: ${respuesta.status}`))))
        .then((datos) => bytesDeBase64(datos.clave));
      claveAvisos.catch(() => {
        claveAvisos = null;
      });
    }
    return claveAvisos;
  }

  /** ¿Esa suscripción se hizo con esta clave? (Si la función cambiara de claves, habría que suscribirse otra vez.) */
  function mismaClave(suscripcion, clave) {
    const suya = suscripcion.options && suscripcion.options.applicationServerKey;
    if (!suya) return true; // El navegador no lo dice: se da por buena.
    const bytes = new Uint8Array(suya);
    return bytes.length === clave.length && bytes.every((valor, i) => valor === clave[i]);
  }

  async function sesionDeAvisos() {
    const nube = await import('./nube.js');
    const sesion = await conTiempo(nube.sesionDeAvisos());
    if (!sesion) throw new Error('Sin configuración de Firebase');
    return sesion;
  }

  async function llamarAvisos(accion, sesion, datos) {
    const respuesta = await conTiempo(fetch(`${API_AVISOS}${accion}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${await sesion.token()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(datos),
    }));
    if (!respuesta.ok) throw new Error(`Avisos (${accion}): ${respuesta.status}`);
  }

  async function darDeAltaAvisos(suscripcion, sesion) {
    await llamarAvisos('alta', sesion, { suscripcion: suscripcion.toJSON() });
    guardarAvisos({ uid: sesion.uid, endpoint: suscripcion.endpoint, en: Date.now() });
  }

  async function suscribirse(registro) {
    const clave = await pedirClaveAvisos();
    let suscripcion = await registro.pushManager.getSubscription();
    if (suscripcion && !mismaClave(suscripcion, clave)) {
      await suscripcion.unsubscribe();
      suscripcion = null;
    }
    return suscripcion || registro.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: clave });
  }

  /** Se apunta (con los recordatorios) si el alta de este dispositivo se ha quedado a medias con el permiso ya dado. */
  function apuntarAltaAMedias(aMedias) {
    if (recordar.activar === aMedias) return;
    recordar.activar = aMedias;
    escribirRecordar();
  }

  /** true si quedan activadas; false si no se ha dado permiso. */
  async function activarAvisos() {
    // Lo primero, el permiso: iPhone y iPad solo lo preguntan mientras dura el toque.
    const permiso = await Notification.requestPermission();
    if (permiso !== 'granted') return false;
    try {
      const [registro, sesion] = await Promise.all([conTiempo(navigator.serviceWorker.ready), sesionDeAvisos()]);
      await darDeAltaAvisos(await suscribirse(registro), sesion);
    } catch (error) {
      // Con el permiso dado ya no hace falta ningún toque: lo que falta (sin conexión, lo más seguro) se reintenta solo.
      apuntarAltaAMedias(true);
      throw error;
    }
    apuntarAltaAMedias(false);
    return true;
  }

  async function desactivarAvisos() {
    const guardado = leerAvisos();
    guardarAvisos(null);
    // Sin cuenta de Google, los recordatorios solo estaban en la función para avisar a este dispositivo: se le pide
    // que los olvide (y entonces se olvida aquí la base: si se vuelven a activar, se le cuentan todos otra vez).
    const olvidar = !leerCuenta() && (recordar.base !== null || recordar.general !== null || Object.keys(recordar.cosas).length > 0);
    clearTimeout(temporizadorRecordatorios);
    if (olvidar) olvidoAplazado = sincronizandoRecordatorios; // Una ronda a medias volvería a dejar allí lo olvidado: al acabar, se olvida otra vez.
    if (recordar.activar) {
      recordar.activar = false;
      escribirRecordar();
    }
    const registro = await navigator.serviceWorker.getRegistration();
    const suscripcion = registro ? await registro.pushManager.getSubscription() : null;
    if (suscripcion) await suscripcion.unsubscribe().catch(() => {});
    // Que la función lo olvide ya (sin esto, lo olvidaría sola la primera vez que el aviso no llegara). Los
    // recordatorios, antes que el dispositivo: mientras aún es de esa sesión.
    const endpoint = (suscripcion && suscripcion.endpoint) || (guardado && guardado.endpoint);
    const olvidados = olvidar ? olvidarEnLaFuncion() : Promise.resolve();
    if (endpoint) olvidados.then(() => sesionDeAvisos()).then((sesion) => llamarAvisos('baja', sesion, { endpoint })).catch(() => {});
  }

  /**
   * Pide a la función de avisos que olvide los recordatorios de esta sesión (si no se puede, sin conexión, no se
   * insiste). La base se olvida aquí solo cuando ella lo ha hecho: si no llegó a enterarse, sigue teniendo lo que la
   * base dice, y al reactivar las notificaciones lo que se haya quitado aquí entretanto se quitará también allí.
   */
  function olvidarEnLaFuncion() {
    return import('./nube.js')
      .then((modulo) => modulo.olvidarRecordatorios(API_AVISOS))
      .then((olvidado) => {
        if (olvidado) guardarBaseDeRecordatorios(null);
      })
      .catch(() => {});
  }

  /** Mientras se activan o desactivan, sus botones (el de Ajustes y el de la nota de Recordar) dicen lo que se está haciendo. */
  function ocuparBotonesDeAvisos(texto) {
    cambiandoAvisos = true;
    for (const boton of [botonAvisos, botonNotaRecordar]) {
      boton.textContent = texto;
      boton.setAttribute('aria-busy', 'true');
    }
  }

  function soltarBotonesDeAvisos() {
    cambiandoAvisos = false;
    for (const boton of [botonAvisos, botonNotaRecordar]) boton.removeAttribute('aria-busy');
    pintarAvisos();
    pintarNotaRecordar();
  }

  async function alPulsarAvisos() {
    if (cambiandoAvisos) return; // Ya se está activando o desactivando (doble toque).
    const activar = estadoAvisos() !== 'activos';
    // La primera vez, suscribirse puede tardar unos segundos (el móvil se da de alta en su servicio de avisos).
    ocuparBotonesDeAvisos(activar ? 'Activando…' : 'Desactivando…');
    try {
      if (!activar) {
        await desactivarAvisos();
        // Con algo pendiente en Recordar, que no pase inadvertido: de eso tampoco se avisará.
        if (hayRecordatoriosPendientes()) avisar('Notificaciones desactivadas: Recordar no te avisará', { duracion: DURACION_DESHACER });
        else avisar('Notificaciones desactivadas');
      } else if (await activarAvisos()) {
        avisar('Notificaciones activadas', { icono: true });
        programarRecordatorios(true); // Ya hay sesión con la que contarle los recordatorios a la función de avisos.
      }
    } catch (error) {
      if (activar) guardarAvisos(null);
      avisar(activar ? 'No se pudieron activar las notificaciones' : 'No se pudieron desactivar las notificaciones');
    } finally {
      soltarBotonesDeAvisos();
    }
  }

  /**
   * El permiso se dio, pero el alta de este dispositivo se quedó a medias (sin conexión, o la función no contestó).
   * Con algo pendiente en Recordar se reintenta sola: al volver la conexión, al volver a la app y al abrirla.
   */
  async function reintentarAvisos() {
    if (!recordar.activar || cambiandoAvisos || !puedeAvisar || window.location.protocol !== 'https:') return;
    if (Notification.permission !== 'granted' || leerAvisos()) {
      apuntarAltaAMedias(false); // Ya no hay nada a medias: se quitó el permiso, o se activaron por otro camino.
      pintarNotaRecordar();
      return;
    }
    if (!hayRecordatoriosPendientes()) return;
    ocuparBotonesDeAvisos('Activando…');
    try {
      await activarAvisos();
    } catch (error) { /* Sigue sin poderse: la próxima vez. */ } finally {
      soltarBotonesDeAvisos();
      programarRecordatorios(true);
    }
  }

  /**
   * Al abrir la app con las notificaciones activadas: que la función siga teniendo este dispositivo. Si el
   * navegador ha cambiado la suscripción, o ha pasado un día, se da de alta otra vez; si se ha quitado el
   * permiso en los ajustes del móvil, quedan desactivadas. Con «forzar», se da de alta sin esperar a nada de eso.
   */
  async function renovarAvisos(forzar = false) {
    const guardado = leerAvisos();
    if (!guardado || !puedeAvisar || window.location.protocol !== 'https:') return;
    if (Notification.permission !== 'granted') {
      guardarAvisos(null);
      pintarAvisos();
      pintarNotaRecordar();
      return;
    }
    try {
      const registro = await conTiempo(navigator.serviceWorker.ready);
      const actual = await registro.pushManager.getSubscription();
      if (!forzar && actual && actual.endpoint === guardado.endpoint && Date.now() - guardado.en < RENOVAR_AVISOS) return;
      const [suscripcion, sesion] = await Promise.all([suscribirse(registro), sesionDeAvisos()]);
      await darDeAltaAvisos(suscripcion, sesion);
    } catch (error) { /* Sin conexión, por ejemplo: la próxima vez. */ }
  }

  // ---------- Recordar: lo que pasa con los avisos ----------

  /*
   * El service worker (sw.js) deja apuntado, en una caché que comparte con la app, lo que pasa con los avisos de
   * Recordar mientras la app no mira: «hechas» (se pulsó «Hecho» en el aviso de una cosa) y «avisadas» (se avisó de
   * una cosa y aún no se ha contestado). La app lo lee al abrirse, al volver a primer plano y cuando él la avisa.
   * Cada cosa, en su propia entrada (así la app y él no se pisan). Él guarda además, para sí, los «Hecho» que aún
   * no ha podido decirle a la función de avisos («por-decir»): la app los mira para saber si ella ya está al tanto de
   * un «Hecho», y los olvida cuando lo está.
   */
  const raizDeLaApp = () => new URL('./', window.location.href);
  const urlDeDato = (tipo, id) => new URL(`__recordar/${tipo}/${encodeURIComponent(id)}`, raizDeLaApp()).href;

  /** La caché de los avisos, si existe (no se crea por mirar): sin almacenamiento, null. */
  async function cacheDeAvisos() {
    try {
      if (!('caches' in window) || !(await window.caches.has(DATOS_DE_AVISOS))) return null;
      return await window.caches.open(DATOS_DE_AVISOS);
    } catch (error) {
      return null;
    }
  }

  /** Lo apuntado de ese tipo («hechas», «avisadas», «por-decir»): [{ id, … }]. */
  async function datosDeAvisos(tipo) {
    const datos = [];
    try {
      const cache = await cacheDeAvisos();
      if (!cache) return datos;
      const prefijo = new URL(`__recordar/${tipo}/`, raizDeLaApp()).pathname;
      for (const peticion of await cache.keys()) {
        if (!new URL(peticion.url).pathname.startsWith(prefijo)) continue;
        const respuesta = await cache.match(peticion);
        const dato = respuesta ? await respuesta.json().catch(() => null) : null;
        if (dato && typeof dato.id === 'string') datos.push(dato);
      }
    } catch (error) { /* Sin poder leerla: como si no hubiera nada. */ }
    return datos;
  }

  async function olvidarDato(tipo, id) {
    try {
      const cache = await cacheDeAvisos();
      if (cache) await cache.delete(urlDeDato(tipo, id));
    } catch (error) { /* Sin caché no había nada apuntado. */ }
  }

  /**
   * Una cosa vuelve a estar pendiente: el «Hecho» que se le dio en un aviso ya no cuenta. Si el service worker aún
   * lo tenía por decir, que no llegue ahora a la función y la vuelva a dar por hecha.
   */
  function olvidarHechoDe(id) {
    olvidarDato('hechas', id);
    olvidarDato('por-decir', id);
  }

  /**
   * Tras una ronda con la función de avisos: de las cosas que ella ya tiene por hechas, el «Hecho» que el service
   * worker aún guardaba por decir sobra. Repetido más adelante, volvería a dar por hecha una cosa que entretanto se
   * hubiera desmarcado en otro dispositivo.
   */
  async function olvidarHechosYaDichos(uid) {
    const cosas = recordar.base && recordar.base.uid === uid && recordar.base.cosas;
    if (!cosas) return;
    for (const dato of await datosDeAvisos('por-decir')) {
      if (dato.uid === uid && cosas[dato.id] && cosas[dato.id].hecha) olvidarDato('por-decir', dato.id);
    }
  }

  /** Quita de la vista el aviso de esa cosa (ya está contestado, o ya no procede) y deja de preguntar por él. */
  function cerrarAvisoDe(id) {
    olvidarDato('avisadas', id);
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
    navigator.serviceWorker.ready
      .then((registro) => (typeof registro.getNotifications === 'function' ? registro.getNotifications({ tag: `recordar-${id}` }) : []))
      .then((avisos) => {
        for (const aviso of avisos) aviso.close();
      })
      .catch(() => {});
  }

  /** «?recordada=ID»: se llega desde el aviso de esa cosa. Se quita de la dirección: recargar no vuelve a preguntar. */
  function recordadaDeLaDireccion() {
    try {
      const parametros = new URLSearchParams(window.location.search);
      const id = parametros.get('recordada');
      if (id === null) return null;
      parametros.delete('recordada');
      const resto = parametros.toString();
      conHistorial(() => window.history.replaceState(window.history.state, '', `${window.location.pathname}${resto ? `?${resto}` : ''}${window.location.hash}`));
      return id;
    } catch (error) {
      return null;
    }
  }

  /** «Hecho» en el aviso de una cosa: queda marcada, como con su check (aunque entretanto haya salido de Recordar). */
  function marcarHechaPorAviso(id) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    if (cosa && !cosa.hecha) ponerHecha(cosa, true);
    olvidarDato('hechas', id);
    cerrarAvisoDe(id); // También si ya estaba hecha o ha salido de Recordar: ese aviso está contestado.
  }

  const HECHO_RECIENTE = 2 * 60 * 1000; // Un «Hecho» de hace menos se acaba de pulsar: nadie ha podido deshacerlo en otro dispositivo.

  /**
   * ¿Se le puede preguntar ahora a la función de avisos cómo ha quedado una cosa? Hace falta conexión, y solo importa
   * con la cuenta de Google: sin ella no hay otro dispositivo que la haya cambiado.
   */
  const puedePreguntarPorHechos = () => window.location.protocol === 'https:' && Boolean(leerCuenta()) && navigator.onLine !== false;

  let revisandoAvisos = false;
  let otraRevision = null; // { tocada }: se pidió revisar mientras ya se revisaba.

  /*
   * Con la cuenta de Google puede llegar aquí el aviso de una cosa que este dispositivo aún no ha traído de la nube (la
   * apuntó otro): no es que la hayan borrado. Lo de ese aviso espera a que la cosa llegue, un día como mucho: lo
   * apuntado en la caché se queda donde está, y «avisoEnEspera» ({ tocada, en }) recuerda que hay algo esperando y,
   * si se tocó uno de esos avisos, de qué cosa era y cuándo fue.
   *
   * Un «Hecho» apuntado, y la pregunta por un aviso («Hecho» o «Mantener»), esperan además a lo que la función de
   * avisos sabe de esa cosa, que llega detrás de ella (con la ronda que pide su llegada): de una cosa nueva aquí manda
   * lo que diga la función, y marcada antes de oírla volvería a quedar pendiente. «hechosPorLlegar» y
   * «avisadasPorLlegar» son las cosas de esos «Hecho» y de esos avisos.
   */
  let avisoEnEspera = null;
  let hechosPorLlegar = new Set();
  let avisadasPorLlegar = new Set();
  const porLlegar = (id) => Boolean(id) && Boolean(leerCuenta()) && !estado.cosas.some((cosa) => cosa.id === id);
  const esReciente = (en) => typeof en === 'number' && Date.now() - en < AVISADA_RECIENTE;
  const enLaBase = (id) => Boolean(recordar.base && recordar.base.cosas) && Object.prototype.hasOwnProperty.call(recordar.base.cosas, id);
  /** Lo de esa cosa espera mientras no llega y, si ya esperaba («esperaban»), mientras no se sepa lo que la función tiene de ella. */
  const esperaASuCosa = (id, esperaban) => porLlegar(id) || (esperaban.has(id) && !enLaBase(id));

  /**
   * Pone al día lo de los avisos: las cosas con «Hecho» pulsado en su aviso quedan marcadas y, si toca, se pregunta
   * en la app («Hecho» o «Mantener») por las avisadas del último día que sigan sin contestar: al tocar un aviso
   * («tocada», la suya, la primera) y, donde los avisos no tienen botones, también al abrir la app o volver a ella.
   *
   * Un «Hecho» se marca aquí sin más si se acaba de pulsar, o si la función de avisos aún no lo sabe (quedó por
   * decir). Uno antiguo que ella ya sabe, de una cosa que aquí sigue pendiente en Recordar, no: con la cuenta de
   * Google, desde entonces ha podido desmarcarse o eliminarse en otro dispositivo, y marcarla ahora lo desharía. Si
   * hay conexión, se le pregunta a ella cómo ha quedado.
   */
  async function revisarAvisos(tocada = null) {
    if (revisandoAvisos) {
      // Al acabar se mira otra vez, sin perder la cosa del aviso tocado.
      otraRevision = { tocada: tocada !== null || !otraRevision ? tocada : otraRevision.tocada };
      return;
    }
    revisandoAvisos = true;
    try {
      const porDecir = new Set((await datosDeAvisos('por-decir')).map((dato) => dato.id));
      const hechosEnEspera = new Set();
      let preguntar = false;
      for (const { id, en } of await datosDeAvisos('hechas')) {
        if (esReciente(en) && esperaASuCosa(id, hechosPorLlegar)) {
          hechosEnEspera.add(id); // Se marcará cuando haya llegado la cosa y la función haya dicho lo suyo.
          continue;
        }
        const antiguo = typeof en !== 'number' || Date.now() - en > HECHO_RECIENTE; // Sin hora: lo apuntó un service worker anterior.
        if (antiguo && !porDecir.has(id) && pendienteEnRecordar(id) && puedePreguntarPorHechos()) {
          olvidarDato('hechas', id);
          cerrarAvisoDe(id);
          preguntar = true;
        } else {
          marcarHechaPorAviso(id);
        }
      }
      hechosPorLlegar = hechosEnEspera;
      if (preguntar) programarRecordatorios(true);
      const avisadas = [];
      const avisadasEnEspera = new Set();
      for (const { id, en } of await datosDeAvisos('avisadas')) {
        if (esReciente(en) && esperaASuCosa(id, avisadasPorLlegar)) avisadasEnEspera.add(id); // Se preguntará cuando llegue y la función diga lo suyo.
        else if (esReciente(en) && pendienteEnRecordar(id)) avisadas.push({ id, en });
        else olvidarDato('avisadas', id); // Ya está hecha, ya no está en Recordar o el aviso es de hace mucho.
      }
      // El aviso tocado: el de ahora o, si no, el que esperaba a su cosa. Si espera aún, se queda esperando; si no, se pregunta por ella.
      const pedida = tocada !== null ? { tocada, en: Date.now() } : avisoEnEspera;
      let tocadaEnEspera = null;
      if (pedida && pedida.tocada !== null && esReciente(pedida.en)) {
        if (esperaASuCosa(pedida.tocada, avisadasPorLlegar)) {
          tocadaEnEspera = pedida;
          avisadasEnEspera.add(pedida.tocada);
        } else {
          tocada = pedida.tocada;
        }
      }
      avisadasPorLlegar = avisadasEnEspera;
      avisoEnEspera = tocadaEnEspera || (hechosEnEspera.size > 0 || avisadasEnEspera.size > 0 ? { tocada: null } : null);
      avisadas.sort((a, b) => b.en - a.en); // La más reciente, antes.
      podarHojaAvisada();
      if (tocada !== null || !avisosConBotones()) {
        const resto = avisadas.map((avisada) => avisada.id).filter((id) => id !== tocada);
        preguntarPorAvisadas(tocada !== null && !tocadaEnEspera ? [tocada, ...resto] : resto, tocada);
      }
    } finally {
      revisandoAvisos = false;
      if (otraRevision) {
        const pedida = otraRevision;
        otraRevision = null;
        revisarAvisos(pedida.tocada);
      }
    }
  }

  /** Llegan cosas de la nube: si lo de algún aviso esperaba a la suya (o se estaba mirando justo ahora, con lo de antes), se mira otra vez. */
  function revisarAvisosEnEspera() {
    if (avisoEnEspera || revisandoAvisos) revisarAvisos();
  }

  /**
   * Acaba bien una ronda con la función de avisos: los «Hecho» que quedaban por decir y ella ya sabe se olvidan, y si
   * lo de algún aviso esperaba a lo que ella dijera de su cosa, se mira otra vez.
   */
  function avisosTrasLaRonda(uid) {
    olvidarHechosYaDichos(uid);
    if (hechosPorLlegar.size > 0 || avisadasPorLlegar.size > 0) revisarAvisos();
  }

  /**
   * Lo que dice el service worker con la app abierta: se ha pulsado «Hecho» en el aviso de una cosa, o se ha tocado un
   * aviso y hay que preguntar por él aquí. El «Hecho» se lee de la caché, donde consta cuándo se pulsó: a una página
   * que estaba dormida el mensaje le llega tarde, y no dice de cuándo es. Sin caché (puede no estar), se marca ya.
   */
  function alMensajeDelServiceWorker(evento) {
    const mensaje = evento.data;
    if (!mensaje || typeof mensaje !== 'object' || typeof mensaje.id !== 'string') return;
    if (mensaje.tipo === 'recordar-hecho') {
      cacheDeAvisos().then((cache) => {
        if (!cache) marcarHechaPorAviso(mensaje.id);
        revisarAvisos();
      });
    } else if (mensaje.tipo === 'recordar-abrir') {
      revisarAvisos(mensaje.id);
    }
  }

  // ---------- Recordar: la función de avisos guarda los recordatorios ----------

  /*
   * Para avisar a su hora, la función de avisos tiene que saber qué cosas de Recordar tienen recordatorio (su texto,
   * si están hechas y cuándo toca). nube.js se lo cuenta con la sesión de las notificaciones de este dispositivo (o
   * la de la cuenta de Google) y trae lo que haya cambiado allí: un «Hecho» pulsado en un aviso, o los recordatorios
   * que se pusieron en otro dispositivo con la misma cuenta. Solo en la app publicada (https) y solo si hay algo de
   * Recordar que contar o que preguntar: sin nada de eso, ni se carga la nube. Solo sube el texto de lo que tiene
   * recordatorio, y la función lo borra cuando deja de tenerlo (o la cosa sale de Recordar, o se elimina).
   */
  let temporizadorRecordatorios = 0;
  let sincronizandoRecordatorios = false;
  let recordatoriosPendientes = false; // Algo ha cambiado mientras se sincronizaba: otra ronda al acabar.
  let leerRecordatorios = false; // La próxima ronda pregunta antes a la función qué tiene.
  let ultimaLecturaRecordatorios = -Infinity;
  let intentosRecordatorios = 0;
  let rondaFallida = false; // La última ronda no llegó a buen fin (sin conexión, lo más seguro): la nota de Recordar lo dice.
  let olvidoAplazado = false; // Se desactivaron las notificaciones (sin cuenta) con una ronda a medias: ver desactivarAvisos.

  /**
   * La zona horaria de este dispositivo, tal como la admite la función de avisos. Un navegador que no reconoce la del
   * sistema dice «Etc/Unknown», y ella rechazaría el envío entero: entonces va la diferencia horaria de ahora en horas
   * enteras («Etc/GMT-2» es UTC+2: sin cambios de hora, pero con el reloj de aquí), o «UTC» si no la hay o no es de
   * horas enteras.
   */
  function zonaHoraria() {
    try {
      const zona = new Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (zona && zona !== 'Etc/Unknown') return zona;
    } catch (error) { /* Sin Intl: la diferencia horaria. */ }
    const horas = -new Date().getTimezoneOffset() / 60;
    return Number.isInteger(horas) && horas !== 0 && horas >= -12 && horas <= 14 ? `Etc/GMT${horas > 0 ? '-' : '+'}${Math.abs(horas)}` : 'UTC';
  }

  /** Lo que la función de avisos tiene que saber: { general, cosas: { id: { texto, hecha, r } } } (hasta donde ella admite). */
  function recordatoriosLocales() {
    const cosas = {};
    let cuantas = 0;
    for (const cosa of recordadas()) { // Las pendientes primero: si no cupieran todas, se quedan fuera las hechas.
      const r = recordatorioDeCosa(cosa.id);
      if (!r || cosa.id.length > MAX_ID_RECORDADA || cuantas >= MAX_RECORDADAS) continue;
      cosas[cosa.id] = { texto: cosa.texto.trim(), hecha: cosa.hecha, r }; // Como lo guarda la función: sin espacios en las puntas.
      cuantas += 1;
    }
    return { general: recordar.general, cosas };
  }

  /** Una firma de eso (y de la zona horaria): si es la misma que la de la última ronda, no hay nada nuevo que contar. */
  function firmaDeRecordatorios() {
    const { general, cosas } = recordatoriosLocales();
    return JSON.stringify([zonaHoraria(), general, Object.keys(cosas).sort().map((id) => [id, cosas[id]])]);
  }

  /** ¿Tiene la función algo de este dispositivo? Lo que dice la base o, si un envío se quedó sin respuesta, quizá lo que llevaba. */
  function hayBaseDeRecordatorios() {
    const { base } = recordar;
    return Boolean(base && (base.general || Object.keys(base.cosas || {}).length > 0 || base.enDuda));
  }

  /** ¿Hay algo de Recordar que la función de avisos aún no sabe? (Lo que lleva la firma de la última ronda, lo sabe.) */
  const recordatoriosSinSubir = () => !recordar.base || recordar.base.firma !== firmaDeRecordatorios();

  /** Pide una ronda con la función de avisos (los cambios seguidos viajan juntos); con «leer», preguntándole antes qué tiene. */
  function programarRecordatorios(leer = false) {
    if (window.location.protocol !== 'https:' || !(leerAvisos() || leerCuenta())) return;
    // Con un envío por el camino la base va sin firma y con lo que lleva (nube.js lo apunta antes de enviar): lo que
    // cambie mientras tanto no se queda sin contar, aunque vuelva a lo que decía la base de antes.
    const hayAlgo = recordar.general !== null || Object.keys(recordar.cosas).length > 0 || hayBaseDeRecordatorios();
    // Sin recordatorios aquí solo hay algo que preguntar si en Recordar hay cosas (pueden tenerlos de otro dispositivo).
    if (!hayAlgo && !(leer && cosasDe(ID_RECORDAR).length > 0)) return;
    if (!leer && !leerRecordatorios && recordar.base && recordar.base.firma === firmaDeRecordatorios()) return; // Nada nuevo.
    leerRecordatorios = leerRecordatorios || leer;
    clearTimeout(temporizadorRecordatorios);
    temporizadorRecordatorios = setTimeout(sincronizarRecordatorios, RETARDO_RECORDATORIOS);
  }

  /** Lo que llega de la función: recordatorios puestos o quitados en otro dispositivo, y cosas hechas (o vueltas a pendientes) allí o en un aviso. */
  function aplicarRecordatorios({ general, cosas }) {
    let cambianCosas = false;
    if (general !== undefined) {
      // Otro para todas (o ninguno): es para lo que se apunte desde ahora. Cada cosa de aquí sigue con el que tenía,
      // salvo que la función diga otra cosa de ella (abajo).
      for (const cosa of cosasDe(ID_RECORDAR)) if (!tieneLoSuyo(cosa.id)) fijarRecordatorioDe(cosa.id);
      recordar.general = general;
    }
    for (const [id, documento] of cosas) {
      if (!documento) {
        quitarRecordatorioDe(id); // Se lo quitaron en otro dispositivo.
        continue;
      }
      const cosa = estado.cosas.find((candidata) => candidata.id === id && candidata.grupo === ID_RECORDAR);
      if (!cosa) continue;
      // Si es el de todas, no hace falta apuntárselo: ya lo sigue.
      if (JSON.stringify(documento.r) === JSON.stringify(recordar.general)) delete recordar.cosas[id];
      else recordar.cosas[id] = documento.r;
      if (cosa.hecha === documento.hecha) continue;
      cosa.hecha = documento.hecha;
      cosa.hechaEn = documento.hecha ? marcaDeTiempo(ultimaHecha()) : null;
      if (documento.hecha) cerrarAvisoDe(id);
      else olvidarHechoDe(id);
      cambianCosas = true;
    }
    escribirRecordar();
    if (cambianCosas) guardarEstado();
    if (cambianCosas) sincronizar(true);
    else repintarRecordatorios();
    actualizarAccesoRecordar();
    podarHojaAvisada();
  }

  function guardarBaseDeRecordatorios(base) {
    recordar.base = base;
    escribirRecordar();
  }

  async function sincronizarRecordatorios() {
    if (sincronizandoRecordatorios) {
      recordatoriosPendientes = true;
      return;
    }
    sincronizandoRecordatorios = true;
    const leer = leerRecordatorios;
    leerRecordatorios = false;
    if (leer) ultimaLecturaRecordatorios = performance.now();
    try {
      const modulo = await import('./nube.js');
      const ronda = await modulo.sincronizarRecordatorios({
        api: API_AVISOS,
        leer,
        zona: zonaHoraria(),
        recordatorio: recordatorioDe,
        local: recordatoriosLocales,
        firma: firmaDeRecordatorios,
        conocidas: () => new Map(cosasDe(ID_RECORDAR).map((cosa) => [cosa.id, cosa.hecha])),
        base: () => recordar.base,
        guardarBase: guardarBaseDeRecordatorios,
        aplicar: aplicarRecordatorios,
      }, intentosRecordatorios);
      // Los avisos de este dispositivo tienen que ir a la sesión que guarda los recordatorios: si se entró con
      // otra cuenta y aún avisa a la de antes, se da de alta con esta.
      const avisos = leerAvisos();
      if (ronda && avisos && avisos.uid !== ronda.uid) renovarAvisos(true);
      rondaFallida = !ronda;
      if (ronda) avisosTrasLaRonda(ronda.uid);
    } catch (error) {
      intentosRecordatorios += 1; // Sin conexión, por ejemplo: la próxima vez.
      leerRecordatorios = leerRecordatorios || leer;
      rondaFallida = true;
    } finally {
      sincronizandoRecordatorios = false;
      if (olvidoAplazado) {
        olvidoAplazado = false;
        olvidarEnLaFuncion();
      }
      pintarNotaRecordar(); // Si no se han podido subir, que se sepa; y si ya están arriba, que deje de decirlo.
      if (recordatoriosPendientes) {
        recordatoriosPendientes = false;
        programarRecordatorios(leerRecordatorios); // También si lo que se pidió mientras tanto fue preguntar.
      }
    }
  }

  /**
   * Al volver a la app: lo que haya pasado con los avisos y, como mucho una vez por minuto (o ya, si la última ronda
   * falló), lo que diga la función. El permiso ha podido cambiar en los ajustes del móvil: la nota de Recordar se repinta.
   */
  function alVolverALaApp() {
    if (document.hidden) return;
    revisarAvisos();
    reintentarAvisos();
    if (rondaFallida || performance.now() - ultimaLecturaRecordatorios > ENTRE_LECTURAS) programarRecordatorios(true);
    pintarNotaRecordar();
  }

  /** Vuelve la conexión: lo que se quedó sin hacer por falta de ella (el alta de este dispositivo, subir los recordatorios). */
  function alVolverLaConexion() {
    reintentarAvisos();
    programarRecordatorios(true);
    pintarNotaRecordar();
  }

  // ---------- Teclado en pantalla ----------

  const esCampoDeTexto = (elemento) =>
    Boolean(elemento && elemento.matches && elemento.matches('input:not([type="radio"]):not([type="color"])'));

  /** iOS no encoge la página con el teclado: se mide con visualViewport y se suben las barras. */
  function medirTeclado() {
    const vv = window.visualViewport;
    if (!vv || Math.abs(vv.scale - 1) > 0.01) return; // Con zoom de pellizco no se toca nada.
    const tapado = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    raiz.style.setProperty('--teclado', `${tapado}px`);
    raiz.style.setProperty('--vv-arriba', `${Math.max(0, Math.round(vv.offsetTop))}px`);
    // «Teclado abierto» se decide por su altura real: iOS desplaza la ventana visual al
    // enfocar y entonces lo tapado (que es lo que sube la barra) puede quedarse en casi nada.
    raiz.toggleAttribute('data-teclado', Math.round(window.innerHeight - vv.height) > UMBRAL_TECLADO);
  }

  /** Al cerrarse el teclado, la página no debe quedarse desplazada. */
  function alPerderFoco(evento) {
    if (!esCampoDeTexto(evento.target)) return;
    setTimeout(() => {
      if (esCampoDeTexto(document.activeElement)) return;
      window.scrollTo(0, 0);
      medirTeclado();
    }, 120);
  }

  function iniciarTeclado() {
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', medirTeclado);
      window.visualViewport.addEventListener('scroll', medirTeclado);
      medirTeclado();
    }
    document.addEventListener('focusout', alPerderFoco);
  }

  // ---------- Sincronización y ciclo de vida ----------

  /** Vuelve a leer el almacenamiento (otra pestaña lo ha cambiado) y repinta. */
  function recargarEstado() {
    estado = leerEstado();
    recordar = leerRecordar();
    consolidarRescate();
    if (nube) nube.cambio();
    if (accionAviso) ocultarAviso();
    aplicarColor(estado.ajustes.colorFondo);
    actualizarTituloLista();
    if (vistaActual === 'lista') pintarLista();
    if (vistaActual === 'ajustes') sincronizarAjustes();
    if (vistaActual === 'grupo') {
      if (grupoMostrado()) pintarGrupo();
      else irALaLista(); // El grupo ya no existe (borrado desde otra pestaña).
    }
    actualizarAccesoRecordar();
  }

  function alCambiarAlmacenamiento(evento) {
    if (evento.key === null || evento.key === CLAVE || evento.key === CLAVE_RECORDAR) recargarEstado();
    if (evento.key === null || evento.key === CLAVE_CUENTA) {
      pintarSesion();
      cargarNube(); // Se acaba de entrar con Google en otra pestaña.
    }
    if (evento.key === null || evento.key === CLAVE_LISTAS) cargarNovedades(); // La primera lista, en otra pestaña.
    if (evento.key === null || evento.key === CLAVE_AVISOS) {
      pintarAvisos();
      pintarNotaRecordar();
    }
    // Otra sesión (o la primera) con la que contarle los recordatorios a la función de avisos.
    if (evento.key === null || evento.key === CLAVE_CUENTA || evento.key === CLAVE_AVISOS) programarRecordatorios(true);
  }

  function registrarServiceWorker() {
    if (!('serviceWorker' in navigator) || !/^https?:$/.test(window.location.protocol)) return;
    const registrar = () => navigator.serviceWorker.register('./sw.js').catch(() => {});
    if (document.readyState === 'complete') registrar();
    else window.addEventListener('load', registrar, { once: true });
  }

  /** Cosas con, Cosas de y Tu cuenta son de este dominio: Netlify las trae de cosas.info
   *  (netlify.toml), siempre por https. Sin ese proxy (file://, un servidor local) van a la web
   *  que dice data-web en index.html. */
  function rutaDeCosasInfo(ruta) {
    const accesos = document.getElementById('accesos');
    if (window.location.protocol === 'https:' || !accesos) return ruta;
    return accesos.dataset.web + ruta;
  }

  function prepararAccesos() {
    for (const acceso of document.querySelectorAll('#accesos a')) {
      acceso.href = rutaDeCosasInfo(acceso.getAttribute('href'));
    }
  }

  // ---------- Arranque ----------

  function iniciar() {
    consolidarRescate();
    aplicarColor(estado.ajustes.colorFondo);
    prepararAccesos();
    pintarSesion();
    construirMuestras(muestras, 'color-fondo', PALETA);
    ayudaDictado.hidden = !Reconocimiento;
    pintarInstalacion();
    pintarAvisos();
    construirSelectores();
    olvidarRecordatoriosSinCosa();
    actualizarAccesoRecordar();
    const recordada = recordadaDeLaDireccion(); // Antes de mirar la dirección para navegar.

    abridores.lista.addEventListener('click', () => abrirVista('lista', abridores.lista));
    abridores.ajustes.addEventListener('click', () => abrirVista('ajustes', abridores.ajustes));
    for (const boton of document.querySelectorAll('[data-volver]')) boton.addEventListener('click', volver);
    for (const nombre of Object.keys(AL_DESLIZAR)) {
      vistas[nombre].addEventListener('touchstart', alEmpezarTiron, { passive: true });
      vistas[nombre].addEventListener('touchmove', alMoverTiron, { passive: true });
      vistas[nombre].addEventListener('touchend', alSoltarTiron, { passive: true });
      vistas[nombre].addEventListener('touchcancel', soltarTiron, { passive: true });
    }

    for (const barra of escrituras) {
      barra.micro.hidden = !Reconocimiento;
      actualizarBarra(barra);
      barra.formulario.addEventListener('submit', (evento) => alEnviar(evento, barra));
      barra.campo.addEventListener('input', () => alEscribir(barra));
      barra.micro.addEventListener('click', () => alPulsarMicro(barra));
    }
    listaEl.addEventListener('click', alPulsarLista);
    listaEl.addEventListener('keydown', alTeclearEnLista);
    accesoRecordar.addEventListener('click', alPulsarRecordar);
    toast.addEventListener('click', alPulsarAviso);
    toast.addEventListener('focusin', pausarAviso);
    toast.addEventListener('pointerenter', pausarAviso);
    toast.addEventListener('focusout', () => setTimeout(reanudarAviso, 0)); // El foco nuevo aún no está puesto.
    toast.addEventListener('pointerleave', reanudarAviso);
    toast.addEventListener('pointercancel', reanudarAviso);

    botonCrearGrupo.addEventListener('click', empezarEdicionGrupo);
    formularioGrupo.addEventListener('submit', alCrearGrupo);
    campoGrupo.addEventListener('input', actualizarBarraGrupo);
    campoGrupo.addEventListener('keydown', alTeclearEnCampoGrupo);
    campoGrupo.addEventListener('blur', alSalirDeCampoGrupo);

    botonEditarGrupo.addEventListener('click', abrirHojaGrupo);
    botonAnadir.addEventListener('click', alternarCandidatas);
    vistas.grupo.addEventListener('click', alPulsarEnGrupo); // Cuerpo y desplegable (que vive en la barra).
    vistas.grupo.addEventListener('keydown', alTeclearEnLista);
    document.addEventListener('keydown', alTeclearEnGrupo); // En el documento: Escape cierra el desplegable aunque el foco esté en body.
    formularioEdicion.addEventListener('submit', guardarEdicionGrupo);
    botonCerrarHojaGrupo.addEventListener('click', alPulsarCierreDeHoja);
    veloHojaGrupo.addEventListener('click', alPulsarCierreDeHoja);

    botonConfigurar.addEventListener('click', () => abrirHojaRecordar());
    botonNotaRecordar.addEventListener('click', alPulsarNotaRecordar);
    formularioRecordar.addEventListener('submit', guardarHojaRecordar);
    formularioRecordar.addEventListener('change', alCambiarLaHoja);
    botonQuitarRecordar.addEventListener('click', quitarDesdeLaHoja);
    botonCerrarHojaRecordar.addEventListener('click', alPulsarCierreDeHoja);
    veloHojaRecordar.addEventListener('click', alPulsarCierreDeHoja);
    listaAvisadas.addEventListener('click', alResponderAvisada);
    botonCerrarHojaAvisada.addEventListener('click', alPulsarCierreDeHoja);
    veloHojaAvisada.addEventListener('click', alPulsarCierreDeHoja);
    if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', alMensajeDelServiceWorker);

    muestras.addEventListener('change', (evento) => cambiarColor(evento.target.value));
    selectorColor.addEventListener('input', () => cambiarColor(selectorColor.value));
    selectorColor.addEventListener('change', () => cambiarColor(selectorColor.value));
    formularioDatos.addEventListener('submit', (evento) => evento.preventDefault());
    campoNombre.addEventListener('keydown', alPulsarTeclaEnDatos);
    campoNombre.addEventListener('input', programarGuardadoDatos);
    campoNombre.addEventListener('change', guardarDatosPersonales);
    campoNombre.addEventListener('blur', guardarDatosPersonales);
    botonInstalar.addEventListener('click', alPulsarInstalar);
    botonCompartir.addEventListener('click', alPulsarCompartir);
    botonAvisos.addEventListener('click', alPulsarAvisos);
    botonCerrarHoja.addEventListener('click', alPulsarCierreDeHoja);
    veloHoja.addEventListener('click', alPulsarCierreDeHoja);
    window.addEventListener('beforeinstallprompt', alPoderInstalar);
    window.addEventListener('appinstalled', marcarInstalada);

    window.addEventListener('storage', alCambiarAlmacenamiento);
    window.addEventListener('online', cargarNube);
    window.addEventListener('online', cargarNovedades);
    window.addEventListener('online', alVolverLaConexion);
    window.addEventListener('offline', pintarNotaRecordar);
    document.addEventListener('visibilitychange', alVolverALaApp);
    window.addEventListener('pageshow', (evento) => { if (evento.persisted) recargarEstado(); });
    window.addEventListener('pagehide', guardarDatosPersonales);
    window.addEventListener('pagehide', cancelarDictado);
    document.addEventListener('visibilitychange', () => { if (document.hidden) cancelarDictado(); });
    // iOS solo aplica :active al tocar si existe algún oyente táctil (pasivo: no frena nada).
    document.addEventListener('touchstart', () => {}, { passive: true });
    // El anillo de foco, solo con el teclado (styles.css): se apaga al tocar y vuelve con la primera tecla
    // que no sea escribir en un campo.
    document.addEventListener('pointerdown', () => raiz.setAttribute('data-puntero', ''), { capture: true, passive: true });
    document.addEventListener('keydown', (evento) => {
      if (evento.key === 'Tab' || !esCampoDeTexto(evento.target)) raiz.removeAttribute('data-puntero');
    }, true);

    iniciarTeclado();
    iniciarNavegacion();
    registrarServiceWorker();
    cargarNube();
    cargarNovedades();
    renovarAvisos();
    reintentarAvisos();
    revisarAvisos(recordada);
    programarRecordatorios(true);
  }

  iniciar();
})();
