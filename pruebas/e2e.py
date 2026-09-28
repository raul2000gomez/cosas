# -*- coding: utf-8 -*-
"""Pruebas de extremo a extremo de «Cosas» (Playwright + Chromium).

Cada prueba se ejecuta en un contexto nuevo y en los dos móviles emulados
(«iPhone 13» y «Pixel 7»). El servidor HTTP vive dentro de este mismo proceso
(hilo «daemon», puerto libre) y muere con el script.

Uso (PowerShell):

    $env:PYTHONIOENCODING = "utf-8"
    & "C:/Users/Raul/AppData/Local/Programs/Python/Python312/python.exe" `
        "C:/Users/Raul/Desktop/Dropshipping/Cosas/pruebas/e2e.py" [--capturas DIR]
        [--filtro TEXTO] [--dispositivo "iPhone 13"] [--detalle]

Sale con código distinto de cero si alguna prueba falla.
"""
import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import TimeoutError as TiempoAgotado
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"
NODE = Path(shutil.which("node") or "C:/Program Files/nodejs/node.exe")
DISPOSITIVOS = ["iPhone 13", "Pixel 7"]
CLAVE = "cosas:v1"
CLAVE_RESPALDO = "cosas:grupos"  # Respaldo de grupos, pertenencia y hechaEn (1.2): lo que una 1.1 aún abierta no sabe guardar.
COLOR_DEFECTO = "#2F6FED"
VERDE = (34, 197, 94)
PALETA = [
    ("Azul", "#2F6FED"), ("Azul noche", "#1E2A44"), ("Turquesa", "#1BA39C"),
    ("Verde", "#2E8B57"), ("Amarillo", "#F2B705"), ("Naranja", "#F2711C"),
    ("Coral", "#EF5B5B"), ("Rosa", "#E85D9E"), ("Morado", "#7A4FD6"),
    ("Arena", "#EADFCB"), ("Blanco", "#FFFFFF"), ("Negro", "#111111"),
]
# Lo único que la app dice sobre la privacidad del dictado: el audio puede salir del móvil (Google en Android, Apple en iOS).
AYUDA_DICTADO = "El dictado usa el servicio de voz de tu móvil, que puede enviar el audio a Google o Apple."
AYUDA_SESION = ("Inicia sesión con Google para tener tus cosas, tus grupos y tus listas de Cosas con y Cosas de en todos tus dispositivos. "
                "Sin sesión, todo se queda en este dispositivo.")
CLAVE_CUENTA = "cosascon:cuenta"  # La cuenta de Google con la que se entró en Cosas con / Cosas de / Tu cuenta.
DETALLE = False


# ----------------------------------------------------------------------------
# Utilidades generales
# ----------------------------------------------------------------------------

class Fallo(AssertionError):
    """Una comprobación de la especificación no se cumple."""


def comprobar(condicion, mensaje):
    if not condicion:
        raise Fallo(mensaje)


def igual(obtenido, esperado, que):
    if obtenido != esperado:
        raise Fallo(f"{que}: esperado {esperado!r}, obtenido {obtenido!r}")


def hex_a_rgb(valor):
    valor = valor.lstrip("#")
    return tuple(int(valor[i:i + 2], 16) for i in (0, 2, 4))


def rgb_css(valor):
    """'rgb(1, 2, 3)' o 'rgba(1, 2, 3, .5)' -> (1, 2, 3)."""
    numeros = re.findall(r"[\d.]+", valor)
    return tuple(int(round(float(n))) for n in numeros[:3])


def detalle(texto):
    if DETALLE:
        print(f"        · {texto}")


def decodificar_png(datos):
    """Decodificador PNG mínimo (8 bits, RGB/RGBA, sin entrelazado)."""
    comprobar(datos[:8] == b"\x89PNG\r\n\x1a\n", "La captura no es un PNG")
    posicion, comprimido = 8, b""
    ancho = alto = canales = 0
    while posicion < len(datos):
        largo = struct.unpack(">I", datos[posicion:posicion + 4])[0]
        tipo = datos[posicion + 4:posicion + 8]
        cuerpo = datos[posicion + 8:posicion + 8 + largo]
        if tipo == b"IHDR":
            ancho, alto, bits, color, _, _, entrelazado = struct.unpack(">IIBBBBB", cuerpo)
            comprobar(bits == 8 and color in (2, 6) and entrelazado == 0, "PNG no soportado")
            canales = 3 if color == 2 else 4
        elif tipo == b"IDAT":
            comprimido += cuerpo
        posicion += 12 + largo
    bruto = zlib.decompress(comprimido)
    largo_fila = ancho * canales
    filas, previa, posicion = [], bytearray(largo_fila), 0
    for _ in range(alto):
        filtro = bruto[posicion]
        linea = bytearray(bruto[posicion + 1:posicion + 1 + largo_fila])
        posicion += 1 + largo_fila
        for i in range(largo_fila):
            izquierda = linea[i - canales] if i >= canales else 0
            arriba = previa[i]
            esquina = previa[i - canales] if i >= canales else 0
            if filtro == 1:
                suma = izquierda
            elif filtro == 2:
                suma = arriba
            elif filtro == 3:
                suma = (izquierda + arriba) >> 1
            elif filtro == 4:
                p = izquierda + arriba - esquina
                pa, pb, pc = abs(p - izquierda), abs(p - arriba), abs(p - esquina)
                suma = izquierda if pa <= pb and pa <= pc else (arriba if pb <= pc else esquina)
            else:
                suma = 0
            linea[i] = (linea[i] + suma) & 255
        filas.append(linea)
        previa = linea

    def pixel(x, y):
        inicio = x * canales
        return tuple(filas[y][inicio:inicio + 3])

    return ancho, alto, pixel


def dimensiones_png(datos):
    comprobar(datos[:8] == b"\x89PNG\r\n\x1a\n" and datos[12:16] == b"IHDR", "No es un PNG válido")
    return struct.unpack(">II", datos[16:24])


# ----------------------------------------------------------------------------
# Servidor HTTP dentro del proceso
# ----------------------------------------------------------------------------

class Manejador(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".png": "image/png",
        ".webmanifest": "application/manifest+json",
    }

    def log_message(self, *args):  # Sin ruido en la consola.
        pass

    def do_GET(self):
        # Simula el alojamiento: Netlify sirve sus propios scripts bajo /.netlify/.
        if self.path.startswith("/.netlify/"):
            cuerpo = b"/* script del alojamiento */"
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # Conexiones cortadas por el navegador.
        pass


def iniciar_servidor(directorio):
    servidor = Servidor(("127.0.0.1", 0), partial(Manejador, directory=str(directorio)))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


def url_de(servidor, ruta=""):
    return f"http://127.0.0.1:{servidor.server_address[1]}/{ruta}"


# ----------------------------------------------------------------------------
# Entorno de cada prueba
# ----------------------------------------------------------------------------

class Entorno:
    """Contextos y páginas de una prueba, con vigilancia de consola y red."""

    def __init__(self, pw, navegador, dispositivo, base, nombre_prueba, incidencias):
        self.pw = pw
        self.navegador = navegador
        self.dispositivo = dispositivo
        self.descriptor = {k: v for k, v in pw.devices[dispositivo].items() if k != "default_browser_type"}
        self.base = base
        self.nombre_prueba = nombre_prueba
        self.incidencias = incidencias
        self.tolerar = []  # Patrones de incidencias esperadas por la propia prueba.
        self.origenes_propios = [base]
        self._contextos = []

    @property
    def ancho(self):
        return self.descriptor["viewport"]["width"]

    @property
    def alto(self):
        return self.descriptor["viewport"]["height"]

    def contexto(self, **extra):
        opciones = dict(self.descriptor)
        opciones.update({"locale": "es-ES"})
        opciones.update(extra)
        contexto = self.navegador.new_context(**opciones)
        contexto.set_default_timeout(8000)
        self._contextos.append(contexto)
        return contexto

    def _anotar(self, tipo, texto):
        if any(re.search(patron, texto) for patron in self.tolerar):
            return
        self.incidencias.append((self.dispositivo, self.nombre_prueba, tipo, texto))

    def vigilar(self, pagina):
        def al_mensaje(mensaje):
            if mensaje.type == "error":
                self._anotar("console.error", mensaje.text)
            else:
                self._anotar(f"console.{mensaje.type}", mensaje.text)

        def al_pedir(peticion):
            url = peticion.url
            if url.startswith(("data:", "blob:", "about:", "file:")):
                return
            if not any(url.startswith(origen) for origen in self.origenes_propios):
                self._anotar("petición externa", url)

        pagina.on("console", al_mensaje)
        pagina.on("pageerror", lambda error: self._anotar("pageerror", str(error)))
        pagina.on("requestfailed", lambda pet: self._anotar("requestfailed", f"{pet.url} ({pet.failure})"))
        pagina.on("response", lambda r: r.status >= 400 and self._anotar("respuesta HTTP", f"{r.status} {r.url}"))
        pagina.on("request", al_pedir)
        return pagina

    def pagina(self, ruta="", contexto=None, url=None):
        contexto = contexto or self.contexto()
        pagina = self.vigilar(contexto.new_page())
        pagina.goto(url or (self.base + ruta))
        return pagina

    def cerrar(self):
        for contexto in self._contextos:
            try:
                contexto.close()
            except Exception:
                pass
        self._contextos = []


PRUEBAS = []


def prueba(nombre, una_vez=False):
    def decorar(funcion):
        PRUEBAS.append((nombre, funcion, una_vez))
        return funcion
    return decorar


# ----------------------------------------------------------------------------
# Ayudas de página
# ----------------------------------------------------------------------------

JS_UTIL = r"""
  const parsear = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c);
    if (!m) return [0, 0, 0, c === 'transparent' ? 0 : 1];
    const p = m[1].split(/[,\/\s]+/).filter(Boolean).map(parseFloat);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const mezclar = (abajo, arriba) => [0, 1, 2].map((i) => arriba[i] * arriba[3] + abajo[i] * (1 - arriba[3])).concat(1);
  const fondoEfectivo = (el) => {
    const cadena = [];
    for (let n = el; n; n = n.parentElement) cadena.unshift(n);
    let base = [255, 255, 255, 1];
    for (const n of cadena) base = mezclar(base, parsear(getComputedStyle(n).backgroundColor));
    return base;
  };
  const luminancia = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const contraste = (a, b) => {
    const la = luminancia(a) + 0.05, lb = luminancia(b) + 0.05;
    return la > lb ? la / lb : lb / la;
  };
  const contrasteTexto = (el, pseudo) => {
    const fondo = fondoEfectivo(el);
    const tinta = mezclar(fondo, parsear(getComputedStyle(el, pseudo || null).color));
    return contraste(tinta, fondo);
  };
  const contrasteIcono = (svg) => {
    const fondo = fondoEfectivo(svg);
    const cs = getComputedStyle(svg);
    const trazo = /^rgb/.test(cs.stroke) ? cs.stroke : cs.color;
    return contraste(mezclar(fondo, parsear(trazo)), fondo);
  };
  const redondear = (c) => c.slice(0, 3).map((v) => Math.round(v));
"""

JS_TOAST = r"""
() => {
  const t = document.querySelector('[role="status"]');
  if (!t) return null;
  const cs = getComputedStyle(t);
  const r = t.getBoundingClientRect();
  const copia = t.cloneNode(true);
  copia.querySelectorAll('button').forEach((b) => b.remove());
  const boton = t.querySelector('button');
  const rb = boton ? boton.getBoundingClientRect() : null;
  return {
    texto: copia.textContent.trim(),
    boton: boton ? boton.textContent.trim() : null,
    altoBoton: rb ? rb.height : 0,
    anchoBoton: rb ? rb.width : 0,
    icono: Boolean(t.querySelector('svg')),
    opacidad: parseFloat(cs.opacity),
    visible: parseFloat(cs.opacity) > 0.9 && r.width > 0 && r.height > 0
      && cs.visibility !== 'hidden' && cs.display !== 'none',
    x: r.left, y: r.top, ancho: r.width, alto: r.height,
    ariaLive: t.getAttribute('aria-live'),
  };
}
"""


def esperar(pagina, expresion, arg=None, ms=4000, que=None):
    try:
        pagina.wait_for_function(expresion, arg=arg, timeout=ms)
    except TiempoAgotado:
        raise Fallo(f"No se cumplió en {ms} ms: {que or expresion}") from None


def leer_estado(pagina):
    return pagina.evaluate("(clave) => JSON.parse(localStorage.getItem(clave))", CLAVE)


def cosa(numero, texto, hecha=False, grupo=None, hecha_en=None):
    """Una cosa tal como la guardaba la versión 1 (sin hechaEn ni grupo) o, con grupo/hecha_en, la versión 2."""
    datos = {"id": f"id-{numero}", "texto": texto, "hecha": hecha, "creada": 1_700_000_000_000 + numero}
    if grupo is not None:
        datos["grupo"] = grupo
    if hecha_en is not None:
        datos["hechaEn"] = hecha_en
    return datos


def grupo(numero, nombre, color=None, abierto=False, id=None):
    return {"id": id or f"g-{numero}", "nombre": nombre, "color": color, "creada": 1_700_000_000_000 + numero, "abierto": abierto}


def estado_con(cosas=(), color=COLOR_DEFECTO, nombre="", correo="", grupos=None):
    """Sin «grupos», un estado de la versión 1 (el que dejó la app 1.x): así cada prueba pasa por la migración."""
    datos = {"version": 1, "cosas": list(cosas), "ajustes": {"colorFondo": color, "nombre": nombre, "correo": correo}}
    if grupos is not None:
        datos.update({"version": 2, "grupos": list(grupos)})
    return datos


def orden_visible(cosas):
    """El orden en que la app enseña esas cosas: pendientes de más nueva a más antigua y, al final,
    las hechas por el momento en que se marcaron (su creación si vienen de la versión 1)."""
    pendientes = sorted((c for c in cosas if not c["hecha"]), key=lambda c: -c["creada"])
    hechas = sorted((c for c in cosas if c["hecha"]), key=lambda c: c.get("hechaEn") or c["creada"])
    return [c["texto"] for c in pendientes + hechas]


def sembrar(e, datos, ruta="", contexto=None):
    """Deja datos en localStorage ANTES de que arranque la app (desde una imagen del mismo origen)."""
    contexto = contexto or e.contexto()
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(e.base + "icons/favicon-32.png")
    bruto = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
    pagina.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE, bruto])
    pagina.goto(e.base + ruta)
    return pagina


def toast(pagina):
    return pagina.evaluate(JS_TOAST)


def esperar_toast(pagina, texto, ms=3000):
    esperar(pagina, f"(texto) => {{ const t = ({JS_TOAST})(); return t && t.visible && t.texto === texto; }}",
            arg=texto, ms=ms, que=f"aparece el aviso «{texto}»")
    return toast(pagina)


def esperar_sin_toast(pagina, ms=4000):
    esperar(pagina, f"() => {{ const t = ({JS_TOAST})(); return !t || t.opacidad < 0.05 || t.ancho === 0; }}",
            ms=ms, que="desaparece el aviso")


def boton_lista(pagina):
    return pagina.get_by_role("button", name="Ver lista de cosas", exact=True)


def boton_ajustes(pagina):
    return pagina.get_by_role("button", name="Ajustes", exact=True)


def accesos(pagina):
    """La fila de accesos «Cosas con» y «Cosas de» de la pantalla principal."""
    return pagina.locator("#accesos")


def campo(pagina):
    """El campo de la pantalla principal (la página de un grupo tiene otro con el mismo marcador)."""
    return pagina.locator("#campo")


def boton_guardar(pagina):
    return pagina.locator("#formulario button[type=submit]")


def esperar_vista(pagina, nombre):
    esperar(pagina, """(nombre) => ['inicio', 'lista', 'ajustes', 'grupo'].every((v) => {
        const el = document.querySelector('#vista-' + v);
        const visible = el.checkVisibility();
        return v === nombre ? visible : !visible;
    })""", arg=nombre, que=f"solo se ve la vista «{nombre}»")


def abrir_lista(pagina):
    boton_lista(pagina).tap()
    esperar_vista(pagina, "lista")


def abrir_ajustes(pagina):
    boton_ajustes(pagina).tap()
    esperar_vista(pagina, "ajustes")


def volver(pagina):
    pagina.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(pagina, "inicio")


def anotar(pagina, texto, con="intro"):
    campo(pagina).fill(texto)
    if con == "intro":
        campo(pagina).press("Enter")
    else:
        boton_guardar(pagina).tap()


def filas(pagina):
    return pagina.locator("#lista-cosas > li")


def textos_lista(pagina):
    return pagina.evaluate("() => [...document.querySelectorAll('#lista-cosas > li p')].map((p) => p.textContent)")


def esperar_filas(pagina, cuantas, ms=3000):
    esperar(pagina, "(n) => document.querySelectorAll('#lista-cosas > li').length === n", arg=cuantas, ms=ms,
            que=f"la lista tiene {cuantas} filas en el DOM")


def fila_de(pagina, texto):
    return filas(pagina).filter(has=pagina.get_by_text(texto, exact=True))


def check_de(fila):
    return fila.locator("button").first


def borrar_de(fila):
    return fila.get_by_role("button", name="Eliminar", exact=True)


def elegir_color(pagina, nombre):
    """Toca la muestra como una persona: sobre el círculo (la etiqueta que envuelve al radio)."""
    radio = pagina.get_by_role("radio", name=nombre, exact=True)
    radio.locator("xpath=ancestor::label").tap()
    comprobar(radio.is_checked(), f"Tocar la muestra «{nombre}» no la selecciona")


def esperar_fondo(pagina, color):
    esperar(pagina, """(rgb) => [document.documentElement, document.body].every(
        (el) => getComputedStyle(el).backgroundColor === rgb)""",
            arg="rgb({}, {}, {})".format(*hex_a_rgb(color)), ms=3000,
            que=f"el fondo de html y body pasa a {color}")


def muestrear(pagina, puntos):
    """Color real (captura de pantalla) en varios puntos CSS."""
    colores = []
    for x, y in puntos:
        datos = pagina.screenshot(clip={"x": x, "y": y, "width": 2, "height": 2}, scale="css")
        _, _, pixel = decodificar_png(datos)
        colores.append(pixel(0, 0))
    return colores


# ----------------------------------------------------------------------------
# Pantalla principal
# ----------------------------------------------------------------------------

JS_AUDITORIA_INICIO = r"""
() => {
  const permitidos = ['#abrir-lista', '#abrir-ajustes', '#accesos', '#formulario'].map((s) => document.querySelector(s));
  const alfa = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c);
    if (!m) return c === 'transparent' ? 0 : 1;
    const p = m[1].split(/[,\/\s]+/).filter(Boolean).map(parseFloat);
    return p.length > 3 ? p[3] : 1;
  };
  const pinta = (el) => {
    const cs = getComputedStyle(el);
    if (alfa(cs.backgroundColor) > 0 || cs.backgroundImage !== 'none' || cs.boxShadow !== 'none') return true;
    for (const lado of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(cs['border' + lado + 'Width']) > 0 && cs['border' + lado + 'Style'] !== 'none'
          && alfa(cs['border' + lado + 'Color']) > 0) return true;
    }
    if (['IMG', 'SVG', 'CANVAS', 'VIDEO', 'INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'IFRAME']
      .includes(el.tagName.toUpperCase())) return true;
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) return true;
    return false;
  };
  const intrusos = [];
  const raices = new Set();
  for (const el of document.body.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT'].includes(el.tagName)) continue;
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true })) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) continue;
    if (!pinta(el)) continue;
    const raiz = permitidos.find((p) => p && p.contains(el));
    if (raiz) raices.add(raiz.id);
    else intrusos.push(`${el.tagName.toLowerCase()}#${el.id}.${el.className}`);
  }
  return { intrusos, raices: [...raices].sort(), texto: document.body.innerText.trim() };
}
"""


# Lo único con texto en la pantalla principal: los dos accesos a las listas compartidas.
RAICES_INICIO = ["abrir-ajustes", "abrir-lista", "accesos", "formulario"]
TEXTO_INICIO = "Cosas con Cosas de"


def texto_inicio(auditoria):
    """El texto visible de la pantalla principal, con los espacios normalizados."""
    return re.sub(r"\s+", " ", auditoria["texto"]).strip()


@prueba("inicio: exactamente 4 elementos visibles (dos botones, los accesos y la barra) y nada más")
def t_inicio_tres_elementos(e):
    p = e.pagina()
    esperar_vista(p, "inicio")
    comprobar(boton_lista(p).is_visible(), "El botón de lista no es visible")
    comprobar(boton_ajustes(p).is_visible(), "El botón de ajustes no es visible")
    comprobar(accesos(p).is_visible(), "Los accesos «Cosas con» y «Cosas de» no son visibles")
    comprobar(campo(p).is_visible(), "La barra para escribir no es visible")
    comprobar(not boton_guardar(p).is_visible(), "El botón Guardar se ve con el campo vacío")
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual(auditoria["intrusos"], [], "Elementos visibles que sobran en la pantalla principal")
    igual(auditoria["raices"], RAICES_INICIO, "Elementos visibles de inicio")
    igual(texto_inicio(auditoria), TEXTO_INICIO, "Texto visible en la pantalla principal (solo los accesos: ni títulos ni contadores)")
    # Con cosas guardadas sigue sin haber contadores ni insignias.
    anotar(p, "Una cosa")
    esperar_toast(p, "Guardado")
    esperar_sin_toast(p)
    p.wait_for_timeout(300)
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual(auditoria["intrusos"], [], "Elementos que sobran tras guardar una cosa")
    igual(texto_inicio(auditoria), TEXTO_INICIO, "Texto visible tras guardar (contador/insignia)")


@prueba("inicio: accesos «Cosas con» y «Cosas de» bajo los botones, centrados, en el dominio de la app y de vuelta a ella")
def t_inicio_accesos(e):
    # En el HTML son de este dominio (netlify.toml las trae de cosas.info: sin barra del navegador en la
    # app instalada). Aquí, por http y sin ese proxy, app.js los manda a cosas.info.
    html = (APP / "index.html").read_text(encoding="utf-8")
    for destino in ('href="con/?desde=app"', 'href="de/?desde=app"'):
        comprobar(destino in html, f"El HTML no lleva el acceso relativo {destino}")
    p = e.pagina()
    esperar_vista(p, "inicio")
    ancho = e.ancho
    lista, pildora = boton_lista(p).bounding_box(), p.locator("#formulario .pildora").bounding_box()
    nav = accesos(p).bounding_box()
    comprobar(nav["y"] >= lista["y"] + lista["height"] + 8, f"Los accesos no están bajo los botones: {nav} / {lista}")
    comprobar(nav["y"] + nav["height"] < pildora["y"], f"Los accesos pisan la barra: {nav} / {pildora}")
    enlaces = p.locator("#accesos a")
    igual(enlaces.count(), 2, "Número de accesos")
    igual([enlaces.nth(i).inner_text().strip() for i in range(2)], ["Cosas con", "Cosas de"], "Texto de los accesos")
    igual([enlaces.nth(i).get_attribute("href") for i in range(2)],
          ["https://cosas.info/con/?desde=app", "https://cosas.info/de/?desde=app"], "Destino de los accesos (con ?desde=app, para que la flecha vuelva a la app)")
    cajas = [enlaces.nth(i).bounding_box() for i in range(2)]
    for texto, caja in zip(("Cosas con", "Cosas de"), cajas):
        comprobar(caja["height"] >= 44 and caja["width"] >= 44, f"«{texto}» no llega a 44px de alto o ancho: {caja}")
        comprobar(caja["y"] + caja["height"] <= pildora["y"], f"«{texto}» pisa la barra")
    comprobar(abs(cajas[0]["y"] - cajas[1]["y"]) <= 1, "Los dos accesos no están a la misma altura")
    comprobar(cajas[0]["x"] + cajas[0]["width"] <= cajas[1]["x"], "Los accesos se solapan")
    centro = (cajas[0]["x"] + cajas[1]["x"] + cajas[1]["width"]) / 2
    comprobar(abs(centro - ancho / 2) <= 1.5, f"Los accesos no están centrados: centro={centro}")
    datos = enlaces.first.evaluate("""(a) => { const cs = getComputedStyle(a), svg = a.querySelector('svg');
        return { radio: cs.borderTopLeftRadius, fondo: cs.backgroundColor, trazo: getComputedStyle(svg).stroke, color: cs.color,
                 anchoSvg: svg.getBoundingClientRect().width, subrayado: cs.textDecorationLine }; }""")
    comprobar(parseFloat_css(datos["radio"]) >= 22, f"Los accesos no tienen forma de píldora: radio {datos['radio']}")
    alfa = re.findall(r"[\d.]+", datos["fondo"])
    comprobar(len(alfa) == 4 and 0 < float(alfa[3]) < 1, f"La superficie de los accesos no es translúcida: {datos['fondo']}")
    igual((datos["trazo"], datos["subrayado"]), (datos["color"], "none"), "Icono en currentColor y sin subrayado")
    comprobar(datos["anchoSvg"] >= 16, "El icono del acceso no se ve")
    # El tabulador llega antes al campo (tercer elemento) y después a los accesos.
    for _ in range(3):
        p.keyboard.press("Tab")
    igual(p.evaluate("() => document.activeElement.id"), "campo", "Tercer elemento al tabular")
    while p.evaluate("() => document.activeElement.closest('#formulario') !== null"):
        p.keyboard.press("Tab")
    igual(p.evaluate("() => document.activeElement.textContent.trim()"), "Cosas con", "Tras la barra, el tabulador llega a los accesos")


def parseFloat_css(valor):
    return float(re.findall(r"[\d.]+", valor)[0])


@prueba("inicio: posición de los 3 elementos (cajas)")
def t_inicio_posiciones(e):
    p = e.pagina()
    ancho, alto = e.ancho, e.alto
    lista, ajustes = boton_lista(p).bounding_box(), boton_ajustes(p).bounding_box()
    pildora = p.locator("#formulario .pildora").bounding_box()
    comprobar(lista["x"] + lista["width"] < ancho / 2, f"El botón de lista no está a la izquierda: {lista}")
    comprobar(ajustes["x"] > ancho / 2, f"El botón de ajustes no está a la derecha: {ajustes}")
    for nombre, caja in (("lista", lista), ("ajustes", ajustes)):
        comprobar(0 <= caja["y"] <= 40, f"El botón de {nombre} no está arriba: y={caja['y']}")
    comprobar(abs(lista["y"] - ajustes["y"]) <= 1, "Los dos botones no están a la misma altura")
    margen_izq = lista["x"]
    margen_der = ancho - (ajustes["x"] + ajustes["width"])
    comprobar(abs(margen_izq - margen_der) <= 1 and 8 <= margen_izq <= 32,
              f"Márgenes laterales desiguales o raros: izq={margen_izq}, der={margen_der}")
    hueco = alto - (pildora["y"] + pildora["height"])
    comprobar(0 <= hueco <= 48, f"La barra no está pegada abajo: quedan {hueco}px por debajo")
    comprobar(pildora["y"] > alto * 0.75, f"La barra no está en la parte baja: y={pildora['y']}")
    comprobar(pildora["width"] >= min(ancho - 48, 640) - 1, f"La barra es demasiado estrecha: {pildora['width']}")
    centro = pildora["x"] + pildora["width"] / 2
    comprobar(abs(centro - ancho / 2) <= 1, f"La barra no está centrada: centro={centro}")
    radio = p.evaluate("() => parseFloat(getComputedStyle(document.querySelector('#formulario .pildora')).borderTopLeftRadius)")
    comprobar(radio >= pildora["height"] / 2 - 0.5, f"La barra no tiene forma de píldora: radio {radio}px")
    comprobar(p.evaluate("() => document.querySelector('#campo').closest('form') !== null"), "El campo no está en un <form>")


@prueba("inicio: botones redondos de 48px, translúcidos, con icono SVG")
def t_botones_redondos(e):
    p = e.pagina()
    for nombre, boton in (("lista", boton_lista(p)), ("ajustes", boton_ajustes(p))):
        datos = boton.evaluate("""(b) => {
            const cs = getComputedStyle(b), r = b.getBoundingClientRect(), svg = b.querySelector('svg');
            const css = svg ? getComputedStyle(svg) : null;
            return { ancho: r.width, alto: r.height, radio: cs.borderTopLeftRadius, fondo: cs.backgroundColor,
                     svg: Boolean(svg), trazo: css && css.stroke, color: cs.color, relleno: css && css.fill,
                     anchoSvg: svg ? svg.getBoundingClientRect().width : 0,
                     ariaOculto: svg && svg.getAttribute('aria-hidden') };
        }""")
        igual((round(datos["ancho"], 2), round(datos["alto"], 2)), (48, 48), f"Tamaño del botón de {nombre}")
        igual(datos["radio"], "50%", f"border-radius del botón de {nombre}")
        comprobar(datos["svg"] and datos["anchoSvg"] >= 18, f"El botón de {nombre} no tiene icono SVG visible")
        igual(datos["trazo"], datos["color"], f"El trazo del icono de {nombre} no es currentColor")
        alfa = re.findall(r"[\d.]+", datos["fondo"])
        comprobar(len(alfa) == 4 and 0 < float(alfa[3]) < 1, f"La superficie del botón de {nombre} no es translúcida: {datos['fondo']}")
    # El icono de la izquierda son tres líneas horizontales (se cuenta en la captura).
    caja = boton_lista(p).bounding_box()
    _, alto_img, pixel = decodificar_png(p.screenshot(clip=caja, scale="css"))
    columna = [pixel(int(caja["width"] // 2), y) for y in range(alto_img)]
    tinta = [min(c) > 200 for c in columna]  # Tinta blanca sobre el azul por defecto.
    tramos = sum(1 for i, v in enumerate(tinta) if v and (i == 0 or not tinta[i - 1]))
    igual(tramos, 3, "Número de líneas horizontales del icono del botón de lista")


def tocar(cdp, tipo, puntos, pagina=None):
    cdp.send("Input.dispatchTouchEvent", {"type": tipo, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(puntos)]})
    if pagina:
        pagina.wait_for_timeout(60)  # Chrome entrega los touchmove al ritmo de los fotogramas.


def deslizar(pagina, desde, dx, dy=0, pasos=8, pausa=0, soltar=True, dedos=1):
    """Un gesto táctil de verdad (CDP): pone el dedo en «desde», lo lleva dx/dy en «pasos» y lo levanta."""
    cdp = pagina.context.new_cdp_session(pagina)
    x0, y0 = desde
    tocar(cdp, "touchStart", [(x0 + 30 * d, y0) for d in range(dedos)])
    for i in range(1, pasos + 1):
        tocar(cdp, "touchMove", [(x0 + 30 * d + dx * i / pasos, y0 + dy * i / pasos) for d in range(dedos)])
        if pausa:
            pagina.wait_for_timeout(pausa)
    if soltar:
        tocar(cdp, "touchEnd", [])
    return cdp


JS_ESQUINAS = """() => Object.fromEntries(['abrir-lista', 'abrir-ajustes'].map((id) => { const b = document.getElementById(id), cs = getComputedStyle(b);
    return [id, { atributos: [...b.attributes].map((a) => a.name).sort(), estilo: b.getAttribute('style'), transform: cs.transform, fondo: cs.backgroundColor }]; }))"""


JS_ARO = "() => getComputedStyle(document.activeElement).outlineStyle"


@prueba("inicio: deslizar hacia la derecha abre la lista y hacia la izquierda los ajustes, sin marcas en pantalla ni gestos del navegador")
def t_inicio_deslizar(e):
    p = e.pagina()
    esperar_vista(p, "inicio")
    centro = (e.ancho / 2, e.alto * 0.45)
    # El deslizar de lado es de la app: el navegador no lo toma (ni su «atrás»/«adelante» con un círculo).
    igual(p.evaluate("() => [getComputedStyle(document.querySelector('#vista-inicio')).touchAction, getComputedStyle(document.documentElement).overscrollBehaviorX]"),
          ["pan-y pinch-zoom", "none"], "touch-action de la pantalla principal y overscroll-behavior-x de la página")
    # Mientras se desliza no se mueve ni se marca nada; volviendo atrás con el dedo no se abre nada.
    reposo = p.evaluate(JS_ESQUINAS)
    cdp = deslizar(p, centro, 40, soltar=False)
    p.wait_for_timeout(60)
    igual(p.evaluate(JS_ESQUINAS), reposo, "Los botones a mitad de camino")
    tocar(cdp, "touchMove", [(centro[0] + 90, centro[1])], p)
    igual(p.evaluate(JS_ESQUINAS), reposo, "Los botones pasado el umbral")
    tocar(cdp, "touchMove", [centro])
    tocar(cdp, "touchEnd", [])
    p.wait_for_timeout(400)
    igual(vista_visible(p), "inicio", "Soltar donde se empezó no abre nada")
    # Hacia la derecha: la lista, la esquina izquierda (una entrada de historial; al volver, el foco en su botón).
    antes = p.evaluate("() => history.length")
    deslizar(p, centro, 90, pausa=40)
    esperar_vista(p, "lista")
    igual(p.evaluate("() => history.length"), antes + 1, "Historial tras abrir la lista deslizando")
    p.go_back()
    esperar_vista(p, "inicio")
    igual(p.evaluate("() => document.activeElement.id"), "abrir-lista", "Foco al volver de la lista abierta deslizando")
    # Sin aro de foco en la esquina (es para el teclado, y en iPhone salía al volver); con la primera tecla, vuelve.
    igual(p.evaluate(JS_ARO), "none", "Aro de foco en la esquina tras deslizar y volver")
    p.keyboard.press("Tab")
    igual((p.evaluate("() => document.activeElement.id"), p.evaluate(JS_ARO)), ("abrir-ajustes", "solid"), "Con el tabulador, el aro vuelve")
    p.wait_for_timeout(400)
    # Lo mismo tocando: el botón de la lista y «Volver». (Antes del golpe rápido de abajo: en la emulación, su inercia
    # no se acaba nunca y el siguiente toque solo la para.)
    abrir_lista(p)
    volver(p)
    igual((p.evaluate("() => document.activeElement.id"), p.evaluate(JS_ARO)), ("abrir-lista", "none"), "Aro de foco en la esquina tras tocar y volver")
    p.wait_for_timeout(400)
    # Hacia la izquierda, con un golpe rápido y corto: los ajustes, la esquina derecha.
    deslizar(p, centro, -40, pasos=3)
    esperar_vista(p, "ajustes")
    p.go_back()
    esperar_vista(p, "inicio")
    igual(p.evaluate("() => document.activeElement.id"), "abrir-ajustes", "Foco al volver de los ajustes abiertos deslizando")
    igual(p.evaluate(JS_ARO), "none", "Aro de foco en la esquina tras un golpe rápido y volver")
    p.wait_for_timeout(400)
    # Lo que no abre nada: corto y lento, vertical, en diagonal, desde la barra de escribir, desde el borde, con dos dedos.
    barra = p.locator("#formulario .pildora").bounding_box()
    for que, desde, dx, dy, dedos, pausa in (("corto y lento", centro, 50, 0, 1, 40), ("vertical", centro, 0, -150, 1, 0),
                                              ("en diagonal", centro, 80, 70, 1, 0), ("desde la barra de escribir", (e.ancho / 2, barra["y"] + barra["height"] / 2), 120, 0, 1, 0),
                                              ("desde el borde izquierdo", (8, e.alto * 0.45), 150, 0, 1, 0), ("desde el borde derecho", (e.ancho - 8, e.alto * 0.45), -150, 0, 1, 0),
                                              ("con dos dedos", (e.ancho / 2 - 15, e.alto * 0.45), 120, 0, 2, 0)):
        deslizar(p, desde, dx, dy, pausa=pausa, dedos=dedos)
        p.wait_for_timeout(400)
        igual(vista_visible(p), "inicio", f"Deslizar {que}")
    igual(p.evaluate("() => history.length"), antes + 1, "Historial tras los gestos que no abren nada")


@prueba("lista y ajustes: deslizar de vuelta (a la izquierda en la lista, a la derecha en los ajustes) vuelve a la principal; el otro lado, los campos, el grupo y lo vertical, no")
def t_deslizar_volver(e):
    p = sembrar(e, estado_con([cosa(i, f"Cosa número {i}") for i in range(1, 41)], grupos=[grupo(100, "Casa")]))
    esperar_vista(p, "inicio")
    centro = (e.ancho / 2, e.alto * 0.5)
    antes = p.evaluate("() => history.length")
    # Todos los gestos, lentos (pausa): en la emulación, la inercia de uno rápido se come el toque siguiente.
    for vista, abrir, boton, vuelta in (("lista", abrir_lista, "abrir-lista", -120), ("ajustes", abrir_ajustes, "abrir-ajustes", 120)):
        abrir(p)
        p.wait_for_timeout(400)
        igual(p.evaluate(f"() => [...document.querySelectorAll('#vista-{vista}, #vista-{vista} .desplazable')].map((el) => getComputedStyle(el).touchAction)"),
              ["pan-y pinch-zoom"] * 2, f"touch-action en «{vista}» y su zona desplazable")
        deslizar(p, centro, -vuelta, pausa=40)
        p.wait_for_timeout(400)
        igual(vista_visible(p), vista, f"En «{vista}», deslizar hacia el otro lado no hace nada")
        campo_texto = "#campo-grupo" if vista == "lista" else "#nombre"
        if vista == "lista":
            p.locator("#crear-grupo").tap()  # La barra de crear grupos se convierte en un campo.
            p.wait_for_timeout(300)
        caja = p.locator(campo_texto).bounding_box()
        deslizar(p, (caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2), vuelta, pausa=40)
        p.wait_for_timeout(400)
        igual(vista_visible(p), vista, f"En «{vista}», deslizar desde un campo de texto no vuelve")
        deslizar(p, centro, vuelta, pausa=40)
        esperar_vista(p, "inicio")
        # Como «Volver»: retrocede en el historial (no apila otra entrada) y el foco va al botón que la abrió, sin aro.
        igual((p.evaluate("() => location.hash"), p.evaluate("() => history.length")), ("", antes + 1), f"Historial tras volver de «{vista}» deslizando")
        igual((p.evaluate("() => document.activeElement.id"), p.evaluate(JS_ARO)), (boton, "none"), f"Foco tras volver de «{vista}» deslizando")
        p.wait_for_timeout(400)
    # En la página de un grupo, deslizar no hace nada (tiene su propio «Volver», a la lista).
    abrir_lista(p)
    p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
    abrir_grupo(p, "Casa")
    p.wait_for_timeout(400)
    for dx in (-120, 120):
        deslizar(p, centro, dx, pausa=40)
        p.wait_for_timeout(400)
        igual(vista_visible(p), "grupo", f"En un grupo, deslizar {dx:+d}px")
    volver_a_la_lista = p.get_by_role("button", name="Volver", exact=True)
    volver_a_la_lista.tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    # Deslizar en vertical sigue desplazando la lista y no vuelve.
    zona = p.locator("#zona-lista")
    zona.evaluate("(z) => { z.scrollTop = 0; }")
    deslizar(p, centro, 0, -250, pausa=40)
    p.wait_for_timeout(500)
    igual(vista_visible(p), "lista", "Deslizar en vertical en la lista")
    comprobar(zona.evaluate("(z) => z.scrollTop") > 50, "Deslizar en vertical ya no desplaza la lista")


@prueba("inicio: atributos de la barra de escribir")
def t_atributos_campo(e):
    p = e.pagina()
    datos = campo(p).evaluate("""(c) => ({
        placeholder: c.getAttribute('placeholder'), enterkeyhint: c.getAttribute('enterkeyhint'),
        autocomplete: c.getAttribute('autocomplete'), autocapitalize: c.getAttribute('autocapitalize'),
        maxlength: c.getAttribute('maxlength'), fuente: parseFloat(getComputedStyle(c).fontSize), tipo: c.type })""")
    igual(datos["placeholder"], "Escribe una cosa\u2026", "placeholder (con el carácter de puntos suspensivos)")
    igual(datos["enterkeyhint"], "done", "enterkeyhint")
    igual(datos["autocomplete"], "off", "autocomplete")
    igual(datos["autocapitalize"], "sentences", "autocapitalize")
    igual(datos["maxlength"], "500", "maxlength")
    comprobar(datos["fuente"] >= 16, f"font-size del campo < 16px: {datos['fuente']}")
    fuentes = p.evaluate("() => [...document.querySelectorAll('input[type=text], input[type=email]')].map((i) => parseFloat(getComputedStyle(i).fontSize))")
    comprobar(all(f >= 16 for f in fuentes), f"Algún campo de texto baja de 16px (zoom en iOS): {fuentes}")


@prueba("guardar con Intro: aviso «Guardado», campo vacío y sin foco")
def t_guardar_intro(e):
    p = e.pagina()
    campo(p).tap()
    comprobar(p.evaluate("() => document.activeElement.id === 'campo'"), "El campo no recibe el foco al tocarlo")
    p.keyboard.type("Comprar pan")
    p.keyboard.press("Enter")
    aviso = esperar_toast(p, "Guardado")
    comprobar(aviso["icono"], "El aviso «Guardado» no lleva icono de check")
    igual(aviso["ariaLive"], "polite", "aria-live del aviso")
    igual(campo(p).input_value(), "", "El campo no se vacía tras guardar")
    comprobar(p.evaluate("() => document.activeElement.id !== 'campo'"), "El campo conserva el foco (el teclado no se cerraría)")
    esperar(p, "() => getComputedStyle(document.querySelector('#formulario button[type=submit]')).visibility === 'hidden'", ms=1500, que="el botón Guardar se oculta tras guardar")
    estado = leer_estado(p)
    igual([c["texto"] for c in estado["cosas"]], ["Comprar pan"], "Cosas guardadas")
    unica = estado["cosas"][0]
    igual(estado["version"], 2, "version en el almacenamiento")
    igual(sorted(unica.keys()), ["creada", "grupo", "hecha", "hechaEn", "id", "texto"], "Forma de cada cosa guardada")
    comprobar(isinstance(unica["id"], str) and re.fullmatch(r"[0-9a-f-]{36}", unica["id"]), f"id no es un UUID: {unica['id']!r}")
    igual((unica["hecha"], unica["hechaEn"], unica["grupo"]), (False, None, None), "hecha, hechaEn y grupo iniciales")
    igual(estado["grupos"], [], "grupos iniciales")
    comprobar(isinstance(unica["creada"], (int, float)) and abs(unica["creada"] - time.time() * 1000) < 60_000, "creada no es la hora actual en ms")
    igual(sorted(estado["ajustes"].keys()), ["colorFondo", "nombre"], "Forma de ajustes")
    igual(sorted(p.evaluate("() => Object.keys(localStorage)")), sorted([CLAVE, CLAVE_RESPALDO]), "Claves usadas en localStorage (la principal y el respaldo de grupos)")


@prueba("guardar con el botón de enviar (visible solo con texto)")
def t_guardar_boton(e):
    p = e.pagina()
    guardar = boton_guardar(p)
    comprobar(not guardar.is_visible(), "Botón visible con el campo vacío")
    campo(p).fill("   ")
    comprobar(not guardar.is_visible(), "Botón visible con solo espacios")
    campo(p).fill("Llamar a mamá")
    esperar(p, "() => getComputedStyle(document.querySelector('#formulario button[type=submit]')).opacity === '1'", que="el botón Guardar aparece")
    comprobar(guardar.is_visible(), "El botón Guardar no aparece al escribir")
    igual(guardar.get_attribute("aria-label"), "Guardar", "aria-label del botón de enviar")
    caja, pildora = guardar.bounding_box(), p.locator("#formulario .pildora").bounding_box()
    comprobar(abs(caja["width"] - caja["height"]) < 0.5 and caja["width"] >= 32, f"El botón Guardar no es redondo/pequeño: {caja}")
    igual(guardar.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), "50%", "border-radius del botón Guardar")
    comprobar(caja["x"] >= pildora["x"] and caja["y"] >= pildora["y"]
              and caja["x"] + caja["width"] <= pildora["x"] + pildora["width"]
              and caja["y"] + caja["height"] <= pildora["y"] + pildora["height"], "El botón Guardar se sale de la píldora")
    comprobar(pildora["x"] + pildora["width"] - (caja["x"] + caja["width"]) <= 12, "El botón Guardar no está en el extremo derecho")
    comprobar(guardar.locator("svg").count() == 1, "El botón Guardar no tiene icono")
    guardar.tap()
    esperar_toast(p, "Guardado")
    igual(campo(p).input_value(), "", "Campo tras guardar con el botón")
    campo(p).fill("x")
    campo(p).fill("")
    esperar(p, "() => getComputedStyle(document.querySelector('#formulario button[type=submit]')).visibility === 'hidden'", que="el botón Guardar se oculta al vaciar")
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["Llamar a mamá"], "Cosas guardadas")


@prueba("guardar: recorta espacios; solo espacios o vacío no hace nada")
def t_guardar_vacio(e):
    p = e.pagina()
    for texto in ("", "     ", "\t  "):
        campo(p).fill(texto)
        campo(p).press("Enter")
        p.evaluate("() => document.querySelector('#formulario').requestSubmit()")
        p.wait_for_timeout(250)
        aviso = toast(p)
        comprobar(not aviso["visible"] and aviso["texto"] == "", f"Sale un aviso al enviar {texto!r}: {aviso}")
        estado = leer_estado(p)
        comprobar(estado is None or estado["cosas"] == [], f"Se guardó algo al enviar {texto!r}: {estado}")
    anotar(p, "   con espacios   ")
    esperar_toast(p, "Guardado")
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["con espacios"], "El texto no se recorta")


@prueba("aviso «Guardado»: centrado, visible, ~1,8 s y desaparece")
def t_toast_guardado(e):
    p = e.pagina()
    campo(p).fill("Regar las plantas")
    inicio = time.monotonic()
    campo(p).press("Enter")
    aviso = esperar_toast(p, "Guardado")
    esperar(p, f"() => ({JS_TOAST})().opacidad === 1", que="el aviso termina de aparecer")
    aviso = toast(p)
    centro = aviso["x"] + aviso["ancho"] / 2
    comprobar(abs(centro - e.ancho / 2) <= 1.5, f"El aviso no está centrado: centro={centro}, pantalla={e.ancho}")
    comprobar(aviso["x"] >= 0 and aviso["y"] >= 0 and aviso["x"] + aviso["ancho"] <= e.ancho
              and aviso["y"] + aviso["alto"] <= e.alto, f"El aviso se sale de la pantalla: {aviso}")
    solapes = p.evaluate("""() => {
        const t = document.querySelector('[role="status"]').getBoundingClientRect();
        const choca = (sel) => { const r = document.querySelector(sel).getBoundingClientRect();
            return !(r.right <= t.left || r.left >= t.right || r.bottom <= t.top || r.top >= t.bottom); };
        return { barra: choca('#formulario .pildora'), lista: choca('#abrir-lista'), ajustes: choca('#abrir-ajustes') };
    }""")
    igual(solapes, {"barra": False, "lista": False, "ajustes": False}, "Solapes del aviso")
    # El aviso lleva pointer-events:none: que nada lo tapa se comprueba con píxeles reales.
    fondo_aviso = rgb_css(p.evaluate("() => getComputedStyle(document.querySelector('[role=status]')).backgroundColor"))
    medio = aviso["y"] + aviso["alto"] / 2
    pintado = muestrear(p, [(aviso["x"] + 7, medio), (aviso["x"] + aviso["ancho"] - 8, medio)])
    igual(pintado, [fondo_aviso] * 2, "El aviso no se ve en la captura (algo lo tapa)")
    p.wait_for_timeout(max(0, 1000 - (time.monotonic() - inicio) * 1000))
    comprobar(toast(p)["visible"], "El aviso desaparece antes de 1 s")
    esperar_sin_toast(p, ms=3000)
    duracion = time.monotonic() - inicio
    detalle(f"duración del aviso Guardado: {duracion:.2f} s")
    comprobar(1.4 <= duracion <= 2.8, f"El aviso dura {duracion:.2f} s (se esperaban ~1,8 s)")
    esperar(p, "() => document.querySelector('[role=\"status\"]').textContent.trim() === ''", ms=1500, que="el aviso se vacía")


@prueba("guardados rápidos consecutivos: todos se guardan y el aviso se reinicia")
def t_guardados_rapidos(e):
    p = e.pagina()
    for texto in ("Uno", "Dos", "Tres"):
        anotar(p, texto)
    esperar_toast(p, "Guardado")
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["Tres", "Dos", "Uno"], "Cosas tras tres guardados seguidos")
    ids = [c["id"] for c in leer_estado(p)["cosas"]]
    igual(len(set(ids)), 3, "ids únicos")
    esperar_sin_toast(p)
    # El segundo guardado reinicia el reloj del aviso.
    anotar(p, "Cuatro")
    esperar_toast(p, "Guardado")
    p.wait_for_timeout(1200)
    anotar(p, "Cinco")
    p.wait_for_timeout(1000)
    aviso = toast(p)
    comprobar(aviso["visible"] and aviso["texto"] == "Guardado", f"El aviso no se vuelve a mostrar limpio tras un guardado seguido: {aviso}")
    esperar_sin_toast(p)
    igual(len(leer_estado(p)["cosas"]), 5, "Número de cosas")


@prueba("doble toque en enviar no duplica")
def t_doble_toque_enviar(e):
    p = e.pagina()
    campo(p).fill("Doble clic")
    boton_guardar(p).dblclick()
    campo(p).fill("Doble toque táctil")
    caja = boton_guardar(p).bounding_box()
    x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
    p.touchscreen.tap(x, y)
    p.touchscreen.tap(x, y)
    campo(p).fill("Doble envío")
    p.evaluate("() => { const f = document.querySelector('#formulario'); f.requestSubmit(); f.requestSubmit(); }")
    campo(p).fill("Doble intro")
    campo(p).press("Enter")
    p.keyboard.press("Enter")
    p.wait_for_timeout(200)
    igual([c["texto"] for c in leer_estado(p)["cosas"]],
          ["Doble intro", "Doble envío", "Doble toque táctil", "Doble clic"], "Cosas tras dobles toques")


# ----------------------------------------------------------------------------
# Lista
# ----------------------------------------------------------------------------

@prueba("lista: cabecera, las más nuevas primero y disposición de cada fila")
def t_lista_orden(e):
    p = e.pagina()
    for texto in ("Primera", "Segunda", "Tercera"):
        anotar(p, texto)
    abrir_lista(p)
    igual(textos_lista(p), ["Tercera", "Segunda", "Primera"], "Orden de la lista")
    igual(p.locator("#vista-lista h1").text_content().strip(), "Cosas", "Título de la lista")
    atras = p.get_by_role("button", name="Volver", exact=True)
    caja = atras.bounding_box()
    comprobar(caja["x"] < e.ancho / 2 and caja["y"] <= 40, f"El botón Volver no está arriba a la izquierda: {caja}")
    igual((round(caja["width"], 2), round(caja["height"], 2)), (48, 48), "Tamaño del botón Volver")
    igual(atras.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), "50%", "border-radius de Volver")
    for fila in filas(p).all():
        check, texto, borrar = check_de(fila).bounding_box(), fila.locator("p").bounding_box(), borrar_de(fila).bounding_box()
        comprobar(check["x"] + check["width"] <= texto["x"] + 0.5, "El check no está a la izquierda del texto")
        comprobar(texto["x"] + texto["width"] <= borrar["x"] + 0.5, "Eliminar no está a la derecha del texto")
        for nombre, caja_boton, boton in (("check", check, check_de(fila)), ("eliminar", borrar, borrar_de(fila))):
            comprobar(abs(caja_boton["width"] - caja_boton["height"]) < 0.5 and caja_boton["width"] >= 44,
                      f"El botón {nombre} no es redondo de ≥44px: {caja_boton}")
            igual(boton.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), "50%", f"border-radius de {nombre}")
            comprobar(boton.locator("svg").count() == 1, f"El botón {nombre} no tiene icono")
        igual(check_de(fila).get_attribute("aria-label"), "Marcar como hecha", "aria-label del check pendiente")
        igual(check_de(fila).get_attribute("aria-pressed"), "false", "aria-pressed inicial")


def colores_check(boton):
    return boton.evaluate("(b) => { const cs = getComputedStyle(b); return { fondo: cs.backgroundColor, icono: getComputedStyle(b.querySelector('svg')).stroke }; }")


def comprobar_gris(boton, pagina, momento):
    esperar(pagina, "(b) => getComputedStyle(b).backgroundColor !== 'rgb(34, 197, 94)'", arg=boton.element_handle(), que="el check deja de ser verde")
    pagina.wait_for_timeout(260)
    colores = colores_check(boton)
    fondo, icono = rgb_css(colores["fondo"]), rgb_css(colores["icono"])
    comprobar(min(fondo) >= 200 and max(fondo) - min(fondo) <= 12, f"{momento}: la superficie del check no es clara y neutra: {colores['fondo']}")
    comprobar(max(icono) - min(icono) <= 20 and 90 <= sum(icono) / 3 <= 185, f"{momento}: el icono del check no es gris: {colores['icono']}")
    return fondo, icono


@prueba("check: gris -> verde #22C55E -> gris (aria-pressed, etiquetas, color real)")
def t_check_alterna(e):
    p = e.pagina()
    anotar(p, "Tender la ropa")
    abrir_lista(p)
    fila = fila_de(p, "Tender la ropa")
    boton = check_de(fila)
    comprobar_gris(boton, p, "pendiente")
    boton.tap()
    esperar(p, "(b) => b.getAttribute('aria-pressed') === 'true'", arg=boton.element_handle(), que="aria-pressed pasa a true")
    esperar(p, "(b) => getComputedStyle(b).backgroundColor === 'rgb(34, 197, 94)'", arg=boton.element_handle(), que="el check se pone verde #22C55E")
    igual(boton.get_attribute("aria-label"), "Marcar como pendiente", "aria-label del check hecho")
    esperar(p, "(b) => getComputedStyle(b.querySelector('svg')).stroke === 'rgb(255, 255, 255)'", arg=boton.element_handle(), que="el icono del check hecho es blanco")
    estilo = fila.locator("p").evaluate("(t) => { const cs = getComputedStyle(t); return { linea: cs.textDecorationLine, opacidad: parseFloat(cs.opacity) }; }")
    comprobar("line-through" in estilo["linea"], f"El texto hecho no va tachado: {estilo}")
    p.wait_for_timeout(300)
    opacidad = fila.locator("p").evaluate("(t) => parseFloat(getComputedStyle(t).opacity)")
    comprobar(0.3 <= opacidad < 1, f"El texto hecho no baja de opacidad: {opacidad}")
    igual(leer_estado(p)["cosas"][0]["hecha"], True, "hecha en el almacenamiento")
    # Color real en pantalla: la mayor parte del botón es verde.
    caja = boton.bounding_box()
    ancho_img, alto_img, pixel = decodificar_png(p.screenshot(clip=caja, scale="css"))
    verdes = sum(1 for y in range(alto_img) for x in range(ancho_img)
                 if all(abs(a - b) <= 8 for a, b in zip(pixel(x, y), VERDE)))
    comprobar(verdes / (ancho_img * alto_img) > 0.45, f"El botón no se ve verde en la captura ({verdes} píxeles verdes)")
    boton.tap()
    esperar(p, "(b) => b.getAttribute('aria-pressed') === 'false'", arg=boton.element_handle(), que="aria-pressed vuelve a false")
    comprobar_gris(boton, p, "de vuelta a pendiente")
    igual(boton.get_attribute("aria-label"), "Marcar como hecha", "aria-label al volver a pendiente")
    igual(leer_estado(p)["cosas"][0]["hecha"], False, "hecha vuelve a false en el almacenamiento")
    linea = fila.locator("p").evaluate("(t) => getComputedStyle(t).textDecorationLine")
    comprobar("line-through" not in linea, "El texto sigue tachado al volver a pendiente")


@prueba("check pendiente se lee gris sobre cualquier fondo")
def t_check_gris_en_todos_los_fondos(e):
    vistos = set()
    for color in ("#2F6FED", "#FFFFFF", "#111111", "#F2B705", "#EF5B5B"):
        p = sembrar(e, estado_con([cosa(1, "Pendiente")], color=color), ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
        esperar_filas(p, 1)
        vistos.add(comprobar_gris(check_de(filas(p).first), p, f"fondo {color}"))
        p.context.close()
    igual(len(vistos), 1, "El gris del check cambia según el fondo")


@prueba("eliminar: quita exactamente esa cosa y muestra «Eliminado» + «Deshacer»")
def t_eliminar(e):
    p = sembrar(e, estado_con([cosa(3, "Tercera"), cosa(2, "Segunda", hecha=True), cosa(1, "Primera")]), ruta="#lista")
    esperar_filas(p, 3)
    igual(borrar_de(fila_de(p, "Segunda")).get_attribute("aria-label"), "Eliminar", "aria-label de eliminar")
    inicio = time.monotonic()
    borrar_de(fila_de(p, "Segunda")).tap()
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["Tercera", "Primera"], "Almacenamiento justo después de eliminar")
    aviso = esperar_toast(p, "Eliminado")
    igual(aviso["boton"], "Deshacer", "Botón del aviso")
    comprobar(aviso["altoBoton"] >= 44 and aviso["anchoBoton"] >= 44, f"«Deshacer» mide menos de 44px: {aviso}")
    esperar_filas(p, 2, ms=1500)
    comprobar(time.monotonic() - inicio < 1.5, "La animación de salida no es corta")
    igual(textos_lista(p), ["Tercera", "Primera"], "Lista tras eliminar")
    esperar(p, f"() => ({JS_TOAST})().opacidad === 1")
    aviso = toast(p)
    comprobar(abs(aviso["x"] + aviso["ancho"] / 2 - e.ancho / 2) <= 1.5, f"«Eliminado» no está centrado: {aviso}")
    comprobar(aviso["y"] >= 0 and aviso["y"] + aviso["alto"] <= e.alto and aviso["x"] >= 0 and aviso["x"] + aviso["ancho"] <= e.ancho, f"«Eliminado» se sale de la pantalla: {aviso}")
    p.wait_for_timeout(max(0, 3000 - (time.monotonic() - inicio) * 1000))
    comprobar(toast(p)["visible"], "«Eliminado» desaparece antes de 3 s")
    esperar_sin_toast(p, ms=3500)
    duracion = time.monotonic() - inicio
    detalle(f"duración del aviso Eliminado: {duracion:.2f} s")
    comprobar(3.4 <= duracion <= 5.2, f"«Eliminado» dura {duracion:.2f} s (se esperaban ~4 s)")
    p.reload()
    esperar_filas(p, 2)
    igual(textos_lista(p), ["Tercera", "Primera"], "Lista tras recargar")


@prueba("deshacer: devuelve la cosa a su posición y con su estado")
def t_deshacer(e):
    base = [cosa(4, "Cuarta"), cosa(3, "Tercera", hecha=True), cosa(2, "Segunda"), cosa(1, "Primera", hecha=True)]
    orden = orden_visible(base)  # Las hechas van al final (versión 1.2).
    p = sembrar(e, estado_con(base), ruta="#lista")
    esperar_filas(p, 4)
    for texto in ("Tercera", "Cuarta", "Primera", "Segunda"):
        hecha = next(c["hecha"] for c in base if c["texto"] == texto)
        borrar_de(fila_de(p, texto)).tap()
        esperar_toast(p, "Eliminado")
        comprobar(texto not in [c["texto"] for c in leer_estado(p)["cosas"]], f"«{texto}» sigue guardada tras eliminar")
        p.get_by_role("button", name="Deshacer", exact=True).tap()
        esperar(p, "(orden) => JSON.stringify([...document.querySelectorAll('#lista-cosas > li:not(.saliendo) p')].map((x) => x.textContent)) === JSON.stringify(orden)",
                arg=orden, que=f"«{texto}» vuelve a su posición original")
        esperar_filas(p, 4)
        igual(textos_lista(p), orden, f"Orden tras deshacer «{texto}»")
        igual(check_de(fila_de(p, texto)).get_attribute("aria-pressed"), str(hecha).lower(), f"Estado de «{texto}» tras deshacer")
        estado = leer_estado(p)["cosas"]
        igual([(c["texto"], c["hecha"]) for c in estado], [(c["texto"], c["hecha"]) for c in base], "Almacenamiento tras deshacer")
        esperar_sin_toast(p)
    # Borrar la última que queda enseña el estado vacío; deshacer lo quita.
    p2 = sembrar(e, estado_con([cosa(1, "Única", hecha=True)]), ruta="#lista")
    borrar_de(fila_de(p2, "Única")).tap()
    esperar(p2, "() => document.querySelector('#vacio').checkVisibility()", que="aparece el estado vacío al borrar la última")
    p2.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p2, 1)
    comprobar(not p2.get_by_text("No tienes cosas apuntadas.").is_visible(), "El estado vacío sigue tras deshacer")
    igual(check_de(filas(p2).first).get_attribute("aria-pressed"), "true", "Estado tras deshacer la única")


@prueba("doble toque en eliminar (0 ms) borra solo una cosa")
def t_doble_toque_eliminar(e):
    base = [cosa(5, "E"), cosa(4, "D"), cosa(3, "C"), cosa(2, "B"), cosa(1, "A")]
    p = sembrar(e, estado_con(base), ruta="#lista")
    esperar_filas(p, 5)
    borrar_de(fila_de(p, "D")).dblclick()
    p.wait_for_timeout(400)
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "C", "B", "A"], "Tras doble clic en eliminar D")
    p.evaluate("() => { const b = document.querySelectorAll('#lista-cosas > li')[1].querySelector('[aria-label=Eliminar]'); b.click(); b.click(); }")
    p.wait_for_timeout(400)
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "B", "A"], "Tras dos clics seguidos por script en eliminar C")
    caja = borrar_de(fila_de(p, "B")).bounding_box()
    x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
    p.touchscreen.tap(x, y)
    p.touchscreen.tap(x, y)
    p.wait_for_timeout(400)
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "A"], "Tras doble toque táctil inmediato en eliminar B")
    esperar_filas(p, 2)


@prueba("doble toque humano en eliminar (120 y 250 ms) borra solo una cosa")
def t_doble_toque_eliminar_humano(e):
    for pausa in (120, 250):
        base = [cosa(5, "E"), cosa(4, "D"), cosa(3, "C"), cosa(2, "B"), cosa(1, "A")]
        p = sembrar(e, estado_con(base), ruta="#lista")
        esperar_filas(p, 5)
        caja = borrar_de(fila_de(p, "D")).bounding_box()
        x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
        p.touchscreen.tap(x, y)
        p.wait_for_timeout(pausa)
        p.touchscreen.tap(x, y)
        p.wait_for_timeout(500)
        igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "C", "B", "A"],
              f"Cosas tras un doble toque con {pausa} ms entre toques sobre «Eliminar» de D")
        p.context.close()


@prueba("eliminar: Intro mantenido borra una sola cosa y «Deshacer» recupera todo lo borrado con el aviso a la vista")
def t_eliminar_intro_mantenido_y_deshacer_varias(e):
    base = [cosa(5, "E"), cosa(4, "D", hecha=True), cosa(3, "C"), cosa(2, "B"), cosa(1, "A")]
    p = sembrar(e, estado_con(base), ruta="#lista")
    esperar_filas(p, 5)
    borrar_de(fila_de(p, "D")).focus()
    for _ in range(14):  # Tecla mantenida: a partir de la segunda pulsación el evento lleva repeat=true.
        p.keyboard.down("Enter")
        p.wait_for_timeout(40)
    p.keyboard.up("Enter")
    p.wait_for_timeout(300)
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "C", "B", "A"], "Cosas tras mantener Intro ~0,5 s sobre «Eliminar» de D")
    # Un segundo borrado deliberado mientras sigue el aviso: «Deshacer» devuelve las dos a su sitio.
    borrar_de(fila_de(p, "B")).tap()
    esperar_filas(p, 3)
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["E", "C", "A"], "Cosas tras borrar también B")
    igual(esperar_toast(p, "Eliminado")["boton"], "Deshacer", "Botón del aviso tras el segundo borrado")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 5)
    igual(textos_lista(p), ["E", "C", "B", "A", "D"], "Lista tras deshacer dos borrados seguidos (la hecha, D, al final)")
    igual([(c["texto"], c["hecha"]) for c in leer_estado(p)["cosas"]], [(c["texto"], c["hecha"]) for c in base], "Almacenamiento tras deshacer dos borrados")
    igual(check_de(fila_de(p, "D")).get_attribute("aria-pressed"), "true", "Estado de D tras deshacer")
    # Cuando el aviso caduca ya no queda nada pendiente: el siguiente «Deshacer» solo recupera lo último.
    esperar_sin_toast(p)
    borrar_de(fila_de(p, "E")).tap()
    esperar_toast(p, "Eliminado")
    esperar_sin_toast(p, ms=6000)
    borrar_de(fila_de(p, "A")).tap()
    esperar_toast(p, "Eliminado")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 4)
    igual(textos_lista(p), ["C", "B", "A", "D"], "Tras caducar un aviso, «Deshacer» solo recupera el último borrado")


@prueba("«Deshacer» con el foco: el aviso espera y, si se cierra, el foco no se pierde")
def t_deshacer_con_foco(e):
    contexto = e.contexto()
    p = sembrar(e, estado_con([cosa(3, "C"), cosa(2, "B"), cosa(1, "A")]), ruta="#lista", contexto=contexto)
    esperar_filas(p, 3)
    borrar_de(fila_de(p, "B")).tap()
    esperar_toast(p, "Eliminado")
    p.get_by_role("button", name="Deshacer", exact=True).focus()
    p.wait_for_timeout(4800)
    comprobar(toast(p)["visible"], "El aviso desaparece aunque el foco esté en «Deshacer»")
    inicio = time.monotonic()
    check_de(filas(p).first).focus()
    p.wait_for_timeout(3000)
    comprobar(toast(p)["visible"], "Al salir el foco, el aviso no concede otros ~4 s")
    esperar_sin_toast(p, ms=2500)
    detalle(f"el aviso dura {time.monotonic() - inicio:.2f} s tras salir el foco")
    # Si el aviso se cierra con el foco dentro (otra pestaña cambia los datos), el foco pasa al título.
    borrar_de(fila_de(p, "C")).tap()
    esperar_toast(p, "Eliminado")
    p.get_by_role("button", name="Deshacer", exact=True).focus()
    otra = e.pagina(contexto=contexto)
    anotar(otra, "Desde otra pestaña")
    esperar_sin_toast(p)
    p.wait_for_timeout(350)
    igual(p.evaluate("() => document.activeElement.id"), "titulo-lista", "Foco tras cerrarse el aviso que lo tenía")


@prueba("estado vacío: «No tienes cosas apuntadas.» centrado y discreto")
def t_lista_vacia(e):
    p = e.pagina()
    abrir_lista(p)
    vacio = p.get_by_text("No tienes cosas apuntadas.", exact=True)
    comprobar(vacio.is_visible(), "No se ve el texto del estado vacío")
    igual(filas(p).count(), 0, "Filas en la lista vacía")
    caja = vacio.evaluate("(el) => { const r = document.createRange(); r.selectNodeContents(el); const b = r.getBoundingClientRect(); return { cx: b.left + b.width / 2, cy: b.top + b.height / 2, fuente: parseFloat(getComputedStyle(el).fontSize) }; }")
    comprobar(abs(caja["cx"] - e.ancho / 2) <= 3, f"El estado vacío no está centrado en horizontal: {caja}")
    comprobar(e.alto * 0.3 <= caja["cy"] <= e.alto * 0.7, f"El estado vacío no está centrado en vertical: {caja}")
    comprobar(caja["fuente"] <= 20, f"El estado vacío no es discreto: {caja['fuente']}px")
    volver(p)
    anotar(p, "Algo")
    abrir_lista(p)
    comprobar(not vacio.is_visible(), "El estado vacío se ve con cosas en la lista")


@prueba("persistencia tras recargar: cosas, hechas, color y nombre")
def t_persistencia(e):
    p = e.pagina()
    for texto in ("Pagar la luz", "Ir al gimnasio"):
        anotar(p, texto)
    abrir_lista(p)
    check_de(fila_de(p, "Pagar la luz")).tap()
    volver(p)
    abrir_ajustes(p)
    elegir_color(p, "Coral")
    p.get_by_label("Nombre", exact=True).fill("Raúl")
    p.get_by_label("Nombre", exact=True).press("Tab")
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).ajustes.nombre === 'Raúl'", arg=CLAVE, que="el nombre se autoguarda")
    p.reload()
    esperar_vista(p, "ajustes")
    esperar_fondo(p, "#EF5B5B")
    igual(p.get_by_label("Nombre", exact=True).input_value(), "Raúl", "Nombre tras recargar")
    comprobar(p.get_by_role("radio", name="Coral", exact=True).is_checked(), "Coral no sigue seleccionado tras recargar")
    igual(p.locator('meta[name="theme-color"]').get_attribute("content").upper(), "#EF5B5B", "theme-color tras recargar")
    p.goto(e.base)
    esperar_fondo(p, "#EF5B5B")
    abrir_lista(p)
    igual(textos_lista(p), ["Ir al gimnasio", "Pagar la luz"], "Cosas tras recargar")
    igual(check_de(fila_de(p, "Pagar la luz")).get_attribute("aria-pressed"), "true", "Hecha tras recargar")
    igual(check_de(fila_de(p, "Ir al gimnasio")).get_attribute("aria-pressed"), "false", "Pendiente tras recargar")
    igual(p.locator("#vista-lista h1").text_content().strip(), "Cosas de Raúl", "Título con nombre tras recargar")
    estado = leer_estado(p)
    igual(estado["ajustes"], {"colorFondo": "#EF5B5B", "nombre": "Raúl"}, "Ajustes guardados")


# ----------------------------------------------------------------------------
# Ajustes
# ----------------------------------------------------------------------------

@prueba("ajustes: estructura, textos y atributos de los campos")
def t_ajustes_estructura(e):
    p = e.pagina()
    abrir_ajustes(p)
    igual(p.locator("#vista-ajustes h1").text_content().strip(), "Ajustes", "Título de ajustes")
    comprobar(p.get_by_role("button", name="Volver", exact=True).is_visible(), "Falta el botón Volver en ajustes")
    igual([h.strip() for h in p.locator("#vista-ajustes h2").all_text_contents()], ["Apariencia", "Datos personales", "Aplicación"], "Secciones de ajustes")
    for texto in ("Color de fondo", "Color personalizado", f"{AYUDA_SESION} {AYUDA_DICTADO}", "Cosas 1.2"):
        comprobar(p.get_by_text(texto, exact=True).count() == 1, f"Falta el texto «{texto}» en ajustes")
    grupo = p.get_by_role("radiogroup")
    igual(grupo.count(), 1, "Número de radiogroup")
    radios = grupo.get_by_role("radio")
    igual(radios.count(), 12, "Número de muestras")
    igual([r.get_attribute("aria-label") or "" for r in radios.all()], [n for n, _ in PALETA], "Nombres de las muestras")
    colores = p.evaluate("() => [...document.querySelectorAll('[role=radiogroup] label')].map((l) => getComputedStyle(l.querySelector('span')).backgroundColor)")
    igual([rgb_css(c) for c in colores], [hex_a_rgb(c) for _, c in PALETA], "Colores de las muestras")
    comprobar(p.get_by_role("radio", name="Azul", exact=True).is_checked(), "Azul (#2F6FED) no es la muestra seleccionada por defecto")
    igual(sum(1 for r in radios.all() if r.is_checked()), 1, "Muestras seleccionadas")
    medidas = p.evaluate("() => [...document.querySelectorAll('[role=radiogroup] label')].map((l) => { const r = l.getBoundingClientRect(); return [r.width, r.height, getComputedStyle(l.querySelector('span')).borderTopLeftRadius]; })")
    comprobar(all(a >= 44 and abs(a - b) < 0.5 and r == "50%" for a, b, r in medidas), f"Las muestras no son redondas de ≥44px: {medidas}")
    igual(p.locator("input[type=color]").count(), 1, "input type=color")
    nombre = p.get_by_label("Nombre", exact=True)
    igual((nombre.get_attribute("autocomplete"), nombre.get_attribute("maxlength")), ("name", "60"), "Atributos de Nombre")
    igual(p.locator("#vista-ajustes input:not([type=color]):not([type=radio])").count(), 1, "Datos personales: solo el nombre (sin correo)")
    # Tras el nombre, el botón de sesión: un enlace a Tu cuenta (en local, sin proxy, en cosas.info).
    sesion = p.get_by_role("link", name="Iniciar sesión", exact=True)
    igual(sesion.count(), 1, "Botón «Iniciar sesión»")
    igual(sesion.get_attribute("href"), "https://cosas.info/cuenta/?desde=app&accion=entrar", "Destino de «Iniciar sesión»")
    igual(sesion.get_attribute("aria-describedby"), "ayuda-datos", "«Iniciar sesión» lleva la ayuda de datos")
    comprobar('href="cuenta/?desde=app&amp;accion=entrar"' in (APP / "index.html").read_text(encoding="utf-8"),
              "En el HTML, «Iniciar sesión» no va a cuenta/ de este dominio")
    caja, campo_caja = sesion.bounding_box(), nombre.bounding_box()
    comprobar(caja["height"] >= 44 and caja["y"] >= campo_caja["y"] + campo_caja["height"] + 12, f"«Iniciar sesión» no va bajo el nombre o es pequeño: {caja} / {campo_caja}")
    # El pie se alcanza desplazando.
    p.get_by_text("Cosas 1.2", exact=True).scroll_into_view_if_needed()
    caja = p.get_by_text("Cosas 1.2", exact=True).bounding_box()
    comprobar(caja["y"] + caja["height"] <= e.alto, f"El pie «Cosas 1.2» queda cortado: {caja}")
    comprobar(p.get_by_text("Cosas 1.2", exact=True).evaluate("(el) => parseFloat(getComputedStyle(el).fontSize)") <= 14, "El pie no es discreto")


@prueba("ajustes: cada muestra cambia el fondo de las 3 vistas y el theme-color")
def t_muestras(e):
    p = e.pagina()
    abrir_ajustes(p)
    for nombre, color in PALETA[1:] + PALETA[:1]:
        elegir_color(p, nombre)
        esperar_fondo(p, color)
        fondos = p.evaluate("() => {" + JS_UTIL + """
            return ['#vista-inicio', '#vista-lista', '#vista-ajustes'].map((s) => redondear(fondoEfectivo(document.querySelector(s)))); }""")
        igual([tuple(f) for f in fondos], [hex_a_rgb(color)] * 3, f"Fondo efectivo de las tres vistas con {nombre}")
        igual(p.locator('meta[name="theme-color"]').get_attribute("content").upper(), color, f"theme-color con {nombre}")
        igual(leer_estado(p)["ajustes"]["colorFondo"].upper(), color, f"Color guardado con {nombre}")
        marcas = p.evaluate("() => [...document.querySelectorAll('[role=radiogroup] input')].map((r) => r.checked)")
        igual(marcas, [n == nombre for n, _ in PALETA], f"Selección única con {nombre}")
    # La seleccionada enseña anillo + check; las demás no.
    p.wait_for_timeout(300)
    marcas = p.evaluate("""() => [...document.querySelectorAll('[role=radiogroup] label')].map((l) => ({
        check: parseFloat(getComputedStyle(l.querySelector('svg')).opacity), sombra: getComputedStyle(l.querySelector('span')).boxShadow }))""")
    igual([m["check"] for m in marcas], [1.0] + [0.0] * 11, "Check visible solo en la muestra seleccionada")
    comprobar(marcas[0]["sombra"] != marcas[1]["sombra"] and marcas[0]["sombra"].count("px") > marcas[1]["sombra"].count("px"), "La muestra seleccionada no enseña un anillo")


@prueba("ajustes: muestras manejables con teclado")
def t_muestras_teclado(e):
    p = e.pagina()
    abrir_ajustes(p)
    p.get_by_role("radio", name="Azul", exact=True).focus()
    p.keyboard.press("ArrowRight")
    comprobar(p.get_by_role("radio", name="Azul noche", exact=True).is_checked(), "Flecha derecha no pasa a «Azul noche»")
    esperar_fondo(p, "#1E2A44")
    p.keyboard.press("ArrowLeft")
    esperar_fondo(p, "#2F6FED")
    p.keyboard.press("ArrowLeft")
    esperar_fondo(p, "#111111")
    anillo = p.evaluate("() => { const s = document.activeElement.parentElement.querySelector('span'); const cs = getComputedStyle(s); return { estilo: cs.outlineStyle, ancho: parseFloat(cs.outlineWidth) }; }")
    comprobar(anillo["estilo"] != "none" and anillo["ancho"] >= 2, f"La muestra con foco de teclado no enseña anillo: {anillo}")


@prueba("fondo plano a pantalla completa en las 3 vistas (píxeles reales)")
def t_fondo_pixeles(e):
    for nombre, color in (("Azul", "#2F6FED"), ("Blanco", "#FFFFFF"), ("Negro", "#111111"), ("Amarillo", "#F2B705")):
        p = sembrar(e, estado_con(color=color), contexto=e.contexto(reduced_motion="reduce"))
        ancho, alto = e.ancho, e.alto
        puntos = [(2, 2), (ancho - 4, 2), (ancho // 2, 1), (ancho // 2, alto // 2), (2, alto // 2),
                  (ancho - 4, alto // 2), (2, alto - 4), (ancho - 4, alto - 4), (ancho // 2, alto - 3), (ancho // 2, 120)]
        igual(set(muestrear(p, puntos)), {hex_a_rgb(color)}, f"Píxeles del fondo de inicio con {nombre}")
        esquinas = [(2, alto - 4), (ancho - 4, alto - 4), (2, alto // 2), (ancho - 3, 2), (ancho // 2, 1)]
        abrir_lista(p)
        p.wait_for_timeout(100)
        igual(set(muestrear(p, esquinas)), {hex_a_rgb(color)}, f"Píxeles del fondo de la lista con {nombre}")
        volver(p)
        abrir_ajustes(p)
        p.wait_for_timeout(100)
        igual(set(muestrear(p, [(2, alto - 4), (ancho - 3, 2), (ancho // 2, 1), (1, alto // 2)])), {hex_a_rgb(color)}, f"Píxeles del fondo de ajustes con {nombre}")
        p.context.close()


@prueba("ajustes: color personalizado (input type=color)")
def t_color_personalizado(e):
    p = e.pagina()
    abrir_ajustes(p)
    selector = p.locator("input[type=color]")
    caja = selector.bounding_box()
    comprobar(caja["width"] >= 44 and caja["height"] >= 44, f"El selector de color mide menos de 44px: {caja}")
    selector.fill("#808080")
    esperar_fondo(p, "#808080")
    igual(p.locator('meta[name="theme-color"]').get_attribute("content").upper(), "#808080", "theme-color con color personalizado")
    igual(leer_estado(p)["ajustes"]["colorFondo"].upper(), "#808080", "Color personalizado guardado")
    igual(p.evaluate("() => [...document.querySelectorAll('[role=radiogroup] input')].filter((r) => r.checked).length"), 0, "Muestras seleccionadas con un color fuera de la paleta")
    p.reload()
    esperar_fondo(p, "#808080")
    igual(selector.input_value().lower(), "#808080", "Valor del selector tras recargar")
    # Elegir con el selector un color de la paleta marca su muestra.
    selector.fill("#1ba39c")
    esperar_fondo(p, "#1BA39C")
    comprobar(p.get_by_role("radio", name="Turquesa", exact=True).is_checked(), "Turquesa no se marca al elegir su color con el selector")


JS_CONTRASTES = "() => {" + JS_UTIL + r"""
  const q = (s) => document.querySelector(s);
  const svg = (s) => q(s + ' svg') || q(s);
  const seleccionada = [...document.querySelectorAll('[role=radiogroup] label')].find((l) => l.querySelector('input').checked);
  const toastEl = q('[role="status"]');
  return {
    tono: document.documentElement.dataset.tono,
    texto: {
      'texto de la cosa pendiente': contrasteTexto(q('#lista-cosas > li:not(.hecha) p')),
      'título de la lista': contrasteTexto(q('#vista-lista h1')),
      'título de ajustes': contrasteTexto(q('#vista-ajustes h1')),
      'etiqueta «Color de fondo»': contrasteTexto(q('#etiqueta-color')),
      'etiqueta «Nombre»': contrasteTexto(q('label[for=nombre]')),
      'texto del campo de escribir': contrasteTexto(q('#campo')),
      'texto del campo Nombre': contrasteTexto(q('#nombre')),
      'texto del aviso': contraste(parsear(getComputedStyle(toastEl).color), parsear(getComputedStyle(toastEl).backgroundColor)),
      // Textos pequeños que van directamente sobre el fondo: también necesitan 4,5:1.
      'estado vacío': contrasteTexto(q('#vacio')),
      'ayuda de datos': contrasteTexto(q('#ayuda-datos')),
      'pie «Cosas 1.2»': contrasteTexto(q('.pie')),
      'subtítulo de sección': contrasteTexto(q('.subtitulo')),
      'botón «Convertir en aplicación»': contrasteTexto(q('#instalar')),
      'botón «Compartir aplicación»': contrasteTexto(q('#compartir')),
      'botón «Iniciar sesión»': contrasteTexto(q('#sesion')),
      'ayuda de «Aplicación»': contrasteTexto(q('#ayuda-instalar')),
    },
    iconos: {
      'icono del botón de lista': contrasteIcono(svg('#abrir-lista')),
      'icono del botón de ajustes': contrasteIcono(svg('#abrir-ajustes')),
      'icono Volver (lista)': contrasteIcono(svg('#vista-lista [data-volver]')),
      'icono Volver (ajustes)': contrasteIcono(svg('#vista-ajustes [data-volver]')),
      'icono Eliminar': contrasteIcono(q('#lista-cosas > li [aria-label=Eliminar] svg')),
      'icono de enviar': contrasteIcono(svg('#enviar')),
      'icono del micrófono (en reposo)': contrasteIcono(svg('#dictar')),
      'check de la muestra seleccionada': seleccionada ? contrasteIcono(seleccionada.querySelector('svg')) : 99,
    },
    secundario: {
      'placeholder': contrasteTexto(q('#campo'), '::placeholder'),
    },
    grisCheck: contrasteIcono(q('#lista-cosas > li:not(.hecha) button svg')),
  };
}"""


def medir_contrastes(e, colores, elegir, minimo_texto=4.5):
    p = sembrar(e, estado_con([cosa(2, "Pendiente"), cosa(1, "Hecha", hecha=True)]))
    abrir_lista(p)
    volver(p)
    abrir_ajustes(p)
    fallos, tonos = [], {}
    for nombre, color in colores:
        elegir(p, nombre, color)
        esperar_fondo(p, color)
        p.wait_for_timeout(450)  # Fin de las transiciones de color de superficies e iconos.
        medidas = p.evaluate(JS_CONTRASTES)
        tonos[nombre] = medidas["tono"]
        for grupo, minimo in (("texto", minimo_texto), ("iconos", 3.0), ("secundario", 3.0)):
            for que, valor in medidas[grupo].items():
                detalle(f"{nombre} {color} · {que}: {valor:.2f}")
                if valor < minimo:
                    fallos.append(f"{nombre} {color}: {que} = {valor:.2f}:1 (mínimo {minimo})")
    return fallos, tonos, p


@prueba("contraste adaptable: texto ≥ 4,5 e iconos ≥ 3 en las 12 muestras")
def t_contraste_muestras(e):
    fallos, tonos, _ = medir_contrastes(e, PALETA, lambda p, nombre, color: elegir_color(p, nombre))
    esperados = {"Azul": "oscuro", "Azul noche": "oscuro", "Morado": "oscuro", "Negro": "oscuro",
                 "Amarillo": "claro", "Arena": "claro", "Blanco": "claro"}
    for nombre, tono in esperados.items():
        igual(tonos[nombre], tono, f"data-tono con el fondo {nombre}")
    comprobar(not fallos, "Contraste insuficiente:\n          " + "\n          ".join(fallos))


@prueba("contraste adaptable: colores personalizados difíciles")
def t_contraste_personalizados(e):
    dificiles = [("blanco", "#FFFFFF"), ("negro", "#000000"), ("amarillo puro", "#FFFF00"), ("gris medio", "#808080"),
                 ("gris 777", "#777777"), ("gris 6E", "#6E6E6E"), ("gris 8C", "#8C8C8C"), ("rojo", "#FF0000"),
                 ("verde lima", "#00FF00"), ("azul puro", "#0000FF"), ("cian", "#00FFFF"), ("magenta", "#FF00FF")]
    # Con tinta «casi negra» (#0A0A0A, la que pide la especificación) el máximo teórico en la franja más
    # desfavorable de grises medios es 4,45:1; por eso aquí el umbral de texto es 4,4 y no 4,5.
    fallos, _, _ = medir_contrastes(e, dificiles, lambda p, nombre, color: p.locator("input[type=color]").fill(color.lower()), minimo_texto=4.4)
    comprobar(not fallos, "Contraste insuficiente:\n          " + "\n          ".join(fallos))


@prueba("contraste del check gris pendiente ≥ 3 (icono sobre su superficie)")
def t_contraste_check_gris(e):
    p = sembrar(e, estado_con([cosa(1, "Pendiente")]), ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
    esperar_filas(p, 1)
    medidas = p.evaluate(JS_CONTRASTES)
    colores = colores_check(check_de(filas(p).first))
    comprobar(medidas["grisCheck"] >= 3.0,
              f"El check gris pendiente tiene contraste {medidas['grisCheck']:.2f}:1 sobre su superficie "
              f"(icono {colores['icono']} sobre {colores['fondo']}); mínimo 3:1 para iconos")


@prueba("el tono del primer plano cambia entre fondo claro y oscuro")
def t_tono(e):
    p = e.pagina()
    abrir_ajustes(p)
    medidas = {}
    for nombre, color in (("Blanco", "#FFFFFF"), ("Negro", "#111111")):
        elegir_color(p, nombre)
        esperar_fondo(p, color)
        p.wait_for_timeout(450)
        medidas[nombre] = p.evaluate("""() => ({ tono: document.documentElement.dataset.tono,
            titulo: getComputedStyle(document.querySelector('#vista-ajustes h1')).color,
            icono: getComputedStyle(document.querySelector('#abrir-lista svg')).stroke,
            campo: getComputedStyle(document.querySelector('#campo')).color })""")
    igual(medidas["Blanco"]["tono"], "claro", "data-tono con fondo blanco")
    igual(medidas["Negro"]["tono"], "oscuro", "data-tono con fondo negro")
    for que in ("titulo", "icono", "campo"):
        comprobar(max(rgb_css(medidas["Blanco"][que])) <= 40, f"{que} no es casi negro sobre blanco: {medidas['Blanco'][que]}")
        igual(rgb_css(medidas["Negro"][que]), (255, 255, 255), f"{que} sobre negro")


@prueba("datos personales: autoguardado con aviso discreto y «Cosas de NOMBRE»")
def t_datos_personales(e):
    p = e.pagina()
    abrir_ajustes(p)
    nombre = p.get_by_label("Nombre", exact=True)
    nombre.tap()
    p.keyboard.type("  Raúl  ")
    esperar_toast(p, "Guardado", ms=2500)  # Guardado con retardo, sin salir del campo.
    comprobar(p.evaluate("() => document.activeElement.id === 'nombre'"), "El autoguardado quita el foco del campo")
    igual(leer_estado(p)["ajustes"]["nombre"], "Raúl", "Nombre autoguardado (recortado)")
    esperar_sin_toast(p)
    nombre.fill("Raúl García")
    nombre.evaluate("(c) => c.blur()")
    p.wait_for_timeout(150)
    igual(leer_estado(p)["ajustes"]["nombre"], "Raúl García", "Nombre guardado al salir del campo (sin esperar al retardo)")
    esperar_toast(p, "Guardado")
    # Intro en un campo no envía ni recarga nada.
    nombre.press("Enter")
    p.wait_for_timeout(200)
    comprobar(p.url.endswith("#ajustes") and "?" not in p.url, f"Intro en Nombre cambió la URL: {p.url}")
    volver(p)
    abrir_lista(p)
    igual(p.locator("#vista-lista h1").text_content().strip(), "Cosas de Raúl García", "Título de la lista con nombre")
    volver(p)
    # Escribir y volver enseguida (antes del retardo) también guarda.
    abrir_ajustes(p)
    nombre.fill("")
    volver(p)
    igual(leer_estado(p)["ajustes"]["nombre"], "", "Nombre vaciado y guardado al volver enseguida")
    abrir_lista(p)
    igual(p.locator("#vista-lista h1").text_content().strip(), "Cosas", "Título de la lista sin nombre")
    volver(p)
    # Los límites de longitud también se aplican a lo que se guarda.
    abrir_ajustes(p)
    nombre.evaluate("(c) => { c.value = 'N'.repeat(80); c.dispatchEvent(new Event('input', { bubbles: true })); }")
    volver(p)
    guardado = leer_estado(p)["ajustes"]
    comprobar(len(guardado["nombre"]) <= 60, f"Se guarda un nombre más largo que el máximo: {len(guardado['nombre'])}")


@prueba("datos personales: sobreviven a una sesión posterior en la que no se abre Ajustes (y el correo de la 1.2 se olvida)")
def t_datos_sobreviven(e):
    p = sembrar(e, estado_con([cosa(1, "Algo")], color="#7A4FD6", nombre="Raúl", correo="raul@example.com"))
    esperar_vista(p, "inicio")
    anotar(p, "Otra cosa")  # Una sesión normal: se apunta algo y se cierra/recarga sin pasar por Ajustes.
    esperar_toast(p, "Guardado")
    p.reload()
    esperar_vista(p, "inicio")
    ajustes = leer_estado(p)["ajustes"]
    igual(ajustes, {"colorFondo": "#7A4FD6", "nombre": "Raúl"},
          "Ajustes guardados tras recargar una sesión en la que no se abrió Ajustes (sin el correo que guardaba la 1.2)")
    abrir_lista(p)
    igual(p.locator("#vista-lista h1").text_content().strip(), "Cosas de Raúl", "Título de la lista tras esa recarga")


@prueba("sesión: sin cuenta «Iniciar sesión»; con cuenta (cosascon:cuenta) «Cerrar sesión» y quién; valores raros se ignoran")
def t_sesion(e):
    con_cuenta = "tus cosas, tus grupos, tu nombre y tu color te siguen a cualquier dispositivo, igual que tus listas de Cosas con y Cosas de."
    casos = [
        (None, "Iniciar sesión", "entrar", AYUDA_SESION),
        (json.dumps({"nombre": "Raúl García", "correo": "raul@example.com"}), "Cerrar sesión", "salir",
         f"Has iniciado sesión como Raúl García (raul@example.com): {con_cuenta}"),
        (json.dumps({"nombre": "", "correo": "raul@example.com"}), "Cerrar sesión", "salir",
         f"Has iniciado sesión como raul@example.com: {con_cuenta}"),
        ("{roto", "Iniciar sesión", "entrar", AYUDA_SESION),
        (json.dumps({"nombre": 5, "correo": ["x"]}), "Iniciar sesión", "entrar", AYUDA_SESION),
        (json.dumps("texto"), "Iniciar sesión", "entrar", AYUDA_SESION),
    ]
    for bruto, texto, accion, ayuda in casos:
        contexto = e.contexto()
        p = e.vigilar(contexto.new_page())
        p.goto(e.base + "icons/favicon-32.png")
        if bruto is not None:
            p.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE_CUENTA, bruto])
        p.goto(e.base + "#ajustes")
        esperar_vista(p, "ajustes")
        sesion = p.locator("#sesion")
        igual(sesion.text_content().strip(), texto, f"[{bruto}] texto del botón")
        igual(sesion.get_attribute("href"), f"https://cosas.info/cuenta/?desde=app&accion={accion}", f"[{bruto}] destino (en local, sin proxy)")
        igual(p.locator("#ayuda-sesion").text_content().strip(), ayuda, f"[{bruto}] ayuda de la sesión")
        contexto.close()
    # Si otra pestaña (Tu cuenta, Cosas con) inicia sesión, los ajustes abiertos lo reflejan (evento storage).
    contexto = e.contexto()
    a = e.pagina(ruta="#ajustes", contexto=contexto)
    esperar_vista(a, "ajustes")
    b = e.pagina(contexto=contexto)
    b.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE_CUENTA, json.dumps({"nombre": "Raúl", "correo": ""})])
    esperar(a, "() => document.querySelector('#sesion').textContent.trim() === 'Cerrar sesión'", que="los ajustes abiertos ven la sesión nueva")
    contexto.close()


# ----------------------------------------------------------------------------
# Navegación e historial
# ----------------------------------------------------------------------------

@prueba("historial: atrás del sistema vuelve a inicio desde lista y ajustes")
def t_historial_atras(e):
    p = e.pagina()
    for abrir, nombre, boton in ((abrir_lista, "lista", boton_lista), (abrir_ajustes, "ajustes", boton_ajustes)):
        abrir(p)
        comprobar(p.url.endswith(f"#{nombre}"), f"La URL no lleva #{nombre}: {p.url}")
        dentro = p.evaluate("(id) => document.querySelector(id).contains(document.activeElement)", f"#vista-{nombre}")
        comprobar(dentro, f"Al abrir «{nombre}» el foco no entra en la vista")
        p.go_back()
        esperar_vista(p, "inicio")
        comprobar(p.url.startswith(e.base) and "#" not in p.url.rstrip("#"), f"URL tras volver: {p.url}")
        igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), boton(p).get_attribute("aria-label"), f"Foco al volver de «{nombre}»")
        p.go_forward()
        esperar_vista(p, nombre)
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "inicio")
        igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), boton(p).get_attribute("aria-label"), f"Foco al volver de «{nombre}» con el botón")
    # Un solo «atrás» desde inicio sale de la app: no hay entradas apiladas.
    p.go_back()
    igual(p.url, "about:blank", "URL tras un «atrás» más desde inicio")


@prueba("historial: carga directa de #lista y #ajustes; atrás vuelve a inicio")
def t_carga_directa(e):
    for nombre in ("lista", "ajustes"):
        p = sembrar(e, estado_con([cosa(1, "Directa")]), ruta=f"#{nombre}")
        esperar_vista(p, nombre)
        igual(p.evaluate("() => document.activeElement.id"), f"titulo-{nombre}", f"Foco al cargar directamente #{nombre} (título de la vista)")
        if nombre == "lista":
            igual(textos_lista(p), ["Directa"], "Lista en carga directa")
        p.go_back()
        esperar_vista(p, "inicio")
        comprobar(p.url.startswith(e.base), f"«Atrás» sacó de la app: {p.url}")
        p.context.close()
        p = e.pagina(ruta=f"#{nombre}")
        esperar_vista(p, nombre)
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "inicio")
        comprobar(p.url.startswith(e.base) and not p.url.endswith(f"#{nombre}"), f"URL tras Volver: {p.url}")
        abrir_lista(p)
        p.reload()
        esperar_vista(p, "lista")
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "inicio")
        abrir_ajustes(p)
        p.reload()
        esperar_vista(p, "ajustes")
        p.go_back()
        esperar_vista(p, "inicio")
        p.context.close()
    p = e.pagina(ruta="#noexiste")
    esperar_vista(p, "inicio")


@prueba("historial: 10 ciclos abrir/cerrar no hacen crecer history.length")
def t_historial_ciclos(e):
    p = e.pagina()
    antes = p.evaluate("() => history.length")
    for i in range(10):
        abrir_lista(p)
        if i % 2:
            p.go_back()
            esperar_vista(p, "inicio")
        else:
            volver(p)
        abrir_ajustes(p)
        volver(p)
    despues = p.evaluate("() => history.length")
    detalle(f"history.length: {antes} -> {despues}")
    comprobar(despues - antes <= 1, f"history.length crece de {antes} a {despues} tras 20 ciclos")
    p.go_back()
    igual(p.url, "about:blank", "Un «atrás» desde inicio tras 20 ciclos")


def doble_toque(pagina, localizador, pausa):
    caja = localizador.bounding_box()
    x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
    pagina.touchscreen.tap(x, y)
    if pausa:
        pagina.wait_for_timeout(pausa)
    pagina.touchscreen.tap(x, y)


def vista_visible(pagina):
    return pagina.evaluate("() => ['inicio', 'lista', 'ajustes', 'grupo'].filter((v) => document.querySelector('#vista-' + v).checkVisibility()).join('+')")


@prueba("doble toque en el botón de ajustes: ajustes queda abierto y el historial solo crece en 1")
def t_doble_toque_ajustes(e):
    for pausa in (0, 150, 250):
        p = e.pagina()
        antes = p.evaluate("() => history.length")
        doble_toque(p, boton_ajustes(p), pausa)
        p.wait_for_timeout(700)
        igual(vista_visible(p), "ajustes", f"Vista tras un doble toque ({pausa} ms) en el botón de ajustes")
        igual(p.evaluate("() => history.length") - antes, 1, "Entradas de historial añadidas por el doble toque")
        p.context.close()


@prueba("doble toque en Volver: se queda en inicio (no reabre nada ni sale de la app)")
def t_doble_toque_volver(e):
    for origen, abrir in (("ajustes", abrir_ajustes), ("lista", abrir_lista)):
        for pausa in (0, 150):
            p = e.pagina()
            abrir(p)
            p.wait_for_timeout(400)
            doble_toque(p, p.get_by_role("button", name="Volver", exact=True), pausa)
            p.wait_for_timeout(800)
            comprobar(p.url.startswith(e.base), f"El doble toque en Volver sacó de la app: {p.url}")
            igual(vista_visible(p), "inicio", f"Vista tras un doble toque ({pausa} ms entre toques) en Volver desde «{origen}»")
            p.context.close()


@prueba("doble toque en el botón de lista: la lista queda abierta")
def t_doble_toque_lista(e):
    for pausa in (0, 150, 250):
        p = e.pagina()
        antes = p.evaluate("() => history.length")
        doble_toque(p, boton_lista(p), pausa)
        p.wait_for_timeout(800)
        igual(vista_visible(p), "lista", f"Vista tras un doble toque ({pausa} ms entre toques) en el botón de lista")
        igual(p.evaluate("() => history.length") - antes, 1, "Entradas de historial añadidas por el doble toque")
        p.context.close()


@prueba("vistas ocultas: hidden/inert y fuera del tabulador; foco visible")
def t_vistas_ocultas(e):
    p = e.pagina()
    for actual, abrir in (("inicio", None), ("lista", abrir_lista), ("ajustes", abrir_ajustes)):
        if abrir:
            abrir(p)
        datos = p.evaluate("""() => ['inicio', 'lista', 'ajustes'].map((v) => { const el = document.querySelector('#vista-' + v);
            return { vista: v, oculta: el.hidden || el.hasAttribute('inert'), visible: el.checkVisibility() }; })""")
        for d in datos:
            igual(d["visible"], d["vista"] == actual, f"Visibilidad de «{d['vista']}» estando en «{actual}»")
            if d["vista"] != actual:
                comprobar(d["oculta"], f"La vista «{d['vista']}» no lleva hidden/inert estando en «{actual}»")
        visitadas = set()
        for _ in range(12):
            p.keyboard.press("Tab")
            visitadas.add(p.evaluate("() => { const s = document.activeElement.closest('section[id^=vista-]'); return s ? s.id : 'fuera'; }"))
        comprobar(visitadas <= {f"vista-{actual}", "fuera"}, f"El tabulador entra en vistas ocultas desde «{actual}»: {visitadas}")
        if abrir:
            volver(p)
    p2 = e.pagina()
    for esperado in ("Ver lista de cosas", "Ajustes"):
        p2.keyboard.press("Tab")
        anillo = p2.evaluate("() => { const cs = getComputedStyle(document.activeElement); return { quien: document.activeElement.getAttribute('aria-label'), estilo: cs.outlineStyle, ancho: parseFloat(cs.outlineWidth) }; }")
        igual(anillo["quien"], esperado, "Orden de tabulación en inicio")
        comprobar(anillo["estilo"] != "none" and anillo["ancho"] >= 2, f"Sin anillo :focus-visible con teclado: {anillo}")
    sin_foco = p2.evaluate("() => getComputedStyle(document.querySelector('#formulario .pildora')).boxShadow")
    p2.keyboard.press("Tab")
    igual(p2.evaluate("() => document.activeElement.id"), "campo", "Tercer elemento al tabular")
    p2.wait_for_timeout(250)
    con_foco = p2.evaluate("() => getComputedStyle(document.activeElement).outlineStyle !== 'none' ? 'outline' : getComputedStyle(document.querySelector('#formulario .pildora')).boxShadow")
    comprobar(con_foco != sin_foco, "La barra no enseña ningún indicador de foco")


@prueba("prefers-reduced-motion: sin animaciones y todo sigue funcionando")
def t_menos_movimiento(e):
    p = sembrar(e, estado_con([cosa(2, "B"), cosa(1, "A")]), contexto=e.contexto(reduced_motion="reduce"))
    abrir_lista(p)
    duracion = p.evaluate("() => Math.max(...getComputedStyle(document.querySelector('#vista-lista')).animationDuration.split(',').map(parseFloat))")
    comprobar(duracion <= 0.01, f"La transición de vista dura {duracion}s con «reducir movimiento»")
    borrar_de(fila_de(p, "B")).tap()
    esperar_filas(p, 1, ms=300)
    esperar_toast(p, "Eliminado")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 2)
    igual(textos_lista(p), ["B", "A"], "Lista tras deshacer con menos movimiento")


# ----------------------------------------------------------------------------
# Robustez del contenido
# ----------------------------------------------------------------------------

XSS = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script><b>negrita</b>'


@prueba("XSS: el texto del usuario se pinta literal y no crea elementos")
def t_xss(e):
    p = e.pagina()
    anotar(p, XSS)
    abrir_lista(p)
    igual(textos_lista(p), [XSS], "Texto literal en la lista")
    igual(p.evaluate("() => document.querySelectorAll('#lista-cosas img, #lista-cosas script, #lista-cosas b').length"), 0, "Elementos creados por el texto")
    igual(p.evaluate("() => document.querySelector('#lista-cosas > li p').childElementCount"), 0, "Hijos elemento dentro del texto")
    volver(p)
    abrir_ajustes(p)
    p.get_by_label("Nombre", exact=True).fill("<img src=x onerror=window.__xss=3>")
    volver(p)
    abrir_lista(p)
    igual(p.locator("#vista-lista h1").text_content(), "Cosas de <img src=x onerror=window.__xss=3>", "Título literal")
    igual(p.locator("#vista-lista h1").evaluate("(h) => h.childElementCount"), 0, "Elementos dentro del título")
    borrar_de(filas(p).first).tap()
    esperar_toast(p, "Eliminado")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 1)
    p.reload()
    esperar_filas(p, 1)
    p.wait_for_timeout(200)
    igual(p.evaluate("() => document.querySelectorAll('main img, main script, main b').length"), 0, "Elementos inyectados tras recargar")
    igual(p.evaluate("() => window.__xss"), None, "Se ejecutó código inyectado")


JS_DESBORDES = r"""
() => {
  const vista = [...document.querySelectorAll('section[id^=vista-]')].find((s) => s.checkVisibility());
  const fuera = [];
  for (const el of vista.querySelectorAll('*')) {
    if (!el.checkVisibility()) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > innerWidth + 0.5 || r.left < -0.5) fuera.push(`${el.tagName.toLowerCase()}.${el.className} [${r.left.toFixed(1)}, ${r.right.toFixed(1)}]`);
  }
  const con = (el) => el.scrollWidth - el.clientWidth;
  const zona = vista.querySelector('.desplazable');
  return { fuera: fuera.slice(0, 5), html: con(document.documentElement), body: con(document.body),
           vista: con(vista), zona: zona ? con(zona) : 0, scrollX: window.scrollX };
}
"""


def comprobar_sin_desborde(pagina, donde):
    datos = pagina.evaluate(JS_DESBORDES)
    igual(datos["fuera"], [], f"Elementos que se salen de la pantalla en {donde}")
    comprobar(datos["html"] <= 0 and datos["body"] <= 0 and datos["vista"] <= 0 and datos["zona"] <= 0 and datos["scrollX"] == 0,
              f"Hay desplazamiento horizontal en {donde}: {datos}")


@prueba("textos largos: 500 caracteres y palabra de 200 sin desbordar la fila")
def t_textos_largos(e):
    largo = ("Lorem ipsum dolor sit amet " * 30)[:500].strip()
    palabra = "A" * 200
    p = e.pagina()
    anotar(p, largo)
    anotar(p, palabra)
    campo(p).fill("x" * 600)
    campo(p).press("Enter")
    guardadas = leer_estado(p)["cosas"]
    comprobar(all(len(c["texto"]) <= 500 for c in guardadas), f"Se guardó un texto de más de 500 caracteres: {[len(c['texto']) for c in guardadas]}")
    abrir_lista(p)
    igual(textos_lista(p)[1:], [palabra, largo], "Textos largos en la lista")
    comprobar_sin_desborde(p, "la lista con textos largos")
    for fila in filas(p).all():
        datos = fila.evaluate("""(f) => { const r = f.getBoundingClientRect(), t = f.querySelector('p').getBoundingClientRect(), b = f.querySelector('[aria-label=Eliminar]').getBoundingClientRect();
            const rango = document.createRange(); rango.selectNodeContents(f.querySelector('p')); const rt = rango.getBoundingClientRect();
            return { desborde: f.scrollWidth - f.clientWidth, fila: [r.left, r.right], texto: [t.left, t.right], tinta: [rt.left, rt.right], borrar: [b.left, b.right], ancho: b.width }; }""")
        comprobar(datos["desborde"] <= 0, f"La fila desborda en horizontal: {datos}")
        comprobar(datos["tinta"][1] <= datos["borrar"][0] + 0.5 and datos["texto"][1] <= datos["borrar"][0] + 0.5, f"El texto pisa el botón de eliminar: {datos}")
        comprobar(datos["borrar"][1] <= datos["fila"][1] + 0.5 and datos["ancho"] >= 44, f"El botón eliminar se sale o se encoge: {datos}")
    estilo = filas(p).first.locator("p").evaluate("(t) => getComputedStyle(t).overflowWrap")
    igual(estilo, "anywhere", "overflow-wrap del texto")


@prueba("sin desplazamiento horizontal en ninguna vista (también a 320px y con nombre largo)")
def t_sin_scroll_horizontal(e):
    datos = estado_con([cosa(2, "Normal"), cosa(1, "B" * 200, hecha=True)], nombre="Maximiliano Alejandro de Todos los Santos Fernández-Villaverde"[:60])
    for ancho in (e.ancho, 320):
        contexto = e.contexto(viewport={"width": ancho, "height": e.alto if ancho != 320 else 568})
        p = sembrar(e, datos, contexto=contexto)
        comprobar_sin_desborde(p, f"inicio a {ancho}px")
        campo(p).fill("Texto bastante largo para comprobar que la barra no crece " * 3)
        comprobar_sin_desborde(p, f"inicio con texto largo a {ancho}px")
        campo(p).fill("")
        abrir_lista(p)
        comprobar_sin_desborde(p, f"lista a {ancho}px")
        titulo = p.locator("#vista-lista h1").bounding_box()
        comprobar(titulo["x"] >= 0 and titulo["x"] + titulo["width"] <= ancho, f"El título largo se sale a {ancho}px: {titulo}")
        borrar_de(filas(p).first).tap()
        esperar_toast(p, "Eliminado")
        aviso = toast(p)
        comprobar(aviso["x"] >= 0 and aviso["x"] + aviso["ancho"] <= ancho, f"El aviso «Eliminado» se sale a {ancho}px: {aviso}")
        volver(p)
        abrir_ajustes(p)
        comprobar_sin_desborde(p, f"ajustes a {ancho}px")
        igual(p.get_by_role("radio").count(), 12, f"Muestras a {ancho}px")
        cajas = [r.bounding_box() for r in p.get_by_role("radio").all()]
        comprobar(all(c["x"] >= 0 and c["x"] + c["width"] <= ancho for c in cajas), f"Alguna muestra se sale a {ancho}px")
        contexto.close()


@prueba("apaisado: los elementos de inicio caben y ajustes/lista se pueden recorrer")
def t_apaisado(e):
    ancho, alto = e.alto, e.ancho
    contexto = e.contexto(viewport={"width": ancho, "height": alto})
    p = sembrar(e, estado_con([cosa(i, f"Cosa {i}") for i in range(1, 13)]), contexto=contexto)
    lista, ajustes, pildora = boton_lista(p).bounding_box(), boton_ajustes(p).bounding_box(), p.locator("#formulario .pildora").bounding_box()
    comprobar(lista["y"] + lista["height"] <= pildora["y"], f"En apaisado la barra pisa los botones: {lista} {pildora}")
    nav = accesos(p).bounding_box()
    comprobar(lista["y"] + lista["height"] <= nav["y"] and nav["y"] + nav["height"] <= pildora["y"], f"En apaisado los accesos pisan los botones o la barra: {nav}")
    comprobar(ajustes["x"] + ajustes["width"] <= ancho and pildora["y"] + pildora["height"] <= alto, "En apaisado algo se sale de la pantalla")
    comprobar_sin_desborde(p, "inicio apaisado")
    abrir_lista(p)
    filas(p).last.scroll_into_view_if_needed()
    caja = filas(p).last.bounding_box()
    comprobar(caja["y"] + caja["height"] <= alto + 0.5, f"La última fila queda cortada en apaisado: {caja}")
    comprobar_sin_desborde(p, "lista apaisada")
    volver(p)
    abrir_ajustes(p)
    pie = p.get_by_text("Cosas 1.2", exact=True)
    pie.scroll_into_view_if_needed()
    caja = pie.bounding_box()
    comprobar(caja["y"] + caja["height"] <= alto + 0.5, f"El pie no se alcanza en apaisado: {caja}")
    comprobar_sin_desborde(p, "ajustes apaisado")


CORRUPTOS = [
    ("JSON inválido", "{{{esto no es json", 0),
    ("cadena vacía", "", 0),
    ("null", "null", 0),
    ("array", "[]", 0),
    ("array con cosas", '[{"id":"a","texto":"x"}]', 0),
    ("número", "12345", 0),
    ("cadena", '"hola"', 0),
    ("true", "true", 0),
    ("tipos equivocados", '{"version":1,"cosas":"nope","ajustes":42}', 0),
    ("cosas como objeto", '{"version":"uno","cosas":{"0":{"texto":"x"}},"ajustes":[]}', 0),
    ("ajustes null", '{"version":1,"cosas":[],"ajustes":null}', 0),
    ("mezcla", json.dumps({"version": 1, "cosas": [1, None, "x", [], {"texto": 5}, {"id": "a", "texto": "   "},
                                                  {"id": "a", "texto": "Buena", "hecha": "sí", "creada": "ayer"},
                                                  {"id": "a", "texto": "Duplicada", "hecha": True, "creada": 5},
                                                  {"id": 7, "texto": "Id numérico", "creada": 3}],
                           "ajustes": {"colorFondo": "rojo", "nombre": 123, "correo": None}}), 3),
    ("color peligroso", json.dumps({"version": 1, "cosas": [], "ajustes": {"colorFondo": "#FFF;} body{display:none", "nombre": {"a": 1}, "correo": ["x"]}}), 0),
]


@prueba("localStorage corrupto o inesperado no rompe la app")
def t_almacenamiento_corrupto(e):
    for nombre, bruto, validas in CORRUPTOS:
        p = sembrar(e, bruto)
        esperar_vista(p, "inicio")
        esperar_fondo(p, COLOR_DEFECTO)
        igual(p.evaluate("() => document.documentElement.dataset.tono"), "oscuro", f"[{nombre}] tono por defecto")
        anotar(p, "Nueva tras corrupción")
        esperar_toast(p, "Guardado")
        abrir_lista(p)
        esperar_filas(p, validas + 1)
        textos = textos_lista(p)
        igual(textos[0], "Nueva tras corrupción", f"[{nombre}] la nueva va primero")
        if nombre == "mezcla":
            igual(sorted(textos[1:]), ["Buena", "Duplicada", "Id numérico"], "[mezcla] se conservan solo las cosas válidas")
            ids = [c["id"] for c in leer_estado(p)["cosas"]]
            comprobar(len(set(ids)) == len(ids) and all(isinstance(i, str) and i for i in ids), f"[mezcla] ids repetidos o inválidos: {ids}")
            igual(check_de(fila_de(p, "Buena")).get_attribute("aria-pressed"), "false", "[mezcla] hecha no booleana se trata como pendiente")
        nueva = fila_de(p, "Nueva tras corrupción")
        check_de(nueva).tap()  # Al marcarla baja al final de la lista (1.2): se mira su propio check.
        esperar(p, "(b) => b.getAttribute('aria-pressed') === 'true'", arg=check_de(nueva).element_handle(), que=f"[{nombre}] el check funciona")
        borrar_de(filas(p).first).tap()
        esperar_filas(p, validas)
        volver(p)
        abrir_ajustes(p)
        igual(p.get_by_label("Nombre", exact=True).input_value(), "", f"[{nombre}] nombre por defecto")
        guardado = leer_estado(p)
        comprobar(isinstance(guardado, dict) and guardado.get("version") == 2 and isinstance(guardado.get("cosas"), list)
                  and guardado.get("grupos") == [] and guardado["ajustes"]["colorFondo"].upper() == COLOR_DEFECTO,
                  f"[{nombre}] el estado no se regenera bien: {guardado}")
        p.context.close()


@prueba("almacenamiento no disponible o lleno: la app no se rompe")
def t_almacenamiento_no_disponible(e):
    guiones = {
        "localStorage lanza SecurityError": "Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('Acceso denegado', 'SecurityError'); } });",
        "setItem lanza QuotaExceededError": "Storage.prototype.setItem = function () { throw new DOMException('Lleno', 'QuotaExceededError'); };",
        "getItem lanza": "Storage.prototype.getItem = function () { throw new Error('roto'); };",
    }
    for nombre, guion in guiones.items():
        contexto = e.contexto()
        contexto.add_init_script(guion)
        p = e.pagina(contexto=contexto)
        esperar_vista(p, "inicio")
        anotar(p, "Sin almacenamiento")
        esperar(p, f"() => ({JS_TOAST})().visible", que=f"[{nombre}] sale algún aviso al guardar")
        igual(campo(p).input_value(), "", f"[{nombre}] el campo se vacía")
        abrir_lista(p)
        igual(textos_lista(p), ["Sin almacenamiento"], f"[{nombre}] la cosa está en la lista durante la sesión")
        volver(p)
        abrir_ajustes(p)
        elegir_color(p, "Negro")
        esperar_fondo(p, "#111111")
        contexto.close()


# Un texto sale recortado si no cabe a lo ancho o le sobra alguna línea. Se toleran los 1-2px que la
# caja de la fuente del sistema puede sobresalir de su línea (una línea de más serían ~24px).
JS_RECORTADO = "(el) => el.scrollWidth > el.clientWidth + 0.5 || el.scrollHeight > el.clientHeight + 4"


JS_CAJAS_AVISO = r"""
() => {
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom }; };
  const aviso = document.querySelector('[role="status"]');
  const texto = aviso.querySelector('span');
  const boton = aviso.querySelector('button');
  return {
    aviso: caja(aviso), boton: boton ? caja(boton) : null,
    textoRecortado: (JS_RECORTADO)(texto),
    vecinos: ['#abrir-lista', '#abrir-ajustes', '#accesos', '#formulario > div'].map((s) => document.querySelector(s))
      .filter((el) => el.checkVisibility()).map(caja),
  };
}
""".replace("JS_RECORTADO", JS_RECORTADO)


def comprobar_aviso_entero(pagina, ancho, alto, que):
    """El aviso se lee entero, cabe en la pantalla y no pisa ni los botones ni la barra."""
    esperar(pagina, f"() => ({JS_TOAST})().opacidad === 1")
    pagina.wait_for_timeout(200)
    cajas = pagina.evaluate(JS_CAJAS_AVISO)
    aviso = cajas["aviso"]
    comprobar(not cajas["textoRecortado"], f"{que}: el texto del aviso sale recortado")
    comprobar(aviso["x"] >= 0 and aviso["y"] >= 0 and aviso["d"] <= ancho + 0.5 and aviso["b"] <= alto + 0.5, f"{que}: el aviso se sale de la pantalla: {aviso}")
    comprobar(abs((aviso["x"] + aviso["d"]) / 2 - ancho / 2) <= 1.5, f"{que}: el aviso no está centrado: {aviso}")
    for vecino in cajas["vecinos"]:
        separados = aviso["d"] <= vecino["x"] or aviso["x"] >= vecino["d"] or aviso["b"] <= vecino["y"] or aviso["y"] >= vecino["b"]
        comprobar(separados, f"{que}: el aviso pisa otro elemento: {aviso} sobre {vecino}")
    if cajas["boton"]:
        boton = cajas["boton"]
        comprobar(boton["x"] >= aviso["x"] - 0.5 and boton["d"] <= aviso["d"] + 0.5, f"{que}: «Deshacer» se sale del aviso: {boton} en {aviso}")
    return cajas


@prueba("aviso de error al guardar: se lee entero en cualquier ancho de móvil (412, 390, 360 y 320 px)")
def t_aviso_de_error_entero(e):
    for ancho in (412, 390, 360, 320):
        contexto = e.contexto(viewport={"width": ancho, "height": e.alto})
        contexto.add_init_script("Storage.prototype.setItem = function () { throw new DOMException('Lleno', 'QuotaExceededError'); };")
        p = e.pagina(contexto=contexto)
        anotar(p, "No cabe")
        esperar_toast(p, "No se ha podido guardar")
        comprobar_aviso_entero(p, ancho, e.alto, f"Error al guardar a {ancho}px")
        # Marcar o cambiar el color tampoco fallan en silencio.
        abrir_lista(p)
        esperar_sin_toast(p)
        check_de(filas(p).first).tap()
        esperar_toast(p, "No se ha podido guardar")
        comprobar_aviso_entero(p, ancho, e.alto, f"Error al marcar a {ancho}px")
        contexto.close()


@prueba("zoom de página al 200 % (195 px de ancho): «Guardado», el título y «Deshacer» se leen enteros")
def t_zoom_200(e):
    ancho, alto = 195, 400
    contexto = e.contexto(viewport={"width": ancho, "height": alto})
    p = sembrar(e, estado_con([cosa(2, "Dos"), cosa(1, "Una")]), contexto=contexto)
    anotar(p, "Tres")
    aviso = esperar_toast(p, "Guardado")
    comprobar(aviso["icono"], "«Guardado» pierde el icono con la pantalla estrecha")
    comprobar_aviso_entero(p, ancho, alto, "«Guardado» a 195px")
    comprobar_sin_desborde(p, "inicio a 195px")
    abrir_lista(p)
    titulo = p.locator("#vista-lista h1").evaluate(f"(h) => ({{ recortado: ({JS_RECORTADO})(h), texto: h.textContent }})")
    igual(titulo, {"recortado": False, "texto": "Cosas"}, "Título de la lista a 195px")
    borrar_de(filas(p).first).tap()
    igual(esperar_toast(p, "Eliminado")["boton"], "Deshacer", "Botón del aviso a 195px")
    cajas = p.evaluate(JS_CAJAS_AVISO)
    comprobar(cajas["boton"]["x"] >= cajas["aviso"]["x"] - 0.5 and cajas["boton"]["d"] <= cajas["aviso"]["d"] + 0.5 and cajas["aviso"]["d"] <= ancho,
              f"«Deshacer» se sale del aviso o de la pantalla a 195px: {cajas}")
    comprobar_sin_desborde(p, "lista a 195px")
    volver(p)
    abrir_ajustes(p)
    comprobar_sin_desborde(p, "ajustes a 195px")
    igual(p.locator("#vista-ajustes h1").evaluate(f"(h) => ({JS_RECORTADO})(h)"), False, "Título «Ajustes» recortado a 195px")
    # La página de un grupo: la cabecera sigue en una línea de 48px, con el lápiz arriba a la derecha (no bajo «Volver»).
    p2 = sembrar(e, estado_con([cosa(1, "Una", grupo="g-2")], grupos=[grupo(2, "Grupo con un nombre largo")]), ruta="#grupo/g-2", contexto=e.contexto(viewport={"width": ancho, "height": alto}))
    esperar_vista(p2, "grupo")
    cabecera = p2.locator("#vista-grupo header").bounding_box()
    atras, lapiz = p2.get_by_role("button", name="Volver", exact=True).bounding_box(), boton_editar_grupo(p2).bounding_box()
    igual(round(cabecera["height"]), 48, f"A 195px la cabecera del grupo crece (el lápiz se cae a otra fila): {cabecera}")
    comprobar(abs(lapiz["y"] - atras["y"]) <= 1 and lapiz["x"] >= atras["x"] + atras["width"] and lapiz["x"] + lapiz["width"] <= ancho + 0.5,
              f"A 195px el lápiz no está arriba a la derecha, a la altura de «Volver»: {atras} / {lapiz}")
    comprobar_sin_desborde(p2, "página del grupo a 195px")


@prueba("título de la lista: un nombre con dos apellidos se lee entero (dos líneas) sin crecer la cabecera")
def t_titulo_con_nombre_completo(e):
    for ancho in (e.ancho, 320):
        contexto = e.contexto(viewport={"width": ancho, "height": e.alto})
        p = sembrar(e, estado_con([cosa(1, "Algo")], nombre="Raúl Gómez Fernández"), ruta="#lista", contexto=contexto)
        esperar_filas(p, 1)
        medidas = p.locator("#vista-lista h1").evaluate(f"""(h) => ({{ texto: h.textContent, recortado: ({JS_RECORTADO})(h),
            cabecera: h.parentElement.getBoundingClientRect().height }})""")
        igual(medidas["texto"], "Cosas de Raúl Gómez Fernández", "Texto del título")
        comprobar(not medidas["recortado"], f"El título con nombre y dos apellidos sale recortado a {ancho}px")
        comprobar(medidas["cabecera"] <= 48.5, f"La cabecera crece con el título en dos líneas: {medidas['cabecera']}px")
        contexto.close()
    # Un nombre desmesurado sigue sin romper nada: se corta con elegancia dentro de la cabecera.
    p = sembrar(e, estado_con([cosa(1, "Algo")], nombre="María de los Ángeles Fernández-Villaverde y Ruiz de Alarcón"), ruta="#lista")
    esperar_filas(p, 1)
    igual(round(p.locator("#vista-lista header").bounding_box()["height"]), 48, "Altura de la cabecera con un nombre larguísimo")
    comprobar_sin_desborde(p, "lista con un nombre larguísimo")


@prueba("ids: crypto.randomUUID y alternativa cuando no existe")
def t_ids(e):
    contexto = e.contexto()
    contexto.add_init_script("try { Object.defineProperty(Crypto.prototype, 'randomUUID', { value: undefined, configurable: true, writable: true }); } catch (error) {}")
    p = e.pagina(contexto=contexto)
    igual(p.evaluate("() => typeof crypto.randomUUID"), "undefined", "randomUUID anulado por la prueba")
    for texto in ("a", "b", "c", "d"):
        anotar(p, texto)
    ids = [c["id"] for c in leer_estado(p)["cosas"]]
    comprobar(len(ids) == 4 and len(set(ids)) == 4 and all(isinstance(i, str) and len(i) >= 8 for i in ids), f"ids de reserva inválidos: {ids}")


@prueba("navigator.storage.persist() se pide (mejor esfuerzo)")
def t_persist(e):
    contexto = e.contexto()
    contexto.add_init_script("""(() => { window.__persist = 0;
        if (navigator.storage && navigator.storage.persist) { const original = navigator.storage.persist.bind(navigator.storage);
            navigator.storage.persist = () => { window.__persist += 1; return original(); }; } })();""")
    p = e.pagina(contexto=contexto)
    anotar(p, "Persistente")
    esperar_toast(p, "Guardado")
    comprobar(p.evaluate("() => window.__persist") >= 1, "No se llama a navigator.storage.persist()")
    contexto2 = e.contexto()
    contexto2.add_init_script("navigator.storage.persist = () => Promise.reject(new Error('no'));")
    p2 = e.pagina(contexto=contexto2)
    anotar(p2, "Persist rechaza")
    esperar_toast(p2, "Guardado")
    p2.wait_for_timeout(200)


@prueba("500 cosas: se pintan, se desplazan y marcar/borrar sigue siendo ágil")
def t_quinientas(e):
    p = sembrar(e, estado_con([cosa(i, f"Cosa número {i}", hecha=i % 3 == 0) for i in range(1, 501)]))
    inicio = time.monotonic()
    boton_lista(p).tap()
    esperar_filas(p, 500, ms=6000)
    pintado = time.monotonic() - inicio
    detalle(f"500 filas pintadas en {pintado * 1000:.0f} ms")
    comprobar(pintado < 3.0, f"Pintar 500 cosas tarda {pintado:.2f} s")
    # Las hechas (múltiplos de 3) van al final, ordenadas por creación: la última fila es la 498.
    igual(textos_lista(p)[:2] + textos_lista(p)[-1:], ["Cosa número 500", "Cosa número 499", "Cosa número 498"], "Orden con 500 cosas")
    coste = p.evaluate("""() => { const filas = document.querySelectorAll('#lista-cosas > li'); const medir = (f) => { const t = performance.now(); f(); document.body.getBoundingClientRect(); filas[0].getBoundingClientRect(); return performance.now() - t; };
        return { marcar: medir(() => filas[0].querySelector('button').click()), marcarMedio: medir(() => filas[250].querySelector('button').click()) }; }""")
    detalle(f"coste síncrono de marcar con 500 filas: {coste}")
    comprobar(coste["marcar"] < 100 and coste["marcarMedio"] < 100, f"Marcar con 500 cosas bloquea demasiado: {coste}")
    inicio = time.monotonic()
    segunda = fila_de(p, "Cosa número 497")
    check_de(segunda).tap()
    esperar(p, "(b) => b.getAttribute('aria-pressed') === 'true'", arg=check_de(segunda).element_handle(), ms=2000, que="marcar responde con 500 cosas")
    comprobar(time.monotonic() - inicio < 1.5, f"Marcar tarda {time.monotonic() - inicio:.2f} s con 500 cosas")
    igual(textos_lista(p)[-1], "Cosa número 497", "La recién marcada es la última fila incluso con 500 cosas")
    cabecera_antes = p.get_by_role("button", name="Volver", exact=True).bounding_box()
    desplazado = p.evaluate("() => { const z = document.querySelector('#zona-lista'); const cs = getComputedStyle(z); z.scrollTop = z.scrollHeight; return { top: z.scrollTop, y: cs.overflowY, alto: z.scrollHeight - z.clientHeight }; }")
    comprobar(desplazado["top"] > 1000 and desplazado["y"] in ("auto", "scroll"), f"La lista no se desplaza: {desplazado}")
    igual(p.get_by_role("button", name="Volver", exact=True).bounding_box(), cabecera_antes, "La cabecera se mueve al desplazar la lista")
    comprobar(p.get_by_role("button", name="Volver", exact=True).is_visible(), "La cabecera desaparece al desplazar")
    ultima = filas(p).last.bounding_box()
    comprobar(ultima["y"] + ultima["height"] <= e.alto, f"La última fila queda tapada por abajo: {ultima}")
    inicio = time.monotonic()
    borrar_de(filas(p).last).tap()
    esperar_filas(p, 499, ms=2500)
    comprobar(time.monotonic() - inicio < 2.0, f"Borrar tarda {time.monotonic() - inicio:.2f} s con 500 cosas")
    estado = leer_estado(p)["cosas"]
    igual(len(estado), 499, "Cosas guardadas tras borrar una de 500")
    comprobar("Cosa número 497" not in [c["texto"] for c in estado], "No se borró la última")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 500)
    igual(textos_lista(p)[-1], "Cosa número 497", "Deshacer con 500 cosas devuelve la última a su sitio")


# ----------------------------------------------------------------------------
# Teclado en pantalla
# ----------------------------------------------------------------------------

@prueba("teclado iOS (visualViewport simulado): la barra sube y vuelve abajo")
def t_teclado_ios(e):
    p = e.pagina()
    reposo = p.locator("#formulario .pildora").bounding_box()
    campo(p).tap()
    teclado = 300
    p.evaluate("""(teclado) => { const vv = window.visualViewport; const alto = window.innerHeight;
        Object.defineProperty(vv, 'height', { configurable: true, get: () => alto - teclado });
        vv.dispatchEvent(new Event('resize')); }""", teclado)
    p.wait_for_timeout(400)
    subida = p.locator("#formulario .pildora").bounding_box()
    hueco = (e.alto - teclado) - (subida["y"] + subida["height"])
    comprobar(0 <= hueco <= 24, f"Con teclado de {teclado}px la barra no queda justo encima: hueco={hueco}px ({subida})")
    p.keyboard.type("Con teclado abierto")
    p.keyboard.press("Enter")
    esperar_toast(p, "Guardado")
    aviso = toast(p)
    comprobar(aviso["y"] >= 0 and aviso["y"] + aviso["alto"] <= e.alto - teclado, f"El aviso queda tapado por el teclado: {aviso}")
    comprobar(aviso["y"] + aviso["alto"] <= subida["y"] or aviso["y"] >= subida["y"] + subida["height"], "El aviso pisa la barra con el teclado abierto")
    p.evaluate("() => { delete window.visualViewport.height; window.visualViewport.dispatchEvent(new Event('resize')); }")
    p.wait_for_timeout(450)
    igual(p.locator("#formulario .pildora").bounding_box(), reposo, "La barra no vuelve a su sitio al cerrar el teclado")
    igual(p.evaluate("() => [window.scrollX, window.scrollY, document.documentElement.scrollTop, document.body.scrollTop]"), [0, 0, 0, 0], "La página queda desplazada")


@prueba("teclado iOS con la ventana visual desplazada: barra pegada al teclado y botones a la vista")
def t_teclado_ios_desplazado(e):
    p = e.pagina()
    reposo = {"pildora": p.locator("#formulario .pildora").bounding_box(), "lista": boton_lista(p).bounding_box()}
    campo(p).tap()
    teclado = 336
    for desplazada in (0, 200, 320, 336):  # Safari desplaza la ventana visual al enfocar un campo que está abajo.
        p.evaluate("""([teclado, desplazada]) => { const vv = window.visualViewport; const alto = window.innerHeight;
            Object.defineProperty(vv, 'height', { configurable: true, get: () => alto - teclado });
            Object.defineProperty(vv, 'offsetTop', { configurable: true, get: () => desplazada });
            vv.dispatchEvent(new Event('resize')); vv.dispatchEvent(new Event('scroll')); }""", [teclado, desplazada])
        p.wait_for_timeout(400)
        arriba, abajo = desplazada, desplazada + e.alto - teclado  # Franja visible, en coordenadas de la página.
        pildora, lista, ajustes = p.locator("#formulario .pildora").bounding_box(), boton_lista(p).bounding_box(), boton_ajustes(p).bounding_box()
        hueco = abajo - (pildora["y"] + pildora["height"])
        comprobar(0 <= hueco <= 12, f"Ventana desplazada {desplazada}px: la barra no queda justo encima del teclado (hueco={hueco}px)")
        comprobar(p.evaluate("() => document.documentElement.hasAttribute('data-teclado')"), f"Ventana desplazada {desplazada}px: no se detecta el teclado abierto")
        for nombre, boton in (("lista", lista), ("ajustes", ajustes)):
            comprobar(boton["y"] >= arriba and boton["y"] + boton["height"] <= pildora["y"],
                      f"Ventana desplazada {desplazada}px: el botón de {nombre} queda fuera de la franja visible o bajo la barra: {boton}")
        igual(round(lista["y"] - arriba, 1), round(reposo["lista"]["y"], 1), f"Ventana desplazada {desplazada}px: distancia del botón al borde visible")
    p.evaluate("() => { const vv = window.visualViewport; delete vv.height; delete vv.offsetTop; vv.dispatchEvent(new Event('resize')); }")
    p.wait_for_timeout(450)
    igual((p.locator("#formulario .pildora").bounding_box(), boton_lista(p).bounding_box()), (reposo["pildora"], reposo["lista"]), "Al cerrar el teclado todo vuelve a su sitio")
    comprobar(not p.evaluate("() => document.documentElement.hasAttribute('data-teclado')"), "data-teclado sigue puesto con el teclado cerrado")


@prueba("teclado Android (la ventana encoge): la barra sigue pegada abajo")
def t_teclado_android(e):
    p = e.pagina()
    campo(p).tap()
    p.set_viewport_size({"width": e.ancho, "height": 420})
    p.wait_for_timeout(350)
    pildora, lista = p.locator("#formulario .pildora").bounding_box(), boton_lista(p).bounding_box()
    hueco = 420 - (pildora["y"] + pildora["height"])
    comprobar(0 <= hueco <= 24, f"Con la ventana encogida la barra no queda abajo: hueco={hueco}")
    comprobar(lista["y"] + lista["height"] <= pildora["y"], "La barra pisa los botones con la ventana encogida")
    comprobar(not p.evaluate("() => document.documentElement.hasAttribute('data-teclado')"), "En Android (la ventana ya encoge) no hay que compensar el teclado")
    p.keyboard.type("Desde Android")
    p.keyboard.press("Enter")
    aviso = esperar_toast(p, "Guardado")
    comprobar(aviso["y"] >= 0 and aviso["y"] + aviso["alto"] <= pildora["y"], f"El aviso no se ve con la ventana encogida: {aviso}")
    p.set_viewport_size({"width": e.ancho, "height": e.alto})
    p.wait_for_timeout(350)
    pildora = p.locator("#formulario .pildora").bounding_box()
    comprobar(0 <= e.alto - (pildora["y"] + pildora["height"]) <= 48, "La barra no vuelve abajo al restaurar la ventana")
    igual(p.evaluate("() => [document.documentElement.scrollHeight <= innerHeight, window.scrollY]"), [True, 0], "La pantalla principal se puede desplazar")


# ----------------------------------------------------------------------------
# Dictado (reconocimiento de voz simulado)
# ----------------------------------------------------------------------------

# Chromium trae SpeechRecognition (y su alias webkit), pero en las pruebas no puede reconocer nada. Se sustituye
# por un reconocimiento falso que apunta sus instancias (configuración, llamadas y oyentes vivos) y deja a la prueba
# emitir resultados, errores y el final. La lista de resultados imita a la real: se recorre, pero no es un Array.
# Queda solo con el nombre «webkit», como en Safari y en los Chrome de casi todos los móviles.
JS_DICTADO_FALSO = r"""
(() => {
  const instancias = [];
  const control = { instancias, fallarAlEmpezar: false };
  class ReconocimientoFalso extends EventTarget {
    constructor() {
      super();
      Object.assign(this, { lang: '', interimResults: false, continuous: true, maxAlternatives: 0, llamadas: [], oyentes: 0 });
      instancias.push(this);
    }
    addEventListener(...args) { this.oyentes += 1; super.addEventListener(...args); }
    removeEventListener(...args) { this.oyentes -= 1; super.removeEventListener(...args); }
    start() {
      if (control.fallarAlEmpezar) throw new DOMException('El reconocimiento ya ha empezado', 'InvalidStateError');
      this.llamadas.push('start');
    }
    stop() { this.llamadas.push('stop'); }
    abort() { this.llamadas.push('abort'); }
  }
  const emitir = (tipo, datos, indice) => {
    const objetivo = instancias[indice === undefined ? instancias.length - 1 : indice];
    objetivo.dispatchEvent(Object.assign(new Event(tipo), datos));
  };
  control.resultado = (trozos, final, indice) => {
    const results = { length: trozos.length };
    trozos.forEach((transcript, i) => { results[i] = { 0: { transcript, confidence: 0.9 }, length: 1, isFinal: Boolean(final) }; });
    emitir('result', { resultIndex: 0, results }, indice);
  };
  control.error = (codigo, indice) => emitir('error', { error: codigo, message: '' }, indice);
  control.fin = (indice) => emitir('end', {}, indice);
  control.resumen = () => instancias.map((r) => ({ lang: r.lang, provisionales: r.interimResults, continuo: r.continuous,
    alternativas: r.maxAlternatives, llamadas: r.llamadas.slice(), oyentes: r.oyentes }));
  window.__dictado = control;
  delete window.SpeechRecognition;
  window.webkitSpeechRecognition = ReconocimientoFalso;
})();
"""

JS_SIN_DICTADO = "delete window.SpeechRecognition; delete window.webkitSpeechRecognition;"

JS_BARRA = r"""
() => {
  const micro = document.querySelector('#dictar'), enviar = document.querySelector('#enviar'), campo = document.querySelector('#campo');
  const seVe = (el) => el.checkVisibility({ visibilityProperty: true, opacityProperty: true }) && !el.disabled;
  return { micro: seVe(micro), enviar: seVe(enviar), pulsado: micro.getAttribute('aria-pressed'), etiqueta: micro.getAttribute('aria-label'),
           marcador: campo.placeholder, valor: campo.value, focoEnCampo: document.activeElement === campo,
           anillo: document.querySelector('#formulario .pildora').classList.contains('escuchando') };
}
"""

BARRA_EN_REPOSO = {"micro": True, "enviar": False, "pulsado": "false", "etiqueta": "Dictar", "marcador": "Escribe una cosa…",
                   "valor": "", "focoEnCampo": False, "anillo": False}
BARRA_ESCUCHANDO = {"micro": True, "enviar": False, "pulsado": "true", "etiqueta": "Detener dictado", "marcador": "Te escucho…",
                    "valor": "", "focoEnCampo": False, "anillo": True}


def pagina_con_dictado(e, datos=None, guiones=(), **opciones):
    contexto = e.contexto(**opciones)
    for guion in (JS_DICTADO_FALSO, *guiones):
        contexto.add_init_script(guion)
    pagina = sembrar(e, datos, contexto=contexto) if datos else e.pagina(contexto=contexto)
    esperar_vista(pagina, "inicio")
    return pagina


def boton_micro(pagina):
    return pagina.locator("#dictar")


def campo_barra(pagina):
    """El campo de la barra por su id: mientras se dicta, su placeholder es otro."""
    return pagina.locator("#campo")


def barra(pagina):
    return pagina.evaluate(JS_BARRA)


def esperar_barra(pagina, esperado, que):
    """Espera (los botones del hueco se cruzan con una transición corta) y compara el estado completo de la barra."""
    try:
        pagina.wait_for_function(f"(esperado) => JSON.stringify(({JS_BARRA})()) === JSON.stringify(esperado)", arg=esperado, timeout=2500)
    except TiempoAgotado:
        igual(barra(pagina), esperado, que)


def reconocimientos(pagina):
    return pagina.evaluate("() => window.__dictado.resumen()")


def empezar_a_dictar(pagina, cuantos=1):
    boton_micro(pagina).tap()
    igual(len(reconocimientos(pagina)), cuantos, "Reconocimientos creados al tocar el micrófono")
    pagina.wait_for_timeout(400)  # Pasa la guarda del doble toque: el siguiente toque ya es un «detener».


def dictar(pagina, texto, final=False, indice=None):
    pagina.evaluate("([trozos, final, indice]) => window.__dictado.resultado(trozos, final, indice === null ? undefined : indice)",
                    [texto if isinstance(texto, list) else [texto], final, indice])


def cosas_guardadas(pagina):
    estado = leer_estado(pagina)
    return [c["texto"] for c in estado["cosas"]] if estado else []


@prueba("dictado: micrófono con el campo vacío, enviar con texto; nunca los dos a la vez")
def t_dictado_hueco(e):
    p = pagina_con_dictado(e)
    esperar_barra(p, BARRA_EN_REPOSO, "Barra con el campo vacío")
    # Orden de tabulación: lista, ajustes, campo y el botón del hueco.
    for esperado in ("Ver lista de cosas", "Ajustes", "Escribe una cosa", "Dictar"):
        p.keyboard.press("Tab")
        igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), esperado, "Orden de tabulación en inicio")
    micro = boton_micro(p)
    datos = micro.evaluate("""(b) => { const cs = getComputedStyle(b), svg = b.querySelector('svg'), css = getComputedStyle(svg);
        return { tipo: b.type, radio: cs.borderTopLeftRadius, trazo: css.stroke, color: cs.color, relleno: css.fill,
                 anchoSvg: svg.getBoundingClientRect().width, ariaOculto: svg.getAttribute('aria-hidden'), dentro: b.closest('#formulario .pildora') !== null }; }""")
    igual((datos["tipo"], datos["radio"], datos["relleno"], datos["ariaOculto"], datos["dentro"]), ("button", "50%", "none", "true", True), "Micrófono: tipo, forma, icono y sitio")
    igual(datos["trazo"], datos["color"], "El trazo del icono del micrófono no es currentColor")
    caja, pildora = micro.bounding_box(), p.locator("#formulario .pildora").bounding_box()
    comprobar(abs(caja["width"] - caja["height"]) < 0.5 and caja["width"] >= 44, f"El micrófono no es redondo de ≥44px: {caja}")
    comprobar(caja["x"] >= pildora["x"] and caja["y"] >= pildora["y"] and caja["x"] + caja["width"] <= pildora["x"] + pildora["width"]
              and caja["y"] + caja["height"] <= pildora["y"] + pildora["height"], f"El micrófono se sale de la píldora: {caja}")
    comprobar(pildora["x"] + pildora["width"] - (caja["x"] + caja["width"]) <= 12, "El micrófono no está en el extremo derecho de la barra")
    campo_barra(p).fill("   ")
    esperar_barra(p, dict(BARRA_EN_REPOSO, valor="   ", focoEnCampo=True), "Barra con solo espacios")
    campo_barra(p).fill("Llamar a mamá")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Llamar a mamá", focoEnCampo=True), "Barra con texto")
    p.wait_for_timeout(250)  # Fin del cruce entre los dos botones.
    igual(boton_guardar(p).bounding_box(), caja, "Enviar no ocupa el mismo hueco que el micrófono")
    # Sigue habiendo exactamente tres elementos: el micrófono es parte de la barra.
    campo_barra(p).fill("")
    esperar_barra(p, dict(BARRA_EN_REPOSO, focoEnCampo=True), "Barra al vaciar el campo")
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual((auditoria["intrusos"], auditoria["raices"], texto_inicio(auditoria)), ([], RAICES_INICIO, TEXTO_INICIO), "Pantalla principal con micrófono")


@prueba("dictado: sin reconocimiento de voz en el navegador no hay micrófono y todo sigue como antes")
def t_dictado_sin_soporte(e):
    contexto = e.contexto()
    contexto.add_init_script(JS_SIN_DICTADO)
    p = e.pagina(contexto=contexto)
    esperar_vista(p, "inicio")
    igual(p.evaluate("() => [typeof window.SpeechRecognition, typeof window.webkitSpeechRecognition]"), ["undefined", "undefined"], "Reconocimiento anulado por la prueba")
    comprobar(not boton_micro(p).is_visible(), "El micrófono se ve en un navegador que no sabe reconocer la voz")
    igual(p.evaluate("() => document.querySelector('#dictar-en-grupo').hidden"), True, "El micrófono de la página de un grupo existe sin reconocimiento de voz")
    comprobar(not boton_guardar(p).is_visible(), "El botón Guardar se ve con el campo vacío")
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual((auditoria["intrusos"], auditoria["raices"], texto_inicio(auditoria)), ([], RAICES_INICIO, TEXTO_INICIO), "Pantalla principal sin micrófono")
    for _ in range(3):
        p.keyboard.press("Tab")
    igual(p.evaluate("() => document.activeElement.id"), "campo", "Tercer elemento al tabular")
    p.keyboard.press("Tab")
    comprobar(p.evaluate("() => document.activeElement.id !== 'dictar'"), "El micrófono oculto entra en el tabulador")
    campo(p).fill("Sin dictado")
    comprobar(boton_guardar(p).is_visible() and not boton_micro(p).is_visible(), "Con texto debe verse solo el botón Guardar")
    p.wait_for_timeout(250)  # Fin de la transición de entrada del botón.
    caja, pildora = boton_guardar(p).bounding_box(), p.locator("#formulario .pildora").bounding_box()
    comprobar(pildora["x"] + pildora["width"] - (caja["x"] + caja["width"]) <= 12, "El botón Guardar no está en el extremo derecho")
    boton_guardar(p).tap()
    esperar_toast(p, "Guardado")
    igual(cosas_guardadas(p), ["Sin dictado"], "Cosas guardadas sin dictado")
    comprobar(not boton_micro(p).is_visible(), "El micrófono aparece tras guardar")
    abrir_ajustes(p)
    comprobar(p.get_by_text(AYUDA_SESION).is_visible(), "Falta la ayuda de datos")
    igual(p.get_by_text(AYUDA_DICTADO).count(), 1, "La frase del dictado existe (oculta) en Ajustes")
    comprobar(not p.get_by_text(AYUDA_DICTADO).is_visible(), "Se habla del dictado donde no lo hay")


@prueba("dictado: al tocar empieza un reconocimiento es-ES con provisionales, no continuo, sin abrir el teclado")
def t_dictado_empieza(e):
    p = pagina_con_dictado(e)
    boton_micro(p).tap()
    igual(reconocimientos(p), [{"lang": "es-ES", "provisionales": True, "continuo": False, "alternativas": 1, "llamadas": ["start"], "oyentes": 3}],
          "Reconocimiento creado al tocar el micrófono")
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra mientras escucha")
    comprobar(p.evaluate("() => !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)"), "Al dictar el foco está en un campo de texto (se abriría el teclado)")
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual((auditoria["intrusos"], auditoria["raices"]), ([], RAICES_INICIO), "Pantalla principal mientras escucha")
    # Termina sin haber oído nada: todo vuelve al reposo y no queda nada vivo.
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra tras terminar sin texto")
    igual(reconocimientos(p)[0]["oyentes"], 0, "Oyentes que quedan en el reconocimiento terminado")
    igual(reconocimientos(p)[0]["llamadas"], ["start"], "Llamadas tras un final natural (no hay nada que abortar)")
    comprobar(not toast(p)["visible"], "Sale un aviso al terminar sin texto")
    igual(cosas_guardadas(p), [], "Cosas guardadas tras un dictado vacío")
    # Con el teclado abierto (campo enfocado y vacío), dictar lo cierra.
    p.wait_for_timeout(400)  # Pasa la guarda del micrófono recién aparecido bajo el dedo (tras guardar o al acabar un dictado).
    campo(p).tap()
    comprobar(p.evaluate("() => document.activeElement.id === 'campo'"), "El campo no recibe el foco al tocarlo")
    boton_micro(p).tap()
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra al dictar desde el campo enfocado")
    igual(len(reconocimientos(p)), 2, "Reconocimientos tras el segundo dictado")
    # También con el nombre estándar, sin prefijo.
    contexto = e.contexto()
    contexto.add_init_script(JS_DICTADO_FALSO + "window.SpeechRecognition = window.webkitSpeechRecognition; delete window.webkitSpeechRecognition;")
    p2 = e.pagina(contexto=contexto)
    boton_micro(p2).tap()
    esperar_barra(p2, BARRA_ESCUCHANDO, "Barra mientras escucha (SpeechRecognition sin prefijo)")
    igual([r["llamadas"] for r in reconocimientos(p2)], [["start"]], "Reconocimiento sin prefijo")


@prueba("dictado: lo provisional se escribe en vivo, lo definitivo se normaliza y no se guarda hasta pulsar enviar")
def t_dictado_texto(e):
    p = pagina_con_dictado(e)
    empezar_a_dictar(p)
    for provisional, esperado in (("comprar", "Comprar"), ("comprar pan", "Comprar pan"), (["comprar pan", " y leche"], "Comprar pan y leche")):
        dictar(p, provisional)
        esperar_barra(p, dict(BARRA_ESCUCHANDO, valor=esperado), f"Barra con el texto provisional {provisional!r}")
    dictar(p, "  comprar   pan y\tleche  sin lactosa ", final=True)
    esperar_barra(p, dict(BARRA_ESCUCHANDO, valor="Comprar pan y leche sin lactosa"), "Barra con el texto definitivo, aún escuchando")
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Comprar pan y leche sin lactosa"), "Barra al terminar con texto")
    p.wait_for_timeout(700)
    igual(cosas_guardadas(p), [], "El dictado se guarda solo, sin confirmar (puede haberse entendido mal)")
    comprobar(not toast(p)["visible"], "Sale un aviso al terminar de dictar, antes de confirmar")
    igual(reconocimientos(p)[0]["oyentes"], 0, "Oyentes que quedan tras terminar")
    boton_guardar(p).tap()
    comprobar(esperar_toast(p, "Guardado")["icono"], "El aviso «Guardado» no lleva icono")
    igual(cosas_guardadas(p), ["Comprar pan y leche sin lactosa"], "Cosas guardadas al confirmar lo dictado")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra tras guardar lo dictado")
    p.wait_for_timeout(400)  # Pasa la guarda del micrófono recién aparecido bajo el dedo (tras guardar o al acabar un dictado).
    # Un dictado larguísimo se corta a los 500 caracteres del campo; «ñ» y tildes iniciales también se ponen en mayúscula.
    empezar_a_dictar(p, 2)
    dictar(p, "palabra " * 100, final=True)
    igual(len(campo_barra(p).input_value()), 500, "Longitud de un dictado larguísimo")
    dictar(p, "a" * 499 + " cola", final=True)
    igual(campo_barra(p).input_value(), "A" + "a" * 498, "Un corte que cae en un espacio no deja el espacio al final")
    dictar(p, "ñoquis y élite", final=True)
    igual(campo_barra(p).input_value(), "Ñoquis y élite", "Mayúscula inicial con ñ")
    # Un resultado llegado después del final ya no escribe nada: no quedan oyentes.
    p.evaluate("() => window.__dictado.fin()")
    dictar(p, "fantasma", final=True, indice=1)
    igual(campo_barra(p).input_value(), "Ñoquis y élite", "Un reconocimiento terminado sigue escribiendo en el campo")


@prueba("dictado: con el dedo la barra vuelve a verse en reposo al acabar; con teclado el foco pasa del micrófono a «Guardar»")
def t_dictado_foco(e):
    js_anillo = "() => getComputedStyle(document.querySelector('#formulario .pildora')).boxShadow"
    p = pagina_con_dictado(e)
    reposo = p.evaluate(js_anillo)
    for con_texto in (False, True):
        boton_micro(p).tap()
        esperar_barra(p, BARRA_ESCUCHANDO, "Barra escuchando tras tocar el micrófono")
        if con_texto:
            dictar(p, "tirar el vidrio", final=True)
        p.evaluate("() => window.__dictado.fin()")
        p.wait_for_timeout(400)  # Y pasa la guarda del micrófono recién aparecido: el toque siguiente vuelve a dictar.
        igual(p.evaluate("() => document.activeElement.tagName"), "BODY", "Foco tras un dictado empezado con el dedo")
        igual(p.evaluate(js_anillo), reposo, "La barra se queda resaltada tras un dictado empezado con el dedo")
    # Con teclado: Tab hasta el micrófono, Intro para dictar, y al terminar Intro guarda.
    p = pagina_con_dictado(e)
    for _ in range(4):
        p.keyboard.press("Tab")
    igual(p.evaluate("() => document.activeElement.id"), "dictar", "Cuarto elemento al tabular")
    p.keyboard.press("Enter")
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra escuchando tras Intro en el micrófono")
    p.evaluate("() => window.__dictado.fin()")
    p.wait_for_timeout(200)
    igual(p.evaluate("() => document.activeElement.id"), "dictar", "Foco tras un dictado vacío empezado con el teclado")
    p.wait_for_timeout(250)
    p.keyboard.press("Enter")
    dictar(p, "bajar la basura", final=True)
    p.evaluate("() => window.__dictado.fin()")
    esperar(p, "() => document.activeElement.id === 'enviar'", que="el foco pasa del micrófono a «Guardar»")
    p.keyboard.press("Enter")  # Un Intro repetido sobre el micrófono cae ahora en «Guardar»: tampoco guarda sin revisar.
    p.wait_for_timeout(200)
    igual(cosas_guardadas(p), [], "Un Intro justo al acabar el dictado guarda sin revisar")
    p.wait_for_timeout(450)  # Pasa la guarda del final del dictado.
    p.keyboard.press("Enter")
    esperar_toast(p, "Guardado")
    igual(cosas_guardadas(p), ["Bajar la basura"], "Cosas guardadas dictando solo con el teclado")


@prueba("dictado: el segundo toque detiene y conserva lo reconocido; si el navegador no avisa del final, se corta")
def t_dictado_detener(e):
    p = pagina_con_dictado(e)
    empezar_a_dictar(p)
    dictar(p, "llamar al dentista")
    boton_micro(p).tap()
    igual(reconocimientos(p)[0]["llamadas"], ["start", "stop"], "Llamadas tras el segundo toque")
    igual(len(reconocimientos(p)), 1, "El toque de detener crea otro reconocimiento")
    dictar(p, "llamar al dentista mañana", final=True)  # stop() entrega lo que faltaba y después «end».
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Llamar al dentista mañana"), "Barra tras detener")
    igual(cosas_guardadas(p), [], "Detener el dictado guarda sin confirmar")
    campo_barra(p).fill("")
    p.wait_for_timeout(400)  # Pasa la guarda del micrófono recién aparecido bajo el dedo (tras guardar o al acabar un dictado).
    # Sin «end» tras stop(), la app no se queda escuchando para siempre.
    empezar_a_dictar(p, 2)
    dictar(p, "regar")
    boton_micro(p).tap()
    p.wait_for_timeout(1200)
    igual(barra(p)["pulsado"], "true", "La barra deja de escuchar antes de dar tiempo al navegador a terminar")
    esperar(p, f"() => ({JS_BARRA})().pulsado === 'false'", ms=3000, que="la barra vuelve al reposo aunque el navegador no avise del final")
    igual(reconocimientos(p)[1], {"lang": "es-ES", "provisionales": True, "continuo": False, "alternativas": 1, "llamadas": ["start", "stop", "abort"], "oyentes": 0},
          "Reconocimiento cortado por la espera")
    igual(campo_barra(p).input_value(), "Regar", "Texto conservado tras el corte")


ERRORES_DICTADO = [
    ("not-allowed", "Permite el micrófono para dictar"),
    ("no-speech", "No te he oído"),
    ("service-not-allowed", "El dictado no está disponible: revisa los ajustes del móvil"),  # No es el permiso del micrófono.
    ("network", "El dictado necesita conexión"),
    ("audio-capture", "No encuentro el micrófono"),
    ("language-not-supported", "No se ha podido dictar"),
    ("toString", "No se ha podido dictar"),
]


@prueba("dictado: cada error enseña su aviso (solo texto), la barra vuelve al reposo y el campo conserva lo que tenía")
def t_dictado_errores(e):
    p = pagina_con_dictado(e, viewport={"width": 320, "height": e.alto})
    for indice, (codigo, mensaje) in enumerate(ERRORES_DICTADO):
        boton_micro(p).tap()
        esperar_barra(p, BARRA_ESCUCHANDO, f"Barra escuchando antes del error «{codigo}»")
        p.evaluate("(codigo) => window.__dictado.error(codigo)", codigo)
        aviso = esperar_toast(p, mensaje)
        comprobar(not aviso["icono"] and aviso["boton"] is None, f"El aviso de «{codigo}» no es de solo texto: {aviso}")
        esperar_barra(p, BARRA_EN_REPOSO, f"Barra tras el error «{codigo}»")
        igual(reconocimientos(p)[indice]["oyentes"], 0, f"Oyentes vivos tras el error «{codigo}»")
        if indice == 0 or codigo == "service-not-allowed":  # El primero y el más largo.
            comprobar_aviso_entero(p, 320, e.alto, f"Aviso de «{codigo}» a 320px")
        p.evaluate("() => window.__dictado.fin()")  # El «end» que sigue a todo error ya no encuentra a nadie.
        esperar_sin_toast(p, ms=6000)  # El aviso largo dura más.
    # «aborted» (un corte, propio o del sistema) no se cuenta como fallo; lo ya reconocido se queda.
    empezar_a_dictar(p, len(ERRORES_DICTADO) + 1)
    dictar(p, "a medias")
    p.evaluate("() => window.__dictado.error('aborted')")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="A medias"), "Barra tras «aborted»")
    p.wait_for_timeout(400)  # Y pasa la guarda del micrófono recién aparecido.
    comprobar(not toast(p)["visible"], "«aborted» enseña un aviso")
    campo_barra(p).fill("")
    # start() puede lanzar InvalidStateError: aviso genérico y reposo, sin errores de página.
    p.evaluate("() => { window.__dictado.fallarAlEmpezar = true; }")
    boton_micro(p).tap()
    esperar_toast(p, "No se ha podido dictar")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra cuando start() lanza")
    igual(reconocimientos(p)[-1]["oyentes"], 0, "Oyentes vivos cuando start() lanza")
    igual(cosas_guardadas(p), [], "Cosas guardadas tras los errores")


@prueba("dictado: se corta en silencio al cambiar de vista, al teclear, al enviar y al ocultarse la página")
def t_dictado_cortes(e):
    p = pagina_con_dictado(e)
    cortado = {"lang": "es-ES", "provisionales": True, "continuo": False, "alternativas": 1, "llamadas": ["start", "abort"], "oyentes": 0}
    # Cambio de vista.
    empezar_a_dictar(p)
    boton_ajustes(p).tap()
    esperar_vista(p, "ajustes")
    igual(reconocimientos(p)[0], cortado, "Reconocimiento al cambiar de vista")
    volver(p)
    esperar_barra(p, BARRA_EN_REPOSO, "Barra al volver a inicio")
    # Tecleo: lo reconocido se queda y el usuario sigue a mano.
    empezar_a_dictar(p, 2)
    dictar(p, "comprar")
    campo_barra(p).tap()
    p.keyboard.type("x")
    igual(reconocimientos(p)[1], cortado, "Reconocimiento al teclear")
    valor = campo_barra(p).input_value()
    comprobar(len(valor) == 8 and "x" in valor and valor.replace("x", "", 1) == "Comprar", f"El campo no conserva lo dictado más lo tecleado: {valor!r}")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor=valor, focoEnCampo=True), "Barra tras teclear")
    dictar(p, "fantasma", indice=1)
    igual(campo_barra(p).input_value(), valor, "Un reconocimiento cortado sigue escribiendo en el campo")
    campo_barra(p).fill("")
    campo_barra(p).evaluate("(c) => c.blur()")
    # Envío del formulario (Intro con teclado físico): corta y guarda lo que hay en el campo.
    empezar_a_dictar(p, 3)
    dictar(p, "sacar la basura")
    p.evaluate("() => document.querySelector('#formulario').requestSubmit()")
    esperar_toast(p, "Guardado")
    igual(reconocimientos(p)[2], cortado, "Reconocimiento al enviar")
    igual(cosas_guardadas(p), ["Sacar la basura"], "Cosas guardadas al enviar mientras se dicta")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra tras enviar")
    p.wait_for_timeout(400)  # Pasa la guarda del micrófono recién aparecido bajo el dedo (tras guardar o al acabar un dictado).
    # Página oculta (otra app, pantalla apagada) y página descartada.
    for numero, guion in ((4, "Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); delete document.hidden;"),
                          (5, "window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));")):
        empezar_a_dictar(p, numero)
        p.evaluate(f"() => {{ {guion} }}")
        igual(reconocimientos(p)[numero - 1], cortado, f"Reconocimiento {numero} al ocultarse la página")
        esperar_barra(p, BARRA_EN_REPOSO, "Barra tras ocultarse la página")
    # Volver a estar visible no corta nada.
    empezar_a_dictar(p, 6)
    p.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    igual(reconocimientos(p)[5]["llamadas"], ["start"], "Un visibilitychange con la página visible corta el dictado")
    p.wait_for_timeout(300)
    comprobar(not toast(p)["visible"] or toast(p)["texto"] == "Guardado", f"Los cortes enseñan avisos: {toast(p)}")


@prueba("dictado: un doble toque empieza un solo reconocimiento (y no lo detiene)")
def t_dictado_doble_toque(e):
    solo_empezado = [{"lang": "es-ES", "provisionales": True, "continuo": False, "alternativas": 1, "llamadas": ["start"], "oyentes": 3}]
    for pausa in (0, 120, 250):
        p = pagina_con_dictado(e)
        doble_toque(p, boton_micro(p), pausa)
        p.wait_for_timeout(300)
        igual(reconocimientos(p), solo_empezado, f"Reconocimientos tras un doble toque ({pausa} ms) en el micrófono")
        esperar_barra(p, BARRA_ESCUCHANDO, f"Barra tras un doble toque ({pausa} ms)")
        p.context.close()
    p = pagina_con_dictado(e)
    p.evaluate("() => { const b = document.querySelector('#dictar'); b.click(); b.click(); b.click(); }")
    igual(reconocimientos(p), solo_empezado, "Reconocimientos tras tres clics seguidos por script")
    # Mientras el navegador termina de parar, más toques no crean otro reconocimiento.
    p.wait_for_timeout(400)
    for _ in range(3):
        boton_micro(p).tap()
    igual(len(reconocimientos(p)), 1, "Reconocimientos vivos a la vez")


def centro_del_hueco(pagina):
    """El punto de la barra donde se turnan el micrófono y «Guardar»."""
    caja = boton_micro(pagina).bounding_box()
    return caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2


@prueba("dictado: si termina solo, el toque que iba a «detener» cae en «Guardar» recién aparecido y no guarda lo dictado sin revisar")
def t_dictado_fin_bajo_el_dedo(e):
    con_texto = dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Comprar pam")
    # El navegador da por terminada la frase (continuous = false) justo cuando el usuario va a tocar «detener».
    for pausa in (0, 150, 300):
        p = pagina_con_dictado(e)
        x, y = centro_del_hueco(p)
        empezar_a_dictar(p)
        dictar(p, "comprar pam", final=True)  # Mal entendido: hay que poder corregirlo antes de guardar.
        p.evaluate("() => window.__dictado.fin()")
        if pausa:
            p.wait_for_timeout(pausa)
        p.touchscreen.tap(x, y)
        p.wait_for_timeout(250)
        igual(cosas_guardadas(p), [], f"Cosas guardadas por un toque {pausa} ms después de terminar el dictado")
        comprobar(not toast(p)["visible"], f"Sale un aviso con un toque {pausa} ms después de terminar: {toast(p)}")
        esperar_barra(p, con_texto, f"Barra tras un toque {pausa} ms después de terminar el dictado")
        igual(len(reconocimientos(p)), 1, "Ese toque empieza otro dictado")
        p.wait_for_timeout(350)  # Pasada la guarda, «Guardar» guarda con un toque.
        p.touchscreen.tap(x, y)
        esperar_toast(p, "Guardado")
        igual(cosas_guardadas(p), ["Comprar pam"], "Cosas guardadas al confirmar pasada la guarda")
        p.context.close()
    # Doble toque para detener: el primero para, el navegador termina enseguida y el segundo cae en «Guardar».
    p = pagina_con_dictado(e)
    empezar_a_dictar(p)
    dictar(p, "llamar a ana")
    p.evaluate("""() => document.querySelector('#dictar').addEventListener('click', () => setTimeout(() => {
        window.__dictado.resultado(['llamar a ana'], true); window.__dictado.fin(); }, 100), { once: true })""")
    doble_toque(p, boton_micro(p), 250)
    p.wait_for_timeout(250)
    igual(reconocimientos(p)[0]["llamadas"], ["start", "stop"], "Llamadas tras el doble toque de «detener»")
    igual(cosas_guardadas(p), [], "Cosas guardadas por el segundo toque de un doble toque en «detener»")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Llamar a ana"), "Barra tras el doble toque de «detener»")
    # Un envío hecho a propósito mientras escucha (Intro con teclado físico) no tiene guarda: ver «se corta en silencio…».
    # Tras un error con el campo vacío, lo que reaparece bajo el dedo es el micrófono en reposo: ese toque no vuelve a dictar.
    p = pagina_con_dictado(e)
    x, y = centro_del_hueco(p)
    empezar_a_dictar(p)
    p.evaluate("() => window.__dictado.error('no-speech')")
    p.wait_for_timeout(100)
    p.touchscreen.tap(x, y)
    p.wait_for_timeout(200)
    igual(len(reconocimientos(p)), 1, "Reconocimientos tras un toque 100 ms después de un error")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra tras un toque 100 ms después de un error")
    p.wait_for_timeout(200)
    p.touchscreen.tap(x, y)
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra al volver a dictar pasada la guarda")
    igual(len(reconocimientos(p)), 2, "Reconocimientos al volver a dictar pasada la guarda")


@prueba("dictado: un doble toque humano en enviar (200-300 ms) guarda una sola vez y no empieza a dictar")
def t_dictado_doble_toque_enviar(e):
    for pausa in (200, 250, 300):
        p = pagina_con_dictado(e)
        campo_barra(p).fill("Doble toque")
        p.wait_for_timeout(250)  # Fin del cruce entre los dos botones del hueco.
        doble_toque(p, boton_guardar(p), pausa)  # El segundo toque cae en el micrófono que acaba de aparecer.
        p.wait_for_timeout(300)
        igual(cosas_guardadas(p), ["Doble toque"], f"Cosas guardadas tras un doble toque ({pausa} ms) en enviar")
        igual(reconocimientos(p), [], f"Reconocimientos empezados por un doble toque ({pausa} ms) en enviar")
        esperar_barra(p, BARRA_EN_REPOSO, f"Barra tras un doble toque ({pausa} ms) en enviar")
        p.context.close()
    # Pasada la guarda, dictar justo después de guardar funciona.
    p = pagina_con_dictado(e)
    anotar(p, "Primero a mano", con="boton")
    esperar_toast(p, "Guardado")
    p.wait_for_timeout(400)
    boton_micro(p).tap()
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra al dictar después de guardar")


@prueba("dictado: insistir en «detener» mientras el navegador para no repite stop(), no alarga la espera y no guarda por accidente")
def t_dictado_detener_insistiendo(e):
    p = pagina_con_dictado(e)
    x, y = centro_del_hueco(p)
    empezar_a_dictar(p)
    dictar(p, "regar")
    p.evaluate("""() => { const b = document.querySelector('#dictar'); window.__reposo = null;
        new MutationObserver(() => { if (window.__reposo === null && b.getAttribute('aria-pressed') === 'false') window.__reposo = performance.now(); })
            .observe(b, { attributes: true, attributeFilter: ['aria-pressed'] });
        b.addEventListener('click', () => { window.__primerStop = performance.now(); }, { once: true }); }""")
    p.touchscreen.tap(x, y)  # El navegador no avisa del final (sin «end»): el usuario insiste.
    for _ in range(3):
        p.wait_for_timeout(600)
        p.touchscreen.tap(x, y)
        igual(barra(p)["pulsado"], "true", "Insistir en «detener» corta el dictado antes de dar tiempo al navegador")
    esperar(p, "() => window.__reposo !== null", ms=3000, que="la barra vuelve al reposo aunque el navegador no avise del final")
    igual(reconocimientos(p)[0]["llamadas"], ["start", "stop", "abort"], "Llamadas tras insistir en «detener»")
    tardanza = p.evaluate("() => window.__reposo - window.__primerStop")
    detalle(f"Del primer «detener» al reposo: {tardanza:.0f} ms")
    comprobar(2300 <= tardanza <= 2900, f"Insistir alarga la espera del corte: {tardanza:.0f} ms desde el primer «detener» (debían ser unos 2500)")
    # El corte también deja «Guardar» bajo el dedo de quien seguía insistiendo.
    p.touchscreen.tap(x, y)
    p.wait_for_timeout(250)
    igual(cosas_guardadas(p), [], "Cosas guardadas por el toque que sigue al corte")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Regar"), "Barra tras el corte por espera")
    p.wait_for_timeout(350)
    p.touchscreen.tap(x, y)
    esperar_toast(p, "Guardado")
    igual(cosas_guardadas(p), ["Regar"], "Cosas guardadas al confirmar tras el corte")
    # Y después se dicta y se detiene con normalidad.
    p.wait_for_timeout(400)
    empezar_a_dictar(p, 2)
    boton_micro(p).tap()
    igual(reconocimientos(p)[1]["llamadas"], ["start", "stop"], "Llamadas del dictado siguiente")
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra(p, BARRA_EN_REPOSO, "Barra tras detener el dictado siguiente")


@prueba("dictado: en una frase larga se ve lo último que se ha entendido; al terminar se revisa desde el principio")
def t_dictado_frase_larga(e):
    js_campo = """(c) => ({ izquierda: c.scrollLeft, tope: c.scrollWidth - c.clientWidth, desborde: getComputedStyle(c).textOverflow,
                           conFoco: document.activeElement === c })"""
    frase = "llamar al dentista para cambiar la cita del jueves por la tarde y comprar pan"
    p = pagina_con_dictado(e)
    empezar_a_dictar(p)
    for provisional in (frase, frase + " integral"):
        dictar(p, provisional)
        m = campo_barra(p).evaluate(js_campo)
        comprobar(m["tope"] > 100, f"La frase de prueba no desborda el campo: {m}")
        comprobar(m["izquierda"] >= m["tope"] - 1, f"Mientras escucha no se ve el final de lo reconocido: {m}")
        igual((m["desborde"], m["conFoco"]), ("clip", False), "Recorte del campo mientras escucha (sin foco: el teclado no se abre)")
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="L" + frase[1:] + " integral"), "Barra al terminar la frase larga")
    m = campo_barra(p).evaluate(js_campo)
    igual((m["izquierda"], m["desborde"]), (0, "ellipsis"), "Al terminar, el campo enseña la frase desde el principio")
    # Si el corte viene de teclear, el campo tiene el foco y manda el cursor: no se le mueve el texto.
    campo_barra(p).fill("")
    campo_barra(p).evaluate("(c) => c.blur()")
    p.wait_for_timeout(400)
    empezar_a_dictar(p, 2)
    dictar(p, frase)
    campo_barra(p).tap()
    p.keyboard.type("s")
    comprobar(campo_barra(p).evaluate("(c) => c.scrollLeft") > 0, "Al teclear tras dictar, el campo salta al principio y el cursor se pierde de vista")


@prueba("dictado: «service-not-allowed» en la app instalada de iPhone → se dice la verdad y el micrófono se retira; en el resto se queda")
def t_dictado_no_disponible(e):
    ios_instalada = "Object.defineProperty(Navigator.prototype, 'standalone', { configurable: true, get: () => true });"
    p = pagina_con_dictado(e, guiones=[ios_instalada], user_agent=UA_IPHONE, viewport={"width": 320, "height": e.alto})
    esperar_barra(p, BARRA_EN_REPOSO, "Barra al arrancar en la app instalada de iPhone (el micrófono no se quita por adelantado)")
    boton_micro(p).tap()
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra escuchando en la app instalada de iPhone")
    p.evaluate("() => window.__dictado.error('service-not-allowed')")
    aviso = esperar_toast(p, "Aquí no se puede dictar: usa el micrófono del teclado")
    comprobar(not aviso["icono"] and aviso["boton"] is None, f"El aviso no es de solo texto: {aviso}")
    comprobar_aviso_entero(p, 320, e.alto, "Aviso de dictado no disponible a 320px")
    comprobar(not boton_micro(p).is_visible() and not boton_guardar(p).is_visible(), "El micrófono que no puede funcionar sigue a la vista")
    igual(reconocimientos(p)[0]["oyentes"], 0, "Oyentes vivos tras el error")
    p.wait_for_timeout(1500)
    comprobar(toast(p)["visible"], "Una frase tan larga necesita más tiempo que un aviso corto (≥ 2,5 s)")
    esperar_sin_toast(p, ms=4000)
    p.wait_for_timeout(300)  # El aviso ya invisible se vacía un instante después.
    auditoria = p.evaluate(JS_AUDITORIA_INICIO)
    igual((auditoria["intrusos"], auditoria["raices"], texto_inicio(auditoria)), ([], RAICES_INICIO, TEXTO_INICIO), "Pantalla principal tras retirar el micrófono")
    # Todo lo demás sigue como en un navegador sin dictado: se escribe (o se dicta con el teclado) y se guarda.
    for _ in range(4):
        p.keyboard.press("Tab")
    comprobar(p.evaluate("() => document.activeElement.id !== 'dictar'"), "El micrófono retirado entra en el tabulador")
    anotar(p, "Con el teclado", con="boton")
    esperar_toast(p, "Guardado")
    comprobar(not boton_micro(p).is_visible(), "El micrófono reaparece tras guardar")
    abrir_ajustes(p)
    comprobar(p.get_by_text(AYUDA_SESION).is_visible(), "Falta la ayuda de datos")
    comprobar(not p.get_by_text(AYUDA_DICTADO).is_visible(), "Ajustes sigue hablando de un dictado que aquí no existe")
    igual(p.evaluate("() => document.querySelector('#dictar-en-grupo').hidden"), True, "El micrófono de la página de un grupo sigue ahí")
    p.context.close()
    # En Safari (dictado desactivado en los ajustes de iOS) y en la app instalada en Android el micrófono se queda: se puede reintentar.
    for guiones, agente in (([], UA_IPHONE), ([JS_MODO_APLICACION], UA_ANDROID)):
        p = pagina_con_dictado(e, guiones=guiones, user_agent=agente)
        boton_micro(p).tap()
        p.evaluate("() => window.__dictado.error('service-not-allowed')")
        esperar_toast(p, "El dictado no está disponible: revisa los ajustes del móvil")
        esperar_barra(p, BARRA_EN_REPOSO, "Barra tras «service-not-allowed» fuera de la app instalada de iOS")
        p.wait_for_timeout(400)
        boton_micro(p).tap()
        esperar_barra(p, BARRA_ESCUCHANDO, "Barra al reintentar el dictado")
        p.context.close()


@prueba("dictado: el estado «escuchando» se ve y contrasta en Azul, Blanco y Negro (píxeles reales); sin latido con menos movimiento")
def t_dictado_aspecto(e):
    for nombre, color, tinta in (("Azul", "#2F6FED", (255, 255, 255)), ("Blanco", "#FFFFFF", (10, 10, 10)), ("Negro", "#111111", (255, 255, 255))):
        p = pagina_con_dictado(e, datos=estado_con(color=color))
        caja = boton_micro(p).bounding_box()
        borde = (caja["x"] + 5, caja["y"] + caja["height"] / 2)  # Dentro del círculo, lejos del icono.
        reposo = muestrear(p, [borde])[0]
        boton_micro(p).tap()
        esperar_barra(p, BARRA_ESCUCHANDO, f"Barra escuchando sobre {nombre}")
        p.wait_for_timeout(350)  # Fin de la transición de relleno.
        medidas = p.evaluate("() => {" + JS_UTIL + """
            const b = document.querySelector('#dictar'), disco = getComputedStyle(b, '::before');  // El relleno es un disco tras el icono.
            return { fondo: redondear(parsear(disco.backgroundColor)), opacidad: disco.opacity,
                     icono: contraste(parsear(getComputedStyle(b.querySelector('svg')).stroke), parsear(disco.backgroundColor)),
                     marcador: contrasteTexto(document.querySelector('#campo'), '::placeholder'),
                     latido: disco.animationName, repeticiones: disco.animationIterationCount,
                     anillo: getComputedStyle(document.querySelector('#formulario .pildora')).boxShadow }; }""")
        igual((tuple(medidas["fondo"]), medidas["opacidad"]), (tinta, "1"), f"Relleno del micrófono que escucha sobre {nombre}")
        igual(muestrear(p, [borde])[0], tinta, f"Píxel real del micrófono que escucha sobre {nombre}")
        comprobar(reposo != tinta, f"En reposo el micrófono ya se ve relleno sobre {nombre}: {reposo}")
        comprobar(medidas["icono"] >= 3, f"{nombre}: icono del micrófono que escucha = {medidas['icono']:.2f}:1")
        comprobar(medidas["marcador"] >= 3, f"{nombre}: «Te escucho…» = {medidas['marcador']:.2f}:1")
        igual((medidas["latido"], medidas["repeticiones"]), ("latir", "infinite"), f"Latido del micrófono sobre {nombre}")
        comprobar("2px" in medidas["anillo"], f"La barra no se resalta mientras escucha sobre {nombre}: {medidas['anillo']}")
        p.context.close()
    p = pagina_con_dictado(e, reduced_motion="reduce")
    boton_micro(p).tap()
    esperar_barra(p, BARRA_ESCUCHANDO, "Barra escuchando con menos movimiento")
    p.wait_for_timeout(100)  # La transición de 0,01 ms necesita un fotograma.
    igual(p.evaluate("() => getComputedStyle(document.querySelector('#dictar'), '::before').animationName"), "none", "Latido con «reducir movimiento»")
    caja = boton_micro(p).bounding_box()
    igual(muestrear(p, [(caja["x"] + 5, caja["y"] + caja["height"] / 2)])[0], (255, 255, 255), "Con menos movimiento el micrófono que escucha sigue relleno (píxel real)")


# ----------------------------------------------------------------------------
# Aplicación: «Convertir en aplicación» y la hoja de pasos
# ----------------------------------------------------------------------------

UA_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
UA_IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"  # iPadOS se presenta como un Mac.
UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
PASOS_IOS = ["Pulsa Compartir", "Elige «Añadir a pantalla de inicio»", "Pulsa «Añadir»"]
PASOS_GENERICOS = ["Abre el menú del navegador (⋮)", "Elige «Instalar aplicación» o «Añadir a pantalla de inicio»", "Confirma"]
# Según la versión de iOS, Safari enseña «Compartir» o lo guarda en su menú: la nota vale para todas (sin mirar la versión).
NOTAS_IOS = ["Si no ves Compartir, búscalo en el menú de Safari, junto a la barra de direcciones.", "Si no ves la opción, abre esta página en Safari."]
# En iOS la app instalada tiene su propio almacenamiento: quien ya tiene algo apuntado en Safari la encontrará vacía.
NOTA_DATOS_IOS = "La aplicación empezará vacía: lo que ya has apuntado se queda en Safari."
# Con la app ya instalada, Chrome no ofrece «Instalar»: en su menú pone «Abrir Cosas».
NOTA_GENERICA = "Si en el menú pone «Abrir Cosas», ya la tienes instalada."

# En la emulación «beforeinstallprompt» no llega nunca: la prueba lanza uno sintético, con su prompt() y su
# userChoice, y decide cuándo y qué «elige» el usuario.
JS_INSTALACION_FALSA = r"""
(() => {
  const registro = { prompts: 0, prevenido: null };
  let resolver = null;
  window.__instalacion = {
    registro,
    ofrecer(falla) {
      const evento = new Event('beforeinstallprompt', { cancelable: true });
      evento.platforms = ['web'];
      evento.userChoice = new Promise((resuelve) => { resolver = resuelve; });
      evento.prompt = () => {
        registro.prompts += 1;
        return falla ? Promise.reject(new DOMException('Hace falta un gesto del usuario', 'NotAllowedError')) : Promise.resolve();
      };
      window.dispatchEvent(evento);
      registro.prevenido = evento.defaultPrevented;
    },
    elegir(outcome) { resolver({ outcome, platform: 'web' }); },
  };
})();
"""

JS_MODO_APLICACION = r"""
(() => {
  const original = window.matchMedia.bind(window);
  window.matchMedia = (consulta) => (/display-mode:\s*standalone/.test(consulta)
    ? { matches: true, media: consulta, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; } }
    : original(consulta));
})();
"""

JS_HOJA = r"""
() => {
  const hoja = document.querySelector('#hoja'), dialogo = document.querySelector('[role=dialog]'), principal = document.querySelector('main');
  const abierta = hoja.checkVisibility();
  const visibles = (s) => [...dialogo.querySelectorAll(s)].filter((el) => el.checkVisibility());
  return {
    abierta,
    titulo: abierta ? document.getElementById(dialogo.getAttribute('aria-labelledby')).textContent.trim() : null,
    pasos: visibles('li').map((li) => li.textContent.trim()),
    iconos: visibles('li').map((li) => li.querySelectorAll('svg[aria-hidden=true]').length),
    notas: visibles('p').map((p) => p.textContent.trim()),
    focoDentro: dialogo.contains(document.activeElement),
    foco: document.activeElement.id || document.activeElement.tagName,
    paginaInerte: principal.hasAttribute('inert'), paginaOculta: principal.getAttribute('aria-hidden'),
  };
}
"""

HOJA_CERRADA = {"abierta": False, "titulo": None, "pasos": [], "iconos": [], "notas": [], "focoDentro": False, "foco": "instalar",
                "paginaInerte": False, "paginaOculta": None}

# Navegador sin «inert» (iOS 15): la app se apoya en aria-hidden, el velo y el tabulador retenido.
JS_SIN_INERT = """(() => { const alternar = Element.prototype.toggleAttribute, poner = Element.prototype.setAttribute;
    Element.prototype.toggleAttribute = function (nombre, forzar) { return nombre === 'inert' && forzar !== false ? false : alternar.call(this, nombre, forzar); };
    Element.prototype.setAttribute = function (nombre, ...resto) { if (nombre !== 'inert') poner.call(this, nombre, ...resto); }; })();"""


def pagina_de_ajustes(e, guiones=(), datos=None, **opciones):
    contexto = e.contexto(**opciones)
    for guion in guiones:
        contexto.add_init_script(guion)
    pagina = sembrar(e, datos, contexto=contexto) if datos else e.pagina(contexto=contexto)
    abrir_ajustes(pagina)
    pagina.evaluate("() => { const zona = document.querySelector('#vista-ajustes .desplazable'); zona.scrollTop = zona.scrollHeight; }")
    pagina.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
    return pagina


def boton_instalar(pagina):
    return pagina.get_by_role("button", name="Convertir en aplicación", exact=True)


def ayuda_instalar(pagina):
    return pagina.locator("#ayuda-instalar").text_content().strip()


def hoja(pagina):
    return pagina.evaluate(JS_HOJA)


def abrir_hoja(pagina):
    boton_instalar(pagina).tap()
    esperar(pagina, f"() => ({JS_HOJA})().abierta", que="se abre la hoja de pasos")
    pagina.wait_for_timeout(450)  # Fin de la animación de entrada y de la guarda del doble toque.
    return hoja(pagina)


@prueba("aplicación: apartado «Aplicación» tras los datos personales, con un botón ancho en píldora y una ayuda de una línea")
def t_aplicacion_apartado(e):
    p = pagina_de_ajustes(e, user_agent=UA_ANDROID)
    boton = boton_instalar(p)
    boton.scroll_into_view_if_needed()
    comprobar(boton.is_visible(), "No se ve el botón «Convertir en aplicación»")
    igual(boton.text_content().strip(), "Convertir en aplicación", "Texto del botón")
    orden = p.evaluate("""() => { const hijos = [...document.querySelector('#vista-ajustes .ajustes').children];
        return hijos.map((h) => (h.querySelector('h2') || h).textContent.trim()); }""")
    igual(orden, ["Apariencia", "Datos personales", "Aplicación", "Cosas 1.2"], "Orden de los apartados de ajustes")
    medidas = boton.evaluate("""(b) => { const r = b.getBoundingClientRect(), cs = getComputedStyle(b), grupo = b.parentElement.getBoundingClientRect(), ayuda = document.querySelector('#ayuda-instalar');
        return { ancho: r.width, alto: r.height, anchoGrupo: grupo.width, radio: parseFloat(cs.borderTopLeftRadius), fuente: parseFloat(cs.fontSize),
                 descrito: b.getAttribute('aria-describedby'), tipo: b.type, lineasAyuda: Math.round(ayuda.getBoundingClientRect().height / parseFloat(getComputedStyle(ayuda).lineHeight)),
                 ayudaDebajo: ayuda.getBoundingClientRect().top >= r.bottom }; }""")
    comprobar(abs(medidas["ancho"] - medidas["anchoGrupo"]) <= 1, f"El botón no ocupa todo el ancho: {medidas}")
    comprobar(medidas["alto"] >= 48 and medidas["radio"] >= medidas["alto"] / 2 - 0.5, f"El botón no es una píldora cómoda de tocar: {medidas}")
    comprobar(medidas["fuente"] >= 16, f"Texto del botón demasiado pequeño: {medidas['fuente']}")
    igual((medidas["tipo"], medidas["descrito"], medidas["lineasAyuda"], medidas["ayudaDebajo"]), ("button", "ayuda-instalar", 1, True), "Botón y ayuda")
    igual(ayuda_instalar(p), "Se hace desde el menú del navegador.", "Ayuda sin evento de instalación")
    comprobar_sin_desborde(p, "ajustes con el apartado «Aplicación»")


@prueba("aplicación (b): con «beforeinstallprompt» el botón abre el diálogo del sistema una sola vez; aceptado → «Aplicación añadida»")
def t_aplicacion_instalable(e):
    p = pagina_de_ajustes(e, [JS_INSTALACION_FALSA], user_agent=UA_ANDROID)
    p.evaluate("() => window.__instalacion.ofrecer()")
    igual(p.evaluate("() => window.__instalacion.registro"), {"prompts": 0, "prevenido": True}, "El evento se retiene con preventDefault() y sin abrir nada todavía")
    igual(ayuda_instalar(p), "Se añadirá a tu pantalla de inicio.", "Ayuda con el evento de instalación")
    doble_toque(p, boton_instalar(p), 120)
    p.wait_for_timeout(300)
    igual(p.evaluate("() => window.__instalacion.registro.prompts"), 1, "Llamadas a prompt() tras un doble toque")
    boton_instalar(p).tap()  # Con el diálogo del sistema abierto, más toques no hacen nada.
    igual((p.evaluate("() => window.__instalacion.registro.prompts"), hoja(p)["abierta"]), (1, False), "prompt() y hoja con el diálogo del sistema abierto")
    p.evaluate("() => window.__instalacion.elegir('accepted')")
    esperar_toast(p, "Aplicación añadida")
    comprobar_aviso_entero(p, e.ancho, e.alto, "«Aplicación añadida» en Ajustes")
    comprobar(not boton_instalar(p).is_visible(), "El botón sigue a la vista con la app ya añadida")
    igual(ayuda_instalar(p), "Ya está en tu pantalla de inicio.", "Texto tras aceptar")
    igual(p.evaluate("() => document.activeElement.id"), "titulo-ajustes", "Foco al desaparecer el botón")
    # El «appinstalled» que llega después no repite el aviso.
    esperar_sin_toast(p)
    p.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
    p.wait_for_timeout(300)
    comprobar(not toast(p)["visible"], "«appinstalled» repite el aviso tras haber aceptado")


@prueba("aplicación (b): rechazado → no pasa nada y el botón sigue sirviendo (pasos a mano hasta que llegue otro evento)")
def t_aplicacion_rechazada(e):
    p = pagina_de_ajustes(e, [JS_INSTALACION_FALSA], user_agent=UA_ANDROID)
    p.evaluate("() => window.__instalacion.ofrecer()")
    boton_instalar(p).tap()
    p.evaluate("() => window.__instalacion.elegir('dismissed')")
    p.wait_for_timeout(300)
    comprobar(not toast(p)["visible"], f"Sale un aviso al rechazar la instalación: {toast(p)}")
    comprobar(boton_instalar(p).is_visible() and boton_instalar(p).is_enabled(), "El botón deja de servir tras rechazar")
    igual(ayuda_instalar(p), "Se hace desde el menú del navegador.", "Ayuda tras rechazar (el evento usado no sirve otra vez)")
    igual(abrir_hoja(p)["pasos"], PASOS_GENERICOS, "Pasos tras rechazar")
    igual(p.evaluate("() => window.__instalacion.registro.prompts"), 1, "Un evento ya usado no se vuelve a lanzar")
    # Llega un evento nuevo con la hoja abierta: el botón mejora en silencio.
    p.evaluate("() => window.__instalacion.ofrecer()")
    igual(ayuda_instalar(p), "Se añadirá a tu pantalla de inicio.", "Ayuda al llegar un evento nuevo")
    comprobar(hoja(p)["abierta"] and not toast(p)["visible"], "La llegada del evento cierra la hoja o enseña un aviso")
    p.keyboard.press("Escape")
    boton_instalar(p).tap()
    igual((p.evaluate("() => window.__instalacion.registro.prompts"), hoja(p)["abierta"]), (2, False), "Con el evento nuevo el botón vuelve a abrir el diálogo del sistema")
    p.evaluate("() => window.__instalacion.elegir('accepted')")
    esperar_toast(p, "Aplicación añadida")
    # Si el navegador se niega a abrir su diálogo, quedan los pasos a mano.
    p2 = pagina_de_ajustes(e, [JS_INSTALACION_FALSA], user_agent=UA_ANDROID)
    p2.evaluate("() => window.__instalacion.ofrecer(true)")
    boton_instalar(p2).tap()
    esperar(p2, f"() => ({JS_HOJA})().abierta", que="se abre la hoja si prompt() falla")
    igual(hoja(p2)["pasos"], PASOS_GENERICOS, "Pasos cuando prompt() falla")


@prueba("aplicación (c): en iPhone y iPad el botón abre la hoja con los tres pasos de iOS")
def t_aplicacion_ios(e):
    tactil = "Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { configurable: true, get: () => 5 });"
    for agente, guiones, ayuda in ((UA_IPHONE, [], "En iPhone se hace en tres pasos."), (UA_IPAD, [tactil], "En iPad se hace en tres pasos.")):
        p = pagina_de_ajustes(e, guiones, user_agent=agente)
        igual(ayuda_instalar(p), ayuda, "Ayuda en iOS")
        estado = abrir_hoja(p)
        igual((estado["titulo"], estado["pasos"], estado["iconos"]), ("Añadir a la pantalla de inicio", PASOS_IOS, [1, 1, 1]), "Hoja en iOS")
        igual(estado["notas"], NOTAS_IOS, "Notas de la hoja en iOS sin nada apuntado (no hay nada que avisar)")
        numeros = p.evaluate("() => [...document.querySelectorAll('[role=dialog] li')].filter((li) => li.checkVisibility()).map((li) => getComputedStyle(li, '::before').content)")
        igual(numeros, ["counter(paso)"] * 3, "Los pasos van numerados")
        igual(p.evaluate("() => [...document.querySelectorAll('[role=dialog] ol')].filter((o) => o.checkVisibility()).map((o) => o.getAttribute('role'))"), ["list"], "La lista de pasos se anuncia como lista")
        p.context.close()
    # Con algo ya guardado en Safari (cosas o nombre), la hoja avisa de que la app instalada empezará vacía.
    for datos in (estado_con([cosa(1, "Comprar pan")]), estado_con(nombre="Raúl")):
        p = pagina_de_ajustes(e, datos=datos, user_agent=UA_IPHONE)
        igual(abrir_hoja(p)["notas"], NOTAS_IOS + [NOTA_DATOS_IOS], f"Notas de la hoja en iOS con datos guardados ({datos['ajustes']})")
        p.context.close()
    # El aviso sigue al estado de cada momento: al borrar lo único apuntado, desaparece.
    p = pagina_de_ajustes(e, datos=estado_con([cosa(1, "Comprar pan")]), user_agent=UA_IPHONE)
    volver(p)
    abrir_lista(p)
    borrar_de(filas(p).first).tap()
    esperar_filas(p, 0)
    p.wait_for_timeout(400)
    volver(p)
    abrir_ajustes(p)
    p.wait_for_timeout(400)
    igual(abrir_hoja(p)["notas"], NOTAS_IOS, "Notas de la hoja en iOS tras borrar lo único apuntado")
    p.context.close()
    # Un Mac de verdad (sin pantalla táctil) no es un iPad.
    mac = pagina_de_ajustes(e, ["Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { configurable: true, get: () => 0 });"], user_agent=UA_IPAD)
    igual(abrir_hoja(mac)["pasos"], PASOS_GENERICOS, "Pasos en un Mac sin pantalla táctil")


@prueba("aplicación (d): sin evento de instalación, la hoja enseña los pasos genéricos del navegador")
def t_aplicacion_generica(e):
    # Con datos guardados: el aviso de «empezará vacía» es solo de iOS (en Android la app instalada comparte los datos de Chrome).
    p = pagina_de_ajustes(e, datos=estado_con([cosa(1, "Comprar pan")], nombre="Raúl"), user_agent=UA_ANDROID)
    estado = abrir_hoja(p)
    igual((estado["titulo"], estado["pasos"], estado["iconos"], estado["notas"]), ("Añadir a la pantalla de inicio", PASOS_GENERICOS, [1, 1, 1], [NOTA_GENERICA]), "Hoja genérica")
    # Abierta con file:// el apartado sigue ahí y cae en los mismos pasos.
    contexto = e.contexto(user_agent=UA_ANDROID)
    p2 = e.vigilar(contexto.new_page())
    p2.goto((APP / "index.html").as_uri() + "#ajustes")
    esperar_vista(p2, "ajustes")
    igual(ayuda_instalar(p2), "Se hace desde el menú del navegador.", "Ayuda con file://")
    igual(abrir_hoja(p2)["pasos"], PASOS_GENERICOS, "Pasos con file://")


@prueba("aplicación (a): abierta ya como aplicación, el apartado lo dice y no hay botón")
def t_aplicacion_instalada(e):
    ios_instalada = "Object.defineProperty(Navigator.prototype, 'standalone', { configurable: true, get: () => true });"
    for guiones, agente in (([JS_MODO_APLICACION], UA_ANDROID), ([ios_instalada], UA_IPHONE), ([JS_MODO_APLICACION, JS_INSTALACION_FALSA], UA_ANDROID)):
        p = pagina_de_ajustes(e, guiones, user_agent=agente)
        if JS_INSTALACION_FALSA in guiones:
            p.evaluate("() => window.__instalacion.ofrecer()")
        igual(p.locator("#vista-ajustes h2").last.text_content().strip(), "Aplicación", "El apartado sigue existiendo")
        igual(ayuda_instalar(p), "Ya la estás usando como aplicación.", "Texto del apartado en modo aplicación")
        comprobar(p.locator("#ayuda-instalar").is_visible(), "No se ve el texto del apartado")
        igual(p.get_by_role("button", name="Convertir en aplicación").count(), 0, "Botones «Convertir en aplicación» accesibles en modo aplicación")
        comprobar(not p.locator("#instalar").is_visible(), "El botón se ve en modo aplicación")
        fuente = p.locator("#ayuda-instalar").evaluate("(el) => parseFloat(getComputedStyle(el).fontSize)")
        comprobar(fuente >= 16, f"Sin botón, el texto del apartado debe leerse como texto principal: {fuente}px")
        p.context.close()


@prueba("aplicación: el evento «appinstalled» actualiza el apartado (y cierra la hoja)")
def t_aplicacion_appinstalled(e):
    p = pagina_de_ajustes(e, user_agent=UA_ANDROID)
    abrir_hoja(p)
    p.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")  # Instalada desde el menú del navegador.
    esperar_toast(p, "Aplicación añadida")
    estado = hoja(p)
    igual((estado["abierta"], estado["paginaInerte"], estado["paginaOculta"]), (False, False, None), "Hoja tras «appinstalled»")
    igual(ayuda_instalar(p), "Ya está en tu pantalla de inicio.", "Texto tras «appinstalled»")
    comprobar(not p.locator("#instalar").is_visible(), "El botón sigue a la vista tras «appinstalled»")
    igual(p.evaluate("() => document.activeElement.id"), "titulo-ajustes", "Foco tras «appinstalled» con la hoja abierta")
    # También si llega estando en otra vista.
    p2 = e.pagina(contexto=e.contexto(user_agent=UA_ANDROID, viewport={"width": 320, "height": e.alto}))
    esperar_vista(p2, "inicio")
    p2.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
    esperar_toast(p2, "Aplicación añadida")
    comprobar_aviso_entero(p2, 320, e.alto, "«Aplicación añadida» en inicio a 320px")
    esperar_sin_toast(p2)
    abrir_ajustes(p2)
    igual(ayuda_instalar(p2), "Ya está en tu pantalla de inicio.", "Texto al abrir Ajustes después")
    comprobar(not p2.locator("#instalar").is_visible(), "El botón se ve tras instalar desde otra vista")


# «Compartir aplicación»: un navigator.share y un portapapeles falsos, que apuntan lo que reciben. share() se
# queda abierto (hasta __compartir.cerrar()), se cancela o falla según __compartir.modo.
JS_COMPARTIR_FALSO = r"""
(() => {
  const registro = { llamadas: [], copiado: null };
  window.__compartir = { registro, modo: 'abierto', cerrar: null };
  Object.defineProperty(Navigator.prototype, 'share', { configurable: true, value(datos) {
    registro.llamadas.push(datos);
    if (window.__compartir.modo === 'cancelar') return Promise.reject(new DOMException('Cancelado', 'AbortError'));
    if (window.__compartir.modo === 'falla') return Promise.reject(new DOMException('No permitido', 'NotAllowedError'));
    return new Promise((resuelve) => { window.__compartir.cerrar = resuelve; });
  } });
  Object.defineProperty(Navigator.prototype, 'clipboard', { configurable: true, get: () => ({
    writeText: async (texto) => { registro.copiado = texto; } }) });
})();
"""
JS_SIN_COMPARTIR = "Object.defineProperty(Navigator.prototype, 'share', { configurable: true, value: undefined });"
JS_SIN_PORTAPAPELES = """Object.defineProperty(Navigator.prototype, 'clipboard', { configurable: true, get: () => ({
    writeText: () => Promise.reject(new DOMException('No permitido', 'NotAllowedError')) }) });
    Document.prototype.execCommand = () => false;"""


def boton_compartir(pagina):
    return pagina.get_by_role("button", name="Compartir aplicación", exact=True)


def compartido(pagina):
    return pagina.evaluate("() => window.__compartir.registro")


@prueba("aplicación: «Compartir aplicación» bajo «Convertir en aplicación» envía el enlace de la app (o lo copia)")
def t_aplicacion_compartir(e):
    p = pagina_de_ajustes(e, [JS_COMPARTIR_FALSO], user_agent=UA_ANDROID)
    boton = boton_compartir(p)
    boton.scroll_into_view_if_needed()
    comprobar(boton.is_visible(), "No se ve el botón «Compartir aplicación»")
    medidas = boton.evaluate("""(b) => { const r = b.getBoundingClientRect(), cs = getComputedStyle(b), grupo = b.parentElement.getBoundingClientRect(),
            instalar = document.querySelector('#instalar').getBoundingClientRect(), ayuda = document.querySelector('#ayuda-instalar').getBoundingClientRect();
        return { apartado: b.closest('section').querySelector('h2').textContent.trim(), ultimo: b === b.parentElement.lastElementChild, tipo: b.type,
                 ancho: r.width, anchoGrupo: grupo.width, alto: r.height, radio: parseFloat(cs.borderTopLeftRadius),
                 bajoInstalar: r.top >= instalar.bottom, bajoAyuda: r.top - ayuda.bottom }; }""")
    igual((medidas["apartado"], medidas["ultimo"], medidas["tipo"], medidas["bajoInstalar"]), ("Aplicación", True, "button", True), "Sitio del botón")
    comprobar(abs(medidas["ancho"] - medidas["anchoGrupo"]) <= 1 and medidas["alto"] >= 48 and medidas["radio"] >= medidas["alto"] / 2 - 0.5,
              f"«Compartir aplicación» no es la misma píldora ancha: {medidas}")
    comprobar(12 <= medidas["bajoAyuda"] <= 32, f"Separación entre la ayuda y «Compartir aplicación»: {medidas['bajoAyuda']}")
    # Un doble toque abre el menú del sistema una sola vez, con el enlace de la portada de la app.
    doble_toque(p, boton, 120)
    p.wait_for_timeout(300)
    igual(compartido(p)["llamadas"], [{"title": "Cosas", "text": "Te recomiendo Cosas", "url": e.base}], "Lo que recibe navigator.share")
    comprobar(not toast(p)["visible"], "Sale un aviso al compartir")
    p.evaluate("() => window.__compartir.cerrar()")
    p.wait_for_timeout(100)
    # Cerrado sin elegir: nada; si el sistema no abre el menú, se copia el enlace.
    p.evaluate("() => { window.__compartir.modo = 'cancelar'; }")
    boton.tap()
    p.wait_for_timeout(300)
    igual((len(compartido(p)["llamadas"]), compartido(p)["copiado"], toast(p)["visible"]), (2, None, False), "Menú cerrado sin elegir")
    p.evaluate("() => { window.__compartir.modo = 'falla'; }")
    boton.tap()
    esperar_toast(p, "Enlace copiado")
    igual((len(compartido(p)["llamadas"]), compartido(p)["copiado"]), (3, e.base), "El sistema no abre el menú: se copia el enlace")
    comprobar_aviso_entero(p, e.ancho, e.alto, "«Enlace copiado» en Ajustes")
    # Sin menú de compartir (algunos navegadores de ordenador): se copia.
    p2 = pagina_de_ajustes(e, [JS_COMPARTIR_FALSO, JS_SIN_COMPARTIR], user_agent=UA_ANDROID)
    boton_compartir(p2).tap()
    esperar_toast(p2, "Enlace copiado")
    igual(compartido(p2), {"llamadas": [], "copiado": e.base}, "Sin navigator.share")
    # Ni menú ni portapapeles: lo dice y el foco sigue en el botón.
    p3 = pagina_de_ajustes(e, [JS_COMPARTIR_FALSO, JS_SIN_COMPARTIR, JS_SIN_PORTAPAPELES], user_agent=UA_ANDROID)
    boton_compartir(p3).tap()
    esperar_toast(p3, "No se pudo copiar el enlace")
    igual(p3.evaluate("() => document.activeElement.id"), "compartir", "Foco tras no poder copiar")
    # En la app instalada «Convertir en aplicación» no está, pero se puede compartir igual.
    for guiones, agente in (([JS_MODO_APLICACION], UA_ANDROID), (["Object.defineProperty(Navigator.prototype, 'standalone', { configurable: true, get: () => true });"], UA_IPHONE)):
        p4 = pagina_de_ajustes(e, guiones + [JS_COMPARTIR_FALSO], user_agent=agente)
        boton = boton_compartir(p4)
        comprobar(boton.is_visible() and not p4.locator("#instalar").is_visible(), "En modo aplicación, «Compartir aplicación» sin «Convertir en aplicación»")
        separacion = boton.evaluate("(b) => b.getBoundingClientRect().top - document.querySelector('#ayuda-instalar').getBoundingClientRect().bottom")
        comprobar(12 <= separacion <= 32, f"En modo aplicación, separación entre el texto y el botón: {separacion}")
        boton.tap()
        p4.wait_for_timeout(300)
        igual(len(compartido(p4)["llamadas"]), 1, f"Compartir en modo aplicación ({agente[:30]})")
        p4.context.close()
    comprobar_sin_desborde(p, "ajustes con «Compartir aplicación»")


@prueba("aplicación: la vista previa del enlace compartido (etiquetas og: y la tarjeta de 1200x630)")
def t_aplicacion_vista_previa(e):
    p = e.pagina()
    og = p.evaluate("() => Object.fromEntries([...document.querySelectorAll('meta[property^=\"og:\"]')].map((m) => [m.getAttribute('property'), m.content]))")
    igual({k: og.get(k) for k in ("og:title", "og:url", "og:image", "og:image:width", "og:image:height")},
          {"og:title": "Cosas", "og:url": "https://cosas-app.netlify.app/", "og:image": "https://cosas-app.netlify.app/icons/compartir.png",
           "og:image:width": "1200", "og:image:height": "630"}, "Etiquetas og: de la app")
    comprobar(og.get("og:description"), "Falta og:description")
    respuesta = p.request.get(e.base + "icons/compartir.png")
    igual((respuesta.status, respuesta.headers.get("content-type")), (200, "image/png"), "Descarga de la tarjeta")
    cuerpo = respuesta.body()
    igual((int.from_bytes(cuerpo[16:20], "big"), int.from_bytes(cuerpo[20:24], "big")), (1200, 630), "Tamaño de la tarjeta")


@prueba("hoja de pasos: diálogo modal accesible (role, aria, foco dentro y atrapado, Escape, velo, «Cerrar», el foco vuelve)")
def t_hoja_accesible(e):
    p = pagina_de_ajustes(e, user_agent=UA_IPHONE)
    historial = p.evaluate("() => history.length")
    estado = abrir_hoja(p)
    dialogo = p.get_by_role("dialog", name="Añadir a la pantalla de inicio", exact=True)
    igual(dialogo.count(), 1, "Diálogos con el nombre de la hoja")
    igual((dialogo.get_attribute("aria-modal"), p.evaluate("() => document.querySelector('[role=dialog]').tagName !== 'DIALOG'")), ("true", True), "aria-modal (sin depender de <dialog>)")
    igual((estado["focoDentro"], estado["paginaInerte"], estado["paginaOculta"]), (True, True, "true"), "Foco dentro y resto de la página fuera de juego")
    comprobar(p.evaluate("() => !document.querySelector('main').contains(document.querySelector('[role=dialog]'))"), "La hoja está dentro de lo que queda inerte")
    cerrar = p.get_by_role("button", name="Cerrar", exact=True)
    caja = cerrar.bounding_box()
    igual((round(caja["width"], 2), round(caja["height"], 2), cerrar.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius")), (48, 48, "50%"), "Botón «Cerrar» redondo")
    for tecla in ("Tab", "Tab", "Shift+Tab", "Tab", "Shift+Tab", "Shift+Tab"):
        p.keyboard.press(tecla)
        comprobar(hoja(p)["focoDentro"], f"Con {tecla} el foco se sale de la hoja: {hoja(p)['foco']}")
    igual(hoja(p)["foco"], "cerrar-hoja", "Foco tras tabular dentro de la hoja")
    anillo = p.evaluate("() => { const cs = getComputedStyle(document.activeElement); return cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2; }")
    comprobar(anillo, "«Cerrar» no enseña el anillo de foco con teclado")
    p.keyboard.press("Escape")
    igual(hoja(p), HOJA_CERRADA, "Hoja tras Escape")
    # El velo cierra; la hoja en sí, no.
    abrir_hoja(p)
    p.get_by_text("Pulsa Compartir", exact=True).tap()
    p.wait_for_timeout(150)
    comprobar(hoja(p)["abierta"], "Tocar dentro de la hoja la cierra")
    p.touchscreen.tap(e.ancho / 2, 60)
    esperar(p, f"() => !({JS_HOJA})().abierta", que="tocar el velo cierra la hoja")
    igual(hoja(p), HOJA_CERRADA, "Hoja tras tocar el velo")
    igual(vista_visible(p), "ajustes", "Tocar el velo acciona lo que hay debajo")
    abrir_hoja(p)
    cerrar.tap()
    igual(hoja(p), HOJA_CERRADA, "Hoja tras «Cerrar»")
    # El velo aparece bajo el dedo: un doble toque en el botón la deja abierta.
    for pausa in (0, 150, 250):
        doble_toque(p, boton_instalar(p), pausa)
        p.wait_for_timeout(500)
        comprobar(hoja(p)["abierta"], f"Un doble toque ({pausa} ms) en «Convertir en aplicación» abre y cierra la hoja")
        p.keyboard.press("Escape")
    # «Cerrar» también aparece bajo el dedo (en muchos móviles, sobre el extremo derecho del botón): mismo trato.
    boton, equis = boton_instalar(p).bounding_box(), caja
    for pausa in (150, 200, 250):
        p.touchscreen.tap(boton["x"] + boton["width"] / 2, boton["y"] + boton["height"] / 2)
        p.wait_for_timeout(pausa)
        p.touchscreen.tap(equis["x"] + equis["width"] / 2, equis["y"] + equis["height"] / 2)
        p.wait_for_timeout(500)
        comprobar(hoja(p)["abierta"], f"Un segundo toque {pausa} ms después, donde aparece «Cerrar», cierra la hoja recién abierta")
        cerrar.tap()  # Pasada la guarda, «Cerrar» cierra.
        igual(hoja(p), HOJA_CERRADA, "Hoja tras «Cerrar» pasada la guarda")
    igual(p.evaluate("() => history.length"), historial, "La hoja añade entradas al historial")


@prueba("hoja de pasos sin «inert» (iOS 15): velo, aria-hidden y tabulador retenido hacen el mismo trabajo")
def t_hoja_sin_inert(e):
    p = pagina_de_ajustes(e, [JS_SIN_INERT], user_agent=UA_IPHONE)
    estado = abrir_hoja(p)
    igual((estado["paginaInerte"], estado["paginaOculta"], estado["focoDentro"]), (False, "true", True), "Sin «inert» la página queda oculta a los lectores de pantalla")
    for tecla in ("Tab", "Tab", "Shift+Tab", "Tab"):
        p.keyboard.press(tecla)
        comprobar(hoja(p)["focoDentro"], f"Sin «inert», con {tecla} el foco se sale de la hoja: {hoja(p)['foco']}")
    tapados = p.evaluate("""() => ['#instalar', '#vista-ajustes [data-volver]'].map((s) => { const r = document.querySelector(s).getBoundingClientRect();
        const encima = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return encima !== null && document.querySelector('#hoja').contains(encima); })""")
    igual(tapados, [True, True], "Controles de la página tapados por la hoja o su velo")
    p.get_by_role("button", name="Cerrar", exact=True).tap()
    igual(hoja(p), HOJA_CERRADA, "Hoja tras «Cerrar» sin «inert»")


@prueba("hoja de pasos: se cierra sola al cambiar de vista (atrás del sistema incluido) y no vuelve a salir")
def t_hoja_cambio_de_vista(e):
    p = pagina_de_ajustes(e, user_agent=UA_ANDROID)
    abrir_hoja(p)
    p.go_back()
    esperar_vista(p, "inicio")
    estado = hoja(p)
    igual((estado["abierta"], estado["paginaInerte"], estado["paginaOculta"]), (False, False, None), "Hoja tras el atrás del sistema")
    igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), "Ajustes", "Foco tras el atrás del sistema con la hoja abierta")
    comprobar(p.url.startswith(e.base), f"El atrás con la hoja abierta sacó de la app: {p.url}")
    p.go_forward()
    esperar_vista(p, "ajustes")
    comprobar(not hoja(p)["abierta"], "La hoja reaparece al volver a Ajustes")
    # Otra pestaña no la toca; un cambio de vista por hash, sí.
    p.wait_for_timeout(400)
    abrir_hoja(p)
    p.evaluate("() => { location.hash = '#lista'; }")
    esperar_vista(p, "lista")
    comprobar(not hoja(p)["abierta"] and not hoja(p)["paginaInerte"], "La hoja sigue abierta en otra vista")
    botones = p.evaluate("() => document.querySelector('#vista-lista [data-volver]').matches(':not([inert] *)')")
    comprobar(botones, "La vista nueva queda inerte tras cerrarse la hoja")


JS_MEDIDAS_HOJA = "() => {" + JS_UTIL + r"""
  const dialogo = document.querySelector('[role=dialog]');
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom }; };
  const visibles = [dialogo, ...dialogo.querySelectorAll('*')].filter((el) => el.checkVisibility() && el.getBoundingClientRect().width > 0);
  const paso = visibles.find((el) => el.tagName === 'LI');
  return {
    panel: caja(dialogo), alto: innerHeight, ancho: innerWidth,
    fuera: visibles.filter((el) => { const r = el.getBoundingClientRect(); return r.left < -0.5 || r.right > innerWidth + 0.5; }).map((el) => el.tagName + '.' + el.className),
    desbordePanel: dialogo.scrollWidth - dialogo.clientWidth, desbordePagina: document.documentElement.scrollWidth - innerWidth,
    tituloRecortado: (JS_RECORTADO)(dialogo.querySelector('h2')),
    pasosRecortados: visibles.filter((el) => el.tagName === 'SPAN').map((el) => (JS_RECORTADO)(el)),
    texto: { 'título de la hoja': contrasteTexto(dialogo.querySelector('h2')), 'texto de un paso': contrasteTexto(paso.querySelector('span')),
             'número de un paso': contrasteTexto(paso, '::before'), 'nota de la hoja': contrasteTexto(visibles.find((el) => el.tagName === 'P') || paso) },
    iconos: { 'icono de un paso': contrasteIcono(paso.querySelector('svg')), 'icono de «Cerrar»': contrasteIcono(dialogo.querySelector('button svg')) },
    animaciones: [dialogo, document.querySelector('#velo-hoja')].map((el) => Math.max(...getComputedStyle(el).animationDuration.split(',').map(parseFloat))),
  };
}""".replace("JS_RECORTADO", JS_RECORTADO)


@prueba("hoja de pasos: cabe y se lee a 320px, en apaisado y sobre Azul, Blanco y Negro (píxeles reales); sin animación con menos movimiento")
def t_hoja_aspecto(e):
    for nombre, color in (("Azul", "#2F6FED"), ("Blanco", "#FFFFFF"), ("Negro", "#111111")):
        for agente in (UA_IPHONE, UA_ANDROID):
            # Con algo apuntado: en iOS la hoja lleva además el aviso de que la app empezará vacía (su versión más alta).
            p = pagina_de_ajustes(e, datos=estado_con([cosa(1, "Comprar pan")], color=color), user_agent=agente, viewport={"width": 320, "height": 568})
            igual(abrir_hoja(p)["notas"], (NOTAS_IOS + [NOTA_DATOS_IOS]) if agente == UA_IPHONE else [NOTA_GENERICA], "Notas de la hoja a 320px")
            m = p.evaluate(JS_MEDIDAS_HOJA)
            donde = f"la hoja a 320px sobre {nombre} ({'iOS' if agente == UA_IPHONE else 'genérica'})"
            igual((m["fuera"], m["desbordePanel"] <= 0, m["desbordePagina"] <= 0), ([], True, True), f"Desbordes en {donde}")
            comprobar(m["panel"]["x"] >= 0 and m["panel"]["d"] <= 320.5 and m["panel"]["y"] >= 0 and abs(m["panel"]["b"] - m["alto"]) <= 0.5, f"{donde}: no está pegada abajo o se sale: {m['panel']}")
            comprobar(not m["tituloRecortado"] and not any(m["pasosRecortados"]), f"{donde}: algún texto sale recortado")
            for grupo, minimo in (("texto", 4.5), ("iconos", 3.0)):
                for que, valor in m[grupo].items():
                    detalle(f"{donde} · {que}: {valor:.2f}")
                    comprobar(valor >= minimo, f"{donde}: {que} = {valor:.2f}:1 (mínimo {minimo})")
            # Píxeles reales: la hoja es del color de la app y el resto de la página queda oscurecido tras el velo.
            dentro, fuera = muestrear(p, [(160, m["panel"]["y"] + 8), (160, max(2, m["panel"]["y"] - 30))])
            igual(dentro, hex_a_rgb(color), f"Color real de {donde}")
            comprobar(sum(fuera) < sum(hex_a_rgb(color)) or color == "#111111", f"{donde}: la página de detrás no se oscurece: {fuera}")
            comprobar(dentro != fuera, f"{donde}: no se distingue de la página de detrás")
            p.context.close()
    # Apaisado: cabe entera (o se desplaza por dentro) sin tapar más que la pantalla.
    p = pagina_de_ajustes(e, datos=estado_con([cosa(1, "Comprar pan")]), user_agent=UA_IPHONE, viewport={"width": e.alto, "height": e.ancho})
    abrir_hoja(p)
    m = p.evaluate(JS_MEDIDAS_HOJA)
    comprobar(m["panel"]["y"] >= 0 and abs(m["panel"]["b"] - m["alto"]) <= 0.5 and m["fuera"] == [], f"La hoja no cabe en apaisado: {m['panel']}")
    p.get_by_text(NOTA_DATOS_IOS, exact=True).scroll_into_view_if_needed()
    nota = p.get_by_text(NOTA_DATOS_IOS, exact=True).bounding_box()
    comprobar(nota["y"] >= m["panel"]["y"] and nota["y"] + nota["height"] <= e.ancho + 0.5, f"La nota final (el aviso de los datos) no se alcanza en apaisado: {nota}")
    p.context.close()
    p = pagina_de_ajustes(e, user_agent=UA_IPHONE, reduced_motion="reduce")
    boton_instalar(p).tap()
    p.wait_for_timeout(100)  # La animación de 0,01 ms necesita un fotograma.
    m = p.evaluate(JS_MEDIDAS_HOJA)
    comprobar(max(m["animaciones"]) <= 0.01, f"La hoja se anima con «reducir movimiento»: {m['animaciones']}")
    comprobar(abs(m["panel"]["b"] - m["alto"]) <= 0.5, "Con menos movimiento la hoja no queda en su sitio al instante")


# ----------------------------------------------------------------------------
# Documento, PWA y service worker
# ----------------------------------------------------------------------------

@prueba("documento: metas de iOS/Android, idioma y estilos táctiles")
def t_documento(e):
    p = e.pagina()
    metas = p.evaluate("() => Object.fromEntries([...document.querySelectorAll('meta[name]')].map((m) => [m.name, m.content]))")
    igual(p.evaluate("() => document.documentElement.lang"), "es", "html lang")
    igual(p.title(), "Cosas", "title")
    viewport = [t.strip() for t in metas.get("viewport", "").split(",")]
    for trozo in ("width=device-width", "initial-scale=1", "viewport-fit=cover", "interactive-widget=resizes-content"):
        comprobar(trozo in viewport, f"Falta «{trozo}» en meta viewport: {viewport}")
    comprobar(not re.search(r"user-scalable\s*=\s*(no|0)|maximum-scale", metas["viewport"]), f"El viewport desactiva el zoom: {metas['viewport']}")
    esperadas = {"apple-mobile-web-app-capable": "yes", "mobile-web-app-capable": "yes",
                 "apple-mobile-web-app-status-bar-style": "black-translucent", "apple-mobile-web-app-title": "Cosas",
                 "format-detection": "telephone=no"}
    for nombre, valor in esperadas.items():
        igual(metas.get(nombre), valor, f"meta {nombre}")
    igual(metas.get("theme-color", "").upper(), COLOR_DEFECTO, "meta theme-color por defecto")
    comprobar("color-scheme" in metas, "Falta meta color-scheme")
    enlaces = p.evaluate("() => [...document.querySelectorAll('link[rel]')].map((l) => [l.rel, l.getAttribute('href')])")
    rels = {rel: href for rel, href in enlaces}
    for rel in ("manifest", "apple-touch-icon", "icon", "stylesheet"):
        comprobar(rel in rels, f"Falta <link rel={rel}>")
    rutas = p.evaluate("() => [...document.querySelectorAll('[src], link[href]')].map((el) => el.getAttribute('src') || el.getAttribute('href'))")
    comprobar(all(not re.match(r"^(/|https?:|//)", r) for r in rutas), f"Hay rutas absolutas o externas: {rutas}")
    for rel in ("manifest", "apple-touch-icon", "icon"):
        igual(p.request.get(e.base + rels[rel]).status, 200, f"Descarga de {rels[rel]}")
    igual(p.evaluate("() => [...document.querySelectorAll('*')].filter((el) => [...el.attributes].some((a) => /^on/i.test(a.name))).length"), 0, "Manejadores de eventos en línea")
    estilos = p.evaluate("""() => { const b = document.querySelector('#abrir-lista'), c = document.querySelector('#campo'), cuerpo = getComputedStyle(document.body);
        return { toque: getComputedStyle(b).webkitTapHighlightColor, accion: getComputedStyle(b).touchAction, accionCampo: getComputedStyle(c).touchAction,
                 accionPagina: getComputedStyle(document.documentElement).touchAction,
                 seleccion: getComputedStyle(b).userSelect, rebote: cuerpo.overscrollBehaviorY, desborde: cuerpo.overflowY,
                 altoCuerpo: document.body.getBoundingClientRect().height, alto: innerHeight,
                 fuente: getComputedStyle(document.documentElement).fontFamily }; }""")
    igual(estilos["toque"], "rgba(0, 0, 0, 0)", "-webkit-tap-highlight-color")
    igual((estilos["accion"], estilos["accionCampo"]), ("manipulation", "manipulation"), "touch-action en controles")
    igual(estilos["accionPagina"], "manipulation", "touch-action en <html> (sin zoom por doble toque sobre el texto; el de pellizco sigue)")
    igual(estilos["seleccion"], "none", "user-select en botones")
    igual(estilos["rebote"], "none", "overscroll-behavior de la pantalla principal")
    igual(estilos["altoCuerpo"], estilos["alto"], "La app ocupa toda la altura (100dvh)")
    comprobar("system-ui" in estilos["fuente"] or "-apple-system" in estilos["fuente"], f"No usa la fuente del sistema: {estilos['fuente']}")
    # La pantalla principal no se desplaza.
    p.mouse.wheel(0, 600)
    p.evaluate("() => window.scrollTo(0, 500)")
    p.wait_for_timeout(100)
    igual(p.evaluate("() => [window.scrollY, document.querySelector('#vista-inicio').scrollTop]"), [0, 0], "La pantalla principal se desplaza")


@prueba("sin variables globales filtradas")
def t_globales(e):
    p = e.pagina()
    extra = p.evaluate("""() => { const marco = document.createElement('iframe'); document.body.append(marco);
        const base = new Set(Object.getOwnPropertyNames(marco.contentWindow)); marco.remove();
        return Object.getOwnPropertyNames(window).filter((k) => !base.has(k) && !/^(__playwright|__pw|playwright)/i.test(k) && isNaN(Number(k))); }""")
    igual(extra, [], "Globales añadidas por la app")


JS_ANTES_DE_PINTAR = "() => ({ fondo: getComputedStyle(document.documentElement).backgroundColor, cuerpo: getComputedStyle(document.body).backgroundColor, tono: document.documentElement.dataset.tono, tema: document.querySelector('meta[name=theme-color]').content })"


@prueba("el color guardado (o el del grupo, al cargar #grupo/ID) se aplica antes del primer pintado (script en <head>)")
def t_color_antes_de_pintar(e):
    contexto = e.contexto()
    contexto.route("**/app.js", lambda ruta: ruta.fulfill(status=200, content_type="text/javascript", body="/* bloqueado por la prueba */"))
    p = sembrar(e, estado_con(color="#EADFCB"), contexto=contexto)
    datos = p.evaluate(JS_ANTES_DE_PINTAR)
    igual((rgb_css(datos["fondo"]), rgb_css(datos["cuerpo"])), (hex_a_rgb("#EADFCB"),) * 2, "Fondo sin app.js (solo el script del head)")
    igual(datos["tono"], "claro", "Tono sin app.js")
    igual(datos["tema"].upper(), "#EADFCB", "theme-color sin app.js")
    p2 = sembrar(e, "{roto", contexto=contexto)
    igual(rgb_css(p2.evaluate("() => getComputedStyle(document.body).backgroundColor")), hex_a_rgb(COLOR_DEFECTO), "Fondo por defecto con datos corruptos y sin app.js")
    # Carga directa de la página de un grupo con color: ese color (con su tono y theme-color) va antes del primer pintado.
    for fondo_app, color_grupo, tono in (("#FFFFFF", "#111111", "oscuro"), ("#2F6FED", "#F2B705", "claro")):
        p3 = sembrar(e, estado_con([], grupos=[grupo(1, "Con color", color=color_grupo)], color=fondo_app), ruta="#grupo/g-1", contexto=contexto)
        datos = p3.evaluate(JS_ANTES_DE_PINTAR)
        igual((rgb_css(datos["fondo"]), rgb_css(datos["cuerpo"]), datos["tono"], datos["tema"].upper()), (hex_a_rgb(color_grupo), hex_a_rgb(color_grupo), tono, color_grupo),
              f"Sin app.js, #grupo/ID de un grupo {color_grupo} sobre {fondo_app} pinta el color de la app (destello al cargar)")
    # Id desconocido, grupo sin color o «grupos» corrupto: el color de la app, sin errores.
    for ruta, grupos in (("#grupo/no-existe", [grupo(1, "Con color", color="#111111")]), ("#grupo/g-1", [grupo(1, "Sin color")]), ("#grupo/g-1", "no")):
        p4 = sembrar(e, {**estado_con([], grupos=[], color="#F2B705"), "grupos": grupos}, ruta=ruta, contexto=contexto)
        datos = p4.evaluate(JS_ANTES_DE_PINTAR)
        igual((rgb_css(datos["cuerpo"]), datos["tono"], datos["tema"].upper()), (hex_a_rgb("#F2B705"), "claro", "#F2B705"), f"Sin app.js, {ruta} con grupos={grupos!r} debe pintar el color de la app")


@prueba("iOS instalada: velo tras la barra de estado solo con fondo claro (la hora va siempre en blanco)")
def t_velo_barra_de_estado(e):
    js_velo = "() => { const cs = getComputedStyle(document.body, '::before'); return { contenido: cs.content, fondo: cs.backgroundColor, posicion: cs.position, toques: cs.pointerEvents }; }"
    instalada = e.contexto()
    instalada.add_init_script("Object.defineProperty(Navigator.prototype, 'standalone', { configurable: true, get: () => true });")
    for color, con_velo in (("#FFFFFF", True), ("#EADFCB", True), ("#F2B705", True), (COLOR_DEFECTO, False), ("#111111", False)):
        p = sembrar(e, estado_con(color=color), contexto=instalada)
        velo = p.evaluate(js_velo)
        if con_velo:
            igual((velo["contenido"], velo["posicion"], velo["toques"]), ('""', "fixed", "none"), f"Velo de la barra de estado con fondo {color}")
            comprobar(re.search(r"rgba\(0, 0, 0, 0\.[2-5]", velo["fondo"]), f"El velo con fondo {color} no es un negro translúcido: {velo['fondo']}")
        else:
            igual(velo["contenido"], "none", f"Con fondo oscuro {color} no debe haber velo")
        p.close()
    p = sembrar(e, estado_con(color="#FFFFFF"))  # En Android o en Safari (sin instalar) nunca hay velo.
    igual(p.evaluate(js_velo)["contenido"], "none", "Velo fuera de la app instalada en iOS")


@prueba("manifest válido e iconos con el tamaño declarado")
def t_manifest(e):
    p = e.pagina()
    href = p.locator("link[rel=manifest]").get_attribute("href")
    respuesta = p.request.get(e.base + href)
    igual(respuesta.status, 200, "Descarga del manifest")
    manifest = json.loads(respuesta.text())
    esperado = {"name": "Cosas", "short_name": "Cosas", "lang": "es", "dir": "ltr", "start_url": "./", "scope": "./",
                "display": "standalone", "orientation": "portrait"}
    for clave, valor in esperado.items():
        igual(manifest.get(clave), valor, f"manifest.{clave}")
    # Un «id» relativo se resuelve contra el ORIGEN, no contra la carpeta de la app: alojada en una
    # subcarpeta, «./» la identificaría como la raíz del dominio. Sin «id», la identidad es start_url.
    comprobar("id" not in manifest, f"El manifest no debe llevar «id» (se resuelve contra el origen): {manifest.get('id')!r}")
    igual((manifest.get("background_color", "").upper(), manifest.get("theme_color", "").upper()), (COLOR_DEFECTO, COLOR_DEFECTO), "Colores del manifest")
    comprobar("productivity" in manifest.get("categories", []), "Falta la categoría productivity")
    comprobar(isinstance(manifest.get("description"), str) and len(manifest["description"]) > 10 and re.search(r"[áéíóúñ]| que | las | sin ", manifest["description"]), "La descripción no está en español")
    declarados = {(i.get("sizes"), i.get("purpose", "any")) for i in manifest["icons"]}
    for par in (("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")):
        comprobar(par in declarados, f"Falta el icono {par} en el manifest: {declarados}")
    for icono in manifest["icons"]:
        comprobar(not re.match(r"^(/|https?:)", icono["src"]), f"Ruta de icono no relativa: {icono['src']}")
        igual(icono.get("type"), "image/png", f"type de {icono['src']}")
        datos = p.request.get(e.base + icono["src"])
        igual(datos.status, 200, f"Descarga de {icono['src']}")
        ancho, alto = dimensiones_png(datos.body())
        igual(f"{ancho}x{alto}", icono["sizes"], f"Tamaño real de {icono['src']}")
        comprobar((APP / icono["src"]).is_file(), f"No existe el fichero {icono['src']}")
    for ruta, lado in (("icons/apple-touch-icon.png", 180), ("icons/favicon-32.png", 32), ("icons/icon-192.png", 192),
                       ("icons/icon-512.png", 512), ("icons/icon-maskable-512.png", 512)):
        igual(dimensiones_png((APP / ruta).read_bytes()), (lado, lado), f"Tamaño de {ruta}")
    analisis = p.evaluate("""async (rutas) => { const salida = {};
        for (const ruta of rutas) { const img = new Image(); img.src = ruta; await img.decode();
            const lienzo = document.createElement('canvas'); lienzo.width = img.naturalWidth; lienzo.height = img.naturalHeight;
            const ctx = lienzo.getContext('2d'); ctx.drawImage(img, 0, 0);
            const d = ctx.getImageData(0, 0, lienzo.width, lienzo.height).data, n = lienzo.width;
            let alfaMin = 255, blancos = 0, fueraDeZona = 0; const margen = Math.floor(n * 0.1);
            for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) { const i = (y * n + x) * 4;
                alfaMin = Math.min(alfaMin, d[i + 3]);
                const blanco = d[i] > 235 && d[i + 1] > 235 && d[i + 2] > 235 && d[i + 3] > 200;
                if (blanco) blancos++;
                const enBorde = x < margen || y < margen || x >= n - margen || y >= n - margen;
                if (enBorde && (Math.abs(d[i] - 47) > 3 || Math.abs(d[i + 1] - 111) > 3 || Math.abs(d[i + 2] - 237) > 3 || d[i + 3] !== 255)) fueraDeZona++; }
            const centro = ((n >> 1) * n + 4) * 4;
            salida[ruta] = { alfaMin, blancos: blancos / (n * n), fueraDeZona, fondo: [d[centro], d[centro + 1], d[centro + 2]] }; }
        return salida; }""", ["icons/apple-touch-icon.png", "icons/icon-maskable-512.png", "icons/icon-192.png", "icons/icon-512.png", "icons/favicon-32.png"])
    detalle(f"análisis de iconos: {analisis}")
    igual(analisis["icons/apple-touch-icon.png"]["alfaMin"], 255, "apple-touch-icon debe ser opaco (alfa mínimo)")
    igual(analisis["icons/icon-maskable-512.png"]["alfaMin"], 255, "El icono maskable debe ir a sangre (alfa mínimo)")
    igual(analisis["icons/icon-maskable-512.png"]["fueraDeZona"], 0, "Píxeles del maskable fuera del 80 % central que no son fondo")
    for ruta, datos in analisis.items():
        comprobar(0.02 <= datos["blancos"] <= 0.5, f"{ruta}: no se reconoce un check blanco ({datos['blancos']:.3f} de píxeles blancos)")
        comprobar(all(abs(a - b) <= 3 for a, b in zip(datos["fondo"], hex_a_rgb(COLOR_DEFECTO))), f"{ruta}: el fondo no es {COLOR_DEFECTO}: {datos['fondo']}")


def flujo_sin_conexion(e, directorio, prefijo):
    """Primera visita con red, después se apaga el servidor y se corta la red: todo debe seguir funcionando."""
    servidor = iniciar_servidor(directorio)
    base = url_de(servidor, prefijo)
    e.origenes_propios.append(url_de(servidor))
    apagado = False
    try:
        contexto = e.contexto()
        p = e.vigilar(contexto.new_page())
        pedidas = []
        p.on("request", lambda pet: pedidas.append(pet.url))
        p.goto(base)
        esperar(p, "() => navigator.serviceWorker && navigator.serviceWorker.controller !== null", ms=15000, que="el service worker controla la página sin recargar (clients.claim)")
        registro = p.evaluate("async () => { const r = await navigator.serviceWorker.ready; return { alcance: r.scope, estado: r.active && r.active.state, guion: r.active && r.active.scriptURL }; }")
        igual(registro["alcance"], base, "Alcance del service worker")
        comprobar(registro["guion"].endswith(prefijo + "sw.js"), f"Script del SW: {registro['guion']}")
        esperar(p, "async () => (await navigator.serviceWorker.ready).active.state === 'activated'", que="el SW queda activado")
        cache = p.evaluate("async () => { const nombres = await caches.keys(); const salida = {}; for (const n of nombres) salida[n] = (await (await caches.open(n)).keys()).map((r) => r.url); return salida; }")
        igual(len(cache), 1, f"Número de cachés ({list(cache)})")
        nombre_cache, urls = next(iter(cache.items()))
        comprobar(re.search(r"\d", nombre_cache), f"El nombre de la caché no lleva versión: {nombre_cache}")
        necesarias = ["", "index.html", "styles.css", "app.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
                      "icons/icon-maskable-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png"]
        faltan = [r for r in necesarias if base + r not in urls]
        igual(faltan, [], "Recursos que faltan en la precarga")
        fuera = [u for u in pedidas if not u.startswith(base)]
        igual(fuera, [], "Peticiones fuera de la carpeta de la app (rutas no relativas)")
        anotar(p, "Con red")
        abrir_ajustes(p)
        elegir_color(p, "Negro")
        volver(p)

        # Se corta todo: red emulada fuera y servidor apagado.
        contexto.set_offline(True)
        servidor.shutdown()
        servidor.server_close()
        apagado = True
        e.tolerar += [r"sw\.js", r"net::ERR_INTERNET_DISCONNECTED", r"net::ERR_CONNECTION_REFUSED", r"Failed to load resource"]
        for destino in (None, base + "index.html", base + "?origen=pwa", base + "desconocida"):
            if destino is None:
                p.reload()
            else:
                p.goto(destino)
            esperar_vista(p, "inicio")
            igual(round(boton_lista(p).bounding_box()["width"], 2), 48, f"Sin conexión ({destino or 'recarga'}): los estilos no cargan")
            esperar_fondo(p, "#111111")
        anotar(p, "Sin red")
        esperar_toast(p, "Guardado")
        abrir_lista(p)
        igual(textos_lista(p), ["Sin red", "Con red"], "Lista sin conexión")
        check_de(fila_de(p, "Sin red")).tap()
        borrar_de(fila_de(p, "Con red")).tap()
        esperar_filas(p, 1)
        p.goto(base + "#ajustes")
        p.reload()
        esperar_vista(p, "ajustes")
        igual(p.get_by_role("radio").count(), 12, "Muestras sin conexión")
        elegir_color(p, "Amarillo")
        esperar_fondo(p, "#F2B705")
        recursos = p.evaluate("""async (rutas) => { const salida = {}; for (const r of rutas) { try { const resp = await fetch(r); salida[r] = resp.ok && (await resp.blob()).size > 0; } catch (error) { salida[r] = String(error); } } return salida; }""",
                              ["manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png", "styles.css", "app.js"])
        igual([r for r, ok in recursos.items() if ok is not True], [], "Recursos que no se sirven sin conexión")
        igual([c["texto"] for c in leer_estado(p)["cosas"]], ["Sin red"], "Estado guardado sin conexión")
    finally:
        if not apagado:
            servidor.shutdown()
            servidor.server_close()


@prueba("service worker: registra, controla, precarga y la app funciona sin conexión (raíz)")
def t_sw_raiz(e):
    flujo_sin_conexion(e, APP, "")


@prueba("service worker y rutas relativas: alojada en una subcarpeta, también sin conexión")
def t_sw_subcarpeta(e):
    flujo_sin_conexion(e, RAIZ, "app/")


@prueba("service worker: borra las cachés antiguas al activarse")
def t_sw_caches_antiguas(e):
    contexto = e.contexto()
    p = e.vigilar(contexto.new_page())
    p.goto(e.base + "icons/favicon-32.png")
    p.evaluate("async () => { for (const n of ['cosas-v0.0.1', 'cosas-antigua']) { const c = await caches.open(n); await c.put('./viejo', new Response('viejo')); } }")
    p.goto(e.base)
    esperar(p, "() => navigator.serviceWorker.controller !== null", ms=15000, que="el SW toma el control")
    esperar(p, "async () => { const n = await caches.keys(); return !n.includes('cosas-v0.0.1') && !n.includes('cosas-antigua'); }", ms=5000, que="se borran las cachés antiguas de Cosas")
    igual(len(p.evaluate("async () => (await caches.keys()).filter((n) => n.startsWith('cosas'))")), 1, "Cachés de Cosas tras activar")


@prueba("service worker: no sirve ni guarda las rutas del alojamiento (/.netlify/)")
def t_sw_rutas_del_alojamiento(e):
    contexto = e.contexto()
    p = e.vigilar(contexto.new_page())
    p.goto(e.base)
    esperar(p, "() => navigator.serviceWorker.controller !== null", ms=15000, que="el SW toma el control")
    igual(p.evaluate("async () => (await fetch('/.netlify/scripts/hud?variant=public')).ok"), True, "La ruta del alojamiento responde")
    guardadas = p.evaluate("""async () => { const rutas = []; for (const n of await caches.keys()) { for (const r of await (await caches.open(n)).keys()) rutas.push(new URL(r.url).pathname); } return rutas; }""")
    comprobar(len(guardadas) > 0, "La caché de la app está vacía")
    igual([r for r in guardadas if r.startswith("/.netlify/")], [], "Rutas del alojamiento guardadas en la caché")


@prueba("funciona abriendo index.html con file:// (sin service worker)")
def t_file(e):
    contexto = e.contexto()
    p = e.vigilar(contexto.new_page())
    p.goto((APP / "index.html").as_uri())
    esperar_vista(p, "inicio")
    igual(round(boton_lista(p).bounding_box()["width"], 2), 48, "Estilos con file://")
    anotar(p, "Desde file")
    esperar_toast(p, "Guardado")
    abrir_lista(p)
    igual(textos_lista(p), ["Desde file"], "Lista con file://")
    check_de(filas(p).first).tap()
    p.go_back()
    esperar_vista(p, "inicio")
    abrir_ajustes(p)
    elegir_color(p, "Rosa")
    esperar_fondo(p, "#E85D9E")
    p.reload()
    esperar_vista(p, "ajustes")
    esperar_fondo(p, "#E85D9E")
    volver(p)
    abrir_lista(p)
    igual(check_de(filas(p).first).get_attribute("aria-pressed"), "true", "Persistencia con file://")


@prueba("dos pestañas: los cambios de una llegan a la otra (evento storage)")
def t_dos_pestanas(e):
    contexto = e.contexto()
    a = e.pagina(contexto=contexto)
    b = e.pagina(ruta="#lista", contexto=contexto)
    esperar_vista(b, "lista")
    anotar(a, "Desde la otra pestaña")
    esperar(b, "() => [...document.querySelectorAll('#lista-cosas > li p')].some((p) => p.textContent === 'Desde la otra pestaña')", que="la otra pestaña pinta la cosa nueva")
    abrir_ajustes(a)
    elegir_color(a, "Verde")
    esperar_fondo(b, "#2E8B57")


# ----------------------------------------------------------------------------
# Orden de las hechas y grupos (1.2)
# ----------------------------------------------------------------------------

# Textos del nivel superior de la lista (cosas y grupos), sin las filas que están saliendo.
JS_NIVEL = r"""
() => [...document.querySelectorAll('#lista-cosas > li')].filter((li) => !li.classList.contains('saliendo'))
  .map((li) => (li.querySelector(':scope > .texto') || li.querySelector(':scope > .fila-grupo .texto')).textContent)
"""

# Textos de las cosas anidadas bajo el grupo con ese nombre.
JS_ANIDADAS = r"""
(nombre) => {
  const li = [...document.querySelectorAll('#lista-cosas > li.grupo-fila')].find((l) => l.querySelector('.enlace-grupo').textContent === nombre);
  if (!li) return null;
  return [...li.querySelectorAll('.anidada > li')].filter((f) => !f.classList.contains('saliendo')).map((f) => f.querySelector('.texto').textContent);
}
"""

JS_BARRA_GRUPO = r"""
() => {
  const f = document.querySelector('#formulario-grupo'), b = document.querySelector('#crear-grupo');
  const c = document.querySelector('#campo-grupo'), k = document.querySelector('#confirmar-grupo');
  const seVe = (el) => el.checkVisibility({ visibilityProperty: true, opacityProperty: true }) && !el.disabled;
  return { editando: f.classList.contains('editando'), boton: seVe(b), campo: seVe(c), crear: seVe(k), valor: c.value,
           foco: document.activeElement.id || document.activeElement.tagName };
}
"""

JS_PAGINA_GRUPO = r"""
() => {
  const textos = (s) => [...document.querySelectorAll(s)].filter((f) => !f.classList.contains('saliendo')).map((f) => f.querySelector('.texto').textContent);
  return {
    titulo: document.querySelector('#titulo-grupo').textContent,
    miembros: textos('#lista-grupo > li'), candidatas: textos('#lista-candidatas > li'),
    desplegable: document.querySelector('#candidatas').checkVisibility(),
    expandido: document.querySelector('#anadir-cosas').getAttribute('aria-expanded'),
    sinMas: document.querySelector('#sin-candidatas').checkVisibility(),
    vacio: document.querySelector('#grupo-vacio').checkVisibility(),
    centrado: document.querySelector('#cuerpo-grupo').classList.contains('centrado'),
    tono: document.querySelector('#vista-grupo').dataset.tono,
    tema: document.querySelector('meta[name=theme-color]').content.toUpperCase(),
    foco: document.activeElement.id || document.activeElement.tagName,
  };
}
"""

JS_HOJA_GRUPO = r"""
() => {
  const hoja = document.querySelector('#hoja-grupo'), dialogo = hoja.querySelector('[role=dialog]'), principal = document.querySelector('main');
  const abierta = hoja.checkVisibility();
  return {
    abierta, modal: dialogo.getAttribute('aria-modal'),
    titulo: abierta ? document.getElementById(dialogo.getAttribute('aria-labelledby')).textContent.trim() : null,
    nombre: document.querySelector('#nombre-grupo').value,
    muestras: [...hoja.querySelectorAll('[role=radiogroup] input')].map((r) => r.getAttribute('aria-label')),
    elegida: (hoja.querySelector('[role=radiogroup] input:checked') || {}).value,
    focoDentro: dialogo.contains(document.activeElement), foco: document.activeElement.id || document.activeElement.tagName,
    paginaInerte: principal.hasAttribute('inert'), paginaOculta: principal.getAttribute('aria-hidden'),
    tono: hoja.dataset.tono,
  };
}
"""

# Geometría de la barra «Añadir cosas» y de su desplegable (abierto hacia arriba, sobre la píldora).
JS_BARRA_ANADIR = r"""
() => {
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom, ancho: r.width, alto: r.height }; };
  const panel = document.querySelector('#candidatas'), zona = document.querySelector('#zona-grupo');
  return { pildora: caja(document.querySelector('#barra-anadir > .pildora')), escribir: caja(document.querySelector('#formulario-en-grupo .pildora')),
           cabecera: caja(document.querySelector('#vista-grupo header')),
           panel: panel.checkVisibility() ? caja(panel) : null, desplazable: panel.scrollHeight > panel.clientHeight + 0.5,
           desplazamiento: panel.scrollTop, desplazamientoZona: zona.scrollTop, alto: innerHeight,
           primerMas: document.activeElement === document.querySelector('#lista-candidatas > li .mas') };
}
"""

# Dónde está el foco respecto al desplegable, y si alguna candidata aún se está yendo (su fila sigue en la lista, invisible).
JS_FOCO_CANDIDATAS = r"""
() => {
  const foco = document.activeElement, fila = foco.closest ? foco.closest('li') : null;
  return { foco: foco.id || foco.tagName, texto: fila ? fila.querySelector('.texto').textContent : null,
           enFilaQueSeQueda: foco.matches('#lista-candidatas > li:not(.saliendo) .mas'), filaYendose: document.querySelector('#lista-candidatas > li.saliendo') !== null };
}
"""

BARRA_GRUPO_REPOSO = {"editando": False, "boton": True, "campo": False, "crear": False, "valor": ""}

# Deslizamientos (Web Animations) en curso sobre las filas de la lista: los crea app.js al marcar o desmarcar.
# Las animaciones y transiciones CSS (aparecer, saliendo…) no cuentan.
JS_DESLIZANDOSE = r"""
() => document.getAnimations().filter((a) => !(a instanceof CSSAnimation) && !(a instanceof CSSTransition)
  && a.effect && a.effect.target && a.effect.target.tagName === 'LI' && a.effect.target.closest('#lista-cosas')).length
"""

# Lo que la 1.1 escribe en la clave principal: versión 1, sin «grupos», y cada cosa con solo id, texto, hecha y creada (una, borrada).
JS_ESCRITURA_1_1 = r"""
([clave, borrada]) => {
  const s = JSON.parse(localStorage.getItem(clave));
  const cosas = s.cosas.filter((c) => c.texto !== borrada).map(({ id, texto, hecha, creada }) => ({ id, texto, hecha, creada }));
  localStorage.setItem(clave, JSON.stringify({ version: 1, cosas, ajustes: s.ajustes }));
}
"""

# Desplazamiento de la lista, y si el elemento enfocado es el enlace de un grupo que se ve entero
# (bajo la máscara de la cabecera y por encima de la barra de crear grupos).
JS_ENLACE_A_LA_VISTA = r"""
() => {
  const zona = document.querySelector('#zona-lista'), enfocado = document.activeElement;
  const z = zona.getBoundingClientRect(), r = enfocado.getBoundingClientRect(), barra = document.querySelector('#formulario-grupo .pildora').getBoundingClientRect();
  return { desplazamiento: zona.scrollTop, enfocado: enfocado.classList.contains('enlace-grupo'),
           visible: r.top >= z.top + 16 - 0.5 && r.bottom <= barra.top + 0.5, enlace: [r.top, r.bottom], zona: [z.top + 16, barra.top] };
}
"""


def deslizandose(pagina):
    return pagina.evaluate(JS_DESLIZANDOSE)


def textos_nivel(pagina):
    return pagina.evaluate(JS_NIVEL)


def textos_anidados(pagina, nombre):
    return pagina.evaluate(JS_ANIDADAS, nombre)


def esperar_nivel(pagina, esperado, que="el orden del nivel superior"):
    esperar(pagina, f"(esperado) => JSON.stringify(({JS_NIVEL})()) === JSON.stringify(esperado)", arg=esperado, que=f"{que}: {esperado}")


def esperar_anidadas(pagina, nombre, esperado):
    esperar(pagina, f"([nombre, esperado]) => JSON.stringify(({JS_ANIDADAS})(nombre)) === JSON.stringify(esperado)", arg=[nombre, esperado],
            que=f"las cosas de «{nombre}» son {esperado}")


def fila_grupo(pagina, nombre):
    return pagina.locator("#lista-cosas > li.grupo-fila").filter(has=pagina.get_by_role("link", name=nombre, exact=True))


def ojo_de(fila):
    return fila.locator(".ojo")


def borrar_grupo_de(fila):
    return fila.get_by_role("button", name="Eliminar grupo", exact=True)


def fila_anidada(pagina, nombre_grupo, texto):
    return fila_grupo(pagina, nombre_grupo).locator(".anidada > li").filter(has=pagina.get_by_text(texto, exact=True))


def barra_grupo(pagina):
    datos = pagina.evaluate(JS_BARRA_GRUPO)
    datos.pop("foco")
    return datos


def esperar_barra_grupo(pagina, esperado, que):
    try:
        pagina.wait_for_function(f"(esperado) => {{ const b = ({JS_BARRA_GRUPO})(); delete b.foco; return JSON.stringify(b) === JSON.stringify(esperado); }}",
                                 arg=esperado, timeout=2500)
    except TiempoAgotado:
        igual(barra_grupo(pagina), esperado, que)


def boton_crear_grupo(pagina):
    return pagina.get_by_role("button", name="Crear grupo de cosas", exact=True)


def campo_grupo(pagina):
    return pagina.locator("#campo-grupo")


def crear_grupo(pagina, nombre, con="intro"):
    boton_crear_grupo(pagina).tap()
    campo_grupo(pagina).fill(nombre)
    if con == "intro":
        campo_grupo(pagina).press("Enter")
    else:
        pagina.locator("#confirmar-grupo").tap()


def pagina_grupo(pagina):
    return pagina.evaluate(JS_PAGINA_GRUPO)


def esperar_pagina_grupo(pagina, que, **esperado):
    esperar(pagina, f"(esperado) => {{ const d = ({JS_PAGINA_GRUPO})(); return Object.keys(esperado).every((k) => JSON.stringify(d[k]) === JSON.stringify(esperado[k])); }}",
            arg=esperado, que=f"{que}: {esperado}")


def abrir_grupo(pagina, nombre):
    pagina.get_by_role("link", name=nombre, exact=True).tap()
    esperar_vista(pagina, "grupo")


def boton_anadir(pagina):
    return pagina.get_by_role("button", name="Añadir cosas", exact=True)


def mas_de(pagina, texto):
    return pagina.locator("#lista-candidatas > li").filter(has=pagina.get_by_text(texto, exact=True)).get_by_role("button", name="Añadir al grupo", exact=True)


def quitar_de(pagina, texto):
    return pagina.locator("#lista-grupo > li").filter(has=pagina.get_by_text(texto, exact=True)).get_by_role("button", name="Quitar del grupo", exact=True)


def barra_anadir(pagina):
    return pagina.evaluate(JS_BARRA_ANADIR)


def comprobar_desplegable_sobre_la_barra(m, que):
    """El desplegable abierto queda justo encima de la píldora, bajo la cabecera, a lo ancho de la barra y sin pasar de ~55 % de la pantalla."""
    panel, pildora = m["panel"], m["pildora"]
    comprobar(panel is not None, f"{que}: el desplegable no se ve")
    comprobar(panel["b"] <= pildora["y"] + 0.5 and pildora["y"] - panel["b"] <= 16, f"{que}: el desplegable no queda justo encima de la barra: {panel} / {pildora}")
    comprobar(panel["y"] >= m["cabecera"]["b"] - 0.5, f"{que}: el desplegable pisa la cabecera: {panel} / {m['cabecera']}")
    comprobar(abs(panel["x"] - pildora["x"]) <= 0.5 and abs(panel["d"] - pildora["d"]) <= 0.5, f"{que}: el desplegable no va a lo ancho de la barra: {panel} / {pildora}")
    comprobar(panel["alto"] <= m["alto"] * 0.6, f"{que}: el desplegable ocupa más de media pantalla: {panel} en {m['alto']}px")


def boton_editar_grupo(pagina):
    return pagina.get_by_role("button", name="Editar grupo", exact=True)


def hoja_grupo(pagina):
    return pagina.evaluate(JS_HOJA_GRUPO)


def abrir_hoja_grupo(pagina):
    boton_editar_grupo(pagina).tap()
    esperar(pagina, f"() => ({JS_HOJA_GRUPO})().abierta", que="se abre la hoja de edición del grupo")
    pagina.wait_for_timeout(450)  # Fin de la animación de entrada y de la guarda del doble toque.
    return hoja_grupo(pagina)


def nombre_en_hoja(pagina):
    return pagina.locator("#hoja-grupo").get_by_label("Nombre del grupo", exact=True)


def guardar_hoja(pagina):
    pagina.get_by_role("button", name="Guardar", exact=True).tap()
    esperar(pagina, f"() => !({JS_HOJA_GRUPO})().abierta", que="la hoja se cierra al guardar")


def sembrar_grupos(e, ruta="#lista", contexto=None, **opciones):
    """Cuatro cosas sueltas (dos hechas), un grupo cerrado con tres cosas (g-1, el más nuevo) y otro abierto con una (g-2, el más viejo); versión 2."""
    cosas = [cosa(8, "Suelta nueva"), cosa(7, "En Casa 3", grupo="g-1"), cosa(6, "Suelta hecha", hecha=True), cosa(5, "En Casa 2", hecha=True, grupo="g-1"),
             cosa(4, "En Compras", grupo="g-2"), cosa(3, "En Casa 1", grupo="g-1"), cosa(2, "Suelta vieja"), cosa(1, "Suelta hecha antes", hecha=True)]
    grupos = [grupo(9, "Casa", id="g-1"), grupo(1, "Compras", abierto=True, id="g-2")]
    return sembrar(e, estado_con(cosas, grupos=grupos, **opciones), ruta=ruta, contexto=contexto)


NIVEL_SEMBRADO = ["Casa", "Compras", "Suelta nueva", "Suelta vieja", "Suelta hecha antes", "Suelta hecha"]


@prueba("orden: al marcar, la cosa baja al final (deslizándose); al desmarcar vuelve a su sitio; la última marcada es la última fila")
def t_hechas_al_final(e):
    base = [cosa(4, "D"), cosa(3, "C", hecha=True), cosa(2, "B"), cosa(1, "A")]
    p = sembrar(e, estado_con(base), ruta="#lista")
    esperar_filas(p, 4)
    igual(textos_nivel(p), ["D", "B", "A", "C"], "Orden inicial (la hecha de la versión 1 va al final)")
    check_de(fila_de(p, "B")).tap()
    comprobar(deslizandose(p) > 0, "Las filas no se deslizan al marcar (FLIP)")
    esperar_nivel(p, ["D", "A", "C", "B"], "orden tras marcar B")
    check_de(fila_de(p, "A")).tap()
    esperar_nivel(p, ["D", "C", "B", "A"], "orden tras marcar A (la última marcada es la última fila)")
    estado = {c["texto"]: c for c in leer_estado(p)["cosas"]}
    comprobar(estado["C"]["hechaEn"] < estado["B"]["hechaEn"] < estado["A"]["hechaEn"], f"hechaEn no crece con cada marcado: {estado}")
    igual((estado["D"]["hechaEn"], estado["A"]["hecha"]), (None, True), "hechaEn de una pendiente y hecha de A")
    check_de(fila_de(p, "B")).tap()
    esperar_nivel(p, ["D", "B", "C", "A"], "orden tras desmarcar B (vuelve a su sitio por creación)")
    igual(leer_estado(p)["cosas"][2]["hechaEn"], None, "hechaEn se vacía al desmarcar")
    p.wait_for_timeout(350)
    igual(deslizandose(p) + p.evaluate("() => document.querySelectorAll('#lista-cosas [style*=transform]').length"), 0, "Quedan filas deslizándose o con un transform del deslizamiento")
    p.reload()
    esperar_filas(p, 4)
    igual(textos_nivel(p), ["D", "B", "C", "A"], "Orden tras recargar")
    # Con «reducir movimiento» no se desliza nada, pero el orden es el mismo.
    p2 = sembrar(e, estado_con(base), ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
    esperar_filas(p2, 4)
    check_de(fila_de(p2, "D")).tap()
    igual(deslizandose(p2), 0, "Con menos movimiento hay filas deslizándose")
    esperar_nivel(p2, ["B", "A", "C", "D"], "orden con menos movimiento")
    # Dentro de un grupo (vista del ojo y página del grupo) rige el mismo orden.
    p3 = sembrar_grupos(e)
    esperar_filas(p3, 6)
    igual(textos_nivel(p3), NIVEL_SEMBRADO, "Nivel superior con grupos (los grupos van primero, el más nuevo arriba)")
    ojo_de(fila_grupo(p3, "Casa")).tap()
    esperar_anidadas(p3, "Casa", ["En Casa 3", "En Casa 1", "En Casa 2"])
    check_de(fila_anidada(p3, "Casa", "En Casa 3")).tap()
    esperar_anidadas(p3, "Casa", ["En Casa 1", "En Casa 2", "En Casa 3"])
    igual(textos_nivel(p3), NIVEL_SEMBRADO, "Marcar una cosa anidada no mueve el nivel superior")
    check_de(fila_anidada(p3, "Casa", "En Casa 2")).tap()
    esperar_anidadas(p3, "Casa", ["En Casa 2", "En Casa 1", "En Casa 3"])
    abrir_grupo(p3, "Casa")
    igual(pagina_grupo(p3)["miembros"], ["En Casa 2", "En Casa 1", "En Casa 3"], "Orden en la página del grupo")
    check_de(p3.locator("#lista-grupo > li").first).tap()
    esperar_pagina_grupo(p3, "orden en la página tras marcar", miembros=["En Casa 1", "En Casa 3", "En Casa 2"])


@prueba("orden: marcar dos cosas seguidas (100 ms) no hace saltar las filas que aún se deslizan; al acabar no queda ninguna animación")
def t_deslizamiento_seguido(e):
    p = sembrar(e, estado_con([cosa(i, str(i)) for i in range(1, 9)]), ruta="#lista")
    esperar_filas(p, 8)
    igual(textos_nivel(p), [str(i) for i in range(8, 0, -1)], "Orden inicial")
    m = p.evaluate("""async () => {
        const fila = (id) => document.querySelector('#lista-cosas > li[data-id="' + id + '"]');
        const filas = [...document.querySelectorAll('#lista-cosas > li')];
        const tops = () => new Map(filas.map((f) => [f.dataset.id, f.getBoundingClientRect().top]));
        const cuadro = () => new Promise((r) => requestAnimationFrame(() => r()));
        const espera = (ms) => new Promise((r) => setTimeout(r, ms));
        fila('id-8').querySelector('.hecho').click();
        await espera(100);
        const vistas = tops();  // Donde se ven las filas, a medio deslizamiento.
        fila('id-7').querySelector('.hecho').click();
        const mismoTick = tops();
        await cuadro();
        const cuadroSiguiente = tops();
        const saltos = {};
        for (const [id, top] of vistas) saltos[id] = [Math.abs(mismoTick.get(id) - top), Math.abs(cuadroSiguiente.get(id) - top)];
        await espera(150);
        const enVuelo = (JS_DESLIZANDOSE)();
        await espera(400);
        return { saltos, enVuelo, restos: (JS_DESLIZANDOSE)() + document.querySelectorAll('#lista-cosas [style*=transform]').length };
    }""".replace("JS_DESLIZANDOSE", JS_DESLIZANDOSE.strip()))
    detalle(f"saltos [mismo tick, cuadro siguiente] por fila: {m['saltos']}")
    for id_, (mismo, siguiente) in m["saltos"].items():
        comprobar(mismo < 1, f"Al marcar la segunda cosa, la fila {id_} salta {mismo:.1f}px en el mismo instante (debe arrancar donde se veía)")
        comprobar(siguiente < (120 if id_ in ("id-7", "id-8") else 30), f"En el cuadro siguiente la fila {id_} se mueve {siguiente:.1f}px de golpe")
    comprobar(m["enVuelo"] > 0, "150 ms después del segundo marcado ya no hay filas deslizándose (la limpieza del primero las cortó)")
    igual(m["restos"], 0, "Al acabar quedan animaciones o transforms en las filas")
    esperar_nivel(p, ["6", "5", "4", "3", "2", "1", "8", "7"], "orden tras marcar 8 y 7 seguidas")
    por_texto = {c["texto"]: c for c in leer_estado(p)["cosas"]}
    comprobar(por_texto["7"]["hechaEn"] > por_texto["8"]["hechaEn"], "La segunda marcada no queda la última en el almacenamiento")


@prueba("migración: los datos de la versión 1 se leen y se guardan como versión 2 (las hechas reciben hechaEn)")
def t_migracion_v1(e):
    base = [cosa(3, "Tres", hecha=True), cosa(2, "Dos"), cosa(1, "Uno", hecha=True)]
    p = sembrar(e, estado_con(base), ruta="#lista")
    esperar_filas(p, 3)
    igual(textos_nivel(p), ["Dos", "Uno", "Tres"], "Orden tras migrar (hechas por creación, al final)")
    igual(leer_estado(p)["version"], 1, "Leer no reescribe el almacenamiento por sí solo")
    check_de(fila_de(p, "Dos")).tap()
    guardado = leer_estado(p)
    igual((guardado["version"], guardado["grupos"]), (2, []), "Versión y grupos tras el primer guardado")
    por_texto = {c["texto"]: c for c in guardado["cosas"]}
    igual((por_texto["Tres"]["hechaEn"], por_texto["Uno"]["hechaEn"]), (por_texto["Tres"]["creada"], por_texto["Uno"]["creada"]), "hechaEn de las hechas migradas = creada")
    igual([c["grupo"] for c in guardado["cosas"]], [None, None, None], "grupo tras migrar")
    comprobar(por_texto["Dos"]["hechaEn"] > por_texto["Tres"]["hechaEn"], "La recién marcada no queda la última")
    igual(sorted(guardado["cosas"][0].keys()), ["creada", "grupo", "hecha", "hechaEn", "id", "texto"], "Forma de cada cosa en la versión 2")


CORRUPTOS_V2 = [
    ("grupos no es un array", {"version": 2, "cosas": [cosa(1, "Suelta", grupo="g-1")], "grupos": "nope", "ajustes": {}}, ["Suelta"], []),
    ("grupos null", {"version": 2, "cosas": [cosa(1, "Suelta")], "grupos": None, "ajustes": {}}, ["Suelta"], []),
    ("grupo inexistente y color malo", {"version": 2, "cosas": [cosa(3, "De g-4", grupo="g-4"), cosa(2, "Huérfana", grupo="no-existe"), cosa(1, "Del grupo", grupo="g-5")],
                                        "grupos": [grupo(5, "Bueno", color="rojo"), {"id": "g-2", "nombre": "   "}, {"id": "g-3"}, 7, None, "x",
                                                   {"id": "g-4", "nombre": "Raro", "color": "#12345", "creada": "ayer", "abierto": "sí"},
                                                   grupo(3, "Duplicado", color="#ABCDEF", id="g-4"), grupo(4, "Id numérico", id=9)],
                                        "ajustes": {}}, ["Raro", "Bueno", "Id numérico", "Duplicado", "Huérfana"], ["Raro", "Bueno", "Id numérico", "Duplicado"]),
    ("hechaEn y grupo con tipos raros", {"version": 2, "cosas": [{"id": "a", "texto": "Hecha rara", "hecha": True, "hechaEn": "hoy", "grupo": 5, "creada": 3},
                                                                  {"id": "b", "texto": "Pendiente", "hechaEn": 99, "grupo": "", "creada": 4}],
                                         "grupos": [], "ajustes": {}}, ["Pendiente", "Hecha rara"], []),
]


@prueba("versión 2 corrupta (grupos que no son array, ids de grupo falsos, colores malos) no rompe la app")
def t_v2_corrupto(e):
    for nombre, bruto, nivel, nombres_grupos in CORRUPTOS_V2:
        p = sembrar(e, bruto, ruta="#lista")
        esperar_vista(p, "lista")
        esperar_nivel(p, nivel, f"[{nombre}] nivel superior")
        guardado = None
        if nombres_grupos:
            ojo_de(fila_grupo(p, nombres_grupos[-1])).tap()  # Fuerza un guardado (sin tocar «Raro», cuyo «abierto» se comprueba).
        else:
            check_de(filas(p).first).tap()
        guardado = leer_estado(p)
        igual((guardado["version"], [g["nombre"] for g in guardado["grupos"]]), (2, nombres_grupos), f"[{nombre}] grupos guardados")
        for g in guardado["grupos"]:
            comprobar(isinstance(g["id"], str) and g["id"], f"[{nombre}] id de grupo inválido: {g}")
            comprobar(g["color"] is None or re.fullmatch(r"#[0-9A-F]{6}", g["color"]), f"[{nombre}] color de grupo inválido: {g}")
            comprobar(isinstance(g["abierto"], bool) and isinstance(g["creada"], (int, float)), f"[{nombre}] abierto/creada inválidos: {g}")
        ids_grupos = {g["id"] for g in guardado["grupos"]}
        igual(len(ids_grupos), len(guardado["grupos"]), f"[{nombre}] ids de grupo repetidos")
        for c in guardado["cosas"]:
            comprobar(c["grupo"] is None or c["grupo"] in ids_grupos, f"[{nombre}] cosa con grupo que no existe: {c}")
            comprobar((c["hechaEn"] is None) == (not c["hecha"]), f"[{nombre}] hechaEn no casa con hecha: {c}")
        if nombre == "grupo inexistente y color malo":
            por_nombre = {g["nombre"]: g for g in guardado["grupos"]}
            igual((por_nombre["Bueno"]["color"], por_nombre["Raro"]["color"], por_nombre["Duplicado"]["color"]), (None, None, "#ABCDEF"), "[colores] los inválidos se descartan")
            igual(por_nombre["Raro"]["abierto"], False, "[abierto] un valor no booleano se trata como cerrado")
            igual(sorted(c["texto"] for c in guardado["cosas"] if c["grupo"] is not None), ["De g-4", "Del grupo"], "[grupo] solo las cosas con grupo válido lo conservan")
            igual(next(c["grupo"] for c in guardado["cosas"] if c["texto"] == "De g-4"), "g-4", "[grupo] la cosa de g-4 no pertenece al primer g-4 (Raro)")
            abrir_grupo(p, "Duplicado")
            igual(pagina_grupo(p)["miembros"], [], "[grupo] el grupo con id repetido (renombrado) hereda cosas ajenas")
        p.context.close()


@prueba("respaldo de grupos: si una pestaña con la 1.1 reescribe la versión 1, los grupos, la pertenencia y el orden de las hechas vuelven del respaldo (al cargar y en vivo); respaldos corruptos no rompen nada")
def t_respaldo_grupos(e):
    p = sembrar_grupos(e)
    esperar_filas(p, 6)
    check_de(fila_de(p, "Suelta vieja")).tap()  # Un guardado de la 1.2 escribe el respaldo.
    nivel = ["Casa", "Compras", "Suelta nueva", "Suelta hecha antes", "Suelta hecha", "Suelta vieja"]
    esperar_nivel(p, nivel, "nivel tras marcar «Suelta vieja»")
    igual(sorted(p.evaluate("() => Object.keys(localStorage)")), sorted([CLAVE, CLAVE_RESPALDO]), "Claves tras guardar")
    respaldo = json.loads(p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE_RESPALDO))
    igual(sorted(respaldo.keys()), ["grupos", "hechas", "pertenencia"], "Forma del respaldo")
    igual(([g["id"] for g in respaldo["grupos"]], sorted(respaldo["pertenencia"]), sorted(h[0] for h in respaldo["hechas"])),
          (["g-1", "g-2"], [["id-3", "g-1"], ["id-4", "g-2"], ["id-5", "g-1"], ["id-7", "g-1"]], ["id-1", "id-2", "id-5", "id-6"]), "Contenido del respaldo")
    # Una pestaña con la 1.1 reescribe la clave principal como versión 1 (sin «grupos», las cosas sin «grupo» ni «hechaEn», una borrada) y se recarga.
    p.evaluate(JS_ESCRITURA_1_1, [CLAVE, "En Casa 1"])
    igual(leer_estado(p)["version"], 1, "La escritura simulada de la 1.1 no ha dejado versión 1")
    p.reload()
    esperar_filas(p, 6)
    igual(textos_nivel(p), nivel, "Tras la reescritura de la 1.1 la lista no recupera los grupos ni el orden de las hechas")
    igual((textos_anidados(p, "Casa"), textos_anidados(p, "Compras")), (["En Casa 3", "En Casa 2"], ["En Compras"]), "Las cosas que sobreviven no vuelven a su grupo (la borrada no debe resucitar)")
    guardado = leer_estado(p)
    igual((guardado["version"], [g["nombre"] for g in guardado["grupos"]], [g["abierto"] for g in guardado["grupos"]]), (2, ["Casa", "Compras"], [False, True]), "Lo recuperado no se vuelve a guardar como versión 2")
    por_texto = {c["texto"]: c for c in guardado["cosas"]}
    comprobar(por_texto["Suelta vieja"]["hechaEn"] > por_texto["Suelta hecha"]["hechaEn"] > por_texto["Suelta hecha antes"]["hechaEn"], f"El momento de marcado no vuelve del respaldo: {por_texto}")
    igual((por_texto["En Casa 2"]["grupo"], por_texto["Suelta nueva"]["grupo"]), ("g-1", None), "La pertenencia no vuelve del respaldo")
    # En vivo: la otra pestaña (una 1.1) escribe y esta se entera por el aviso de almacenamiento sin perder los grupos.
    contexto = e.contexto()
    a = sembrar_grupos(e, contexto=contexto)
    b = e.pagina(ruta="#lista", contexto=contexto)
    esperar_filas(b, 6)
    check_de(fila_de(b, "Suelta vieja")).tap()
    esperar_nivel(b, nivel, "b tras marcar")
    esperar_nivel(a, nivel, "a se entera del marcado")
    a.evaluate(JS_ESCRITURA_1_1, [CLAVE, "En Casa 1"])
    esperar_anidadas(b, "Casa", ["En Casa 3", "En Casa 2"])
    igual(textos_nivel(b), nivel, "Tras la escritura de la 1.1 en otra pestaña, b pierde grupos u orden")
    igual((leer_estado(b)["version"], [g["nombre"] for g in leer_estado(b)["grupos"]]), (2, ["Casa", "Compras"]), "Tras recuperar en vivo no se vuelve a guardar la versión 2")
    contexto.close()
    # Respaldos corruptos: la app carga, no se rompe y se queda con los grupos válidos (o ninguno).
    v1 = estado_con([cosa(2, "Dos", hecha=True), cosa(1, "Uno")])
    casos = (
        ("no es JSON", "{roto", [], ["Uno", "Dos"]),
        ("null", "null", [], ["Uno", "Dos"]),
        ("grupos no es un array", {"grupos": "x", "pertenencia": [], "hechas": []}, [], ["Uno", "Dos"]),
        ("pares con tipos raros", {"grupos": [grupo(3, "Bueno"), {"id": "g-4"}, 7, None], "pertenencia": [["id-1", 5], "x", None, ["id-2", "g-9"], [3, "g-3"]], "hechas": [["id-2", "hoy"], ["id-1", 5], 4]},
         ["Bueno"], ["Bueno", "Uno", "Dos"]),
    )
    for nombre, bruto, nombres_grupos, nivel_esperado in casos:
        contexto = e.contexto()
        pagina = e.vigilar(contexto.new_page())
        pagina.goto(e.base + "icons/favicon-32.png")
        pagina.evaluate("([clave, principal, claveRespaldo, respaldo]) => { localStorage.setItem(clave, principal); localStorage.setItem(claveRespaldo, respaldo); }",
                        [CLAVE, json.dumps(v1), CLAVE_RESPALDO, bruto if isinstance(bruto, str) else json.dumps(bruto)])
        pagina.goto(e.base + "#lista")
        esperar_vista(pagina, "lista")
        esperar_nivel(pagina, nivel_esperado, f"[{nombre}] nivel superior")
        check_de(fila_de(pagina, "Uno")).tap()  # Fuerza un guardado.
        guardado = leer_estado(pagina)
        igual((guardado["version"], [g["nombre"] for g in guardado["grupos"]]), (2, nombres_grupos), f"[{nombre}] grupos tras el guardado")
        for c in guardado["cosas"]:
            comprobar(c["grupo"] is None and (c["hechaEn"] is None) == (not c["hecha"]), f"[{nombre}] cosa con grupo o hechaEn inválidos: {c}")
        contexto.close()


@prueba("barra «Crear grupo de cosas»: reposo y edición, Intro y «Crear», espacios, Escape/atrás/salir cancelan, el grupo aparece arriba")
def t_barra_crear_grupo(e):
    p = e.pagina(ruta="#lista")
    esperar_vista(p, "lista")
    boton, pildora = boton_crear_grupo(p), p.locator("#formulario-grupo .pildora")
    comprobar(boton.is_visible() and p.get_by_text("No tienes cosas apuntadas.", exact=True).is_visible(), "Sin cosas ni grupos faltan el botón o el estado vacío")
    igual(barra_grupo(p), BARRA_GRUPO_REPOSO, "Barra en reposo")
    caja, caja_boton, barra_inicio = pildora.bounding_box(), boton.bounding_box(), None
    comprobar(abs(caja["width"] - caja_boton["width"]) <= 1 and abs(caja["height"] - caja_boton["height"]) <= 1, f"En reposo el botón no es la píldora entera: {caja} vs {caja_boton}")
    comprobar(0 <= e.alto - (caja["y"] + caja["height"]) <= 48 and caja["height"] >= 44, f"La barra no está pegada abajo: {caja}")
    comprobar(abs(caja["x"] + caja["width"] / 2 - e.ancho / 2) <= 1, "La barra no está centrada")
    igual(boton.evaluate("(b) => [b.getAttribute('aria-label'), b.textContent.trim(), b.type]"), ["Crear grupo de cosas", "Crear grupo de cosas", "button"], "Botón de crear grupo")
    # Mismo aspecto que la barra de escribir de inicio.
    p.go_back()
    esperar_vista(p, "inicio")
    barra_inicio = p.locator("#formulario .pildora").evaluate("(el) => { const cs = getComputedStyle(el), r = el.getBoundingClientRect(); return [cs.borderTopLeftRadius, cs.backgroundColor, cs.boxShadow, r.height, r.width, r.x]; }")
    abrir_lista(p)
    barra_lista = pildora.evaluate("(el) => { const cs = getComputedStyle(el), r = el.getBoundingClientRect(); return [cs.borderTopLeftRadius, cs.backgroundColor, cs.boxShadow, r.height, r.width, r.x]; }")
    igual(barra_lista, barra_inicio, "La barra de crear grupos no tiene el mismo aspecto y tamaño que la barra de escribir")
    # Edición: campo con el foco y «Crear» solo con texto.
    boton.tap()
    esperar_barra_grupo(p, {"editando": True, "boton": False, "campo": True, "crear": False, "valor": ""}, "Barra al empezar a editar")
    igual(p.evaluate("() => document.activeElement.id"), "campo-grupo", "El campo no recibe el foco")
    atributos = campo_grupo(p).evaluate("(c) => ({ placeholder: c.placeholder, maxlength: c.getAttribute('maxlength'), fuente: parseFloat(getComputedStyle(c).fontSize), autocomplete: c.getAttribute('autocomplete'), autocapitalize: c.getAttribute('autocapitalize'), enterkeyhint: c.getAttribute('enterkeyhint'), tipo: c.type })")
    igual(atributos, {"placeholder": "Nombre del grupo", "maxlength": "60", "fuente": atributos["fuente"], "autocomplete": "off", "autocapitalize": "sentences", "enterkeyhint": "done", "tipo": "text"}, "Atributos del campo del grupo")
    comprobar(atributos["fuente"] >= 16, f"El campo del grupo baja de 16px: {atributos['fuente']}")
    campo_grupo(p).fill("   ")
    esperar_barra_grupo(p, {"editando": True, "boton": False, "campo": True, "crear": False, "valor": "   "}, "Solo espacios no habilita «Crear»")
    campo_grupo(p).press("Enter")
    p.evaluate("() => document.querySelector('#formulario-grupo').requestSubmit()")
    p.wait_for_timeout(250)
    comprobar(leer_estado(p) is None or leer_estado(p).get("grupos", []) == [], "Se creó un grupo con solo espacios")
    comprobar(not toast(p)["visible"], "Sale un aviso al crear con solo espacios")
    campo_grupo(p).fill("  Casa   y  jardín ")
    esperar_barra_grupo(p, {"editando": True, "boton": False, "campo": True, "crear": True, "valor": "  Casa   y  jardín "}, "Con texto aparece «Crear»")
    crear = p.locator("#confirmar-grupo")
    igual((crear.text_content().strip(), crear.get_attribute("type")), ("Crear", "submit"), "Botón «Crear»")
    p.wait_for_timeout(250)  # Fin de la transición de entrada del botón.
    caja_crear = crear.bounding_box()
    comprobar(caja_crear["height"] >= 44 and caja["x"] + caja["width"] - (caja_crear["x"] + caja_crear["width"]) <= 12, f"«Crear» no es cómodo o no está al final de la píldora: {caja_crear}")
    campo_grupo(p).press("Enter")
    aviso = esperar_toast(p, "Grupo creado")
    comprobar(aviso["icono"], "«Grupo creado» no lleva icono")
    esperar_barra_grupo(p, BARRA_GRUPO_REPOSO, "Barra tras crear")
    comprobar(p.evaluate("() => document.activeElement.id !== 'campo-grupo'"), "El campo conserva el foco tras crear (el teclado no se cerraría)")
    grupos = leer_estado(p)["grupos"]
    igual([g["nombre"] for g in grupos], ["Casa y jardín"], "El nombre no se recorta ni se le reducen los espacios")
    igual(sorted(grupos[0].keys()), ["abierto", "color", "creada", "id", "nombre"], "Forma de un grupo guardado")
    igual((grupos[0]["color"], grupos[0]["abierto"]), (None, False), "Color y estado iniciales de un grupo")
    igual(textos_nivel(p), ["Casa y jardín"], "El grupo aparece en la lista")
    comprobar(not p.get_by_text("No tienes cosas apuntadas.", exact=True).is_visible(), "El estado vacío sigue con un grupo")
    # El aviso queda por encima de la barra.
    comprobar(aviso["y"] + aviso["alto"] <= caja["y"], f"«Grupo creado» tapa la barra: {aviso} sobre {caja}")
    # Doble toque tras crear: el botón recién aparecido no vuelve a abrir la edición.
    esperar_sin_toast(p)
    crear_grupo(p, "Trabajo", con="boton")
    esperar_toast(p, "Grupo creado")
    p.wait_for_timeout(100)
    boton.tap()
    p.wait_for_timeout(100)
    igual(barra_grupo(p)["editando"], False, "El toque inmediato tras crear vuelve a abrir la edición")
    esperar_nivel(p, ["Trabajo", "Casa y jardín"], "el grupo nuevo va arriba")
    # Escape cancela y devuelve el foco al botón; atrás del sistema cancela sin dejar entradas.
    p.wait_for_timeout(400)
    historial = p.evaluate("() => history.length")
    boton.tap()
    campo_grupo(p).fill("A medias")
    campo_grupo(p).press("Escape")
    esperar_barra_grupo(p, BARRA_GRUPO_REPOSO, "Barra tras Escape")
    igual(p.evaluate("() => document.activeElement.id"), "crear-grupo", "Tras Escape el foco no vuelve al botón")
    igual(p.evaluate("() => history.length"), historial, "Editar el nombre añade entradas al historial")
    boton.tap()
    campo_grupo(p).fill("Otro")
    p.go_back()
    esperar_vista(p, "inicio")
    abrir_lista(p)
    esperar_barra_grupo(p, BARRA_GRUPO_REPOSO, "Al volver a la lista la barra sigue en edición")
    igual(len(leer_estado(p)["grupos"]), 2, "Salir de la vista creó un grupo")
    # Salir del campo vacío cancela; con texto, no (el toque en «Crear» no debe perderlo).
    p.wait_for_timeout(400)
    boton.tap()
    campo_grupo(p).evaluate("(c) => c.blur()")
    esperar_barra_grupo(p, BARRA_GRUPO_REPOSO, "Barra al salir del campo vacío")
    boton.tap()
    campo_grupo(p).fill("Con texto")
    campo_grupo(p).evaluate("(c) => c.blur()")
    p.wait_for_timeout(150)
    igual(barra_grupo(p)["editando"], True, "Salir del campo con texto cancela la edición")
    # Un nombre de 80 caracteres se queda en 60.
    campo_grupo(p).evaluate("(c) => { c.value = 'N'.repeat(80); c.dispatchEvent(new Event('input', { bubbles: true })); }")
    campo_grupo(p).press("Enter")
    esperar_toast(p, "Grupo creado")
    igual(len(leer_estado(p)["grupos"][0]["nombre"]), 60, "Se guarda un nombre de más de 60 caracteres")
    # Con Intro mantenido o dobles envíos no se crean dos.
    esperar_sin_toast(p)
    p.wait_for_timeout(400)
    boton.tap()
    campo_grupo(p).fill("Único")
    p.evaluate("() => { const f = document.querySelector('#formulario-grupo'); f.requestSubmit(); f.requestSubmit(); }")
    p.wait_for_timeout(200)
    igual([g["nombre"] for g in leer_estado(p)["grupos"]].count("Único"), 1, "Un doble envío crea dos grupos")
    comprobar_sin_desborde(p, "la lista con la barra de crear grupos")


@prueba("barra de crear grupos: sube con el teclado de iOS (visualViewport) y los avisos quedan por encima")
def t_barra_grupo_teclado(e):
    p = sembrar(e, estado_con([cosa(2, "B"), cosa(1, "A")]), ruta="#lista")
    esperar_filas(p, 2)
    pildora = p.locator("#formulario-grupo .pildora")
    reposo = pildora.bounding_box()
    boton_crear_grupo(p).tap()
    teclado = 300
    p.evaluate("""(teclado) => { const vv = window.visualViewport; const alto = window.innerHeight;
        Object.defineProperty(vv, 'height', { configurable: true, get: () => alto - teclado });
        vv.dispatchEvent(new Event('resize')); }""", teclado)
    p.wait_for_timeout(400)
    subida = pildora.bounding_box()
    hueco = (e.alto - teclado) - (subida["y"] + subida["height"])
    comprobar(0 <= hueco <= 24, f"Con teclado de {teclado}px la barra de grupos no queda justo encima: hueco={hueco}px ({subida})")
    comprobar(p.evaluate("() => document.documentElement.hasAttribute('data-teclado')"), "No se detecta el teclado abierto en la lista")
    p.keyboard.type("Con teclado")
    p.keyboard.press("Enter")
    aviso = esperar_toast(p, "Grupo creado")
    comprobar(aviso["y"] + aviso["alto"] <= subida["y"], f"El aviso no queda por encima de la barra subida: {aviso} / {subida}")
    comprobar(aviso["y"] + aviso["alto"] <= e.alto - teclado, f"El aviso queda bajo el teclado: {aviso}")
    p.evaluate("() => { delete window.visualViewport.height; window.visualViewport.dispatchEvent(new Event('resize')); }")
    p.wait_for_timeout(450)
    igual(pildora.bounding_box(), reposo, "La barra de grupos no vuelve a su sitio al cerrar el teclado")
    # Sin teclado, «Eliminado»/«Deshacer» también va por encima de la barra, y la última fila se puede ver entera sobre ella.
    esperar_sin_toast(p)
    borrar_de(fila_de(p, "B")).tap()
    aviso = esperar_toast(p, "Eliminado")
    comprobar(aviso["y"] + aviso["alto"] <= reposo["y"], f"«Eliminado» tapa la barra: {aviso} / {reposo}")
    p2 = sembrar(e, estado_con([cosa(i, f"Cosa {i}") for i in range(1, 31)]), ruta="#lista")
    esperar_filas(p2, 30)
    al_final = "() => { const z = document.querySelector('#zona-lista'); z.scrollTop = z.scrollHeight; }"
    p2.evaluate(al_final)
    p2.wait_for_timeout(100)
    ultima, barra_p2 = filas(p2).last.bounding_box(), p2.locator("#formulario-grupo .pildora").bounding_box()
    comprobar(ultima["y"] + ultima["height"] <= barra_p2["y"], f"Al final de la lista la última fila queda bajo la barra: {ultima} / {barra_p2}")
    # Con el teclado abierto (la barra subida) también se llega al final de la lista: la última fila queda sobre la barra y sobre el teclado.
    boton_crear_grupo(p2).tap()
    p2.evaluate("""(teclado) => { const vv = window.visualViewport; const alto = window.innerHeight;
        Object.defineProperty(vv, 'height', { configurable: true, get: () => alto - teclado });
        vv.dispatchEvent(new Event('resize')); }""", teclado)
    p2.wait_for_timeout(400)
    p2.evaluate(al_final)
    p2.wait_for_timeout(100)
    ultima, subida_p2 = filas(p2).last.bounding_box(), p2.locator("#formulario-grupo .pildora").bounding_box()
    comprobar(subida_p2["y"] < barra_p2["y"] - 100, f"La barra de grupos no ha subido con el teclado: {subida_p2} / {barra_p2}")
    comprobar(ultima["y"] + ultima["height"] <= subida_p2["y"] and ultima["y"] + ultima["height"] <= e.alto - teclado,
              f"Con el teclado abierto la última fila queda bajo la barra subida o bajo el teclado: {ultima} / {subida_p2}")
    p2.keyboard.press("Escape")
    p2.evaluate("() => { delete window.visualViewport.height; window.visualViewport.dispatchEvent(new Event('resize')); }")
    p2.wait_for_timeout(450)
    p2.evaluate(al_final)
    p2.wait_for_timeout(100)
    ultima = filas(p2).last.bounding_box()
    igual(p2.locator("#formulario-grupo .pildora").bounding_box(), barra_p2, "Al cerrar el teclado la barra no vuelve a su sitio")
    comprobar(ultima["y"] + ultima["height"] <= barra_p2["y"], f"Al cerrar el teclado la última fila queda bajo la barra: {ultima} / {barra_p2}")


JS_ASPECTO_GRUPO = "() => {" + JS_UTIL + r"""
  const grupo = document.querySelector('#lista-cosas > li.grupo-fila .fila-grupo'), normal = document.querySelector('#lista-cosas > li.fila');
  const ojo = grupo.querySelector('.ojo'), r = ojo.getBoundingClientRect();
  return {
    fondoGrupo: fondoEfectivo(grupo), fondoNormal: fondoEfectivo(normal), fondoPagina: fondoEfectivo(document.body),
    contrasteSuperficies: contraste(fondoEfectivo(grupo), fondoEfectivo(normal)),
    textoGrupo: contrasteTexto(grupo.querySelector('.enlace-grupo')), textoNormal: contrasteTexto(normal.querySelector('.texto')),
    iconoOjo: contrasteIcono(ojo.querySelector('svg:not([hidden])')), iconoPapelera: contrasteIcono(grupo.querySelector('.borrar svg')),
    ojo: { ancho: r.width, alto: r.height, radio: getComputedStyle(ojo).borderTopLeftRadius, fondo: redondear(parsear(getComputedStyle(ojo).backgroundColor)) },
    enlace: { etiqueta: grupo.querySelector('.enlace-grupo').tagName, decoracion: getComputedStyle(grupo.querySelector('.enlace-grupo')).textDecorationLine,
              alto: grupo.querySelector('.enlace-grupo').getBoundingClientRect().height, color: getComputedStyle(grupo.querySelector('.enlace-grupo')).color,
              colorTexto: getComputedStyle(normal.querySelector('.texto')).color },
  };
}"""


@prueba("fila de grupo: superficie más oscura (o distinguible en negro), texto ≥ 4,5 y ojo gris o del color del grupo en Azul, Blanco, Amarillo y Negro")
def t_fila_grupo_aspecto(e):
    for nombre, color in (("Azul", "#2F6FED"), ("Blanco", "#FFFFFF"), ("Amarillo", "#F2B705"), ("Negro", "#111111")):
        p = sembrar(e, estado_con([cosa(2, "Suelta"), cosa(1, "Dentro", grupo="g-3")], grupos=[grupo(3, "Grupo")], color=color), ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
        esperar_filas(p, 2)
        m = p.evaluate(JS_ASPECTO_GRUPO)
        detalle(f"{nombre}: superficies {m['contrasteSuperficies']:.2f}, texto grupo {m['textoGrupo']:.2f}, ojo {m['iconoOjo']:.2f}")
        comprobar(m["contrasteSuperficies"] >= 1.15, f"{nombre}: la fila del grupo no se distingue de una fila normal ({m['contrasteSuperficies']:.2f}:1): {m['fondoGrupo']} vs {m['fondoNormal']}")
        if nombre != "Negro":
            comprobar(sum(m["fondoGrupo"][:3]) < sum(m["fondoNormal"][:3]), f"{nombre}: la fila del grupo no es más oscura que una normal: {m['fondoGrupo']} vs {m['fondoNormal']}")
        else:
            comprobar(sum(m["fondoGrupo"][:3]) > sum(m["fondoNormal"][:3]), f"Negro: la fila del grupo debe aclararse para distinguirse: {m['fondoGrupo']} vs {m['fondoNormal']}")
        comprobar(m["textoGrupo"] >= 4.5 and m["textoNormal"] >= 4.5, f"{nombre}: contraste del texto: grupo {m['textoGrupo']:.2f}, normal {m['textoNormal']:.2f}")
        comprobar(m["iconoOjo"] >= 3 and m["iconoPapelera"] >= 3, f"{nombre}: iconos del ojo/papelera: {m['iconoOjo']:.2f} / {m['iconoPapelera']:.2f}")
        igual((round(m["ojo"]["ancho"]), round(m["ojo"]["alto"]), m["ojo"]["radio"]), (44, 44, "50%"), f"{nombre}: el ojo no es redondo de 44px")
        comprobar(min(m["ojo"]["fondo"]) >= 200 and max(m["ojo"]["fondo"]) - min(m["ojo"]["fondo"]) <= 12, f"{nombre}: el ojo sin color no es gris neutro: {m['ojo']['fondo']}")
        igual((m["enlace"]["etiqueta"], m["enlace"]["decoracion"], m["enlace"]["color"]), ("A", "none", m["enlace"]["colorTexto"]), f"{nombre}: el nombre no es un enlace con aspecto de texto")
        comprobar(m["enlace"]["alto"] >= 44, f"{nombre}: el enlace del nombre mide menos de 44px de alto: {m['enlace']['alto']}")
        # Con color: el disco del ojo se rellena con el color y su icono contrasta.
        p.evaluate("(clave) => { const s = JSON.parse(localStorage.getItem(clave)); s.grupos[0].color = '#F2B705'; localStorage.setItem(clave, s && JSON.stringify(s)); }", CLAVE)
        p.reload()
        esperar_filas(p, 2)
        m2 = p.evaluate(JS_ASPECTO_GRUPO)
        igual(tuple(m2["ojo"]["fondo"]), hex_a_rgb("#F2B705"), f"{nombre}: el disco del ojo no toma el color del grupo")
        comprobar(m2["iconoOjo"] >= 3, f"{nombre}: el icono del ojo sobre el color del grupo contrasta {m2['iconoOjo']:.2f}:1")
        caja = ojo_de(fila_grupo(p, "Grupo")).bounding_box()
        igual(muestrear(p, [(caja["x"] + 5, caja["y"] + caja["height"] / 2)])[0], hex_a_rgb("#F2B705"), f"{nombre}: píxel real del disco del ojo")
        p.context.close()


@prueba("ojo: abre y cierra las cosas del grupo (aria-expanded, icono, filas anidadas, «Este grupo está vacío.») y el estado se guarda")
def t_ojo(e):
    p = sembrar_grupos(e)
    esperar_filas(p, 6)
    casa, compras = fila_grupo(p, "Casa"), fila_grupo(p, "Compras")
    ojo = ojo_de(casa)
    igual((ojo.get_attribute("aria-expanded"), ojo.get_attribute("aria-label")), ("false", "Ver cosas del grupo"), "Ojo cerrado")
    comprobar(ojo.get_attribute("aria-controls") and p.locator("#" + ojo.get_attribute("aria-controls")).count() == 1, "aria-controls del ojo no apunta al pliegue")
    comprobar(not casa.locator(".anidada > li").first.is_visible(), "Las cosas de un grupo cerrado se ven")
    igual(textos_anidados(p, "Compras"), ["En Compras"], "El grupo guardado como abierto enseña sus cosas")
    comprobar(compras.locator(".anidada > li").first.is_visible(), "Las cosas del grupo abierto no se ven")
    igual(ojo_de(compras).get_attribute("aria-expanded"), "true", "El ojo del grupo abierto")
    icono_cerrado = ojo.evaluate("(b) => [...b.querySelectorAll('svg')].find((s) => s.checkVisibility()).outerHTML")
    ojo.tap()
    esperar(p, "(b) => b.getAttribute('aria-expanded') === 'true'", arg=ojo.element_handle(), que="aria-expanded pasa a true")
    igual(ojo.get_attribute("aria-label"), "Ocultar cosas del grupo", "Etiqueta del ojo abierto")
    icono_abierto = ojo.evaluate("(b) => [...b.querySelectorAll('svg')].find((s) => s.checkVisibility()).outerHTML")
    comprobar(icono_abierto != icono_cerrado and "ojo-cerrado" in icono_abierto, "El icono no cambia a «ojo tachado» al abrir")
    esperar(p, "(nombre) => { const f = [...document.querySelectorAll('#lista-cosas > li.grupo-fila')].find((l) => l.querySelector('.enlace-grupo').textContent === nombre); return [...f.querySelectorAll('.anidada > li')].every((li) => li.checkVisibility()); }", arg="Casa", que="las cosas anidadas se ven")
    igual(textos_anidados(p, "Casa"), ["En Casa 3", "En Casa 1", "En Casa 2"], "Cosas anidadas en orden")
    p.wait_for_timeout(300)
    sangria = p.evaluate("""() => { const f = [...document.querySelectorAll('#lista-cosas > li.grupo-fila')].find((l) => l.querySelector('.enlace-grupo').textContent === 'Casa');
        const cabeza = f.querySelector('.fila-grupo').getBoundingClientRect(), anidada = f.querySelector('.anidada > li').getBoundingClientRect();
        return { sangria: anidada.left - cabeza.left, debajo: anidada.top >= cabeza.bottom, ancho: anidada.width < cabeza.width }; }""")
    comprobar(12 <= sangria["sangria"] <= 24 and sangria["debajo"] and sangria["ancho"], f"Las filas anidadas no van sangradas bajo la fila del grupo: {sangria}")
    for fila in casa.locator(".anidada > li").all():
        igual((check_de(fila).get_attribute("aria-label") is not None, borrar_de(fila).count()), (True, 1), "Una fila anidada no lleva check y papelera")
    igual(leer_estado(p)["grupos"][0]["abierto"], True, "«abierto» no se guarda")
    p.reload()
    esperar_filas(p, 6)
    igual(ojo_de(fila_grupo(p, "Casa")).get_attribute("aria-expanded"), "true", "El ojo no recuerda que estaba abierto tras recargar")
    igual(textos_anidados(p, "Casa"), ["En Casa 3", "En Casa 1", "En Casa 2"], "Cosas anidadas tras recargar")
    ojo = ojo_de(fila_grupo(p, "Casa"))
    ojo.tap()
    esperar(p, "(b) => b.getAttribute('aria-expanded') === 'false'", arg=ojo.element_handle(), que="aria-expanded vuelve a false")
    esperar(p, "() => { const f = [...document.querySelectorAll('#lista-cosas > li.grupo-fila')].find((l) => l.querySelector('.enlace-grupo').textContent === 'Casa'); return !f.querySelector('.pliegue').checkVisibility(); }", que="el pliegue se oculta al cerrar")
    igual(leer_estado(p)["grupos"][0]["abierto"], False, "«abierto» no vuelve a false")
    # Doble toque: se queda abierto.
    p.wait_for_timeout(400)
    for pausa in (0, 150, 250):
        doble_toque(p, ojo, pausa)
        p.wait_for_timeout(500)
        igual(ojo.get_attribute("aria-expanded"), "true", f"Un doble toque ({pausa} ms) en el ojo abre y cierra")
        ojo.tap()
        p.wait_for_timeout(400)
    # Dos ojos distintos tocados seguidos (100 ms): el segundo no es el «segundo toque» del primero y también cambia.
    igual(p.evaluate("() => [...document.querySelectorAll('#lista-cosas .ojo')].map((o) => o.getAttribute('aria-expanded'))"), ["false", "true"], "Estado de los ojos antes de tocarlos seguidos (Casa cerrado, Compras abierto)")
    p.evaluate("() => new Promise((listo) => { const ojos = document.querySelectorAll('#lista-cosas .ojo'); ojos[0].click(); setTimeout(() => { ojos[1].click(); listo(); }, 100); })")
    igual(p.evaluate("() => [...document.querySelectorAll('#lista-cosas .ojo')].map((o) => o.getAttribute('aria-expanded'))"), ["true", "false"], "El ojo de otro grupo tocado 100 ms después del primero se ignora")
    igual([g["abierto"] for g in leer_estado(p)["grupos"]], [True, False], "«abierto» de los dos grupos tras tocar sus ojos seguidos")
    # Un grupo vacío abierto lo dice.
    p2 = sembrar(e, estado_con([], grupos=[grupo(1, "Vacío", abierto=True)]), ruta="#lista")
    esperar_filas(p2, 1)
    nota = p2.locator("#lista-cosas").get_by_text("Este grupo está vacío.", exact=True)
    comprobar(nota.is_visible(), "Un grupo vacío abierto no dice que está vacío")
    comprobar(not p2.get_by_text("No tienes cosas apuntadas.", exact=True).is_visible(), "Con un grupo, el estado vacío general se ve")
    igual(nota.evaluate("(el) => parseFloat(getComputedStyle(el).fontSize)") <= 16, True, "El texto de grupo vacío no es discreto")


@prueba("anidadas: borrar y «Deshacer» conservan el grupo y la posición; borrar un grupo devuelve sus cosas al nivel superior y «Deshacer» lo repone entero")
def t_anidadas_borrar_deshacer(e):
    p = sembrar_grupos(e)
    esperar_filas(p, 6)
    ojo_de(fila_grupo(p, "Casa")).tap()
    esperar_anidadas(p, "Casa", ["En Casa 3", "En Casa 1", "En Casa 2"])
    p.wait_for_timeout(300)
    borrar_de(fila_anidada(p, "Casa", "En Casa 1")).tap()
    esperar_toast(p, "Eliminado")
    esperar_anidadas(p, "Casa", ["En Casa 3", "En Casa 2"])
    comprobar("En Casa 1" not in [c["texto"] for c in leer_estado(p)["cosas"]], "La cosa anidada sigue guardada tras borrarla")
    igual(textos_nivel(p), NIVEL_SEMBRADO, "Borrar una anidada toca el nivel superior")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_anidadas(p, "Casa", ["En Casa 3", "En Casa 1", "En Casa 2"])
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "En Casa 1"), "g-1", "Al deshacer, la cosa no vuelve a su grupo")
    esperar_sin_toast(p)
    p.wait_for_timeout(400)  # Pasa la guarda del doble toque entre borrados.
    # Borrar el grupo: sus cosas pasan al nivel superior en su orden y nada se pierde.
    borrar_grupo_de(fila_grupo(p, "Casa")).tap()
    aviso = esperar_toast(p, "Eliminado")
    igual(aviso["boton"], "Deshacer", "Borrar un grupo no ofrece «Deshacer»")
    esperar_nivel(p, ["Compras", "Suelta nueva", "En Casa 3", "En Casa 1", "Suelta vieja", "Suelta hecha antes", "En Casa 2", "Suelta hecha"], "nivel superior tras borrar el grupo")
    guardado = leer_estado(p)
    igual([g["nombre"] for g in guardado["grupos"]], ["Compras"], "Grupos tras borrar Casa")
    igual(sorted(c["texto"] for c in guardado["cosas"]), sorted(["Suelta nueva", "En Casa 3", "Suelta hecha", "En Casa 2", "En Compras", "En Casa 1", "Suelta vieja", "Suelta hecha antes"]), "Borrar un grupo pierde cosas")
    igual([c["grupo"] for c in guardado["cosas"] if c["texto"].startswith("En Casa")], [None, None, None], "Las cosas del grupo borrado no vuelven al nivel superior")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_nivel(p, NIVEL_SEMBRADO, "nivel superior tras deshacer el borrado del grupo")
    esperar_anidadas(p, "Casa", ["En Casa 3", "En Casa 1", "En Casa 2"])
    guardado = leer_estado(p)
    igual([g["nombre"] for g in guardado["grupos"]], ["Casa", "Compras"], "Grupos tras deshacer")
    igual(guardado["grupos"][0]["abierto"], True, "El grupo repuesto no conserva su estado abierto")
    igual(sorted(c["texto"] for c in guardado["cosas"] if c["grupo"] == "g-1"), ["En Casa 1", "En Casa 2", "En Casa 3"], "La pertenencia al grupo no se restaura")
    igual(p.evaluate("() => document.activeElement.className"), "ojo", "El foco no va al grupo repuesto")
    esperar_sin_toast(p)
    p.wait_for_timeout(400)
    # Borrar una cosa anidada y después su grupo: un solo «Deshacer» lo devuelve todo.
    borrar_de(fila_anidada(p, "Casa", "En Casa 3")).tap()
    esperar_anidadas(p, "Casa", ["En Casa 1", "En Casa 2"])
    p.wait_for_timeout(400)  # Pasa la guarda del doble toque entre borrados.
    borrar_grupo_de(fila_grupo(p, "Casa")).tap()
    esperar_nivel(p, ["Compras", "Suelta nueva", "En Casa 1", "Suelta vieja", "Suelta hecha antes", "En Casa 2", "Suelta hecha"], "nivel superior tras borrar cosa y grupo")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_nivel(p, NIVEL_SEMBRADO, "nivel tras deshacer cosa y grupo")
    esperar_anidadas(p, "Casa", ["En Casa 3", "En Casa 1", "En Casa 2"])
    # Al caducar el aviso, borrar el grupo es definitivo y sus cosas siguen sueltas tras recargar.
    esperar_sin_toast(p)
    p.wait_for_timeout(400)
    borrar_grupo_de(fila_grupo(p, "Compras")).tap()
    esperar_nivel(p, ["Casa", "Suelta nueva", "En Compras", "Suelta vieja", "Suelta hecha antes", "Suelta hecha"], "nivel tras borrar Compras")
    p.reload()
    esperar_filas(p, 6)
    igual(textos_nivel(p), ["Casa", "Suelta nueva", "En Compras", "Suelta vieja", "Suelta hecha antes", "Suelta hecha"], "Nivel tras recargar con Compras borrado")
    # Borrar el último grupo y la última cosa deja el estado vacío; deshacer lo quita.
    p2 = sembrar(e, estado_con([cosa(1, "Sola", grupo="g-2")], grupos=[grupo(2, "Único", abierto=True)]), ruta="#lista")
    esperar_filas(p2, 1)
    borrar_grupo_de(fila_grupo(p2, "Único")).tap()
    esperar_nivel(p2, ["Sola"], "la cosa del grupo borrado queda suelta")
    p2.wait_for_timeout(400)
    borrar_de(fila_de(p2, "Sola")).tap()
    esperar(p2, "() => document.querySelector('#vacio').checkVisibility()", que="aparece el estado vacío")
    p2.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_nivel(p2, ["Único"], "todo repuesto")
    esperar_anidadas(p2, "Único", ["Sola"])
    comprobar(not p2.get_by_text("No tienes cosas apuntadas.", exact=True).is_visible(), "El estado vacío sigue tras deshacer")


@prueba("página de grupo: el nombre abre #grupo/ID (foco al título y de vuelta al enlace), carga directa, id desconocido y atrás sin apilar historial")
def t_enlace_grupo(e):
    p = sembrar_grupos(e)
    esperar_filas(p, 6)
    enlace = p.get_by_role("link", name="Casa", exact=True)
    igual(enlace.get_attribute("href"), "#grupo/g-1", "href del enlace del grupo")
    antes = p.evaluate("() => history.length")
    enlace.tap()
    esperar_vista(p, "grupo")
    comprobar(p.url.endswith("#grupo/g-1"), f"La URL no lleva #grupo/ID: {p.url}")
    igual(p.evaluate("() => document.activeElement.id"), "titulo-grupo", "Al abrir un grupo el foco no va al título")
    igual(pagina_grupo(p)["titulo"], "Casa", "Título de la página del grupo")
    igual(p.evaluate("() => history.length") - antes, 1, "Entradas de historial al abrir un grupo")
    ocultas = p.evaluate("""() => ['inicio', 'lista', 'ajustes'].map((v) => { const el = document.querySelector('#vista-' + v); return el.hidden && el.hasAttribute('inert'); })""")
    igual(ocultas, [True, True, True], "Las otras vistas no quedan ocultas e inertes en la página del grupo")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    comprobar(p.url.endswith("#lista"), f"Volver del grupo no lleva a #lista: {p.url}")
    igual(p.evaluate("() => document.activeElement.textContent"), "Casa", "Al volver, el foco no vuelve al enlace del grupo")
    igual(p.evaluate("() => history.length") - antes, 1, "Volver apila historial")
    # Atrás del sistema desde el grupo, y con teclado (Intro sobre el enlace).
    p.wait_for_timeout(400)
    p.keyboard.press("Enter")
    esperar_vista(p, "grupo")
    p.go_back()
    esperar_vista(p, "lista")
    p.go_back()
    esperar_vista(p, "inicio")
    p.go_back()  # Debajo de inicio solo queda la página con la que se sembró el almacenamiento.
    igual(p.url, e.base + "icons/favicon-32.png", "Quedan entradas apiladas tras abrir grupos")
    # Carga directa: atrás enseña la lista y después inicio.
    p2 = sembrar_grupos(e, ruta="#grupo/g-1")
    esperar_vista(p2, "grupo")
    igual((pagina_grupo(p2)["titulo"], p2.evaluate("() => document.activeElement.id")), ("Casa", "titulo-grupo"), "Carga directa de #grupo/ID")
    p2.go_back()
    esperar_vista(p2, "lista")
    comprobar(p2.url.endswith("#lista"), f"Atrás desde la carga directa: {p2.url}")
    p2.go_back()
    esperar_vista(p2, "inicio")
    comprobar(p2.url.startswith(e.base), f"Atrás sacó de la app: {p2.url}")
    # Recargar en la página del grupo y volver con el botón.
    p2.go_forward()
    esperar_vista(p2, "lista")
    p2.go_forward()
    esperar_vista(p2, "grupo")
    p2.reload()
    esperar_vista(p2, "grupo")
    p2.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p2, "lista")
    # Id desconocido: se cae en la lista en silencio.
    for ruta in ("#grupo/no-existe", "#grupo/", "#grupo"):
        p3 = sembrar_grupos(e, ruta=ruta)
        esperar_vista(p3, "lista" if ruta == "#grupo/no-existe" else "inicio")
        if ruta == "#grupo/no-existe":
            comprobar(p3.url.endswith("#lista"), f"Un id desconocido no corrige la URL: {p3.url}")
            p3.go_back()
            esperar_vista(p3, "inicio")
        p3.context.close()
    # Un grupo borrado desde otra pestaña mientras se mira: se vuelve a la lista.
    contexto = e.contexto()
    a = sembrar_grupos(e, ruta="#grupo/g-2", contexto=contexto)
    esperar_vista(a, "grupo")
    b = e.pagina(ruta="#lista", contexto=contexto)
    esperar_filas(b, 6)
    borrar_grupo_de(fila_grupo(b, "Compras")).tap()
    esperar_vista(a, "lista")
    comprobar(a.url.endswith("#lista"), f"Tras borrarse el grupo en otra pestaña la URL no vuelve a la lista: {a.url}")


@prueba("historial: la entrada de un grupo borrado (al avanzar hacia ella, al borrarlo otra pestaña o al recargar) se abandona sin dejar dos entradas de la lista: un solo «atrás» llega a inicio")
def t_grupo_borrado_historial(e):
    # (a) Avanzar hacia la entrada de un grupo que se borró después de salir de él.
    p = sembrar_grupos(e)
    esperar_filas(p, 6)
    abrir_grupo(p, "Casa")
    p.go_back()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    borrar_grupo_de(fila_grupo(p, "Casa")).tap()
    esperar_toast(p, "Eliminado")
    esperar_sin_toast(p, ms=6000)
    p.go_forward()
    esperar(p, "() => location.hash === '#lista'", que="al avanzar hacia el grupo borrado la URL vuelve a #lista")
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    p.go_back()
    esperar_vista(p, "inicio")
    comprobar(p.url.startswith(e.base) and "#" not in p.url.rstrip("#"), f"[avanzar] un solo «atrás» desde la lista no llega a inicio: {p.url}")
    # (b) Otra pestaña borra el grupo mientras se mira su página.
    contexto = e.contexto()
    a = sembrar_grupos(e, ruta="#grupo/g-2", contexto=contexto)
    esperar_vista(a, "grupo")
    b = e.pagina(ruta="#lista", contexto=contexto)
    esperar_filas(b, 6)
    borrar_grupo_de(fila_grupo(b, "Compras")).tap()
    esperar_vista(a, "lista")
    esperar(a, "() => location.hash === '#lista'", que="tras el borrado desde otra pestaña la URL es #lista")
    a.wait_for_timeout(400)
    a.go_back()
    esperar_vista(a, "inicio")
    comprobar(a.url.startswith(e.base) and "#" not in a.url.rstrip("#"), f"[otra pestaña] un solo «atrás» desde la lista no llega a inicio: {a.url}")
    contexto.close()
    # (b') Recargar con la URL de un grupo que ya no existe.
    p3 = sembrar_grupos(e, ruta="#grupo/g-1")
    esperar_vista(p3, "grupo")
    p3.evaluate("(clave) => { const s = JSON.parse(localStorage.getItem(clave)); s.grupos = s.grupos.filter((g) => g.id !== 'g-1'); localStorage.setItem(clave, JSON.stringify(s)); }", CLAVE)
    p3.reload()
    esperar_vista(p3, "lista")
    esperar(p3, "() => location.hash === '#lista'", que="al recargar con un grupo borrado la URL pasa a #lista")
    igual(textos_nivel(p3), ["Compras", "Suelta nueva", "En Casa 3", "En Casa 1", "Suelta vieja", "Suelta hecha antes", "En Casa 2", "Suelta hecha"], "Las cosas del grupo que ya no existe no vuelven al nivel superior")
    p3.wait_for_timeout(400)
    p3.go_back()
    esperar_vista(p3, "inicio")
    comprobar(p3.url.startswith(e.base) and "#" not in p3.url.rstrip("#"), f"[recarga] un solo «atrás» desde la lista no llega a inicio: {p3.url}")
    # Control: desde la página de un grupo vivo, «atrás» sigue llevando a la lista y otro «atrás» a inicio.
    p3.go_forward()
    esperar_vista(p3, "lista")
    p3.wait_for_timeout(400)
    abrir_grupo(p3, "Compras")
    p3.go_back()
    esperar_vista(p3, "lista")
    p3.go_back()
    esperar_vista(p3, "inicio")


@prueba("volver de la página de un grupo: el foco vuelve a su enlace, que queda a la vista, y la lista corrige su desplazamiento (con «Volver», con atrás del sistema tras desplazarla hasta el final y si la lista se acortó entretanto)")
def t_volver_del_grupo_desplazamiento(e):
    p = sembrar(e, estado_con([cosa(i, f"Cosa {i}") for i in range(1, 60) if i != 30], grupos=[grupo(30, "Arriba")]), ruta="#lista")
    esperar_filas(p, 59)
    igual(textos_nivel(p)[:2], ["Arriba", "Cosa 59"], "El grupo no va el primero aunque sea más viejo que muchas cosas")
    enlace = p.get_by_role("link", name="Arriba", exact=True)
    # «Volver» con la lista sin desplazar: el foco vuelve al enlace, que sigue arriba, a la vista.
    enlace.tap()
    esperar_vista(p, "grupo")
    p.wait_for_timeout(400)
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    m = p.evaluate(JS_ENLACE_A_LA_VISTA)
    igual(m["desplazamiento"], 0, "Al volver del grupo la lista se ha desplazado sola")
    comprobar(m["enfocado"] and m["visible"], f"El foco no vuelve al enlace del grupo o no queda a la vista: {m}")
    p.wait_for_timeout(400)
    # La lista desplazada hasta el final (el enlace, que conserva el foco, queda fuera por arriba): Intro abre el grupo y, al volver
    # con atrás del sistema, la lista se corrige lo justo para que el enlace enfocado se vea.
    for como in ("atras", "acortada"):
        antes = p.evaluate("() => { const z = document.querySelector('#zona-lista'); z.scrollTop = z.scrollHeight; return z.scrollTop; }")
        comprobar(antes > 1000, f"[{como}] La lista no se ha desplazado hasta el final: {antes}")
        p.wait_for_timeout(100)
        p.keyboard.press("Enter")  # El enlace conserva el foco desde la vuelta anterior.
        esperar_vista(p, "grupo")
        if como == "acortada":
            # Otra pestaña borra las 20 cosas más nuevas mientras se mira el grupo.
            otra = e.pagina(ruta="icons/favicon-32.png", contexto=p.context)
            otra.evaluate("(clave) => { const s = JSON.parse(localStorage.getItem(clave)); s.cosas = s.cosas.filter((c) => c.creada < 1700000000040); localStorage.setItem(clave, JSON.stringify(s)); }", CLAVE)
        p.wait_for_timeout(400)
        if como == "atras":
            p.go_back()
        else:
            p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        esperar_filas(p, 59 if como == "atras" else 39)
        m = p.evaluate(JS_ENLACE_A_LA_VISTA)
        comprobar(m["enfocado"] and m["visible"], f"[{como}] El enlace enfocado no queda a la vista al volver: {m}")
        comprobar(m["desplazamiento"] < antes, f"[{como}] El desplazamiento no se corrige para enseñar el enlace: {m}")
        p.wait_for_timeout(400)


@prueba("página de grupo: cabecera (Volver, título, lápiz), «Añadir cosas» en una barra abajo, la nota de grupo vacío centrada, desplegable solo con las cosas sueltas (las de otros grupos no salen), «+» las mete y «No hay más cosas que añadir.»")
def t_pagina_grupo_anadir(e):
    p = sembrar(e, estado_con([cosa(5, "Nueva suelta"), cosa(4, "Hecha suelta", hecha=True), cosa(3, "De otro", grupo="g-6"), cosa(2, "Vieja suelta"), cosa(1, "Hecha antes", hecha=True)],
                              grupos=[grupo(7, "Vacío"), grupo(6, "Otro")]), ruta="#grupo/g-7")
    esperar_vista(p, "grupo")
    volver_b, lapiz = p.get_by_role("button", name="Volver", exact=True), boton_editar_grupo(p)
    caja_v, caja_l, titulo = volver_b.bounding_box(), lapiz.bounding_box(), p.locator("#titulo-grupo").bounding_box()
    comprobar(caja_v["x"] < e.ancho / 2 < caja_l["x"] and caja_v["y"] <= 40 and abs(caja_v["y"] - caja_l["y"]) <= 1, f"Volver y el lápiz no están arriba a cada lado: {caja_v} {caja_l}")
    igual((round(caja_l["width"]), round(caja_l["height"]), lapiz.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), lapiz.locator("svg").count()), (48, 48, "50%", 1), "Botón del lápiz")
    comprobar(caja_v["x"] + caja_v["width"] <= titulo["x"] and titulo["x"] + titulo["width"] <= caja_l["x"], "El título no queda entre los dos botones")
    igual(p.locator("#vista-grupo h1").text_content(), "Vacío", "Título de la página")
    estado = pagina_grupo(p)
    igual((estado["miembros"], estado["desplegable"], estado["expandido"], estado["vacio"], estado["centrado"]), ([], False, "false", True, True), "Página de un grupo vacío")
    boton = boton_anadir(p)
    caja, m = boton.bounding_box(), barra_anadir(p)
    cuerpo = p.locator("#zona-grupo").bounding_box()
    comprobar(caja["height"] >= 44 and 0 <= m["escribir"]["y"] - (caja["y"] + caja["height"]) <= 16 and 0 <= e.alto - m["escribir"]["b"] <= 48,
              f"«Añadir cosas» no va justo encima de la barra de escribir, pegada abajo: {caja} / {m['escribir']}")
    comprobar(abs(caja["width"] - min(e.ancho - 32, 640)) <= 1, f"«Añadir cosas» no es una píldora ancha: {caja}")
    igual(boton.evaluate("(b) => parseFloat(getComputedStyle(b).borderTopLeftRadius) >= b.getBoundingClientRect().height / 2 - 0.5"), True, "«Añadir cosas» no es una píldora")
    vacio = p.get_by_text("Este grupo está vacío.", exact=True).bounding_box()
    arriba, abajo, centro = m["cabecera"]["b"], caja["y"], vacio["y"] + vacio["height"] / 2
    comprobar(arriba + (abajo - arriba) * 0.35 <= centro <= arriba + (abajo - arriba) * 0.65, f"La nota de grupo vacío no está centrada entre la cabecera y la barra: {vacio} en [{arriba}, {abajo}]")
    igual(boton.get_attribute("aria-controls"), "candidatas", "aria-controls de «Añadir cosas»")
    # El desplegable: solo las cosas sueltas (una que ya está en otro grupo no se ofrece), pendientes primero, abierto hacia arriba sobre la barra.
    boton.tap()
    esperar_pagina_grupo(p, "desplegable abierto", desplegable=True, expandido="true", candidatas=["Nueva suelta", "Vieja suelta", "Hecha antes", "Hecha suelta"], centrado=False, sinMas=False)
    igual(p.locator("#lista-candidatas").get_by_text("De otro", exact=True).count(), 0, "Una cosa de otro grupo sale en el desplegable")
    p.wait_for_timeout(300)
    comprobar_desplegable_sobre_la_barra(barra_anadir(p), "con cinco candidatas")
    igual(boton.bounding_box(), caja, "Al abrir el desplegable la barra se mueve")
    igual(p.evaluate("() => document.querySelector('#lista-candidatas').getAttribute('role')"), "list", "El desplegable no es una lista")
    igual(p.locator("#lista-candidatas select").count(), 0, "El desplegable no debe ser un <select>")
    for fila in p.locator("#lista-candidatas > li").all():
        mas = fila.get_by_role("button", name="Añadir al grupo", exact=True)
        caja_mas, caja_fila, texto = mas.bounding_box(), fila.bounding_box(), fila.locator(".texto").bounding_box()
        comprobar(caja_mas["width"] >= 44 and abs(caja_mas["width"] - caja_mas["height"]) < 0.5 and mas.locator("svg").count() == 1, f"«+» no es redondo de 44px con icono: {caja_mas}")
        comprobar(texto["x"] + texto["width"] <= caja_mas["x"] + 0.5 and caja_fila["x"] + caja_fila["width"] - (caja_mas["x"] + caja_mas["width"]) <= 12, "«+» no está a la derecha del texto")
        igual(mas.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), "50%", "border-radius de «+»")
    comprobar("line-through" in p.locator("#lista-candidatas > li").last.locator(".texto").evaluate("(t) => getComputedStyle(t).textDecorationLine"), "Las hechas del desplegable no se ven tachadas")
    mas_de(p, "Vieja suelta").tap()
    esperar_pagina_grupo(p, "tras añadir «Vieja suelta»", miembros=["Vieja suelta"], candidatas=["Nueva suelta", "Hecha antes", "Hecha suelta"], desplegable=True, vacio=False)
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Vieja suelta"), "g-7", "La cosa añadida no cambia de grupo en el almacenamiento")
    igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), "Añadir al grupo", "Tras añadir, el foco no pasa al «+» vecino")
    # Doble toque en «+»: la fila siguiente sube bajo el dedo y no debe añadirse también.
    p.wait_for_timeout(400)
    doble_toque(p, mas_de(p, "Nueva suelta"), 150)
    p.wait_for_timeout(500)
    igual(pagina_grupo(p)["miembros"], ["Nueva suelta", "Vieja suelta"], "Un doble toque en «+» añade dos cosas")
    p.wait_for_timeout(400)
    for texto in ("Hecha antes", "Hecha suelta"):
        mas_de(p, texto).tap()
        p.wait_for_timeout(400)
    esperar_pagina_grupo(p, "sin cosas que añadir", candidatas=[], sinMas=True, miembros=["Nueva suelta", "Vieja suelta", "Hecha antes", "Hecha suelta"])
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "De otro"), "g-6", "La cosa de otro grupo ha cambiado de grupo")
    igual(p.evaluate("() => document.activeElement.id"), "anadir-cosas", "Sin más candidatas el foco no vuelve a «Añadir cosas»")
    comprobar(p.get_by_text("No hay más cosas que añadir.", exact=True).is_visible(), "Falta «No hay más cosas que añadir.»")
    # Se cierra con el botón y con Escape.
    boton.tap()
    esperar_pagina_grupo(p, "desplegable cerrado con el botón", desplegable=False, expandido="false", centrado=False)
    p.wait_for_timeout(400)
    boton.tap()
    esperar_pagina_grupo(p, "desplegable abierto de nuevo", desplegable=True)
    p.keyboard.press("Escape")
    esperar_pagina_grupo(p, "desplegable cerrado con Escape", desplegable=False, expandido="false")
    igual(p.evaluate("() => document.activeElement.id"), "anadir-cosas", "Tras Escape el foco no queda en «Añadir cosas»")
    # Con cosas, estas empiezan arriba del cuerpo, en orden, y la barra sigue abajo; en la lista principal el grupo tiene todo.
    primera = p.locator("#lista-grupo > li").first.bounding_box()
    comprobar(cuerpo["y"] <= primera["y"] < cuerpo["y"] + 40 and boton.bounding_box() == caja, f"Con cosas estas no empiezan arriba o la barra se ha movido: {primera} {boton.bounding_box()}")
    for fila in p.locator("#lista-grupo > li").all():
        quitar = fila.get_by_role("button", name="Quitar del grupo", exact=True)
        check, texto, caja_q = check_de(fila).bounding_box(), fila.locator(".texto").bounding_box(), quitar.bounding_box()
        comprobar(check["x"] + check["width"] <= texto["x"] and texto["x"] + texto["width"] <= caja_q["x"] + 0.5 and caja_q["width"] >= 44, "La fila del grupo no lleva check a la izquierda y «−» a la derecha")
        igual(borrar_de(fila).count(), 0, "En la página del grupo no debe haber papelera")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual(textos_nivel(p), ["Vacío", "Otro"], "Tras meter todo en el grupo no quedan cosas sueltas")
    igual(leer_estado(p)["grupos"][1]["nombre"], "Otro", "El otro grupo se conserva")


@prueba("página de grupo: «−» devuelve la cosa al nivel superior (animación corta, aviso con «Deshacer», que la devuelve al grupo, también varias) y el grupo vacío vuelve a centrar su nota")
def t_quitar_del_grupo(e):
    p = sembrar(e, estado_con([cosa(3, "Tres", grupo="g-4"), cosa(2, "Dos", hecha=True, grupo="g-4"), cosa(1, "Una")], grupos=[grupo(4, "Grupo")]), ruta="#grupo/g-4")
    esperar_vista(p, "grupo")
    igual(pagina_grupo(p)["miembros"], ["Tres", "Dos"], "Cosas del grupo")
    igual(quitar_de(p, "Tres").get_attribute("aria-label"), "Quitar del grupo", "aria-label de «−»")
    inicio = time.monotonic()
    quitar_de(p, "Tres").tap()
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Tres"), None, "La cosa quitada sigue en el grupo en el almacenamiento")
    esperar(p, "() => document.querySelectorAll('#lista-grupo > li').length === 1", ms=1500, que="la fila quitada desaparece")
    comprobar(time.monotonic() - inicio < 1.5, "La animación de quitar no es corta")
    aviso = esperar_toast(p, "Quitado del grupo")
    igual((aviso["boton"], aviso["icono"]), ("Deshacer", False), "Aviso al quitar del grupo")
    igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), "Quitar del grupo", "El foco no pasa al «−» vecino")
    m = barra_anadir(p)
    comprobar(aviso["y"] + aviso["alto"] <= m["pildora"]["y"], f"El aviso tapa la barra: {aviso} / {m['pildora']}")
    # «Deshacer» la devuelve al grupo, en su sitio, y el foco va a su check.
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_pagina_grupo(p, "tras deshacer", miembros=["Tres", "Dos"])
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Tres"), "g-4", "Tras deshacer, la cosa no vuelve al grupo en el almacenamiento")
    igual(p.evaluate("() => [document.activeElement.getAttribute('aria-label'), document.activeElement.closest('li').querySelector('.texto').textContent]"),
          ["Marcar como hecha", "Tres"], "Tras deshacer, el foco no va al check de la cosa devuelta")
    esperar_sin_toast(p, ms=1500)
    # Varias seguidas (con el aviso a la vista) se deshacen juntas.
    quitar_de(p, "Tres").tap()
    p.wait_for_timeout(400)
    quitar_de(p, "Dos").tap()
    esperar_pagina_grupo(p, "tras quitar las dos", miembros=[], vacio=True)
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_pagina_grupo(p, "tras deshacer las dos", miembros=["Tres", "Dos"], vacio=False)
    igual(sorted(c["texto"] for c in leer_estado(p)["cosas"] if c.get("grupo") == "g-4"), ["Dos", "Tres"], "Deshacer varias no las devuelve todas")
    # Pasado el aviso ya no hay nada que deshacer: la cosa se queda fuera.
    p.wait_for_timeout(400)
    quitar_de(p, "Tres").tap()
    esperar_toast(p, "Quitado del grupo")
    esperar_sin_toast(p, ms=6000)
    p.wait_for_timeout(300)  # El aviso ya invisible se vacía un instante después.
    igual(p.get_by_role("button", name="Deshacer", exact=True).count(), 0, "«Deshacer» sigue ahí sin aviso")
    igual(pagina_grupo(p)["miembros"], ["Dos"], "Cosas del grupo cuando el aviso se va")
    # Doble toque: solo se quita una.
    p2 = sembrar(e, estado_con([cosa(3, "C", grupo="g-4"), cosa(2, "B", grupo="g-4"), cosa(1, "A", grupo="g-4")], grupos=[grupo(4, "Grupo")]), ruta="#grupo/g-4")
    esperar_vista(p2, "grupo")
    doble_toque(p2, quitar_de(p2, "C"), 150)
    p2.wait_for_timeout(500)
    igual(pagina_grupo(p2)["miembros"], ["B", "A"], "Un doble toque en «−» quita dos cosas")
    p2.context.close()
    quitar_de(p, "Dos").tap()
    esperar_pagina_grupo(p, "grupo vacío tras quitar todo", miembros=[], vacio=True, centrado=True)
    igual(p.evaluate("() => document.activeElement.id"), "anadir-cosas", "Vacío el grupo, el foco no va a «Añadir cosas»")
    p.wait_for_timeout(300)
    nota, m = p.get_by_text("Este grupo está vacío.", exact=True).bounding_box(), barra_anadir(p)
    arriba, abajo, centro = m["cabecera"]["b"], m["pildora"]["y"], nota["y"] + nota["height"] / 2
    comprobar(arriba + (abajo - arriba) * 0.35 <= centro <= arriba + (abajo - arriba) * 0.65, f"La nota no vuelve al centro al vaciarse el grupo: {nota} en [{arriba}, {abajo}]")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual(textos_nivel(p), ["Grupo", "Tres", "Una", "Dos"], "Las cosas quitadas no vuelven al nivel superior en su orden")
    # Quitar y volver a añadir conserva hecha y hechaEn.
    igual(check_de(fila_de(p, "Dos")).get_attribute("aria-pressed"), "true", "La hecha quitada del grupo pierde su estado")


@prueba("barra «Añadir cosas»: pegada abajo e igual que la de crear grupos; el desplegable se abre hacia arriba entre la cabecera y la barra, se desplaza por dentro con 60 cosas, al reabrirlo vuelve al principio y se cierra con el botón, Escape (también con el foco en body), un toque fuera y al cambiar de vista (el foco entra y vuelve, sin historial)")
def t_barra_anadir(e):
    p = sembrar(e, estado_con([cosa(i, f"Cosa número {i}", hecha=i % 4 == 0) for i in range(1, 61)], grupos=[grupo(100, "Grupo")]), ruta="#grupo/g-100")
    esperar_vista(p, "grupo")
    boton, pildora = boton_anadir(p), p.locator("#barra-anadir > .pildora")
    m = barra_anadir(p)
    caja, caja_boton = m["pildora"], boton.bounding_box()
    comprobar(abs(caja["ancho"] - caja_boton["width"]) <= 1 and abs(caja["alto"] - caja_boton["height"]) <= 1, f"El botón no es la píldora entera: {caja} vs {caja_boton}")
    comprobar(caja["alto"] >= 44 and 0 <= m["escribir"]["y"] - caja["b"] <= 16 and 0 <= e.alto - m["escribir"]["b"] <= 48,
              f"«Añadir cosas» no va justo encima de la barra de escribir, pegada abajo: {caja} / {m['escribir']}")
    comprobar(abs((caja["x"] + caja["d"]) / 2 - e.ancho / 2) <= 1, "La barra no está centrada")
    igual(boton.evaluate("(b) => [b.textContent.trim(), b.getAttribute('aria-expanded'), b.getAttribute('aria-controls'), b.type]"), ["Añadir cosas", "false", "candidatas", "button"], "Botón «Añadir cosas»")
    igual(m["panel"], None, "El desplegable se ve antes de abrirlo")
    # La misma barra que la de crear grupos: píldora (aspecto, tamaño y sitio) y contenedor (fijo abajo, relleno y degradado).
    aspecto = "(el) => { const cs = getComputedStyle(el), r = el.getBoundingClientRect(); return [cs.borderTopLeftRadius, cs.backgroundColor, cs.backgroundImage, cs.boxShadow, r.height, r.width, r.x, r.bottom]; }"
    contenedor = "(el) => { const cs = getComputedStyle(el); return [cs.position, cs.bottom, cs.paddingTop, cs.paddingBottom, cs.paddingLeft, cs.backgroundImage]; }"
    barra_pagina = [pildora.evaluate(aspecto), p.locator("#barra-anadir").evaluate(contenedor)]
    escalon = m["escribir"]["b"] - caja["b"]  # «Añadir cosas» queda sobre la barra de escribir: su píldora y su margen.
    igual(round(escalon), 66, "Escalón entre «Añadir cosas» y la barra de escribir")
    barra_pagina[0][-1] += escalon
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(300)  # Fin de la animación de entrada de la vista.
    barra_lista = [p.locator("#formulario-grupo .pildora").evaluate(aspecto), p.locator("#formulario-grupo").evaluate(contenedor)]
    igual(barra_pagina, barra_lista, "La barra «Añadir cosas» no es igual que la de crear grupos")
    p.wait_for_timeout(400)
    abrir_grupo(p, "Grupo")
    p.wait_for_timeout(400)
    historial = p.evaluate("() => history.length")
    # Se abre hacia arriba, entre la cabecera y la barra; con 60 candidatas se desplaza por dentro (no el cuerpo) y el foco va al primer «+».
    boton.tap()
    esperar_pagina_grupo(p, "desplegable abierto", desplegable=True, expandido="true")
    p.wait_for_timeout(300)
    m = barra_anadir(p)
    comprobar_desplegable_sobre_la_barra(m, "con 60 candidatas")
    comprobar(m["desplazable"], f"Con 60 candidatas el desplegable no se desplaza por dentro: {m['panel']}")
    comprobar(m["primerMas"], "Al abrir, el foco no va al primer «+»")
    igual(p.locator("#candidatas").evaluate("(c) => [getComputedStyle(c).overflowY, getComputedStyle(c).overscrollBehaviorY]"), ["auto", "contain"], "Desplazamiento interno del desplegable")
    p.evaluate("() => { const c = document.querySelector('#candidatas'); c.scrollTop = c.scrollHeight; }")
    p.wait_for_timeout(150)
    m = barra_anadir(p)
    ultima = p.locator("#lista-candidatas > li").last.bounding_box()
    comprobar(m["desplazamiento"] > 0 and m["desplazamientoZona"] == 0, f"Desplazar el desplegable mueve el cuerpo (o no se mueve): {m}")
    comprobar(m["panel"]["y"] <= ultima["y"] and ultima["y"] + ultima["height"] <= m["pildora"]["y"] + 0.5, f"Al final del desplegable la última candidata no se ve entera sobre la barra: {ultima} / {m['panel']}")
    # Se cierra con el botón (el foco vuelve a él) sin tocar el historial; con Escape; y con un toque fuera, en el cuerpo.
    boton.tap()
    esperar_pagina_grupo(p, "cerrado con el botón", desplegable=False, expandido="false", foco="anadir-cosas")
    igual(p.evaluate("() => history.length"), historial, "Abrir y cerrar el desplegable añade entradas al historial")
    p.wait_for_timeout(400)
    boton.tap()
    esperar_pagina_grupo(p, "abierto de nuevo", desplegable=True, expandido="true")
    p.wait_for_timeout(300)
    # Reabierto tras desplazarlo hasta el final: vuelve al principio y el primer «+» (con el foco) se ve dentro del panel.
    m = barra_anadir(p)
    primer_mas = p.locator("#lista-candidatas .mas").first.bounding_box()
    comprobar(m["desplazamiento"] == 0, f"Al reabrir, el desplegable conserva el desplazamiento anterior: {m['desplazamiento']}px")
    comprobar(m["panel"]["y"] - 0.5 <= primer_mas["y"] and primer_mas["y"] + primer_mas["height"] <= m["panel"]["b"] + 0.5, f"Al reabrir, el primer «+» queda fuera de lo visible del desplegable: {primer_mas} / {m['panel']}")
    p.keyboard.press("Escape")
    esperar_pagina_grupo(p, "cerrado con Escape", desplegable=False, expandido="false", foco="anadir-cosas")
    # Escape también cierra cuando el foco no está en ningún control (por ejemplo tras tocar el texto de una candidata).
    p.wait_for_timeout(400)
    boton.tap()
    esperar_pagina_grupo(p, "abierto con el foco suelto", desplegable=True, expandido="true")
    p.evaluate("() => document.activeElement.blur()")
    igual(p.evaluate("() => document.activeElement.tagName"), "BODY", "No se ha podido soltar el foco")
    p.keyboard.press("Escape")
    esperar_pagina_grupo(p, "cerrado con Escape desde body", desplegable=False, expandido="false", foco="anadir-cosas")
    p.wait_for_timeout(400)
    boton.tap()
    esperar_pagina_grupo(p, "abierto por tercera vez", desplegable=True, expandido="true")
    p.wait_for_timeout(300)
    m = barra_anadir(p)
    p.touchscreen.tap(e.ancho / 2, (m["cabecera"]["b"] + m["panel"]["y"]) / 2)
    esperar_pagina_grupo(p, "cerrado con un toque fuera", desplegable=False, expandido="false", foco="anadir-cosas")
    # Un doble toque en el botón lo deja abierto; al cambiar de vista se cierra y no reaparece al volver.
    p.wait_for_timeout(400)
    doble_toque(p, boton, 150)
    p.wait_for_timeout(500)
    esperar_pagina_grupo(p, "tras un doble toque", desplegable=True, expandido="true")
    p.go_back()
    esperar_vista(p, "lista")
    igual(p.evaluate("() => [document.querySelector('#candidatas').hidden, document.querySelector('#anadir-cosas').getAttribute('aria-expanded')]"), [True, "false"], "Al salir de la página el desplegable sigue abierto")
    p.wait_for_timeout(400)
    abrir_grupo(p, "Grupo")
    igual(pagina_grupo(p)["desplegable"], False, "Al volver a la página el desplegable aparece abierto")


@prueba("desplegable reabierto con teclado mientras una fila se va (Intro en «+», Escape, Intro): el foco cae en el «+» de una fila que se queda, no se pierde en body al irse la otra, y Escape lo sigue cerrando")
def t_desplegable_reabierto_en_carrera(e):
    p = sembrar(e, estado_con([cosa(i, f"Cosa {i}") for i in range(1, 6)], grupos=[grupo(100, "Grupo")]), ruta="#grupo/g-100")
    esperar_vista(p, "grupo")
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable abierto", desplegable=True, expandido="true", candidatas=[f"Cosa {i}" for i in (5, 4, 3, 2, 1)])
    p.wait_for_timeout(400)  # Pasa la guarda del doble toque del botón.
    comprobar(barra_anadir(p)["primerMas"], "Al abrir, el foco no va al primer «+»")
    # Intro en el «+» (su fila tarda 220 ms en irse), Escape (el foco vuelve al botón) e Intro en el botón: la fila aún está en la lista.
    p.keyboard.press("Enter")
    p.keyboard.press("Escape")
    p.keyboard.press("Enter")
    foco = p.evaluate(JS_FOCO_CANDIDATAS)
    comprobar(foco["filaYendose"], f"La fila añadida ya se había ido al reabrir: la carrera no se ha dado ({foco})")
    igual((foco["enFilaQueSeQueda"], foco["texto"]), (True, "Cosa 4"), "Al reabrir mientras una fila se va, el foco no está en el «+» de la primera fila que se queda")
    p.wait_for_timeout(300)  # La fila que se iba ya no está.
    foco = p.evaluate(JS_FOCO_CANDIDATAS)
    igual((foco["filaYendose"], foco["enFilaQueSeQueda"], foco["texto"]), (False, True, "Cosa 4"), "Al irse la fila, el foco se ha perdido")
    esperar_pagina_grupo(p, "tras la carrera", desplegable=True, expandido="true", miembros=["Cosa 5"], candidatas=["Cosa 4", "Cosa 3", "Cosa 2", "Cosa 1"])
    p.keyboard.press("Escape")
    esperar_pagina_grupo(p, "cerrado con Escape tras la carrera", desplegable=False, expandido="false", foco="anadir-cosas")


@prueba("página de grupo con la barra abajo: las cosas empiezan arriba y la última de una lista larga se alcanza sobre la barra, la nota de grupo vacío queda centrada, los avisos salen por encima de la barra y nada desborda (vertical, apaisado y 320px, con el desplegable abierto)")
def t_cuerpo_grupo_con_barra(e):
    largo = estado_con([cosa(i, f"Cosa número {i}", hecha=i % 4 == 0, grupo="g-1") for i in range(1, 41)] + [cosa(50, "Suelta")], grupos=[grupo(1, "Larga")])
    for etiqueta, ancho, alto in (("vertical", e.ancho, e.alto), ("apaisado", e.alto, e.ancho), ("320", 320, 568)):
        p = sembrar(e, largo, ruta="#grupo/g-1", contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_vista(p, "grupo")
        m = barra_anadir(p)
        primera = p.locator("#lista-grupo > li").first.bounding_box()
        comprobar(m["cabecera"]["b"] <= primera["y"] <= m["cabecera"]["b"] + 40, f"[{etiqueta}] La primera cosa no empieza justo bajo la cabecera: {primera} / {m['cabecera']}")
        comprobar(0 <= alto - m["escribir"]["b"] <= 48 and 0 <= m["escribir"]["y"] - m["pildora"]["b"] <= 16,
                  f"[{etiqueta}] La barra de escribir no está pegada abajo con «Añadir cosas» justo encima: {m['pildora']} / {m['escribir']}")
        p.evaluate("() => { const z = document.querySelector('#zona-grupo'); z.scrollTop = z.scrollHeight; }")
        p.wait_for_timeout(150)
        ultima = p.locator("#lista-grupo > li").last.bounding_box()
        comprobar(ultima["y"] + ultima["height"] <= m["pildora"]["y"] + 0.5, f"[{etiqueta}] Al final de la lista la última cosa queda bajo la barra: {ultima} / {m['pildora']}")
        comprobar_sin_desborde(p, f"página del grupo ({etiqueta})")
        boton_anadir(p).tap()
        esperar_pagina_grupo(p, f"[{etiqueta}] desplegable abierto", desplegable=True, candidatas=["Suelta"])
        p.wait_for_timeout(300)
        comprobar_desplegable_sobre_la_barra(barra_anadir(p), f"[{etiqueta}]")
        comprobar_sin_desborde(p, f"página del grupo con el desplegable ({etiqueta})")
        # Un aviso en la página del grupo sale por encima de la barra.
        p.keyboard.press("Escape")
        p.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
        aviso = esperar_toast(p, "Aplicación añadida")
        comprobar(aviso["y"] + aviso["alto"] <= m["pildora"]["y"], f"[{etiqueta}] El aviso tapa la barra: {aviso} / {m['pildora']}")
        p.context.close()
    # Grupo vacío: la nota, centrada en el cuerpo (entre la cabecera y la barra), también en apaisado.
    for etiqueta, ancho, alto in (("vertical", e.ancho, e.alto), ("apaisado", e.alto, e.ancho)):
        p = sembrar(e, estado_con([cosa(1, "Suelta")], grupos=[grupo(2, "Vacío")]), ruta="#grupo/g-2", contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_vista(p, "grupo")
        m = barra_anadir(p)
        nota = p.get_by_text("Este grupo está vacío.", exact=True).bounding_box()
        arriba, abajo, centro = m["cabecera"]["b"], m["pildora"]["y"], nota["y"] + nota["height"] / 2
        comprobar(arriba + (abajo - arriba) * 0.35 <= centro <= arriba + (abajo - arriba) * 0.65, f"[{etiqueta}] La nota de grupo vacío no está centrada entre la cabecera y la barra: {nota} en [{arriba}, {abajo}]")
        comprobar(abs(nota["x"] + nota["width"] / 2 - ancho / 2) <= 3, f"[{etiqueta}] La nota de grupo vacío no está centrada en horizontal: {nota}")
        p.context.close()


@prueba("editar grupo (hoja): diálogo accesible, nombre precargado, «Sin color» + 12 muestras, Guardar aplica nombre y color; cerrar de otro modo descarta; vacío conserva el nombre")
def t_hoja_editar_grupo(e):
    p = sembrar(e, estado_con([cosa(1, "Dentro", grupo="g-2")], grupos=[grupo(2, "Casa")]), ruta="#grupo/g-2")
    esperar_vista(p, "grupo")
    historial = p.evaluate("() => history.length")
    igual(p.get_by_role("radio").count(), 0, "Con la hoja cerrada no debe haber muestras de color en la página")
    estado = abrir_hoja_grupo(p)
    igual((estado["titulo"], estado["modal"], estado["nombre"], estado["elegida"], estado["focoDentro"], estado["paginaInerte"], estado["paginaOculta"]),
          ("Editar grupo", "true", "Casa", "", True, True, "true"), "Hoja de edición recién abierta")
    igual(estado["muestras"], ["Sin color"] + [n for n, _ in PALETA], "Muestras de la hoja")
    dialogo = p.get_by_role("dialog", name="Editar grupo", exact=True)
    igual(dialogo.count(), 1, "Diálogos con el nombre de la hoja")
    comprobar(p.evaluate("() => !document.querySelector('main').contains(document.querySelector('#hoja-grupo'))"), "La hoja está dentro de lo que queda inerte")
    campo_n = nombre_en_hoja(p)
    igual((campo_n.get_attribute("maxlength"), campo_n.evaluate("(c) => parseFloat(getComputedStyle(c).fontSize) >= 16"), campo_n.evaluate("(c) => c.labels[0].textContent.trim()")), ("60", True, "Nombre"), "Campo Nombre de la hoja")
    igual(p.locator("#hoja-grupo").get_by_text("Color", exact=True).count(), 1, "Falta la etiqueta «Color»")
    igual(p.locator("#hoja-grupo input[type=color]").count(), 0, "La hoja no debe ofrecer color personalizado")
    medidas = p.evaluate("() => [...document.querySelectorAll('#hoja-grupo [role=radiogroup] label')].map((l) => { const r = l.getBoundingClientRect(); return [r.width >= 44, getComputedStyle(l.querySelector('span')).borderTopLeftRadius]; })")
    comprobar(all(a and r == "50%" for a, r in medidas), f"Las muestras de la hoja no son redondas de ≥44px: {medidas}")
    guardar = p.get_by_role("button", name="Guardar", exact=True)
    caja_g, caja_d = guardar.bounding_box(), dialogo.bounding_box()
    comprobar(caja_g["height"] >= 48 and caja_g["width"] >= caja_d["width"] - 48, f"«Guardar» no es una píldora ancha: {caja_g} en {caja_d}")
    # Tabulador retenido dentro; Escape cierra y descarta; el foco vuelve al lápiz.
    for tecla in ("Tab", "Tab", "Tab", "Shift+Tab", "Shift+Tab", "Shift+Tab", "Shift+Tab"):
        p.keyboard.press(tecla)
        comprobar(hoja_grupo(p)["focoDentro"], f"Con {tecla} el foco se sale de la hoja: {hoja_grupo(p)['foco']}")
    campo_n.fill("Cambio que se descarta")
    elegir_color(p, "Coral")
    p.keyboard.press("Escape")
    estado = hoja_grupo(p)
    igual((estado["abierta"], estado["paginaInerte"], estado["paginaOculta"], estado["foco"]), (False, False, None, "editar-grupo"), "Hoja tras Escape")
    igual((pagina_grupo(p)["titulo"], leer_estado(p)["grupos"][0]["nombre"], leer_estado(p)["grupos"][0]["color"]), ("Casa", "Casa", None), "Escape guardó cambios")
    p.wait_for_timeout(400)
    igual(abrir_hoja_grupo(p)["nombre"], "Casa", "Al reabrir, la hoja no vuelve a los valores guardados")
    igual(hoja_grupo(p)["elegida"], "", "Al reabrir, el color descartado sigue elegido")
    p.touchscreen.tap(e.ancho / 2, 40)
    esperar(p, f"() => !({JS_HOJA_GRUPO})().abierta", que="tocar el velo cierra la hoja")
    p.wait_for_timeout(400)
    abrir_hoja_grupo(p)
    p.get_by_role("button", name="Cerrar", exact=True).tap()
    comprobar(not hoja_grupo(p)["abierta"], "«Cerrar» no cierra la hoja")
    igual(p.evaluate("() => history.length"), historial, "La hoja añade entradas al historial")
    # Guardar: nombre recortado y color.
    p.wait_for_timeout(400)
    abrir_hoja_grupo(p)
    campo_n.fill("  Casa   y  huerto ")
    elegir_color(p, "Amarillo")
    guardar_hoja(p)
    igual(p.evaluate("() => document.activeElement.id"), "editar-grupo", "Tras guardar el foco no vuelve al lápiz")
    guardado = leer_estado(p)["grupos"][0]
    igual((pagina_grupo(p)["titulo"], guardado["nombre"], guardado["color"]), ("Casa y huerto", "Casa y huerto", "#F2B705"), "Guardar no aplica nombre y color")
    igual(p.evaluate("() => document.querySelector('#vista-grupo h1').childElementCount"), 0, "El título contiene elementos")
    # Vacío conserva el nombre; «Sin color» quita el color.
    p.wait_for_timeout(400)
    abrir_hoja_grupo(p)
    igual(hoja_grupo(p)["elegida"], "#F2B705", "La hoja no precarga el color guardado")
    campo_n.fill("   ")
    elegir_color(p, "Sin color")
    p.wait_for_timeout(200)
    comprobar(p.evaluate("() => getComputedStyle(document.querySelector('#hoja-grupo .sin-color input:checked + span svg')).opacity === '1'"), "«Sin color» elegido no enseña su check")
    campo_n.press("Enter")
    esperar(p, f"() => !({JS_HOJA_GRUPO})().abierta", que="Intro en el nombre guarda y cierra")
    guardado = leer_estado(p)["grupos"][0]
    igual((pagina_grupo(p)["titulo"], guardado["nombre"], guardado["color"]), ("Casa y huerto", "Casa y huerto", None), "Un nombre vacío no conserva el anterior o «Sin color» no quita el color")
    # Se cierra sola al cambiar de vista (atrás del sistema) y la lista enseña el nombre nuevo.
    p.wait_for_timeout(400)
    abrir_hoja_grupo(p)
    p.go_back()
    esperar_vista(p, "lista")
    comprobar(not hoja_grupo(p)["abierta"] and not hoja_grupo(p)["paginaInerte"], "La hoja sigue abierta al cambiar de vista")
    igual(textos_nivel(p), ["Casa y huerto"], "La lista no enseña el nombre editado")
    # Doble toque en el lápiz: la hoja se queda abierta; doble toque en Guardar: guarda una vez y lo de debajo no se acciona.
    p.wait_for_timeout(400)
    abrir_grupo(p, "Casa y huerto")
    p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista (el lápiz es un botón redondo).
    for pausa in (0, 150, 250):
        doble_toque(p, boton_editar_grupo(p), pausa)
        p.wait_for_timeout(500)
        comprobar(hoja_grupo(p)["abierta"], f"Un doble toque ({pausa} ms) en el lápiz abre y cierra la hoja")
        p.keyboard.press("Escape")
        p.wait_for_timeout(400)
    abrir_hoja_grupo(p)
    campo_n.fill("Final")
    doble_toque(p, p.get_by_role("button", name="Guardar", exact=True), 200)
    p.wait_for_timeout(500)
    igual((hoja_grupo(p)["abierta"], pagina_grupo(p)["titulo"], pagina_grupo(p)["desplegable"]), (False, "Final", False), "Tras un doble toque en Guardar")


@prueba("editar grupo con el desplegable abierto: se cierra antes de que la hoja tome el foco, que se queda en su título (con «inert» y sin él, como en iOS 15); al cerrar la hoja el foco vuelve al lápiz y el desplegable sigue cerrado")
def t_hoja_grupo_con_desplegable(e):
    datos = estado_con([cosa(1, "Fuera"), cosa(2, "Dentro", grupo="g-2")], grupos=[grupo(2, "Casa")])
    for etiqueta, guiones, opciones in (("con inert", (), {}), ("sin inert", (JS_SIN_INERT,), {"user_agent": UA_IPHONE})):
        contexto = e.contexto(**opciones)
        for guion in guiones:
            contexto.add_init_script(guion)
        p = sembrar(e, datos, ruta="#grupo/g-2", contexto=contexto)
        esperar_vista(p, "grupo")
        boton_anadir(p).tap()
        esperar_pagina_grupo(p, f"[{etiqueta}] desplegable abierto", desplegable=True, expandido="true", candidatas=["Fuera"])
        p.wait_for_timeout(400)  # Pasa la guarda del doble toque del botón.
        estado = abrir_hoja_grupo(p)
        igual((estado["foco"], estado["focoDentro"], estado["paginaInerte"], estado["paginaOculta"]), ("titulo-hoja-grupo", True, etiqueta == "con inert", "true"), f"[{etiqueta}] Hoja abierta con el desplegable abierto")
        igual((pagina_grupo(p)["desplegable"], pagina_grupo(p)["expandido"]), (False, "false"), f"[{etiqueta}] El desplegable sigue abierto bajo la hoja")
        p.keyboard.press("Escape")
        igual((hoja_grupo(p)["abierta"], hoja_grupo(p)["foco"], pagina_grupo(p)["desplegable"]), (False, "editar-grupo", False), f"[{etiqueta}] Tras cerrar la hoja")
        p.context.close()


JS_MEDIDAS_HOJA_GRUPO = r"""
() => {
  const dialogo = document.querySelector('#hoja-grupo [role=dialog]');
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom }; };
  const visibles = [dialogo, ...dialogo.querySelectorAll('*')].filter((el) => el.checkVisibility() && el.getBoundingClientRect().width > 0);
  return { panel: caja(dialogo), alto: innerHeight, desbordePanel: dialogo.scrollWidth - dialogo.clientWidth,
           fuera: visibles.filter((el) => { const r = el.getBoundingClientRect(); return r.left < -0.5 || r.right > innerWidth + 0.5; }).map((el) => el.tagName + '.' + el.className) };
}
"""


JS_COLOR_GRUPO = "() => {" + JS_UTIL + r"""
  const vista = document.querySelector('#vista-grupo'), fila = document.querySelector('#lista-grupo > li');
  const ojo = document.querySelector('#lista-cosas .ojo');
  return {
    fondo: redondear(fondoEfectivo(vista)), fondoHtml: redondear(fondoEfectivo(document.body)), tono: vista.dataset.tono, tonoRaiz: document.documentElement.dataset.tono,
    tema: document.querySelector('meta[name=theme-color]').content.toUpperCase(),
    texto: { 'título del grupo': contrasteTexto(document.querySelector('#titulo-grupo')), 'texto de una cosa': fila ? contrasteTexto(fila.querySelector('.texto')) : 99,
             'botón Añadir cosas': contrasteTexto(document.querySelector('#anadir-cosas')), 'nota del grupo': contrasteTexto(document.querySelector('#grupo-vacio')) },
    iconos: { 'Volver': contrasteIcono(document.querySelector('#vista-grupo [data-volver] svg')), 'lápiz': contrasteIcono(document.querySelector('#editar-grupo svg')),
              'quitar': fila ? contrasteIcono(fila.querySelector('.quitar svg')) : 99 },
    anillo: getComputedStyle(document.querySelector('#editar-grupo')).outlineColor,
    ojo: ojo ? { fondo: redondear(parsear(getComputedStyle(ojo).backgroundColor)), icono: contrasteIcono(ojo.querySelector('svg:not([hidden])')) } : null,
  };
}"""


JS_TONO_AVISO = "() => {" + JS_UTIL + r"""
  const aviso = document.querySelector('#toast'), vista = document.querySelector('#vista-grupo');
  return { tono: aviso.dataset.tono || null, contraste: contraste(fondoEfectivo(aviso), fondoEfectivo(vista)) };
}"""


@prueba("color del grupo: pinta su página (fondo real, data-tono propio, theme-color mientras se enseña y restaurado al salir), el aviso que salga en ella y el disco del ojo, en un color claro y uno oscuro")
def t_color_grupo_efectos(e):
    for fondo_app, color_grupo, tono_grupo in (("#2F6FED", "#F2B705", "claro"), ("#FFFFFF", "#111111", "oscuro"), ("#F2B705", "#1E2A44", "oscuro")):
        p = sembrar(e, estado_con([cosa(1, "Dentro", grupo="g-2")], grupos=[grupo(2, "Color", color=color_grupo)], color=fondo_app), ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
        esperar_filas(p, 1)
        igual(p.locator('meta[name="theme-color"]').get_attribute("content").upper(), fondo_app, "theme-color en la lista")
        abrir_grupo(p, "Color")
        p.wait_for_timeout(350)
        m = p.evaluate(JS_COLOR_GRUPO)
        igual((tuple(m["fondo"]), m["tono"], m["tema"], tuple(m["fondoHtml"])), (hex_a_rgb(color_grupo), tono_grupo, color_grupo, hex_a_rgb(fondo_app)),
              f"Grupo {color_grupo} sobre {fondo_app}: fondo de la vista, tono, theme-color y fondo del documento")
        igual(m["tonoRaiz"], "oscuro" if fondo_app in ("#2F6FED",) else "claro", "El tono de la raíz cambió al abrir el grupo")
        puntos = [(2, 2), (e.ancho - 4, 2), (e.ancho // 2, e.alto // 2), (2, e.alto - 4), (e.ancho - 4, e.alto - 4)]
        igual(set(muestrear(p, puntos)), {hex_a_rgb(color_grupo)}, f"Píxeles reales de la página del grupo {color_grupo}")
        for conjunto, minimo in (("texto", 4.5), ("iconos", 3.0)):
            for que, valor in m[conjunto].items():
                detalle(f"{color_grupo} sobre {fondo_app} · {que}: {valor:.2f}")
                comprobar(valor >= minimo, f"Grupo {color_grupo}: {que} = {valor:.2f}:1 (mínimo {minimo})")
        # El anillo de foco y la hoja de edición toman la tinta del grupo, no la de la app.
        tinta = (10, 10, 10) if tono_grupo == "claro" else (255, 255, 255)
        p.get_by_role("button", name="Volver", exact=True).focus()
        igual(rgb_css(p.evaluate("() => getComputedStyle(document.activeElement).outlineColor")), tinta, f"Grupo {color_grupo}: el anillo de foco no usa la tinta del grupo")
        abrir_hoja_grupo(p)
        igual(hoja_grupo(p)["tono"], tono_grupo, "La hoja de edición no lleva el tono del grupo")
        igual(rgb_css(p.evaluate("() => getComputedStyle(document.querySelector('#hoja-grupo [role=dialog]')).backgroundColor")), hex_a_rgb(color_grupo), "La hoja no es del color del grupo")
        comprobar(p.evaluate("() => {" + JS_UTIL + " return contrasteTexto(document.querySelector('#titulo-hoja-grupo')); }") >= 4.5, "El título de la hoja no contrasta sobre el color del grupo")
        p.keyboard.press("Escape")
        # Un aviso en la página del grupo (vive fuera de la vista) toma el tono del grupo: su píldora se distingue del fondo.
        p.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
        esperar_toast(p, "Aplicación añadida")
        aviso = p.evaluate(JS_TONO_AVISO)
        igual(aviso["tono"], tono_grupo, f"Grupo {color_grupo} sobre {fondo_app}: el aviso no lleva el tono del grupo")
        comprobar(aviso["contraste"] >= 3, f"Grupo {color_grupo} sobre {fondo_app}: la píldora del aviso no se distingue de la página ({aviso['contraste']:.2f}:1)")
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        igual(p.locator('meta[name="theme-color"]').get_attribute("content").upper(), fondo_app, "theme-color no se restaura al salir del grupo")
        tonos = p.evaluate("() => [document.querySelector('#toast').dataset.tono, document.documentElement.dataset.tono]")
        igual(tonos[0], tonos[1], "Al salir del grupo el aviso no recupera el tono de la app")
        m = p.evaluate(JS_COLOR_GRUPO)
        igual(tuple(m["ojo"]["fondo"]), hex_a_rgb(color_grupo), "El disco del ojo no lleva el color del grupo")
        comprobar(m["ojo"]["icono"] >= 3, f"El icono del ojo sobre {color_grupo} contrasta {m['ojo']['icono']:.2f}:1")
        p.context.close()
    # Sin color: la página usa el fondo de la app y el ojo es gris; la hoja, el tono de la app.
    p = sembrar(e, estado_con([], grupos=[grupo(2, "Sin color")], color="#FFFFFF"), ruta="#grupo/g-2")
    esperar_vista(p, "grupo")
    m = p.evaluate(JS_COLOR_GRUPO)
    igual((tuple(m["fondo"]), m["tono"], m["tema"]), ((255, 255, 255), "claro", "#FFFFFF"), "Grupo sin color: fondo, tono y theme-color de la app")
    igual(set(muestrear(p, [(2, 2), (e.ancho // 2, e.alto // 2)])), {(255, 255, 255)}, "Píxeles del grupo sin color")
    p.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
    esperar_toast(p, "Aplicación añadida")
    aviso = p.evaluate(JS_TONO_AVISO)
    comprobar(aviso["tono"] == "claro" and aviso["contraste"] >= 3, f"Grupo sin color: el aviso no lleva el tono de la app o no se distingue ({aviso})")


JS_CONTRASTE_DESPLEGABLE = "() => {" + JS_UTIL + r"""
  const fila = document.querySelector('#lista-candidatas > li'), panel = document.querySelector('#candidatas');
  return {
    texto: { 'texto de una candidata': contrasteTexto(fila.querySelector('.texto')), 'botón Añadir cosas': contrasteTexto(document.querySelector('#anadir-cosas')),
             'texto del campo de escribir del grupo': contrasteTexto(document.querySelector('#campo-en-grupo')) },
    iconos: { '«+»': contrasteIcono(fila.querySelector('.mas svg')), 'placeholder del campo del grupo': contrasteTexto(document.querySelector('#campo-en-grupo'), '::placeholder') },
    tono: document.querySelector('#vista-grupo').dataset.tono, fondoPanel: redondear(fondoEfectivo(panel)),
    punto: [panel.getBoundingClientRect().left + 3, panel.getBoundingClientRect().top + panel.getBoundingClientRect().height / 2],
  };
}"""


@prueba("desplegable «Añadir cosas»: texto ≥ 4,5 e icono «+» ≥ 3 sobre la barra en Azul, Blanco y Negro y en grupos Amarillo y Azul noche (el panel toma el color y el tono del grupo)")
def t_desplegable_legible(e):
    for fondo_app, color_grupo, tono in (("#2F6FED", None, "oscuro"), ("#FFFFFF", None, "claro"), ("#111111", None, "oscuro"), ("#2F6FED", "#F2B705", "claro"), ("#FFFFFF", "#1E2A44", "oscuro")):
        p = sembrar(e, estado_con([cosa(2, "Dentro", grupo="g-2"), cosa(1, "Fuera")], grupos=[grupo(2, "Color", color=color_grupo)], color=fondo_app), ruta="#grupo/g-2", contexto=e.contexto(reduced_motion="reduce"))
        esperar_vista(p, "grupo")
        boton_anadir(p).tap()
        esperar_pagina_grupo(p, "desplegable abierto", desplegable=True, candidatas=["Fuera"])
        m = p.evaluate(JS_CONTRASTE_DESPLEGABLE)
        igual(m["tono"], tono, f"Grupo {color_grupo} sobre {fondo_app}: tono de la página")
        for conjunto, minimo in (("texto", 4.5), ("iconos", 3.0)):
            for que, valor in m[conjunto].items():
                detalle(f"{color_grupo or fondo_app} · {que}: {valor:.2f}")
                comprobar(valor >= minimo, f"Grupo {color_grupo} sobre {fondo_app}: {que} = {valor:.2f}:1 (mínimo {minimo})")
        # Píxeles reales del panel: su superficie sobre el color del grupo (no sobre el de la app), y opaco sobre lo que pasa por debajo.
        [pixel] = muestrear(p, [tuple(m["punto"])])
        comprobar(all(abs(a - b) <= 3 for a, b in zip(pixel, m["fondoPanel"])), f"Grupo {color_grupo} sobre {fondo_app}: el panel no se pinta con su superficie sobre el color del grupo: {pixel} vs {m['fondoPanel']}")
        p.context.close()


@prueba("grupos: un nombre con código se pinta literal; nombres de 60 caracteres y sin espacios no desbordan la fila, el título ni la hoja")
def t_grupo_xss_y_largos(e):
    largo = ("Grupo con un nombre bastante largo para probar el ajuste " * 2)[:60].strip()
    sin_espacios = "G" * 60
    p = sembrar(e, estado_con([cosa(1, "Cosa")], grupos=[grupo(4, XSS[:60]), grupo(3, largo), grupo(2, sin_espacios)]), ruta="#lista")
    esperar_filas(p, 4)
    igual(textos_nivel(p)[:3], [XSS[:60], largo, sin_espacios], "Nombres literales en la lista")
    igual(p.evaluate("() => document.querySelectorAll('#lista-cosas img, #lista-cosas script, #lista-cosas b, #lista-cosas a > *').length"), 0, "Elementos creados por el nombre del grupo")
    igual(p.evaluate("() => window.__xss"), None, "Se ejecutó código inyectado desde un nombre de grupo")
    comprobar_sin_desborde(p, "la lista con nombres de grupo largos")
    for nombre in (largo, sin_espacios):
        fila = fila_grupo(p, nombre)
        datos = fila.evaluate("""(f) => { const r = f.getBoundingClientRect(), a = f.querySelector('.enlace-grupo').getBoundingClientRect(), b = f.querySelector('.borrar').getBoundingClientRect();
            return { desborde: f.scrollWidth - f.clientWidth, dentro: a.right <= b.left + 0.5 && b.right <= r.right + 0.5, lineas: a.height > 30 }; }""")
        comprobar(datos["desborde"] <= 0 and datos["dentro"], f"El nombre largo pisa la papelera o desborda: {datos}")
        comprobar(datos["lineas"], "Un nombre de 60 caracteres debería ocupar más de una línea (no se recorta)")
    crear_grupo(p, "<b>Nuevo</b>" + XSS)
    esperar_toast(p, "Grupo creado")
    igual(textos_nivel(p)[0], ("<b>Nuevo</b>" + XSS)[:60], "El nombre creado con código se guarda literal (recortado a 60)")
    igual(p.evaluate("() => window.__xss"), None, "Se ejecutó código inyectado al crear un grupo")
    for nombre in (XSS[:60], sin_espacios):
        abrir_grupo(p, nombre)
        titulo = p.locator("#vista-grupo h1")
        igual((titulo.text_content(), titulo.evaluate("(h) => h.childElementCount")), (nombre, 0), "Título de la página del grupo")
        igual(round(p.locator("#vista-grupo header").bounding_box()["height"]), 48, "La cabecera crece con el nombre largo (debe cortarse a dos líneas, como el de la lista)")
        comprobar_sin_desborde(p, f"la página del grupo «{nombre[:12]}…»")
        abrir_hoja_grupo(p)
        igual(nombre_en_hoja(p).input_value(), nombre, "La hoja no precarga el nombre literal")
        igual(p.evaluate("() => document.querySelector('#hoja-grupo').scrollWidth - document.querySelector('#hoja-grupo').clientWidth"), 0, "La hoja desborda")
        p.keyboard.press("Escape")
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        p.wait_for_timeout(400)
    p.reload()
    esperar_filas(p, 5)
    igual(p.evaluate("() => document.querySelectorAll('main img, main script, main b').length + (window.__xss || 0)"), 0, "Código inyectado tras recargar")


@prueba("500 cosas y 50 grupos (la mitad abiertos): la lista y la página de un grupo se pintan rápido y marcar sigue siendo ágil")
def t_rendimiento_grupos(e):
    cosas = [cosa(i, f"Cosa número {i}", hecha=i % 3 == 0, grupo=(f"g-{i % 50}" if i % 2 == 0 else None)) for i in range(1, 501)]
    grupos = [grupo(1000 + i, f"Grupo {i}", abierto=i % 2 == 0, color=("#F2B705" if i % 5 == 0 else None), id=f"g-{i}") for i in range(50)]
    p = sembrar(e, estado_con(cosas, grupos=grupos))
    inicio = time.monotonic()
    boton_lista(p).tap()
    esperar_filas(p, 300, ms=6000)  # 250 sueltas + 50 grupos.
    pintado = time.monotonic() - inicio
    detalle(f"300 filas de nivel superior (y 250 anidadas) pintadas en {pintado * 1000:.0f} ms")
    comprobar(pintado < 3.0, f"Pintar 500 cosas y 50 grupos tarda {pintado:.2f} s")
    igual(p.evaluate("() => document.querySelectorAll('#lista-cosas .anidada > li').length"), 250, "Filas anidadas")
    igual(textos_nivel(p)[:2], ["Grupo 49", "Grupo 48"], "Los grupos (los más nuevos) van primero")
    coste = p.evaluate("""() => { const filas = document.querySelectorAll('#lista-cosas > li.fila'); const medir = (f) => { const t = performance.now(); f(); document.body.getBoundingClientRect(); filas[0].getBoundingClientRect(); return performance.now() - t; };
        const anidada = document.querySelector('#lista-cosas .anidada > li');
        return { marcar: medir(() => filas[0].querySelector('button').click()), marcarAnidada: medir(() => anidada.querySelector('button').click()),
                 ojo: medir(() => document.querySelector('#lista-cosas .ojo').click()) }; }""")
    detalle(f"coste síncrono con 550 filas: {coste}")
    comprobar(all(v < 100 for v in coste.values()), f"Con 500 cosas y 50 grupos algo bloquea demasiado: {coste}")
    igual(deslizandose(p), 0, "Con más de 150 filas no debe haber deslizamiento")
    inicio = time.monotonic()
    fila = fila_de(p, "Cosa número 497")
    check_de(fila).tap()
    esperar(p, "(b) => b.getAttribute('aria-pressed') === 'true'", arg=check_de(fila).element_handle(), ms=2000, que="marcar responde con 550 filas")
    comprobar(time.monotonic() - inicio < 1.5, f"Marcar tarda {time.monotonic() - inicio:.2f} s con 550 filas")
    inicio = time.monotonic()
    abrir_grupo(p, "Grupo 0")
    esperar(p, "() => document.querySelectorAll('#lista-grupo > li').length === 10", que="la página del grupo pinta sus cosas")
    boton_anadir(p).tap()
    esperar(p, "() => document.querySelectorAll('#lista-candidatas > li').length === 250", ms=6000, que="el desplegable pinta las 250 cosas sueltas (las de otros grupos no salen)")
    detalle(f"página del grupo con 250 candidatas en {(time.monotonic() - inicio) * 1000:.0f} ms")
    comprobar(time.monotonic() - inicio < 3.0, "La página del grupo con el desplegable tarda demasiado")
    mas_de(p, "Cosa número 499").tap()
    esperar(p, "() => document.querySelectorAll('#lista-grupo > li:not(.saliendo)').length === 11", que="añadir con 250 candidatas responde")


@prueba("sin desplazamiento horizontal con grupos: lista (grupos abiertos) y página del grupo (desplegable abierto) a 320px y en apaisado")
def t_sin_desborde_grupos(e):
    datos = estado_con([cosa(5, "B" * 120, grupo="g-6"), cosa(4, "Normal", grupo="g-6"), cosa(3, "Otra cosa con texto largo que ocupa varias líneas en un móvil estrecho"),
                        cosa(2, "Hecha", hecha=True), cosa(1, "A" * 90)], grupos=[grupo(6, "N" * 60, abierto=True), grupo(7, "Grupo con un nombre bastante largo para ajustar", abierto=True)])
    for ancho, alto in ((320, 568), (e.alto, e.ancho)):
        contexto = e.contexto(viewport={"width": ancho, "height": alto})
        p = sembrar(e, datos, ruta="#lista", contexto=contexto)
        esperar_filas(p, 5)
        comprobar_sin_desborde(p, f"lista con grupos a {ancho}x{alto}")
        boton_crear_grupo(p).tap()
        campo_grupo(p).fill("Nombre largo para la barra de crear grupos " * 2)
        comprobar_sin_desborde(p, f"lista editando un grupo a {ancho}x{alto}")
        p.keyboard.press("Escape")
        pildora = p.locator("#formulario-grupo .pildora").bounding_box()
        comprobar(pildora["x"] >= 0 and pildora["x"] + pildora["width"] <= ancho and pildora["y"] + pildora["height"] <= alto, f"La barra se sale a {ancho}x{alto}: {pildora}")
        texto_boton = boton_crear_grupo(p).evaluate(JS_RECORTADO)
        comprobar(not texto_boton, f"«Crear grupo de cosas» sale recortado a {ancho}x{alto}")
        abrir_grupo(p, "N" * 60)
        comprobar_sin_desborde(p, f"página del grupo a {ancho}x{alto}")
        boton_anadir(p).tap()
        esperar_pagina_grupo(p, "desplegable abierto", desplegable=True)
        comprobar_sin_desborde(p, f"página del grupo con desplegable a {ancho}x{alto}")
        p.locator("#lista-candidatas > li").last.scroll_into_view_if_needed()
        caja = p.locator("#lista-candidatas > li").last.bounding_box()
        comprobar(caja["y"] + caja["height"] <= alto + 0.5, f"La última candidata queda cortada a {ancho}x{alto}: {caja}")
        abrir_hoja_grupo(p)
        m = p.evaluate(JS_MEDIDAS_HOJA_GRUPO)
        comprobar(m["fuera"] == [] and m["desbordePanel"] <= 0 and m["panel"]["y"] >= 0 and abs(m["panel"]["b"] - alto) <= 0.5, f"La hoja de edición no cabe a {ancho}x{alto}: {m['panel']} {m['fuera']}")
        p.get_by_role("button", name="Guardar", exact=True).scroll_into_view_if_needed()
        caja = p.get_by_role("button", name="Guardar", exact=True).bounding_box()
        comprobar(caja["y"] + caja["height"] <= alto + 0.5, f"«Guardar» no se alcanza a {ancho}x{alto}: {caja}")
        contexto.close()


# ----------------------------------------------------------------------------
# Página de un grupo: su barra de escribir (con dictado)
# ----------------------------------------------------------------------------

JS_BARRA_EN_GRUPO = (JS_BARRA.replace("'#dictar'", "'#dictar-en-grupo'").replace("'#enviar'", "'#enviar-en-grupo'")
                     .replace("'#campo'", "'#campo-en-grupo'").replace("'#formulario .pildora'", "'#formulario-en-grupo .pildora'"))


def barra_en_grupo(pagina):
    return pagina.evaluate(JS_BARRA_EN_GRUPO)


def esperar_barra_en_grupo(pagina, esperado, que):
    try:
        pagina.wait_for_function(f"(esperado) => JSON.stringify(({JS_BARRA_EN_GRUPO})()) === JSON.stringify(esperado)", arg=esperado, timeout=2500)
    except TiempoAgotado:
        igual(barra_en_grupo(pagina), esperado, que)


def campo_en_grupo(pagina):
    return pagina.locator("#campo-en-grupo")


def pagina_de_grupo_con_dictado(e, datos, ruta, **opciones):
    contexto = e.contexto(**opciones)
    contexto.add_init_script(JS_DICTADO_FALSO)
    pagina = sembrar(e, datos, ruta=ruta, contexto=contexto)
    esperar_vista(pagina, "grupo")
    return pagina


@prueba("página de grupo: bajo «Añadir cosas», la barra de escribir de inicio (campo, micrófono y Guardar) pegada abajo; lo escrito entra en el grupo, arriba, con «Guardado»; vacío no guarda; tocar el campo cierra el desplegable; lo que queda a medias no pasa a otro grupo")
def t_grupo_escribir(e):
    datos = estado_con([cosa(3, "Dentro", grupo="g-5"), cosa(2, "Hecha dentro", hecha=True, grupo="g-5"), cosa(1, "Fuera")],
                       grupos=[grupo(5, "Casa"), grupo(4, "Otro")])
    p = pagina_de_grupo_con_dictado(e, datos, "#grupo/g-5")
    esperar_barra_en_grupo(p, BARRA_EN_REPOSO, "Barra de escribir del grupo en reposo")
    m = barra_anadir(p)
    anadir, escribir = m["pildora"], m["escribir"]
    comprobar(0 <= e.alto - escribir["b"] <= 48 and abs(escribir["alto"] - 56) <= 0.5, f"La barra de escribir no está pegada abajo: {escribir}")
    comprobar(0 <= escribir["y"] - anadir["b"] <= 16, f"«Añadir cosas» no queda justo encima de la barra de escribir: {anadir} / {escribir}")
    comprobar(abs(anadir["x"] - escribir["x"]) <= 0.5 and abs(anadir["d"] - escribir["d"]) <= 0.5, f"Las dos píldoras no tienen el mismo ancho: {anadir} / {escribir}")
    # La misma píldora que la de la pantalla principal (forma, relleno, letra del campo y hueco de los botones).
    aspecto = """(sel) => { const el = document.querySelector(sel), cs = getComputedStyle(el), r = getComputedStyle(el.querySelector('.ranura'));
        return [cs.borderTopLeftRadius, cs.height, cs.paddingLeft, cs.paddingRight, getComputedStyle(el.querySelector('.campo')).fontSize, r.width, r.height]; }"""
    igual(p.evaluate(aspecto, "#formulario-en-grupo .pildora"), p.evaluate(aspecto, "#formulario .pildora"), "La barra de escribir del grupo no es como la de inicio")
    igual(campo_en_grupo(p).evaluate("(c) => [c.getAttribute('aria-label'), c.placeholder, c.maxLength, c.getAttribute('enterkeyhint'), c.closest('form').id]"),
          ["Escribe una cosa en este grupo", "Escribe una cosa…", 500, "done", "formulario-en-grupo"], "Atributos del campo del grupo")
    # Tabulador: tras «Añadir cosas», el campo y el hueco.
    boton_anadir(p).focus()
    for esperado in ("Escribe una cosa en este grupo", "Dictar"):
        p.keyboard.press("Tab")
        igual(p.evaluate("() => document.activeElement.getAttribute('aria-label')"), esperado, "Orden de tabulación tras «Añadir cosas»")
    # Solo espacios: no guarda nada.
    campo_en_grupo(p).fill("   ")
    campo_en_grupo(p).press("Enter")
    p.wait_for_timeout(200)
    igual(pagina_grupo(p)["miembros"], ["Dentro", "Hecha dentro"], "Solo espacios guarda algo")
    # Con Intro: entra en el grupo (arriba, antes que las hechas) con «Guardado», y el campo se vacía.
    campo_en_grupo(p).fill("  Comprar bombillas ")
    esperar_barra_en_grupo(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="  Comprar bombillas ", focoEnCampo=True), "Barra del grupo con texto")
    campo_en_grupo(p).press("Enter")
    comprobar(esperar_toast(p, "Guardado")["icono"], "«Guardado» sin icono")
    esperar_pagina_grupo(p, "tras escribir en el grupo", miembros=["Comprar bombillas", "Dentro", "Hecha dentro"], vacio=False)
    nueva = next(c for c in leer_estado(p)["cosas"] if c["texto"] == "Comprar bombillas")
    igual((nueva["grupo"], nueva["hecha"], nueva["hechaEn"]), ("g-5", False, None), "La cosa escrita en el grupo")
    esperar_barra_en_grupo(p, BARRA_EN_REPOSO, "Barra del grupo tras guardar")
    # Con el botón Guardar.
    p.wait_for_timeout(400)
    campo_en_grupo(p).fill("Cambiar la bombilla")
    p.wait_for_timeout(250)  # Fin del cruce entre el micrófono y Guardar.
    p.locator("#enviar-en-grupo").tap()
    esperar_pagina_grupo(p, "tras guardar con el botón", miembros=["Cambiar la bombilla", "Comprar bombillas", "Dentro", "Hecha dentro"])
    # Lo escrito aquí no se ofrece en el desplegable (ya está dentro); tocar el campo lo cierra y el foco se queda en él.
    p.wait_for_timeout(400)
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable abierto", desplegable=True, candidatas=["Fuera"])
    p.wait_for_timeout(400)
    campo_en_grupo(p).tap()
    esperar_pagina_grupo(p, "desplegable cerrado al tocar el campo", desplegable=False, foco="campo-en-grupo")
    # Lo que queda a medias no pasa a otro grupo.
    campo_en_grupo(p).fill("A medias")
    p.go_back()
    esperar_vista(p, "lista")
    igual(campo_en_grupo(p).input_value(), "", "Lo escrito a medias sigue en el campo del grupo al salir")
    igual(textos_anidados(p, "Casa"), ["Cambiar la bombilla", "Comprar bombillas", "Dentro", "Hecha dentro"], "Lo escrito en el grupo no está dentro de él en la lista")
    p.wait_for_timeout(400)
    abrir_grupo(p, "Otro")
    esperar_barra_en_grupo(p, BARRA_EN_REPOSO, "Barra del otro grupo")
    comprobar("A medias" not in cosas_guardadas(p), "Se ha guardado lo escrito a medias")


@prueba("página de grupo: se dicta en su barra como en la de inicio (micrófono, «Te escucho…», texto, Guardar) y lo dictado entra en el grupo; al salir de la página el dictado se corta")
def t_grupo_dictado(e):
    p = pagina_de_grupo_con_dictado(e, estado_con([cosa(1, "Fuera")], grupos=[grupo(5, "Casa")]), "#grupo/g-5")
    esperar_barra_en_grupo(p, BARRA_EN_REPOSO, "Barra del grupo en reposo")
    p.locator("#dictar-en-grupo").tap()
    igual([r["llamadas"] for r in reconocimientos(p)], [["start"]], "Reconocimiento al tocar el micrófono del grupo")
    igual(reconocimientos(p)[0]["lang"], "es-ES", "Idioma del dictado en el grupo")
    esperar_barra_en_grupo(p, BARRA_ESCUCHANDO, "Barra del grupo escuchando")
    igual(barra(p)["pulsado"], "false", "El micrófono de inicio también se marca como escuchando")
    comprobar(p.evaluate("() => !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)"), "Al dictar el foco está en un campo de texto (se abriría el teclado)")
    p.wait_for_timeout(400)
    dictar(p, "regar las plantas")
    esperar_barra_en_grupo(p, dict(BARRA_ESCUCHANDO, valor="Regar las plantas"), "Texto provisional en el campo del grupo")
    igual(campo_barra(p).input_value(), "", "Lo dictado en el grupo se escribe en el campo de inicio")
    dictar(p, "regar las plantas del balcón", final=True)
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra_en_grupo(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Regar las plantas del balcón"), "Barra del grupo al terminar de dictar")
    p.wait_for_timeout(600)
    igual(pagina_grupo(p)["miembros"], [], "Lo dictado se guarda sin confirmar")
    p.locator("#enviar-en-grupo").tap()
    esperar_toast(p, "Guardado")
    esperar_pagina_grupo(p, "tras guardar lo dictado", miembros=["Regar las plantas del balcón"])
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Regar las plantas del balcón"), "g-5", "Lo dictado no entra en el grupo")
    # Salir de la página mientras escucha corta el dictado: nada sigue escuchando ni escribiendo.
    p.wait_for_timeout(400)
    p.locator("#dictar-en-grupo").tap()
    p.wait_for_timeout(400)
    dictar(p, "a medias")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual(reconocimientos(p)[1]["llamadas"], ["start", "abort"], "Al salir de la página el dictado no se corta")
    igual(reconocimientos(p)[1]["oyentes"], 0, "Oyentes vivos tras salir de la página")
    igual(campo_en_grupo(p).input_value(), "", "Lo dictado a medias se queda en el campo del grupo")
    igual(cosas_guardadas(p), ["Regar las plantas del balcón", "Fuera"], "Cosas guardadas tras salir dictando")


# ----------------------------------------------------------------------------
# Nube: con sesión, tus cosas en todos tus dispositivos (nube.js)
# ----------------------------------------------------------------------------

# La nube solo se carga en la app publicada (https): las pruebas la sirven en un origen https de mentira,
# con una configuración y un Firebase de mentira (módulos que hablan con NubeFalsa, en este proceso).
ORIGEN_NUBE = "https://cosas.test/"
CDN_FIREBASE = "https://www.gstatic.com/firebasejs/"
CONFIG_FALSA = "window.COSAS_FIREBASE = { apiKey: 'clave-de-prueba', authDomain: 'cosas.test', projectId: 'cosas-prueba', appId: '1:1:web:1' };"
TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml"}

FIREBASE_FALSO = {
    "firebase-app.js": r"""
export function initializeApp(config, nombre) { window.__appFalsa = { config, nombre: nombre || '[DEFAULT]' }; return window.__appFalsa; }
""",
    "firebase-auth.js": r"""
export const indexedDBLocalPersistence = { tipo: 'indexedDB' };
export const browserLocalPersistence = { tipo: 'local' };
export function initializeAuth(app, opciones) { window.__authFalsa = { app, persistencias: opciones.persistence.map((p) => p.tipo) }; return window.__authFalsa; }
export function onAuthStateChanged(auth, oyente) {
  const avisar = () => oyente(window.__usuarioFalso || null);
  window.__cambiarUsuarioFalso = (usuario) => { window.__usuarioFalso = usuario; avisar(); };
  setTimeout(avisar, 0);
  return () => {};
}
""",
    "firebase-firestore.js": r"""
const llamar = (op, datos) => window.__nubeFalsa(JSON.stringify({ op, ...datos })).then((r) => JSON.parse(r));
export function memoryLocalCache() { return { tipo: 'memoria' }; }
export function initializeFirestore(app, opciones) { window.__cacheFalsa = opciones.localCache.tipo; return { app }; }
export function collection(db, ...partes) { return { ruta: partes.join('/') }; }
export function doc(coleccion, id) { return { ruta: coleccion.ruta, id }; }
export function onSnapshot(coleccion, opciones, siguiente, error) {
  let version = -1;
  let vivo = true;
  const mirar = async () => {
    if (!vivo) return;
    try {
      const r = await llamar('leer', { ruta: coleccion.ruta });
      if (r.error) { vivo = false; error(new Error(r.error)); return; }
      if (vivo && r.version !== version) {
        version = r.version;
        siguiente({ metadata: { fromCache: false, hasPendingWrites: false }, docs: r.docs.map((d) => ({ id: d.id, data: () => d.datos })) });
      }
    } catch (e) { /* La página se está cerrando. */ }
    if (vivo) setTimeout(mirar, 80);
  };
  // Como el SDK de verdad: antes de oír al servidor, una instantánea de la caché (vacía en memoria).
  setTimeout(() => { if (vivo) siguiente({ metadata: { fromCache: true, hasPendingWrites: false }, docs: [] }); }, 0);
  setTimeout(mirar, 30);
  return () => { vivo = false; };
}
export function writeBatch(db) {
  const operaciones = [];
  return {
    set(ref, datos) { operaciones.push({ ruta: ref.ruta, id: ref.id, datos }); },
    delete(ref) { operaciones.push({ ruta: ref.ruta, id: ref.id, datos: null }); },
    commit() {
      return llamar('escribir', { operaciones }).then((r) => {
        if (r.retener) return new Promise(() => {}); // Sin conexión: nunca llega.
        if (r.error) throw new Error(r.error);
      });
    },
  };
}
""",
}


class NubeFalsa:
    """Firestore de mentira: colecciones en memoria, compartidas por todos los «dispositivos» (contextos) de una prueba."""

    def __init__(self):
        self.colecciones = {}
        self.version = 0
        self.escrituras = 0  # Lotes recibidos (también los retenidos y los rechazados).
        self.retener = False  # Los lotes no llegan (la app se queda sin conexión y se cierra).
        self.rechazar = False  # Los lotes se rechazan (como unas reglas que no los dejan pasar).

    def atender(self, bruto):
        peticion = json.loads(bruto)
        if peticion["op"] == "leer":
            documentos = self.colecciones.get(peticion["ruta"], {})
            return json.dumps({"version": self.version, "docs": [{"id": k, "datos": v} for k, v in sorted(documentos.items())]})
        self.escrituras += 1
        if self.retener:
            return json.dumps({"retener": True})
        if self.rechazar:
            return json.dumps({"error": "permission-denied"})
        for operacion in peticion["operaciones"]:
            documentos = self.colecciones.setdefault(operacion["ruta"], {})
            if operacion["datos"] is None:
                documentos.pop(operacion["id"], None)
            else:
                documentos[operacion["id"]] = operacion["datos"]
        self.version += 1
        return json.dumps({})

    def documentos(self, uid="u1"):
        return dict(self.colecciones.get(f"personales/{uid}/elementos", {}))


def servir_app_en_la_nube(route):
    ruta = re.sub(r"^https://[^/]+/", "", route.request.url).split("?")[0].split("#")[0] or "index.html"
    if ruta == "firebase-config.js":
        route.fulfill(status=200, content_type=TIPOS[".js"], body=CONFIG_FALSA)
        return
    fichero = APP / ruta
    if not fichero.is_file():
        route.fulfill(status=404, body="")
        return
    route.fulfill(status=200, content_type=TIPOS.get(fichero.suffix, "application/octet-stream"), body=fichero.read_bytes())


def servir_firebase_falso(route):
    nombre = route.request.url.split("?")[0].rsplit("/", 1)[-1]
    route.fulfill(status=200, content_type=TIPOS[".js"], body=FIREBASE_FALSO[nombre])


def dispositivo(e, nube, usuario="u1"):
    """Un contexto (un dispositivo) con la app en https://cosas.test/ y el Firebase de mentira; «usuario» es su sesión de Google."""
    for origen in (ORIGEN_NUBE, CDN_FIREBASE):
        if origen not in e.origenes_propios:
            e.origenes_propios.append(origen)
    # El origen de mentira no tiene service worker (lo sirve Playwright): la app avisa de que no se ha podido registrar.
    e.tolerar.append(r"^Service Worker registration blocked by Playwright$")
    contexto = e.contexto(service_workers="block")
    contexto.expose_function("__nubeFalsa", nube.atender)
    if usuario:
        contexto.add_init_script(f"window.__usuarioFalso = {json.dumps({'uid': usuario, 'isAnonymous': False})};")
    contexto.route(ORIGEN_NUBE + "**", servir_app_en_la_nube)
    contexto.route(CDN_FIREBASE + "**", servir_firebase_falso)
    return contexto


def abrir_en_la_nube(contexto, e, datos=None, ruta="", cuenta=True):
    """Abre la app en ese dispositivo; con «datos», lo que ya tenía apuntado; con «cuenta», la sesión que dejó cuenta/."""
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(ORIGEN_NUBE + "icons/favicon-32.png")
    if datos is not None:
        pagina.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE, json.dumps(datos, ensure_ascii=False)])
    if cuenta:
        pagina.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE_CUENTA, json.dumps({"nombre": "Raúl", "correo": "raul@example.com"})])
    pagina.goto(ORIGEN_NUBE + ruta)
    return pagina


def esperar_nube(pagina, estado, ms=5000):
    esperar(pagina, "(estado) => document.documentElement.dataset.nube === estado", arg=estado, ms=ms, que=f"la nube pasa a «{estado}»")


def esperar_guardado(pagina, condicion, que, ms=5000):
    """Espera a que lo guardado en ese dispositivo cumpla «condicion» (JS sobre el estado)."""
    esperar(pagina, f"(clave) => {{ const s = JSON.parse(localStorage.getItem(clave)); return ({condicion})(s); }}", arg=CLAVE, ms=ms, que=que)


def esperar_en_la_nube(pagina, nube, condicion, que, ms=5000):
    fin = time.monotonic() + ms / 1000
    while not condicion(nube.documentos()):
        if time.monotonic() > fin:
            raise Fallo(f"No se cumplió en {ms} ms: {que} (en la nube: {sorted(nube.documentos())})")
        pagina.wait_for_timeout(50)  # Con Playwright esperando, las llamadas a la nube falsa se atienden.


@prueba("nube: reconciliación documento a documento (la primera vez se juntan, bajas y cambios de cada lado, si cambian los dos gana este dispositivo, los ajustes de la cuenta mandan la primera vez, lo que se está subiendo no se toca)", una_vez=True)
def t_nube_reconciliar(e):
    p = e.pagina("icons/favicon-32.png")
    r = p.evaluate("""async () => {
      const n = await import('/nube.js');
      const M = (o) => new Map(Object.entries(o));
      const O = (m) => Object.fromEntries(m);
      const c = (id, texto, extra = {}) => ({ tipo: 'cosa', id, texto, hecha: false, creada: 1, hechaEn: null, grupo: null, ...extra });
      const aj = (colorFondo, nombre = '') => ({ tipo: 'ajustes', colorFondo, nombre });
      const caso = (local, base, nube, ocupados = []) => {
        const r = n.reconciliar(M(local), M(base), M(nube), new Set(ocupados));
        return { escribir: O(r.escribir), aplicar: O(r.aplicar), base: O(r.base) };
      };
      return {
        union: caso({ 'c-a': c('a', 'A'), ajustes: aj('#111111') }, {}, { 'c-b': c('b', 'B'), ajustes: aj('#EF5B5B', 'Raúl') }),
        bajaEnLaNube: caso({ 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A') }, {}),
        bajaAqui: caso({}, { 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A') }),
        cambioEnLaNube: caso({ 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A', { hecha: true, hechaEn: 5 }) }),
        cambioAqui: caso({ 'c-a': c('a', 'A2') }, { 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A') }),
        ambos: caso({ 'c-a': c('a', 'Aquí') }, { 'c-a': c('a', 'A') }, { 'c-a': c('a', 'Allí') }),
        borradaAllíCambiadaAquí: caso({ 'c-a': c('a', 'A2') }, { 'c-a': c('a', 'A') }, {}),
        ajustesConBase: caso({ ajustes: aj('#111111') }, { ajustes: aj('#2F6FED') }, { ajustes: aj('#EF5B5B') }),
        ocupado: caso({ 'c-a': c('a', 'A2') }, { 'c-a': c('a', 'A') }, { 'c-a': c('a', 'A3') }, ['c-a']),
        iguales: caso({ 'c-a': c('a', 'A') }, {}, { 'c-a': c('a', 'A') }),
        claves: [n.claveDe('cosa', 'x/y.z'), n.claveDe('grupo', 'g-1'), n.claveDe('cosa', ''), n.claveDe('cosa', 'a'.repeat(201)), n.claveDe('cosa', 7)],
        elementos: O(n.elementosDe({ cosas: [{ id: 'a', texto: 'A', hecha: true, creada: 1, hechaEn: 2, grupo: 'g' }, { id: '', texto: 'Sin id' }],
                                     grupos: [{ id: 'g', nombre: 'G', color: null, creada: 3, abierto: true }], ajustes: { colorFondo: '#2F6FED', nombre: 'R' } })),
        aplicado: n.estadoCon({ version: 2, cosas: [{ id: 'a', texto: 'A' }, { id: 'b', texto: 'B' }], grupos: [{ id: 'g', nombre: 'G', color: null, creada: 3, abierto: true }],
                                ajustes: { colorFondo: '#2F6FED', nombre: '' } },
                              new Map([['c-a', null], ['c-c', c('c', 'C')], ['g-g', { tipo: 'grupo', id: 'g', nombre: 'G2', color: '#EF5B5B', creada: 3 }],
                                       ['g-h', { tipo: 'grupo', id: 'h', nombre: 'H', color: null, creada: 4 }], ['ajustes', aj('#111111', 'R')]])),
        raros: [n.deLaNube('c-a', { tipo: 'cosa', id: 'b', texto: 'B', hecha: false, creada: 1 }), n.deLaNube('c-a', { tipo: 'cosa', id: 'a', texto: 5, hecha: false, creada: 1 }),
                n.deLaNube('ajustes', null), n.deLaNube('g-g', { tipo: 'cosa', id: 'g', texto: 'x', hecha: false, creada: 1 }), n.deLaNube('c-a', { tipo: 'cosa', id: 'a', texto: 'A', hecha: 'no', creada: 1 })],
        completada: n.deLaNube('c-a', { tipo: 'cosa', id: 'a', texto: 'A', hecha: false, creada: 1, extra: 1 }),
      };
    }""")

    def c(id, texto, **extra):
        return {"tipo": "cosa", "id": id, "texto": texto, "hecha": False, "creada": 1, "hechaEn": None, "grupo": None, **extra}

    def aj(color, nombre=""):
        return {"tipo": "ajustes", "colorFondo": color, "nombre": nombre}

    vacio = {"escribir": {}, "aplicar": {}}
    igual(r["union"], {"escribir": {"c-a": c("a", "A")}, "aplicar": {"c-b": c("b", "B"), "ajustes": aj("#EF5B5B", "Raúl")},
                       "base": {"c-b": c("b", "B"), "ajustes": aj("#EF5B5B", "Raúl")}}, "Primera vez: se juntan y mandan los ajustes de la cuenta")
    igual(r["bajaEnLaNube"], {"escribir": {}, "aplicar": {"c-a": None}, "base": {}}, "Borrada en otro dispositivo")
    igual(r["bajaAqui"], {"escribir": {"c-a": None}, "aplicar": {}, "base": {"c-a": c("a", "A")}}, "Borrada aquí")
    igual(r["cambioEnLaNube"], {"escribir": {}, "aplicar": {"c-a": c("a", "A", hecha=True, hechaEn=5)}, "base": {"c-a": c("a", "A", hecha=True, hechaEn=5)}}, "Cambiada en otro dispositivo")
    igual(r["cambioAqui"], {"escribir": {"c-a": c("a", "A2")}, "aplicar": {}, "base": {"c-a": c("a", "A")}}, "Cambiada aquí")
    igual(r["ambos"], {"escribir": {"c-a": c("a", "Aquí")}, "aplicar": {}, "base": {"c-a": c("a", "A")}}, "Cambiada en los dos: gana este dispositivo")
    igual(r["borradaAllíCambiadaAquí"], {"escribir": {"c-a": c("a", "A2")}, "aplicar": {}, "base": {"c-a": c("a", "A")}}, "Borrada allí y cambiada aquí: se conserva")
    igual(r["ajustesConBase"], {"escribir": {"ajustes": aj("#111111")}, "aplicar": {}, "base": {"ajustes": aj("#2F6FED")}}, "Ajustes cambiados en los dos (ya con base): gana este dispositivo")
    igual(r["ocupado"], dict(vacio, base={"c-a": c("a", "A")}), "Lo que se está subiendo no se toca")
    igual(r["iguales"], dict(vacio, base={"c-a": c("a", "A")}), "Iguales en los dos: solo avanza la base")
    igual(r["claves"], ["c-x%2Fy.z", "g-g-1", None, None, None], "Ids de documento")
    igual(r["elementos"], {"c-a": c("a", "A", hecha=True, hechaEn=2, grupo="g"), "g-g": {"tipo": "grupo", "id": "g", "nombre": "G", "color": None, "creada": 3},
                           "ajustes": aj("#2F6FED", "R")}, "Lo local como documentos (el ojo de un grupo no viaja; una cosa sin id no se sube)")
    igual(r["aplicado"], {"version": 2, "cosas": [{"id": "b", "texto": "B"}, {"id": "c", "texto": "C", "hecha": False, "creada": 1, "hechaEn": None, "grupo": None}],
                          "grupos": [{"id": "g", "nombre": "G2", "color": "#EF5B5B", "creada": 3, "abierto": True}, {"id": "h", "nombre": "H", "color": None, "creada": 4, "abierto": False}],
                          "ajustes": {"colorFondo": "#111111", "nombre": "R"}}, "Estado con lo de la nube aplicado (el ojo abierto se conserva)")
    igual(r["raros"], [None] * 5, "Documentos de la nube que no tienen la forma esperada")
    igual(r["completada"], c("a", "A"), "Un documento con campos de más se queda con los suyos")


@prueba("nube: con sesión, dos dispositivos juntan lo que tenían y se pasan los cambios (altas, bajas, grupos, lo escrito en un grupo, color); lo pendiente al cerrar se sube al volver y lo borrado mientras tanto no resucita; un rechazo no se reintenta sin fin; al cerrar la sesión se para; sin sesión, ni se carga")
def t_nube_dos_dispositivos(e):
    nube = NubeFalsa()
    # A tenía cosas antes de entrar: un grupo abierto, una hecha, su color y su nombre.
    contexto_a = dispositivo(e, nube)
    a = abrir_en_la_nube(contexto_a, e, estado_con([cosa(3, "Pan", grupo="g-casa"), cosa(2, "Luz", hecha=True), cosa(1, "Agua")],
                                                   grupos=[grupo(9, "Casa", id="g-casa", abierto=True)], color="#EF5B5B", nombre="Raúl"), ruta="#lista")
    esperar_nube(a, "al-dia")
    igual(a.evaluate("() => [window.__appFalsa.nombre, window.__authFalsa.persistencias, window.__cacheFalsa]"), ["[DEFAULT]", ["indexedDB", "local"], "memoria"],
          "Firebase: la app por defecto (la sesión de cosas.info), persistencia local y caché en memoria")
    docs = nube.documentos()
    igual(sorted(docs), ["ajustes", "c-id-1", "c-id-2", "c-id-3", "g-g-casa"], "Documentos en la nube tras entrar con A")
    igual(docs["ajustes"], {"tipo": "ajustes", "colorFondo": "#EF5B5B", "nombre": "Raúl"}, "Ajustes en la nube")
    igual(docs["g-g-casa"], {"tipo": "grupo", "id": "g-casa", "nombre": "Casa", "color": None, "creada": 1_700_000_000_009}, "Grupo en la nube (sin el ojo, que es de cada dispositivo)")
    igual((docs["c-id-3"]["grupo"], docs["c-id-2"]["hecha"]), ("g-casa", True), "Pertenencia y hecha en la nube")
    # B, otro dispositivo con la misma cuenta, tenía una cosa suya y el color de siempre: se juntan y mandan los ajustes de la cuenta.
    contexto_b = dispositivo(e, nube)
    b = abrir_en_la_nube(contexto_b, e, estado_con([cosa(5, "Llamar")]))
    esperar_nube(b, "al-dia")
    esperar_guardado(b, "(s) => s.cosas.map((c) => c.texto).sort().join() === 'Agua,Llamar,Luz,Pan'", "B tiene las cosas de los dos")
    esperar_fondo(b, "#EF5B5B")
    igual(leer_estado(b)["ajustes"], {"colorFondo": "#EF5B5B", "nombre": "Raúl"}, "B toma los ajustes de la cuenta")
    igual([(g["nombre"], g["abierto"]) for g in leer_estado(b)["grupos"]], [("Casa", False)], "B tiene el grupo (cerrado: el ojo es de cada dispositivo)")
    esperar_en_la_nube(b, nube, lambda d: "c-id-5" in d, "la cosa de B llega a la nube")
    # A la ve en su lista, sin recargar.
    esperar_nivel(a, ["Casa", "Llamar", "Agua", "Luz"], "A recibe la cosa de B en la lista abierta")
    igual(leer_estado(a)["grupos"][0]["abierto"], True, "El ojo abierto de A se ha cerrado")
    # A borra «Agua» y desaparece en B.
    borrar_de(fila_de(a, "Agua")).tap()
    esperar_guardado(b, "(s) => !s.cosas.some((c) => c.texto === 'Agua')", "«Agua» desaparece en B")
    # B escribe en la página del grupo y A lo ve dentro del grupo.
    abrir_lista(b)
    b.wait_for_timeout(400)  # Pasa la guarda del doble toque tras cambiar de vista.
    abrir_grupo(b, "Casa")
    campo_en_grupo(b).fill("Bombillas")
    campo_en_grupo(b).press("Enter")
    esperar_toast(b, "Guardado")
    esperar_anidadas(a, "Casa", ["Bombillas", "Pan"])
    # A cambia el color y B, en la página del grupo (sin color propio), lo ve.
    a.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(a, "inicio")
    abrir_ajustes(a)
    igual(a.locator("#sesion").text_content().strip(), "Cerrar sesión", "Con la sesión, el botón es «Cerrar sesión»")
    elegir_color(a, "Turquesa")
    esperar_fondo(b, "#1BA39C")
    esperar(b, "() => getComputedStyle(document.querySelector('#vista-grupo')).backgroundColor === 'rgb(27, 163, 156)'", que="la página del grupo en B toma el color nuevo")
    # B se queda sin conexión: quita «Pan» del grupo, pero no llega a la nube antes de cerrarse.
    nube.retener = True
    quitar_de(b, "Pan").tap()
    esperar_toast(b, "Quitado del grupo")
    b.wait_for_timeout(800)
    b.close()
    nube.retener = False
    igual(nube.documentos()["c-id-3"]["grupo"], "g-casa", "Lo que no llegó está en la nube")
    # Mientras B está cerrado, A borra «Luz»; al volver, B sube lo pendiente y no resucita «Luz».
    volver(a)
    abrir_lista(a)
    borrar_de(fila_de(a, "Luz")).tap()
    esperar_en_la_nube(a, nube, lambda d: "c-id-2" not in d, "«Luz» se borra de la nube")
    b = abrir_en_la_nube(contexto_b, e, ruta="#lista", cuenta=False)
    esperar_nube(b, "al-dia")
    esperar_en_la_nube(b, nube, lambda d: d["c-id-3"]["grupo"] is None, "lo pendiente de B llega a la nube al volver")
    esperar_guardado(b, "(s) => !s.cosas.some((c) => c.texto === 'Luz')", "B borra «Luz» (borrada en A mientras estaba cerrado)")
    b.wait_for_timeout(600)
    comprobar("c-id-2" not in nube.documentos(), "«Luz» ha resucitado en la nube")
    esperar_anidadas(a, "Casa", ["Bombillas"])
    igual(sorted(c["texto"] for c in leer_estado(a)["cosas"]), sorted(c["texto"] for c in leer_estado(b)["cosas"]), "A y B no acaban con las mismas cosas")
    # Si la nube rechaza una subida, se marca el error y no se reintenta en bucle.
    nube.rechazar = True
    volver(b)
    anotar(b, "Rechazada")
    esperar_nube(b, "error")
    antes = nube.escrituras
    b.wait_for_timeout(1500)
    igual(nube.escrituras, antes, "Tras un rechazo la app reintenta sin parar")
    nube.rechazar = False
    # Al cerrar la sesión (en otra pestaña, Tu cuenta) la sincronización se para; lo local se queda.
    a.evaluate("() => window.__cambiarUsuarioFalso(null)")
    esperar(a, "() => !document.documentElement.hasAttribute('data-nube')", que="sin sesión, A deja de sincronizar")
    volver(a)
    anotar(a, "Solo en A")
    esperar_toast(a, "Guardado")
    a.wait_for_timeout(800)
    comprobar(all(d.get("texto") != "Solo en A" for d in nube.documentos().values()), "Sin sesión, lo apuntado sube a la nube")
    # Sin cuenta (cosascon:cuenta), la app ni siquiera pide nube.js ni Firebase.
    contexto_c = dispositivo(e, nube)
    peticiones = []
    contexto_c.on("request", lambda peticion: peticiones.append(peticion.url))
    c = abrir_en_la_nube(contexto_c, e, estado_con([cosa(1, "Sin cuenta")]), cuenta=False)
    esperar_vista(c, "inicio")
    c.wait_for_timeout(800)
    igual([u for u in peticiones if "nube.js" in u or u.startswith(CDN_FIREBASE) or "firebase-config" in u], [], "Sin sesión se carga la nube")
    comprobar(not c.evaluate("() => document.documentElement.hasAttribute('data-nube')"), "Sin sesión hay estado de nube")


# ----------------------------------------------------------------------------
# Comprobaciones estáticas (una sola vez)
# ----------------------------------------------------------------------------

@prueba("estático: node --check, 'use strict', hover solo con puntero, áreas seguras", una_vez=True)
def t_estatico(e):
    comprobar(NODE.is_file(), f"No se encuentra Node en {NODE}")
    for nombre in ("app.js", "sw.js"):
        resultado = subprocess.run([str(NODE), "--check", str(APP / nombre)], capture_output=True, text=True, encoding="utf-8")
        igual(resultado.returncode, 0, f"node --check {nombre}: {resultado.stderr.strip()}")
        fuente = (APP / nombre).read_text(encoding="utf-8")
        comprobar(re.search(r"^\s*'use strict';", fuente, re.M), f"{nombre} sin 'use strict'")
        fuente = re.sub(r"/\*.*?\*/", "", fuente, flags=re.S)  # Los comentarios no cuentan.
        fuente = re.sub(r"(?m)(^|\s)//[^\n]*", "", fuente)
        comprobar(not re.search(r"\bconsole\.(log|debug|info|warn|error)\b", fuente), f"{nombre} escribe en la consola")
        comprobar("innerHTML" not in fuente and "insertAdjacentHTML" not in fuente and "document.write" not in fuente, f"{nombre} usa innerHTML o similar")
        comprobar(not re.search(r"https?://", fuente), f"{nombre} contiene URLs externas")
    # nube.js es un módulo (se comprueba como tal) y solo carga Firebase de su CDN.
    with tempfile.TemporaryDirectory() as carpeta:
        copia = Path(carpeta) / "nube.mjs"
        copia.write_bytes((APP / "nube.js").read_bytes())
        resultado = subprocess.run([str(NODE), "--check", str(copia)], capture_output=True, text=True, encoding="utf-8")
        igual(resultado.returncode, 0, f"node --check nube.js: {resultado.stderr.strip()}")
    fuente = re.sub(r"/\*.*?\*/", "", (APP / "nube.js").read_text(encoding="utf-8"), flags=re.S)
    fuente = re.sub(r"(?m)(^|\s)//[^\n]*", "", fuente)
    comprobar(not re.search(r"\bconsole\.(log|debug|info|warn|error)\b", fuente), "nube.js escribe en la consola")
    comprobar("innerHTML" not in fuente and "insertAdjacentHTML" not in fuente and "document.write" not in fuente, "nube.js usa innerHTML o similar")
    igual(sorted(set(re.findall(r"https?://[^\s'\"`$]+", fuente))), ["https://www.gstatic.com/firebasejs/"], "URLs externas de nube.js")
    css = (APP / "styles.css").read_text(encoding="utf-8")
    sin_bloques, posicion = "", 0
    for coincidencia in re.finditer(r"@media\s*\(hover:\s*hover\)\s*\{", css):
        sin_bloques += css[posicion:coincidencia.start()]
        nivel, i = 1, coincidencia.end()
        while nivel and i < len(css):
            nivel += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        posicion = i
    sin_bloques += css[posicion:]
    comprobar(":hover" not in sin_bloques, "Hay reglas :hover fuera de @media (hover: hover)")
    for trozo in ("env(safe-area-inset-top", "env(safe-area-inset-bottom", "env(safe-area-inset-left", "env(safe-area-inset-right",
                  "100dvh", "100vh", "prefers-reduced-motion", ":focus-visible", "-webkit-overflow-scrolling"):
        comprobar(trozo in css, f"styles.css no contiene «{trozo}»")
    comprobar(css.index("100vh") < css.index("100dvh"), "100vh debe ir antes que 100dvh (alternativa)")
    html = (APP / "index.html").read_text(encoding="utf-8")
    comprobar(not re.search(r"<[^>]+\son[a-z]+\s*=", html), "index.html tiene manejadores de eventos en línea")
    # Los <a> son navegación (los accesos a cosas.info), no recursos que se carguen: no cuentan.
    sin_enlaces = re.sub(r"<a\s[^>]*>", "", html)
    comprobar(not re.search(r"(src|href)\s*=\s*[\"'](https?:)?//", sin_enlaces), "index.html carga recursos externos")
    # Los accesos son de este dominio (netlify.toml los trae de cosas.info); sin ese proxy, van a data-web.
    externos = [d for d in re.findall(r"<a\s[^>]*href\s*=\s*[\"']([^\"']+)", html) if re.match(r"https?:", d)]
    comprobar(all(d.startswith("https://cosas.info/") for d in externos), f"Los enlaces externos de index.html deben ir a cosas.info: {externos}")
    comprobar('data-web="https://cosas.info/"' in html, "Sin proxy, los accesos no van a cosas.info (data-web de #accesos)")
    comprobar("..." not in re.sub(r"<script.*?</script>", "", html, flags=re.S), "index.html usa tres puntos en vez de «…»")
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    cabecera = "\n".join(sw.splitlines()[:12])
    comprobar(re.search(r"const\s+\w+\s*=\s*'[^']*\d[^']*'", cabecera), "sw.js no declara arriba una constante de caché con versión")
    for trozo in ("skipWaiting", "clients.claim", "caches.delete"):
        comprobar(trozo in sw, f"sw.js no usa {trozo}")


@prueba("service worker: el sello de versión corresponde al contenido actual de la app", una_vez=True)
def t_sello_de_version(e):
    sys.path.insert(0, str(RAIZ / "herramientas"))
    try:
        import sellar_version
    finally:
        sys.path.pop(0)
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    precargados = sellar_version.recursos_precargados(sw)
    # La tarjeta de la vista previa del enlace (compartir.png) no es de la app: solo la piden WhatsApp y compañía.
    iconos = [f"icons/{i.name}" for i in (APP / "icons").glob("*.png") if i.name != "compartir.png"]
    igual(sorted(precargados), sorted(["index.html", "styles.css", "app.js", "nube.js", "manifest.webmanifest"] + iconos),
          "Ficheros cubiertos por el sello (los que precarga sw.js)")
    actual, debida = sellar_version.version_actual(sw), sellar_version.version_sellada(sw)
    comprobar(re.fullmatch(r"cosas-v[0-9.]+-[0-9a-f]{8}", actual), f"VERSION_CACHE no lleva sello de contenido: {actual!r}")
    comprobar(actual == debida, f"La app ha cambiado y sw.js sigue con el sello antiguo ({actual}; ahora toca {debida}): "
                                "quien ya la tenga instalada no recibiría los cambios. Ejecuta herramientas/sellar_version.py antes de publicar.")


# ----------------------------------------------------------------------------
# Capturas opcionales
# ----------------------------------------------------------------------------

def hacer_capturas(e, carpeta):
    carpeta.mkdir(parents=True, exist_ok=True)
    prefijo = e.dispositivo.lower().replace(" ", "-")
    contexto = e.contexto()
    p = e.pagina(contexto=contexto)
    p.screenshot(path=str(carpeta / f"{prefijo}_1-inicio.png"))
    anotar(p, "Comprar pan")
    esperar_toast(p, "Guardado")
    esperar(p, f"() => ({JS_TOAST})().opacidad === 1")
    p.screenshot(path=str(carpeta / f"{prefijo}_2-toast-guardado.png"))
    abrir_lista(p)
    borrar_de(filas(p).first).tap()
    esperar_filas(p, 0)
    esperar_sin_toast(p, ms=6000)
    p.wait_for_timeout(300)
    p.screenshot(path=str(carpeta / f"{prefijo}_4-lista-vacia.png"))
    cosas = [cosa(5, "Llamar al dentista"), cosa(4, "Comprar pan y leche", hecha=True), cosa(3, "Enviar el informe trimestral antes del viernes por la tarde"),
             cosa(2, "Regar las plantas", hecha=True), cosa(1, "Renovar el DNI")]
    p2 = sembrar(e, estado_con(cosas, nombre="Raúl"), ruta="#lista", contexto=e.contexto())
    esperar_filas(p2, 5)
    p2.wait_for_timeout(400)
    p2.screenshot(path=str(carpeta / f"{prefijo}_3-lista.png"))
    p2.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p2, "inicio")
    abrir_ajustes(p2)
    p2.wait_for_timeout(400)
    p2.screenshot(path=str(carpeta / f"{prefijo}_5-ajustes.png"))
    p3 = pagina_con_dictado(e)
    boton_micro(p3).tap()
    dictar(p3, "comprar pan y")
    p3.wait_for_timeout(400)
    p3.screenshot(path=str(carpeta / f"{prefijo}_6-dictando.png"))
    p4 = pagina_de_ajustes(e)
    p4.screenshot(path=str(carpeta / f"{prefijo}_7-ajustes-aplicacion.png"))
    abrir_hoja(p4)
    p4.screenshot(path=str(carpeta / f"{prefijo}_8-hoja-de-pasos.png"))
    # 1.2: grupos en la lista, página del grupo, desplegable y hoja de edición sobre Azul, Blanco y Negro.
    for nombre, color in (("azul", "#2F6FED"), ("blanco", "#FFFFFF"), ("negro", "#111111")):
        p5 = sembrar_grupos(e, color=color, contexto=e.contexto())
        esperar_filas(p5, 6)
        ojo_de(fila_grupo(p5, "Casa")).tap()
        p5.wait_for_timeout(500)
        p5.screenshot(path=str(carpeta / f"{prefijo}_9-lista-grupos-{nombre}.png"))
        abrir_grupo(p5, "Casa")
        p5.wait_for_timeout(400)
        p5.screenshot(path=str(carpeta / f"{prefijo}_13-grupo-con-cosas-{nombre}.png"))
        boton_anadir(p5).tap()
        p5.wait_for_timeout(400)
        p5.screenshot(path=str(carpeta / f"{prefijo}_14-grupo-desplegable-{nombre}.png"))
        abrir_hoja_grupo(p5)
        p5.screenshot(path=str(carpeta / f"{prefijo}_15-grupo-editar-{nombre}.png"))
        p5.context.close()
    p6 = sembrar_grupos(e, contexto=e.contexto())
    esperar_filas(p6, 6)
    p6.screenshot(path=str(carpeta / f"{prefijo}_10-crear-grupo-reposo.png"))
    boton_crear_grupo(p6).tap()
    campo_grupo(p6).fill("Vacaciones")
    p6.wait_for_timeout(300)
    p6.screenshot(path=str(carpeta / f"{prefijo}_11-crear-grupo-editando.png"))
    p7 = sembrar(e, estado_con([cosa(1, "Comprar pan")], grupos=[grupo(2, "Vacaciones")]), ruta="#grupo/g-2", contexto=e.contexto())
    esperar_vista(p7, "grupo")
    p7.wait_for_timeout(300)
    p7.screenshot(path=str(carpeta / f"{prefijo}_12-grupo-vacio.png"))
    # 1.2 (barra abajo): el desplegable con muchas candidatas (arriba y al final) y una lista larga desplazada hasta el final.
    p10 = sembrar(e, estado_con([cosa(i, f"Cosa número {i}", hecha=i % 4 == 0) for i in range(1, 61)], grupos=[grupo(100, "Muchas")]), ruta="#grupo/g-100", contexto=e.contexto())
    esperar_vista(p10, "grupo")
    boton_anadir(p10).tap()
    p10.wait_for_timeout(400)
    p10.screenshot(path=str(carpeta / f"{prefijo}_20-grupo-muchas-candidatas.png"))
    p10.evaluate("() => { const c = document.querySelector('#candidatas'); c.scrollTop = c.scrollHeight; }")
    p10.wait_for_timeout(200)
    p10.screenshot(path=str(carpeta / f"{prefijo}_21-grupo-muchas-candidatas-final.png"))
    p11 = sembrar(e, estado_con([cosa(i, f"Cosa número {i}", hecha=i % 4 == 0, grupo="g-100") for i in range(1, 41)], grupos=[grupo(100, "Larga")]), ruta="#grupo/g-100", contexto=e.contexto())
    esperar_vista(p11, "grupo")
    p11.evaluate("() => { const z = document.querySelector('#zona-grupo'); z.scrollTop = z.scrollHeight; }")
    p11.wait_for_timeout(200)
    p11.screenshot(path=str(carpeta / f"{prefijo}_22-grupo-lista-larga-final.png"))
    for nombre, fondo, color in (("claro", "#2F6FED", "#F2B705"), ("oscuro", "#FFFFFF", "#1E2A44")):
        p8 = sembrar(e, estado_con([cosa(4, "Enviar el informe"), cosa(3, "Llamar al dentista"), cosa(2, "Regar las plantas", grupo="g-3"), cosa(1, "Comprar pan", hecha=True, grupo="g-3")], grupos=[grupo(3, "Casa", color=color)], color=fondo), ruta="#grupo/g-3", contexto=e.contexto())
        esperar_vista(p8, "grupo")
        p8.wait_for_timeout(300)
        p8.screenshot(path=str(carpeta / f"{prefijo}_16-grupo-color-{nombre}.png"))
        boton_anadir(p8).tap()
        p8.wait_for_timeout(400)
        p8.screenshot(path=str(carpeta / f"{prefijo}_23-grupo-color-{nombre}-desplegable.png"))
        p8.keyboard.press("Escape")
        p8.wait_for_timeout(300)
        p8.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p8, "lista")
        p8.wait_for_timeout(400)
        p8.screenshot(path=str(carpeta / f"{prefijo}_17-lista-grupo-color-{nombre}.png"))
        p8.context.close()
    for etiqueta, ancho, alto in (("320", 320, 568), ("apaisado", e.alto, e.ancho)):
        p9 = sembrar_grupos(e, contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_filas(p9, 6)
        ojo_de(fila_grupo(p9, "Casa")).tap()
        p9.wait_for_timeout(500)
        p9.screenshot(path=str(carpeta / f"{prefijo}_18-lista-{etiqueta}.png"))
        abrir_grupo(p9, "Casa")
        boton_anadir(p9).tap()
        p9.wait_for_timeout(400)
        p9.screenshot(path=str(carpeta / f"{prefijo}_19-grupo-{etiqueta}.png"))
        p9.context.close()


# ----------------------------------------------------------------------------
# Ejecución
# ----------------------------------------------------------------------------

def ejecutar(argumentos):
    global DETALLE
    DETALLE = argumentos.detalle
    servidor = iniciar_servidor(APP)
    base = url_de(servidor)
    resultados, incidencias = [], []
    dispositivos = [d for d in DISPOSITIVOS if not argumentos.dispositivo or d == argumentos.dispositivo]
    with sync_playwright() as pw:
        navegador = pw.chromium.launch()
        try:
            for indice, dispositivo in enumerate(dispositivos):
                print(f"\n=== {dispositivo} ===")
                for nombre, funcion, una_vez in PRUEBAS:
                    if argumentos.filtro and argumentos.filtro.lower() not in nombre.lower() and argumentos.filtro != funcion.__name__:
                        continue
                    if una_vez and indice > 0:
                        continue
                    etiqueta = "estático" if una_vez else dispositivo
                    entorno = Entorno(pw, navegador, dispositivo, base, nombre, incidencias)
                    inicio = time.monotonic()
                    try:
                        funcion(entorno)
                        error = None
                    except Fallo as fallo:
                        error = str(fallo)
                    except Exception as excepcion:  # Errores de Playwright, tiempos agotados…
                        error = f"{type(excepcion).__name__}: {excepcion}".strip()
                        if argumentos.detalle:
                            traceback.print_exc()
                    finally:
                        entorno.cerrar()
                    duracion = time.monotonic() - inicio
                    resultados.append((etiqueta, nombre, error))
                    print(f"  [{'PASS' if error is None else 'FAIL'}] {nombre}  ({duracion:.1f} s)")
                    if error:
                        print("         " + error.replace("\n", "\n         "))
                if argumentos.capturas:
                    entorno = Entorno(pw, navegador, dispositivo, base, "capturas", incidencias)
                    try:
                        hacer_capturas(entorno, Path(argumentos.capturas))
                        error = None
                    except Exception as excepcion:
                        error = f"{type(excepcion).__name__}: {excepcion}"
                    finally:
                        entorno.cerrar()
                    resultados.append((dispositivo, "capturas de pantalla", error))
                    print(f"  [{'PASS' if error is None else 'FAIL'}] capturas de pantalla en {argumentos.capturas}")
                    if error:
                        print("         " + error)
                # Consola, errores de página y red de todo lo ejecutado en este dispositivo.
                propias = [i for i in incidencias if i[0] == dispositivo]
                graves = [i for i in propias if i[2] in ("console.error", "pageerror", "requestfailed", "respuesta HTTP", "petición externa")]
                ruido = [i for i in propias if i not in graves]
                for nombre, lista in (("cero errores de consola, errores de página, peticiones fallidas o externas en toda la ejecución", graves),
                                      ("sin ruido en la consola (log/warn/info) en toda la ejecución", ruido)):
                    error = None
                    if lista:
                        error = f"{len(lista)} incidencias:\n" + "\n".join(f"- [{i[1]}] {i[2]}: {i[3]}" for i in lista[:20])
                    resultados.append((dispositivo, nombre, error))
                    print(f"  [{'PASS' if error is None else 'FAIL'}] {nombre}")
                    if error:
                        print("         " + error.replace("\n", "\n         "))
        finally:
            navegador.close()
    servidor.shutdown()
    servidor.server_close()

    fallidas = [r for r in resultados if r[2] is not None]
    print("\n" + "=" * 78)
    print(f"RESUMEN: {len(resultados)} pruebas · {len(resultados) - len(fallidas)} PASS · {len(fallidas)} FAIL")
    for etiqueta, nombre, _ in fallidas:
        print(f"  FAIL [{etiqueta}] {nombre}")
    print("=" * 78)
    return 1 if fallidas else 0


def main():
    analizador = argparse.ArgumentParser(description="Pruebas E2E de Cosas")
    analizador.add_argument("--capturas", metavar="DIR", help="carpeta donde guardar capturas por dispositivo")
    analizador.add_argument("--filtro", help="ejecuta solo las pruebas cuyo nombre contiene este texto")
    analizador.add_argument("--dispositivo", choices=DISPOSITIVOS, help="ejecuta solo en este dispositivo")
    analizador.add_argument("--detalle", action="store_true", help="muestra medidas y trazas")
    argumentos = analizador.parse_args()
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(ejecutar(argumentos))


if __name__ == "__main__":
    main()
