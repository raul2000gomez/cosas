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
  const zonaLista = $('#zona-lista');
  const listaEl = $('#lista-cosas');
  const vacio = $('#vacio');
  const formularioGrupo = $('#formulario-grupo');
  const botonCrearGrupo = $('#crear-grupo');
  const campoGrupo = $('#campo-grupo');
  const botonConfirmarGrupo = $('#confirmar-grupo');
  const botonEditarGrupo = $('#editar-grupo');
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
  const toast = $('#toast');
  const plantillaFila = $('#plantilla-fila');
  const plantillaGrupo = $('#plantilla-grupo');
  const plantillaMiembro = $('#plantilla-miembro');
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
        .sort(porFecha);
    }
    if (Array.isArray(bruto.cosas)) {
      const idsVistos = new Set();
      const idsGrupos = new Set(estadoNuevo.grupos.map((grupo) => grupo.id));
      estadoNuevo.cosas = bruto.cosas
        .map((bruta) => normalizarCosa(bruta, idsVistos))
        .filter(Boolean)
        .sort(porFecha);
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

  const grupoPorId = (id) => estado.grupos.find((grupo) => grupo.id === id) || null;

  const cosasDe = (grupo) => estado.cosas.filter((cosa) => cosa.grupo === grupo);

  // ---------- Aviso (toast) ----------

  let temporizadorAviso = 0;
  let temporizadorVaciado = 0;
  let accionAviso = null;

  function avisar(texto, opciones = {}) {
    const { icono = false, accion = null, duracion = DURACION_AVISO } = opciones;
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
    if (nombre === 'lista' && destino && destino !== titulos.lista) asegurarVisibleEnLista(destino);
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

    estado.cosas.unshift({ id: nuevoId(), texto, hecha: false, creada: marcaDeTiempo(ultimaCreacion()), hechaEn: null, grupo: grupo ? grupo.id : null });
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
    (vecina ? vecina.querySelector('.borrar') : respaldo).focus({ preventScroll: true });
  }

  function alternarHecha(id) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    if (!cosa || !filasPorId.has(id)) return;
    cosa.hecha = !cosa.hecha;
    cosa.hechaEn = cosa.hecha ? marcaDeTiempo(ultimaHecha()) : null;
    if (!guardarEstado()) avisarFallo();
    sincronizar(true); // La hecha baja al final (o la pendiente vuelve a su sitio) deslizándose.
  }

  /** Intro mantenido sobre un botón de una lista no repite la pulsación fila tras fila. */
  function alTeclearEnLista(evento) {
    if (evento.repeat && evento.key === 'Enter' && evento.target.closest('button')) evento.preventDefault();
  }

  // ---------- Lista ----------

  const ultimosOjos = new WeakMap(); // Último toque en el ojo de cada fila de grupo.
  const temporizadoresPliegue = new WeakMap();

  function actualizarVacio() {
    vacio.hidden = estado.cosas.length > 0 || estado.grupos.length > 0;
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
    const fila = filasDeGrupo.get(id);
    return fila && fila.isConnected ? fila.querySelector('.enlace-grupo') : null;
  };

  /** Mueve, crea y retira filas para que la lista (grupos incluidos) refleje el estado. */
  function sincronizarLista(nuevas = true) {
    const nivel = ordenar([...estado.grupos, ...cosasDe(null)]);
    colocar(listaEl, nivel.map((elemento) => (esGrupo(elemento)
      ? filaDeGrupo(elemento, nuevas)
      : filaDeCosa(elemento, listaEl, plantillaFila, nuevas))));
    for (const grupo of estado.grupos) {
      const fila = filasDeGrupo.get(grupo.id);
      const anidada = fila.querySelector('.anidada');
      const miembros = ordenar(cosasDe(grupo.id));
      colocar(anidada, miembros.map((cosa) => filaDeCosa(cosa, anidada, plantillaFila, nuevas)));
      fila.querySelector('.grupo-vacio').hidden = miembros.length > 0;
    }
    podarFilas();
    actualizarVacio();
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
    filasPorId.delete(id);
    const grupo = fila.parentElement.closest('li');
    enfocarVecina(fila, grupo ? grupo.querySelector('.ojo') : titulos.lista);
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

  /** Devuelve a su grupo una cosa quitada de él (si sigue suelta y el grupo existe). */
  function devolverAlGrupo(id, grupo) {
    const cosa = estado.cosas.find((candidata) => candidata.id === id);
    if (!cosa || cosa.grupo !== null || !grupoPorId(grupo)) return false;
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
    colocar(listaGrupo, miembros.map((cosa) => filaDeCosa(cosa, listaGrupo, plantillaMiembro, nuevas)));
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
    eliminados.push({ quitada: id, deGrupo: grupo });
    avisar('Quitado del grupo', {
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
    // Al añadir o quitar, la fila vecina sube y su botón queda bajo el dedo: el segundo toque no se la lleva.
    if (ahora - ultimoMovimiento < GUARDA_DOBLE_TOQUE) return;
    if (accion === 'anadir') moverCosa(fila.dataset.id, grupoEnPagina, botonAnadir);
    else if (accion === 'quitar') quitarDelGrupo(fila.dataset.id);
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
      ayudaSesion.textContent = 'Inicia sesión con Google para tener tus cosas, tus grupos y tus listas de Cosas con y Cosas de en todos tus dispositivos. Sin sesión, todo se queda en este dispositivo.';
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
    estado = normalizarEstado(bruto);
    guardarEstado();
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

  let panelAbierto = null; // Como mucho hay una hoja abierta: la de pasos o la de edición.
  let aperturaHoja = -Infinity;

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
    titulo.focus({ preventScroll: true });
  }

  function cerrarPanel(panel) {
    panel.hidden = true;
    panelAbierto = null;
    principal.removeAttribute('inert');
    principal.removeAttribute('aria-hidden');
    document.removeEventListener('keydown', alTeclearConHoja);
  }

  function cerrarHojaAbierta() {
    if (panelAbierto === hoja) cerrarHoja();
    else if (panelAbierto === hojaGrupo) cerrarHojaGrupo();
  }

  function alTeclearConHoja(evento) {
    if (evento.key === 'Escape') {
      cerrarHojaAbierta();
      return;
    }
    if (evento.key !== 'Tab' || !panelAbierto) return;
    // El tabulador da vueltas dentro de la hoja y no sale a la página.
    const focables = Array.from(panelAbierto.querySelectorAll('button, input'))
      .filter((elemento) => !elemento.disabled && elemento.offsetParent !== null);
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
    // El velo y «Cerrar» aparecen bajo el dedo que abrió la hoja: el segundo toque de un doble toque no la cierra.
    if (performance.now() - aperturaHoja >= GUARDA_DOBLE_TOQUE) cerrarHojaAbierta();
  }

  // ---------- Aplicación (añadir a la pantalla de inicio) ----------

  const enModoAplicacion = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  // iPadOS se presenta como un Mac: lo delata la pantalla táctil.
  const esIOS = /iPhone|iPad|iPod/.test(window.navigator.userAgent)
    || (/Macintosh/.test(window.navigator.userAgent) && window.navigator.maxTouchPoints > 1);
  let eventoInstalar = null; // El «beforeinstallprompt» retenido: con él se abre el diálogo del sistema.
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
  function abrirHoja() {
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
    if (devolverFoco) botonInstalar.focus({ preventScroll: true });
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
  }

  function alCambiarAlmacenamiento(evento) {
    if (evento.key === null || evento.key === CLAVE) recargarEstado();
    if (evento.key === null || evento.key === CLAVE_CUENTA) {
      pintarSesion();
      cargarNube(); // Se acaba de entrar con Google en otra pestaña.
    }
    if (evento.key === null || evento.key === CLAVE_LISTAS) cargarNovedades(); // La primera lista, en otra pestaña.
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
    botonCerrarHoja.addEventListener('click', alPulsarCierreDeHoja);
    veloHoja.addEventListener('click', alPulsarCierreDeHoja);
    window.addEventListener('beforeinstallprompt', alPoderInstalar);
    window.addEventListener('appinstalled', marcarInstalada);

    window.addEventListener('storage', alCambiarAlmacenamiento);
    window.addEventListener('online', cargarNube);
    window.addEventListener('online', cargarNovedades);
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
  }

  iniciar();
})();
