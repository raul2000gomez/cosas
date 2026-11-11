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
import base64
import calendar
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
import urllib.error
import urllib.request
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
                "Sin sesión, todo se queda en este dispositivo, menos lo que tenga recordatorio en Recordar: "
                "eso se guarda también en el servidor de Cosas para poder avisarte.")
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
        # Y la función de avisos, en /api/.
        if self.path.startswith("/api/"):
            cuerpo = b'{"clave": "de-prueba"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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

# El aviso se busca por su id: no es el único role="status" de la página (la nota de Recordar también lo es, y va antes).
JS_TOAST = r"""
() => {
  const t = document.querySelector('#toast[role="status"]');
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
        const t = document.querySelector('#toast[role="status"]').getBoundingClientRect();
        const choca = (sel) => { const r = document.querySelector(sel).getBoundingClientRect();
            return !(r.right <= t.left || r.left >= t.right || r.bottom <= t.top || r.top >= t.bottom); };
        return { barra: choca('#formulario .pildora'), lista: choca('#abrir-lista'), ajustes: choca('#abrir-ajustes') };
    }""")
    igual(solapes, {"barra": False, "lista": False, "ajustes": False}, "Solapes del aviso")
    # El aviso lleva pointer-events:none: que nada lo tapa se comprueba con píxeles reales.
    fondo_aviso = rgb_css(p.evaluate("() => getComputedStyle(document.querySelector('#toast[role=status]')).backgroundColor"))
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
    igual(estilo["linea"], "none", "El texto hecho va tachado (hecha es el check verde y el texto atenuado, sin tachar)")
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
    esperar(p, "(t) => getComputedStyle(t).opacity === '1'", arg=fila.locator("p").element_handle(), que="el texto recupera su opacidad al volver a pendiente")
    igual(fila.locator("p").evaluate("(t) => getComputedStyle(t).textDecorationLine"), "none", "El texto va tachado al volver a pendiente")


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
    igual([h.strip() for h in p.locator("#vista-ajustes h2").all_text_contents()], ["Apariencia", "Datos personales", "Notificaciones", "Aplicación"], "Secciones de ajustes")
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
  const toastEl = q('#toast[role="status"]');
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
  const aviso = document.querySelector('#toast[role="status"]');
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
    igual(orden, ["Apariencia", "Datos personales", "Notificaciones", "Aplicación", "Cosas 1.2"], "Orden de los apartados de ajustes")
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


@prueba("service worker: no sirve ni guarda las rutas del alojamiento (/.netlify/) ni la función de avisos (/api/)")
def t_sw_rutas_del_alojamiento(e):
    contexto = e.contexto()
    p = e.vigilar(contexto.new_page())
    p.goto(e.base)
    esperar(p, "() => navigator.serviceWorker.controller !== null", ms=15000, que="el SW toma el control")
    igual(p.evaluate("async () => (await fetch('/.netlify/scripts/hud?variant=public')).ok"), True, "La ruta del alojamiento responde")
    igual(p.evaluate("async () => (await fetch('/api/avisos/clave')).ok"), True, "La función de avisos responde")
    guardadas = p.evaluate("""async () => { const rutas = []; for (const n of await caches.keys()) { for (const r of await (await caches.open(n)).keys()) rutas.push(new URL(r.url).pathname); } return rutas; }""")
    comprobar(len(guardadas) > 0, "La caché de la app está vacía")
    igual([r for r in guardadas if r.startswith(("/.netlify/", "/api/"))], [], "Rutas del alojamiento o de la función guardadas en la caché")


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
    hecha = p.locator("#lista-candidatas > li").last.locator(".texto").evaluate("(t) => { const cs = getComputedStyle(t); return { linea: cs.textDecorationLine, opacidad: parseFloat(cs.opacity) }; }")
    comprobar(hecha["linea"] == "none" and 0.3 <= hecha["opacidad"] < 1, f"Las hechas del desplegable no se ven atenuadas y sin tachar: {hecha}")
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
export function initializeAuth(app, opciones) {
  window.__authFalsa = {
    app,
    persistencias: opciones.persistence.map((p) => p.tipo),
    get currentUser() { const u = window.__usuarioFalso; return u ? { ...u, getIdToken: async () => `token-${u.uid}` } : null; },
    authStateReady: async () => {},
  };
  return window.__authFalsa;
}
export async function signInAnonymously() {
  window.__anonimos = (window.__anonimos || 0) + 1;
  window.__usuarioFalso = { uid: `anonimo-${window.__anonimos}`, isAnonymous: true };
  if (window.__cambiarUsuarioFalso) window.__cambiarUsuarioFalso(window.__usuarioFalso);
  return { user: window.__usuarioFalso };
}
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
// doc(coleccion, id) o doc(db, 'coleccion', …, 'id').
export function doc(base, ...partes) {
  if (base && typeof base.ruta === 'string' && partes.length === 1) return { ruta: base.ruta, id: partes[0] };
  return { ruta: partes.slice(0, -1).join('/'), id: partes[partes.length - 1] };
}
export function where(campo, op, valor) { return { campo, op, valor }; }
export function query(coleccion, ...filtros) { return { ruta: coleccion.ruta, filtros }; }
export function serverTimestamp() { return Date.now(); }
const pasa = (datos, filtros) => (filtros || []).every((f) => f.op === 'array-contains' && Array.isArray(datos[f.campo]) && datos[f.campo].includes(f.valor));
// La instantánea de un documento (exists, data) o de una colección o consulta (docs).
function instantanea(ref, docs, fromCache) {
  const metadata = { fromCache, hasPendingWrites: false };
  if (ref.id !== undefined) {
    const d = docs.find((x) => x.id === ref.id);
    return { metadata, id: ref.id, exists: () => Boolean(d), data: () => (d ? d.datos : undefined) };
  }
  return { metadata, docs: docs.filter((d) => pasa(d.datos, ref.filtros)).map((d) => ({ id: d.id, data: () => d.datos })) };
}
export function onSnapshot(ref, ...resto) {
  const [siguiente, error] = typeof resto[0] === 'function' ? resto : resto.slice(1);
  let version = -1;
  let vivo = true;
  const mirar = async () => {
    if (!vivo) return;
    try {
      const r = await llamar('leer', { ruta: ref.ruta });
      if (r.error) { vivo = false; error(new Error(r.error)); return; }
      if (vivo && r.version !== version) {
        version = r.version;
        siguiente(instantanea(ref, r.docs, false));
      }
    } catch (e) { /* La página se está cerrando. */ }
    if (vivo) setTimeout(mirar, 80);
  };
  // Como el SDK de verdad: antes de oír al servidor, una instantánea de la caché (vacía en memoria).
  setTimeout(() => { if (vivo) siguiente(instantanea(ref, [], true)); }, 0);
  setTimeout(mirar, 30);
  return () => { vivo = false; };
}
export function setDoc(ref, datos, opciones) {
  return llamar('escribir', { operaciones: [{ ruta: ref.ruta, id: ref.id, datos, fundir: Boolean(opciones && opciones.merge) }] }).then((r) => {
    if (r.retener) return new Promise(() => {});
    if (r.error) throw new Error(r.error);
  });
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


def fundir(antes, cambios):
    """Como setDoc con merge: los mapas se funden campo a campo; lo demás se sustituye."""
    resultado = dict(antes)
    for clave, valor in cambios.items():
        resultado[clave] = fundir(resultado.get(clave) or {}, valor) if isinstance(valor, dict) else valor
    return resultado


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
            elif operacion.get("fundir"):
                documentos[operacion["id"]] = fundir(documentos.get(operacion["id"], {}), operacion["datos"])
            else:
                documentos[operacion["id"]] = operacion["datos"]
        self.version += 1
        return json.dumps({})

    def poner(self, ruta, id, datos):
        """Un cambio que llega de otro sitio (otra persona, otro dispositivo)."""
        self.colecciones.setdefault(ruta, {})[id] = datos
        self.version += 1

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


def dispositivo(e, nube, usuario="u1", contexto=None, **opciones):
    """Un contexto (un dispositivo) con la app en https://cosas.test/ y el Firebase de mentira; «usuario» es su sesión de Google.
    Con «opciones», las del contexto (su zona horaria, por ejemplo); con «contexto», uno ya abierto (el de otro navegador)."""
    for origen in (ORIGEN_NUBE, CDN_FIREBASE):
        if origen not in e.origenes_propios:
            e.origenes_propios.append(origen)
    # El origen de mentira no tiene service worker (lo sirve Playwright): la app avisa de que no se ha podido registrar.
    e.tolerar.append(r"^Service Worker registration blocked by Playwright$")
    contexto = contexto or e.contexto(service_workers="block", **opciones)
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


@prueba("novedades: las cuentas del punto verde (lo último de otra persona frente a lo visto, a cuándo entraste y a cuándo se empezó a contar)", una_vez=True)
def t_novedades_cuentas(e):
    p = e.pagina("icons/favicon-32.png")
    r = p.evaluate("""async () => {
      const n = await import('/nube.js');
      const lista = (id, tipo, en, por, extra = {}) => ({ id, tipo, uids: ['yo', 'otra'], miembros: { yo: { desde: 1000 } }, ultima: { en, por }, ...extra });
      const marca = (v) => ({ toMillis: () => v });
      return {
        sinDesde: n.novedadesDe([lista('a', 'con', 5000, 'otra')], {}, 'yo'),
        sinSesion: n.novedadesDe([lista('a', 'con', 5000, 'otra')], { _desde: 2000 }, null),
        nueva: n.novedadesDe([lista('a', 'con', 5000, 'otra')], { _desde: 2000 }, 'yo'),
        propia: n.novedadesDe([lista('a', 'con', 5000, 'yo')], { _desde: 2000 }, 'yo'),
        vista: n.novedadesDe([lista('a', 'con', 5000, 'otra')], { _desde: 2000, a: 6000 }, 'yo'),
        antesDeContar: n.novedadesDe([lista('a', 'con', 1500, 'otra')], { _desde: 2000 }, 'yo'),
        antesDeEntrar: n.novedadesDe([lista('a', 'de', 5000, 'otra', { miembros: { yo: { desde: 7000 } } })], { _desde: 2000 }, 'yo'),
        deCosasDe: n.novedadesDe([lista('a', 'con', 5000, 'yo'), lista('b', 'de', 5000, 'otra')], { _desde: 2000 }, 'yo'),
        marcasDeFirestore: n.novedadesDe([lista('a', 'de', marca(5000), 'otra')], { _desde: marca(2000), a: marca(4000) }, 'yo'),
        sinUltima: n.novedadesDe([{ id: 'a', tipo: 'con' }], { _desde: 2000 }, 'yo'),
      };
    }""")
    nada, con, de = {"con": False, "de": False}, {"con": True, "de": False}, {"con": False, "de": True}
    esperado = {"sinDesde": nada, "sinSesion": nada, "nueva": con, "propia": nada, "vista": nada, "antesDeContar": nada,
                "antesDeEntrar": nada, "deCosasDe": de, "marcasDeFirestore": de, "sinUltima": nada}
    for caso, valor in esperado.items():
        igual(r[caso], valor, f"Novedades: {caso}")


JS_PUNTOS = """() => Object.fromEntries([...document.querySelectorAll('#accesos a')].map((a) => {
    const d = getComputedStyle(a, '::after'), icono = a.querySelector('svg').getBoundingClientRect(), caja = a.getBoundingClientRect();
    return [a.dataset.tipo, { punto: a.hasAttribute('data-novedad'), contenido: d.content, color: d.backgroundColor, etiqueta: a.getAttribute('aria-label'),
      centro: [caja.left + parseFloat(d.left) + parseFloat(d.width) / 2 - icono.right, caja.top + parseFloat(d.top) + parseFloat(d.height) / 2 - icono.top] }];
}))"""


def abrir_con_listas(contexto, e, listas=("l1", "l2")):
    """La app en ese dispositivo, con listas de Cosas con y Cosas de ya abiertas en él (sin cuenta de Google)."""
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(ORIGEN_NUBE + "icons/favicon-32.png")
    if listas:
        pagina.evaluate("(ids) => localStorage.setItem('cosascon:ids', JSON.stringify(ids))", list(listas))
    pagina.goto(ORIGEN_NUBE)
    esperar_vista(pagina, "inicio")
    return pagina


@prueba("novedades: punto verde arriba a la derecha del icono de Cosas con o Cosas de si otra persona ha apuntado algo sin ver; se va al verlo; la primera vez se empieza a contar; sin listas en el dispositivo, ni se carga")
def t_novedades_puntos(e):
    nube = NubeFalsa()
    nube.colecciones["listas"] = {
        "l1": {"tipo": "con", "uids": ["u1", "u2"], "miembros": {"u1": {"desde": 1000}, "u2": {"desde": 1000}}, "ultima": {"en": 5000, "por": "u2", "grupo": None}},
        "l2": {"tipo": "de", "uids": ["u1", "u3"], "miembros": {"u1": {"desde": 1000}}, "ultima": {"en": 6000, "por": "u1", "grupo": None}},
        "l9": {"tipo": "de", "uids": ["u7"], "ultima": {"en": 9000, "por": "u7"}},  # De otros: no cuenta.
    }
    nube.colecciones["usuarios"] = {"u1": {"nombre": "Raúl", "vistos": {"_desde": 2000}}}
    # Con la sesión (anónima) que dejaron Cosas con y Cosas de, sin cuenta de Google.
    contexto = dispositivo(e, nube, usuario=None)
    contexto.add_init_script("window.__usuarioFalso = { uid: 'u1', isAnonymous: true };")
    p = abrir_con_listas(contexto, e)
    esperar(p, f"() => ({JS_PUNTOS})().con.punto", que="sale el punto de Cosas con")
    puntos = p.evaluate(JS_PUNTOS)
    igual((puntos["con"]["contenido"], rgb_css(puntos["con"]["color"]), puntos["con"]["etiqueta"]), ('""', (34, 197, 94), "Cosas con. Hay cosas nuevas"),
          "Punto verde de Cosas con (y lo dice a los lectores de pantalla)")
    comprobar(all(abs(v) <= 4 for v in puntos["con"]["centro"]), f"El punto no está arriba a la derecha del icono: {puntos['con']['centro']}")
    igual((puntos["de"]["punto"], puntos["de"]["contenido"], puntos["de"]["etiqueta"]), (False, "none", None), "Sin novedades en Cosas de (lo último es tuyo)")
    comprobar(not p.evaluate("() => document.documentElement.hasAttribute('data-nube')"), "Sin cuenta de Google, las novedades encienden la sincronización de tus cosas")
    # Lo ves (en la lista, en Cosas con): se va, sin recargar.
    nube.poner("usuarios", "u1", {"nombre": "Raúl", "vistos": {"_desde": 2000, "l1": 7000}})
    esperar(p, f"() => !({JS_PUNTOS})().con.punto", que="se va el punto de Cosas con al verlo")
    # Otra persona apunta algo en tu lista de Cosas de: sale su punto.
    nube.poner("listas", "l2", {**nube.colecciones["listas"]["l2"], "ultima": {"en": 8000, "por": "u3", "grupo": "g1"}})
    esperar(p, f"() => ({JS_PUNTOS})().de.punto && !({JS_PUNTOS})().con.punto", que="sale el punto de Cosas de")
    # Al cerrar la sesión de esas páginas, sin puntos.
    p.evaluate("() => window.__cambiarUsuarioFalso(null)")
    esperar(p, f"() => !({JS_PUNTOS})().de.punto", que="sin sesión, sin puntos")

    # La primera vez (sin «_desde») no hay nada nuevo y se empieza a contar desde ahora.
    nube2 = NubeFalsa()
    nube2.colecciones["listas"] = {"l1": dict(nube.colecciones["listas"]["l1"])}
    nube2.colecciones["usuarios"] = {"u1": {"nombre": "Raúl"}}
    contexto2 = dispositivo(e, nube2, usuario="u1")
    p2 = abrir_con_listas(contexto2, e)
    for _ in range(40):  # La nube de mentira es de Python: se mira desde aquí.
        if (nube2.colecciones["usuarios"]["u1"].get("vistos") or {}).get("_desde"):
            break
        p2.wait_for_timeout(100)
    desde = (nube2.colecciones["usuarios"]["u1"].get("vistos") or {}).get("_desde")
    comprobar(isinstance(desde, (int, float)) and desde > 0, f"La primera vez no apunta desde cuándo se cuenta: {nube2.colecciones['usuarios']['u1']}")
    igual(nube2.colecciones["usuarios"]["u1"].get("nombre"), "Raúl", "Al apuntar desde cuándo se cuenta, se pierde el resto del perfil")
    p2.wait_for_timeout(300)
    igual(p2.evaluate(f"() => ({JS_PUNTOS})().con.punto"), False, "La primera vez, lo que ya había sale como nuevo")

    # Sin listas de Cosas con y Cosas de en el dispositivo (ni cuenta), la app ni pide nube.js ni Firebase.
    contexto3 = dispositivo(e, NubeFalsa(), usuario="u1")
    peticiones = []
    contexto3.on("request", lambda peticion: peticiones.append(peticion.url))
    p3 = abrir_con_listas(contexto3, e, listas=())
    p3.wait_for_timeout(800)
    igual([u for u in peticiones if "nube.js" in u or u.startswith(CDN_FIREBASE) or "firebase-config" in u], [], "Sin listas compartidas se carga la nube")
    igual(p3.evaluate("() => document.querySelectorAll('#accesos [data-novedad]').length"), 0, "Puntos sin listas compartidas")


# ----------------------------------------------------------------------------
# Notificaciones (avisos de Cosas con y Cosas de)
# ----------------------------------------------------------------------------

CLAVE_AVISOS = "cosas:avisos"
CLAVE_VAPID = bytes(range(1, 66))  # La clave pública de mentira de la función de avisos (65 bytes, como una de verdad).
ENDPOINT_PRUEBA = "https://fcm.googleapis.com/fcm/send/"
PARA_QUE_AVISOS = "te recordamos lo que tengas en Recordar y te avisamos cuando alguien añada una cosa a tus listas de Cosas con y Cosas de."
AYUDA_AVISOS = {
    "apagados": f"Si las activas, {PARA_QUE_AVISOS}",
    "activos": f"Activadas en este dispositivo: {PARA_QUE_AVISOS}",
    "bloqueados": "Las notificaciones de Cosas están bloqueadas en este dispositivo. Para recibirlas, permítelas en los ajustes del móvil o del navegador.",
    "instalar": "En iPhone, las notificaciones llegan a la aplicación: pulsa «Convertir en aplicación» y actívalas desde ella.",
    "imposible": "Este navegador no puede recibir notificaciones.",
}

# El permiso y el push del navegador, de mentira (el Chromium de las pruebas no los tiene): window.__avisos dice lo que
# hay (permiso, suscripción) y apunta lo que se pide.
JS_AVISOS_FALSOS = r"""(() => {
  const a = window.__avisos = { permiso: 'default', respuesta: 'granted', pedidos: 0, suscripcion: null, suscritas: 0, bajas: 0, retraso: 0, opciones: null };
  Object.defineProperty(Notification, 'permission', { configurable: true, get: () => a.permiso });
  Notification.requestPermission = async () => { a.pedidos += 1; if (a.permiso === 'default') a.permiso = a.respuesta; return a.permiso; };
  a.nueva = (endpoint, clave) => ({
    endpoint,
    options: { userVisibleOnly: true, applicationServerKey: clave ? new Uint8Array(clave).buffer : null },
    toJSON() { return { endpoint, expirationTime: null, keys: { p256dh: 'clave-p256dh', auth: 'clave-auth' } }; },
    async unsubscribe() { a.bajas += 1; if (a.suscripcion && a.suscripcion.endpoint === endpoint) a.suscripcion = null; return true; },
  });
  const pushManager = {
    async getSubscription() { return a.suscripcion; },
    async subscribe(opciones) {
      a.suscritas += 1;
      a.opciones = { userVisibleOnly: opciones.userVisibleOnly, clave: Array.from(new Uint8Array(opciones.applicationServerKey)) };
      if (a.retraso) await new Promise((r) => setTimeout(r, a.retraso));
      a.suscripcion = a.nueva(`https://fcm.googleapis.com/fcm/send/prueba-${a.suscritas}`, a.opciones.clave);
      return a.suscripcion;
    },
  };
  if (!('PushManager' in window)) window.PushManager = function PushManager() {};
  const registro = { scope: location.origin + '/', pushManager };
  Object.defineProperty(ServiceWorkerContainer.prototype, 'ready', { configurable: true, get: () => Promise.resolve(registro) });
  ServiceWorkerContainer.prototype.getRegistration = async () => registro;
})();"""
JS_SIN_PUSH = "delete window.PushManager;"


class ApiAvisosFalsa:
    """La función de avisos (/api/avisos/…) de mentira: apunta cada llamada (acción, Authorization, cuerpo)."""

    def __init__(self):
        self.llamadas = []
        self.fallar = set()

    def atender(self, route):
        accion = route.request.url.split("/api/avisos/", 1)[1].split("?")[0]
        cuerpo = route.request.post_data
        self.llamadas.append((accion, route.request.headers.get("authorization"), json.loads(cuerpo) if cuerpo else None))
        if accion in self.fallar:
            route.fulfill(status=500, content_type="application/json", body='{"error":"interno"}')
        elif accion == "clave":
            clave = base64.urlsafe_b64encode(CLAVE_VAPID).rstrip(b"=").decode()
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"clave": clave}))
        else:
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def de(self, accion):
        return [llamada for llamada in self.llamadas if llamada[0] == accion]


def dispositivo_con_avisos(e, api, usuario=None, preparar="", nube=None, **opciones):
    """Un dispositivo en https://cosas.test con el push de mentira y la función de avisos «api»; «usuario», su cuenta de Google;
    «nube», la de sus cosas (si la comparte con otro dispositivo); «opciones», las de su contexto."""
    contexto = dispositivo(e, nube or NubeFalsa(), usuario=usuario, **opciones)
    contexto.add_init_script(JS_AVISOS_FALSOS + preparar)
    contexto.route(ORIGEN_NUBE + "api/avisos/**", api.atender)
    return contexto


def abrir_con_avisos(contexto, e, guardado=None):
    """La app en ese dispositivo; con «guardado», las notificaciones que ya se activaron en él (CLAVE_AVISOS)."""
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(ORIGEN_NUBE + "icons/favicon-32.png")
    if guardado is not None:
        pagina.evaluate("([clave, valor]) => localStorage.setItem(clave, valor)", [CLAVE_AVISOS, json.dumps(guardado)])
    pagina.goto(ORIGEN_NUBE)
    esperar_vista(pagina, "inicio")
    return pagina


def boton_avisos(pagina):
    return pagina.locator("#avisos")


def ayuda_avisos(pagina):
    return pagina.locator("#ayuda-avisos").text_content()


def guardado_avisos(pagina):
    return pagina.evaluate("(clave) => JSON.parse(localStorage.getItem(clave))", CLAVE_AVISOS)


def esperar_boton_avisos(pagina, texto, ms=6000):
    esperar(pagina, "(texto) => document.querySelector('#avisos').textContent === texto", arg=texto, ms=ms, que=f"el botón dice «{texto}»")


def esperar_llamadas(pagina, api, accion, cuantas, ms=5000):
    fin = time.monotonic() + ms / 1000
    while len(api.de(accion)) < cuantas:
        if time.monotonic() > fin:
            raise Fallo(f"La función no recibe {cuantas} «{accion}» en {ms} ms: {api.llamadas}")
        pagina.wait_for_timeout(50)  # Con Playwright esperando, las llamadas a la función falsa se atienden.


@prueba("notificaciones: apartado propio en Ajustes, antes de Aplicación; el botón y la ayuda según se pueda, estén activadas o bloqueadas")
def t_avisos_apartado(e):
    p = pagina_de_ajustes(e, [JS_AVISOS_FALSOS], user_agent=UA_ANDROID)
    orden = p.evaluate("() => [...document.querySelector('#vista-ajustes .ajustes').children].map((h) => (h.querySelector('h2') || h).textContent.trim())")
    igual(orden, ["Apariencia", "Datos personales", "Notificaciones", "Aplicación", "Cosas 1.2"], "Orden de los apartados de ajustes")
    boton = p.get_by_role("button", name="Activar notificaciones", exact=True)
    boton.scroll_into_view_if_needed()
    comprobar(boton.is_visible(), "No se ve el botón «Activar notificaciones»")
    medidas = boton.evaluate("""(b) => { const r = b.getBoundingClientRect(), cs = getComputedStyle(b), grupo = b.parentElement.getBoundingClientRect(), ayuda = document.querySelector('#ayuda-avisos');
        return { ancho: r.width, alto: r.height, anchoGrupo: grupo.width, radio: parseFloat(cs.borderTopLeftRadius), fuente: parseFloat(cs.fontSize),
                 descrito: b.getAttribute('aria-describedby'), tipo: b.type, ayudaDebajo: ayuda.getBoundingClientRect().top >= r.bottom }; }""")
    comprobar(abs(medidas["ancho"] - medidas["anchoGrupo"]) <= 1 and medidas["alto"] >= 48 and medidas["radio"] >= medidas["alto"] / 2 - 0.5 and medidas["fuente"] >= 16,
              f"El botón no es una píldora de todo el ancho como las demás de Ajustes: {medidas}")
    igual((medidas["tipo"], medidas["descrito"], medidas["ayudaDebajo"]), ("button", "ayuda-avisos", True), "Botón y ayuda")
    igual(ayuda_avisos(p), AYUDA_AVISOS["apagados"], "Ayuda sin activar")
    comprobar_sin_desborde(p, "ajustes con el apartado «Notificaciones»")

    activadas = "localStorage.setItem('cosas:avisos', JSON.stringify({ uid: 'u1', endpoint: 'https://fcm.googleapis.com/fcm/send/x', en: Date.now() }));"
    casos = [
        # (qué, agente, guion, botón, ayuda)
        ("activadas", UA_ANDROID, "window.__avisos.permiso = 'granted';" + activadas, "Desactivar notificaciones", "activos"),
        ("con permiso, pero sin activar en este dispositivo", UA_ANDROID, "window.__avisos.permiso = 'granted';", "Activar notificaciones", "apagados"),
        ("bloqueadas", UA_ANDROID, "window.__avisos.permiso = 'denied';" + activadas, None, "bloqueados"),
        ("iPhone en Safari (sin push hasta instalarla)", UA_IPHONE, JS_SIN_PUSH, None, "instalar"),
        ("navegador sin push", UA_ANDROID, JS_SIN_PUSH, None, "imposible"),
    ]
    for que, agente, guion, texto_boton, ayuda in casos:
        p = pagina_de_ajustes(e, [JS_AVISOS_FALSOS, guion], user_agent=agente)
        if texto_boton:
            comprobar(boton_avisos(p).is_visible(), f"{que}: no se ve el botón")
            igual(boton_avisos(p).text_content(), texto_boton, f"{que}: texto del botón")
        else:
            comprobar(boton_avisos(p).is_hidden(), f"{que}: se ve un botón que no sirve")
        igual(ayuda_avisos(p), AYUDA_AVISOS[ayuda], f"{que}: ayuda")


@prueba("notificaciones: activarlas pide permiso, se suscribe con la clave de la función y da de alta este dispositivo con la sesión de Cosas con y Cosas de (anónima si aún no hay); desactivarlas lo deshace")
def t_avisos_activar(e):
    api = ApiAvisosFalsa()
    p = abrir_con_avisos(dispositivo_con_avisos(e, api, preparar="window.__avisos.retraso = 500;"), e)
    abrir_ajustes(p)
    p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
    boton = boton_avisos(p)
    igual(boton.text_content(), "Activar notificaciones", "Botón sin activar")
    boton.click()
    esperar(p, "() => { const b = document.querySelector('#avisos'); return b.textContent === 'Activando…' && b.getAttribute('aria-busy') === 'true'; }",
            que="mientras se activan, el botón dice «Activando…»")
    boton.click()  # Un segundo toque mientras tanto no hace nada.
    esperar_boton_avisos(p, "Desactivar notificaciones")
    esperar_toast(p, "Notificaciones activadas")
    comprobar(boton.get_attribute("aria-busy") is None, "El botón sigue marcado como ocupado")
    igual(ayuda_avisos(p), AYUDA_AVISOS["activos"], "Ayuda con las notificaciones activadas")
    avisos = p.evaluate("() => ({ pedidos: window.__avisos.pedidos, suscritas: window.__avisos.suscritas, opciones: window.__avisos.opciones, anonimos: window.__anonimos || 0 })")
    igual(avisos, {"pedidos": 1, "suscritas": 1, "opciones": {"userVisibleOnly": True, "clave": list(CLAVE_VAPID)}, "anonimos": 1},
          "Permiso pedido una vez, suscripción con la clave de la función y, sin sesión, entrada anónima (como harían Cosas con y Cosas de)")
    altas = api.de("alta")
    igual(len(altas), 1, f"Altas en la función (un doble toque no da dos): {api.llamadas}")
    igual(altas[0][1:], ("Bearer token-anonimo-1", {"suscripcion": {"endpoint": ENDPOINT_PRUEBA + "prueba-1", "expirationTime": None,
                                                                    "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"}}}),
          "El alta: con el token de la sesión y la suscripción del navegador")
    guardado = guardado_avisos(p)
    comprobar(guardado and guardado["uid"] == "anonimo-1" and guardado["endpoint"] == ENDPOINT_PRUEBA + "prueba-1" and abs(guardado["en"] - time.time() * 1000) < 60000,
              f"Lo que recuerda el dispositivo: {guardado}")

    # Desactivarlas: se deshace la suscripción y se olvidan aquí y en la función.
    p.wait_for_timeout(400)
    boton.click()
    esperar_boton_avisos(p, "Activar notificaciones")
    esperar_toast(p, "Notificaciones desactivadas")
    igual((guardado_avisos(p), p.evaluate("() => window.__avisos.bajas")), (None, 1), "Desactivadas: el dispositivo las olvida y deshace la suscripción")
    esperar_llamadas(p, api, "baja", 1)
    igual(api.de("baja")[0][1:], ("Bearer token-anonimo-1", {"endpoint": ENDPOINT_PRUEBA + "prueba-1"}), "La baja en la función")
    igual(ayuda_avisos(p), AYUDA_AVISOS["apagados"], "Ayuda tras desactivarlas")

    # Con la cuenta de Google de Cosas con y Cosas de: se da de alta con ella, sin entrar de forma anónima.
    api2 = ApiAvisosFalsa()
    p2 = abrir_con_avisos(dispositivo_con_avisos(e, api2, usuario="u1"), e)
    abrir_ajustes(p2)
    p2.wait_for_timeout(400)
    boton_avisos(p2).click()
    esperar_boton_avisos(p2, "Desactivar notificaciones")
    igual((api2.de("alta")[0][1], (guardado_avisos(p2) or {}).get("uid"), p2.evaluate("() => window.__anonimos || 0")), ("Bearer token-u1", "u1", 0),
          "Con cuenta: el alta va con su token, el dispositivo avisa a esa cuenta y no se entra de forma anónima")


@prueba("notificaciones: sin permiso quedan bloqueadas (sin alta ni suscripción); si se cierra la pregunta, nada cambia; si la función falla, se dice y se pueden volver a activar")
def t_avisos_fallos(e):
    api = ApiAvisosFalsa()
    p = abrir_con_avisos(dispositivo_con_avisos(e, api, preparar="window.__avisos.respuesta = 'denied';"), e)
    abrir_ajustes(p)
    p.wait_for_timeout(400)
    boton_avisos(p).click()
    esperar(p, "() => document.querySelector('#avisos').hidden", que="sin permiso, el botón se va")
    igual(ayuda_avisos(p), AYUDA_AVISOS["bloqueados"], "Ayuda sin permiso")
    igual(p.evaluate("() => document.activeElement && document.activeElement.id"), "titulo-ajustes", "Al irse el botón, el foco pasa al título")
    igual((api.llamadas, guardado_avisos(p), p.evaluate("() => window.__avisos.suscritas")), ([], None, 0), "Sin permiso: llamadas, lo guardado y suscripciones")

    api2 = ApiAvisosFalsa()
    p2 = abrir_con_avisos(dispositivo_con_avisos(e, api2, preparar="window.__avisos.respuesta = 'default';"), e)
    abrir_ajustes(p2)
    p2.wait_for_timeout(400)
    boton_avisos(p2).click()
    esperar_boton_avisos(p2, "Activar notificaciones")
    p2.wait_for_timeout(300)
    igual((api2.llamadas, guardado_avisos(p2), (toast(p2) or {}).get("texto") or ""), ([], None, ""), "Pregunta cerrada sin elegir: nada cambia ni se avisa")

    e.tolerar += [r"/api/avisos/alta", r"status of 500 \(Internal Server Error\)$"]  # El 500 de la función de mentira.
    api3 = ApiAvisosFalsa()
    api3.fallar.add("alta")
    p3 = abrir_con_avisos(dispositivo_con_avisos(e, api3), e)
    abrir_ajustes(p3)
    p3.wait_for_timeout(400)
    boton_avisos(p3).click()
    esperar_toast(p3, "No se pudieron activar las notificaciones", ms=6000)
    igual((boton_avisos(p3).text_content(), guardado_avisos(p3)), ("Activar notificaciones", None), "Tras el fallo: sin activar y se puede volver a intentar")


@prueba("notificaciones: al abrir la app, si siguen activadas, este dispositivo sigue dado de alta (otra suscripción, otra clave o pasado un día); al día, ni se carga la nube; sin permiso, quedan desactivadas")
def t_avisos_renovar(e):
    clave = json.dumps(list(CLAVE_VAPID))
    e1 = ENDPOINT_PRUEBA + "E1"
    reciente = {"uid": "anonimo-1", "endpoint": e1, "en": time.time() * 1000}
    antiguo = {**reciente, "en": time.time() * 1000 - 2 * 86400 * 1000}

    def abrir(preparar, guardado):
        api = ApiAvisosFalsa()
        contexto = dispositivo_con_avisos(e, api, preparar="window.__avisos.permiso = 'granted';" + preparar)
        peticiones = []
        contexto.on("request", lambda peticion: peticiones.append(peticion.url))
        return api, abrir_con_avisos(contexto, e, guardado), peticiones

    suscrita = f"window.__avisos.suscripcion = window.__avisos.nueva('{e1}', {clave});"
    api, p, peticiones = abrir(suscrita, reciente)
    p.wait_for_timeout(800)
    igual(api.llamadas, [], "Al día: llamadas a la función")
    igual([u for u in peticiones if "nube.js" in u or u.startswith(CDN_FIREBASE)], [], "Al día, se carga la nube")
    abrir_ajustes(p)
    igual(boton_avisos(p).text_content(), "Desactivar notificaciones", "Al día, siguen activadas")

    casos = [
        # (qué, preparar, guardado, endpoint dado de alta, [suscritas, bajas])
        ("pasado un día, la misma suscripción", suscrita, antiguo, e1, [0, 0]),
        ("con otra clave (la función cambió las suyas), otra suscripción", f"window.__avisos.suscripcion = window.__avisos.nueva('{e1}', [9, 9, 9]);", antiguo, ENDPOINT_PRUEBA + "prueba-1", [1, 1]),
        ("el navegador cambió la suscripción: la nueva, enseguida", f"window.__avisos.suscripcion = window.__avisos.nueva('{ENDPOINT_PRUEBA}E2', {clave});", reciente, ENDPOINT_PRUEBA + "E2", [0, 0]),
        ("el navegador la perdió: se suscribe otra vez", "", reciente, ENDPOINT_PRUEBA + "prueba-1", [1, 0]),
    ]
    for que, preparar, guardado, endpoint, cuentas in casos:
        api, p, _ = abrir(preparar, guardado)
        esperar_llamadas(p, api, "alta", 1)
        igual(api.de("alta")[0][2]["suscripcion"]["endpoint"], endpoint, f"{que}: lo que se da de alta")
        esperar(p, "([clave, endpoint]) => { const g = JSON.parse(localStorage.getItem(clave)); return g.endpoint === endpoint && g.en > Date.now() - 60000; }",
                arg=[CLAVE_AVISOS, endpoint], que=f"{que}: se apunta el alta")
        igual(p.evaluate("() => [window.__avisos.suscritas, window.__avisos.bajas]"), cuentas, f"{que}: suscripciones y bajas")

    api, p, _ = abrir("window.__avisos.permiso = 'default';", reciente)
    esperar(p, "(clave) => localStorage.getItem(clave) === null", arg=CLAVE_AVISOS, que="sin permiso (quitado en los ajustes del móvil), quedan desactivadas")
    igual(api.llamadas, [], "Sin permiso: llamadas a la función")


@prueba("service worker: enseña los avisos (título, texto, icono, insignia, enlace) y junta los de una misma lista en uno, lo nuevo arriba y hasta cinco líneas", una_vez=True)
def t_sw_avisos(e):
    # El Chromium de las pruebas no enseña notificaciones: hace falta Google Chrome (los avisos llegan por CDP, sin servicio push).
    try:
        chrome = e.pw.chromium.launch(channel="chrome")
    except Exception:
        print("         (sin Google Chrome en este equipo: no se puede comprobar)")
        return
    try:
        e.tolerar.append(r"^Banner not shown")  # Chrome lo dice al guardarse la instalación para el botón de Ajustes.
        origen = e.base.rstrip("/")
        contexto = chrome.new_context()
        contexto.grant_permissions(["notifications"], origin=origen)
        p = e.vigilar(contexto.new_page())
        p.goto(e.base)
        esperar(p, "() => navigator.serviceWorker.controller !== null", ms=15000, que="el SW toma el control")
        cdp = contexto.new_cdp_session(p)
        registros = []
        cdp.on("ServiceWorker.workerRegistrationUpdated", lambda evento: registros.extend(evento["registrations"]))
        cdp.send("ServiceWorker.enable")
        fin = time.monotonic() + 5
        while not any(not r.get("isDeleted") for r in registros) and time.monotonic() < fin:
            p.wait_for_timeout(50)
        registro = next(r["registrationId"] for r in registros if not r.get("isDeleted"))

        def empujar(datos):
            bruto = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
            cdp.send("ServiceWorker.deliverPushMessage", {"origin": origen, "registrationId": registro, "data": bruto})

        def esperar_avisos(condicion, que):
            fin = time.monotonic() + 5
            while True:
                avisos = p.evaluate("""async () => (await (await navigator.serviceWorker.ready).getNotifications())
                    .map((n) => ({ titulo: n.title, cuerpo: n.body, etiqueta: n.tag, datos: n.data, icono: n.icon, insignia: n.badge }))""")
                if condicion(avisos):
                    return avisos
                if time.monotonic() > fin:
                    raise Fallo(f"No se cumplió: {que} (avisos: {avisos})")
                p.wait_for_timeout(50)

        viaje = {"titulo": "Cosas de Viaje", "etiqueta": "lista-viaje123"}
        empujar({**viaje, "linea": "Ana ha añadido «Billetes»", "url": "/de/viaje123#grupo/g1"})
        aviso = esperar_avisos(lambda a: len(a) == 1, "llega el aviso")[0]
        igual((aviso["titulo"], aviso["cuerpo"], aviso["etiqueta"], aviso["datos"]),
              ("Cosas de Viaje", "Ana ha añadido «Billetes»", "lista-viaje123", {"url": "/de/viaje123#grupo/g1", "lineas": ["Ana ha añadido «Billetes»"]}), "El aviso")
        igual((aviso["icono"], aviso["insignia"]), (e.base + "icons/icon-192.png", e.base + "icons/aviso-96.png"), "Icono e insignia del aviso")

        empujar({**viaje, "linea": "Beto ha añadido «Maleta»", "url": "/de/viaje123"})
        aviso = esperar_avisos(lambda a: len(a) == 1 and a[0]["cuerpo"].startswith("Beto"), "se junta con el de antes")[0]
        igual((aviso["cuerpo"], aviso["datos"]["url"]), ("Beto ha añadido «Maleta»\nAna ha añadido «Billetes»", "/de/viaje123"), "Dos avisos de la misma lista, en uno (lo nuevo arriba, con su enlace)")
        for i in range(5):  # Seguidos, sin esperar: no se pisan.
            empujar({**viaje, "linea": f"Ana ha añadido «Cosa {i}»", "url": "/de/viaje123"})
        aviso = esperar_avisos(lambda a: len(a) == 1 and a[0]["cuerpo"].startswith("Ana ha añadido «Cosa 4»"), "llegan cinco más")[0]
        igual(aviso["cuerpo"].split("\n"), [f"Ana ha añadido «Cosa {i}»" for i in (4, 3, 2, 1, 0)], "Hasta cinco líneas, las más nuevas")

        empujar({"titulo": "Cosas con Beto", "etiqueta": "lista-pan12345", "linea": "Beto ha añadido «Pan»", "url": "/con/pan12345"})
        esperar_avisos(lambda a: len(a) == 2, "el de otra lista va aparte")
        empujar("esto no es JSON")
        generico = [a for a in esperar_avisos(lambda a: len(a) == 3, "un aviso sin datos legibles") if a["etiqueta"] == "cosas"]
        igual([(a["titulo"], a["cuerpo"], a["datos"]["url"]) for a in generico], [("Cosas", "Hay cosas nuevas", "./")], "Aviso sin datos legibles: genérico, abre la app")
        for i, url in enumerate(("https://fuera.example/", "//fuera.example/x", "/\\fuera.example/x")):
            empujar({"titulo": "Otro", "etiqueta": f"lista-fuera{i}", "linea": "Algo", "url": url})
            fuera = [a for a in esperar_avisos(lambda a: len(a) == 4 + i, f"un aviso con un enlace de fuera ({url})") if a["etiqueta"] == f"lista-fuera{i}"]
            igual(fuera[0]["datos"]["url"], "./", f"Un enlace de fuera ({url}) no se abre: abre la app")
    finally:
        chrome.close()


# ----------------------------------------------------------------------------
# Recordar: el grupo fijo, sus recordatorios y sus hojas (lo que se ve y se hace sin la nube)
# ----------------------------------------------------------------------------

CLAVE_RECORDAR = "cosas:recordar"  # Los recordatorios ({ general, cosas, base, activar }): la clave no existe hasta que hay alguno.
ID_RECORDAR = "recordar"
# El grupo fijo tal como se guarda desde que tiene algo dentro: para lo guardado es un grupo más, y el más antiguo.
GRUPO_RECORDAR = {"id": ID_RECORDAR, "nombre": "Recordar", "color": None, "creada": 1, "abierto": False}
VACIO_RECORDAR = "Apunta aquí lo que quieras que te recuerde y ponle hora."
# Las notificaciones ya activadas en este dispositivo (con el permiso dado en JS_AVISOS_FALSOS): así Recordar no tiene nada que advertir.
AVISOS_ACTIVOS = {"uid": "anonimo-1", "endpoint": ENDPOINT_PRUEBA + "E1", "en": 1_700_000_000_000}
CON_PERMISO = "window.__avisos.permiso = 'granted';"
# Para lo que depende de «hoy» (el día que propone la hoja), un instante fijo: las 23:30 UTC del sábado 30 de enero de
# 2027, que en Madrid ya son las 0:30 del domingo 31. La app cuenta con la hora del móvil: es domingo y es día 31.
AHORA_FIJO = "2027-01-30T23:30:00Z"


def recordada(numero, texto, hecha=False, hecha_en=None):
    """Una cosa apuntada en Recordar."""
    return cosa(numero, texto, hecha=hecha, grupo=ID_RECORDAR, hecha_en=hecha_en)


def recordatorio(hora="09:00", frec="dia", **resto):
    """Un recordatorio con sus campos en el orden de siempre (hora, frec y, si toca, dia o diaMes)."""
    return {"hora": hora, "frec": frec, **resto}


def estado_recordar(cosas, grupos=(), **opciones):
    """Un estado de la versión 2 con Recordar ya guardado (tiene cosas dentro) y, si se dan, grupos propios."""
    return estado_con(cosas, grupos=list(grupos) + [dict(GRUPO_RECORDAR)], **opciones)


def recordar_guardado(general=None, cosas=None):
    """Lo que la app deja en CLAVE_RECORDAR sin haber hablado nunca con la función de avisos."""
    return {"general": general, "cosas": cosas or {}, "base": None, "activar": False}


def sembrar_recordar(e, datos, recordatorios=None, ruta="#grupo/recordar", contexto=None, avisos=None):
    """Como sembrar, con los recordatorios (CLAVE_RECORDAR) y, si se dan, las notificaciones de este dispositivo (CLAVE_AVISOS)."""
    contexto = contexto or e.contexto()
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(e.base + "icons/favicon-32.png")
    pares = [(clave, valor) for clave, valor in ((CLAVE, datos), (CLAVE_RECORDAR, recordatorios), (CLAVE_AVISOS, avisos)) if valor is not None]
    pagina.evaluate("(pares) => { for (const [clave, valor] of pares) localStorage.setItem(clave, valor); }",
                    [[clave, valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)] for clave, valor in pares])
    pagina.goto(e.base + ruta)
    return pagina


def contexto_con_avisos(e, guion=CON_PERMISO, **opciones):
    """Un contexto con el permiso y el push de mentira (JS_AVISOS_FALSOS); «guion» los deja como haga falta."""
    contexto = e.contexto(**opciones)
    contexto.add_init_script(JS_AVISOS_FALSOS + guion)
    return contexto


def contexto_con_reloj(e, **opciones):
    """Un contexto en la hora de Madrid, parado en AHORA_FIJO (los temporizadores siguen corriendo)."""
    contexto = e.contexto(timezone_id="Europe/Madrid", **opciones)
    contexto.clock.set_fixed_time(AHORA_FIJO)
    return contexto


def leer_recordar(pagina):
    return pagina.evaluate("(clave) => JSON.parse(localStorage.getItem(clave))", CLAVE_RECORDAR)


def recordar_en_bruto(pagina):
    """Lo guardado, letra a letra: el orden de los campos de un recordatorio también cuenta (es el de la función de avisos)."""
    return pagina.evaluate("(clave) => localStorage.getItem(clave)", CLAVE_RECORDAR)


def en_bruto(datos):
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":"))


def claves_guardadas(pagina):
    return pagina.evaluate("() => Object.keys(localStorage).sort()")


def acceso_recordar(pagina):
    """La fila fija de Recordar, sobre la lista."""
    return pagina.locator("#acceso-recordar")


def abrir_recordar(pagina):
    acceso_recordar(pagina).tap()
    esperar_vista(pagina, "grupo")
    pagina.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.


def boton_configurar(pagina):
    """La campana de la cabecera de Recordar (donde los demás grupos tienen el lápiz)."""
    return pagina.get_by_role("button", name="Configurar recordatorio", exact=True)


def fila_recordada(pagina, texto):
    return pagina.locator("#lista-grupo > li").filter(has=pagina.get_by_text(texto, exact=True))


def cuando_de(pagina, texto):
    """El recordatorio de esa cosa en su fila: el botón que dice cuándo avisa (o «Poner recordatorio»)."""
    return fila_recordada(pagina, texto).locator(".cuando")


def quitar_de_recordar(pagina, texto):
    return fila_recordada(pagina, texto).get_by_role("button", name="Quitar de Recordar", exact=True)


# La fila fija: qué es, qué dice, dónde está respecto a la cabecera y a la zona que se desplaza, y su forma.
JS_ACCESO_RECORDAR = r"""
() => {
  const a = document.querySelector('#acceso-recordar'), cs = getComputedStyle(a), zona = document.querySelector('#zona-lista');
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom, ancho: r.width, alto: r.height }; };
  const campana = a.querySelector('.recordar-campana'), nombre = a.querySelector('.recordar-nombre'), cuenta = a.querySelector('#cuenta-recordar'), flecha = a.querySelector('.recordar-flecha');
  const fila = document.querySelector('#lista-cosas > li.fila');
  return {
    etiqueta: a.tagName, destino: a.getAttribute('href'), padre: a.parentElement.id, enLaZona: zona.contains(a), visible: a.checkVisibility(),
    nombre: nombre.textContent, cuenta: cuenta.checkVisibility() ? cuenta.textContent : null, cuentaOculta: cuenta.getAttribute('aria-hidden'),
    dicho: a.getAttribute('aria-label'),
    caja: caja(a), radio: parseFloat(cs.borderTopLeftRadius), subrayado: cs.textDecorationLine,
    cabecera: caja(document.querySelector('#vista-lista header')), zona: caja(zona), desplazamiento: zona.scrollTop, ventana: [scrollX, scrollY],
    partes: [campana, nombre, cuenta, flecha].filter((el) => el.checkVisibility()).map((el) => el.getAttribute('class').split(' ')[0]),
    enOrden: [campana, nombre, cuenta, flecha].filter((el) => el.checkVisibility()).map((el) => el.getBoundingClientRect().left).every((x, i, xs) => i === 0 || x > xs[i - 1]),
    iconos: [campana, flecha].map((svg) => [svg.tagName, svg.getAttribute('aria-hidden')]),
    fila: fila ? { x: fila.getBoundingClientRect().left, d: fila.getBoundingClientRect().right, radio: parseFloat(getComputedStyle(fila).borderTopLeftRadius) } : null,
    foco: document.activeElement.id || document.activeElement.tagName,
  };
}
"""

# Las filas de la página de Recordar (sin las que se están yendo): texto, si está hecha, su recordatorio (lo que se
# lee y lo que oye un lector de pantalla) y el botón de la derecha que enseña («−» o la papelera).
JS_RECORDADAS = r"""
() => [...document.querySelectorAll('#lista-grupo > li')].filter((li) => !li.classList.contains('saliendo')).map((li) => {
  const cuando = li.querySelector('.cuando');
  return { texto: li.querySelector('.texto').textContent, hecha: li.classList.contains('hecha'), pulsado: li.querySelector('.hecho').getAttribute('aria-pressed'),
           cuando: cuando.textContent.trim(), dicho: cuando.getAttribute('aria-label'), sin: cuando.classList.contains('sin'),
           derecha: [...li.querySelectorAll('.borrar')].filter((b) => b.checkVisibility()).map((b) => b.getAttribute('aria-label')) };
})
"""


def acceso(pagina):
    return pagina.evaluate(JS_ACCESO_RECORDAR)


def recordadas(pagina):
    return pagina.evaluate(JS_RECORDADAS)


def cuandos(pagina):
    """Lo que dice el recordatorio de cada fila de Recordar, en orden: {texto: recordatorio}."""
    return {fila["texto"]: fila["cuando"] for fila in recordadas(pagina)}


def esperar_json(pagina, sonda, esperado, que, ms=4000):
    """Espera a que lo que devuelve «sonda» (JS) valga, campo a campo, lo esperado. Lo esperado viaja como JSON: de un argumento, Playwright pierde los None."""
    esperar(pagina, f"(esperado) => {{ const real = ({sonda})(); return Object.entries(JSON.parse(esperado)).every(([k, v]) => JSON.stringify(real[k]) === JSON.stringify(v)); }}",
            arg=json.dumps(esperado), ms=ms, que=f"{que}: {esperado}")


def esperar_recordadas(pagina, que, **esperado):
    """Espera a que las filas de Recordar tengan, en ese orden, esos valores (textos=[…], cuandos=[…], hechas=[…])."""
    sonda = f"() => {{ const filas = ({JS_RECORDADAS})(); return {{ textos: filas.map((f) => f.texto), cuandos: filas.map((f) => f.cuando), hechas: filas.map((f) => f.hecha) }}; }}"
    esperar_json(pagina, sonda, esperado, que)


def esperar_cuenta(pagina, cuenta, dicho):
    """La cuenta de la fila fija (None: sin número) y lo que la fila dice a un lector de pantalla."""
    esperar_json(pagina, JS_ACCESO_RECORDAR, {"cuenta": cuenta, "dicho": dicho}, "la cuenta de la fila de Recordar y lo que dice")


@prueba("hecha sin tachar: en la lista, en un grupo abierto, en la página de un grupo, en Recordar y en el desplegable «Añadir cosas» el texto de lo hecho no se tacha (ni nada de la vista) y sigue atenuado; al desmarcar vuelve a verse entero")
def t_sin_tachado(e):
    datos = estado_recordar([recordada(7, "Recordada hecha", hecha=True), recordada(6, "Recordada pendiente"), cosa(5, "En Casa hecha", hecha=True, grupo="g-9"),
                             cosa(4, "En Casa pendiente", grupo="g-9"), cosa(3, "Suelta hecha", hecha=True), cosa(2, "Suelta pendiente")],
                            grupos=[grupo(9, "Casa", abierto=True)])
    p = sembrar_recordar(e, datos, ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
    esperar_filas(p, 3)
    # Cada texto de la vista que se enseña: si va tachado y con qué opacidad; y cualquier otro elemento tachado.
    medir = """() => { const vista = [...document.querySelectorAll('section[id^=vista-]')].find((s) => s.checkVisibility());
        return { textos: Object.fromEntries([...vista.querySelectorAll('.fila .texto')].filter((t) => t.checkVisibility()).map((t) => { const cs = getComputedStyle(t);
                   return [t.textContent, [cs.textDecorationLine, parseFloat(cs.opacity)]]; })),
                 tachados: [...vista.querySelectorAll('*')].filter((el) => getComputedStyle(el).textDecorationLine.includes('line-through')).map((el) => el.className) }; }"""

    def comprobar_vista(donde, hechas, pendientes):
        m = p.evaluate(medir)
        igual(sorted(m["textos"]), sorted(hechas + pendientes), f"Textos de {donde}")
        igual(m["tachados"], [], f"Elementos tachados en {donde}")
        for texto in hechas:
            linea, opacidad = m["textos"][texto]
            comprobar(linea == "none" and 0.3 <= opacidad < 1, f"{donde}: «{texto}» (hecha) no se ve atenuada y sin tachar: {linea}, opacidad {opacidad}")
        for texto in pendientes:
            igual(m["textos"][texto], ["none", 1], f"{donde}: «{texto}» (pendiente) no se ve entera")

    comprobar_vista("la lista (con un grupo abierto)", ["Suelta hecha", "En Casa hecha"], ["Suelta pendiente", "En Casa pendiente", "Casa"])
    abrir_grupo(p, "Casa")
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable de «Casa»", desplegable=True, candidatas=["Suelta pendiente", "Suelta hecha"])
    comprobar_vista("la página de un grupo con el desplegable", ["En Casa hecha", "Suelta hecha"], ["En Casa pendiente", "Suelta pendiente"])
    p.go_back()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
    abrir_recordar(p)
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable de Recordar", desplegable=True, candidatas=["Suelta pendiente", "Suelta hecha"])
    comprobar_vista("Recordar con el desplegable", ["Recordada hecha", "Suelta hecha"], ["Recordada pendiente", "Suelta pendiente"])
    p.keyboard.press("Escape")
    # Marcar y desmarcar en Recordar: el check dice el estado; el texto, solo se atenúa.
    fila = fila_recordada(p, "Recordada pendiente")
    check_de(fila).tap()
    esperar(p, "(f) => f.querySelector('.hecho').getAttribute('aria-pressed') === 'true' && parseFloat(getComputedStyle(f.querySelector('.texto')).opacity) < 0.7",
            arg=fila.element_handle(), que="la cosa de Recordar queda marcada y su texto se atenúa")
    comprobar_vista("Recordar tras marcar", ["Recordada hecha", "Recordada pendiente"], [])
    check_de(fila).tap()
    esperar(p, "(f) => f.querySelector('.hecho').getAttribute('aria-pressed') === 'false' && getComputedStyle(f.querySelector('.texto')).opacity === '1'",
            arg=fila.element_handle(), que="la cosa de Recordar vuelve a pendiente y su texto se ve entero")
    comprobar_vista("Recordar tras desmarcar", ["Recordada hecha"], ["Recordada pendiente"])


@prueba("lista: «Recordar» es una fila fija entre la cabecera y la lista, fuera de la zona que se desplaza (con 60 cosas no se mueve), rectangular frente a las píldoras, con campana, nombre, cuenta y flecha; es un enlace a #grupo/recordar de 44 px o más y sigue ahí con la lista vacía")
def t_recordar_fila_fija(e):
    p = sembrar_recordar(e, estado_recordar([cosa(i, f"Cosa número {i}") for i in range(1, 61)] + [recordada(70, "Recordada"), recordada(71, "Otra recordada")]), ruta="#lista")
    esperar_filas(p, 60)
    a = acceso(p)
    igual((a["etiqueta"], a["destino"], a["padre"], a["enLaZona"], a["visible"]), ("A", "#grupo/recordar", "vista-lista", False, True), "La fila fija: un enlace de la vista, fuera de la zona que se desplaza")
    igual((a["nombre"], a["cuenta"], a["subrayado"]), ("Recordar", "2", "none"), "Lo que se lee en la fila fija")
    igual((a["partes"], a["enOrden"], a["iconos"]), (["recordar-campana", "recordar-nombre", "recordar-cuenta", "recordar-flecha"], True, [["svg", "true"], ["svg", "true"]]),
          "Campana, nombre, cuenta y flecha, de izquierda a derecha (los iconos, solo adorno)")
    igual(p.get_by_role("link", name="Recordar. 2 cosas pendientes", exact=True).count(), 1, "Enlaces con el nombre accesible de la fila fija")
    caja, cabecera, zona, fila = a["caja"], a["cabecera"], a["zona"], a["fila"]
    comprobar(cabecera["b"] <= caja["y"] <= cabecera["b"] + 24 and caja["b"] <= zona["y"] + 0.5, f"La fila fija no va entre la cabecera y la zona de la lista: {cabecera} / {caja} / {zona}")
    comprobar(abs(caja["x"] - fila["x"]) <= 0.5 and abs(caja["d"] - fila["d"]) <= 0.5, f"La fila fija no tiene el ancho de las filas de la lista: {caja} / {fila}")
    comprobar(caja["alto"] >= 44, f"La fila fija mide menos de 44 px de alto: {caja}")
    comprobar(4 <= a["radio"] <= 16 and a["radio"] * 2 <= fila["radio"] and a["radio"] <= caja["alto"] / 4,
              f"La fila fija no es rectangular frente a las píldoras de la lista: radio {a['radio']} (una fila, {fila['radio']}; alto {caja['alto']})")
    # No es parte de la lista: ni una fila suya ni se desplaza con ella.
    igual(p.evaluate("() => document.querySelector('#lista-cosas').contains(document.querySelector('#acceso-recordar'))"), False, "La fila fija está dentro de la lista")
    final = p.evaluate("() => { const z = document.querySelector('#zona-lista'); z.scrollTop = z.scrollHeight; return z.scrollTop; }")
    comprobar(final > 1000, f"La lista de 60 cosas no se ha desplazado hasta el final: {final}")
    p.wait_for_timeout(150)
    b = acceso(p)
    igual((b["caja"], b["cabecera"], b["ventana"]), (caja, cabecera, [0, 0]), "Con la lista desplazada hasta el final, la fila fija (o la cabecera) se ha movido")
    ultima = filas(p).last.bounding_box()
    comprobar(ultima["y"] > caja["b"], f"Al final de la lista, la última cosa queda bajo la fila fija: {ultima} / {caja}")
    # Con el teclado: tras «Volver», la fila fija; con Intro se abre.
    p.get_by_role("button", name="Volver", exact=True).focus()
    p.keyboard.press("Tab")
    anillo = p.evaluate("() => { const cs = getComputedStyle(document.activeElement); return [document.activeElement.id, cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2]; }")
    igual(anillo, ["acceso-recordar", True], "Tras «Volver», el tabulador no llega a la fila fija con su anillo de foco")
    # Con la lista vacía (y sin nada en Recordar) sigue ahí, sin cuenta, sobre «No tienes cosas apuntadas.».
    p2 = e.pagina(ruta="#lista")
    esperar_vista(p2, "lista")
    vacia = acceso(p2)
    igual((vacia["visible"], vacia["nombre"], vacia["cuenta"], vacia["dicho"], vacia["partes"]), (True, "Recordar", None, None, ["recordar-campana", "recordar-nombre", "recordar-flecha"]),
          "La fila fija con la lista vacía")
    igual(p2.get_by_role("link", name="Recordar", exact=True).count(), 1, "Sin nada pendiente, la fila fija se llama «Recordar»")
    # (La nota ocupa toda la vista y centra su texto: se mide la tinta.)
    nota = p2.get_by_text("No tienes cosas apuntadas.", exact=True).evaluate("(el) => { const r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect().top; }")
    comprobar(nota >= vacia["caja"]["b"], f"La nota de lista vacía no queda bajo la fila fija: {nota} / {vacia['caja']}")
    igual(claves_guardadas(p2), [], "Enseñar la fila fija guarda algo")


@prueba("fila fija: cuenta lo PENDIENTE de Recordar (sin número con 0; «Una cosa pendiente» y «N cosas pendientes» para un lector de pantalla) y se pone al día al apuntar, marcar, desmarcar, quitar, eliminar y con lo que cambia otra pestaña")
def t_recordar_cuenta(e):
    contexto = e.contexto()
    p = sembrar_recordar(e, estado_recordar([recordada(4, "Pendiente nueva"), recordada(3, "Pendiente vieja"), recordada(2, "Hecha", hecha=True), cosa(1, "Suelta")]), ruta="#lista", contexto=contexto)
    esperar_filas(p, 1)
    a = acceso(p)
    igual((a["cuenta"], a["dicho"], a["cuentaOculta"]), ("2", "Recordar. 2 cosas pendientes", "true"), "La cuenta al abrir la lista (las hechas no cuentan; el número lo dice el nombre accesible)")
    # En esta misma pestaña: apuntar en Recordar y volver.
    abrir_recordar(p)
    campo_en_grupo(p).fill("Apuntada aquí")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    esperar_cuenta(p, "3", "Recordar. 3 cosas pendientes")
    # Desde otra pestaña, con la lista a la vista en esta: cada cambio llega a la cuenta.
    otra = e.pagina(ruta="#grupo/recordar", contexto=contexto)
    esperar_vista(otra, "grupo")
    check = check_de(fila_recordada(otra, "Apuntada aquí"))
    check.tap()
    esperar_cuenta(p, "2", "Recordar. 2 cosas pendientes")
    check.tap()
    esperar_cuenta(p, "3", "Recordar. 3 cosas pendientes")
    quitar_de_recordar(otra, "Apuntada aquí").tap()
    esperar_toast(otra, "Quitado de Recordar")
    esperar_cuenta(p, "2", "Recordar. 2 cosas pendientes")
    esperar_nivel(p, ["Apuntada aquí", "Suelta"], "la cosa quitada de Recordar vuelve a la lista de la otra pestaña")
    otra.wait_for_timeout(400)  # La fila vecina ha subido bajo el dedo: pasa la guarda.
    check_de(fila_recordada(otra, "Pendiente nueva")).tap()
    esperar_cuenta(p, "1", "Recordar. Una cosa pendiente")
    borrar_de(fila_recordada(otra, "Hecha")).tap()  # Una hecha no contaba: eliminarla no cambia la cuenta.
    esperar_toast(otra, "Eliminado")
    otra.wait_for_timeout(400)
    quitar_de_recordar(otra, "Pendiente vieja").tap()
    esperar_cuenta(p, None, None)
    igual(p.get_by_role("link", name="Recordar", exact=True).count(), 1, "Sin nada pendiente, la fila fija vuelve a llamarse «Recordar»")
    igual(sorted(c["texto"] for c in leer_estado(p)["cosas"] if c["grupo"] == ID_RECORDAR), ["Pendiente nueva"], "Lo que queda en Recordar (una hecha: no cuenta)")
    # La pantalla principal no sabe nada de Recordar.
    volver(p)
    igual(p.evaluate("() => document.querySelector('#vista-inicio').contains(document.querySelector('#acceso-recordar'))"), False, "La fila fija sale en la pantalla principal")


# Contrastes de la fila fija (en la lista) y de lo propio de la página de Recordar: la campana, los recordatorios de las filas y la nota.
JS_ASPECTO_RECORDAR = "() => {" + JS_UTIL + r"""
  const q = (s) => document.querySelector(s);
  const a = q('#acceso-recordar'), cuenta = q('#cuenta-recordar'), fila = q('#lista-cosas > li.fila'), grupo = q('#lista-cosas .enlace-grupo');
  if (a.checkVisibility()) {
    const disco = mezclar(fondoEfectivo(a), parsear(getComputedStyle(cuenta).backgroundColor));
    return {
      tono: document.documentElement.dataset.tono,
      texto: { 'nombre de la fila fija': contrasteTexto(a.querySelector('.recordar-nombre')), 'número de la cuenta': contraste(parsear(getComputedStyle(cuenta).color), disco) },
      iconos: { 'campana de la fila fija': contrasteIcono(a.querySelector('.recordar-campana')), 'flecha de la fila fija': contrasteIcono(a.querySelector('.recordar-flecha')),
                'disco de la cuenta sobre la fila': contraste(disco, fondoEfectivo(a)) },
      superficies: { 'la página': contraste(fondoEfectivo(a), fondoEfectivo(q('#vista-lista'))), 'una fila de la lista': contraste(fondoEfectivo(a), fondoEfectivo(fila)) },
      comoUnGrupo: [contrasteTexto(a.querySelector('.recordar-nombre')), contrasteTexto(grupo)].map((v) => Math.round(v * 100) / 100),
      fondo: redondear(fondoEfectivo(a)), punto: [a.getBoundingClientRect().left + 6, a.getBoundingClientRect().top + a.getBoundingClientRect().height / 2],
    };
  }
  const con = q('#lista-grupo .cuando:not(.sin)'), sin = q('#lista-grupo .cuando.sin'), hecha = q('#lista-grupo > li.hecha .cuando');
  return {
    tono: q('#vista-grupo').dataset.tono,
    texto: { 'recordatorio puesto': contrasteTexto(con.querySelector('.cuando-texto')), '«Poner recordatorio»': contrasteTexto(sin.querySelector('.cuando-texto')),
             'nota de Recordar': contrasteTexto(q('#texto-nota-recordar')), 'título de Recordar': contrasteTexto(q('#titulo-grupo')) },
    iconos: { 'campana de la cabecera': contrasteIcono(q('#configurar-recordar svg')), 'campana de un recordatorio puesto': contrasteIcono(con.querySelector('svg')),
              'campana de «Poner recordatorio»': contrasteIcono(sin.querySelector('svg')), '«−» de una pendiente': contrasteIcono(q('#lista-grupo .quitar svg')),
              'papelera de una hecha': contrasteIcono(q('#lista-grupo > li.hecha [data-accion=eliminar] svg')),
              'contorno de «Poner recordatorio»': contraste(parsear(getComputedStyle(sin).color), fondoEfectivo(sin)) },
    atenuado: parseFloat(getComputedStyle(hecha).opacity), contorno: getComputedStyle(sin).backgroundColor,
  };
}"""


@prueba("Recordar se lee sobre cualquier color: la fila fija, como la de un grupo (nombre y cuenta ≥ 4,5, campana y flecha ≥ 3, superficie distinta de la página y de una fila, píxeles reales) en Azul, Blanco, Amarillo y Negro; en su página, recordatorios, nota y título ≥ 4,5 e iconos ≥ 3 en las 12 muestras")
def t_recordar_aspecto(e):
    datos = [recordada(4, "Con recordatorio"), recordada(3, "Sin recordatorio"), recordada(2, "Hecha con recordatorio", hecha=True), cosa(1, "Suelta")]
    recordatorios = recordar_guardado(cosas={"id-4": recordatorio(), "id-2": recordatorio("21:30", "semana", dia=2)})
    fallos = []
    for nombre, color in PALETA:
        p = sembrar_recordar(e, estado_recordar(datos, grupos=[grupo(9, "Grupo")], color=color), recordatorios, ruta="#lista", contexto=e.contexto(reduced_motion="reduce"))
        esperar_filas(p, 2)
        lista = p.evaluate(JS_ASPECTO_RECORDAR)
        # La fila fija lleva la superficie y la tinta de la fila de un grupo: se lee, como mínimo, igual que ella, en todos los colores.
        igual(lista["comoUnGrupo"][0], lista["comoUnGrupo"][1], f"{nombre}: el nombre de la fila fija no se lee como el de un grupo")
        [pixel] = muestrear(p, [tuple(lista["punto"])])
        comprobar(all(abs(a - b) <= 3 for a, b in zip(pixel, lista["fondo"])), f"{nombre}: la fila fija no se pinta con su superficie: {pixel} vs {lista['fondo']}")
        abrir_recordar(p)
        pagina = p.evaluate(JS_ASPECTO_RECORDAR)
        igual(pagina["tono"], lista["tono"], f"{nombre}: Recordar no tiene color propio (su página lleva el tono de la app)")
        igual(pagina["contorno"], "rgba(0, 0, 0, 0)", f"{nombre}: «Poner recordatorio» no es un botón de contorno (una acción, no un estado)")
        comprobar(0.3 <= pagina["atenuado"] < 1, f"{nombre}: el recordatorio de una cosa hecha no se atenúa con su texto: {pagina['atenuado']}")
        # La fila fija se mide en los mismos cuatro colores que la fila de un grupo (t_fila_grupo_aspecto): un tono oscuro, dos claros y el negro.
        medidas = [pagina] + ([lista] if nombre in ("Azul", "Blanco", "Amarillo", "Negro") else [])
        for m in medidas:
            for conjunto, minimo in (("texto", 4.5), ("iconos", 3.0), ("superficies", 1.15)):
                for que, valor in m.get(conjunto, {}).items():
                    detalle(f"{nombre} {color} · {que}: {valor:.2f}")
                    if valor < minimo:
                        fallos.append(f"{nombre} {color}: {que} = {valor:.2f}:1 (mínimo {minimo})")
        p.context.close()
    comprobar(not fallos, "Contraste insuficiente en Recordar:\n          " + "\n          ".join(fallos))


@prueba("Recordar no es un grupo de quien usa la app: no sale entre los grupos de la lista (ni sus cosas entre las sueltas), no tiene ojo, papelera ni lápiz, sus cosas no se ofrecen a otros grupos ni las de otros a él, lo que una versión anterior le cambiara (nombre, color) no cuenta, y un grupo propio llamado «Recordar» convive con él y se borra sin tocarlo")
def t_recordar_no_es_un_grupo(e):
    p = sembrar_recordar(e, estado_recordar([recordada(4, "En Recordar"), cosa(3, "Suelta"), cosa(2, "En Casa", grupo="g-5")], grupos=[grupo(5, "Casa")]), ruta="#lista")
    esperar_filas(p, 2)
    igual(textos_nivel(p), ["Casa", "Suelta"], "Recordar (o una cosa suya) sale en el nivel superior de la lista")
    en_la_lista = p.evaluate("""() => { const lista = document.querySelector('#lista-cosas');
        return [lista.querySelectorAll('[data-id=recordar]').length, lista.querySelectorAll('.ojo').length, lista.querySelectorAll('[data-accion=eliminar-grupo]').length, lista.textContent.includes('En Recordar')]; }""")
    igual(en_la_lista, [0, 1, 1, False], "En la lista, filas de Recordar, ojos, papeleras de grupo (solo los de «Casa») y cosas de Recordar")
    # Sus cosas no se ofrecen a otro grupo, ni a él las de otro: solo las sueltas.
    abrir_grupo(p, "Casa")
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable de «Casa»", desplegable=True, candidatas=["Suelta"], miembros=["En Casa"])
    p.go_back()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    abrir_recordar(p)
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable de Recordar", desplegable=True, candidatas=["Suelta"], miembros=["En Recordar"], titulo="Recordar")
    p.keyboard.press("Escape")
    # Ni nombre ni color que editar: sin lápiz, y la hoja de edición no se abre de ninguna forma.
    igual((boton_editar_grupo(p).count(), p.locator("#editar-grupo").is_hidden(), boton_configurar(p).count(), p.locator("#hoja-grupo").is_hidden()), (0, True, 1, True), "Lápiz, campana y hoja de edición en Recordar")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    # Un grupo propio con el mismo nombre: es otro grupo (otro id), con su ojo, su papelera y su lápiz.
    crear_grupo(p, "Recordar")
    esperar_toast(p, "Grupo creado")
    esperar_nivel(p, ["Recordar", "Casa", "Suelta"], "el grupo propio «Recordar» aparece en la lista")
    grupos = leer_estado(p)["grupos"]
    igual(([g["nombre"] for g in grupos], grupos[1]["id"], grupos[2]), (["Recordar", "Casa", "Recordar"], "g-5", GRUPO_RECORDAR), "Grupos guardados: el propio (el más nuevo), «Casa» y el fijo")
    propio = grupos[0]["id"]
    comprobar(re.fullmatch(r"[0-9a-f-]{36}", propio), f"El grupo propio «Recordar» no tiene un id suyo: {propio!r}")
    igual((p.get_by_role("link", name="Recordar", exact=True).get_attribute("href"), p.get_by_role("link", name="Recordar. Una cosa pendiente", exact=True).get_attribute("href")),
          (f"#grupo/{propio}", "#grupo/recordar"), "Los dos enlaces: el del grupo propio y el de la fila fija")
    p.wait_for_timeout(400)  # El botón de crear grupos acaba de reaparecer: pasa su guarda.
    abrir_grupo(p, "Recordar")
    comprobar(p.url.endswith(f"#grupo/{propio}"), f"El grupo propio no abre su página: {p.url}")
    igual((pagina_grupo(p)["titulo"], boton_editar_grupo(p).count(), boton_configurar(p).count(), p.locator("#vista-grupo").get_attribute("data-recordar"), p.locator("#grupo-vacio").text_content()),
          ("Recordar", 1, 0, None, "Este grupo está vacío."), "La página del grupo propio «Recordar» es la de un grupo cualquiera")
    campo_en_grupo(p).fill("En el mío")
    campo_en_grupo(p).press("Enter")
    esperar_pagina_grupo(p, "lo escrito en el grupo propio", miembros=["En el mío"])
    igual(p.locator("#lista-grupo .cuando").count(), 0, "Las cosas del grupo propio llevan recordatorio")
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "En el mío"), propio, "Lo escrito en el grupo propio no entra en él")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    borrar_grupo_de(fila_grupo(p, "Recordar")).tap()
    esperar_toast(p, "Eliminado")
    esperar_nivel(p, ["Casa", "En el mío", "Suelta"], "al borrar el grupo propio, su cosa vuelve a la lista")
    guardado = leer_estado(p)
    igual(([g["id"] for g in guardado["grupos"]], next(c["grupo"] for c in guardado["cosas"] if c["texto"] == "En Recordar"), acceso(p)["cuenta"]), (["g-5", ID_RECORDAR], ID_RECORDAR, "1"),
          "Borrar el grupo propio «Recordar» toca al fijo o a lo que tiene dentro")
    # Una versión anterior lo enseña como un grupo más y deja cambiarle el nombre y el color: aquí sigue siendo Recordar, sin color.
    cambiado = {"id": ID_RECORDAR, "nombre": "Mis avisos", "color": "#F2B705", "creada": 1_700_000_000_099, "abierto": True}
    p2 = sembrar(e, estado_con([recordada(1, "Dentro")], grupos=[cambiado]), ruta="#lista")
    esperar_vista(p2, "lista")
    igual((textos_nivel(p2), acceso(p2)["nombre"], acceso(p2)["cuenta"]), ([], "Recordar", "1"), "Un Recordar renombrado por una versión anterior sale en la lista o cambia de nombre")
    abrir_recordar(p2)
    estado = pagina_grupo(p2)
    igual((estado["titulo"], estado["miembros"], estado["tono"], estado["tema"]), ("Recordar", ["Dentro"], "oscuro", COLOR_DEFECTO), "Recordar no toma el nombre ni el color que le puso una versión anterior")
    igual(set(muestrear(p2, [(2, 2), (e.ancho - 4, e.alto - 4)])), {hex_a_rgb(COLOR_DEFECTO)}, "Píxeles reales de la página de Recordar (el color de la app)")
    check_de(fila_recordada(p2, "Dentro")).tap()
    esperar(p2, "(clave) => JSON.parse(localStorage.getItem(clave)).cosas[0].hecha", arg=CLAVE, que="se guarda la cosa marcada")
    igual(leer_estado(p2)["grupos"], [GRUPO_RECORDAR], "Al guardar, Recordar vuelve a ser el de siempre")


@prueba("Recordar se abre desde su fila fija (#grupo/recordar, foco al título, una entrada de historial) y «Volver», atrás del sistema e Intro devuelven a la lista donde estaba, con el foco en la fila fija; un doble toque en la fila (sobre la campana, la flecha o su borde inferior) solo la abre: no marca, quita ni configura la primera cosa; y la carga directa en un perfil nuevo funciona sin guardar nada")
def t_recordar_navegacion(e):
    p = sembrar_recordar(e, estado_recordar([cosa(i, f"Cosa número {i}") for i in range(1, 61)] + [recordada(70, "Primera de Recordar")]), ruta="#lista")
    esperar_filas(p, 60)
    desplazada = p.evaluate("() => { const z = document.querySelector('#zona-lista'); z.scrollTop = 700; return z.scrollTop; }")
    igual(desplazada, 700, "La lista de 60 cosas no se deja desplazar 700 px")
    antes = p.evaluate("() => history.length")
    acceso_recordar(p).tap()
    esperar_vista(p, "grupo")
    comprobar(p.url.endswith("#grupo/recordar"), f"La URL no lleva #grupo/recordar: {p.url}")
    estado = pagina_grupo(p)
    igual((estado["titulo"], estado["foco"], estado["miembros"], p.evaluate("() => history.length") - antes), ("Recordar", "titulo-grupo", ["Primera de Recordar"], 1), "Recordar recién abierto: título, foco, cosas y entradas de historial")
    ocultas = p.evaluate("() => ['inicio', 'lista', 'ajustes'].map((v) => { const el = document.querySelector('#vista-' + v); return el.hidden && el.hasAttribute('inert'); })")
    igual(ocultas, [True, True, True], "Las otras vistas no quedan ocultas e inertes en la página de Recordar")
    # De vuelta (con «Volver», con atrás del sistema tras abrir con Intro): la lista está donde estaba y el foco, en la fila fija.
    for como in ("volver", "atras"):
        p.wait_for_timeout(400)
        if como == "volver":
            p.get_by_role("button", name="Volver", exact=True).tap()
        else:
            p.keyboard.press("Enter")  # La fila fija conserva el foco desde la vuelta anterior.
            esperar_vista(p, "grupo")
            p.wait_for_timeout(400)
            p.go_back()
        esperar_vista(p, "lista")
        a = acceso(p)
        igual((a["foco"], a["desplazamiento"], p.url.endswith("#lista"), p.evaluate("() => history.length") - antes), ("acceso-recordar", 700, True, 1),
              f"[{como}] Al volver de Recordar: foco, desplazamiento de la lista, URL y entradas de historial")
    # Toda la fila es el enlace, y bajo ella aparecen el check, el «−» y el recordatorio de la primera cosa: el segundo toque de un doble toque no los acciona.
    p.wait_for_timeout(400)  # La lista acaba de entrar: se mide con su animación terminada.
    caja = acceso_recordar(p).bounding_box()
    medio = caja["y"] + caja["height"] / 2
    for donde, x, y, pausas in (("la campana", caja["x"] + 30, medio, (0, 150, 250)), ("la flecha", caja["x"] + caja["width"] - 30, medio, (150,)),
                                ("el borde inferior", caja["x"] + 100, caja["y"] + caja["height"] - 4, (150,))):
        for pausa in pausas:
            p.touchscreen.tap(x, y)
            if pausa:
                p.wait_for_timeout(pausa)
            p.touchscreen.tap(x, y)
            p.wait_for_timeout(700)
            unica = next(c for c in leer_estado(p)["cosas"] if c["texto"] == "Primera de Recordar")
            igual((vista_visible(p), unica["hecha"], unica["grupo"], p.locator("#hoja-recordar").is_hidden(), (toast(p) or {}).get("texto") or "", p.evaluate("() => history.length") - antes),
                  ("grupo", False, ID_RECORDAR, True, "", 1), f"Tras un doble toque ({pausa} ms) sobre {donde} de la fila fija: vista, la primera cosa (hecha, grupo), hoja, aviso e historial")
            p.go_back()
            esperar_vista(p, "lista")
            p.wait_for_timeout(400)
    # Pasada la guarda, lo que hay bajo el dedo responde.
    abrir_recordar(p)
    check_de(fila_recordada(p, "Primera de Recordar")).tap()
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).cosas.find((c) => c.texto === 'Primera de Recordar').hecha", arg=CLAVE, que="pasada la guarda, el check de la primera cosa responde")
    # Carga directa en un perfil nuevo: Recordar existe sin haberse guardado nunca, y atrás recorre la app (lista, inicio).
    p2 = e.pagina(ruta="#grupo/recordar")
    esperar_vista(p2, "grupo")
    estado = pagina_grupo(p2)
    igual((estado["titulo"], estado["foco"], estado["miembros"], estado["vacio"], boton_configurar(p2).count()), ("Recordar", "titulo-grupo", [], True, 1), "Carga directa de #grupo/recordar en un perfil nuevo")
    igual(claves_guardadas(p2), [], "Abrir Recordar guarda algo")
    p2.reload()
    esperar_vista(p2, "grupo")
    p2.go_back()
    esperar_vista(p2, "lista")
    comprobar(p2.url.endswith("#lista"), f"Atrás desde la carga directa de Recordar: {p2.url}")
    p2.go_back()
    esperar_vista(p2, "inicio")
    igual(p2.url, e.base, "Un segundo «atrás» desde la carga directa de Recordar no llega a la pantalla principal")
    p2.go_forward()
    esperar_vista(p2, "lista")
    p2.wait_for_timeout(400)
    acceso_recordar(p2).tap()  # La nota de lista vacía, que ocupa toda la vista, no estorba al toque.
    esperar_vista(p2, "grupo")
    igual(claves_guardadas(p2), [], "Ir y venir de un Recordar vacío guarda algo")


@prueba("página de Recordar: «Volver», el título y, donde los demás grupos llevan el lápiz, una campana redonda de 48 px («Configurar recordatorio»); vacía, su propia nota centrada; cada fila, check, texto y «−» (o papelera, si está hecha) con su recordatorio debajo, alineado con el texto, último en el orden del tabulador y con 44 px de alto para el dedo; un grupo cualquiera abierto después recupera su lápiz y sus filas de siempre")
def t_recordar_pagina(e):
    p = sembrar_recordar(e, estado_con([cosa(2, "Suelta"), cosa(1, "En Casa", grupo="g-5")], grupos=[grupo(5, "Casa")]))
    esperar_vista(p, "grupo")
    igual((p.locator("#vista-grupo").get_attribute("data-recordar"), p.locator("#vista-grupo h1").text_content()), ("", "Recordar"), "La vista de grupo, en Recordar")
    volver_b, campana = p.get_by_role("button", name="Volver", exact=True), boton_configurar(p)
    caja_v, caja_c, titulo = volver_b.bounding_box(), campana.bounding_box(), p.locator("#titulo-grupo").bounding_box()
    comprobar(caja_v["x"] < e.ancho / 2 < caja_c["x"] and caja_v["y"] <= 40 and abs(caja_v["y"] - caja_c["y"]) <= 1, f"Volver y la campana no están arriba a cada lado: {caja_v} {caja_c}")
    igual((round(caja_c["width"]), round(caja_c["height"]), campana.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius"), campana.locator("svg").count(), campana.get_attribute("type")),
          (48, 48, "50%", 1, "button"), "La campana de la cabecera")
    comprobar(caja_v["x"] + caja_v["width"] <= titulo["x"] and titulo["x"] + titulo["width"] <= caja_c["x"], "El título no queda entre «Volver» y la campana")
    igual((boton_editar_grupo(p).count(), p.locator("#editar-grupo").is_hidden()), (0, True), "El lápiz en Recordar")
    volver_b.focus()
    p.keyboard.press("Tab")
    igual(p.evaluate("() => document.activeElement.id"), "configurar-recordar", "Tras «Volver», el tabulador no llega a la campana (el lápiz escondido no cuenta)")
    # Vacía: su nota, centrada entre la cabecera y la barra; abajo, «Añadir cosas» y la barra de escribir, como en cualquier grupo.
    estado = pagina_grupo(p)
    igual((estado["miembros"], estado["vacio"], estado["centrado"], p.locator("#grupo-vacio").text_content()), ([], True, True, VACIO_RECORDAR), "Recordar vacío")
    nota, m = p.get_by_text(VACIO_RECORDAR, exact=True).bounding_box(), barra_anadir(p)
    arriba, abajo, centro = m["cabecera"]["b"], m["pildora"]["y"], nota["y"] + nota["height"] / 2
    comprobar(arriba + (abajo - arriba) * 0.35 <= centro <= arriba + (abajo - arriba) * 0.65 and abs(nota["x"] + nota["width"] / 2 - e.ancho / 2) <= 3,
              f"La nota de Recordar vacío no está centrada entre la cabecera y la barra: {nota} en [{arriba}, {abajo}]")
    comprobar(0 <= e.alto - m["escribir"]["b"] <= 48 and 0 <= m["escribir"]["y"] - m["pildora"]["b"] <= 16, f"«Añadir cosas» y la barra de escribir no están abajo: {m['pildora']} / {m['escribir']}")
    comprobar(not p.locator("#nota-recordar").is_visible(), "Sin nada que recordar, sale la nota de las notificaciones")
    p.context.close()

    # Con cosas: la disposición de cada fila.
    datos = estado_recordar([recordada(3, "Llamar al dentista"), recordada(2, "Sacar la basura"), recordada(1, "Regar las plantas", hecha=True)])
    p = sembrar_recordar(e, datos, recordar_guardado(cosas={"id-3": recordatorio()}), contexto=contexto_con_avisos(e), avisos=AVISOS_ACTIVOS)
    esperar_vista(p, "grupo")
    igual([(f["texto"], f["cuando"], f["derecha"]) for f in recordadas(p)],
          [("Llamar al dentista", "9:00 · Cada día", ["Quitar de Recordar"]), ("Sacar la basura", "Poner recordatorio", ["Quitar de Recordar"]), ("Regar las plantas", "Poner recordatorio", ["Eliminar"])],
          "Filas de Recordar: pendientes con «−», hechas con papelera, y cada una con su recordatorio")
    comprobar(not p.locator("#nota-recordar").is_visible(), "Con las notificaciones activadas, la nota sale igualmente")
    m = barra_anadir(p)
    primera = p.locator("#lista-grupo > li").first.bounding_box()
    comprobar(m["cabecera"]["b"] <= primera["y"] <= m["cabecera"]["b"] + 40, f"La primera cosa no empieza justo bajo la cabecera: {primera} / {m['cabecera']}")
    medir = """(f) => { const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom, ancho: r.width, alto: r.height }; };
        const cuando = f.querySelector('.cuando'), boton = [...f.querySelectorAll('.borrar')].find((b) => b.checkVisibility()), c = cuando.getBoundingClientRect();
        const dedo = (y) => { const el = document.elementFromPoint(c.left + c.width / 2, y); return el === cuando || cuando.contains(el); };
        return { fila: caja(f), check: caja(f.querySelector('.hecho')), texto: caja(f.querySelector('.texto')), boton: caja(boton), cuando: caja(cuando),
                 radios: [f.querySelector('.hecho'), boton].map((b) => getComputedStyle(b).borderTopLeftRadius), iconos: [f.querySelector('.hecho'), boton, cuando].map((b) => b.querySelectorAll('svg[aria-hidden=true]').length),
                 ultimo: f.lastElementChild === cuando, tipo: cuando.type, pildora: parseFloat(getComputedStyle(cuando).borderTopLeftRadius) >= c.height / 2 - 0.5, fuente: parseFloat(getComputedStyle(cuando).fontSize),
                 dedo: [dedo(c.top + c.height / 2 - 21), dedo(c.top + c.height / 2 + 21)], desborde: f.scrollWidth - f.clientWidth }; }"""
    for fila in p.locator("#lista-grupo > li").all():
        d = fila.evaluate(medir)
        check, texto, boton, cuando, caja = d["check"], d["texto"], d["boton"], d["cuando"], d["fila"]
        comprobar(check["d"] <= texto["x"] and texto["d"] <= boton["x"] + 0.5 and boton["d"] <= caja["d"] and abs(check["y"] - boton["y"]) <= 0.5,
                  f"La fila no lleva el check a la izquierda, el texto y el botón a la derecha, en una línea: {d}")
        igual((round(check["ancho"]), round(check["alto"]), round(boton["ancho"]), round(boton["alto"]), d["radios"], d["iconos"]), (44, 44, 44, 44, ["50%", "50%"], [1, 1, 1]), "Check y botón de la derecha: redondos de 44 px con su icono (y el del recordatorio)")
        comprobar(cuando["y"] >= max(check["b"], texto["b"]) and abs(cuando["x"] - texto["x"]) <= 1 and cuando["d"] <= caja["d"] - 8 and cuando["b"] <= caja["b"],
                  f"El recordatorio no ocupa su propia línea bajo el texto, alineado con él y dentro de la fila: {d}")
        igual((d["ultimo"], d["tipo"], d["pildora"], d["dedo"], d["desborde"] <= 0), (True, "button", True, [True, True], True), "El recordatorio: el último de la fila, un botón en píldora con 44 px de alto para el dedo, sin desbordar")
        comprobar(cuando["alto"] >= 32 and d["fuente"] >= 14, f"El recordatorio es demasiado pequeño para leerse: {d}")
    # El orden del tabulador sigue al de la vista: check, «−» y, el último, el recordatorio; después, la fila siguiente.
    check_de(fila_recordada(p, "Llamar al dentista")).focus()
    orden = []
    for _ in range(4):
        p.keyboard.press("Tab")
        orden.append(p.evaluate("() => [document.activeElement.getAttribute('aria-label'), document.activeElement.closest('li').querySelector('.texto').textContent]"))
    igual(orden, [["Quitar de Recordar", "Llamar al dentista"], ["Recordatorio: cada día a las 9:00. Cambiar", "Llamar al dentista"], ["Marcar como hecha", "Sacar la basura"], ["Quitar de Recordar", "Sacar la basura"]],
          "Orden de tabulación en las filas de Recordar")
    # Un grupo cualquiera abierto después: su lápiz, su nota y sus filas de siempre; y de vuelta en Recordar, otra vez la campana.
    p2 = sembrar_recordar(e, estado_recordar([recordada(3, "Recordada"), cosa(2, "En Casa", grupo="g-5")], grupos=[grupo(5, "Casa"), grupo(4, "Vacío")]), ruta="#lista")
    esperar_filas(p2, 2)
    for nombre, lapiz, vacio in (("Recordar", False, None), ("Casa", True, None), ("Vacío", True, "Este grupo está vacío."), ("Recordar", False, None)):
        if nombre == "Recordar":
            abrir_recordar(p2)
        else:
            abrir_grupo(p2, nombre)
        igual((boton_editar_grupo(p2).count(), boton_configurar(p2).count(), p2.locator("#vista-grupo").get_attribute("data-recordar"), p2.locator("#lista-grupo .cuando").count() > 0),
              (1, 0, None, False) if lapiz else (0, 1, "", True), f"«{nombre}»: lápiz, campana, marca de Recordar y recordatorios en las filas")
        if vacio:
            igual((p2.locator("#grupo-vacio").is_visible(), p2.locator("#grupo-vacio").text_content()), (True, vacio), f"«{nombre}»: la nota de grupo vacío")
        if nombre == "Casa":
            igual((quitar_de(p2, "En Casa").count(), borrar_de(p2.locator("#lista-grupo > li").first).count()), (1, 0), "Las filas de un grupo cualquiera: «Quitar del grupo» y sin papelera")
        p2.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p2, "lista")
        p2.wait_for_timeout(400)


AHORA_FIJO_MS = 1_801_351_800_000  # AHORA_FIJO, en milisegundos (lo que da Date.now() con el reloj parado).


@prueba("escribir en Recordar: lo escrito o dictado en su barra entra en Recordar, arriba, con «Guardado»; se guarda como una cosa más (con grupo «recordar») y el grupo fijo pasa a guardarse en ese momento, no antes; la clave de los recordatorios no nace hasta que hay alguno")
def t_recordar_escribir(e):
    contexto = contexto_con_reloj(e)
    contexto.add_init_script(JS_DICTADO_FALSO)
    p = e.pagina(ruta="#grupo/recordar", contexto=contexto)
    esperar_vista(p, "grupo")
    igual(claves_guardadas(p), [], "Claves guardadas antes de apuntar nada")
    esperar_barra_en_grupo(p, BARRA_EN_REPOSO, "Barra de escribir de Recordar en reposo")
    igual(campo_en_grupo(p).evaluate("(c) => [c.getAttribute('aria-label'), c.placeholder, c.maxLength]"), ["Escribe una cosa en este grupo", "Escribe una cosa…", 500], "El campo de Recordar es el de cualquier grupo")
    # Solo espacios: nada (ni la cosa, ni el grupo).
    campo_en_grupo(p).fill("   ")
    campo_en_grupo(p).press("Enter")
    p.wait_for_timeout(200)
    igual((recordadas(p), claves_guardadas(p)), ([], []), "Solo espacios guarda algo en Recordar")
    campo_en_grupo(p).fill("  Llamar al dentista ")
    campo_en_grupo(p).press("Enter")
    comprobar(esperar_toast(p, "Guardado")["icono"], "«Guardado» sin icono")
    esperar_recordadas(p, "lo escrito entra en Recordar", textos=["Llamar al dentista"], cuandos=["Poner recordatorio"], hechas=[False])
    igual(pagina_grupo(p)["vacio"], False, "Con una cosa, Recordar sigue diciendo que está vacío")
    guardado = leer_estado(p)
    primera = guardado["cosas"][0]["id"]
    comprobar(re.fullmatch(r"[0-9a-f-]{36}", primera), f"id no es un UUID: {primera!r}")
    igual(guardado, {"version": 2, "cosas": [{"id": primera, "texto": "Llamar al dentista", "hecha": False, "creada": AHORA_FIJO_MS, "hechaEn": None, "grupo": ID_RECORDAR}],
                     "grupos": [GRUPO_RECORDAR], "ajustes": {"colorFondo": COLOR_DEFECTO, "nombre": ""}}, "Lo guardado al apuntar la primera cosa en Recordar")
    igual(p.evaluate("(clave) => JSON.parse(localStorage.getItem(clave))", CLAVE_RESPALDO), {"grupos": [GRUPO_RECORDAR], "pertenencia": [[primera, ID_RECORDAR]], "hechas": []},
          "El respaldo de grupos, con Recordar como un grupo más")
    igual(claves_guardadas(p), sorted([CLAVE, CLAVE_RESPALDO]), "Claves guardadas tras apuntar en Recordar (sin recordatorios, su clave no existe)")
    # Dictado: lo reconocido se revisa y, al confirmarlo, entra en Recordar por encima de lo anterior.
    p.wait_for_timeout(400)
    p.locator("#dictar-en-grupo").tap()
    igual([(r["llamadas"], r["lang"]) for r in reconocimientos(p)], [(["start"], "es-ES")], "Reconocimiento al tocar el micrófono de Recordar")
    esperar_barra_en_grupo(p, BARRA_ESCUCHANDO, "Barra de Recordar escuchando")
    p.wait_for_timeout(400)
    dictar(p, "sacar la basura", final=True)
    p.evaluate("() => window.__dictado.fin()")
    esperar_barra_en_grupo(p, dict(BARRA_EN_REPOSO, micro=False, enviar=True, valor="Sacar la basura"), "Barra de Recordar al terminar de dictar")
    p.wait_for_timeout(600)  # Lo dictado no se guarda sin revisar: «Guardar» acaba de aparecer bajo el dedo.
    igual([f["texto"] for f in recordadas(p)], ["Llamar al dentista"], "Lo dictado se guarda sin confirmar")
    p.locator("#enviar-en-grupo").tap()
    esperar_toast(p, "Guardado")
    esperar_recordadas(p, "lo dictado entra en Recordar", textos=["Sacar la basura", "Llamar al dentista"])
    dictada = leer_estado(p)["cosas"][0]
    igual({k: v for k, v in dictada.items() if k != "id"}, {"texto": "Sacar la basura", "hecha": False, "creada": AHORA_FIJO_MS + 1, "hechaEn": None, "grupo": ID_RECORDAR}, "La cosa dictada en Recordar")
    igual((leer_estado(p)["grupos"], claves_guardadas(p)), ([GRUPO_RECORDAR], sorted([CLAVE, CLAVE_RESPALDO])), "Recordar se guarda una sola vez, y sigue sin haber clave de recordatorios")
    # En la lista no salen: están en Recordar, que las cuenta.
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual((textos_nivel(p), acceso(p)["cuenta"]), ([], "2"), "Lo apuntado en Recordar sale en la lista o no se cuenta")


@prueba("cosas de Recordar: «Añadir cosas» ofrece solo las sueltas y «+» las mete (ahí pasa a guardarse el grupo fijo); «−» («Quitar de Recordar», con «Deshacer») devuelve una pendiente a la lista sin olvidar su recordatorio, que recupera al volver con «Deshacer» o con «+», también tras recargar; las hechas llevan la papelera de la lista («Eliminado», «Deshacer»); marcar y desmarcar cambia el botón de la derecha y el orden")
def t_recordar_anadir_y_quitar(e):
    p = sembrar_recordar(e, estado_con([cosa(5, "Suelta nueva"), cosa(4, "Suelta hecha", hecha=True), cosa(3, "En Casa", grupo="g-9")], grupos=[grupo(9, "Casa")]))
    esperar_vista(p, "grupo")
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable de Recordar (lo de «Casa» no se ofrece)", desplegable=True, candidatas=["Suelta nueva", "Suelta hecha"], miembros=[], vacio=False)
    mas_de(p, "Suelta nueva").tap()
    esperar_pagina_grupo(p, "tras añadir a Recordar", miembros=["Suelta nueva"], candidatas=["Suelta hecha"])
    guardado = leer_estado(p)
    igual(([g["id"] for g in guardado["grupos"]], guardado["grupos"][1], next(c["grupo"] for c in guardado["cosas"] if c["texto"] == "Suelta nueva")), (["g-9", ID_RECORDAR], GRUPO_RECORDAR, ID_RECORDAR),
          "Al añadir la primera cosa, Recordar pasa a guardarse (detrás de los grupos propios) y la cosa entra en él")
    p.wait_for_timeout(400)
    mas_de(p, "Suelta hecha").tap()
    esperar_pagina_grupo(p, "sin más que añadir", miembros=["Suelta nueva", "Suelta hecha"], candidatas=[], sinMas=True)
    p.keyboard.press("Escape")
    igual([(f["texto"], f["hecha"], f["cuando"], f["derecha"]) for f in recordadas(p)],
          [("Suelta nueva", False, "Poner recordatorio", ["Quitar de Recordar"]), ("Suelta hecha", True, "Poner recordatorio", ["Eliminar"])], "Lo añadido a Recordar: la pendiente con «−» y la hecha con papelera")
    igual(claves_guardadas(p), sorted([CLAVE, CLAVE_RESPALDO]), "Añadir cosas a Recordar crea la clave de los recordatorios")
    p.context.close()

    recordatorios = recordar_guardado(cosas={"id-4": recordatorio(), "id-2": recordatorio("21:30", "semana", dia=2)})
    p = sembrar_recordar(e, estado_recordar([recordada(4, "Con recordatorio"), recordada(3, "Otra"), recordada(2, "Hecha", hecha=True), cosa(1, "Suelta")]), recordatorios)
    esperar_vista(p, "grupo")
    foco = "() => [document.activeElement.getAttribute('aria-label'), document.activeElement.closest('li').querySelector('.texto').textContent]"
    inicio = time.monotonic()
    quitar_de_recordar(p, "Con recordatorio").tap()
    igual(next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Con recordatorio"), None, "La cosa quitada sigue en Recordar en el almacenamiento")
    esperar_recordadas(p, "la fila quitada desaparece", textos=["Otra", "Hecha"])
    comprobar(time.monotonic() - inicio < 1.5, "La animación de quitar de Recordar no es corta")
    aviso = esperar_toast(p, "Quitado de Recordar")
    igual((aviso["boton"], aviso["icono"], p.evaluate(foco)), ("Deshacer", False, ["Quitar de Recordar", "Otra"]), "Aviso al quitar de Recordar, y el foco en el «−» vecino")
    igual(leer_recordar(p), recordatorios, "Al salir de Recordar, la cosa pierde el recordatorio que tenía")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_recordadas(p, "«Deshacer» la devuelve con su recordatorio", textos=["Con recordatorio", "Otra", "Hecha"], cuandos=["9:00 · Cada día", "Poner recordatorio", "21:30 · Cada martes"])
    igual((p.evaluate(foco), next(c["grupo"] for c in leer_estado(p)["cosas"] if c["texto"] == "Con recordatorio")), (["Marcar como hecha", "Con recordatorio"], ID_RECORDAR), "Tras deshacer: el foco en su check y la cosa, otra vez en Recordar")
    esperar_sin_toast(p, ms=1500)
    # Sin deshacer se queda en la lista, suelta. Su recordatorio sigue guardado (también tras recargar) y vuelve con ella al añadirla.
    quitar_de_recordar(p, "Con recordatorio").tap()
    esperar_toast(p, "Quitado de Recordar")
    p.get_by_role("button", name="Volver", exact=True).tap()  # Al salir de la página, el aviso (y su «Deshacer») se va.
    esperar_vista(p, "lista")
    igual((textos_nivel(p), acceso(p)["cuenta"]), (["Con recordatorio", "Suelta"], "1"), "La cosa quitada de Recordar no está suelta en la lista")
    p.reload()
    esperar_vista(p, "lista")
    igual(leer_recordar(p), recordatorios, "Tras recargar, el recordatorio de la cosa que salió de Recordar")
    abrir_recordar(p)
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "desplegable con la cosa que salió", desplegable=True, candidatas=["Con recordatorio", "Suelta"])
    mas_de(p, "Con recordatorio").tap()
    esperar_recordadas(p, "al volver con «+» recupera su recordatorio", textos=["Con recordatorio", "Otra", "Hecha"], cuandos=["9:00 · Cada día", "Poner recordatorio", "21:30 · Cada martes"])
    p.keyboard.press("Escape")
    # Una hecha se elimina desde aquí (no hay otro sitio), con su «Deshacer».
    p.wait_for_timeout(400)
    borrar_de(fila_recordada(p, "Hecha")).tap()
    igual(esperar_toast(p, "Eliminado")["boton"], "Deshacer", "Botón del aviso al eliminar una hecha de Recordar")
    igual([c["texto"] for c in leer_estado(p)["cosas"]], ["Con recordatorio", "Otra", "Suelta"], "Lo guardado tras eliminar la hecha")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_recordadas(p, "«Deshacer» repone la hecha", textos=["Con recordatorio", "Otra", "Hecha"], cuandos=["9:00 · Cada día", "Poner recordatorio", "21:30 · Cada martes"], hechas=[False, False, True])
    igual(p.evaluate(foco), ["Marcar como pendiente", "Hecha"], "Tras deshacer, el foco no va al check de la cosa repuesta")
    # Marcar: baja al final (tras la que ya estaba hecha) y su «−» pasa a ser la papelera; desmarcar lo deshace.
    check_de(fila_recordada(p, "Otra")).tap()
    esperar_recordadas(p, "la marcada baja al final", textos=["Con recordatorio", "Hecha", "Otra"], hechas=[False, True, True])
    marcada = recordadas(p)[2]
    igual((marcada["pulsado"], marcada["derecha"], marcada["cuando"], next(c["hecha"] for c in leer_estado(p)["cosas"] if c["texto"] == "Otra")), ("true", ["Eliminar"], "Poner recordatorio", True), "La cosa recién marcada")
    check_de(fila_recordada(p, "Otra")).tap()
    esperar_recordadas(p, "la desmarcada vuelve a su sitio", textos=["Con recordatorio", "Otra", "Hecha"], hechas=[False, False, True])
    igual((recordadas(p)[1]["pulsado"], recordadas(p)[1]["derecha"]), ("false", ["Quitar de Recordar"]), "La cosa desmarcada recupera su «−»")
    # Eliminada sin deshacer: al abrir la app otra vez, su recordatorio se olvida (ya no hay cosa a la que recordárselo).
    p.wait_for_timeout(400)
    borrar_de(fila_recordada(p, "Hecha")).tap()
    esperar_toast(p, "Eliminado")
    p.reload()
    esperar_vista(p, "grupo")
    igual(leer_recordar(p), recordar_guardado(cosas={"id-4": recordatorio()}), "Tras eliminar una cosa y volver a abrir la app, su recordatorio sigue guardado")
    # Doble toque en «−»: la fila vecina sube bajo el dedo y no sale también.
    p2 = sembrar_recordar(e, estado_recordar([recordada(3, "C"), recordada(2, "B"), recordada(1, "A")]))
    esperar_vista(p2, "grupo")
    doble_toque(p2, quitar_de_recordar(p2, "C"), 150)
    p2.wait_for_timeout(500)
    igual([f["texto"] for f in recordadas(p2)], ["B", "A"], "Un doble toque en «−» quita dos cosas de Recordar")


@prueba("recordatorio de cada fila de Recordar: sin él, «Poner recordatorio»; con él, la hora y cada cuánto («9:00 · Cada día», «7:05 · De lunes a viernes», «21:30 · Cada lunes», «1:15 · Día 31 de cada mes»…) y, para un lector de pantalla, «Recordatorio: … a las 9:00. Cambiar» («a la 1:15»); la cosa que no tiene uno propio sigue el de todas, salvo que se le quitara a ella")
def t_recordar_cuandos(e):
    casos = [
        # (recordatorio, lo que se lee en la fila, lo que oye un lector de pantalla)
        (None, "Poner recordatorio", "Poner recordatorio"),
        (recordatorio("09:00", "dia"), "9:00 · Cada día", "Recordatorio: cada día a las 9:00. Cambiar"),
        (recordatorio("07:05", "laborables"), "7:05 · De lunes a viernes", "Recordatorio: de lunes a viernes a las 7:05. Cambiar"),
        (recordatorio("21:30", "semana", dia=1), "21:30 · Cada lunes", "Recordatorio: cada lunes a las 21:30. Cambiar"),
        (recordatorio("13:00", "semana", dia=2), "13:00 · Cada martes", "Recordatorio: cada martes a las 13:00. Cambiar"),
        (recordatorio("01:00", "semana", dia=3), "1:00 · Cada miércoles", "Recordatorio: cada miércoles a la 1:00. Cambiar"),
        (recordatorio("10:10", "semana", dia=4), "10:10 · Cada jueves", "Recordatorio: cada jueves a las 10:10. Cambiar"),
        (recordatorio("23:55", "semana", dia=5), "23:55 · Cada viernes", "Recordatorio: cada viernes a las 23:55. Cambiar"),
        (recordatorio("00:00", "semana", dia=6), "0:00 · Cada sábado", "Recordatorio: cada sábado a las 0:00. Cambiar"),
        (recordatorio("12:00", "semana", dia=0), "12:00 · Cada domingo", "Recordatorio: cada domingo a las 12:00. Cambiar"),
        (recordatorio("09:00", "mes", diaMes=1), "9:00 · Día 1 de cada mes", "Recordatorio: el día 1 de cada mes a las 9:00. Cambiar"),
        (recordatorio("01:15", "mes", diaMes=31), "1:15 · Día 31 de cada mes", "Recordatorio: el día 31 de cada mes a la 1:15. Cambiar"),
    ]
    # La más nueva va arriba: el caso i es la cosa número len(casos) - i.
    cosas = [recordada(len(casos) - i, f"Cosa {i}") for i in range(len(casos))]
    propios = {f"id-{len(casos) - i}": r for i, (r, _, _) in enumerate(casos) if r}
    p = sembrar_recordar(e, estado_recordar(cosas), recordar_guardado(cosas=propios))
    esperar_vista(p, "grupo")
    igual([(f["texto"], f["cuando"], f["dicho"], f["sin"]) for f in recordadas(p)], [(f"Cosa {i}", leido, dicho, r is None) for i, (r, leido, dicho) in enumerate(casos)], "Lo que dice el recordatorio de cada fila")
    for nombre in ("Poner recordatorio", "Recordatorio: cada día a las 9:00. Cambiar", "Recordatorio: el día 31 de cada mes a la 1:15. Cambiar"):
        igual(p.locator("#lista-grupo").get_by_role("button", name=nombre, exact=True).count(), 1, f"Botones de fila que se llaman «{nombre}»")
    p.context.close()
    # Con uno para todas: lo sigue quien no tiene nada propio (también una hecha); quien tiene el suyo, el suyo; y a quien se le quitó, ninguno.
    de_todas = recordar_guardado(general=recordatorio("08:00", "laborables"), cosas={"id-3": recordatorio("21:30", "semana", dia=1), "id-2": False})
    p = sembrar_recordar(e, estado_recordar([recordada(4, "Hecha sin nada propio", hecha=True), recordada(3, "Con el suyo"), recordada(2, "Se le quitó"), recordada(1, "Sin nada propio")]), de_todas)
    esperar_vista(p, "grupo")
    igual(cuandos(p), {"Con el suyo": "21:30 · Cada lunes", "Se le quitó": "Poner recordatorio", "Sin nada propio": "8:00 · De lunes a viernes", "Hecha sin nada propio": "8:00 · De lunes a viernes"},
          "Los recordatorios de las filas habiendo uno para todas")
    igual(leer_recordar(p), de_todas, "Enseñar los recordatorios cambia lo guardado")


@prueba("lista vacía con Recordar: sin cosas sueltas ni grupos propios, si hay algo en Recordar dice «Lo que tienes apuntado está en Recordar.» (centrado, bajo la fila fija); con una cosa suelta o un grupo propio no dice nada, y con Recordar también vacío, «No tienes cosas apuntadas.»")
def t_lista_vacia_con_recordar(e):
    en_recordar, sin_nada = "Lo que tienes apuntado está en Recordar.", "No tienes cosas apuntadas."
    nota = "() => { const v = document.querySelector('#vacio'); return v.checkVisibility() ? v.textContent : null; }"
    p = sembrar_recordar(e, estado_recordar([recordada(2, "Hecha en Recordar", hecha=True), cosa(1, "Suelta")]), ruta="#lista")
    esperar_filas(p, 1)
    igual(p.evaluate(nota), None, "Con una cosa suelta, la lista dice que está vacía")
    borrar_de(fila_de(p, "Suelta")).tap()
    esperar_filas(p, 0)
    igual(p.evaluate(nota), en_recordar, "Sin cosas sueltas y con algo en Recordar (aunque esté hecho)")
    caja = p.get_by_text(en_recordar, exact=True).evaluate("(el) => { const r = document.createRange(); r.selectNodeContents(el); const b = r.getBoundingClientRect(); return { cx: b.left + b.width / 2, cy: b.top + b.height / 2, y: b.top, x: b.left, d: b.right }; }")
    comprobar(abs(caja["cx"] - e.ancho / 2) <= 3 and e.alto * 0.3 <= caja["cy"] <= e.alto * 0.7 and caja["x"] >= 16 and caja["d"] <= e.ancho - 16, f"La nota no está centrada o se sale: {caja}")
    comprobar(caja["y"] >= acceso(p)["caja"]["b"], f"La nota pisa la fila fija: {caja} / {acceso(p)['caja']}")
    comprobar(p.evaluate("() => {" + JS_UTIL + " return contrasteTexto(document.querySelector('#vacio')); }") >= 4.5, "La nota no contrasta con el fondo")
    # «Deshacer» devuelve la cosa suelta y la nota se va; un grupo propio, aunque vacío, también la quita.
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_filas(p, 1)
    igual(p.evaluate(nota), None, "Con la cosa suelta de vuelta, la nota sigue")
    p.wait_for_timeout(400)
    borrar_de(fila_de(p, "Suelta")).tap()
    esperar_filas(p, 0)
    crear_grupo(p, "Casa")
    esperar_toast(p, "Grupo creado")
    igual((textos_nivel(p), p.evaluate(nota)), (["Casa"], None), "Con un grupo propio (vacío), la lista dice que no hay nada")
    p.wait_for_timeout(400)
    borrar_grupo_de(fila_grupo(p, "Casa")).tap()
    esperar_filas(p, 0)
    igual(p.evaluate(nota), en_recordar, "Sin el grupo propio, vuelve la nota de Recordar")
    # Recordar se vacía (se elimina su única cosa, desde su página): la lista vuelve a su nota de siempre.
    p.wait_for_timeout(400)
    abrir_recordar(p)
    borrar_de(fila_recordada(p, "Hecha en Recordar")).tap()
    esperar_toast(p, "Eliminado")
    esperar_pagina_grupo(p, "Recordar vacío", miembros=[], vacio=True)
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual((p.evaluate(nota), acceso(p)["cuenta"]), (sin_nada, None), "Sin nada en ningún sitio")
    # Y lo que llega de otra pestaña: una cosa nueva en Recordar cambia la nota al momento.
    otra = e.pagina(ruta="#grupo/recordar", contexto=p.context)
    esperar_vista(otra, "grupo")
    campo_en_grupo(otra).fill("Desde otra pestaña")
    campo_en_grupo(otra).press("Enter")
    esperar(p, f"(texto) => ({nota})() === texto", arg=en_recordar, que="la nota de la lista sigue a lo que otra pestaña apunta en Recordar")
    esperar_cuenta(p, "1", "Recordar. Una cosa pendiente")


# La hoja del recordatorio: lo que enseña y lo que tiene elegido (lo que no se ve cuenta como None).
JS_HOJA_RECORDAR = r"""
() => {
  const hoja = document.querySelector('#hoja-recordar'), dialogo = hoja.querySelector('[role=dialog]'), principal = document.querySelector('main');
  const q = (s) => hoja.querySelector(s), abierta = hoja.checkVisibility();
  const seVe = (el) => abierta && el.checkVisibility();
  const elegido = (nombre) => { const radio = q(`input[name="${nombre}"]:checked`); return radio ? radio.value : null; };
  const cosa = q('#cosa-recordar'), lista = q('#elegir-recordar'), quitar = q('#quitar-recordar'), ayudaMes = q('#ayuda-dia-mes');
  return {
    abierta, modal: dialogo.getAttribute('aria-modal'),
    titulo: abierta ? document.getElementById(dialogo.getAttribute('aria-labelledby')).textContent.trim() : null,
    cosa: seVe(cosa) ? cosa.textContent : null,
    alcance: seVe(q('#alcance-recordar')) ? elegido('alcance') : null,
    elegibles: seVe(lista) ? [...lista.querySelectorAll('li')].map((li) => [li.querySelector('.elegible-texto').textContent, li.querySelector('input').checked]) : null,
    hora: q('#hora-recordar').value, minuto: q('#minuto-recordar').value, frecuencia: elegido('frecuencia'),
    dia: seVe(q('#dia-recordar')) ? elegido('dia') : null,
    diaMes: seVe(q('#mes-recordar')) ? q('#dia-mes-recordar').value : null,
    ayudaMes: seVe(ayudaMes) ? ayudaMes.textContent : null,
    resumen: q('#resumen-recordar').textContent, guardar: !q('#guardar-recordar').disabled,
    quitar: seVe(quitar) ? quitar.textContent : null, ayuda: q('#ayuda-recordar').textContent,
    focoDentro: dialogo.contains(document.activeElement), foco: document.activeElement.id || document.activeElement.className || document.activeElement.tagName,
    paginaInerte: principal.hasAttribute('inert'), paginaOculta: principal.getAttribute('aria-hidden'),
  };
}
"""

# Lo que la hoja dice de las notificaciones de este dispositivo antes de «Guardar», y dónde se guarda lo que se recuerda.
AYUDA_HOJA_CON_BOTONES = "Te llegará una notificación por cada cosa, con «Hecho» y «Mantener»."
AYUDA_HOJA_SIN_BOTONES = "Te llegará una notificación por cada cosa: al tocarla, dices si ya está hecha o si la mantienes."
PRIVACIDAD_HOJA = ("Para avisarte a su hora, el texto de lo que tenga recordatorio se guarda también en el servidor de Cosas, aunque no hayas iniciado sesión. "
                   "Se borra de allí al quitar el recordatorio o al eliminar la cosa.")
DIAS_HOJA = [("Lunes", "L", "1", "lunes"), ("Martes", "M", "2", "martes"), ("Miércoles", "X", "3", "miércoles"), ("Jueves", "J", "4", "jueves"),
             ("Viernes", "V", "5", "viernes"), ("Sábado", "S", "6", "sábado"), ("Domingo", "D", "0", "domingo")]


def hoja_recordar(pagina):
    return pagina.evaluate(JS_HOJA_RECORDAR)


def esperar_hoja_recordar(pagina, que, **esperado):
    esperar_json(pagina, JS_HOJA_RECORDAR, esperado, que)


def abrir_hoja_recordar(pagina, texto=None):
    """Abre la hoja del recordatorio: el de esa cosa (desde su fila) o, sin «texto», desde la campana (para todas o las que se elijan)."""
    (cuando_de(pagina, texto) if texto else boton_configurar(pagina)).tap()
    esperar(pagina, f"() => ({JS_HOJA_RECORDAR})().abierta", que="se abre la hoja del recordatorio")
    pagina.wait_for_timeout(450)  # Fin de la animación de entrada y de la guarda del doble toque.
    return hoja_recordar(pagina)


def pastilla(pagina, nombre):
    """Toca una opción de la hoja como una persona: sobre su pastilla (la etiqueta que envuelve al radio)."""
    radio = pagina.locator("#hoja-recordar").get_by_role("radio", name=nombre, exact=True)
    radio.locator("xpath=ancestor::label").tap()
    comprobar(radio.is_checked(), f"Tocar la pastilla «{nombre}» no la elige")


def elegir_hora(pagina, hora, minuto=None):
    pagina.locator("#hora-recordar").select_option(hora)
    if minuto is not None:
        pagina.locator("#minuto-recordar").select_option(minuto)


def casilla(pagina, texto):
    """La casilla de esa cosa en la lista de «Elegir» (se toca su etiqueta: la fila entera)."""
    return pagina.locator("#elegir-recordar").get_by_role("checkbox", name=texto, exact=True)


def boton_guardar_recordar(pagina):
    return pagina.locator("#hoja-recordar").get_by_role("button", name="Guardar", exact=True)


def guardar_hoja_recordar(pagina):
    boton_guardar_recordar(pagina).tap()
    esperar(pagina, f"() => !({JS_HOJA_RECORDAR})().abierta", que="la hoja del recordatorio se cierra al guardar")


def cerrar_hoja_recordar(pagina):
    pagina.keyboard.press("Escape")
    esperar(pagina, f"() => !({JS_HOJA_RECORDAR})().abierta", que="Escape cierra la hoja del recordatorio")
    pagina.wait_for_timeout(400)  # Lo que hay bajo la hoja acaba de aparecer: pasa su guarda.


@prueba("hoja del recordatorio de una cosa: se abre desde su fila con el texto de la cosa, la hora en dos desplegables (minutos de cinco en cinco) y cuatro frecuencias (ya no hay «Una vez»); «Cada semana» pide el día (por defecto, hoy) y «Cada mes» el día del mes (hoy; del 29 al 31, «En los meses más cortos, el último día.»); la frase dice en vivo lo que pasará («a la 1:05»); «Guardar» deja exactamente ese recordatorio y la fila lo dice; al reabrir están sus valores y «Quitar recordatorio»; cerrar sin guardar no cambia nada")
def t_hoja_recordar_una(e):
    martes = recordatorio("21:30", "semana", dia=2)
    datos = estado_recordar([recordada(3, "Llamar al dentista"), recordada(2, "Sacar la basura"), recordada(1, "Regar las plantas", hecha=True)])
    contexto = contexto_con_reloj(e)
    contexto.add_init_script(JS_AVISOS_FALSOS + CON_PERMISO)
    p = sembrar_recordar(e, datos, recordar_guardado(cosas={"id-2": martes, "id-1": recordatorio("09:07", "dia")}), contexto=contexto, avisos=AVISOS_ACTIVOS)
    esperar_vista(p, "grupo")
    sin_tocar, cosas_sin_tocar = recordar_en_bruto(p), p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE)
    h = abrir_hoja_recordar(p, "Llamar al dentista")
    igual(h, {"abierta": True, "modal": "true", "titulo": "Recordatorio", "cosa": "Llamar al dentista", "alcance": None, "elegibles": None, "hora": "09", "minuto": "00", "frecuencia": "dia",
              "dia": None, "diaMes": None, "ayudaMes": None, "resumen": "Te lo recordaré cada día a las 9:00.", "guardar": True, "quitar": None, "ayuda": AYUDA_HOJA_CON_BOTONES,
              "focoDentro": True, "foco": "titulo-hoja-recordar", "paginaInerte": True, "paginaOculta": "true"}, "La hoja de una cosa sin recordatorio")
    igual(p.get_by_role("dialog", name="Recordatorio", exact=True).count(), 1, "Diálogos con el nombre de la hoja")
    # La hora: dos desplegables del sistema, con nombre y letra de 16 px o más (iOS no amplía la página al tocarlos).
    selectores = p.evaluate("""() => ['#hora-recordar', '#minuto-recordar', '#dia-mes-recordar'].map((s) => { const el = document.querySelector(s);
        return [el.tagName, el.getAttribute('aria-label'), [...el.options].map((o) => o.value), [...el.options].map((o) => o.textContent), parseFloat(getComputedStyle(el).fontSize) >= 16]; })""")
    igual(selectores, [["SELECT", "Hora", [f"{n:02d}" for n in range(24)], [str(n) for n in range(24)], True],
                       ["SELECT", "Minutos", [f"{n:02d}" for n in range(0, 60, 5)], [f"{n:02d}" for n in range(0, 60, 5)], True],
                       ["SELECT", "Día del mes", [str(n) for n in range(1, 32)], [str(n) for n in range(1, 32)], True]], "Desplegables de la hora, los minutos (de cinco en cinco) y el día del mes")
    cajas = [p.locator(s).bounding_box() for s in ("#hora-recordar", "#minuto-recordar")]
    comprobar(all(c["height"] >= 44 and c["width"] >= 44 for c in cajas) and cajas[0]["x"] + cajas[0]["width"] <= cajas[1]["x"] and abs(cajas[0]["y"] - cajas[1]["y"]) <= 0.5, f"La hora y los minutos no van uno junto al otro con 44 px o más: {cajas}")
    frecuencias = p.evaluate("""() => [...document.querySelectorAll('#frecuencia-recordar label')].map((l) => { const r = l.getBoundingClientRect();
        return [l.textContent.trim(), l.querySelector('input').value, l.querySelector('input').type, l.querySelector('input').name, r.height >= 44 && r.width >= 44]; })""")
    igual(frecuencias, [["Cada día", "dia", "radio", "frecuencia", True], ["De lunes a viernes", "laborables", "radio", "frecuencia", True], ["Cada semana", "semana", "radio", "frecuencia", True],
                        ["Cada mes", "mes", "radio", "frecuencia", True]], "Las cuatro frecuencias (sin «Una vez»), en pastillas de 44 px o más")
    igual(p.locator("#hoja-recordar legend").evaluate_all("(ls) => ls.filter((l) => l.checkVisibility()).map((l) => l.textContent)"), ["Hora", "Frecuencia"], "Apartados de la hoja de una cosa")
    igual((p.locator("#resumen-recordar").get_attribute("aria-live"), p.locator("#hoja-recordar").get_by_text(PRIVACIDAD_HOJA, exact=True).count()), ("polite", 1), "La frase se anuncia al cambiar, y la hoja dice dónde se guarda lo que se recuerda")
    # Cada frecuencia, y lo que pide: la frase lo va diciendo.
    pastilla(p, "De lunes a viernes")
    esperar_hoja_recordar(p, "de lunes a viernes", frecuencia="laborables", dia=None, diaMes=None, resumen="Te lo recordaré de lunes a viernes a las 9:00.")
    pastilla(p, "Cada semana")
    esperar_hoja_recordar(p, "cada semana propone el día de hoy (en Madrid ya es domingo; en UTC aún sería sábado)", frecuencia="semana", dia="0", diaMes=None, resumen="Te lo recordaré cada domingo a las 9:00.")
    dias = p.evaluate("""() => [...document.querySelectorAll('#dia-recordar label')].map((l) => { const r = l.getBoundingClientRect(), radio = l.querySelector('input'), letra = l.querySelector('span');
        return [radio.getAttribute('aria-label'), letra.textContent, letra.getAttribute('aria-hidden'), radio.value, r.height >= 44 && r.width >= 40]; })""")
    igual((dias, p.locator("#dia-recordar legend").text_content()), ([[nombre, letra, "true", valor, True] for nombre, letra, valor, _ in DIAS_HOJA], "Día"), "Los siete días, de lunes a domingo, con su nombre para un lector de pantalla")
    for nombre, _, valor, dicho in DIAS_HOJA:
        pastilla(p, nombre)
        esperar_hoja_recordar(p, f"cada {dicho}", dia=valor, resumen=f"Te lo recordaré cada {dicho} a las 9:00.")
    pastilla(p, "Cada mes")
    esperar_hoja_recordar(p, "cada mes propone el día de hoy (el 31, en Madrid)", frecuencia="mes", dia=None, diaMes="31", ayudaMes="En los meses más cortos, el último día.",
                          resumen="Te lo recordaré el día 31 de cada mes a las 9:00.")
    igual(p.locator("#mes-recordar legend").text_content(), "Día del mes", "Apartado del día del mes")
    for dia, corto in (("28", False), ("29", True), ("30", True), ("1", False)):
        p.locator("#dia-mes-recordar").select_option(dia)
        esperar_hoja_recordar(p, f"el día {dia}", diaMes=dia, ayudaMes="En los meses más cortos, el último día." if corto else None, resumen=f"Te lo recordaré el día {dia} de cada mes a las 9:00.")
        igual(p.locator("#dia-mes-recordar").get_attribute("aria-describedby"), "ayuda-dia-mes" if corto else None, f"Día {dia}: el desplegable y su nota de los meses cortos")
    for hora, minuto, dicho in (("01", "05", "a la 1:05"), ("13", "05", "a las 13:05"), ("00", "55", "a las 0:55"), ("23", "00", "a las 23:00")):
        elegir_hora(p, hora, minuto)
        esperar_hoja_recordar(p, f"las {hora}:{minuto}", hora=hora, minuto=minuto, resumen=f"Te lo recordaré el día 1 de cada mes {dicho}.")
    pastilla(p, "Cada día")
    elegir_hora(p, "01", "00")
    esperar_hoja_recordar(p, "cada día a la una", frecuencia="dia", dia=None, diaMes=None, ayudaMes=None, resumen="Te lo recordaré cada día a la 1:00.")
    # Cerrar sin guardar: nada cambia (ni lo guardado ni la fila), el foco vuelve a la fila y, al reabrir, la hoja empieza de cero.
    p.keyboard.press("Escape")
    cerrada = hoja_recordar(p)
    igual((cerrada["abierta"], cerrada["paginaInerte"], cerrada["paginaOculta"], p.evaluate("() => document.activeElement === document.querySelector('#lista-grupo > li .cuando')")), (False, False, None, True),
          "Tras Escape: la hoja cerrada, la página de vuelta y el foco en el recordatorio de la fila que la abrió")
    igual((recordar_en_bruto(p), cuandos(p)["Llamar al dentista"]), (sin_tocar, "Poner recordatorio"), "Cerrar la hoja sin guardar cambia algo")
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p, "Llamar al dentista")
    igual((h["hora"], h["minuto"], h["frecuencia"], h["resumen"]), ("09", "00", "dia", "Te lo recordaré cada día a las 9:00."), "Al reabrir, la hoja conserva lo que se descartó")
    # Guardar: exactamente ese recordatorio (con sus campos en el orden de siempre), la fila lo dice y el foco vuelve a ella.
    elegir_hora(p, "07", "35")
    pastilla(p, "Cada semana")
    pastilla(p, "Jueves")
    guardar_hoja_recordar(p)
    aviso = esperar_toast(p, "Recordatorio guardado")
    igual((aviso["icono"], aviso["boton"]), (True, None), "El aviso al guardar el recordatorio de una cosa")
    jueves = recordatorio("07:35", "semana", dia=4)
    igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-2": martes, "id-1": recordatorio("09:07", "dia"), "id-3": jueves})), "Lo guardado tras poner el recordatorio (letra a letra)")
    igual((cuandos(p), p.evaluate("() => document.activeElement.getAttribute('aria-label')")),
          ({"Llamar al dentista": "7:35 · Cada jueves", "Sacar la basura": "21:30 · Cada martes", "Regar las plantas": "9:07 · Cada día"}, "Recordatorio: cada jueves a las 7:35. Cambiar"),
          "Las filas tras guardar (solo cambia esa), y el foco en su recordatorio")
    igual(p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE), cosas_sin_tocar, "Poner un recordatorio toca lo guardado de las cosas")
    # Al reabrir: sus valores y «Quitar recordatorio». De semanal a mensual no queda rastro del día de la semana.
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p, "Llamar al dentista")
    igual((h["hora"], h["minuto"], h["frecuencia"], h["dia"], h["diaMes"], h["resumen"], h["quitar"]), ("07", "35", "semana", "4", None, "Te lo recordaré cada jueves a las 7:35.", "Quitar recordatorio"),
          "La hoja de una cosa con recordatorio")
    pastilla(p, "Cada mes")
    p.locator("#dia-mes-recordar").select_option("15")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-2": martes, "id-1": recordatorio("09:07", "dia"), "id-3": recordatorio("07:35", "mes", diaMes=15)})), "Lo guardado al pasar de semanal a mensual")
    igual(cuandos(p)["Llamar al dentista"], "7:35 · Día 15 de cada mes", "La fila con el recordatorio mensual")
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p, "Llamar al dentista")
    igual((h["frecuencia"], h["diaMes"], h["dia"]), ("mes", "15", None), "Al reabrir el recordatorio mensual")
    pastilla(p, "Cada semana")
    esperar_hoja_recordar(p, "lo que el recordatorio no dice, por hoy", dia="0", resumen="Te lo recordaré cada domingo a las 7:35.")
    # «Quitar recordatorio»: la fila vuelve a ofrecer ponerlo y de esa cosa no queda nada guardado.
    p.locator("#hoja-recordar").get_by_role("button", name="Quitar recordatorio", exact=True).tap()
    aviso = esperar_toast(p, "Recordatorio quitado")
    igual((aviso["icono"], aviso["boton"], hoja_recordar(p)["abierta"]), (False, None, False), "Al quitar el recordatorio: un aviso de solo texto y la hoja cerrada")
    igual((recordar_en_bruto(p), cuandos(p)["Llamar al dentista"], p.evaluate("() => document.activeElement.getAttribute('aria-label')")),
          (en_bruto(recordar_guardado(cosas={"id-2": martes, "id-1": recordatorio("09:07", "dia")})), "Poner recordatorio", "Poner recordatorio"), "Tras quitar el recordatorio: lo guardado, la fila y el foco")
    # Las otras cosas abren con lo suyo: una semanal, y una hecha cuya hora no va de cinco en cinco (puesta de otra forma): baja al múltiplo anterior.
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p, "Sacar la basura")
    igual((h["cosa"], h["hora"], h["minuto"], h["frecuencia"], h["dia"], h["resumen"], h["quitar"]), ("Sacar la basura", "21", "30", "semana", "2", "Te lo recordaré cada martes a las 21:30.", "Quitar recordatorio"), "La hoja de otra cosa")
    cerrar_hoja_recordar(p)
    h = abrir_hoja_recordar(p, "Regar las plantas")
    igual((h["cosa"], h["hora"], h["minuto"], h["resumen"]), ("Regar las plantas", "09", "05", "Te lo recordaré cada día a las 9:05."), "La hoja de una cosa con una hora que no va de cinco en cinco")
    cerrar_hoja_recordar(p)
    igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-2": martes, "id-1": recordatorio("09:07", "dia")})), "Abrir y cerrar hojas cambia lo guardado")


# Tres cosas pendientes (dos con recordatorio propio) y dos hechas (una con el suyo), además de una suelta.
def recordar_variado():
    cosas = [cosa(6, "Suelta"), recordada(5, "Llamar al dentista"), recordada(4, "Sacar la basura"), recordada(3, "Pagar la comunidad"),
             recordada(2, "Regar las plantas", hecha=True), recordada(1, "Hecha sin recordatorio", hecha=True)]
    propios = {"id-5": recordatorio("21:30", "semana", dia=2), "id-3": recordatorio("09:00", "dia"), "id-2": recordatorio("08:00", "mes", diaMes=1)}
    return estado_recordar(cosas), recordar_guardado(cosas=propios)


@prueba("hoja desde la campana, «Todas»: el recordatorio es para todas las pendientes y para lo que se apunte después (la frase lo dice, y a cuántas les cambia el que tenían); «Guardar» lo deja como el de todas sin tocar las hechas, avisa «Recordatorio puesto a N cosas» y «Deshacer» devuelve exactamente lo de antes; «Quitar todos los recordatorios», también con «Deshacer»; sin nada pendiente no pregunta para qué cosas")
def t_hoja_recordar_todas(e):
    datos, propios = recordar_variado()
    p = sembrar_recordar(e, datos, propios)
    esperar_vista(p, "grupo")
    antes = cuandos(p)
    igual(antes, {"Llamar al dentista": "21:30 · Cada martes", "Sacar la basura": "Poner recordatorio", "Pagar la comunidad": "9:00 · Cada día",
                  "Regar las plantas": "8:00 · Día 1 de cada mes", "Hecha sin recordatorio": "Poner recordatorio"}, "Recordatorios de partida")
    h = abrir_hoja_recordar(p)
    cambia_una = "Te lo recordaré cada día a las 9:00. También lo que apuntes después. Cambiará el recordatorio de 1 cosa que tenía otro."
    igual((h["titulo"], h["cosa"], h["alcance"], h["elegibles"], h["hora"], h["minuto"], h["frecuencia"], h["resumen"], h["guardar"], h["quitar"], h["foco"]),
          ("Recordatorio", None, "todas", None, "09", "00", "dia", cambia_una, True, "Quitar todos los recordatorios", "titulo-hoja-recordar"), "La hoja desde la campana")
    alcance = p.evaluate("""() => { const f = document.querySelector('#alcance-recordar');
        return [f.tagName, f.querySelector('legend').textContent, [...f.querySelectorAll('label')].map((l) => [l.textContent.trim(), l.querySelector('input').value, l.getBoundingClientRect().height >= 44])]; }""")
    igual(alcance, ["FIELDSET", "Para qué cosas", [["Todas", "todas", True], ["Elegir", "elegir", True]]], "«Para qué cosas»: Todas o Elegir")
    # La frase cuenta, en vivo, a cuántas cosas pendientes se les cambiaría el recordatorio que tienen (las que ya tienen ese mismo no cuentan).
    elegir_hora(p, "10")
    esperar_hoja_recordar(p, "a las 10 cambian las dos que tienen uno", resumen="Te lo recordaré cada día a las 10:00. También lo que apuntes después. Cambiará el recordatorio de 2 cosas que tenían otro.")
    elegir_hora(p, "21", "30")
    pastilla(p, "Cada semana")
    pastilla(p, "Martes")
    esperar_hoja_recordar(p, "el de los martes solo cambia el diario", resumen="Te lo recordaré cada martes a las 21:30. También lo que apuntes después. Cambiará el recordatorio de 1 cosa que tenía otro.")
    pastilla(p, "Cada día")
    elegir_hora(p, "10", "00")
    guardar_hoja_recordar(p)
    aviso = esperar_toast(p, "Recordatorio puesto a 3 cosas")
    igual((aviso["boton"], aviso["icono"]), ("Deshacer", False), "El aviso al poner el recordatorio a todas")
    comprobar(not p.evaluate(f"() => ({JS_RECORTADO})(document.querySelector('#toast .toast-texto'))") and aviso["x"] >= 0 and aviso["x"] + aviso["ancho"] <= e.ancho, f"El aviso sale recortado o se sale: {aviso}")
    # El de todas queda dicho una vez; las pendientes lo siguen (ya no tienen nada propio) y las hechas siguen como estaban: una con el suyo, la otra sin ninguno.
    de_todas = recordar_guardado(general=recordatorio("10:00", "dia"), cosas={"id-2": recordatorio("08:00", "mes", diaMes=1), "id-1": False})
    igual(recordar_en_bruto(p), en_bruto(de_todas), "Lo guardado tras «Todas» (letra a letra)")
    igual((cuandos(p), p.evaluate("() => document.activeElement.id")),
          ({"Llamar al dentista": "10:00 · Cada día", "Sacar la basura": "10:00 · Cada día", "Pagar la comunidad": "10:00 · Cada día", "Regar las plantas": "8:00 · Día 1 de cada mes",
            "Hecha sin recordatorio": "Poner recordatorio"}, "configurar-recordar"), "Las filas tras «Todas» (las hechas, intactas), y el foco de vuelta en la campana")
    # «Deshacer»: exactamente lo de antes.
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).general === null", arg=CLAVE_RECORDAR, que="«Deshacer» quita el recordatorio de todas")
    igual((leer_recordar(p), cuandos(p), p.evaluate("() => document.activeElement.id")), (propios, antes, "configurar-recordar"), "Tras «Deshacer»: lo guardado, las filas y el foco")
    esperar_sin_toast(p, ms=1500)
    # Puesto otra vez (sin deshacer): lo sigue también lo que se apunte después, lo que se añada y nada de lo que ya estaba hecho.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p)
    elegir_hora(p, "10")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio puesto a 3 cosas")
    campo_en_grupo(p).fill("Apuntada después")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    esperar(p, f"() => ({JS_RECORDADAS})()[0].cuando === '10:00 · Cada día'", que="lo apuntado después sigue el recordatorio de todas")
    p.wait_for_timeout(400)
    boton_anadir(p).tap()
    mas_de(p, "Suelta").tap()
    p.keyboard.press("Escape")
    esperar(p, f"() => ({JS_RECORDADAS})().some((f) => f.texto === 'Suelta' && f.cuando === '10:00 · Cada día')", que="lo añadido después sigue el recordatorio de todas")
    check_de(fila_recordada(p, "Hecha sin recordatorio")).tap()
    esperar_recordadas(p, "la hecha, desmarcada, vuelve arriba", hechas=[False, False, False, False, False, False, True])
    igual((cuandos(p)["Hecha sin recordatorio"], leer_recordar(p)), ("Poner recordatorio", de_todas), "Lo que estaba hecho al poner el de todas no lo hereda al desmarcarlo (y lo nuevo no guarda nada propio)")
    # Al reabrir, la hoja enseña el de todas; nadie tiene otro, así que no avisa de ningún cambio.
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p)
    igual((h["hora"], h["minuto"], h["frecuencia"], h["resumen"], h["quitar"]), ("10", "00", "dia", "Te lo recordaré cada día a las 10:00. También lo que apuntes después.", "Quitar todos los recordatorios"),
          "La hoja desde la campana con un recordatorio para todas")
    # «Quitar todos los recordatorios»: el de todas y el de cada pendiente; las hechas, como estaban. Y «Deshacer».
    p.locator("#hoja-recordar").get_by_role("button", name="Quitar todos los recordatorios", exact=True).tap()
    aviso = esperar_toast(p, "Recordatorios quitados")
    igual((aviso["boton"], hoja_recordar(p)["abierta"]), ("Deshacer", False), "El aviso al quitar todos los recordatorios")
    igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-2": recordatorio("08:00", "mes", diaMes=1)})), "Lo guardado tras quitar todos (letra a letra)")
    igual(set(cuandos(p).items()) - {("Regar las plantas", "8:00 · Día 1 de cada mes")}, {(texto, "Poner recordatorio") for texto in ("Suelta", "Apuntada después", "Llamar al dentista", "Sacar la basura", "Pagar la comunidad", "Hecha sin recordatorio")},
          "Las filas tras quitar todos los recordatorios")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).general !== null", arg=CLAVE_RECORDAR, que="«Deshacer» devuelve el recordatorio de todas")
    igual((leer_recordar(p), cuandos(p)["Suelta"], cuandos(p)["Hecha sin recordatorio"]), (de_todas, "10:00 · Cada día", "Poner recordatorio"), "Tras deshacer el «Quitar todos»")
    p.context.close()

    # Sin nada pendiente (un perfil nuevo): no hay para qué cosas elegir; el recordatorio es para lo que se apunte.
    p = e.pagina(ruta="#grupo/recordar")
    esperar_vista(p, "grupo")
    h = abrir_hoja_recordar(p)
    igual((h["alcance"], h["elegibles"], h["resumen"], h["quitar"], h["guardar"]), (None, None, "Te recordaré a esa hora lo que apuntes aquí.", None, True), "La hoja desde la campana con Recordar vacío")
    elegir_hora(p, "08", "30")
    pastilla(p, "De lunes a viernes")
    guardar_hoja_recordar(p)
    aviso = esperar_toast(p, "Recordatorio guardado")
    igual((aviso["icono"], aviso["boton"]), (True, None), "Sin cosas a las que ponérselo, no hay nada que deshacer")
    igual((recordar_en_bruto(p), claves_guardadas(p)), (en_bruto(recordar_guardado(general=recordatorio("08:30", "laborables"))), [CLAVE_RECORDAR]), "Lo guardado al poner un recordatorio para todas con Recordar vacío")
    campo_en_grupo(p).fill("La primera")
    campo_en_grupo(p).press("Enter")
    esperar_recordadas(p, "la primera cosa hereda el de todas", textos=["La primera"], cuandos=["8:30 · De lunes a viernes"])
    p.wait_for_timeout(400)
    h = abrir_hoja_recordar(p)
    igual((h["alcance"], h["hora"], h["minuto"], h["frecuencia"], h["resumen"], h["quitar"]),
          ("todas", "08", "30", "laborables", "Te lo recordaré de lunes a viernes a las 8:30. También lo que apuntes después.", "Quitar todos los recordatorios"), "Con una cosa pendiente, la hoja ya pregunta para qué cosas")


@prueba("hoja desde la campana, «Elegir»: lista solo las cosas pendientes, ninguna marcada, y «Guardar» no se puede pulsar hasta marcar alguna («Elige al menos una cosa.»); al guardar cambian solo las elegidas («Recordatorio puesto a N cosas», con «Deshacer»); volver a «Todas» esconde la lista y al regresar se elige de cero")
def t_hoja_recordar_elegir(e):
    datos, propios = recordar_variado()
    p = sembrar_recordar(e, datos, propios)
    esperar_vista(p, "grupo")
    antes = cuandos(p)
    abrir_hoja_recordar(p)
    pastilla(p, "Elegir")
    pendientes = [["Llamar al dentista", False], ["Sacar la basura", False], ["Pagar la comunidad", False]]
    esperar_hoja_recordar(p, "«Elegir» lista las pendientes, sin marcar", alcance="elegir", elegibles=pendientes, guardar=False, quitar=None, resumen="Elige al menos una cosa.")
    lista = p.evaluate("""() => { const ul = document.querySelector('#elegir-recordar');
        return [ul.getAttribute('role'), ul.getAttribute('aria-label'), [...ul.querySelectorAll('label')].map((l) => { const r = l.getBoundingClientRect(), c = l.querySelector('input');
            return [c.type, c.name, r.height >= 44, l.querySelector('.elegible-marca').getAttribute('aria-hidden')]; })]; }""")
    igual(lista, ["list", "Cosas que quieres que te recuerde", [["checkbox", "cosa", True, "true"]] * 3], "La lista de «Elegir»: casillas con su texto por nombre, en filas de 44 px o más")
    # «Guardar» apagado no guarda (ni con el dedo ni con Intro).
    caja = boton_guardar_recordar(p).bounding_box()
    p.touchscreen.tap(caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2)
    casilla(p, "Sacar la basura").focus()
    p.keyboard.press("Enter")
    p.wait_for_timeout(200)
    igual((hoja_recordar(p)["abierta"], leer_recordar(p), boton_guardar_recordar(p).evaluate("(b) => parseFloat(getComputedStyle(b).opacity) < 1")), (True, propios, True), "«Guardar» sin nada elegido guarda algo, cierra la hoja o no se ve apagado")
    casilla(p, "Sacar la basura").locator("xpath=ancestor::label").tap()
    esperar_hoja_recordar(p, "con una elegida ya se puede guardar", elegibles=[["Llamar al dentista", False], ["Sacar la basura", True], ["Pagar la comunidad", False]], guardar=True, resumen="Te lo recordaré cada día a las 9:00.")
    casilla(p, "Sacar la basura").locator("xpath=ancestor::label").tap()
    esperar_hoja_recordar(p, "al desmarcarla, otra vez no", elegibles=pendientes, guardar=False, resumen="Elige al menos una cosa.")
    # De vuelta a «Todas» la lista se esconde; al regresar a «Elegir» se elige de cero.
    casilla(p, "Llamar al dentista").locator("xpath=ancestor::label").tap()
    pastilla(p, "Todas")
    esperar_hoja_recordar(p, "«Todas» esconde la lista", alcance="todas", elegibles=None, guardar=True, quitar="Quitar todos los recordatorios",
                          resumen="Te lo recordaré cada día a las 9:00. También lo que apuntes después. Cambiará el recordatorio de 1 cosa que tenía otro.")
    pastilla(p, "Elegir")
    esperar_hoja_recordar(p, "al volver a «Elegir», ninguna marcada", elegibles=pendientes, guardar=False)
    # Dos elegidas (una sin recordatorio y otra con otro): cambian esas dos y nada más.
    for texto in ("Sacar la basura", "Pagar la comunidad"):
        casilla(p, texto).locator("xpath=ancestor::label").tap()
    elegir_hora(p, "18", "45")
    pastilla(p, "De lunes a viernes")
    esperar_hoja_recordar(p, "dos elegidas", elegibles=[["Llamar al dentista", False], ["Sacar la basura", True], ["Pagar la comunidad", True]], guardar=True, resumen="Te lo recordaré de lunes a viernes a las 18:45.")
    guardar_hoja_recordar(p)
    aviso = esperar_toast(p, "Recordatorio puesto a 2 cosas")
    igual(aviso["boton"], "Deshacer", "Botón del aviso al poner el recordatorio a las elegidas")
    tarde = recordatorio("18:45", "laborables")
    igual(leer_recordar(p), recordar_guardado(cosas={**propios["cosas"], "id-4": tarde, "id-3": tarde}), "Lo guardado tras «Elegir»: solo cambian las elegidas (y no hay recordatorio para todas)")
    igual(cuandos(p), {**antes, "Sacar la basura": "18:45 · De lunes a viernes", "Pagar la comunidad": "18:45 · De lunes a viernes"}, "Las filas tras «Elegir»")
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar(p, "(clave) => !('id-4' in JSON.parse(localStorage.getItem(clave)).cosas)", arg=CLAVE_RECORDAR, que="«Deshacer» devuelve lo de antes")
    igual((leer_recordar(p), cuandos(p)), (propios, antes), "Tras «Deshacer»: lo guardado y las filas")
    esperar_sin_toast(p, ms=1500)
    # Una sola: «1 cosa». Y lo que se apunte después no hereda nada: no es un recordatorio para todas.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p)
    pastilla(p, "Elegir")
    casilla(p, "Sacar la basura").locator("xpath=ancestor::label").tap()
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio puesto a 1 cosa")
    igual(leer_recordar(p), recordar_guardado(cosas={**propios["cosas"], "id-4": recordatorio()}), "Lo guardado tras elegir una sola")
    campo_en_grupo(p).fill("Apuntada después")
    campo_en_grupo(p).press("Enter")
    esperar(p, f"() => ({JS_RECORDADAS})()[0].texto === 'Apuntada después'", que="la cosa nueva aparece en Recordar")
    igual(cuandos(p)["Apuntada después"], "Poner recordatorio", "Lo apuntado después de «Elegir» hereda un recordatorio")


@prueba("hoja del recordatorio: diálogo modal accesible (role, aria-modal, fuera de <main>, que queda inerte y oculto; el foco entra en su título y vuelve a lo que la abrió, la fila o la campana), con el tabulador retenido dentro también en los desplegables y con «Guardar» apagado (con «inert» y sin él, como en iOS 15); Escape, el velo y «Cerrar» la cierran sin guardar; no toca el historial y se cierra sola al cambiar de vista")
def t_hoja_recordar_accesible(e):
    datos, propios = recordar_variado()
    p = sembrar_recordar(e, datos, propios)
    esperar_vista(p, "grupo")
    historial = p.evaluate("() => history.length")
    h = abrir_hoja_recordar(p, "Sacar la basura")
    igual((h["modal"], h["titulo"], h["focoDentro"], h["foco"], h["paginaInerte"], h["paginaOculta"]), ("true", "Recordatorio", True, "titulo-hoja-recordar", True, "true"), "Hoja recién abierta: foco dentro y el resto de la página fuera de juego")
    dialogo = p.get_by_role("dialog", name="Recordatorio", exact=True)
    igual((dialogo.count(), p.evaluate("() => { const d = document.querySelector('#hoja-recordar [role=dialog]'); return [d.tagName !== 'DIALOG', !document.querySelector('main').contains(d)]; }")), (1, [True, True]),
          "Un diálogo con el nombre de la hoja, sin depender de <dialog> y fuera de lo que queda inerte")
    cerrar = p.locator("#hoja-recordar").get_by_role("button", name="Cerrar", exact=True)
    caja_c, caja_g, panel = cerrar.bounding_box(), boton_guardar_recordar(p).bounding_box(), dialogo.bounding_box()
    igual((round(caja_c["width"], 2), round(caja_c["height"], 2), cerrar.evaluate("(b) => getComputedStyle(b).borderTopLeftRadius")), (48, 48, "50%"), "Botón «Cerrar» redondo")
    comprobar(caja_g["height"] >= 48 and caja_g["width"] >= panel["width"] - 48 and boton_guardar_recordar(p).get_attribute("type") == "submit", f"«Guardar» no es una píldora ancha que envía el formulario: {caja_g} en {panel}")
    comprobar(panel["y"] >= 23.5, f"Sobre la hoja no queda velo que tocar para cerrarla: {panel}")
    # El tabulador da vueltas dentro: «Cerrar», la hora, los minutos, la frecuencia, «Guardar» y otra vez «Cerrar».
    visitados = []
    for tecla in ["Tab"] * 7 + ["Shift+Tab"] * 7:
        p.keyboard.press(tecla)
        h = hoja_recordar(p)
        comprobar(h["focoDentro"], f"Con {tecla} el foco se sale de la hoja: {h['foco']}")
        visitados.append(h["foco"])
    igual(visitados[:6], ["cerrar-hoja-recordar", "hora-recordar", "minuto-recordar", "INPUT", "guardar-recordar", "cerrar-hoja-recordar"], "Orden de tabulación de la hoja (los desplegables cuentan)")
    igual(visitados[7:10], ["cerrar-hoja-recordar", "guardar-recordar", "INPUT"], "Con Mayús+Tab, de «Cerrar» al final de la hoja")
    # Con el teclado, cada control enseña su foco: la pastilla elegida, un anillo; el desplegable, un borde más grueso.
    reposo = p.evaluate("() => getComputedStyle(document.querySelector('#minuto-recordar')).boxShadow")
    p.locator("#hoja-recordar input[name=frecuencia]:checked").focus()
    p.keyboard.press("Shift+Tab")
    esperar(p, "(reposo) => document.activeElement.id === 'minuto-recordar' && getComputedStyle(document.activeElement).boxShadow !== reposo", arg=reposo, que="el desplegable de los minutos enseña el foco del teclado")
    p.keyboard.press("Tab")
    anillo = p.evaluate("() => { const cs = getComputedStyle(document.activeElement.nextElementSibling); return [document.activeElement.value, cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2]; }")
    igual(anillo, ["dia", True], "La pastilla elegida no enseña el anillo de foco con teclado")
    # Escape, el velo y «Cerrar»: cierran sin guardar y el foco vuelve a la fila que abrió la hoja.
    pastilla(p, "Cada mes")
    foco_en_la_fila = "() => { const el = document.activeElement; return [el.classList.contains('cuando'), el.closest('li') ? el.closest('li').querySelector('.texto').textContent : null]; }"
    p.keyboard.press("Escape")
    h = hoja_recordar(p)
    igual((h["abierta"], h["paginaInerte"], h["paginaOculta"], p.evaluate(foco_en_la_fila), leer_recordar(p)), (False, False, None, [True, "Sacar la basura"], propios), "Hoja tras Escape")
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p, "Sacar la basura")
    p.locator("#hoja-recordar").get_by_text("Frecuencia", exact=True).tap()  # Tocar la hoja en sí no la cierra.
    p.wait_for_timeout(150)
    comprobar(hoja_recordar(p)["abierta"], "Tocar dentro de la hoja la cierra")
    p.touchscreen.tap(e.ancho / 2, 12)
    esperar(p, f"() => !({JS_HOJA_RECORDAR})().abierta", que="tocar el velo cierra la hoja")
    igual((vista_visible(p), p.evaluate(foco_en_la_fila), leer_recordar(p)), ("grupo", [True, "Sacar la basura"], propios), "Tocar el velo acciona lo que hay debajo, no devuelve el foco o guarda algo")
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p, "Sacar la basura")
    cerrar.tap()
    igual((hoja_recordar(p)["abierta"], p.evaluate(foco_en_la_fila)), (False, [True, "Sacar la basura"]), "Hoja tras «Cerrar»")
    # Desde la campana, el foco vuelve a la campana.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p)
    p.keyboard.press("Escape")
    igual((hoja_recordar(p)["abierta"], p.evaluate("() => document.activeElement.id")), (False, "configurar-recordar"), "Tras cerrar la hoja abierta desde la campana")
    igual(p.evaluate("() => history.length"), historial, "La hoja añade entradas al historial")
    # Se cierra sola al cambiar de vista (atrás del sistema) y no vuelve a salir.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p)
    p.go_back()
    esperar_vista(p, "lista")
    h = hoja_recordar(p)
    igual((h["abierta"], h["paginaInerte"], h["paginaOculta"], p.evaluate("() => document.activeElement.id")), (False, False, None, "titulo-lista"), "Hoja tras el atrás del sistema")
    p.go_forward()
    esperar_vista(p, "grupo")
    comprobar(not hoja_recordar(p)["abierta"] and p.evaluate("() => document.querySelector('#configurar-recordar').matches(':not([inert] *)')"), "Al volver a Recordar, la hoja reaparece o la página sigue inerte")
    p.context.close()

    # «Elegir» sin nada marcado apaga «Guardar»: el último control ya no es un botón, y el tabulador sigue sin salir de la hoja. Tampoco donde no hay «inert».
    for etiqueta, guiones, opciones in (("con inert", (), {}), ("sin inert", (JS_SIN_INERT,), {"user_agent": UA_IPHONE})):
        contexto = e.contexto(**opciones)
        for guion in guiones:
            contexto.add_init_script(guion)
        p = sembrar_recordar(e, datos, propios, contexto=contexto)
        esperar_vista(p, "grupo")
        h = abrir_hoja_recordar(p)
        igual((h["paginaInerte"], h["paginaOculta"], h["focoDentro"]), (etiqueta == "con inert", "true", True), f"[{etiqueta}] La página, con la hoja abierta")
        for modo in ("todas", "elegir", "elegir un día"):
            if modo == "elegir":
                pastilla(p, "Elegir")
            if modo == "elegir un día":
                pastilla(p, "Cada semana")
                pastilla(p, "Miércoles")
            p.locator("#titulo-hoja-recordar").focus()
            visitados = set()
            for tecla in ["Tab"] * 12 + ["Shift+Tab"] * 12:
                p.keyboard.press(tecla)
                h = hoja_recordar(p)
                comprobar(h["focoDentro"], f"[{etiqueta}, {modo}] Con {tecla} el foco se sale de la hoja: {h['foco']}")
                visitados.add(h["foco"])
            comprobar({"cerrar-hoja-recordar", "hora-recordar", "minuto-recordar", "INPUT"} <= visitados and ("guardar-recordar" in visitados) == (modo == "todas"),
                      f"[{etiqueta}, {modo}] El tabulador no recorre la hoja: {sorted(visitados)}")
        tapados = p.evaluate("""() => ['#configurar-recordar', '#vista-grupo [data-volver]', '#lista-grupo .hecho', '#anadir-cosas'].map((s) => { const r = document.querySelector(s).getBoundingClientRect();
            const encima = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return encima !== null && document.querySelector('#hoja-recordar').contains(encima); })""")
        igual(tapados, [True, True, True, True], f"[{etiqueta}] Controles de la página tapados por la hoja o su velo")
        p.locator("#hoja-recordar").get_by_role("button", name="Cerrar", exact=True).tap()
        igual((hoja_recordar(p)["abierta"], p.evaluate("() => document.activeElement.id")), (False, "configurar-recordar"), f"[{etiqueta}] Hoja tras «Cerrar»")
        p.context.close()


@prueba("hoja del recordatorio y dobles toques: recién abierta, la hoja no recibe toques (el segundo toque sobre la fila o la campana no la cierra ni cambia una pastilla que aparezca debajo); pasada la guarda, responde; y un doble toque en «Guardar» o en «Quitar recordatorio» actúa una vez, sin accionar lo que queda debajo al cerrarse la hoja (la barra de escribir y su micrófono)")
def t_hoja_recordar_dobles_toques(e):
    datos, propios = recordar_variado()
    contexto = e.contexto()
    contexto.add_init_script(JS_DICTADO_FALSO)
    p = sembrar_recordar(e, datos, propios, contexto=contexto)
    esperar_vista(p, "grupo")
    # Dónde aparecen, con la hoja abierta, una pastilla y «Cerrar».
    abrir_hoja_recordar(p, "Sacar la basura")
    cajas = {nombre: localizador.bounding_box() for nombre, localizador in (
        ("laborables", p.locator("#hoja-recordar").get_by_role("radio", name="De lunes a viernes", exact=True).locator("xpath=ancestor::label")),
        ("cerrar", p.locator("#hoja-recordar").get_by_role("button", name="Cerrar", exact=True)))}
    centro = {nombre: (c["x"] + c["width"] / 2, c["y"] + c["height"] / 2) for nombre, c in cajas.items()}
    cerrar_hoja_recordar(p)
    chip = cuando_de(p, "Sacar la basura").bounding_box()
    for pausa in (0, 250):
        for segundo in ("laborables", "cerrar"):
            p.touchscreen.tap(chip["x"] + chip["width"] / 2, chip["y"] + chip["height"] / 2)
            if pausa:
                p.wait_for_timeout(pausa)
            else:
                igual((p.locator("#hoja-recordar").get_attribute("data-abriendo"), p.evaluate("() => getComputedStyle(document.querySelector('#hoja-recordar [role=dialog]')).pointerEvents")), ("", "none"),
                      "Recién abierta, la hoja no lleva la marca que le quita los toques")
            p.touchscreen.tap(*centro[segundo])
            p.wait_for_timeout(500)
            h = hoja_recordar(p)
            igual((h["abierta"], h["cosa"], h["frecuencia"], p.locator("#hoja-recordar").get_attribute("data-abriendo")), (True, "Sacar la basura", "dia", None),
                  f"Tras un segundo toque {pausa} ms después de abrir la hoja, donde aparece {'una pastilla' if segundo == 'laborables' else '«Cerrar»'}")
            cerrar_hoja_recordar(p)
    # El doble toque de verdad: los dos sobre lo que abre la hoja (la fila; la campana, que queda junto a «Cerrar»).
    for abridor, etiqueta, pausas in ((cuando_de(p, "Sacar la basura"), "la fila", (150,)), (boton_configurar(p), "la campana", (0, 150, 250))):
        for pausa in pausas:
            doble_toque(p, abridor, pausa)
            p.wait_for_timeout(500)
            h = hoja_recordar(p)
            igual((h["abierta"], h["hora"], h["minuto"], h["frecuencia"], h["alcance"]), (True, "09", "00", "dia", None if etiqueta == "la fila" else "todas"), f"Tras un doble toque ({pausa} ms) en {etiqueta}")
            cerrar_hoja_recordar(p)
    # Pasada la guarda, la hoja responde.
    abrir_hoja_recordar(p, "Sacar la basura")
    p.touchscreen.tap(*centro["laborables"])
    esperar_hoja_recordar(p, "pasada la guarda, la pastilla se elige", frecuencia="laborables")
    # Doble toque en «Guardar» (sobre su extremo derecho: debajo queda el micrófono de la barra de la página): se guarda una vez y,
    # cerrada la hoja, lo que hay bajo el dedo no se acciona: ni se despliega «Añadir cosas», ni se enfoca el campo, ni se empieza a dictar.
    caja = boton_guardar_recordar(p).bounding_box()
    x, y = caja["x"] + caja["width"] - 24, caja["y"] + caja["height"] / 2
    micro_debajo = p.evaluate("([x, y]) => { const r = document.querySelector('#dictar-en-grupo').getBoundingClientRect(); return r.left <= x && x <= r.right && r.top <= y && y <= r.bottom; }", [x, y])
    comprobar(micro_debajo, f"La prueba no toca sobre el micrófono de la barra: ({x}, {y})")
    p.touchscreen.tap(x, y)
    p.wait_for_timeout(200)
    p.touchscreen.tap(x, y)
    p.wait_for_timeout(500)
    estado = pagina_grupo(p)
    igual((hoja_recordar(p)["abierta"], estado["desplegable"], reconocimientos(p), p.evaluate("() => document.activeElement.closest('#formulario-en-grupo, #barra-anadir') !== null"), (toast(p) or {}).get("texto"), leer_recordar(p)),
          (False, False, [], False, "Recordatorio guardado", recordar_guardado(cosas={**propios["cosas"], "id-4": recordatorio("09:00", "laborables")})),
          "Tras un doble toque en «Guardar»: la hoja, el desplegable de la página, dictados empezados, el foco (¿en la barra?), el aviso y lo guardado")
    p.locator("#dictar-en-grupo").tap()
    igual(len(reconocimientos(p)), 1, "Pasada la guarda, la barra de la página no responde")
    p.locator("#dictar-en-grupo").tap()  # (Se deja de dictar.)
    p.evaluate("() => window.__dictado.fin()")
    # Tampoco un doble toque en «Quitar recordatorio» se lleva nada más por delante.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p, "Sacar la basura")
    doble_toque(p, p.locator("#hoja-recordar").get_by_role("button", name="Quitar recordatorio", exact=True), 200)
    p.wait_for_timeout(500)
    igual((hoja_recordar(p)["abierta"], leer_recordar(p), [f["texto"] for f in recordadas(p)]), (False, propios, ["Llamar al dentista", "Sacar la basura", "Pagar la comunidad", "Hecha sin recordatorio", "Regar las plantas"]),
          "Tras un doble toque en «Quitar recordatorio»: la hoja, lo guardado y las cosas de Recordar")


# La hoja del recordatorio en la pantalla: el panel, su pie (que no se desplaza) con «Guardar», lo que se sale y lo que se recorta.
JS_MEDIDAS_HOJA_RECORDAR = r"""
() => {
  const hoja = document.querySelector('#hoja-recordar'), panel = hoja.querySelector('[role=dialog]'), pie = hoja.querySelector('.pie-hoja'), guardar = hoja.querySelector('#guardar-recordar');
  const caja = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, d: r.right, b: r.bottom, ancho: r.width, alto: r.height }; };
  const visibles = [panel, ...panel.querySelectorAll('*')].filter((el) => el.checkVisibility() && el.getBoundingClientRect().width > 0);
  const g = guardar.getBoundingClientRect(), encima = document.elementFromPoint(g.left + g.width / 2, g.top + g.height / 2);
  const ayudas = [...hoja.querySelectorAll('p.ayuda')].filter((el) => el.checkVisibility());
  return {
    alto: innerHeight, ancho: innerWidth, panel: caja(panel), pie: caja(pie), guardar: caja(guardar), alDedo: encima === guardar, pegado: getComputedStyle(pie).position,
    fuera: visibles.filter((el) => { const r = el.getBoundingClientRect(); return r.left < -0.5 || r.right > innerWidth + 0.5; }).map((el) => el.tagName + '.' + el.className),
    desbordePanel: panel.scrollWidth - panel.clientWidth, desbordePagina: document.documentElement.scrollWidth - innerWidth,
    desplazable: panel.scrollHeight > panel.clientHeight + 0.5, desplazamiento: panel.scrollTop,
    tituloRecortado: (JS_RECORTADO)(hoja.querySelector('h2')), cerrar: caja(hoja.querySelector('#cerrar-hoja-recordar')),
    ultimaAyuda: caja(ayudas[ayudas.length - 1]), textoUltimaAyuda: ayudas[ayudas.length - 1].textContent,
    pastillasRecortadas: visibles.filter((el) => el.matches('.pastilla span')).filter((el) => (JS_RECORTADO)(el)).map((el) => el.textContent),
  };
}
""".replace("JS_RECORTADO", JS_RECORTADO)


def comprobar_pie_de_la_hoja(pagina, donde):
    """«Guardar» está a la vista y al alcance del dedo en el pie de la hoja, que no se desplaza con ella; la hoja cabe, pegada abajo, y nada se sale ni se recorta."""
    m = pagina.evaluate(JS_MEDIDAS_HOJA_RECORDAR)
    panel, pie, guardar = m["panel"], m["pie"], m["guardar"]
    comprobar(panel["x"] >= -0.5 and panel["d"] <= m["ancho"] + 0.5 and panel["y"] >= 23.5 and abs(panel["b"] - m["alto"]) <= 0.5, f"{donde}: la hoja no está pegada abajo, se sale o no deja velo arriba: {panel} en {m['ancho']}x{m['alto']}")
    igual((m["fuera"], m["desbordePanel"] <= 0, m["desbordePagina"] <= 0, m["tituloRecortado"], m["pastillasRecortadas"]), ([], True, True, False, []), f"{donde}: lo que se sale de la hoja o sale recortado")
    comprobar(guardar["y"] >= panel["y"] and guardar["b"] <= m["alto"] + 0.5 and guardar["x"] >= 0 and guardar["d"] <= m["ancho"] + 0.5 and guardar["alto"] >= 44 and m["alDedo"],
              f"{donde}: «Guardar» no está entero a la vista y al alcance del dedo: {guardar} en {m['ancho']}x{m['alto']}")
    comprobar(m["pegado"] == "sticky" and abs(pie["b"] - m["alto"]) <= 0.5 and pie["y"] <= guardar["y"] and abs(pie["x"] - panel["x"]) <= 0.5 and abs(pie["d"] - panel["d"]) <= 0.5,
              f"{donde}: el pie de la hoja no va pegado abajo, a todo su ancho: {pie} / {panel}")
    comprobar(m["cerrar"]["d"] <= m["ancho"] + 0.5 and m["cerrar"]["ancho"] >= 44, f"{donde}: «Cerrar» se sale o se encoge: {m['cerrar']}")
    if m["desplazable"]:
        # Desplazada hasta el final: «Guardar» sigue en su sitio y lo último de la hoja (dónde se guarda lo que se recuerda) queda por encima del pie.
        pagina.evaluate("() => { const panel = document.querySelector('#hoja-recordar [role=dialog]'); panel.scrollTop = panel.scrollHeight; }")
        pagina.wait_for_timeout(100)
        final = pagina.evaluate(JS_MEDIDAS_HOJA_RECORDAR)
        igual((final["alDedo"], final["textoUltimaAyuda"]), (True, PRIVACIDAD_HOJA), f"{donde}: con la hoja desplazada hasta el final, «Guardar» y lo último que dice la hoja")
        comprobar(all(abs(final["guardar"][lado] - guardar[lado]) <= 1 for lado in ("x", "y", "d", "b")), f"{donde}: con la hoja desplazada hasta el final, «Guardar» se mueve: {guardar} -> {final['guardar']}")
        comprobar(final["desplazamiento"] > 0 and final["ultimaAyuda"]["b"] <= final["pie"]["y"] + 0.5 and final["ultimaAyuda"]["y"] >= final["panel"]["y"],
                  f"{donde}: el final de la hoja queda bajo el pie: {final['ultimaAyuda']} / {final['pie']}")
        pagina.evaluate("() => { document.querySelector('#hoja-recordar [role=dialog]').scrollTop = 0; }")
    else:
        igual(m["textoUltimaAyuda"], PRIVACIDAD_HOJA, f"{donde}: lo último de la hoja, sobre el pie")
        comprobar(m["ultimaAyuda"]["b"] <= pie["y"] + 0.5, f"{donde}: el final de la hoja queda bajo el pie: {m['ultimaAyuda']} / {pie}")
    return m


@prueba("hoja del recordatorio: «Guardar» va en un pie que no se desplaza y está siempre a la vista (vertical, apaisado, 320 px, zoom al 200 % y «Elegir» con 40 cosas, que se desplazan por dentro); la hoja cabe pegada abajo, nada desborda ni se recorta, lo que toma el foco se ve sobre el pie y la nota de dónde se guarda queda encima de él")
def t_hoja_recordar_pie(e):
    cuarenta = estado_recordar([recordada(i, f"Cosa número {i} de las que hay que acordarse", hecha=i % 8 == 0) for i in range(1, 41)])
    propios = recordar_guardado(cosas={"id-39": recordatorio("21:30", "semana", dia=2)})
    for etiqueta, ancho, alto in (("vertical", e.ancho, e.alto), ("apaisado", e.alto, e.ancho), ("320", 320, 568), ("zoom al 200 %", 195, 400)):
        p = sembrar_recordar(e, cuarenta, propios, contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_vista(p, "grupo")
        # Una cosa con recordatorio semanal: la hoja más alta de una sola cosa (los siete días y «Quitar recordatorio»).
        h = abrir_hoja_recordar(p, "Cosa número 39 de las que hay que acordarse")
        igual((h["frecuencia"], h["dia"], h["quitar"]), ("semana", "2", "Quitar recordatorio"), f"[{etiqueta}] La hoja de una cosa con recordatorio semanal")
        comprobar_pie_de_la_hoja(p, f"[{etiqueta}] hoja de una cosa")
        # Con el teclado, lo que toma el foco se trae a la vista por encima del pie, no detrás.
        p.locator("#titulo-hoja-recordar").focus()
        for _ in range(12):
            p.keyboard.press("Tab")
            visto = p.evaluate("""() => { const el = document.activeElement, pie = document.querySelector('#hoja-recordar .pie-hoja'), panel = document.querySelector('#hoja-recordar [role=dialog]');
                const r = (el.type === 'radio' ? el.closest('label') : el).getBoundingClientRect();
                return { foco: el.id || el.value, dentro: panel.contains(el), enElPie: pie.contains(el), y: r.top, b: r.bottom, pie: pie.getBoundingClientRect().top, panel: panel.getBoundingClientRect().top }; }""")
            comprobar(visto["dentro"] and (visto["enElPie"] or (visto["b"] <= visto["pie"] + 0.5 and visto["y"] >= visto["panel"] - 0.5)), f"[{etiqueta}] Lo que toma el foco con el tabulador queda tapado por el pie o fuera de la hoja: {visto}")
        cerrar_hoja_recordar(p)
        # Desde la campana, «Elegir»: 35 cosas pendientes en una lista que se desplaza por dentro; «Guardar» sigue a la vista.
        abrir_hoja_recordar(p)
        comprobar_pie_de_la_hoja(p, f"[{etiqueta}] hoja desde la campana")
        pastilla(p, "Elegir")
        esperar(p, "() => document.querySelectorAll('#elegir-recordar li').length === 35", que="«Elegir» lista las 35 cosas pendientes")
        m = comprobar_pie_de_la_hoja(p, f"[{etiqueta}] hoja con «Elegir» y 35 cosas")
        lista = p.evaluate("""() => { const ul = document.querySelector('#elegir-recordar'), r = ul.getBoundingClientRect();
            return { alto: r.height, desplazable: ul.scrollHeight > ul.clientHeight + 0.5, desborde: ul.scrollWidth - ul.clientWidth,
                     lineas: Math.max(...[...ul.querySelectorAll('.elegible-texto')].map((t) => t.getBoundingClientRect().height / parseFloat(getComputedStyle(t).lineHeight))) }; }""")
        comprobar(lista["desplazable"] and lista["alto"] <= alto * 0.36 and lista["desborde"] <= 0 and lista["lineas"] <= 2.1, f"[{etiqueta}] La lista de «Elegir» no se desplaza por dentro, ocupa demasiado o no corta cada cosa a dos líneas: {lista}")
        ultima = casilla(p, "Cosa número 1 de las que hay que acordarse").locator("xpath=ancestor::label")
        ultima.scroll_into_view_if_needed()
        caja = ultima.bounding_box()
        comprobar(caja["y"] >= m["panel"]["y"] and caja["y"] + caja["height"] <= m["pie"]["y"] + 0.5, f"[{etiqueta}] La última cosa de «Elegir» no se alcanza sobre el pie: {caja} / {m['pie']}")
        igual(hoja_recordar(p)["guardar"], False, f"[{etiqueta}] «Guardar» sin nada elegido")
        ultima.tap()
        esperar_hoja_recordar(p, "con la última elegida se puede guardar", guardar=True)
        comprobar(abs(p.evaluate(JS_MEDIDAS_HOJA_RECORDAR)["guardar"]["y"] - m["guardar"]["y"]) <= 1, f"[{etiqueta}] Al elegir una cosa, «Guardar» se mueve")
        guardar_hoja_recordar(p)
        aviso = esperar_toast(p, "Recordatorio puesto a 1 cosa")
        comprobar(aviso["x"] >= 0 and aviso["x"] + aviso["ancho"] <= ancho + 0.5 and not p.evaluate(f"() => ({JS_RECORTADO})(document.querySelector('#toast .toast-texto'))"), f"[{etiqueta}] El aviso con «Deshacer» se sale o sale recortado: {aviso}")
        igual(leer_recordar(p)["cosas"].get("id-1"), recordatorio(), f"[{etiqueta}] Lo guardado para la última cosa de la lista")
        p.context.close()


@prueba("Recordar con textos difíciles: código, 500 caracteres y una palabra de 200 se pintan literales y sin desbordar en su fila (con el recordatorio dentro), en la hoja de su recordatorio (tres líneas como mucho), en la lista de «Elegir» (dos) y en la hoja «Hecho o Mantener»; un ?recordada con código no abre nada ni se queda en la dirección")
def t_recordar_xss_y_largos(e):
    largo = ("Lorem ipsum dolor sit amet " * 30)[:500].strip()
    palabra = "A" * 200
    datos = estado_recordar([recordada(3, XSS), recordada(2, largo), recordada(1, palabra)])
    propios = recordar_guardado(cosas={"id-3": recordatorio("21:30", "semana", dia=3)})
    inyectado = "() => [document.querySelectorAll('main img, main script, main b, .hoja img, .hoja script, .hoja b').length, window.__xss === undefined]"
    for ancho, alto in ((e.ancho, e.alto), (320, 568)):
        p = sembrar_recordar(e, datos, propios, contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_vista(p, "grupo")
        igual([(f["texto"], f["cuando"]) for f in recordadas(p)], [(XSS, "21:30 · Cada miércoles"), (largo, "Poner recordatorio"), (palabra, "Poner recordatorio")], f"Textos literales en las filas de Recordar a {ancho}px")
        igual(p.evaluate("() => [...document.querySelectorAll('#lista-grupo .texto')].map((t) => t.childElementCount)"), [0, 0, 0], "Hijos elemento dentro de los textos")
        comprobar_sin_desborde(p, f"Recordar con textos largos a {ancho}px")
        for fila in p.locator("#lista-grupo > li").all():
            d = fila.evaluate("""(f) => { const r = f.getBoundingClientRect(), t = f.querySelector('.texto').getBoundingClientRect(), b = f.querySelector('.borrar:not([hidden])').getBoundingClientRect(), c = f.querySelector('.cuando').getBoundingClientRect();
                const rango = document.createRange(); rango.selectNodeContents(f.querySelector('.texto')); const tinta = rango.getBoundingClientRect();
                return { desborde: f.scrollWidth - f.clientWidth, fila: [r.left, r.right, r.bottom], texto: [t.left, t.right, t.bottom], tinta: [tinta.left, tinta.right], boton: [b.left, b.right, b.width], cuando: [c.left, c.right, c.top, c.bottom] }; }""")
            comprobar(d["desborde"] <= 0 and d["tinta"][1] <= d["boton"][0] + 0.5 and d["texto"][1] <= d["boton"][0] + 0.5 and d["boton"][1] <= d["fila"][1] + 0.5 and d["boton"][2] >= 44, f"El texto pisa el botón de la derecha o la fila desborda a {ancho}px: {d}")
            comprobar(d["cuando"][2] >= d["texto"][2] - 0.5 and d["cuando"][0] >= d["fila"][0] and d["cuando"][1] <= d["fila"][1] - 8 and d["cuando"][3] <= d["fila"][2], f"El recordatorio no queda bajo el texto, dentro de la fila, a {ancho}px: {d}")
        # La hoja de cada una: su texto, literal y en tres líneas como mucho.
        for texto in (XSS, largo, palabra):
            h = abrir_hoja_recordar(p, texto)
            igual(h["cosa"], texto, f"El texto de la cosa en su hoja a {ancho}px")
            cosa_hoja = p.evaluate("() => { const c = document.querySelector('#cosa-recordar'); return [c.childElementCount, c.getBoundingClientRect().height / parseFloat(getComputedStyle(c).lineHeight)]; }")
            comprobar(cosa_hoja[0] == 0 and cosa_hoja[1] <= 3.1, f"El texto de la cosa en su hoja crea elementos u ocupa más de tres líneas a {ancho}px: {cosa_hoja}")
            comprobar_pie_de_la_hoja(p, f"hoja de «{texto[:12]}…» a {ancho}px")
            cerrar_hoja_recordar(p)
        # «Elegir»: las tres, literales, a dos líneas como mucho.
        abrir_hoja_recordar(p)
        pastilla(p, "Elegir")
        esperar_hoja_recordar(p, "«Elegir» con textos difíciles", elegibles=[[XSS, False], [largo, False], [palabra, False]])
        elegibles = p.evaluate("() => [...document.querySelectorAll('#elegir-recordar .elegible-texto')].map((t) => [t.childElementCount, t.getBoundingClientRect().height / parseFloat(getComputedStyle(t).lineHeight) <= 2.1])")
        igual(elegibles, [[0, True]] * 3, f"Las cosas de «Elegir»: sin elementos creados y en dos líneas como mucho a {ancho}px")
        comprobar_pie_de_la_hoja(p, f"hoja con «Elegir» y textos difíciles a {ancho}px")
        casilla(p, XSS).locator("xpath=ancestor::label").tap()
        guardar_hoja_recordar(p)
        esperar_toast(p, "Recordatorio puesto a 1 cosa")
        igual((cuandos(p)[XSS], p.evaluate(inyectado)), ("9:00 · Cada día", [0, True]), f"Tras guardar el recordatorio de la cosa con código a {ancho}px")
        # La hoja «Hecho o Mantener» (se llega con ?recordada=ID): el texto, literal y entero.
        for id, texto in (("id-3", XSS), ("id-2", largo), ("id-1", palabra)):
            p.goto(e.base + f"?recordada={id}#grupo/recordar")
            esperar(p, "() => document.querySelector('#hoja-avisada').checkVisibility()", que="se abre la hoja «Hecho o Mantener»")
            avisada = p.evaluate("""() => { const hoja = document.querySelector('#hoja-avisada'), panel = hoja.querySelector('[role=dialog]'), t = hoja.querySelector('.avisada-texto');
                const visibles = [panel, ...panel.querySelectorAll('*')].filter((el) => el.checkVisibility());
                return { texto: t.textContent, hijos: t.childElementCount, fuera: visibles.filter((el) => { const r = el.getBoundingClientRect(); return r.left < -0.5 || r.right > innerWidth + 0.5; }).length,
                         desborde: panel.scrollWidth - panel.clientWidth, botones: [...hoja.querySelectorAll('.avisada button')].map((b) => { const r = b.getBoundingClientRect(); return [b.textContent, r.height >= 44, r.right <= innerWidth]; }) }; }""")
            igual(avisada, {"texto": texto, "hijos": 0, "fuera": 0, "desborde": 0, "botones": [["Hecho", True, True], ["Mantener", True, True]]}, f"La hoja «Hecho o Mantener» de «{texto[:12]}…» a {ancho}px")
            p.wait_for_timeout(450)
            p.keyboard.press("Escape")
        # Un ?recordada con código: no es de ninguna cosa. No abre nada, no se ejecuta y no se queda en la dirección.
        p.goto(e.base + "?recordada=%3Cimg%20src%3Dx%20onerror%3Dwindow.__xss%3D4%3E#grupo/recordar")
        esperar_vista(p, "grupo")
        p.wait_for_timeout(300)
        igual((p.locator("#hoja-avisada").is_hidden(), p.evaluate("() => location.search + location.hash"), p.evaluate(inyectado)), (True, "#grupo/recordar", [0, True]), f"Tras abrir con un ?recordada con código a {ancho}px")
        p.context.close()


# Por qué este dispositivo no va a avisar de lo que hay en Recordar: lo dice la nota de arriba de su página (y la hoja del recordatorio).
NOTAS_RECORDAR = {
    "apagados": "Para que te avise, activa las notificaciones en este dispositivo.",
    "instalar": "En iPhone, los recordatorios llegan a la aplicación: conviértela en aplicación y ponlos desde ella.",
    "bloqueados": "Las notificaciones de Cosas están bloqueadas en este dispositivo. Para que te avise, permítelas en los ajustes del móvil o del navegador.",
    "imposible": "Este navegador no puede recibir notificaciones: aquí no te avisará.",
    "sin-conexion": "Sin conexión: tus recordatorios empezarán a avisar cuando vuelva.",
}

JS_NOTA_RECORDAR = r"""
() => {
  const nota = document.querySelector('#nota-recordar'), boton = document.querySelector('#boton-nota-recordar');
  return nota.checkVisibility() ? { texto: document.querySelector('#texto-nota-recordar').textContent, boton: boton.checkVisibility() ? boton.textContent : null, papel: nota.getAttribute('role') } : null;
}
"""


def nota_recordar(pagina):
    """La nota de Recordar: None si no se ve; si se ve, su texto, el de su botón (o None) y su papel."""
    return pagina.evaluate(JS_NOTA_RECORDAR)


def esperar_nota(pagina, estado, boton=None):
    """Espera a que la nota diga lo de ese estado (None: a que no se vea), con ese botón o sin ninguno."""
    esperado = None if estado is None else {"texto": NOTAS_RECORDAR.get(estado, estado), "boton": boton, "papel": "status"}
    try:
        pagina.wait_for_function(f"(esperado) => JSON.stringify(({JS_NOTA_RECORDAR})()) === esperado", arg=en_bruto(esperado), timeout=3000)
    except TiempoAgotado:
        igual(nota_recordar(pagina), esperado, f"La nota de Recordar ({estado})")


def ayuda_de_la_hoja(pagina, texto):
    """Lo que la hoja del recordatorio de esa cosa dice de las notificaciones de este dispositivo (se abre y se cierra sin guardar)."""
    ayuda = abrir_hoja_recordar(pagina, texto)["ayuda"]
    cerrar_hoja_recordar(pagina)
    return ayuda


@prueba("nota de Recordar: mientras haya algo pendiente con recordatorio y este dispositivo no vaya a avisar, su página lo dice arriba (role=status) y, si se arregla aquí, con su botón: sin activar («Activar notificaciones», que pide el permiso en ese mismo toque, como «Guardar»), iPhone o iPad en Safari («Convertir en aplicación», que abre los pasos), bloqueadas, imposibles o sin conexión; la hoja dice lo mismo antes de guardar; con las notificaciones activadas, o sin nada pendiente que recordar, no sale")
def t_nota_recordar(e):
    datos = estado_recordar([recordada(3, "Con recordatorio"), recordada(2, "Sin recordatorio"), cosa(1, "Suelta")], grupos=[grupo(9, "Casa")])
    propios = recordar_guardado(cosas={"id-3": recordatorio()})
    # Bloqueadas (el permiso denegado: así está el navegador de las pruebas): la nota lo dice, sin botón (aquí no tiene arreglo).
    p = sembrar_recordar(e, datos, propios)
    esperar_vista(p, "grupo")
    esperar_nota(p, "bloqueados")
    m = p.evaluate(f"""() => {{ const caja = (el) => {{ const r = el.getBoundingClientRect(); return {{ x: r.left, y: r.top, d: r.right, b: r.bottom }}; }};
        const nota = document.querySelector('#nota-recordar'), texto = document.querySelector('#texto-nota-recordar');
        return {{ nota: caja(nota), cabecera: caja(document.querySelector('#vista-grupo header')), fila: caja(document.querySelector('#lista-grupo > li')), padre: nota.parentElement.id,
                 antesDeLaLista: nota.nextElementSibling.id, recortado: ({JS_RECORTADO})(texto), fuente: parseFloat(getComputedStyle(texto).fontSize) }}; }}""")
    igual((m["padre"], m["antesDeLaLista"], m["recortado"]), ("cuerpo-grupo", "lista-grupo", False), "La nota: en el cuerpo de la página, antes de la lista y sin recortar")
    comprobar(m["cabecera"]["b"] <= m["nota"]["y"] and m["nota"]["b"] <= m["fila"]["y"] and abs(m["nota"]["x"] - m["fila"]["x"]) <= 0.5 and abs(m["nota"]["d"] - m["fila"]["d"]) <= 0.5 and m["fuente"] >= 14,
              f"La nota no va entre la cabecera y la primera cosa, al ancho de las filas: {m}")
    igual(ayuda_de_la_hoja(p, "Con recordatorio"), NOTAS_RECORDAR["bloqueados"], "Lo que dice la hoja con las notificaciones bloqueadas")
    # Solo mientras haya algo PENDIENTE con recordatorio: hecha, sin él o fuera de Recordar, no hay nada de lo que avisar.
    check_de(fila_recordada(p, "Con recordatorio")).tap()
    esperar_nota(p, None)
    check_de(fila_recordada(p, "Con recordatorio")).tap()
    esperar_nota(p, "bloqueados")
    abrir_hoja_recordar(p, "Con recordatorio")
    p.locator("#hoja-recordar").get_by_role("button", name="Quitar recordatorio", exact=True).tap()
    esperar_toast(p, "Recordatorio quitado")
    esperar_nota(p, None)
    # Al guardar un recordatorio solo se dice «Recordatorio guardado»: por qué no va a avisar lo explica la nota, que se queda.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p, "Sin recordatorio")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    esperar_nota(p, "bloqueados")
    p.wait_for_timeout(600)
    igual(toast(p)["texto"], "Recordatorio guardado", "Tras guardar, otro aviso sustituye a «Recordatorio guardado»")
    quitar_de_recordar(p, "Sin recordatorio").tap()
    esperar_toast(p, "Quitado de Recordar")
    esperar_nota(p, None)
    p.get_by_role("button", name="Deshacer", exact=True).tap()
    esperar_nota(p, "bloqueados")
    # En la página de otro grupo no sale nunca.
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    abrir_grupo(p, "Casa")
    igual(nota_recordar(p), None, "La nota de Recordar sale en la página de otro grupo")
    p.context.close()

    # Sin activar en este dispositivo: la nota ofrece activarlas, y el permiso se pide dentro de ese mismo toque.
    pide_permiso = "Al guardar te pediremos permiso para enviarte notificaciones. " + AYUDA_HOJA_CON_BOTONES
    p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e, "window.__avisos.respuesta = 'default';"))
    esperar_vista(p, "grupo")
    esperar_nota(p, "apagados", "Activar notificaciones")
    boton = p.locator("#nota-recordar").get_by_role("button", name="Activar notificaciones", exact=True)
    medidas = boton.evaluate("""(b) => { const r = b.getBoundingClientRect(), n = document.querySelector('#texto-nota-recordar').getBoundingClientRect(), cs = getComputedStyle(b);
        return [b.type, r.height >= 48, Math.abs(r.width - n.width) <= 1, r.top >= n.bottom, parseFloat(cs.borderTopLeftRadius) >= r.height / 2 - 0.5, parseFloat(cs.fontSize) >= 16]; }""")
    igual(medidas, ["button", True, True, True, True, True], "El botón de la nota: una píldora de 48 px o más, a todo su ancho, bajo el texto")
    igual(ayuda_de_la_hoja(p, "Con recordatorio"), pide_permiso, "Lo que dice la hoja con las notificaciones sin activar")
    boton.tap()
    esperar(p, "() => window.__avisos.pedidos === 1 && document.querySelector('#boton-nota-recordar').getAttribute('aria-busy') === null", que="el toque en «Activar notificaciones» pide el permiso")
    esperar_nota(p, "apagados", "Activar notificaciones")  # Pregunta cerrada sin elegir: nada cambia ni se avisa.
    igual((toast(p) or {}).get("texto") or "", "", "Cerrar la pregunta del permiso sin elegir avisa de algo")
    p.evaluate("() => { window.__avisos.respuesta = 'denied'; }")
    boton.tap()
    esperar_nota(p, "bloqueados")
    igual((p.evaluate("() => window.__avisos.pedidos"), p.evaluate("() => document.activeElement.id"), p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE_AVISOS)), (2, "titulo-grupo", None),
          "Permiso denegado desde la nota: se pidió, el foco no se queda en el botón que se va y no queda nada activado")
    p.context.close()
    # También «Guardar» lo pide en su toque, y solo dice «Recordatorio guardado».
    p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e, "window.__avisos.respuesta = 'denied';"))
    esperar_vista(p, "grupo")
    abrir_hoja_recordar(p, "Sin recordatorio")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    esperar_nota(p, "bloqueados")
    igual((p.evaluate("() => window.__avisos.pedidos"), toast(p)["texto"], leer_recordar(p)["cosas"].get("id-2")), (1, "Recordatorio guardado", recordatorio()), "Al guardar sin las notificaciones activadas: permiso pedido, un solo aviso y el recordatorio guardado")
    p.context.close()
    # Con el permiso ya dado pero sin activar aquí: la misma nota; la hoja dice que se activarán solas al guardar.
    p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e))
    esperar_vista(p, "grupo")
    esperar_nota(p, "apagados", "Activar notificaciones")
    igual(ayuda_de_la_hoja(p, "Con recordatorio"), "Al guardar se activarán las notificaciones en este dispositivo. " + AYUDA_HOJA_CON_BOTONES, "Lo que dice la hoja con el permiso dado y sin activar")
    p.context.close()

    # iPhone y iPad en Safari (sin push hasta instalarla): la nota ofrece convertirla en aplicación y abre los pasos.
    tactil = "Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { configurable: true, get: () => 5 });"
    for agente, guion, dispositivo_ios in ((UA_IPHONE, JS_SIN_PUSH, "iPhone"), (UA_IPAD, JS_SIN_PUSH + tactil, "iPad")):
        texto = NOTAS_RECORDAR["instalar"].replace("iPhone", dispositivo_ios)
        p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e, guion, user_agent=agente))
        esperar_vista(p, "grupo")
        esperar_nota(p, texto, "Convertir en aplicación")
        igual(ayuda_de_la_hoja(p, "Con recordatorio"), texto, f"Lo que dice la hoja en {dispositivo_ios} sin instalar")
        p.locator("#nota-recordar").get_by_role("button", name="Convertir en aplicación", exact=True).tap()
        esperar(p, f"() => ({JS_HOJA})().abierta", que="el botón de la nota abre la hoja de pasos")
        pasos = hoja(p)
        igual((pasos["titulo"], pasos["pasos"], pasos["focoDentro"], p.evaluate("() => window.__avisos.pedidos")), ("Añadir a la pantalla de inicio", PASOS_IOS, True, 0), f"La hoja de pasos abierta desde la nota en {dispositivo_ios} (sin pedir ningún permiso)")
        p.wait_for_timeout(450)
        p.keyboard.press("Escape")
        cerrada = hoja(p)
        igual((cerrada["abierta"], cerrada["foco"], cerrada["paginaInerte"]), (False, "boton-nota-recordar", False), "Al cerrar los pasos, el foco vuelve al botón de la nota")
        p.context.close()
    # Un navegador sin push: no hay nada que hacer aquí, y se dice.
    p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e, JS_SIN_PUSH, user_agent=UA_ANDROID))
    esperar_vista(p, "grupo")
    esperar_nota(p, "imposible")
    igual(ayuda_de_la_hoja(p, "Con recordatorio"), NOTAS_RECORDAR["imposible"], "Lo que dice la hoja en un navegador sin push")
    p.context.close()

    # Activadas: sin nota. La hoja dice cómo llegará el aviso (con botones o, donde no los hay, al tocarlo). Sin conexión, la nota avisa mientras dure.
    for guion, ayuda in ((CON_PERMISO, AYUDA_HOJA_CON_BOTONES), (CON_PERMISO + "Object.defineProperty(Notification, 'maxActions', { configurable: true, get: () => 0 });", AYUDA_HOJA_SIN_BOTONES)):
        p = sembrar_recordar(e, datos, propios, contexto=contexto_con_avisos(e, guion), avisos=AVISOS_ACTIVOS)
        esperar_vista(p, "grupo")
        igual((nota_recordar(p), ayuda_de_la_hoja(p, "Con recordatorio")), (None, ayuda), "Con las notificaciones activadas: sin nota, y la hoja dice cómo llegará el aviso")
        p.context.set_offline(True)
        esperar_nota(p, "sin-conexion")
        p.context.set_offline(False)
        esperar_nota(p, None)
        p.context.close()


# La hoja «Hecho o Mantener»: None si no se ve; si se ve, su título, cada cosa con sus dos respuestas (y si cada botón dice de qué cosa es) y dónde está el foco.
JS_HOJA_AVISADA = r"""
() => {
  const hoja = document.querySelector('#hoja-avisada'), dialogo = hoja.querySelector('[role=dialog]'), principal = document.querySelector('main');
  if (!hoja.checkVisibility()) return null;
  return {
    titulo: document.getElementById(dialogo.getAttribute('aria-labelledby')).textContent, modal: dialogo.getAttribute('aria-modal'),
    cosas: [...hoja.querySelectorAll('.avisada')].map((li) => { const texto = li.querySelector('.avisada-texto');
      return [texto.textContent, [...li.querySelectorAll('button')].map((b) => b.textContent + (texto.id !== '' && b.getAttribute('aria-describedby') === texto.id ? '' : ' (sin decir de qué cosa)'))]; }),
    focoDentro: dialogo.contains(document.activeElement), foco: document.activeElement.id || document.activeElement.textContent.trim() || document.activeElement.tagName,
    paginaInerte: principal.hasAttribute('inert'), paginaOculta: principal.getAttribute('aria-hidden'),
  };
}
"""

RESPUESTAS = ["Hecho", "Mantener"]
# Lo que sw.js deja apuntado al avisar de una cosa (aquí lo apunta la prueba): en su caché, una entrada por cosa avisada, con cuándo fue.
JS_APUNTAR_AVISADAS = r"""
async (ids) => {
  const cache = await caches.open('recordar-de-cosas');
  for (const [i, id] of ids.entries()) await cache.put(new URL('__recordar/avisadas/' + encodeURIComponent(id), location.href).href, new Response(JSON.stringify({ id, en: Date.now() - 1000 * (i + 1) })));
}
"""
JS_AVISADAS_APUNTADAS = "async () => (await caches.has('recordar-de-cosas')) ? (await (await caches.open('recordar-de-cosas')).keys()).map((k) => decodeURIComponent(new URL(k.url).pathname.split('/').pop())).sort() : []"
# El mensaje que sw.js manda a la app abierta cuando se toca un aviso (aquí lo manda la prueba).
JS_TOCAR_AVISO = "(id) => navigator.serviceWorker.dispatchEvent(new MessageEvent('message', { data: { tipo: 'recordar-abrir', id } }))"


def hoja_avisada(pagina):
    return pagina.evaluate(JS_HOJA_AVISADA)


def esperar_hoja_avisada(pagina, abierta=True):
    esperar(pagina, f"(abierta) => (({JS_HOJA_AVISADA})() !== null) === abierta", arg=abierta, que=f"la hoja «Hecho o Mantener» {'se abre' if abierta else 'se cierra'}")
    if abierta:
        pagina.wait_for_timeout(450)  # Fin de la animación de entrada y de la guarda del doble toque.
    return hoja_avisada(pagina)


def responder(pagina, texto, respuesta):
    pagina.locator("#lista-avisadas > li").filter(has=pagina.get_by_text(texto, exact=True)).get_by_role("button", name=respuesta, exact=True).tap()


@prueba("contestar en la app: al llegar con ?recordada=ID (el parámetro se quita antes de sembrar el historial) se pregunta por esa cosa en una hoja sobre la vista que toque; «Hecho» la marca como su check y avisa «Hecho»; «Mantener» y cerrar la hoja la dejan como estaba; un doble toque en la respuesta no acciona lo que queda debajo; una cosa hecha, fuera de Recordar o que no existe no abre nada")
def t_hoja_avisada(e):
    datos = estado_recordar([recordada(4, "Llamar al dentista"), recordada(3, "Sacar la basura"), recordada(2, "Ya hecha", hecha=True), cosa(1, "Suelta")])
    propios = recordar_guardado(cosas={"id-4": recordatorio(), "id-3": recordatorio("21:30", "semana", dia=2), "id-2": recordatorio()})
    p = sembrar_recordar(e, datos, propios, ruta="?recordada=id-4#grupo/recordar")
    h = esperar_hoja_avisada(p)
    igual(h, {"titulo": "Recordatorio", "modal": "true", "cosas": [["Llamar al dentista", RESPUESTAS]], "focoDentro": True, "foco": "titulo-hoja-avisada", "paginaInerte": True, "paginaOculta": "true"},
          "La hoja «Hecho o Mantener» al llegar desde el aviso de una cosa")
    igual((p.evaluate("() => location.search + location.hash"), vista_visible(p), pagina_grupo(p)["titulo"]), ("#grupo/recordar", "grupo", "Recordar"), "El parámetro se quita de la dirección y la hoja sale sobre la vista pedida")
    dialogo = p.get_by_role("dialog", name="Recordatorio", exact=True)
    igual((dialogo.count(), p.evaluate("() => { const d = document.querySelector('#hoja-avisada [role=dialog]'); return [d.tagName !== 'DIALOG', !document.querySelector('main').contains(d)]; }")), (1, [True, True]),
          "Un diálogo con su nombre, sin depender de <dialog> y fuera de lo que queda inerte")
    botones = p.evaluate("""() => [...document.querySelectorAll('#lista-avisadas button')].map((b) => { const r = b.getBoundingClientRect(); return [b.textContent, b.type, r.height >= 48, b.classList.contains('boton-lleno'), Math.round(r.left), Math.round(r.top)]; })""")
    comprobar([b[:4] for b in botones] == [["Hecho", "button", True, True], ["Mantener", "button", True, False]] and botones[0][4] < botones[1][4] and botones[0][5] == botones[1][5],
              f"«Hecho» (el principal) y «Mantener» no van uno junto al otro, con 48 px o más: {botones}")
    for tecla in ("Tab", "Tab", "Tab", "Tab", "Shift+Tab", "Shift+Tab"):
        p.keyboard.press(tecla)
        comprobar(hoja_avisada(p)["focoDentro"], f"Con {tecla} el foco se sale de la hoja: {hoja_avisada(p)['foco']}")
    # «Hecho»: como tocar su check. La cosa queda marcada (y baja al final), se avisa «Hecho» y, sin más que contestar, la hoja se cierra.
    responder(p, "Llamar al dentista", "Hecho")
    aviso = esperar_toast(p, "Hecho")
    igual((aviso["icono"], aviso["boton"], hoja_avisada(p)), (True, None, None), "Tras «Hecho»: el aviso y la hoja")
    esperar_recordadas(p, "la contestada con «Hecho» queda marcada", textos=["Sacar la basura", "Ya hecha", "Llamar al dentista"], hechas=[False, True, True])
    hecha = next(c for c in leer_estado(p)["cosas"] if c["id"] == "id-4")
    comprobar(hecha["hecha"] is True and isinstance(hecha["hechaEn"], (int, float)) and hecha["grupo"] == ID_RECORDAR, f"«Hecho» no marca la cosa como su check: {hecha}")
    igual((leer_recordar(p), p.evaluate("() => document.activeElement.id"), p.evaluate("() => document.querySelector('main').hasAttribute('inert')")), (propios, "titulo-grupo", False),
          "Tras «Hecho»: su recordatorio sigue guardado (si se desmarca, vuelve a avisar), el foco vuelve a la página y esta deja de estar inerte")
    # El parámetro se quitó antes de sembrar el historial: ni «atrás» (lista, inicio) ni recargar lo traen de vuelta.
    p.reload()
    esperar_vista(p, "grupo")
    p.wait_for_timeout(300)
    igual((hoja_avisada(p), p.evaluate("() => location.search")), (None, ""), "Al recargar se vuelve a preguntar")
    p.go_back()
    esperar_vista(p, "lista")
    igual(p.url, e.base + "#lista", "La entrada de la lista, bajo la de Recordar")
    p.go_back()
    esperar_vista(p, "inicio")
    igual(p.url, e.base, "La entrada de inicio conserva el parámetro del aviso")
    p.context.close()

    # «Mantener» (sobre la lista) y cerrar la hoja de cualquier forma (sobre la pantalla principal) lo dejan todo como estaba.
    for como, ruta, vista, foco in (("Mantener", "?recordada=id-3#lista", "lista", "titulo-lista"), ("Escape", "?recordada=id-3", "inicio", "BODY"), ("el velo", "?recordada=id-3", "inicio", "BODY"), ("«Cerrar»", "?otra=1&recordada=id-3", "inicio", "BODY")):
        p = sembrar_recordar(e, datos, propios, ruta=ruta)
        h = esperar_hoja_avisada(p)
        antes = p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE)
        igual((h["cosas"], vista_visible(p), p.evaluate("() => location.search")), ([["Sacar la basura", RESPUESTAS]], vista, "?otra=1" if "otra" in ruta else ""), f"[{como}] La hoja sobre la vista «{vista}», y lo que queda en la dirección")
        if como == "Mantener":
            responder(p, "Sacar la basura", "Mantener")
        elif como == "Escape":
            p.keyboard.press("Escape")
        elif como == "el velo":
            p.touchscreen.tap(e.ancho / 2, 12)
        else:
            p.locator("#hoja-avisada").get_by_role("button", name="Cerrar", exact=True).tap()
        esperar_hoja_avisada(p, abierta=False)
        p.wait_for_timeout(300)
        igual((p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE), leer_recordar(p), (toast(p) or {}).get("texto") or "", vista_visible(p), p.evaluate("() => document.activeElement.id || document.activeElement.tagName"),
               p.evaluate("() => document.querySelector('main').hasAttribute('inert')")), (antes, propios, "", vista, foco, False), f"[{como}] Todo sigue como estaba (cosas, recordatorios, sin avisos), en la misma vista y con el foco de vuelta")
        p.context.close()

    # Contestada la última (o la única), la hoja se cierra y debajo queda la barra de escribir: el segundo toque de un doble toque
    # no enfoca su campo ni empieza a dictar. Pasada la guarda, la página vuelve a responder.
    for respuesta, donde in (("Mantener", 0.9), ("Hecho", 0.5)):
        contexto = e.contexto()
        contexto.add_init_script(JS_DICTADO_FALSO)
        p = sembrar_recordar(e, datos, propios, ruta="?recordada=id-3", contexto=contexto)
        esperar_hoja_avisada(p)
        caja = p.locator("#lista-avisadas").get_by_role("button", name=respuesta, exact=True).bounding_box()
        x, y = caja["x"] + caja["width"] * donde, caja["y"] + caja["height"] * 0.75
        barra_debajo = p.evaluate("([x, y]) => { const r = document.querySelector('#formulario .pildora').getBoundingClientRect(); return r.left <= x && x <= r.right && r.top <= y && y <= r.bottom; }", [x, y])
        comprobar(barra_debajo, f"[{respuesta}] La prueba no toca sobre la barra de escribir: ({x}, {y})")
        p.touchscreen.tap(x, y)
        p.wait_for_timeout(150)
        p.touchscreen.tap(x, y)
        p.wait_for_timeout(500)
        igual((hoja_avisada(p), reconocimientos(p), p.evaluate("() => document.activeElement.tagName"), vista_visible(p), next(c["hecha"] for c in leer_estado(p)["cosas"] if c["id"] == "id-3")),
              (None, [], "BODY", "inicio", respuesta == "Hecho"), f"Tras un doble toque en «{respuesta}» con una sola cosa: la hoja, dictados empezados, el foco, la vista y la cosa")
        boton_micro(p).tap()
        igual(len(reconocimientos(p)), 1, f"[{respuesta}] Pasada la guarda, la página no responde")
        p.context.close()

    # Lo que no está pendiente en Recordar no se pregunta: hecha, suelta, inexistente o sin id. El parámetro se quita igual.
    for id, que in (("id-2", "una cosa ya hecha"), ("id-1", "una cosa que no está en Recordar"), ("no-existe", "una cosa que ya no existe"), ("", "un parámetro vacío")):
        p = sembrar_recordar(e, datos, propios, ruta=f"?recordada={id}#grupo/recordar")
        esperar_vista(p, "grupo")
        p.wait_for_timeout(400)
        igual((hoja_avisada(p), p.evaluate("() => location.search + location.hash"), p.evaluate("() => document.querySelector('main').hasAttribute('inert')"), p.evaluate("() => document.activeElement.id")),
              (None, "#grupo/recordar", False, "titulo-grupo"), f"Al llegar con el aviso de {que}")
        p.context.close()


@prueba("contestar con la app abierta: al tocar un aviso (lo dice el service worker) la hoja «Hecho o Mantener» sale sobre lo que haya en pantalla, sin cambiar de vista ni perder lo que se escribía; si hay otra hoja abierta, espera a que se cierre; la cosa tocada va la primera y detrás las avisadas sin contestar («Recordatorios»); al contestar una, el foco pasa a la siguiente, y un doble toque ni contesta dos ni cierra la hoja")
def t_hoja_avisada_con_la_app_abierta(e):
    datos = estado_recordar([recordada(5, "Llamar al dentista"), recordada(4, "Sacar la basura"), recordada(3, "Pagar la comunidad"), recordada(2, "Ya hecha", hecha=True), cosa(1, "Suelta")])
    propios = recordar_guardado(general=recordatorio())
    p = sembrar_recordar(e, datos, propios)
    esperar_vista(p, "grupo")
    # Con la hoja del recordatorio abierta, la pregunta espera; al cerrarla, sale.
    abrir_hoja_recordar(p)
    p.evaluate(JS_TOCAR_AVISO, "id-4")
    p.wait_for_timeout(400)
    igual((hoja_avisada(p), hoja_recordar(p)["abierta"], hoja_recordar(p)["focoDentro"]), (None, True, True), "Con otra hoja abierta, la pregunta no espera a que se cierre")
    p.keyboard.press("Escape")
    h = esperar_hoja_avisada(p)
    igual((h["titulo"], h["cosas"], h["focoDentro"], hoja_recordar(p)["abierta"], vista_visible(p)), ("Recordatorio", [["Sacar la basura", RESPUESTAS]], True, False, "grupo"), "Al cerrarse la otra hoja, la pregunta aplazada")
    responder(p, "Sacar la basura", "Hecho")
    esperar_toast(p, "Hecho")
    igual((hoja_avisada(p), next(c["hecha"] for c in leer_estado(p)["cosas"] if c["id"] == "id-4"), p.evaluate("() => document.activeElement.id")), (None, True, "configurar-recordar"),
          "Tras contestar la aplazada: la hoja, la cosa y el foco (donde lo dejó la hoja anterior)")
    # Un aviso de algo que ya no está pendiente no abre nada.
    for id in ("id-4", "id-2", "id-1", "no-existe"):
        p.evaluate(JS_TOCAR_AVISO, id)
    p.wait_for_timeout(400)
    igual(hoja_avisada(p), None, "Tocar el aviso de una cosa hecha, suelta o que no existe abre la hoja")
    p.context.close()

    # En la lista, a medio desplazar y con algo a medio escribir: la hoja sale encima y nada de eso se pierde.
    p = sembrar_recordar(e, estado_recordar(datos["cosas"] + [cosa(i, f"Cosa número {i}") for i in range(10, 50)]), propios, ruta="#lista")
    esperar_filas(p, 41)
    boton_crear_grupo(p).tap()
    campo_grupo(p).fill("A medio escribir")
    p.evaluate("() => { document.querySelector('#zona-lista').scrollTop = 500; }")
    p.evaluate(JS_APUNTAR_AVISADAS, ["id-5", "id-2", "id-3", "id-1", "no-existe", "id-4"])  # De todas ellas se avisó; de la hecha, la suelta y la que no existe no se pregunta.
    antes = p.evaluate("() => history.length")
    p.evaluate(JS_TOCAR_AVISO, "id-3")
    h = esperar_hoja_avisada(p)
    igual((h["titulo"], h["cosas"], h["foco"]), ("Recordatorios", [["Pagar la comunidad", RESPUESTAS], ["Llamar al dentista", RESPUESTAS], ["Sacar la basura", RESPUESTAS]], "titulo-hoja-avisada"),
          "La cosa del aviso tocado va la primera; detrás, las demás avisadas sin contestar (la más reciente antes)")
    igual((vista_visible(p), p.evaluate("() => location.hash"), p.evaluate("() => history.length") - antes, p.evaluate("() => document.querySelector('#zona-lista').scrollTop"), campo_grupo(p).input_value()),
          ("lista", "#lista", 0, 500, "A medio escribir"), "La hoja sale sin cambiar de vista, sin tocar el historial y sin perder el sitio ni lo que se escribía")
    # Otra vez el mismo aviso con la hoja abierta: no se repite ninguna.
    p.evaluate(JS_TOCAR_AVISO, "id-5")
    p.wait_for_timeout(300)
    igual([c[0] for c in hoja_avisada(p)["cosas"]], ["Pagar la comunidad", "Llamar al dentista", "Sacar la basura"], "Tocar otro aviso con la hoja abierta repite o desordena las cosas")
    # «Mantener» en la primera: se va de la hoja, que encoge por arriba, y «Cerrar» baja hasta donde estaba el botón. Un segundo toque
    # ahí mismo (o sobre «Cerrar», esté donde esté) no cierra la hoja con lo que queda por contestar, ni contesta otra.
    caja = p.locator("#lista-avisadas button[data-respuesta=mantener]").first.bounding_box()
    p.touchscreen.tap(caja["x"] + caja["width"] - 20, caja["y"] + caja["height"] / 2)
    p.wait_for_timeout(150)
    p.touchscreen.tap(caja["x"] + caja["width"] - 20, caja["y"] + caja["height"] / 2)
    equis = p.locator("#cerrar-hoja-avisada").bounding_box()
    p.touchscreen.tap(equis["x"] + equis["width"] / 2, equis["y"] + equis["height"] / 2)
    p.wait_for_timeout(500)
    h = hoja_avisada(p)
    igual((h and h["titulo"], h and [c[0] for c in h["cosas"]], sorted(c["id"] for c in leer_estado(p)["cosas"] if c["hecha"])), ("Recordatorios", ["Llamar al dentista", "Sacar la basura"], ["id-2"]),
          "Tras un doble toque en «Mantener» (y otro sobre «Cerrar»): la hoja sigue con las otras dos y nada queda marcado")
    # «Hecho» en la siguiente: queda marcada, se avisa y el foco pasa al «Hecho» de la que queda («Recordatorio», en singular).
    responder(p, "Llamar al dentista", "Hecho")
    esperar_toast(p, "Hecho")
    h = hoja_avisada(p)
    igual((h and h["titulo"], h and h["cosas"], h and h["foco"], sorted(c["id"] for c in leer_estado(p)["cosas"] if c["hecha"])), ("Recordatorio", [["Sacar la basura", RESPUESTAS]], "Hecho", ["id-2", "id-5"]),
          "Tras «Hecho» en una de dos: la que queda, con el foco en su «Hecho»")
    esperar_cuenta(p, "2", "Recordar. 2 cosas pendientes")
    # Cerrar es mantener lo que queda: ya no se vuelve a preguntar por ninguna.
    p.wait_for_timeout(400)
    p.keyboard.press("Escape")
    esperar_hoja_avisada(p, abierta=False)
    esperar(p, f"async () => {{ const quedan = await ({JS_AVISADAS_APUNTADAS})(); return !['id-3', 'id-4', 'id-5'].some((id) => quedan.includes(id)); }}", que="de las contestadas (y de las que se dejaron al cerrar) ya no queda nada por preguntar")
    igual((sorted(c["id"] for c in leer_estado(p)["cosas"] if c["hecha"]), leer_recordar(p), campo_grupo(p).input_value(), p.evaluate("() => document.activeElement.id")),
          (["id-2", "id-5"], propios, "A medio escribir", "campo-grupo"), "Tras cerrar la hoja: solo la contestada con «Hecho» está hecha, los recordatorios siguen y lo que se escribía sigue ahí, con el foco")


# Lo que puede haber en CLAVE_RECORDAR y no es un juego de recordatorios: se lee como si no hubiera ninguno.
RECORDAR_ILEGIBLES = ["{{{esto no es json", "", "null", "[]", "42", '"hola"', "true", '{"general": 5, "cosas": "x", "base": 7, "activar": 1}', '{"cosas": [1, 2]}', '{"general": [], "cosas": {"id-2": []}}']


@prueba("Recordar guardado: en un perfil nuevo nada se guarda hasta apuntar algo, y la clave de los recordatorios nace con el primero; tras recargar sigue todo (cosas, recordatorios, el de todas) y otra pestaña lo ve al momento; un perfil de antes de Recordar (versión 1 o 2) carga tal cual, sin tocar lo guardado; los recordatorios de una frecuencia que ya no existe («una vez»), mal formados o ilegibles se descartan sin romper nada")
def t_recordar_persistencia(e):
    contexto = contexto_con_reloj(e)
    p = e.pagina(ruta="#lista", contexto=contexto)
    esperar_vista(p, "lista")
    abrir_recordar(p)
    igual(claves_guardadas(p), [], "Claves guardadas en un perfil nuevo tras abrir la lista y Recordar")
    campo_en_grupo(p).fill("Llamar al dentista")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    igual(claves_guardadas(p), sorted([CLAVE, CLAVE_RESPALDO]), "Claves guardadas con una cosa en Recordar y ningún recordatorio")
    primera = leer_estado(p)["cosas"][0]["id"]
    abrir_hoja_recordar(p, "Llamar al dentista")
    elegir_hora(p, "18", "30")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    igual((claves_guardadas(p), recordar_en_bruto(p)), (sorted([CLAVE, CLAVE_RESPALDO, CLAVE_RECORDAR]), en_bruto(recordar_guardado(cosas={primera: recordatorio("18:30", "dia")}))),
          "Con el primer recordatorio nace su clave, con ese recordatorio y nada más")
    # Uno para todas (semanal: propone hoy, domingo), otra cosa que lo hereda y la primera, hecha.
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p)
    pastilla(p, "Cada semana")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio puesto a 1 cosa")
    campo_en_grupo(p).fill("Sacar la basura")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    p.wait_for_timeout(400)  # La hoja acaba de cerrarse: pasa la guarda de lo que tenía debajo.
    check_de(fila_recordada(p, "Llamar al dentista")).tap()
    domingos = recordar_guardado(general=recordatorio("09:00", "semana", dia=0))
    filas_esperadas = [("Sacar la basura", False, "9:00 · Cada domingo"), ("Llamar al dentista", True, "9:00 · Cada domingo")]
    esperar_recordadas(p, "lo apuntado después hereda el de todas y la marcada baja", textos=[f[0] for f in filas_esperadas], hechas=[f[1] for f in filas_esperadas], cuandos=[f[2] for f in filas_esperadas])
    igual(recordar_en_bruto(p), en_bruto(domingos), "Lo guardado con un recordatorio para todas (letra a letra)")
    cosas_guardadas_antes = p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE)
    # Recargar: todo sigue, y lo guardado no cambia ni una letra.
    p.reload()
    esperar_vista(p, "grupo")
    igual([(f["texto"], f["hecha"], f["cuando"]) for f in recordadas(p)], filas_esperadas, "Las filas de Recordar tras recargar")
    igual((recordar_en_bruto(p), p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE), claves_guardadas(p)), (en_bruto(domingos), cosas_guardadas_antes, sorted([CLAVE, CLAVE_RESPALDO, CLAVE_RECORDAR])),
          "Lo guardado tras recargar")
    h = abrir_hoja_recordar(p)
    igual((h["hora"], h["frecuencia"], h["dia"], h["resumen"]), ("09", "semana", "0", "Te lo recordaré cada domingo a las 9:00. También lo que apuntes después."), "La hoja de la campana tras recargar")
    cerrar_hoja_recordar(p)
    # Otra pestaña ve al momento los recordatorios que se cambian en esta (cada una en su página de Recordar).
    otra = e.pagina(ruta="#grupo/recordar", contexto=contexto)
    esperar_vista(otra, "grupo")
    igual([(f["texto"], f["hecha"], f["cuando"]) for f in recordadas(otra)], filas_esperadas, "Las filas de Recordar en otra pestaña")
    abrir_hoja_recordar(p, "Sacar la basura")
    pastilla(p, "Cada mes")
    guardar_hoja_recordar(p)
    esperar_recordadas(otra, "la otra pestaña ve el recordatorio cambiado", cuandos=["9:00 · Día 31 de cada mes", "9:00 · Cada domingo"])
    p.context.close()

    # Un perfil de antes de Recordar: carga como siempre y lo guardado no se toca hasta el primer cambio (y entonces, sin rastro de Recordar).
    de_la_1_2 = json.dumps(estado_con([cosa(3, "Suelta"), cosa(2, "En Casa", grupo="g-5"), cosa(1, "Hecha", hecha=True, hecha_en=1_700_000_000_050)],
                                      grupos=[grupo(5, "Casa", color="#F2B705", abierto=True)], color="#1BA39C", nombre="Raúl"), ensure_ascii=False)
    p = sembrar(e, de_la_1_2, ruta="#lista")
    esperar_filas(p, 3)
    a = acceso(p)
    igual((textos_nivel(p), textos_anidados(p, "Casa"), p.locator("#titulo-lista").text_content(), a["visible"], a["cuenta"]), (["Casa", "Suelta", "Hecha"], ["En Casa"], "Cosas de Raúl", True, None),
          "Un perfil de la 1.2 en la lista, con la fila fija de Recordar sin cuenta")
    abrir_recordar(p)
    igual((pagina_grupo(p)["miembros"], pagina_grupo(p)["vacio"]), ([], True), "Recordar en un perfil de la 1.2")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual((p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE), claves_guardadas(p)), (de_la_1_2, [CLAVE]), "Cargar un perfil de antes de Recordar (y asomarse a Recordar) toca lo guardado")
    p.wait_for_timeout(400)
    check_de(fila_de(p, "Suelta")).tap()
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).cosas.find((c) => c.texto === 'Suelta').hecha", arg=CLAVE, que="se guarda la cosa marcada")
    igual(([g["id"] for g in leer_estado(p)["grupos"]], claves_guardadas(p)), (["g-5"], sorted([CLAVE, CLAVE_RESPALDO])), "Al guardar un perfil de la 1.2 aparece Recordar o su clave")
    p.context.close()
    de_la_1_0 = json.dumps(estado_con([cosa(2, "De la 1.0"), cosa(1, "Hecha en la 1.0", hecha=True)]), ensure_ascii=False)
    p = sembrar(e, de_la_1_0)
    esperar_vista(p, "inicio")
    abrir_lista(p)
    igual((textos_nivel(p), acceso(p)["cuenta"], p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE)), (["De la 1.0", "Hecha en la 1.0"], None, de_la_1_0), "Un perfil de la versión 1 en la lista, sin tocar lo guardado")
    p.wait_for_timeout(400)
    abrir_recordar(p)
    boton_anadir(p).tap()
    mas_de(p, "De la 1.0").tap()
    esperar_pagina_grupo(p, "una cosa de la versión 1, añadida a Recordar", miembros=["De la 1.0"])
    guardado = leer_estado(p)
    igual((guardado["version"], guardado["grupos"], [(c["texto"], c["grupo"]) for c in guardado["cosas"]]), (2, [GRUPO_RECORDAR], [("De la 1.0", ID_RECORDAR), ("Hecha en la 1.0", None)]), "El perfil de la versión 1, ya con algo en Recordar")
    p.context.close()

    # Recordatorios que ya no valen: «una vez» (con su fecha), horas y días imposibles, cosas que no son un recordatorio. Vale el que vale.
    raros = {"general": {"hora": "09:00", "frec": "una", "fecha": "2026-12-01"},
             "cosas": {"id-9": {"hora": "10:00", "frec": "una", "fecha": "2026-11-30"}, "id-8": {"hora": "25:99", "frec": "dia"}, "id-7": {"hora": "08:00", "frec": "semana", "dia": 9},
                       "id-6": {"hora": "07:00", "frec": "mes", "diaMes": 0}, "id-5": {"hora": "06:30", "frec": "dia", "sobra": 1}, "id-4": "texto", "id-3": None,
                       "id-2": {"hora": "9:00", "frec": "dia"}, "id-1": {"hora": "12:00", "frec": "semana"}},
             "base": "no", "activar": "sí"}
    nueve = estado_recordar([recordada(i, f"Cosa {i}") for i in range(9, 0, -1)])
    p = sembrar_recordar(e, nueve, raros)
    esperar_vista(p, "grupo")
    igual(cuandos(p), {f"Cosa {i}": ("6:30 · Cada día" if i == 5 else "Poner recordatorio") for i in range(9, 0, -1)}, "De unos recordatorios con de todo, solo vale el que está bien")
    h = abrir_hoja_recordar(p)
    igual((h["hora"], h["minuto"], h["frecuencia"], h["resumen"]), ("09", "00", "dia", "Te lo recordaré cada día a las 9:00. También lo que apuntes después. Cambiará el recordatorio de 1 cosa que tenía otro."),
          "La hoja de la campana con un «para todas» de una frecuencia que ya no existe: como si no hubiera ninguno")
    igual(p.evaluate("() => [...document.querySelectorAll('#frecuencia-recordar input')].map((r) => r.value)"), ["dia", "laborables", "semana", "mes"], "Frecuencias que ofrece la hoja")
    cerrar_hoja_recordar(p)
    abrir_hoja_recordar(p, "Cosa 9")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-5": recordatorio("06:30", "dia"), "id-9": recordatorio()})), "Al guardar, de lo que no valía no queda nada (ni campos de más)")
    p.context.close()
    dos = estado_recordar([recordada(2, "Dos"), recordada(1, "Una")])
    for i, ilegible in enumerate(RECORDAR_ILEGIBLES):
        p = sembrar_recordar(e, dos, ilegible)
        esperar_vista(p, "grupo")
        igual((cuandos(p), nota_recordar(p)), ({"Dos": "Poner recordatorio", "Una": "Poner recordatorio"}, None), f"Recordar con la clave de los recordatorios ilegible ({ilegible!r})")
        if i == 0:
            abrir_hoja_recordar(p, "Una")
            guardar_hoja_recordar(p)
            esperar_toast(p, "Recordatorio guardado")
            igual(recordar_en_bruto(p), en_bruto(recordar_guardado(cosas={"id-1": recordatorio()})), "Tras guardar sobre una clave ilegible")
        p.context.close()


@prueba("Recordar sin almacenamiento (denegado, lleno o ilegible) y abierta con file://: su página, apuntar, poner recordatorios (a una y a todas) y marcar siguen funcionando durante la sesión, con su aviso cuando no se puede guardar")
def t_recordar_sin_almacenamiento(e):
    guiones = {
        # nombre: (guion, ¿se puede guardar?)
        "localStorage lanza SecurityError": ("Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('Acceso denegado', 'SecurityError'); } });", False),
        "setItem lanza QuotaExceededError": ("Storage.prototype.setItem = function () { throw new DOMException('Lleno', 'QuotaExceededError'); };", False),
        "getItem lanza": ("Storage.prototype.getItem = function () { throw new Error('roto'); };", True),
    }
    for nombre, (guion, guarda) in guiones.items():
        contexto = e.contexto()
        contexto.add_init_script(guion)
        p = e.pagina(ruta="#grupo/recordar", contexto=contexto)
        esperar_vista(p, "grupo")
        igual((pagina_grupo(p)["titulo"], pagina_grupo(p)["vacio"], boton_configurar(p).count()), ("Recordar", True, 1), f"[{nombre}] La página de Recordar")
        campo_en_grupo(p).fill("Sin almacenamiento")
        campo_en_grupo(p).press("Enter")
        esperar_toast(p, "Guardado" if guarda else "No se ha podido guardar")
        esperar_recordadas(p, f"[{nombre}] lo apuntado está en Recordar durante la sesión", textos=["Sin almacenamiento"], cuandos=["Poner recordatorio"])
        abrir_hoja_recordar(p, "Sin almacenamiento")
        elegir_hora(p, "20")
        guardar_hoja_recordar(p)
        esperar_toast(p, "Recordatorio guardado" if guarda else "No se ha podido guardar")
        igual(cuandos(p), {"Sin almacenamiento": "20:00 · Cada día"}, f"[{nombre}] El recordatorio puesto, durante la sesión")
        p.wait_for_timeout(400)
        h = abrir_hoja_recordar(p)
        igual((h["alcance"], h["resumen"]), ("todas", "Te lo recordaré cada día a las 9:00. También lo que apuntes después. Cambiará el recordatorio de 1 cosa que tenía otro."), f"[{nombre}] La hoja desde la campana")
        guardar_hoja_recordar(p)
        esperar_toast(p, "Recordatorio puesto a 1 cosa" if guarda else "No se ha podido guardar")
        igual(cuandos(p), {"Sin almacenamiento": "9:00 · Cada día"}, f"[{nombre}] El recordatorio de todas, durante la sesión")
        p.wait_for_timeout(400)
        check_de(fila_recordada(p, "Sin almacenamiento")).tap()
        esperar_recordadas(p, f"[{nombre}] el check funciona", hechas=[True])
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        igual((acceso(p)["visible"], acceso(p)["cuenta"], p.evaluate("() => document.querySelector('#vacio').textContent")), (True, None, "Lo que tienes apuntado está en Recordar."), f"[{nombre}] La lista, con todo en Recordar y hecho")
        contexto.close()
    # Con file:// (sin service worker ni función de avisos): lo mismo, y además se conserva al recargar.
    contexto = e.contexto()
    p = e.vigilar(contexto.new_page())
    p.goto((APP / "index.html").as_uri() + "#grupo/recordar")
    esperar_vista(p, "grupo")
    igual((pagina_grupo(p)["titulo"], p.locator("#grupo-vacio").text_content()), ("Recordar", VACIO_RECORDAR), "Recordar con file://")
    campo_en_grupo(p).fill("Desde file")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    abrir_hoja_recordar(p, "Desde file")
    pastilla(p, "De lunes a viernes")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    igual(cuandos(p), {"Desde file": "9:00 · De lunes a viernes"}, "El recordatorio con file://")
    comprobar(nota_recordar(p) is not None and nota_recordar(p)["texto"] in NOTAS_RECORDAR.values(), f"Con file:// la nota no dice por qué aquí no avisará: {nota_recordar(p)}")
    p.reload()
    esperar_vista(p, "grupo")
    igual((cuandos(p), leer_recordar(p)["cosas"]), ({"Desde file": "9:00 · De lunes a viernes"}, {leer_estado(p)["cosas"][0]["id"]: recordatorio("09:00", "laborables")}), "Persistencia de Recordar con file://")
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    igual((acceso(p)["cuenta"], acceso(p)["dicho"]), ("1", "Recordar. Una cosa pendiente"), "La fila fija con file://")
    p.wait_for_timeout(400)
    acceso_recordar(p).tap()
    esperar_vista(p, "grupo")
    comprobar(p.url.endswith("#grupo/recordar"), f"Con file://, la fila fija no abre Recordar: {p.url}")


@prueba("Recordar en pantallas difíciles (apaisado, 320 px, 280 px y zoom al 200 %): la fila fija cabe sobre la lista sin comérsela (más baja en apaisado; sin flecha, y después sin campana, cuando falta sitio) y no se mueve con ella; en su página la cabecera sigue en una línea con la campana a la derecha, la nota y cada recordatorio caben en su sitio y la última cosa se alcanza sobre la barra; la hoja «Hecho o Mantener» con doce cosas se desplaza por dentro; nada desborda")
def t_recordar_disposicion(e):
    cosas = [recordada(100 + i, f"Recordada número {i} con un texto algo largo", hecha=i % 5 == 0) for i in range(1, 31)] + [cosa(i, f"Cosa número {i}") for i in range(1, 31)]
    propios = recordar_guardado(cosas={f"id-{100 + i}": recordatorio("21:30", "laborables") for i in range(1, 31, 2)})
    todas = ["recordar-campana", "recordar-nombre", "recordar-cuenta", "recordar-flecha"]
    for etiqueta, ancho, alto, partes in (("apaisado", e.alto, e.ancho, todas), ("320", 320, 568, todas), ("280", 280, 568, todas[:3]), ("zoom al 200 %", 195, 400, todas[1:3])):
        p = sembrar_recordar(e, estado_recordar(cosas, grupos=[grupo(200, "Grupo")]), propios, ruta="#lista", contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_filas(p, 31)
        a = acceso(p)
        caja, cabecera = a["caja"], a["cabecera"]
        igual((a["partes"], a["cuenta"], a["enOrden"]), (partes, "24", True), f"[{etiqueta}] Lo que enseña la fila fija")
        medidas = p.evaluate(f"""() => {{ const a = document.querySelector('#acceso-recordar'), r = a.getBoundingClientRect(), dentro = (el) => {{ const c = el.getBoundingClientRect(); return c.left >= r.left && c.right <= r.right + 0.5 && c.top >= r.top && c.bottom <= r.bottom + 0.5; }};
            return {{ nombreRecortado: ({JS_RECORTADO})(a.querySelector('.recordar-nombre')), dentro: [...a.children].filter((el) => el.checkVisibility()).every(dentro),
                     barra: document.querySelector('#formulario-grupo .pildora').getBoundingClientRect().top }}; }}""")
        comprobar(caja["x"] >= 0 and caja["d"] <= ancho + 0.5 and caja["alto"] >= 44 and cabecera["b"] <= caja["y"] and not medidas["nombreRecortado"] and medidas["dentro"],
                  f"[{etiqueta}] La fila fija no cabe, se encoge por debajo de 44 px o recorta su nombre: {caja} / {medidas}")
        if etiqueta == "apaisado":
            comprobar(caja["alto"] <= 48 and caja["y"] - cabecera["b"] <= 8, f"[apaisado] La fila fija no se hace más baja para no comerse la lista: {caja} bajo {cabecera}")
        comprobar(medidas["barra"] - caja["b"] >= 100, f"[{etiqueta}] Entre la fila fija y la barra no queda sitio para la lista: {medidas['barra'] - caja['b']} px")
        comprobar_sin_desborde(p, f"la lista con la fila fija ({etiqueta})")
        p.evaluate("() => { const z = document.querySelector('#zona-lista'); z.scrollTop = z.scrollHeight; }")
        p.wait_for_timeout(150)
        ultima = filas(p).last.bounding_box()
        comprobar(ultima["y"] + ultima["height"] <= medidas["barra"] + 0.5 and ultima["y"] >= caja["b"], f"[{etiqueta}] Al final de la lista, la última cosa no queda entre la fila fija y la barra: {ultima}")
        igual(acceso(p)["caja"], caja, f"[{etiqueta}] La fila fija se mueve al desplazar la lista")
        # Su página: 30 cosas, la mitad con recordatorio, y la nota (en el navegador de las pruebas las notificaciones están bloqueadas).
        abrir_recordar(p)
        esperar(p, "() => document.querySelectorAll('#lista-grupo > li').length === 30", que="la página de Recordar pinta sus 30 cosas")
        esperar_nota(p, "bloqueados")
        cab = p.locator("#vista-grupo header").bounding_box()
        atras, campana = p.get_by_role("button", name="Volver", exact=True).bounding_box(), boton_configurar(p).bounding_box()
        igual(round(cab["height"]), 48, f"[{etiqueta}] La cabecera de Recordar crece (la campana se cae a otra fila): {cab}")
        comprobar(abs(campana["y"] - atras["y"]) <= 1 and campana["x"] >= atras["x"] + atras["width"] and campana["x"] + campana["width"] <= ancho + 0.5 and round(campana["width"]) == 48,
                  f"[{etiqueta}] La campana no está arriba a la derecha, a la altura de «Volver»: {atras} / {campana}")
        comprobar_sin_desborde(p, f"la página de Recordar ({etiqueta})")
        fallos = p.evaluate(f"""() => {{ const recortado = {JS_RECORTADO}; const malas = [];
            if (recortado(document.querySelector('#texto-nota-recordar'))) malas.push('la nota sale recortada');
            for (const f of document.querySelectorAll('#lista-grupo > li')) {{
              const r = f.getBoundingClientRect(), c = f.querySelector('.cuando'), rc = c.getBoundingClientRect(), t = f.querySelector('.texto').getBoundingClientRect(), b = [...f.querySelectorAll('.borrar')].find((el) => el.checkVisibility()).getBoundingClientRect();
              if (f.scrollWidth > f.clientWidth || rc.left < r.left || rc.right > r.right + 0.5 || rc.bottom > r.bottom + 0.5 || rc.top < t.bottom - 0.5 || t.right > b.left + 0.5 || b.right > r.right + 0.5 || b.width < 44
                  || c.querySelector('.cuando-texto').scrollWidth > c.querySelector('.cuando-texto').clientWidth + 0.5) malas.push(f.querySelector('.texto').textContent);
            }}
            return malas; }}""")
        igual(fallos, [], f"[{etiqueta}] Filas de Recordar que desbordan, pisan su botón o no dejan el recordatorio dentro, bajo el texto")
        m = barra_anadir(p)
        p.evaluate("() => { const z = document.querySelector('#zona-grupo'); z.scrollTop = z.scrollHeight; }")
        p.wait_for_timeout(150)
        ultima = p.locator("#lista-grupo > li").last.bounding_box()
        comprobar(ultima["y"] + ultima["height"] <= m["pildora"]["y"] + 0.5, f"[{etiqueta}] Al final de Recordar, la última cosa queda bajo la barra: {ultima} / {m['pildora']}")
        # La hoja «Hecho o Mantener» con doce cosas por contestar: cabe pegada abajo, se desplaza por dentro y la última respuesta se alcanza.
        pendientes = [f"id-{100 + i}" for i in range(1, 16) if i % 5]
        p.evaluate(JS_APUNTAR_AVISADAS, pendientes)
        p.evaluate(JS_TOCAR_AVISO, pendientes[0])
        igual(len(esperar_hoja_avisada(p)["cosas"]), 12, f"[{etiqueta}] Cosas en la hoja «Hecho o Mantener»")
        hoja_a = p.evaluate("""() => { const panel = document.querySelector('#hoja-avisada [role=dialog]'), r = panel.getBoundingClientRect();
            const visibles = [panel, ...panel.querySelectorAll('*')].filter((el) => el.checkVisibility() && el.getBoundingClientRect().width > 0);
            return { y: r.top, b: r.bottom, x: r.left, d: r.right, alto: innerHeight, ancho: innerWidth, desplazable: panel.scrollHeight > panel.clientHeight + 0.5, desborde: panel.scrollWidth - panel.clientWidth,
                     fuera: visibles.filter((el) => { const c = el.getBoundingClientRect(); return c.left < -0.5 || c.right > innerWidth + 0.5; }).map((el) => el.tagName + '.' + el.className) }; }""")
        comprobar(hoja_a["y"] >= 23.5 and abs(hoja_a["b"] - hoja_a["alto"]) <= 0.5 and hoja_a["x"] >= -0.5 and hoja_a["d"] <= hoja_a["ancho"] + 0.5 and hoja_a["desplazable"] and hoja_a["desborde"] <= 0 and hoja_a["fuera"] == [],
                  f"[{etiqueta}] La hoja «Hecho o Mantener» con doce cosas no cabe pegada abajo, no se desplaza por dentro o desborda: {hoja_a}")
        ultimo = p.locator("#lista-avisadas").get_by_role("button", name="Mantener", exact=True).last
        ultimo.scroll_into_view_if_needed()
        caja = ultimo.bounding_box()
        comprobar(caja["y"] >= hoja_a["y"] and caja["y"] + caja["height"] <= alto + 0.5 and caja["height"] >= 44, f"[{etiqueta}] El último «Mantener» no se alcanza: {caja}")
        p.context.close()


@prueba("estático de Recordar: styles.css no tacha nada (ni rastro de «line-through») y de «Una vez» no queda nada en la app (ni la pastilla, ni la frecuencia, ni su fecha)", una_vez=True)
def t_recordar_estatico(e):
    css = (APP / "styles.css").read_text(encoding="utf-8")
    comprobar("line-through" not in css, "styles.css sigue tachando algo («line-through»)")
    html = (APP / "index.html").read_text(encoding="utf-8")
    comprobar("una vez" not in html.lower(), "index.html sigue hablando de «Una vez»")
    igual(re.findall(r'name="frecuencia" value="([^"]+)"', html), ["dia", "laborables", "semana", "mes"], "Frecuencias de la hoja en index.html")
    for nombre in ("app.js", "nube.js", "sw.js"):
        fuente = (APP / nombre).read_text(encoding="utf-8")
        comprobar(not re.search(r"['\"]una['\"]", fuente) and not re.search(r"(\.|['\"])fecha\b|\bfecha\s*:", fuente), f"{nombre} conserva algo de la frecuencia «una vez» ('una' o su campo fecha)")
    comprobar("const FRECUENCIAS = ['dia', 'laborables', 'semana', 'mes'];" in (APP / "app.js").read_text(encoding="utf-8"), "app.js no declara las cuatro frecuencias de un recordatorio")


# ----------------------------------------------------------------------------
# Recordar y la función de avisos: quien avisa a su hora guarda los recordatorios (nube.js se los lleva y los trae)
# ----------------------------------------------------------------------------

MADRID = "Europe/Madrid"
SIN_DOCUMENTO = {"nacido": None, "v": 0, "zona": "UTC", "general": None, "cosas": {}}  # Lo que dice la función de una sesión de la que no guarda nada.
MAX_RECORDADAS = 100  # Las cosas con recordatorio que la función admite de una sesión (y las que la app le lleva).
MAX_BYTES_DE_RECORDAR = 100000  # Lo que puede ocupar, como mucho, un envío de recordatorios a la función.
# El choque de versiones (409) es parte del protocolo, pero Chrome lo apunta en la consola como un error: solo lo toleran las pruebas que lo provocan.
CHOQUE_DE_VERSIONES = [r"^409 https://cosas\.test/api/avisos/recordar$", r"status of 409 \(Conflict\)$"]
JS_VUELVE_LA_CONEXION = "() => { window.dispatchEvent(new Event('online')); }"


class ApiRecordarFalsa(ApiAvisosFalsa):
    """La función de avisos de mentira, con los recordatorios tal como los guarda la de verdad: de cada sesión, un documento
    { nacido, v, zona, general, cosas }. GET recordar lo da; POST recordar le aplica un cambio ({ siV, zona, general?, poner?,
    quitar? }) si «siV» es su versión (si no, 409 con lo que hay) y contesta { nacido, v, rechazadas }, salvo que ocupe más de
    lo que admite (413); «hecho» marca una cosa y «olvidar» borra el documento. Además de «llamadas», apunta cada petición
    entera: método, cabeceras, cuerpo (y lo que ocupa) y respuesta."""

    def __init__(self):
        super().__init__()
        self.peticiones = []  # { accion, metodo, cabeceras, cuerpo, bytes, estado, respuesta }, en orden (estado None: no llegó).
        self.documentos = {}  # uid → su documento.
        self.nacidos = 0
        self.cortar = set()  # Acciones que no llegan a la función (sin conexión).
        self.retener = set()  # Acciones que se quedan por el camino hasta «soltar» (una red lenta).
        self.retenidas = []
        self.antes_de_guardar = None  # Lo que pasa justo antes de atender un POST recordar (otro dispositivo se adelanta).

    def de_la_sesion(self, uid):
        """Lo que la función dice de esa sesión (una copia)."""
        return json.loads(json.dumps(self.documentos.get(uid, SIN_DOCUMENTO)))

    def desde_otro_sitio(self, uid, cambio):
        """Un cambio que no hace este dispositivo (otro de la misma sesión, o un «Hecho» pulsado en un aviso): sube la versión."""
        cambio(self.documentos[uid])
        self.documentos[uid]["v"] += 1

    def guardar(self, uid, envio):
        if self.antes_de_guardar:
            self.antes_de_guardar()
        actual = self.de_la_sesion(uid)
        if envio["siV"] != actual["v"]:
            return 409, actual
        nuevo = self.de_la_sesion(uid)
        nuevo["zona"] = envio["zona"]
        if "general" in envio:
            nuevo["general"] = envio["general"]
        for id in envio.get("quitar", []):
            nuevo["cosas"].pop(id, None)
        rechazadas = []
        for id, cosa_enviada in envio.get("poner", {}).items():
            if id not in nuevo["cosas"] and len(nuevo["cosas"]) >= MAX_RECORDADAS:
                rechazadas.append(id)  # No caben más.
            else:
                nuevo["cosas"][id] = cosa_enviada
        # La versión solo sube si cambia algo; el documento nace con el primer envío.
        if any(nuevo[campo] != actual[campo] for campo in ("zona", "general", "cosas")):
            nuevo["v"] += 1
        if nuevo["nacido"] is None:
            self.nacidos += 1
            nuevo["nacido"] = f"n{self.nacidos}"
        self.documentos[uid] = nuevo
        return 200, {"nacido": nuevo["nacido"], "v": nuevo["v"], "rechazadas": rechazadas}

    def responder(self, accion, metodo, autorizacion, cuerpo, bruto=None):
        """Lo que contesta la función a esa llamada: (estado, datos). «bruto» es el cuerpo tal cual llegó."""
        if accion in self.fallar:
            return 500, {"error": "interno"}
        if accion == "recordar" and len(bruto or b"") > MAX_BYTES_DE_RECORDAR:
            return 413, {"error": "demasiado-grande"}
        if accion == "clave":
            return 200, {"clave": base64.urlsafe_b64encode(CLAVE_VAPID).rstrip(b"=").decode()}
        if accion == "hecho":  # Sin sesión: lo llama el service worker, con la ficha del aviso.
            cosa_avisada = self.documentos.get(cuerpo["uid"], SIN_DOCUMENTO)["cosas"].get(cuerpo["id"])
            if cosa_avisada and not cosa_avisada["hecha"]:
                self.desde_otro_sitio(cuerpo["uid"], lambda doc: doc["cosas"][cuerpo["id"]].update(hecha=True))
            return 200, {"ok": True}
        if accion in ("recordar", "olvidar"):
            if not (autorizacion or "").startswith("Bearer token-"):
                return 401, {"error": "sin-sesion"}
            uid = autorizacion[len("Bearer token-"):]
            if accion == "olvidar":
                self.documentos.pop(uid, None)
            elif metodo == "GET":
                return 200, self.de_la_sesion(uid)
            else:
                return self.guardar(uid, cuerpo)
        return 200, {"ok": True}

    def atender(self, route):
        peticion = route.request
        accion = peticion.url.split("/api/avisos/", 1)[1].split("?")[0]
        cuerpo = json.loads(peticion.post_data) if peticion.post_data else None
        self.llamadas.append((accion, peticion.headers.get("authorization"), cuerpo))
        apunte = {"accion": accion, "metodo": peticion.method, "cabeceras": dict(peticion.headers), "cuerpo": cuerpo, "bytes": len(peticion.post_data_buffer or b""), "estado": None, "respuesta": None}
        self.peticiones.append(apunte)
        if accion in self.cortar:
            route.abort()
        elif accion in self.retener:
            self.retenidas.append((route, apunte))
        else:
            self.contestar(route, apunte)

    def contestar(self, route, apunte):
        peticion = route.request
        apunte["estado"], apunte["respuesta"] = self.responder(apunte["accion"], peticion.method, peticion.headers.get("authorization"), apunte["cuerpo"], peticion.post_data_buffer)
        route.fulfill(status=apunte["estado"], content_type="application/json", body=json.dumps(apunte["respuesta"], ensure_ascii=False))

    def soltar(self):
        """Lo retenido llega por fin a la función, en el orden en que salió."""
        self.retener.clear()
        while self.retenidas:
            self.contestar(*self.retenidas.pop(0))

    def lecturas(self):
        return [p for p in self.peticiones if p["accion"] == "recordar" and p["metodo"] == "GET"]

    def envios(self):
        """Los POST recordar, en orden (también los que chocaron o no llegaron)."""
        return [p for p in self.peticiones if p["accion"] == "recordar" and p["metodo"] == "POST"]


def recordada_enviada(texto, r=None, hecha=False):
    """Una cosa con recordatorio tal como viaja a la función de avisos (y como ella la guarda)."""
    return {"texto": texto, "hecha": hecha, "r": r or recordatorio()}


def parche(si_v, poner=None, quitar=(), zona=MADRID, **general):
    """El cuerpo de un POST recordar: lo que cambia sobre la versión «si_v». «general» solo viaja si cambia (general=None lo quita)."""
    return {"siV": si_v, "zona": zona, **general, "poner": poner or {}, "quitar": list(quitar)}


def base_recordada(uid, nacido, v, cosas, general=None, zona=MADRID, zona_de_la_funcion=None):
    """Lo que el dispositivo recuerda de su última ronda con la función (la «base»): lo que ella tiene de sus cosas, sobre qué
    versión de qué documento, y la firma de lo que aquí había entonces (mientras no cambie, no hay nada nuevo que contarle)."""
    return {"uid": uid, "nacido": nacido, "v": v, "zona": zona, "zonaDeLaFuncion": zona_de_la_funcion or zona,
            "firma": en_bruto([zona, general, [[id, cosas[id]] for id in sorted(cosas)]]), "general": general, "cosas": cosas}


def dispositivo_con_recordar(e, api, usuario=None, endpoint=ENDPOINT_PRUEBA + "E1", zona=MADRID, guion="", **opciones):
    """Un dispositivo en https://cosas.test, en esa zona horaria, que habla con la función «api». Con «endpoint», ya tiene el permiso
    de las notificaciones y está suscrito con él (None: sin tocar); «usuario» es su sesión (sin él, entra de forma anónima)."""
    suscrito = CON_PERMISO + f"window.__avisos.suscripcion = window.__avisos.nueva({json.dumps(endpoint)}, {json.dumps(list(CLAVE_VAPID))});" if endpoint else ""
    return dispositivo_con_avisos(e, api, usuario=usuario, preparar=suscrito + guion, timezone_id=zona, **opciones)


def abrir_con_recordar(contexto, e, datos=None, recordatorios=None, ruta="#grupo/recordar", sesion="anonimo-1", endpoint=ENDPOINT_PRUEBA + "E1", cuenta=False):
    """Abre la app en ese dispositivo con lo que ya tenía: sus cosas, sus recordatorios (CLAVE_RECORDAR) y, con «sesion», las
    notificaciones activadas hace un momento para ella (CLAVE_AVISOS: al día, no hay que renovar el alta); con «cuenta», la de
    Google que dejó cuenta/."""
    pagina = e.vigilar(contexto.new_page())
    pagina.goto(ORIGEN_NUBE + "icons/favicon-32.png")
    guardar = {CLAVE: datos, CLAVE_RECORDAR: recordatorios,
               CLAVE_AVISOS: {"uid": sesion, "endpoint": endpoint, "en": time.time() * 1000} if sesion else None,
               CLAVE_CUENTA: {"nombre": "Raúl", "correo": "raul@example.com"} if cuenta else None}
    pagina.evaluate("(pares) => { for (const [clave, valor] of pares) localStorage.setItem(clave, valor); }",
                    [[clave, valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)] for clave, valor in guardar.items() if valor is not None])
    pagina.goto(ORIGEN_NUBE + ruta)
    return pagina


def esperar_envios(pagina, api, cuantos, ms=5000):
    """Espera a que la función haya contestado (o perdido) ese número de POST recordar y los devuelve."""
    fin = time.monotonic() + ms / 1000
    while len(api.envios()) < cuantos:
        if time.monotonic() > fin:
            raise Fallo(f"La función no recibe {cuantos} envíos de recordatorios en {ms} ms: {[(p['accion'], p['metodo'], p['cuerpo'], p['estado']) for p in api.peticiones]}")
        pagina.wait_for_timeout(50)  # Con Playwright esperando, las llamadas a la función falsa se atienden.
    return api.envios()


def esperar_lecturas(pagina, api, cuantas, ms=5000):
    """Espera a que a la función se le haya preguntado ese número de veces (GET recordar)."""
    fin = time.monotonic() + ms / 1000
    while len(api.lecturas()) < cuantas:
        if time.monotonic() > fin:
            raise Fallo(f"A la función no se le pregunta {cuantas} veces por los recordatorios en {ms} ms: {[(p['accion'], p['metodo'], p['estado']) for p in api.peticiones]}")
        pagina.wait_for_timeout(50)


def esperar_base(pagina, que, ms=5000, **esperado):
    """Espera a que la base guardada en ese dispositivo tenga esos valores (v=…, nacido=…): la ronda con la función ha terminado."""
    esperar_json(pagina, f"() => (JSON.parse(localStorage.getItem('{CLAVE_RECORDAR}')) || {{}}).base || {{}}", esperado, que, ms=ms)
    return leer_recordar(pagina)["base"]


def sin_mas_llamadas(pagina, api, que, ms=1300):
    """Pasado de sobra el retardo con el que la app junta los cambios (700 ms), la función no ha recibido nada más."""
    antes = len(api.peticiones)
    pagina.wait_for_timeout(ms)
    igual([(p["accion"], p["metodo"], p["cuerpo"]) for p in api.peticiones[antes:]], [], que)


def peticiones_de(contexto):
    """Las direcciones que pidan desde ahora las páginas de ese contexto (lo que precarga su service worker no cuenta)."""
    pedidas = []
    contexto.on("request", lambda peticion: peticion.service_worker or pedidas.append(peticion.url))
    return pedidas


def peticiones_de_la_nube(peticiones):
    """De las direcciones pedidas por un contexto, las que son de la nube: su módulo, Firebase o la función de avisos."""
    return [url for url in peticiones if "nube.js" in url or "firebase" in url or "/api/" in url]


@prueba("recordatorios y la función de avisos, cuándo se habla con ella: nunca fuera de la app publicada (http); en ella, sin notificaciones ni cuenta, tampoco (ni se carga la nube), aunque haya recordatorios, cambien o vuelva la conexión, hasta que «Guardar» un recordatorio las activa: entonces, el alta y, con su sesión, los recordatorios; con las notificaciones activadas y Recordar vacío, nada; con cosas en Recordar y ningún recordatorio, solo se le pregunta (pueden tenerlo de otro dispositivo); y con la cuenta de Google, aunque no haya notificaciones aquí, se le llevan con esa sesión")
def t_recordar_nube_cuando(e):
    datos = estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")])
    propios = recordar_guardado(cosas={"id-2": recordatorio()})

    def trastear(p):
        """Lo que haría hablar con la función si tocara: un cambio en Recordar (se marca una cosa), que vuelva la conexión y esperar."""
        esperar_vista(p, "grupo")
        check_de(fila_recordada(p, "Llamar al dentista")).tap()
        esperar_recordadas(p, "la cosa queda marcada", hechas=[False, True])
        p.evaluate(JS_VUELVE_LA_CONEXION)
        p.wait_for_timeout(1300)

    # Fuera de la app publicada (http), ni con las notificaciones activadas y recordatorios puestos.
    contexto = contexto_con_avisos(e)
    pedidas = peticiones_de(contexto)
    p = sembrar_recordar(e, datos, propios, contexto=contexto, avisos=AVISOS_ACTIVOS)
    trastear(p)
    igual(peticiones_de_la_nube(pedidas), [], "Por http se pide algo de la nube")
    igual(leer_recordar(p), recordar_guardado(cosas={"id-2": recordatorio()}), "Por http, lo guardado de los recordatorios (sin base: nadie los ha subido)")
    contexto.close()

    # En la app publicada, sin notificaciones ni cuenta: no hay sesión con la que hablar, y ni se carga la nube.
    api = ApiRecordarFalsa()
    contexto = dispositivo_con_recordar(e, api, endpoint=None)
    pedidas = peticiones_de(contexto)
    p = abrir_con_recordar(contexto, e, datos, propios, sesion=None)
    esperar_nota(p, "apagados", "Activar notificaciones")
    trastear(p)
    igual((api.peticiones, peticiones_de_la_nube(pedidas), p.evaluate("() => window.__anonimos || 0")), ([], [], 0), "Sin notificaciones ni cuenta: llamadas a la función, peticiones de la nube y sesiones anónimas abiertas")
    esperar_nota(p, None)  # Lo único pendiente con recordatorio ya está hecho.
    # Poner otro recordatorio: «Guardar» activa las notificaciones (el permiso se da en ese toque) y, ya con sesión, se sube todo.
    abrir_hoja_recordar(p, "Sacar la basura")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    esperar_envios(p, api, 1)
    esperar_base(p, "tras activarse al guardar, los recordatorios se suben", v=1)
    sesion = "Bearer token-anonimo-1"
    igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization"), q["estado"]) for q in api.peticiones],
          [("clave", "GET", None, 200), ("alta", "POST", sesion, 200), ("recordar", "GET", sesion, 200), ("recordar", "POST", sesion, 200)], "Al guardar un recordatorio sin notificaciones: la clave, el alta y, después, la pregunta y el envío")
    igual((api.envios()[0]["cuerpo"], p.evaluate("() => [window.__avisos.pedidos, window.__anonimos]"), guardado_avisos(p)["uid"]),
          (parche(0, {"id-2": recordada_enviada("Llamar al dentista", hecha=True), "id-1": recordada_enviada("Sacar la basura")}), [1, 1], "anonimo-1"),
          "Lo enviado (también lo que ya tenía recordatorio), con el permiso pedido una vez y una sola sesión anónima")
    esperar_nota(p, None)
    contexto.close()

    # Con las notificaciones activadas y nada en Recordar: ni una llamada, ni la nube.
    api = ApiRecordarFalsa()
    contexto = dispositivo_con_recordar(e, api)
    pedidas = peticiones_de(contexto)
    p = abrir_con_recordar(contexto, e, estado_con([cosa(1, "Suelta")], grupos=[]), ruta="#lista")
    esperar_filas(p, 1)
    check_de(fila_de(p, "Suelta")).tap()
    p.evaluate(JS_VUELVE_LA_CONEXION)
    p.wait_for_timeout(1300)
    igual((api.peticiones, peticiones_de_la_nube(pedidas)), ([], []), "Con Recordar vacío: llamadas a la función y peticiones de la nube")
    contexto.close()

    # Con cosas en Recordar y ningún recordatorio: al abrir se le pregunta (otro dispositivo pudo ponérselos) y no se le envía nada.
    api = ApiRecordarFalsa()
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, datos)
    esperar_vista(p, "grupo")
    esperar_lecturas(p, api, 1)
    sin_mas_llamadas(p, api, "Sin recordatorios aquí, tras preguntar")
    igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization"), q["cuerpo"], q["estado"]) for q in api.peticiones], [("recordar", "GET", "Bearer token-anonimo-1", None, 200)],
          "Con cosas en Recordar y sin recordatorios: una sola pregunta, con la sesión de las notificaciones")
    igual((api.documentos, cuandos(p)), ({}, {"Llamar al dentista": "Poner recordatorio", "Sacar la basura": "Poner recordatorio"}), "Sin nada que subir: la función sigue sin documento y las filas, sin recordatorio")
    p.context.close()

    # Con la cuenta de Google y sin notificaciones en este dispositivo: se suben con la sesión de la cuenta (otro dispositivo suyo avisará).
    api = ApiRecordarFalsa()
    p = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=None), e, datos, propios, sesion=None, cuenta=True)
    esperar_vista(p, "grupo")
    envios = esperar_envios(p, api, 1)
    igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization")) for q in api.peticiones], [("recordar", "GET", "Bearer token-u1"), ("recordar", "POST", "Bearer token-u1")], "Con cuenta y sin notificaciones: se pregunta y se envía con la sesión de la cuenta")
    igual((envios[0]["cuerpo"], api.de_la_sesion("u1")["cosas"]), (parche(0, {"id-2": recordada_enviada("Llamar al dentista")}), {"id-2": recordada_enviada("Llamar al dentista")}), "Lo enviado y lo que guarda la función")
    esperar_nota(p, "apagados", "Activar notificaciones")  # Subidos, pero este dispositivo sigue sin avisar: la nota lo dice.


@prueba("recordatorios en la función, la primera vez: el dispositivo le pregunta qué tiene (GET) y le lleva en un solo envío lo que aquí tiene recordatorio (propio o el de todas; pendiente o hecho; lo que no está en Recordar o no lo tiene, no), con la sesión de las notificaciones, sobre la versión leída y con su zona horaria; «general» solo viaja si lo hay; guarda la base (documento, versión, zona y firma) y, al recargar sin cambios, pregunta pero no envía nada")
def t_recordar_nube_primera_vez(e):
    semanal, mensual = recordatorio("21:30", "semana", dia=2), recordatorio("07:05", "mes", diaMes=31)
    datos = estado_recordar([recordada(5, "Llamar al dentista"), recordada(4, "Sacar la basura"), recordada(3, "Regar las plantas", hecha=True, hecha_en=1_700_000_500_000),
                             recordada(2, "Sin recordatorio"), cosa(1, "Suelta, con el recordatorio de cuando estuvo en Recordar")])
    casos = [
        # (qué, lo guardado aquí, lo que viaja, el «general» que viaja)
        ("cada cosa con el suyo", recordar_guardado(cosas={"id-5": recordatorio(), "id-4": semanal, "id-3": mensual, "id-1": recordatorio()}),
         {"id-5": recordada_enviada("Llamar al dentista"), "id-4": recordada_enviada("Sacar la basura", semanal), "id-3": recordada_enviada("Regar las plantas", mensual, hecha=True)}, {}),
        ("con uno para todas (y una cosa a la que se le quitó)", recordar_guardado(general=recordatorio("08:00"), cosas={"id-4": semanal, "id-2": False}),
         {"id-5": recordada_enviada("Llamar al dentista", recordatorio("08:00")), "id-4": recordada_enviada("Sacar la basura", semanal),
          "id-3": recordada_enviada("Regar las plantas", recordatorio("08:00"), hecha=True)}, {"general": recordatorio("08:00")}),
    ]
    for que, propios, enviadas, general in casos:
        api = ApiRecordarFalsa()
        p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, datos, propios)
        esperar_vista(p, "grupo")
        esperar_envios(p, api, 1)
        base = esperar_base(p, f"[{que}] se guarda la base", v=1)
        igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization"), q["cabeceras"].get("content-type"), q["cuerpo"], q["estado"], q["respuesta"]) for q in api.peticiones],
              [("recordar", "GET", "Bearer token-anonimo-1", None, None, 200, SIN_DOCUMENTO),
               ("recordar", "POST", "Bearer token-anonimo-1", "application/json", parche(0, enviadas, **general), 200, {"nacido": "n1", "v": 1, "rechazadas": []})],
              f"[{que}] La primera ronda: una pregunta y un envío, con la sesión")
        igual(api.de_la_sesion("anonimo-1"), {"nacido": "n1", "v": 1, "zona": MADRID, "general": general.get("general"), "cosas": enviadas}, f"[{que}] Lo que guarda la función")
        igual(base, base_recordada("anonimo-1", "n1", 1, enviadas, **general), f"[{que}] La base que recuerda el dispositivo")
        igual(leer_recordar(p), {**propios, "base": base}, f"[{que}] Lo guardado aquí: los recordatorios, como estaban, y la base")
        esperar_nota(p, None)
        sin_mas_llamadas(p, api, f"[{que}] Con todo subido")
        # Al recargar: se pregunta (pudo cambiar en otro sitio) y, sin nada nuevo en ningún lado, no se envía nada.
        p.reload()
        esperar_vista(p, "grupo")
        esperar_lecturas(p, api, 2)
        sin_mas_llamadas(p, api, f"[{que}] Al recargar sin cambios")
        igual((len(api.envios()), api.lecturas()[1]["respuesta"]["v"], leer_recordar(p)), (1, 1, {**propios, "base": base}), f"[{que}] Tras recargar: envíos, la versión leída y lo guardado")
        p.context.close()


@prueba("recordatorios en la función, cada cambio viaja solo y sin preguntar antes: poner un recordatorio, cambiarlo, quitarlo, poner el de todas, marcar, desmarcar, apuntar algo nuevo, sacarlo de Recordar («−»), devolverlo («+»), eliminarlo y quitar todos los recordatorios envían, cada uno, exactamente lo que cambia (qué poner, qué quitar y «general» solo cuando cambia) sobre la última versión; guardar un recordatorio igual al que había no envía nada")
def t_recordar_nube_parches(e):
    api = ApiRecordarFalsa()
    semanal, laborables, ocho = recordatorio("21:30", "semana", dia=2), recordatorio("07:35", "laborables"), recordatorio("08:00")
    datos = estado_recordar([recordada(4, "Llamar al dentista"), recordada(3, "Sacar la basura"), recordada(2, "Regar las plantas"), cosa(1, "Suelta")])
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, datos, recordar_guardado(cosas={"id-3": semanal}))
    esperar_vista(p, "grupo")

    def enviado(cuantos, esperado, que):
        """El envío número «cuantos» es exactamente ese, la función lo acepta y la ronda termina (la base queda en su versión)."""
        envio = esperar_envios(p, api, cuantos)[-1]
        igual((len(api.envios()), {**envio["cuerpo"], "quitar": sorted(envio["cuerpo"]["quitar"])}, envio["estado"], envio["respuesta"]),
              (cuantos, esperado, 200, {"nacido": "n1", "v": cuantos, "rechazadas": []}), que)
        esperar_base(p, f"{que}: la ronda termina", v=cuantos)

    enviado(1, parche(0, {"id-3": recordada_enviada("Sacar la basura", semanal)}), "Al abrir: lo que ya tenía recordatorio")
    # Poner un recordatorio a una cosa que no lo tenía.
    abrir_hoja_recordar(p, "Llamar al dentista")
    guardar_hoja_recordar(p)
    enviado(2, parche(1, {"id-4": recordada_enviada("Llamar al dentista")}), "Poner un recordatorio: solo esa cosa")
    # Cambiarlo.
    abrir_hoja_recordar(p, "Llamar al dentista")
    elegir_hora(p, "07", "35")
    pastilla(p, "De lunes a viernes")
    guardar_hoja_recordar(p)
    enviado(3, parche(2, {"id-4": recordada_enviada("Llamar al dentista", laborables)}), "Cambiar un recordatorio: esa cosa, con el nuevo")
    # Guardarlo otra vez tal cual (y abrir y cerrar la hoja): nada que contar.
    abrir_hoja_recordar(p, "Llamar al dentista")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    p.wait_for_timeout(400)
    abrir_hoja_recordar(p, "Sacar la basura")
    cerrar_hoja_recordar(p)
    sin_mas_llamadas(p, api, "Guardar un recordatorio igual al que había, o abrir y cerrar la hoja")
    # Quitarlo: la función pierde esa cosa, con su texto.
    abrir_hoja_recordar(p, "Llamar al dentista")
    p.locator("#hoja-recordar").get_by_role("button", name="Quitar recordatorio", exact=True).tap()
    enviado(4, parche(3, quitar=["id-4"]), "Quitar un recordatorio: se quita la cosa")
    # El de todas: viaja «general» y, con él, cada cosa pendiente a la que le cambia el suyo.
    abrir_hoja_recordar(p)
    elegir_hora(p, "08")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio puesto a 3 cosas")
    todas = {"id-4": recordada_enviada("Llamar al dentista", ocho), "id-3": recordada_enviada("Sacar la basura", ocho), "id-2": recordada_enviada("Regar las plantas", ocho)}
    enviado(5, parche(4, todas, general=ocho), "Poner el de todas: «general» y las cosas que pasan a seguirlo")
    # Marcar y desmarcar.
    check_de(fila_recordada(p, "Sacar la basura")).tap()
    enviado(6, parche(5, {"id-3": recordada_enviada("Sacar la basura", ocho, hecha=True)}), "Marcar una cosa: va hecha (ya no se avisará de ella)")
    check_de(fila_recordada(p, "Sacar la basura")).tap()
    enviado(7, parche(6, {"id-3": recordada_enviada("Sacar la basura", ocho)}), "Desmarcarla: vuelve a estar pendiente, con su recordatorio")
    # Apuntar algo nuevo en Recordar: sigue el de todas, así que también viaja.
    campo_en_grupo(p).fill("Pagar la comunidad")
    campo_en_grupo(p).press("Enter")
    esperar_toast(p, "Guardado")
    nueva = next(c["id"] for c in leer_estado(p)["cosas"] if c["texto"] == "Pagar la comunidad")
    enviado(8, parche(7, {nueva: recordada_enviada("Pagar la comunidad", ocho)}), "Apuntar una cosa en Recordar con un recordatorio para todas")
    # Sacarla de Recordar («−») la quita de la función; al devolverla («Añadir cosas», «+») sube otra vez, con el recordatorio que vuelve a seguir.
    quitar_de_recordar(p, "Regar las plantas").tap()
    esperar_toast(p, "Quitado de Recordar")
    enviado(9, parche(8, quitar=["id-2"]), "Sacar una cosa de Recordar")
    boton_anadir(p).tap()
    esperar_pagina_grupo(p, "el desplegable ofrece la cosa que salió", desplegable=True, candidatas=["Regar las plantas", "Suelta"])
    mas_de(p, "Regar las plantas").tap()
    enviado(10, parche(9, {"id-2": recordada_enviada("Regar las plantas", ocho)}), "Devolverla a Recordar")
    p.keyboard.press("Escape")
    # Eliminarla (hecha, lleva la papelera): primero viaja hecha y después se quita.
    check_de(fila_recordada(p, "Regar las plantas")).tap()
    enviado(11, parche(10, {"id-2": recordada_enviada("Regar las plantas", ocho, hecha=True)}), "Marcar otra cosa")
    borrar_de(fila_recordada(p, "Regar las plantas")).tap()
    esperar_toast(p, "Eliminado")
    enviado(12, parche(11, quitar=["id-2"]), "Eliminar una cosa de Recordar")
    # Quitar todos los recordatorios: «general» viaja como null y se quitan las cosas pendientes que lo seguían.
    esperar_sin_toast(p, ms=6000)
    abrir_hoja_recordar(p)
    p.locator("#hoja-recordar").get_by_role("button", name="Quitar todos los recordatorios", exact=True).tap()
    esperar_toast(p, "Recordatorios quitados")
    enviado(13, parche(12, quitar=sorted(["id-4", "id-3", nueva]), general=None), "Quitar todos los recordatorios")
    igual((api.de_la_sesion("anonimo-1"), len(api.lecturas()), {q["cabeceras"].get("authorization") for q in api.peticiones}),
          ({"nacido": "n1", "v": 13, "zona": MADRID, "general": None, "cosas": {}}, 1, {"Bearer token-anonimo-1"}),
          "Al final: la función se queda sin nada, solo se le preguntó al abrir y todo fue con la sesión")
    igual(leer_recordar(p), {"general": None, "cosas": {}, "base": base_recordada("anonimo-1", "n1", 13, {}), "activar": False}, "Lo guardado aquí al final")


@prueba("recordatorios en la función, choque de versiones (409): si entretanto cambió allí, el envío se repite sobre lo que hay, juntándolo cosa a cosa y campo a campo: lo que puso o quitó otro dispositivo se trae; un «Hecho» pulsado en un aviso marca aquí el check aunque aquí se cambiara la hora; lo desmarcado aquí después gana a ese «Hecho»; si cambiaron los dos lo mismo, gana este dispositivo; y si no deja de cambiar, a la cuarta lo deja para otra vez (la nota lo dice) y lo sube al volver la conexión")
def t_recordar_nube_choques(e):
    e.tolerar += CHOQUE_DE_VERSIONES
    api = ApiRecordarFalsa()
    uid = "anonimo-1"
    diario, martes, tarde, martes_tarde = recordatorio(), recordatorio("21:30", "semana", dia=2), recordatorio("18:00"), recordatorio("18:30", "semana", dia=2)
    datos = estado_recordar([recordada(4, "Llamar al dentista"), recordada(3, "Sacar la basura"), recordada(2, "Regar las plantas")])
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, datos, recordar_guardado(cosas={"id-4": diario, "id-3": diario, "id-2": diario}))
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    esperar_base(p, "la primera ronda termina", v=1)

    def choque(cuantos, v, esperado, que):
        """Los dos últimos envíos (de «cuantos»): el que chocó, sobre la versión que este dispositivo creía, y su repetición sobre «v», la que había."""
        envios = esperar_envios(p, api, cuantos)[-2:]
        esperar_base(p, f"{que}: la ronda termina", v=v + 1)
        igual((len(api.envios()), [(q["cuerpo"]["siV"], q["estado"], q["respuesta"]["v"]) for q in envios], envios[1]["cuerpo"]), (cuantos, [(v - 1, 409, v), (v, 200, v + 1)], esperado), que)

    # Otro dispositivo le cambia el recordatorio a una cosa y se lo quita a otra; aquí, mientras, se cambia el de una tercera.
    api.desde_otro_sitio(uid, lambda doc: (doc["cosas"]["id-3"].update(r=martes), doc["cosas"].pop("id-2")))
    abrir_hoja_recordar(p, "Llamar al dentista")
    elegir_hora(p, "18")
    guardar_hoja_recordar(p)
    choque(3, 2, parche(2, {"id-4": recordada_enviada("Llamar al dentista", tarde)}), "Tras el choque solo viaja lo de aquí")
    igual((cuandos(p), leer_recordar(p)["cosas"], api.de_la_sesion(uid)["cosas"]),
          ({"Llamar al dentista": "18:00 · Cada día", "Sacar la basura": "21:30 · Cada martes", "Regar las plantas": "Poner recordatorio"}, {"id-4": tarde, "id-3": martes},
           {"id-4": recordada_enviada("Llamar al dentista", tarde), "id-3": recordada_enviada("Sacar la basura", martes)}),
          "Lo que cambió el otro dispositivo se trae (otro recordatorio, y uno quitado) y lo de aquí se sube")
    # «Hecho» en el aviso de una cosa a la que aquí, sin saberlo, se le cambia la hora: queda hecha, con la hora nueva.
    api.responder("hecho", "POST", None, {"uid": uid, "id": "id-3", "ficha": "la-del-aviso"})
    abrir_hoja_recordar(p, "Sacar la basura")
    elegir_hora(p, "18", "30")
    guardar_hoja_recordar(p)
    choque(5, 4, parche(4, {"id-3": recordada_enviada("Sacar la basura", martes_tarde, hecha=True)}), "Cambiar aquí la hora no deshace el «Hecho» del aviso")
    esperar_recordadas(p, "el «Hecho» del aviso marca el check", textos=["Llamar al dentista", "Regar las plantas", "Sacar la basura"], hechas=[False, False, True])
    marcada = next(c for c in leer_estado(p)["cosas"] if c["id"] == "id-3")
    comprobar(marcada["hecha"] is True and isinstance(marcada["hechaEn"], (int, float)) and p.locator("#cuenta-recordar").text_content() == "2",
              f"El «Hecho» del aviso no queda guardado como el check (o la fila fija no lo cuenta): {marcada}")
    # Desmarcarla aquí después (mientras otro dispositivo cambia otra cosa): gana lo de aquí, vuelve a estar pendiente también allí.
    api.desde_otro_sitio(uid, lambda doc: doc["cosas"]["id-4"].update(r=martes))
    check_de(fila_recordada(p, "Sacar la basura")).tap()
    choque(7, 6, parche(6, {"id-3": recordada_enviada("Sacar la basura", martes_tarde)}), "Lo desmarcado aquí gana al «Hecho» que ya se conocía")
    igual((cuandos(p)["Llamar al dentista"], api.de_la_sesion(uid)["cosas"]["id-3"]["hecha"], [f["hecha"] for f in recordadas(p)]), ("21:30 · Cada martes", False, [False, False, False]),
          "Tras desmarcar: se trae el recordatorio del otro dispositivo y la cosa queda pendiente aquí y allí")
    # Los dos cambian el recordatorio de la misma cosa: gana este dispositivo.
    api.desde_otro_sitio(uid, lambda doc: doc["cosas"]["id-4"].update(r=recordatorio("06:00")))
    abrir_hoja_recordar(p, "Llamar al dentista")
    elegir_hora(p, "18", "30")
    guardar_hoja_recordar(p)
    choque(9, 8, parche(8, {"id-4": recordada_enviada("Llamar al dentista", martes_tarde)}), "Cambiado en los dos sitios: gana este dispositivo")
    igual((cuandos(p)["Llamar al dentista"], api.de_la_sesion(uid)["cosas"]["id-4"]["r"], len(api.lecturas())), ("18:30 · Cada martes", martes_tarde, 1), "Queda el de aquí, aquí y allí; y en todo esto no hizo falta preguntar")
    # Si allí no deja de cambiar, no se insiste sin fin: cuatro intentos y se deja para otra vez. La nota avisa de que no está subido.
    api.antes_de_guardar = lambda: api.desde_otro_sitio(uid, lambda doc: None)
    check_de(fila_recordada(p, "Llamar al dentista")).tap()
    esperar_envios(p, api, 13)
    sin_mas_llamadas(p, api, "Tras cuatro choques seguidos")
    igual(([(q["cuerpo"]["siV"], q["estado"]) for q in api.envios()[9:]], leer_recordar(p)["base"]["v"]), ([(9, 409), (10, 409), (11, 409), (12, 409)], 9), "Cuatro intentos, cada uno sobre la versión que le acaban de decir, y la base donde estaba")
    esperar_nota(p, "sin-conexion")
    api.antes_de_guardar = None
    p.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_envios(p, api, 14)
    esperar_nota(p, None)
    igual((api.envios()[-1]["cuerpo"], api.envios()[-1]["estado"], len(api.lecturas()), api.de_la_sesion(uid)["cosas"]["id-4"]["hecha"]),
          (parche(13, {"id-4": recordada_enviada("Llamar al dentista", martes_tarde, hecha=True)}), 200, 2, True), "Al volver la conexión: se pregunta y se sube lo que quedó sin subir")


@prueba("recordatorios en la función, cuando ya no es el mismo documento: con la base al día, lo que falta allí es que se quitó en otro dispositivo (y se quita aquí); pero si la función ya no tiene aquel documento (ninguno, otro «nacido», o una versión anterior a la conocida) la base no vale: se juntan los dos lados sin quitar aquí nada, lo de aquí gana salvo lo hecho (que lo es si lo dice cualquiera) y todo lo de aquí se vuelve a subir; lo que allí hay de cosas que aquí no se conocen, ni se toca")
def t_recordar_nube_otro_documento(e):
    api = ApiRecordarFalsa()
    uid = "anonimo-1"
    diario, martes, tarde = recordatorio(), recordatorio("21:30", "semana", dia=2), recordatorio("18:00")
    datos = estado_recordar([recordada(4, "Llamar al dentista"), recordada(3, "Sacar la basura"), recordada(2, "Regar las plantas")])
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, datos, recordar_guardado(cosas={"id-4": diario, "id-3": martes}))
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    esperar_base(p, "la primera ronda termina", v=1, nacido="n1")
    dentista, basura = recordada_enviada("Llamar al dentista"), recordada_enviada("Sacar la basura")

    def releer(lecturas, que, **base):
        """Vuelve la conexión: se le pregunta a la función y la ronda termina con esa base."""
        p.evaluate(JS_VUELVE_LA_CONEXION)
        esperar_lecturas(p, api, lecturas)
        return esperar_base(p, que, **base)

    # Con la base al día: una cosa que allí ya no está se quitó en otro dispositivo. Aquí pierde el recordatorio y no se envía nada.
    api.desde_otro_sitio(uid, lambda doc: doc["cosas"].pop("id-3"))
    releer(2, "se trae lo quitado en otro dispositivo", v=2)
    esperar_recordadas(p, "la cosa pierde aquí su recordatorio", cuandos=["9:00 · Cada día", "Poner recordatorio", "Poner recordatorio"])
    igual((leer_recordar(p)["cosas"], len(api.envios())), ({"id-4": diario}, 1), "Quitado en otro dispositivo: lo guardado aquí, y sin enviar nada")
    abrir_hoja_recordar(p, "Sacar la basura")
    guardar_hoja_recordar(p)
    esperar_envios(p, api, 2)
    esperar_base(p, "el recordatorio puesto de nuevo se sube", v=3)

    # La función ya no tiene el documento (se le pidió olvidarlo, o caducó): nada de aquí se quita y todo se sube otra vez, a uno nuevo.
    del api.documentos[uid]
    base = releer(3, "todo se sube a un documento nuevo", nacido="n2", v=1)
    igual((api.envios()[-1]["cuerpo"], api.lecturas()[-1]["respuesta"], len(api.envios())), (parche(0, {"id-4": dentista, "id-3": basura}), SIN_DOCUMENTO, 3), "Sin documento allí: lo de aquí, entero, sobre la versión 0")
    igual((cuandos(p), leer_recordar(p)["cosas"], base), ({"Llamar al dentista": "9:00 · Cada día", "Sacar la basura": "9:00 · Cada día", "Regar las plantas": "Poner recordatorio"},
                                                           {"id-4": diario, "id-3": diario}, base_recordada(uid, "n2", 1, {"id-4": dentista, "id-3": basura})), "Aquí no se pierde ningún recordatorio, y la base es la del documento nuevo")

    # Otro documento («nacido» distinto), con otras cosas: se juntan. Lo de aquí que allí no está, se sube; de lo que está en los dos,
    # manda lo de aquí, salvo que allí esté hecho; lo que solo está allí se trae si la cosa está aquí en Recordar, y si no, ni se toca.
    ajena = recordada_enviada("De otro dispositivo, que aquí aún no ha llegado")
    api.documentos[uid] = {"nacido": "otro", "v": 7, "zona": MADRID, "general": None, "cosas": {
        "id-3": recordada_enviada("Sacar la basura", tarde, hecha=True), "id-2": recordada_enviada("Regar las plantas", martes), "id-99": ajena}}
    base = releer(4, "se junta con el otro documento", nacido="otro", v=8)
    basura_hecha, plantas = recordada_enviada("Sacar la basura", hecha=True), recordada_enviada("Regar las plantas", martes)
    igual((api.envios()[-1]["cuerpo"], len(api.envios())), (parche(7, {"id-4": dentista, "id-3": basura_hecha}), 4), "Otro documento: se sube lo que allí falta o difiere, sin quitar nada")
    esperar_recordadas(p, "lo juntado se ve en las filas", textos=["Llamar al dentista", "Regar las plantas", "Sacar la basura"], cuandos=["9:00 · Cada día", "21:30 · Cada martes", "9:00 · Cada día"], hechas=[False, False, True])
    igual((leer_recordar(p)["cosas"], api.de_la_sesion(uid)["cosas"], base),
          ({"id-4": diario, "id-3": diario, "id-2": martes}, {"id-3": basura_hecha, "id-2": plantas, "id-99": ajena, "id-4": dentista}, base_recordada(uid, "otro", 8, {"id-4": dentista, "id-3": basura_hecha, "id-2": plantas})),
          "Tras juntar: aquí, allí (lo ajeno sigue intacto) y la base (sin lo ajeno)")

    # El mismo documento, pero en una versión anterior a la que se conocía y sin nada: no es que lo hayan quitado todo, es que ha vuelto atrás.
    api.documentos[uid] = {"nacido": "otro", "v": 3, "zona": MADRID, "general": None, "cosas": {}}
    releer(5, "todo vuelve a subirse", nacido="otro", v=4)
    igual((api.envios()[-1]["cuerpo"], len(api.envios()), leer_recordar(p)["cosas"], [f["cuando"] for f in recordadas(p)]),
          (parche(3, {"id-4": dentista, "id-2": plantas, "id-3": basura_hecha}), 5, {"id-4": diario, "id-3": diario, "id-2": martes}, ["9:00 · Cada día", "21:30 · Cada martes", "9:00 · Cada día"]),
          "Una versión anterior: se sube todo otra vez y aquí no se quita nada")

    # Otro documento que, por casualidad, va por la versión conocida: el siguiente cambio entra sin choque, pero la respuesta dice
    # que aquel documento no era este. Entonces se le pregunta qué tiene y se junta de nuevas: lo que le falta de aquí se sube.
    api.documentos[uid] = {"nacido": "tercero", "v": 4, "zona": MADRID, "general": None, "cosas": {"id-99": ajena}}
    p.wait_for_timeout(400)
    check_de(fila_recordada(p, "Regar las plantas")).tap()
    esperar_envios(p, api, 7)
    base = esperar_base(p, "se junta con el documento que resultó ser otro", nacido="tercero", v=6)
    plantas_hecha = recordada_enviada("Regar las plantas", martes, hecha=True)
    igual(([(q["cuerpo"], q["respuesta"]) for q in api.envios()[5:]], len(api.lecturas()), api.de_la_sesion(uid)["cosas"], base),
          ([(parche(4, {"id-2": plantas_hecha}), {"nacido": "tercero", "v": 5, "rechazadas": []}), (parche(5, {"id-4": dentista, "id-3": basura_hecha}), {"nacido": "tercero", "v": 6, "rechazadas": []})], 6,
           {"id-99": ajena, "id-2": plantas_hecha, "id-4": dentista, "id-3": basura_hecha}, base_recordada(uid, "tercero", 6, {"id-4": dentista, "id-3": basura_hecha, "id-2": plantas_hecha})),
          "Escrito sobre otro documento sin saberlo: se pregunta y se sube también lo que a ese le faltaba")


@prueba("recordatorios en la función, el tope de 100 cosas y lo que no admite: la app le lleva como mucho 100 (las pendientes antes que las hechas) y, al marcar una, su sitio pasa a una hecha que no cabía; lo que la función rechaza porque no le cabe (lo dice en «rechazadas») queda en la base como enviado: no se repite sin fin, ni con el siguiente cambio; y en la lectura siguiente, como allí no está, aquí se queda sin recordatorio (no va a avisar de ello); y cien cosas de texto tan largo que no caben en un envío (100000 bytes) viajan en dos seguidos, sin dejarse ninguna (tampoco si entre uno y otro la función cambia)")
def t_recordar_nube_tope(e):
    diario = recordatorio()
    # Aquí, 102 cosas que siguen el recordatorio de todas: 99 pendientes y 3 hechas. Caben las pendientes y la primera de las hechas.
    api = ApiRecordarFalsa()
    pendientes = [recordada(n, f"Pendiente {n}") for n in range(108, 9, -1)]
    hechas = [recordada(n, f"Hecha {n}", hecha=True, hecha_en=1_700_000_100_000 + n) for n in (1, 2, 3)]
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar(pendientes + hechas), recordar_guardado(general=diario))
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    esperar_base(p, "la primera ronda termina", v=1)
    caben = {c["id"]: recordada_enviada(c["texto"], hecha=c["hecha"]) for c in pendientes + hechas[:1]}
    igual((len(caben), api.envios()[0]["cuerpo"], api.envios()[0]["respuesta"]), (MAX_RECORDADAS, parche(0, caben, general=diario), {"nacido": "n1", "v": 1, "rechazadas": []}), "Con 102 cosas: viajan 100 (las 99 pendientes y la primera hecha) y la función las admite todas")
    check_de(fila_recordada(p, "Pendiente 108")).tap()
    esperar_envios(p, api, 2)
    esperar_base(p, "el cambio se sube", v=2)
    igual((api.envios()[1]["cuerpo"], api.envios()[1]["respuesta"]["rechazadas"], len(api.de_la_sesion("anonimo-1")["cosas"])),
          (parche(1, {"id-2": recordada_enviada("Hecha 2", hecha=True)}, quitar=["id-108"]), [], MAX_RECORDADAS), "Al marcar una: la recién hecha (la última) deja su sitio a la siguiente hecha, y siguen siendo 100")
    p.context.close()

    # La función ya guarda 99 cosas de esta sesión que este dispositivo no conoce: de las dos de aquí solo cabe una.
    api = ApiRecordarFalsa()
    ajenas = {f"ajena-{n}": recordada_enviada(f"De otro dispositivo {n}") for n in range(MAX_RECORDADAS - 1)}
    api.documentos["anonimo-1"] = {"nacido": "de-antes", "v": 5, "zona": MADRID, "general": None, "cosas": dict(ajenas)}
    dentista, basura = recordada_enviada("Llamar al dentista"), recordada_enviada("Sacar la basura")
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")]), recordar_guardado(cosas={"id-2": diario, "id-1": diario}))
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    base = esperar_base(p, "la primera ronda termina", v=6)
    igual((api.envios()[0]["cuerpo"], api.envios()[0]["respuesta"], base), (parche(5, {"id-2": dentista, "id-1": basura}), {"nacido": "de-antes", "v": 6, "rechazadas": ["id-1"]},
                                                                            base_recordada("anonimo-1", "de-antes", 6, {"id-2": dentista, "id-1": basura})),
          "La que no cabe vuelve en «rechazadas» y queda en la base como enviada")
    sin_mas_llamadas(p, api, "Tras un rechazo")
    check_de(fila_recordada(p, "Llamar al dentista")).tap()
    esperar_envios(p, api, 2)
    esperar_base(p, "el cambio se sube", v=7)
    igual((api.envios()[1]["cuerpo"], api.envios()[1]["respuesta"]["rechazadas"]), (parche(6, {"id-2": recordada_enviada("Llamar al dentista", hecha=True)}), []), "El siguiente cambio viaja solo: la rechazada no se repite")
    # Al volver a preguntar: la base dice que se envió y allí no está, que es lo que pasa cuando se quita en otro dispositivo.
    p.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_lecturas(p, api, 2)
    esperar_recordadas(p, "la rechazada se queda sin recordatorio", textos=["Sacar la basura", "Llamar al dentista"], cuandos=["Poner recordatorio", "9:00 · Cada día"])
    sin_mas_llamadas(p, api, "Tras releer con una rechazada")
    igual((len(api.envios()), leer_recordar(p)["cosas"], sorted(api.de_la_sesion("anonimo-1")["cosas"]) == sorted([*ajenas, "id-2"])), (2, {"id-2": diario}, True), "Al final: sin más envíos, la rechazada sin recordatorio aquí y la función con sus 100")
    p.context.close()

    # Cien cosas de 500 letras. Si cada letra ocupa un byte, caben en un envío; con letras de dos bytes (ñ, tildes, otros alfabetos)
    # el envío pasaría de lo que la función admite y lo rechazaría entero: viajan en dos, el segundo sobre la versión que deja el primero.
    for letra, envios_debidos in (("n", 1), ("ñ", 2)):
        api = ApiRecordarFalsa()
        largas = [recordada(n, f"{n:03d} " + letra * 496) for n in range(100, 0, -1)]
        p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar(largas), recordar_guardado(general=diario))
        esperar_vista(p, "grupo")
        envios = esperar_envios(p, api, envios_debidos)
        base = esperar_base(p, f"[{letra}] las cien cosas están subidas", v=envios_debidos)
        sin_mas_llamadas(p, api, f"[{letra}] Con las cien cosas subidas")
        todas = {c["id"]: recordada_enviada(c["texto"]) for c in largas}
        enviadas = [id for envio in envios for id in envio["cuerpo"]["poner"]]
        igual(([(q["cuerpo"]["siV"], q["estado"], q["respuesta"], q["bytes"] <= MAX_BYTES_DE_RECORDAR, "general" in q["cuerpo"], q["cuerpo"]["quitar"]) for q in envios], enviadas, sum(q["bytes"] for q in envios) > MAX_BYTES_DE_RECORDAR),
              ([(n, 200, {"nacido": "n1", "v": n + 1, "rechazadas": []}, True, n == 0, []) for n in range(envios_debidos)], list(todas), envios_debidos > 1),
              f"[{letra}] Cada envío cabe en lo que la función admite y va sobre la versión que deja el anterior; entre todos llevan las cien cosas, en orden y una sola vez, y el de todas solo va en el primero")
        igual((api.de_la_sesion("anonimo-1")["cosas"], base), (todas, base_recordada("anonimo-1", "n1", envios_debidos, todas, general=diario)), f"[{letra}] La función las tiene todas, y la base lo sabe")
        esperar_nota(p, None)
        p.context.close()

    # Si entre un envío y el siguiente la función cambia (otro dispositivo se adelanta), el segundo choca: se junta con lo que ya hay
    # y se repite solo lo que faltaba.
    e.tolerar += CHOQUE_DE_VERSIONES
    api = ApiRecordarFalsa()
    api.antes_de_guardar = lambda: len(api.envios()) == 2 and api.desde_otro_sitio("anonimo-1", lambda doc: None)
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar(largas), recordar_guardado(general=diario))
    esperar_vista(p, "grupo")
    envios = esperar_envios(p, api, 3)
    esperar_base(p, "las cien cosas están subidas, tras el choque", v=3)
    sin_mas_llamadas(p, api, "Con las cien cosas subidas tras un choque entre envíos")
    igual(([(q["cuerpo"]["siV"], q["estado"]) for q in envios], envios[2]["cuerpo"], api.de_la_sesion("anonimo-1")["cosas"]), ([(0, 200), (1, 409), (2, 200)], parche(2, envios[1]["cuerpo"]["poner"]), todas),
          "Un choque entre dos envíos seguidos: el que choca se repite, solo con lo suyo, sobre lo que hay, y no falta ninguna cosa")


@prueba("recordatorios en la función, la zona horaria: la pone el primer dispositivo que guarda; otro de la misma sesión, en otra zona, envía la que la función ya tiene (guardar desde él no mueve la hora de todos los avisos); solo la cambia el dispositivo cuya propia zona ha cambiado desde su última ronda (un viaje), y lo dice aunque no tenga nada más que contar; después, los demás siguen con la nueva")
def t_recordar_nube_zona(e):
    e.tolerar += CHOQUE_DE_VERSIONES
    api = ApiRecordarFalsa()
    diario, martes = recordatorio(), recordatorio("21:30", "semana", dia=2)
    nueva_york, tokio = "America/New_York", "Asia/Tokyo"
    movil, tableta = ENDPOINT_PRUEBA + "movil", ENDPOINT_PRUEBA + "tableta"
    datos = estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")])
    dentista, basura = recordada_enviada("Llamar al dentista"), recordada_enviada("Sacar la basura", martes)
    # El móvil, en Madrid, es el primero en guardar: la zona es la suya.
    a = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=movil), e, datos, recordar_guardado(cosas={"id-2": diario}), sesion="u1", endpoint=movil)
    esperar_envios(a, api, 1)
    esperar_base(a, "el móvil sube lo suyo", v=1)
    igual((api.envios()[0]["cuerpo"], api.de_la_sesion("u1")["zona"]), (parche(0, {"id-2": dentista}), MADRID), "El primer dispositivo pone su zona")
    # La tableta, en Nueva York, con la misma sesión: envía la zona que la función ya tiene, también al cambiar algo después.
    b = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=tableta, zona=nueva_york), e, datos, recordar_guardado(cosas={"id-1": martes}), sesion="u1", endpoint=tableta)
    esperar_envios(b, api, 2)
    base = esperar_base(b, "la tableta sube lo suyo", v=2)
    igual((api.envios()[1]["cuerpo"], base, cuandos(b)), (parche(1, {"id-1": basura}), base_recordada("u1", "n1", 2, {"id-2": dentista, "id-1": basura}, zona=nueva_york, zona_de_la_funcion=MADRID),
                                                        {"Llamar al dentista": "9:00 · Cada día", "Sacar la basura": "21:30 · Cada martes"}),
          "Otro dispositivo en otra zona: envía la de la función, recuerda la suya y trae lo que había")
    check_de(fila_recordada(b, "Sacar la basura")).tap()
    esperar_envios(b, api, 3)
    esperar_base(b, "el cambio de la tableta se sube", v=3)
    igual((api.envios()[2]["cuerpo"], api.de_la_sesion("u1")["zona"]), (parche(2, {"id-1": recordada_enviada("Sacar la basura", martes, hecha=True)}), MADRID), "Al cambiar algo desde la otra zona, la de la función sigue igual")
    # La tableta viaja a Tokio (lo mismo guardado, otra zona en su reloj): lo dice ella sola, sin nada más que contar.
    guardado = b.evaluate("(claves) => claves.map((clave) => localStorage.getItem(clave))", [CLAVE, CLAVE_RECORDAR])
    b.context.close()
    c = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=tableta, zona=tokio), e, guardado[0], guardado[1], sesion="u1", endpoint=tableta)
    esperar_envios(c, api, 4)
    base = esperar_base(c, "la tableta dice su zona nueva", v=4)
    hecha = recordada_enviada("Sacar la basura", martes, hecha=True)
    igual((api.envios()[3]["cuerpo"], api.envios()[3]["respuesta"], api.de_la_sesion("u1")["zona"], base),
          ({"siV": 3, "zona": tokio}, {"nacido": "n1", "v": 4, "rechazadas": []}, tokio, base_recordada("u1", "n1", 4, {"id-2": dentista, "id-1": hecha}, zona=tokio)),
          "El dispositivo que cambia de zona se lo dice a la función: solo la versión y la zona")
    sin_mas_llamadas(c, api, "Dicha la zona nueva")
    # El móvil sigue en Madrid: su siguiente cambio choca (la función ha cambiado) y se repite con la zona que ella tiene ahora.
    check_de(fila_recordada(a, "Llamar al dentista")).tap()
    esperar_envios(a, api, 6)
    base = esperar_base(a, "el cambio del móvil se sube", v=5)
    dentista_hecha = recordada_enviada("Llamar al dentista", hecha=True)
    igual(([(q["cuerpo"]["siV"], q["cuerpo"]["zona"], q["estado"]) for q in api.envios()[4:]], api.envios()[5]["cuerpo"], api.de_la_sesion("u1")["zona"], base),
          ([(1, MADRID, 409), (4, tokio, 200)], parche(4, {"id-2": dentista_hecha}, zona=tokio), tokio, base_recordada("u1", "n1", 5, {"id-2": dentista_hecha, "id-1": hecha}, zona=MADRID, zona_de_la_funcion=tokio)),
          "El que no ha cambiado de zona sigue con la de la función, sea la que sea")


@prueba("recordatorios entre dos dispositivos de la misma cuenta: las cosas viajan por la nube y sus recordatorios por la función de avisos; el que se pone en uno aparece en la fila del otro, y al revés; el de todas puesto en uno pasa a ser el de todas en el otro; el que se quita en uno se quita en el otro; y un «Hecho» pulsado en un aviso marca el check en los dos")
def t_recordar_nube_dos_dispositivos(e):
    nube, api = NubeFalsa(), ApiRecordarFalsa()
    movil, tableta = ENDPOINT_PRUEBA + "movil", ENDPOINT_PRUEBA + "tableta"
    diario, tarde, ocho = recordatorio(), recordatorio("18:00"), recordatorio("08:00")
    datos = estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")])
    a = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=movil, nube=nube), e, datos, recordar_guardado(cosas={"id-2": diario}), sesion="u1", endpoint=movil, cuenta=True)
    esperar_nube(a, "al-dia")
    esperar_envios(a, api, 1)
    esperar_base(a, "el móvil sube su recordatorio", v=1)
    # La tableta, recién estrenada con la misma cuenta: le llegan las cosas (por la nube) y, con ellas, el recordatorio (por la función).
    b = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1", endpoint=tableta, nube=nube), e, sesion="u1", endpoint=tableta, cuenta=True)
    esperar_recordadas(b, "a la tableta le llegan las cosas y el recordatorio del móvil", textos=["Llamar al dentista", "Sacar la basura"], cuandos=["9:00 · Cada día", "Poner recordatorio"])
    base = esperar_base(b, "la tableta se queda con lo que tiene la función", v=1)
    igual((leer_recordar(b)["cosas"], base, len(api.envios())), ({"id-2": diario}, base_recordada("u1", "n1", 1, {"id-2": recordada_enviada("Llamar al dentista")}), 1), "En la tableta: el recordatorio traído, su base, y nada que enviar")
    # Y al revés: el que se pone en la tableta llega al móvil en cuanto vuelve a preguntar.
    abrir_hoja_recordar(b, "Sacar la basura")
    elegir_hora(b, "18")
    guardar_hoja_recordar(b)
    esperar_envios(b, api, 2)
    igual((api.envios()[1]["cuerpo"], api.envios()[1]["estado"]), (parche(1, {"id-1": recordada_enviada("Sacar la basura", tarde)}), 200), "Lo que envía la tableta")
    a.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_recordadas(a, "al móvil le llega el recordatorio de la tableta", cuandos=["9:00 · Cada día", "18:00 · Cada día"])
    esperar_base(a, "el móvil se pone al día", v=2)
    # El de todas, puesto en el móvil: en la tableta (al abrirla) también es el de todas, y cada cosa lo sigue.
    abrir_hoja_recordar(a)
    elegir_hora(a, "08")
    guardar_hoja_recordar(a)
    esperar_toast(a, "Recordatorio puesto a 2 cosas")
    esperar_envios(a, api, 3)
    esperar_base(a, "el móvil sube el de todas", v=3)
    igual(api.envios()[2]["cuerpo"], parche(2, {"id-2": recordada_enviada("Llamar al dentista", ocho), "id-1": recordada_enviada("Sacar la basura", ocho)}, general=ocho), "Lo que envía el móvil al poner el de todas")
    b.reload()
    esperar_recordadas(b, "la tableta sigue el de todas", cuandos=["8:00 · Cada día", "8:00 · Cada día"])
    esperar_base(b, "la tableta se pone al día", v=3)
    igual((leer_recordar(b)["general"], leer_recordar(b)["cosas"]), (ocho, {}), "En la tableta: el de todas es el del móvil y ninguna cosa necesita uno propio")
    # El que se quita en el móvil (a una cosa sola) se quita en la tableta.
    abrir_hoja_recordar(a, "Sacar la basura")
    a.locator("#hoja-recordar").get_by_role("button", name="Quitar recordatorio", exact=True).tap()
    esperar_envios(a, api, 4)
    igual(api.envios()[3]["cuerpo"], parche(3, quitar=["id-1"]), "Lo que envía el móvil al quitar un recordatorio")
    b.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_recordadas(b, "la tableta pierde ese recordatorio", cuandos=["8:00 · Cada día", "Poner recordatorio"])
    igual(leer_recordar(b)["cosas"], {"id-1": False}, "En la tableta queda dicho que esa cosa no sigue el de todas")
    # «Hecho» en el aviso que llegó a cualquiera de los dos: el check queda marcado en ambos (en la tableta, al preguntar; en el móvil, además, por la nube).
    api.responder("hecho", "POST", None, {"uid": "u1", "id": "id-2", "ficha": "la-del-aviso"})
    b.evaluate(JS_VUELVE_LA_CONEXION)
    for pagina, cual in ((b, "la tableta"), (a, "el móvil")):
        esperar_recordadas(pagina, f"el «Hecho» del aviso llega a {cual}", textos=["Sacar la basura", "Llamar al dentista"], hechas=[False, True])
    esperar_en_la_nube(a, nube, lambda docs: docs.get("c-id-2", {}).get("hecha") is True, "la cosa hecha llega a la nube")
    a.wait_for_timeout(1300)  # Al llegarle por la nube la cosa hecha, cada uno vuelve a preguntar: pasado ese rato, nadie ha tenido nada que enviar.
    igual((len(api.envios()), [q["estado"] for q in api.envios()], api.de_la_sesion("u1")["cosas"]), (4, [200] * 4, {"id-2": recordada_enviada("Llamar al dentista", ocho, hecha=True)}),
          "Al final: cuatro envíos entre los dos, ninguno rechazado, y la función con lo que los dos enseñan")


@prueba("desactivar las notificaciones con recordatorios: con algo pendiente en Recordar el aviso lo advierte («Notificaciones desactivadas: Recordar no te avisará», solo texto, unos 4 s); sin cuenta de Google, antes de dar de baja el dispositivo se le pide a la función que olvide los recordatorios, y aquí se olvida la base (siguen puestos; al reactivarlas se suben todos otra vez, a un documento nuevo); lo que un envío a medias deje allí después, se olvida también; con cuenta de Google ni se olvidan allí ni aquí: otro dispositivo suyo puede seguir avisando")
def t_recordar_nube_desactivar(e):
    diario = recordatorio()
    datos = estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura", hecha=True)])
    propios = recordar_guardado(cosas={"id-2": diario, "id-1": diario})
    enviadas = {"id-2": recordada_enviada("Llamar al dentista"), "id-1": recordada_enviada("Sacar la basura", hecha=True)}
    advertencia = "Notificaciones desactivadas: Recordar no te avisará"

    def desactivar(api, que, texto=advertencia, **dispositivo_y_sesion):
        """Abre Ajustes con los recordatorios ya subidos y desactiva las notificaciones; devuelve la página y el aviso."""
        usuario = dispositivo_y_sesion.get("usuario")
        p = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario=usuario), e, dispositivo_y_sesion.get("datos", datos), propios, ruta="#ajustes", sesion=usuario or "anonimo-1", cuenta=bool(usuario))
        esperar_vista(p, "ajustes")
        esperar_envios(p, api, 1)
        esperar_base(p, f"[{que}] los recordatorios están subidos", v=1)
        p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
        boton_avisos(p).click()
        esperar_boton_avisos(p, "Activar notificaciones")
        aviso = esperar_toast(p, texto)
        igual((aviso["icono"], aviso["boton"]), (False, None), f"[{que}] El aviso al desactivarlas es solo texto")
        esperar_llamadas(p, api, "baja", 1)
        return p

    # Sin cuenta de Google: la función olvida los recordatorios (antes de la baja, mientras el dispositivo aún es de esa sesión).
    api = ApiRecordarFalsa()
    p = desactivar(api, "sin cuenta")
    igual([llamada for llamada in api.llamadas if llamada[0] in ("olvidar", "baja")], [("olvidar", "Bearer token-anonimo-1", {}), ("baja", "Bearer token-anonimo-1", {"endpoint": ENDPOINT_PRUEBA + "E1"})],
          "Sin cuenta: se pide olvidar y, después, la baja; las dos con la sesión")
    igual((api.documentos, leer_recordar(p), guardado_avisos(p)), ({}, propios, None), "Sin cuenta: la función se queda sin documento, aquí siguen los recordatorios (sin base) y las notificaciones quedan desactivadas")
    p.wait_for_timeout(2500)
    comprobar(toast(p)["visible"] and toast(p)["texto"] == advertencia, f"La advertencia no dura lo que un aviso con «Deshacer» (unos 4 s): {toast(p)}")
    sin_mas_llamadas(p, api, "Con las notificaciones desactivadas y sin cuenta")
    # Al reactivarlas: alta y, sin base, todo se sube otra vez (a un documento nuevo).
    boton_avisos(p).click()
    esperar_boton_avisos(p, "Desactivar notificaciones")
    esperar_envios(p, api, 2)
    base = esperar_base(p, "al reactivarlas se suben otra vez", nacido="n2", v=1)
    igual((len(api.de("alta")), api.envios()[1]["cuerpo"], api.de_la_sesion("anonimo-1")["cosas"], base), (1, parche(0, enviadas), enviadas, base_recordada("anonimo-1", "n2", 1, enviadas)),
          "Al reactivarlas: un alta y todos los recordatorios de nuevo")
    p.context.close()

    # Sin nada pendiente (lo único con recordatorio ya está hecho): el aviso de siempre; la función olvida igual.
    api = ApiRecordarFalsa()
    p = desactivar(api, "sin nada pendiente", texto="Notificaciones desactivadas", datos=estado_recordar([recordada(2, "Llamar al dentista", hecha=True), recordada(1, "Sacar la basura", hecha=True)]))
    igual(([llamada[0] for llamada in api.llamadas if llamada[0] in ("olvidar", "baja")], api.documentos, leer_recordar(p)["base"]), (["olvidar", "baja"], {}, None), "Sin nada pendiente: se olvida igual")
    p.context.close()

    # Con un envío a medias (la red va lenta) al desactivarlas: cuando por fin llega, la función ya no tiene el documento, la app se lo
    # sube entero (no sabe hacer otra cosa con una ronda empezada) y, al acabar esa ronda, lo olvida otra vez. Allí no queda nada.
    e.tolerar += CHOQUE_DE_VERSIONES
    api = ApiRecordarFalsa()
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")]), propios)
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    esperar_base(p, "[envío a medias] los recordatorios están subidos", v=1)
    api.retener.add("recordar")
    check_de(fila_recordada(p, "Sacar la basura")).tap()
    esperar_envios(p, api, 2)
    p.get_by_role("button", name="Volver", exact=True).tap()
    esperar_vista(p, "lista")
    p.wait_for_timeout(400)
    volver(p)
    p.wait_for_timeout(400)
    abrir_ajustes(p)
    p.wait_for_timeout(400)
    boton_avisos(p).click()
    esperar_boton_avisos(p, "Activar notificaciones")
    esperar_toast(p, advertencia)
    esperar_llamadas(p, api, "baja", 1)
    igual(([q["accion"] for q in api.peticiones[3:]], len(api.retenidas), api.documentos), (["olvidar", "baja"], 1, {}), "[envío a medias] Al desactivarlas: olvidar y baja, con el envío aún por el camino")
    api.soltar()
    esperar_llamadas(p, api, "olvidar", 2)
    sin_mas_llamadas(p, api, "[envío a medias] Olvidado por segunda vez")
    igual(([(q["accion"], q["metodo"], q["estado"]) for q in api.peticiones[2:]], api.documentos, leer_recordar(p)),
          ([("recordar", "POST", 409), ("olvidar", "POST", 200), ("baja", "POST", 200), ("recordar", "POST", 200), ("olvidar", "POST", 200)], {}, propios),
          "[envío a medias] El envío retenido choca (ya no hay documento), se repite entero y, al acabar, se olvida de nuevo: en la función no queda nada y aquí no queda base")
    p.context.close()

    # Con cuenta de Google: solo se da de baja este dispositivo. Los recordatorios siguen en la función y aquí sigue la base.
    api = ApiRecordarFalsa()
    p = desactivar(api, "con cuenta", usuario="u1")
    p.wait_for_timeout(600)
    igual(([llamada for llamada in api.llamadas if llamada[0] in ("olvidar", "baja")], api.de_la_sesion("u1")["cosas"], leer_recordar(p)["base"]),
          ([("baja", "Bearer token-u1", {"endpoint": ENDPOINT_PRUEBA + "E1"})], enviadas, base_recordada("u1", "n1", 1, enviadas)), "Con cuenta: ni se pide olvidar ni se olvida la base")


@prueba("recordatorios sin conexión con la función: si no se le puede preguntar o enviar (sin red, o ella falla), la nota de Recordar lo dice («Sin conexión: tus recordatorios empezarán a avisar cuando vuelva.»), lo de aquí sigue guardado y no se insiste sin fin; se reintenta solo al volver la conexión, al volver a la app y al abrirla, y entonces viaja junto todo lo que cambió mientras tanto; lo mismo si lo que se quedó a medias fue el alta del dispositivo al guardar el primer recordatorio")
def t_recordar_nube_sin_conexion(e):
    e.tolerar += [r"^Failed to load resource: net::ERR_FAILED$", r"^https://cosas\.test/api/avisos/recordar \(net::ERR_FAILED\)$",
                  r"^500 https://cosas\.test/api/avisos/(recordar|alta)$", r"status of 500 \(Internal Server Error\)$"]  # La red cortada y el 500 de la función de mentira.
    api = ApiRecordarFalsa()
    diario = recordatorio()
    dentista, basura = recordada_enviada("Llamar al dentista"), recordada_enviada("Sacar la basura")
    propios = recordar_guardado(cosas={"id-2": diario})
    api.cortar.add("recordar")
    p = abrir_con_recordar(dispositivo_con_recordar(e, api), e, estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura")]), propios)
    esperar_vista(p, "grupo")
    esperar_nota(p, "sin-conexion")
    sin_mas_llamadas(p, api, "Tras fallar la primera pregunta")
    igual(([(q["metodo"], q["estado"]) for q in api.peticiones], leer_recordar(p)), ([("GET", None)], propios), "Sin red al abrir: una pregunta que no llega, y lo de aquí como estaba")
    # Vuelve la conexión: se pregunta, se sube y la nota se va.
    api.cortar.clear()
    p.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_envios(p, api, 1)
    esperar_nota(p, None)
    igual((api.envios()[0]["cuerpo"], api.envios()[0]["estado"]), (parche(0, {"id-2": dentista}), 200), "Al volver la conexión")
    # Otra vez sin red: lo que se cambia queda guardado aquí, la nota vuelve, y cada cambio lo intenta una vez (sin bucle).
    api.cortar.add("recordar")
    abrir_hoja_recordar(p, "Sacar la basura")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    esperar_envios(p, api, 2)
    esperar_nota(p, "sin-conexion")
    check_de(fila_recordada(p, "Llamar al dentista")).tap()
    esperar_envios(p, api, 3)
    sin_mas_llamadas(p, api, "Sin red, tras intentar cada cambio")
    igual(([q["estado"] for q in api.envios()], leer_recordar(p)["cosas"], leer_recordar(p)["base"]["v"], nota_recordar(p)["texto"]), ([200, None, None], {"id-2": diario, "id-1": diario}, 1, NOTAS_RECORDAR["sin-conexion"]),
          "Sin red: los envíos no llegan, los recordatorios siguen puestos aquí y la base no avanza")
    # Al volver a la app (pasa a verse otra vez): se reintenta sin esperar, y los dos cambios viajan juntos.
    api.cortar.clear()
    p.evaluate("() => { document.dispatchEvent(new Event('visibilitychange')); }")
    esperar_envios(p, api, 4)
    esperar_nota(p, None)
    igual((api.envios()[3]["cuerpo"], api.envios()[3]["estado"], len(api.lecturas())), (parche(1, {"id-1": basura, "id-2": recordada_enviada("Llamar al dentista", hecha=True)}), 200, 3),
          "Al volver a la app: se pregunta y se envía, de una vez, lo que cambió sin red")
    # La función falla (500): lo mismo. Y al abrir la app de nuevo, se sube.
    api.fallar.add("recordar")
    check_de(fila_recordada(p, "Llamar al dentista")).tap()
    esperar_envios(p, api, 5)
    esperar_nota(p, "sin-conexion")
    sin_mas_llamadas(p, api, "Tras un fallo de la función")
    api.fallar.clear()
    p.reload()
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 6)
    esperar_nota(p, None)
    igual(([q["estado"] for q in api.envios()[4:]], api.envios()[5]["cuerpo"], api.de_la_sesion("anonimo-1")["cosas"]), ([500, 200], parche(2, {"id-2": dentista}), {"id-2": dentista, "id-1": basura}),
          "Tras el fallo de la función: al abrir la app se sube lo que quedó pendiente")
    p.context.close()

    # Sin notificaciones aún: «Guardar» da el permiso, pero el alta de este dispositivo falla. No hay nada que pulsar: la nota dice que
    # no hay conexión (sin botón), queda apuntado que el alta está a medias y, al volver la conexión, se termina sola y se sube todo.
    api = ApiRecordarFalsa()
    api.fallar.add("alta")
    p = abrir_con_recordar(dispositivo_con_recordar(e, api, endpoint=None), e, estado_recordar([recordada(1, "Sacar la basura")]), sesion=None)
    esperar_vista(p, "grupo")
    abrir_hoja_recordar(p, "Sacar la basura")
    guardar_hoja_recordar(p)
    esperar_toast(p, "Recordatorio guardado")
    esperar_nota(p, "sin-conexion")
    igual(([(q["accion"], q["estado"]) for q in api.peticiones], leer_recordar(p), guardado_avisos(p), toast(p)["texto"]),
          ([("clave", 200), ("alta", 500)], {"general": None, "cosas": {"id-1": diario}, "base": None, "activar": True}, None, "Recordatorio guardado"),
          "Con el alta fallida: no se sube nada, el recordatorio queda guardado con el alta apuntada como a medias, y solo se dijo «Recordatorio guardado»")
    api.fallar.clear()
    p.evaluate(JS_VUELVE_LA_CONEXION)
    esperar_envios(p, api, 1)
    esperar_nota(p, None)
    esperar_base(p, "con el alta terminada, el recordatorio se sube", v=1)
    igual(([(q["accion"], q["estado"]) for q in api.peticiones[2:]], api.envios()[0]["cuerpo"], leer_recordar(p)["activar"], guardado_avisos(p)["uid"]),
          ([("alta", 200), ("recordar", 200), ("recordar", 200)], parche(0, {"id-1": recordada_enviada("Sacar la basura")}), False, "anonimo-1"),
          "Al volver la conexión: el alta y, después, la pregunta y el envío; ya no queda nada a medias")


@prueba("recordatorios al entrar con otra cuenta: la base es de la sesión con la que se guardó, y con otra no vale: a la función se le pregunta por la sesión nueva y se le sube todo lo de aquí (lo de la anterior, allí, ni se toca); y este dispositivo, que aún avisaba a la anterior, se da de alta con la nueva")
def t_recordar_nube_otra_sesion(e):
    api = ApiRecordarFalsa()
    diario = recordatorio()
    enviadas = {"id-2": recordada_enviada("Llamar al dentista"), "id-1": recordada_enviada("Sacar la basura", hecha=True)}
    # Lo que dejó la sesión anónima con la que se activaron las notificaciones: su documento en la función y, aquí, su base.
    de_la_anonima = {"nacido": "de-la-anonima", "v": 4, "zona": MADRID, "general": None, "cosas": enviadas}
    api.documentos["anonimo-1"] = json.loads(json.dumps(de_la_anonima))
    propios = {**recordar_guardado(cosas={"id-2": diario, "id-1": diario}), "base": base_recordada("anonimo-1", "de-la-anonima", 4, enviadas)}
    # Ahora se ha entrado con Google (cuenta/ dejó su cuenta y la sesión es la suya).
    p = abrir_con_recordar(dispositivo_con_recordar(e, api, usuario="u1"), e, estado_recordar([recordada(2, "Llamar al dentista"), recordada(1, "Sacar la basura", hecha=True)]), propios, sesion="anonimo-1", cuenta=True)
    esperar_vista(p, "grupo")
    esperar_envios(p, api, 1)
    esperar_llamadas(p, api, "alta", 1)
    esperar(p, "(clave) => JSON.parse(localStorage.getItem(clave)).uid === 'u1'", arg=CLAVE_AVISOS, que="las notificaciones de este dispositivo pasan a la sesión nueva")
    sesion = "Bearer token-u1"
    igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization"), q["cuerpo"], q["estado"]) for q in api.peticiones],
          [("recordar", "GET", sesion, None, 200), ("recordar", "POST", sesion, parche(0, enviadas), 200), ("clave", "GET", None, None, 200),
           ("alta", "POST", sesion, {"suscripcion": {"endpoint": ENDPOINT_PRUEBA + "E1", "expirationTime": None, "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"}}}, 200)],
          "Con otra cuenta: se pregunta y se sube todo con la sesión nueva y, después, el dispositivo se da de alta con ella")
    igual((api.de_la_sesion("u1")["cosas"], api.de_la_sesion("anonimo-1"), leer_recordar(p)["base"], guardado_avisos(p)["endpoint"]),
          (enviadas, de_la_anonima, base_recordada("u1", "n1", 1, enviadas), ENDPOINT_PRUEBA + "E1"), "La función guarda lo de la sesión nueva sin tocar lo de la anterior, y la base pasa a ser de la nueva")
    sin_mas_llamadas(p, api, "Con la sesión nueva al día")


@prueba("recordatorios: cómo se junta lo de aquí, la base y lo de la función (reconciliarRecordatorios), caso a caso: qué se envía (poner, quitar y «general» solo si cambia), qué se trae y qué base queda; sin base nada de aquí se da por quitado y lo hecho lo es si lo dice cualquiera; con ella gana quien cambió desde entonces y, si cambiaron los dos, este dispositivo, campo a campo", una_vez=True)
def t_recordar_reconciliar(e):
    p = e.pagina("icons/favicon-32.png")
    r = p.evaluate("""async () => {
      const n = await import('/nube.js');
      const lado = (o) => ({ general: o.general || null, cosas: new Map(Object.entries(o.cosas || {})) });
      const R1 = { hora: '09:00', frec: 'dia' }, R2 = { hora: '18:00', frec: 'dia' }, R3 = { hora: '21:30', frec: 'semana', dia: 2 };
      const c = (r, hecha = false, texto = 'A') => ({ texto, hecha, r });
      // «conocidas»: las cosas que están en Recordar en este dispositivo (por defecto, las que aquí tienen recordatorio).
      const caso = (local, base, servidor, conocidas) => {
        const r = n.reconciliarRecordatorios(lado(local), base ? lado(base) : null, lado(servidor), new Set(conocidas || Object.keys(local.cosas || {})));
        return { parche: r.parche, traer: { general: r.aplicar.general === undefined ? 'igual' : r.aplicar.general, cosas: r.aplicar.cosas }, base: { general: r.base.general, cosas: Object.fromEntries(r.base.cosas) } };
      };
      const con = (cosas, general) => ({ cosas, general });
      return {
        // Sin base (la primera vez, o cuando la función ya tiene otro documento).
        union: caso(con({ a: c(R1) }), null, con({ b: c(R2, false, 'B'), z: c(R3, false, 'Z') }), ['a', 'b']),
        igualesSinBase: caso(con({ a: c(R1) }), null, con({ a: c(R1) })),
        hechaAlli: caso(con({ a: c(R1) }), null, con({ a: c(R2, true) })),
        hechaAqui: caso(con({ a: c(R1, true) }), null, con({ a: c(R1) })),
        soloAqui: caso(con({ a: c(R1) }), null, con({})),
        generalDeAlli: caso(con({}), null, con({}, R2)),
        generalDeLosDos: caso(con({}, R1), null, con({}, R2)),
        // Con base.
        cambiaAqui: caso(con({ a: c(R2) }), con({ a: c(R1) }), con({ a: c(R1) })),
        cambiaAlli: caso(con({ a: c(R1) }), con({ a: c(R1) }), con({ a: c(R2) })),
        cambianLosDos: caso(con({ a: c(R2) }), con({ a: c(R1) }), con({ a: c(R3) })),
        hechoEnElAviso: caso(con({ a: c(R1) }), con({ a: c(R1) }), con({ a: c(R1, true) })),
        hechoEnElAvisoYHoraAqui: caso(con({ a: c(R2) }), con({ a: c(R1) }), con({ a: c(R1, true) })),
        desmarcadaAqui: caso(con({ a: c(R1) }), con({ a: c(R1, true) }), con({ a: c(R1, true) })),
        desmarcadaAlli: caso(con({ a: c(R1, true) }), con({ a: c(R1, true) }), con({ a: c(R1) })),
        quitadaAqui: caso(con({}), con({ a: c(R1) }), con({ a: c(R1) }), ['a']),
        quitadaAquiYCambiadaAlli: caso(con({}), con({ a: c(R1) }), con({ a: c(R2, true) }), ['a']),
        quitadaAlli: caso(con({ a: c(R1) }), con({ a: c(R1) }), con({})),
        quitadaAlliYCambiadaAqui: caso(con({ a: c(R2) }), con({ a: c(R1) }), con({})),
        quitadaEnLosDos: caso(con({}), con({ a: c(R1) }), con({}), ['a']),
        nuevaAqui: caso(con({ a: c(R1), b: c(R2, false, 'B') }), con({ a: c(R1) }), con({ a: c(R1) })),
        puestaEnOtroDispositivo: caso(con({ a: c(R1) }), con({ a: c(R1) }), con({ a: c(R1), b: c(R2, false, 'B') }), ['a', 'b']),
        ajena: caso(con({ a: c(R1) }), con({ a: c(R1) }), con({ a: c(R1), z: c(R3, false, 'Z') })),
        generalAqui: caso(con({}, R2), con({}, R1), con({}, R1)),
        generalAlli: caso(con({}, R1), con({}, R1), con({}, R2)),
        generalEnLosDos: caso(con({}, R2), con({}, R1), con({}, R3)),
        generalQuitadoAqui: caso(con({}), con({}, R1), con({}, R1)),
        generalQuitadoAlli: caso(con({}, R1), con({}, R1), con({})),
        todoALaVez: caso(con({ a: c(R2), b: c(R1, false, 'B'), d: c(R1, false, 'D') }, R1), con({ a: c(R1), b: c(R1, false, 'B'), e: c(R1, false, 'E') }, R1),
                         con({ a: c(R1, true), b: c(R3, false, 'B'), e: c(R1, false, 'E'), f: c(R2, false, 'F') }, R3), ['a', 'b', 'd', 'f']),
      };
    }""")
    r1, r2, r3 = recordatorio(), recordatorio("18:00"), recordatorio("21:30", "semana", dia=2)

    def c(r, hecha=False, texto="A"):
        return {"texto": texto, "hecha": hecha, "r": r}

    def resultado(poner=None, quitar=None, traer=(), base=None, general_enviado="igual", general_traido="igual", general=None):
        """Lo esperado de un caso: qué viaja (None: nada), qué se trae y la base que queda."""
        viaja = None if poner is None and quitar is None and general_enviado == "igual" else {**({} if general_enviado == "igual" else {"general": general_enviado}), "poner": poner or {}, "quitar": quitar or []}
        return {"parche": viaja, "traer": {"general": general_traido, "cosas": [list(par) for par in traer]}, "base": {"general": general, "cosas": base or {}}}

    esperado = {
        "union": (resultado(poner={"a": c(r1)}, traer=[("b", c(r2, texto="B"))], base={"a": c(r1), "b": c(r2, texto="B")}), "Sin base: lo de aquí se sube, lo de allí (de una cosa que está aquí) se trae y lo ajeno ni se toca"),
        "igualesSinBase": (resultado(base={"a": c(r1)}), "Sin base e iguales: solo queda apuntada la base"),
        "hechaAlli": (resultado(poner={"a": c(r1, True)}, traer=[("a", c(r1, True))], base={"a": c(r1, True)}), "Sin base: gana el recordatorio de aquí, pero lo hecho allí es hecho"),
        "hechaAqui": (resultado(poner={"a": c(r1, True)}, base={"a": c(r1, True)}), "Sin base: lo hecho aquí es hecho"),
        "soloAqui": (resultado(poner={"a": c(r1)}, base={"a": c(r1)}), "Sin base: lo que allí falta no se quita aquí, se sube"),
        "generalDeAlli": (resultado(general_traido=r2, general=r2), "Sin base y sin uno para todas aquí: se trae el de allí"),
        "generalDeLosDos": (resultado(general_enviado=r1, general=r1), "Sin base y con uno para todas en los dos: el de aquí"),
        "cambiaAqui": (resultado(poner={"a": c(r2)}, base={"a": c(r2)}), "Cambiado solo aquí: se sube"),
        "cambiaAlli": (resultado(traer=[("a", c(r2))], base={"a": c(r2)}), "Cambiado solo allí: se trae"),
        "cambianLosDos": (resultado(poner={"a": c(r2)}, base={"a": c(r2)}), "Cambiado en los dos: gana este dispositivo"),
        "hechoEnElAviso": (resultado(traer=[("a", c(r1, True))], base={"a": c(r1, True)}), "«Hecho» en un aviso: se trae"),
        "hechoEnElAvisoYHoraAqui": (resultado(poner={"a": c(r2, True)}, traer=[("a", c(r2, True))], base={"a": c(r2, True)}), "«Hecho» en un aviso y otra hora aquí: campo a campo, las dos cosas"),
        "desmarcadaAqui": (resultado(poner={"a": c(r1)}, base={"a": c(r1)}), "Desmarcada aquí después de la base: gana a lo hecho que la función ya tenía"),
        "desmarcadaAlli": (resultado(traer=[("a", c(r1))], base={"a": c(r1)}), "Desmarcada en otro dispositivo: se trae"),
        "quitadaAqui": (resultado(quitar=["a"]), "Sin recordatorio aquí desde la base: se quita allí"),
        "quitadaAquiYCambiadaAlli": (resultado(quitar=["a"]), "Quitada aquí y cambiada allí: gana este dispositivo"),
        "quitadaAlli": (resultado(traer=[("a", None)]), "Quitada en otro dispositivo y sin cambios aquí: se quita aquí"),
        "quitadaAlliYCambiadaAqui": (resultado(poner={"a": c(r2)}, base={"a": c(r2)}), "Quitada allí y cambiada aquí: se conserva y se sube"),
        "quitadaEnLosDos": (resultado(), "Quitada en los dos: nada"),
        "nuevaAqui": (resultado(poner={"b": c(r2, texto="B")}, base={"a": c(r1), "b": c(r2, texto="B")}), "Una nueva aquí: solo viaja ella"),
        "puestaEnOtroDispositivo": (resultado(traer=[("b", c(r2, texto="B"))], base={"a": c(r1), "b": c(r2, texto="B")}), "Puesta en otro dispositivo a una cosa que está aquí: se trae"),
        "ajena": (resultado(base={"a": c(r1)}), "Lo que la función tiene de una cosa que aquí no está: ni se toca ni se apunta"),
        "generalAqui": (resultado(general_enviado=r2, general=r2), "El de todas, cambiado aquí: viaja"),
        "generalAlli": (resultado(general_traido=r2, general=r2), "El de todas, cambiado allí: se trae"),
        "generalEnLosDos": (resultado(general_enviado=r2, general=r2), "El de todas, cambiado en los dos: gana este dispositivo"),
        "generalQuitadoAqui": (resultado(general_enviado=None), "El de todas, quitado aquí: viaja como null"),
        "generalQuitadoAlli": (resultado(general_traido=None), "El de todas, quitado allí: se quita aquí"),
        "todoALaVez": (resultado(poner={"a": c(r2, True), "d": c(r1, texto="D")}, quitar=["e"], traer=[("a", c(r2, True)), ("b", c(r3, texto="B")), ("f", c(r2, texto="F"))],
                                 base={"a": c(r2, True), "b": c(r3, texto="B"), "d": c(r1, texto="D"), "f": c(r2, texto="F")}, general_traido=r3, general=r3), "Todo a la vez: cada cosa por su lado"),
    }
    igual(sorted(r), sorted(esperado), "Casos comprobados")
    for nombre, (debido, que) in esperado.items():
        igual(r[nombre], debido, que)


# ----------------------------------------------------------------------------
# Recordar y el service worker: los avisos de las cosas y sus respuestas, en Google Chrome
# ----------------------------------------------------------------------------

# Los avisos que el service worker tiene a la vista, por orden de etiqueta, con todo lo que se le pide a cada uno.
JS_AVISOS_A_LA_VISTA = r"""
async () => (await self.registration.getNotifications()).map((n) => ({
  titulo: n.title, cuerpo: n.body, etiqueta: n.tag, datos: n.data, icono: n.icon, insignia: n.badge,
  acciones: n.actions.map((a) => [a.action, a.title]), fijo: n.requireInteraction, calla: n.silent, repite: n.renotify,
})).sort((a, b) => (a.etiqueta < b.etiqueta ? -1 : 1))
"""
# Lo que hay apuntado en la caché que comparten el service worker y la app («tipo/id»); None si la caché no existe.
JS_APUNTADO = r"""
async () => {
  if (!(await caches.has('recordar-de-cosas'))) return null;
  const claves = await (await caches.open('recordar-de-cosas')).keys();
  return claves.map((clave) => decodeURIComponent(new URL(clave.url).pathname.split('/__recordar/')[1])).sort();
}
"""
# Un evento del sistema sobre un aviso (tocarlo, pulsar uno de sus botones, quitarlo de en medio) o la vuelta de la conexión
# («sync»), hecho a mano: se espera a todo lo que el service worker deja en marcha con waitUntil y se dice cómo acabó.
JS_EVENTO_DEL_SISTEMA = r"""
async ([tipo, etiqueta, accion]) => {
  let evento;
  if (tipo === 'sync') {
    evento = new SyncEvent('sync', { tag: etiqueta });
  } else {
    const [aviso] = await self.registration.getNotifications({ tag: etiqueta });
    if (!aviso) return null;
    evento = new NotificationEvent(tipo, { notification: aviso, action: accion });
  }
  const esperas = [];
  evento.waitUntil = (promesa) => { esperas.push(promesa); };
  self.dispatchEvent(evento);
  return (await Promise.allSettled(esperas)).map((espera) => espera.status);
}
"""
# En un toque hecho a mano, Chrome no deja traer una ventana al frente ni abrir otra (no hay gesto de nadie): el service
# worker lo intenta igual y aquí se apunta qué ventana quiso enfocar y qué dirección quiso abrir. Y lo mismo con lo que le
# pide al navegador para cuando vuelva la conexión (Background Sync), que el Chrome de las pruebas no concede.
JS_SIN_VENTANAS = r"""
() => {
  self.__enfocadas = [];
  self.__abiertas = [];
  self.__pedidas = [];
  WindowClient.prototype.focus = async function () { self.__enfocadas.push(this.url); return this; };
  Clients.prototype.openWindow = async (url) => { self.__abiertas.push(url); return null; };
  SyncManager.prototype.register = async function (etiqueta) { self.__pedidas.push(etiqueta); };
}
"""
# La página apunta lo que le dice el service worker.
JS_OIR_AL_SERVICE_WORKER = "window.__mensajes = []; if (navigator.serviceWorker) navigator.serviceWorker.addEventListener('message', (evento) => window.__mensajes.push(evento.data));"
ACCIONES_DE_RECORDATORIO = [["hecho", "Hecho"], ["mantener", "Mantener"]]


def abrir_chrome(e):
    """Google Chrome, el de verdad: el Chromium de las pruebas no enseña notificaciones. None (con una nota) si este equipo no lo tiene."""
    try:
        return e.pw.chromium.launch(channel="chrome")
    except Exception:
        print("         (sin Google Chrome en este equipo: no se puede comprobar)")
        return None


def contexto_de_chrome(e, chrome, origen, **opciones):
    """Un contexto de ese Chrome, como un móvil Android (allí los avisos llevan botones), con permiso para avisar en «origen»."""
    e.tolerar.append(r"^Banner not shown")  # Chrome lo dice al guardarse la instalación para el botón de Ajustes.
    descriptor = {k: v for k, v in e.pw.devices["Pixel 7"].items() if k != "default_browser_type"}
    contexto = chrome.new_context(**{**descriptor, "locale": "es-ES", **opciones})
    contexto.set_default_timeout(8000)
    contexto.grant_permissions(["notifications"], origin=origen.rstrip("/"))
    contexto.add_init_script(JS_OIR_AL_SERVICE_WORKER)
    # Chrome pide /favicon.ico para lo que no es una página (la imagen desde la que se siembra lo guardado): se le da el de la app.
    contexto.route("**/favicon.ico", lambda route: route.fulfill(status=200, content_type="image/png", body=(APP / "icons" / "favicon-32.png").read_bytes()))
    return contexto


class ServiceWorkerDeChrome:
    """El service worker de la app, al mando de «pagina»: se le entregan envíos push (por CDP, sin servicio push), se leen
    los avisos que enseña y lo que apunta, y se le hace llegar lo que el sistema le diría al tocar un aviso."""

    def __init__(self, contexto, pagina, origen):
        self.pagina, self.origen = pagina, origen.rstrip("/")
        esperar(pagina, "() => navigator.serviceWorker.controller !== null", ms=15000, que="el service worker toma el control")
        self.cdp = contexto.new_cdp_session(pagina)
        registros = []
        self.cdp.on("ServiceWorker.workerRegistrationUpdated", lambda evento: registros.extend(evento["registrations"]))
        self.cdp.send("ServiceWorker.enable")
        self.esperar(lambda: any(not r.get("isDeleted") for r in registros), "Chrome dice cuál es el registro del service worker")
        self.registro = next(r["registrationId"] for r in registros if not r.get("isDeleted"))
        self.sw = contexto.service_workers[0] if contexto.service_workers else contexto.wait_for_event("serviceworker")
        self.sw.evaluate(JS_SIN_VENTANAS)

    def esperar(self, condicion, que, ms=5000):
        """Espera a que se cumpla «condicion» (con Playwright esperando, lo que pide el service worker se atiende) y devuelve lo que dé."""
        fin = time.monotonic() + ms / 1000
        while True:
            valor = condicion()
            if valor:
                return valor
            if time.monotonic() > fin:
                raise Fallo(f"No se cumplió en {ms} ms: {que}")
            self.pagina.wait_for_timeout(50)

    def empujar(self, datos):
        bruto = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
        self.cdp.send("ServiceWorker.deliverPushMessage", {"origin": self.origen, "registrationId": self.registro, "data": bruto})

    def avisos(self):
        return self.sw.evaluate(JS_AVISOS_A_LA_VISTA)

    def etiquetas(self):
        return [aviso["etiqueta"] for aviso in self.avisos()]

    def esperar_avisos(self, etiquetas, que):
        """Espera a que los avisos a la vista sean exactamente los de esas etiquetas y los devuelve."""
        try:
            self.esperar(lambda: self.etiquetas() == sorted(etiquetas), que)
        except Fallo:
            raise Fallo(f"No se cumplió: {que} (avisos a la vista: {self.etiquetas()}; esperados: {sorted(etiquetas)})") from None
        return self.avisos()

    def apuntado(self):
        return self.sw.evaluate(JS_APUNTADO)

    def esperar_apuntado(self, esperado, que):
        try:
            self.esperar(lambda: self.apuntado() == esperado, que)
        except Fallo:
            raise Fallo(f"No se cumplió: {que} (apuntado: {self.apuntado()}; esperado: {esperado})") from None

    def tocar(self, etiqueta, accion=""):
        """Lo que hace el sistema al tocar ese aviso («accion» vacía) o uno de sus botones: avisa al service worker. Devuelve cómo acabó lo que dejó en marcha."""
        resultado = self.sw.evaluate(JS_EVENTO_DEL_SISTEMA, ["notificationclick", etiqueta, accion])
        comprobar(resultado is not None, f"No hay ningún aviso con la etiqueta «{etiqueta}» que tocar: {self.etiquetas()}")
        return resultado

    def descartar(self, etiqueta):
        """Quitar el aviso de en medio sin contestarlo (deslizarlo fuera)."""
        resultado = self.sw.evaluate(JS_EVENTO_DEL_SISTEMA, ["notificationclose", etiqueta, ""])
        comprobar(resultado is not None, f"No hay ningún aviso con la etiqueta «{etiqueta}» que descartar: {self.etiquetas()}")
        self.sw.evaluate("async (etiqueta) => { for (const aviso of await self.registration.getNotifications({ tag: etiqueta })) aviso.close(); }", etiqueta)

    def vuelve_la_conexion(self, etiqueta="recordar-hecho"):
        """Lo que hace el navegador cuando vuelve la conexión y el service worker le pidió que le avisara (Background Sync)."""
        return self.sw.evaluate(JS_EVENTO_DEL_SISTEMA, ["sync", etiqueta, ""])

    def ventanas(self):
        """Las ventanas que el service worker ha querido traer al frente y las direcciones que ha querido abrir, desde la última vez que se miró."""
        return self.sw.evaluate("() => [self.__enfocadas.splice(0), self.__abiertas.splice(0)]")

    def avisos_de_conexion(self):
        """Las veces que el service worker ha pedido al navegador que le avise cuando vuelva la conexión (con qué etiqueta), desde la última vez que se miró."""
        return self.sw.evaluate("() => self.__pedidas.splice(0)")

    def mensajes(self):
        """Lo que el service worker le ha dicho a la página desde que se cargó."""
        return self.pagina.evaluate("() => window.__mensajes")


def aviso_de_recordatorio(e, id, texto, uid="u1", ficha="", suena=False, origen=None):
    """El aviso de una cosa de Recordar, entero: lo que tiene que enseñar el service worker (con botones: Chrome los tiene)."""
    origen = origen or e.base
    return {"titulo": "Recordar", "cuerpo": texto, "etiqueta": f"recordar-{id}", "icono": origen + "icons/icon-192.png", "insignia": origen + "icons/aviso-96.png",
            "datos": {"tipo": "recordar", "id": id, "uid": uid, "ficha": ficha, "url": "./?recordada=" + re.sub(r"[^A-Za-z0-9\-_.!~*'()]", lambda letra: "".join(f"%{b:02X}" for b in letra.group().encode()), id) + "#grupo/recordar"},
            "acciones": ACCIONES_DE_RECORDATORIO, "fijo": True, "calla": not suena, "repite": suena}


def envio_de_recordatorios(cosas, uid="u1"):
    """Un envío push de la función de avisos con esas cosas ([(id, texto, ficha)]), como lo arma ella (ver netlify/lib/envio.mjs)."""
    varias = len(cosas) > 1
    return {"tipo": "recordar", "uid": uid, "cosas": [{"id": id, "texto": texto, "ficha": ficha} for id, texto, ficha in cosas], "titulo": "Recordar",
            "linea": f"«{cosas[0][1]}» y {len(cosas) - 1} más" if varias else cosas[0][1], "url": "/#grupo/recordar" if varias else f"/?recordada={cosas[0][0]}#grupo/recordar",
            "etiqueta": "recordar" if varias else f"recordar-{cosas[0][0]}"}


@prueba("service worker, recordatorios: un envío con varias cosas enseña un aviso por cosa (título «Recordar», de texto la cosa, su etiqueta, sus datos, «Hecho» y «Mantener», y no se va solo), solo suena el primero y lo que no se entiende se salta; cada aviso queda apuntado como pendiente de respuesta; repetido, sustituye al de antes y vuelve a sonar; donde los avisos no tienen botones, el texto dice que se toque; sin ninguna cosa que se entienda, el aviso de siempre con su título y su línea; y los avisos de las listas y el genérico siguen como estaban", una_vez=True)
def t_sw_recordatorios(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    try:
        contexto = contexto_de_chrome(e, chrome, e.base)
        s = ServiceWorkerDeChrome(contexto, e.pagina(contexto=contexto), e.base)
        igual(s.apuntado(), None, "Antes del primer aviso, la caché de los avisos")
        # Tres cosas que se entienden (una con un id que hay que escribir con cuidado en una dirección, otra sin ficha) entre varias que no.
        envio = envio_de_recordatorios([("a", "Llamar al dentista", "ficha-a"), ("b c/ñ?&#", "Sacar la basura", "ficha-b"), ("d", "Regar las plantas", "")])
        envio["cosas"][2].pop("ficha")
        envio["cosas"][1:1] = [{"id": "", "texto": "Sin id"}, {"id": "sin-texto"}, {"id": "texto-vacio", "texto": ""}, None, "suelta", {"id": 7, "texto": "Con un id que no es texto"}]
        s.empujar(envio)
        avisos = s.esperar_avisos(["recordar-a", "recordar-b c/ñ?&#", "recordar-d"], "un aviso por cada cosa que se entiende")
        igual(avisos, [aviso_de_recordatorio(e, "a", "Llamar al dentista", ficha="ficha-a", suena=True), aviso_de_recordatorio(e, "b c/ñ?&#", "Sacar la basura", ficha="ficha-b"),
                       aviso_de_recordatorio(e, "d", "Regar las plantas")], "Los avisos de un envío con tres cosas: solo suena el primero")
        igual(avisos[1]["datos"]["url"], "./?recordada=b%20c%2F%C3%B1%3F%26%23#grupo/recordar", "La dirección que abre el aviso de una cosa con un id difícil")
        s.esperar_apuntado(["avisadas/a", "avisadas/b c/ñ?&#", "avisadas/d"], "cada cosa avisada queda apuntada (falta su respuesta)")
        # Otra vez una de ellas (al día siguiente, o repetida por si no llegó): sustituye a su aviso de antes, con el texto de ahora, y suena.
        s.empujar(envio_de_recordatorios([("a", "Llamar al dentista (ya)", "ficha-a2")]))
        s.esperar(lambda: s.avisos()[0]["cuerpo"] == "Llamar al dentista (ya)", "el aviso repetido sustituye al de antes")
        igual(s.avisos(), [aviso_de_recordatorio(e, "a", "Llamar al dentista (ya)", ficha="ficha-a2", suena=True), avisos[1], avisos[2]], "Tras repetirse una cosa: sigue habiendo un aviso por cosa")
        # Donde los avisos no llevan botones (iPhone, iPad, Firefox): sin acciones, y el texto dice qué hacer.
        s.sw.evaluate("() => { self.__maxActions = Object.getOwnPropertyDescriptor(Notification, 'maxActions'); Object.defineProperty(Notification, 'maxActions', { configurable: true, get: () => 0 }); }")
        s.empujar(envio_de_recordatorios([("e", "Pagar la comunidad", "ficha-e")]))
        sin_botones = s.esperar_avisos(["recordar-a", "recordar-b c/ñ?&#", "recordar-d", "recordar-e"], "llega el aviso sin botones")[3]
        igual(sin_botones, {**aviso_de_recordatorio(e, "e", "Pagar la comunidad", ficha="ficha-e", suena=True), "cuerpo": "Pagar la comunidad\nToca para marcarla como hecha o mantenerla.", "acciones": []},
              "El aviso de una cosa donde no hay botones")
        s.sw.evaluate("() => { Object.defineProperty(Notification, 'maxActions', self.__maxActions); }")
        # Un envío de recordatorios sin ninguna cosa que se entienda: lo que trae para cualquier aviso (título, línea y enlace).
        s.empujar({**envio_de_recordatorios([("", "Sin id", "f")]), "linea": "Sin id", "url": "/#grupo/recordar", "etiqueta": "recordar"})
        generico = s.esperar_avisos(["recordar", "recordar-a", "recordar-b c/ñ?&#", "recordar-d", "recordar-e"], "un envío sin cosas legibles enseña el aviso de siempre")[0]
        igual((generico["titulo"], generico["cuerpo"], generico["datos"], generico["acciones"], generico["fijo"]), ("Recordar", "Sin id", {"url": "/#grupo/recordar", "lineas": ["Sin id"]}, [], False), "El aviso de un envío sin cosas legibles")
        igual(s.apuntado(), ["avisadas/a", "avisadas/b c/ñ?&#", "avisadas/d", "avisadas/e"], "Lo apuntado tras todos los recordatorios")
        # Los avisos de las listas se siguen juntando por lista, y lo ilegible sigue dando el genérico: nada de eso toca los recordatorios.
        viaje = {"titulo": "Cosas de Viaje", "etiqueta": "lista-viaje123", "url": "/de/viaje123"}
        s.empujar({**viaje, "linea": "Ana ha añadido «Billetes»"})
        s.empujar({**viaje, "linea": "Beto ha añadido «Maleta»"})
        s.empujar("esto no es JSON")
        todos = s.esperar_avisos(["cosas", "lista-viaje123", "recordar", "recordar-a", "recordar-b c/ñ?&#", "recordar-d", "recordar-e"], "llegan los avisos de siempre")
        s.esperar(lambda: s.avisos()[1]["cuerpo"].startswith("Beto"), "los dos avisos de la lista se juntan")
        igual([(a["titulo"], a["cuerpo"], a["datos"], a["acciones"], a["fijo"]) for a in s.avisos()[:2]],
              [("Cosas", "Hay cosas nuevas", {"url": "./", "lineas": ["Hay cosas nuevas"]}, [], False),
               ("Cosas de Viaje", "Beto ha añadido «Maleta»\nAna ha añadido «Billetes»", {"url": "/de/viaje123", "lineas": ["Beto ha añadido «Maleta»", "Ana ha añadido «Billetes»"]}, [], False)],
              "El aviso genérico y el de una lista, como siempre (sin botones y sin quedarse fijos)")
        igual(todos[3:], [aviso_de_recordatorio(e, "a", "Llamar al dentista (ya)", ficha="ficha-a2", suena=True), avisos[1], avisos[2], sin_botones], "Los recordatorios que ya estaban a la vista siguen igual")
        igual((s.mensajes(), s.ventanas()), ([], [[], []]), "Enseñar avisos no le dice nada a la página ni abre o enfoca ventanas")
    finally:
        chrome.close()


@prueba("service worker, «Hecho» en el aviso de una cosa: el aviso se cierra, a la función se le dice quién, qué cosa y con qué ficha (sin sesión) y la app abierta marca el check al momento, sin recargar; con la app cerrada queda apuntado y se marca al abrirla; si la función no responde (sin red, o falla) queda por decir y se le pide al navegador que avise cuando vuelva la conexión: se repite entonces y con el siguiente envío, y de esa cosa no se vuelve a avisar salvo que fuera lo único del envío; desmarcada entretanto en la app, ese «Hecho» ya no se dice; y si la función responde que la ficha no vale, no se insiste", una_vez=True)
def t_sw_recordar_hecho(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    try:
        contexto = contexto_de_chrome(e, chrome, e.base)
        dichos, funcion = [], {"responde": 200}

        def atender_hecho(route):
            peticion = route.request
            dichos.append((peticion.method, json.loads(peticion.post_data), peticion.headers.get("content-type"), peticion.headers.get("authorization"), bool(peticion.service_worker)))
            if funcion["responde"] is None:
                route.abort()
            else:
                route.fulfill(status=funcion["responde"], content_type="application/json", body='{"ok":true}' if funcion["responde"] == 200 else '{"error":"ficha"}')

        contexto.route("**/api/avisos/hecho", atender_hecho)
        datos = estado_recordar([recordada(4, "Llamar al dentista"), recordada(3, "Sacar la basura"), recordada(2, "Regar las plantas"), recordada(1, "Pagar la comunidad")])
        p = sembrar_recordar(e, datos, recordar_guardado(general=recordatorio()), contexto=contexto)
        esperar_vista(p, "grupo")
        s = ServiceWorkerDeChrome(contexto, p, e.base)

        def dicho(id):
            return ("POST", {"uid": "u1", "id": id, "ficha": f"ficha-{id}"}, "application/json", None, True)

        def hechas():
            return sorted(c["id"] for c in leer_estado(p)["cosas"] if c["hecha"])

        s.empujar(envio_de_recordatorios([("id-4", "Llamar al dentista", "ficha-id-4"), ("id-3", "Sacar la basura", "ficha-id-3"), ("id-2", "Regar las plantas", "ficha-id-2")]))
        s.esperar_avisos(["recordar-id-2", "recordar-id-3", "recordar-id-4"], "llegan los tres avisos")
        # Con la app abierta: el aviso se cierra, la función se entera y la fila queda marcada sin recargar nada.
        direccion = p.url
        igual(s.tocar("recordar-id-4", "hecho"), ["fulfilled"], "Lo que el service worker deja en marcha al pulsar «Hecho»")
        s.esperar_avisos(["recordar-id-2", "recordar-id-3"], "el aviso contestado se cierra")
        esperar_recordadas(p, "la app marca la cosa al momento", textos=["Sacar la basura", "Regar las plantas", "Pagar la comunidad", "Llamar al dentista"], hechas=[False, False, False, True])
        igual((dichos, s.mensajes(), p.url, hechas()), ([dicho("id-4")], [{"tipo": "recordar-hecho", "id": "id-4"}], direccion, ["id-4"]),
              "«Hecho» con la app abierta: lo que se le dice a la función (sin sesión) y a la app, que ni recarga ni cambia de sitio")
        s.esperar_apuntado(["avisadas/id-2", "avisadas/id-3"], "dicho a la función y a la app, de esa cosa no queda nada apuntado")
        igual(s.avisos_de_conexion(), [], "Dicho a la primera, no hay nada que repetir cuando vuelva la conexión")
        # Con la app cerrada: queda apuntado para ella, que lo marca en cuanto se abre.
        p.goto(e.base + "icons/favicon-32.png")
        igual(s.tocar("recordar-id-3", "hecho"), ["fulfilled"], "«Hecho» con la app cerrada")
        s.esperar_avisos(["recordar-id-2"], "el aviso contestado se cierra")
        igual((dichos[1:], s.apuntado()), ([dicho("id-3")], ["avisadas/id-2", "hechas/id-3"]), "Con la app cerrada: la función se entera y queda apuntado para la app")
        p.goto(e.base + "#grupo/recordar")
        esperar_recordadas(p, "al abrir la app, la cosa contestada queda marcada", textos=["Regar las plantas", "Pagar la comunidad", "Llamar al dentista", "Sacar la basura"], hechas=[False, False, True, True])
        s.esperar_apuntado(["avisadas/id-2"], "leído por la app, deja de estar apuntado")
        # Sin red: la app (abierta) lo marca igual, y para la función queda por decir.
        funcion["responde"] = None
        igual(s.tocar("recordar-id-2", "hecho"), ["fulfilled"], "«Hecho» sin red")
        esperar_recordadas(p, "sin red, la app marca la cosa igual", hechas=[False, True, True, True])
        s.esperar_apuntado(["por-decir/id-2"], "sin red, el «Hecho» queda por decir")
        igual((dichos[2:], s.avisos_de_conexion()), ([dicho("id-2")], ["recordar-hecho"]), "Sin red: se intentó decir una vez, y se le pide al navegador que avise cuando vuelva la conexión")
        # El siguiente envío trae esa cosa (la función no sabe que está hecha) y otra: se le repite el «Hecho» y solo se avisa de la otra.
        funcion["responde"] = 200
        s.empujar(envio_de_recordatorios([("id-2", "Regar las plantas", "ficha-id-2"), ("id-1", "Pagar la comunidad", "ficha-id-1")]))
        avisos = s.esperar_avisos(["recordar-id-1"], "solo se avisa de la que no estaba contestada")
        s.esperar_apuntado(["avisadas/id-1"], "dicho por fin, ya no queda por decir")
        igual((avisos, dichos[3:]), ([aviso_de_recordatorio(e, "id-1", "Pagar la comunidad", ficha="ficha-id-1", suena=True)], [dicho("id-2")]), "Con el siguiente envío: suena la única que se enseña y se repite el «Hecho» pendiente")
        # La función falla (500): queda por decir. Si el siguiente envío solo trae esa cosa, se enseña igual (un envío siempre enseña algo).
        funcion["responde"] = 500
        igual(s.tocar("recordar-id-1", "hecho"), ["fulfilled"], "«Hecho» con la función fallando")
        s.esperar_apuntado(["por-decir/id-1"], "con la función fallando, queda por decir")
        igual(s.avisos_de_conexion(), ["recordar-hecho"], "Con la función fallando, también se pide repetirlo")
        s.empujar(envio_de_recordatorios([("id-1", "Pagar la comunidad", "ficha-id-1")]))
        s.esperar_avisos(["recordar-id-1"], "si es lo único del envío, se enseña aunque esté contestada")
        s.esperar(lambda: len(dichos) == 6, "se intenta decir otra vez con ese envío")
        igual((dichos[4:], s.apuntado()), ([dicho("id-1")] * 2, ["avisadas/id-1", "por-decir/id-1"]), "Con la función fallando: dos intentos, y sigue por decir")
        # Vuelve la conexión (el navegador avisa, donde sabe): mientras falle, pide que se le vuelva a avisar; cuando responde, queda dicho.
        igual((s.vuelve_la_conexion("otra-cosa"), len(dichos)), ([], 6), "Un aviso de conexión que no es el de los «Hecho» pendientes")
        igual((s.vuelve_la_conexion(), len(dichos)), (["rejected"], 7), "Al volver la conexión con la función aún fallando: se intenta, y se pide otro intento")
        funcion["responde"] = 200
        igual((s.vuelve_la_conexion(), dichos[7:], s.apuntado()), (["fulfilled"], [dicho("id-1")], ["avisadas/id-1"]), "Al volver la conexión con la función en pie: dicho, y nada por decir")
        # Un «Hecho» que quedó por decir (sin red) y la cosa, desmarcada después en la app: ese «Hecho» ya no cuenta. No se le dirá a la
        # función cuando vuelva la red, y de la cosa se vuelve a avisar.
        funcion["responde"] = None
        s.tocar("recordar-id-1", "hecho")
        s.esperar_apuntado(["por-decir/id-1"], "sin red, queda por decir")
        igual(s.avisos_de_conexion(), ["recordar-hecho"], "Sin red otra vez, se pide repetirlo")
        check_de(fila_recordada(p, "Pagar la comunidad")).tap()
        esperar_recordadas(p, "la cosa vuelve a estar pendiente", textos=["Pagar la comunidad", "Llamar al dentista", "Sacar la basura", "Regar las plantas"], hechas=[False, True, True, True])
        s.esperar_apuntado([], "desmarcada en la app, el «Hecho» pendiente se olvida")
        funcion["responde"] = 200
        intentos = len(dichos)
        s.empujar(envio_de_recordatorios([("id-1", "Pagar la comunidad", "ficha-id-1")]))
        s.esperar_avisos(["recordar-id-1"], "de la desmarcada se vuelve a avisar")
        s.esperar_apuntado(["avisadas/id-1"], "el aviso nuevo queda apuntado")
        igual((intentos, len(dichos)), (9, 9), "Intentos de decir «Hecho»: el que no llegó, y ninguno más tras desmarcar la cosa")
        # La ficha ya no vale (403): no tiene arreglo, no se guarda para repetirlo.
        funcion["responde"] = 403
        igual(s.tocar("recordar-id-1", "hecho"), ["fulfilled"], "«Hecho» con una ficha que la función no admite")
        s.esperar_avisos([], "el último aviso se cierra")
        esperar_recordadas(p, "la app marca la cosa otra vez", hechas=[True, True, True, True])
        s.esperar_apuntado([], "con la ficha rechazada no queda nada por decir")
        igual((dichos[9:], hechas(), s.avisos_de_conexion()), ([dicho("id-1")], ["id-1", "id-2", "id-3", "id-4"], []), "Con la ficha rechazada: un solo intento, que no se pide repetir, y en la app todas quedan hechas")
        igual(s.ventanas(), [[], []], "Contestar con «Hecho» no enfoca ni abre ninguna ventana")
    finally:
        chrome.close()


@prueba("service worker, «Mantener» y tocar el aviso: «Mantener» (o quitar el aviso de en medio) solo lo cierra y deja de preguntar por él: ni llamada a la función, ni mensaje, ni cambio en la cosa; tocar el aviso con la app abierta la trae al frente y le pide que pregunte ahí mismo, sin navegar (la hoja «Hecho o Mantener» sale sobre lo que hubiera y lo que se escribía sigue en su campo); sin ventana de la app, la abre en ?recordada=ID#grupo/recordar; y tocar un aviso de una lista sigue abriendo su enlace", una_vez=True)
def t_sw_recordar_mantener_y_tocar(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    try:
        contexto = contexto_de_chrome(e, chrome, e.base)
        dichos = []
        contexto.route("**/api/avisos/hecho", lambda route: (dichos.append(route.request.post_data), route.fulfill(status=200, content_type="application/json", body='{"ok":true}')))
        datos = estado_recordar([recordada(3, "Llamar al dentista"), recordada(2, "Sacar la basura"), recordada(1, "Regar las plantas")])
        propios = recordar_guardado(general=recordatorio())
        p = sembrar_recordar(e, datos, propios, ruta="", contexto=contexto)
        esperar_vista(p, "inicio")
        s = ServiceWorkerDeChrome(contexto, p, e.base)
        guardado = p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE)
        s.empujar(envio_de_recordatorios([("id-3", "Llamar al dentista", "f3"), ("id-2", "Sacar la basura", "f2"), ("id-1", "Regar las plantas", "f1")]))
        s.esperar_avisos(["recordar-id-1", "recordar-id-2", "recordar-id-3"], "llegan los tres avisos")
        s.esperar_apuntado(["avisadas/id-1", "avisadas/id-2", "avisadas/id-3"], "los tres quedan apuntados")
        # «Mantener»: se cierra y ya no hay que preguntar por él. Nada más.
        igual(s.tocar("recordar-id-3", "mantener"), ["fulfilled"], "Lo que el service worker deja en marcha al pulsar «Mantener»")
        s.esperar_avisos(["recordar-id-1", "recordar-id-2"], "el aviso mantenido se cierra")
        s.esperar_apuntado(["avisadas/id-1", "avisadas/id-2"], "del mantenido ya no se pregunta")
        # Quitarlo de en medio sin contestar es lo mismo.
        s.descartar("recordar-id-2")
        s.esperar_apuntado(["avisadas/id-1"], "del descartado tampoco se pregunta")
        p.wait_for_timeout(300)
        igual((dichos, s.mensajes(), s.ventanas(), p.evaluate("(clave) => localStorage.getItem(clave)", CLAVE), leer_recordar(p), hoja_avisada(p)), ([], [], [[], []], guardado, propios, None),
              "Tras «Mantener» y descartar: ni llamadas a la función, ni mensajes a la app, ni ventanas, ni cambios en lo guardado, ni preguntas")
        # Tocar el aviso con la app abierta (en la pantalla principal, con algo a medio escribir): pregunta ahí mismo.
        campo(p).fill("A medio escribir")
        historial = p.evaluate("() => history.length")
        igual(s.tocar("recordar-id-1"), ["fulfilled"], "Lo que el service worker deja en marcha al tocar el aviso")
        h = esperar_hoja_avisada(p)
        igual((h["titulo"], h["cosas"], s.mensajes(), s.ventanas(), s.etiquetas()), ("Recordatorio", [["Regar las plantas", RESPUESTAS]], [{"tipo": "recordar-abrir", "id": "id-1"}], [[e.base], []], []),
              "Tocar el aviso con la app abierta: la hoja de esa cosa, el mensaje a la app, su ventana al frente (sin abrir otra) y el aviso cerrado")
        igual((vista_visible(p), p.url, p.evaluate("() => history.length") - historial, campo(p).input_value()), ("inicio", e.base, 0, "A medio escribir"), "La app no navega ni pierde lo que se escribía")
        responder(p, "Regar las plantas", "Hecho")
        esperar_toast(p, "Hecho")
        igual((next(c["hecha"] for c in leer_estado(p)["cosas"] if c["id"] == "id-1"), campo(p).input_value(), dichos), (True, "A medio escribir", []), "Contestado en la app: la cosa queda hecha (a la función se lo dirá la app, no el service worker)")
        # Sin ninguna ventana de la app (la que hay es otra página de este dominio): se abre en la pregunta de esa cosa.
        p.goto(e.base + "icons/favicon-32.png")
        s.empujar(envio_de_recordatorios([("id-2", "Sacar la basura", "f2")]))
        s.empujar(envio_de_recordatorios([("b c/ñ", "Otra", "fb")]))
        s.esperar_avisos(["recordar-b c/ñ", "recordar-id-2"], "llegan dos avisos más")
        s.tocar("recordar-id-2")
        s.tocar("recordar-b c/ñ")
        igual((s.ventanas(), s.etiquetas()), ([[], [e.base + "?recordada=id-2#grupo/recordar", e.base + "?recordada=b%20c%2F%C3%B1#grupo/recordar"]], []), "Tocar el aviso sin la app abierta: se abre en ?recordada=ID#grupo/recordar, y el aviso se cierra")
        igual(dichos, [], "Tocar un aviso no le dice nada a la función")
        # Los avisos de las listas siguen abriendo su enlace: en una ventana nueva si no hay ninguna de este dominio que llevar allí…
        s.empujar({"titulo": "Cosas de Viaje", "etiqueta": "lista-viaje123", "linea": "Ana ha añadido «Billetes»", "url": "/de/viaje123#grupo/g1"})
        s.esperar_avisos(["lista-viaje123"], "llega el aviso de una lista")
        p.goto("about:blank")
        s.tocar("lista-viaje123")
        igual((s.ventanas(), s.etiquetas()), ([[], [e.base + "de/viaje123#grupo/g1"]], []), "Tocar el aviso de una lista sin ventanas: se abre su enlace")
        # …y, si la hay, en ella.
        p.goto(e.base)
        esperar_vista(p, "inicio")
        s.empujar({"titulo": "Cosas", "etiqueta": "lista-aqui", "linea": "Algo", "url": "/#lista"})
        s.esperar_avisos(["lista-aqui"], "llega otro aviso")
        s.tocar("lista-aqui")
        p.wait_for_url(e.base + "#lista")
        esperar_vista(p, "lista")
        igual(s.ventanas(), [[e.base], []], "Tocar un aviso con una ventana abierta: se trae al frente y se lleva al enlace")
    finally:
        chrome.close()


# ----------------------------------------------------------------------------
# Recordar de punta a punta: la app y su service worker con la función de avisos de verdad (netlify/functions)
# ----------------------------------------------------------------------------

FUNCION_LOCAL = RAIZ / "pruebas" / "servidor" / "funcion-local.mjs"
OPCIONES_DE_RECORDATORIO = {"TTL": 6 * 60 * 60, "urgency": "high", "timeout": 5000}  # Con las que la función envía un recordatorio: caduca a las seis horas, con prisa y sin tema.
MAX_BYTES_DE_UN_ENVIO = 3000
# El Chrome de las pruebas no tiene servicio push con el que suscribirse: la suscripción (y solo ella) es de mentira, y sigue
# ahí al recargar. El permiso, el service worker y los avisos son los de verdad.
JS_SUSCRIPCION_FALSA = r"""(() => {
  const endpoint = '%s';
  const suscripcion = { endpoint, toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh: 'clave-p256dh', auth: 'clave-auth' } }),
                        unsubscribe: async () => { sessionStorage.removeItem('suscrito'); return true; } };
  PushManager.prototype.getSubscription = async () => (sessionStorage.getItem('suscrito') ? suscripcion : null);
  PushManager.prototype.subscribe = async () => { sessionStorage.setItem('suscrito', '1'); return suscripcion; };
})();"""


def instante(texto):
    """Los milisegundos de un instante UTC escrito «2027-01-30T23:35»: lo que entiende el reloj de la función local."""
    return calendar.timegm(time.strptime(texto, "%Y-%m-%dT%H:%M")) * 1000


class FuncionDeVerdad(ApiRecordarFalsa):
    """La función de avisos y la programada, las de verdad, en un proceso aparte (pruebas/servidor/funcion-local.mjs: de
    mentira solo tienen el almacén, Google y el servicio push, y su reloj es el que aquí se le diga). Apunta cada llamada
    como la de mentira, pero quien contesta es el código de netlify/functions. Hay que cerrarla al acabar."""

    def __init__(self):
        super().__init__()
        self.proceso = subprocess.Popen([str(NODE), "--experimental-test-module-mocks", "--no-warnings", str(FUNCION_LOCAL)], cwd=str(RAIZ),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        linea = self.proceso.stdout.readline()
        if not linea:
            error = self.proceso.stderr.read().decode("utf-8", "replace").strip()
            self.cerrar()
            raise Fallo(f"La función local no arranca (¿falta «npm install»? Hace falta Node 22 o posterior): {error}")
        self.direccion = f"http://127.0.0.1:{json.loads(linea)['puerto']}"

    def cerrar(self):
        """Apaga el proceso (cerrarle la entrada ya lo apaga; si no, se le mata)."""
        for flujo in (self.proceso.stdin, self.proceso.stdout, self.proceso.stderr):
            flujo.close()
        try:
            self.proceso.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proceso.kill()
            self.proceso.wait()

    def pedir(self, metodo, ruta, cuerpo=None, autorizacion=None):
        """Una petición al proceso: (estado, datos). «cuerpo» va tal cual si son bytes; si no, como JSON."""
        bruto = cuerpo if isinstance(cuerpo, bytes) or cuerpo is None else json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        cabeceras = {**({"Content-Type": "application/json"} if bruto is not None else {}), **({"Authorization": autorizacion} if autorizacion else {})}
        peticion = urllib.request.Request(self.direccion + ruta, data=bruto, method=metodo, headers=cabeceras)
        try:
            with urllib.request.urlopen(peticion, timeout=20) as respuesta:
                return respuesta.status, json.loads(respuesta.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def responder(self, accion, metodo, autorizacion, cuerpo, bruto=None):
        """Lo que contesta la función de verdad a lo que la app (o su service worker) le envía, tal cual lo envía."""
        return self.pedir(metodo, "/api/avisos/" + accion, bruto if bruto is not None else cuerpo, autorizacion)

    def mandar(self, metodo, ruta, cuerpo=None):
        """Una orden de las pruebas al proceso (/__pruebas/…): devuelve lo que conteste, o falla con su porqué."""
        estado, datos = self.pedir(metodo, "/__pruebas/" + ruta, cuerpo)
        comprobar(estado == 200, f"La función local no atiende «{metodo} {ruta}» ({estado}): {datos}")
        return datos

    def reloj(self, ahora):
        """Pone el reloj de la función en ese instante (un texto como el de «instante»)."""
        self.mandar("POST", f"reloj?ahora={instante(ahora)}")

    def pasada(self, ahora):
        """Pone el reloj en ese instante y ejecuta una vez la función programada. Devuelve lo que ha querido enviar
        ([{ endpoint, estado, aviso, opciones }], con «aviso» ya leído y «bruto», su texto tal cual salió) y lo que ha escrito en la consola."""
        resultado = self.mandar("POST", f"pasada?ahora={instante(ahora)}")
        return [{**intento, "aviso": json.loads(intento["aviso"]), "bruto": intento["aviso"]} for intento in resultado["intentos"]], resultado["consola"]

    def fallar_en(self, endpoint, estado):
        """Desde ahora, el servicio push rechaza con ese código lo que se envíe a ese dispositivo (None: vuelve a aceptarlo)."""
        self.mandar("POST", "fallar", {"endpoint": endpoint, "estado": estado})

    def almacen(self, prefijo=""):
        """Lo que la función tiene guardado bajo ese prefijo: { clave: valor }."""
        return self.mandar("GET", "almacen?prefijo=" + prefijo)

    def documento(self, uid):
        """El documento de los recordatorios de esa sesión, entero (con lo que la función guarda para sí), o None."""
        return self.almacen("recordar/").get(f"recordar/{uid}")

    def de_la_sesion(self, uid):
        """Lo que la función comparte con la app de esa sesión, leído del almacén (preguntárselo a ella la despertaría)."""
        documento = self.documento(uid)
        return {campo: documento[campo] for campo in SIN_DOCUMENTO} if documento else dict(SIN_DOCUMENTO)


def resumen_de_la_pasada(consola):
    """De lo que la función programada escribe en la consola, las cuentas con las que acaba: (sesiones atendidas, cosas avisadas,
    sesiones en pausa, documentos borrados)."""
    cuentas = [re.fullmatch(r"log: Recordatorios: (\d+) sesiones atendidas, (\d+) cosas avisadas, (\d+) sesiones en pausa, (\d+) documentos borrados", linea) for linea in consola]
    comprobar(consola and cuentas[-1] and not any(cuentas[:-1]), f"La función programada no acaba con la línea de sus cuentas (una sola): {consola}")
    return tuple(int(n) for n in cuentas[-1].groups())


def envio_esperado(uid, cosas):
    """El mensaje push de un recordatorio con esas cosas ([(id, texto)]) tal como lo pide el contrato; la ficha de cada una se comprueba aparte."""
    varias = len(cosas) > 1
    return {"tipo": "recordar", "uid": uid, "cosas": [{"id": id, "texto": texto} for id, texto in cosas], "titulo": "Recordar",
            "linea": f"«{cosas[0][1]}» y {len(cosas) - 1} más" if varias else cosas[0][1], "url": "/#grupo/recordar" if varias else f"/?recordada={cosas[0][0]}#grupo/recordar",
            "etiqueta": "recordar" if varias else f"recordar-{cosas[0][0]}"}


def comprobar_envio(intento, endpoint, uid, cosas, que):
    """Un envío de la función programada: a ese dispositivo, aceptado, con las opciones de un recordatorio y con el mensaje del contrato
    (y en cada cosa una ficha: 32 bytes en base64 para direcciones). Devuelve las fichas, por id."""
    aviso = intento["aviso"]
    fichas = {c.get("id"): c.get("ficha") for c in aviso.get("cosas", [])}
    sin_fichas = {**aviso, "cosas": [{k: v for k, v in c.items() if k != "ficha"} for c in aviso.get("cosas", [])]}
    igual((intento["endpoint"], intento["estado"], intento["opciones"], sin_fichas), (endpoint, 201, OPCIONES_DE_RECORDATORIO, envio_esperado(uid, cosas)), que)
    comprobar(all(isinstance(f, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", f) for f in fichas.values()) and len(set(fichas.values())) == len(cosas), f"{que}: cada cosa lleva su ficha: {fichas}")
    comprobar(len(intento["bruto"].encode("utf-8")) <= MAX_BYTES_DE_UN_ENVIO, f"{que}: el mensaje pasa de {MAX_BYTES_DE_UN_ENVIO} bytes")
    return fichas


def app_en_chrome_con_la_funcion(e, chrome, funcion, uid, endpoint):
    """La app publicada (https://cosas.test) en ese Chrome, con su service worker de verdad, hablando con «funcion» con la sesión
    «uid»: se abre, se activan las notificaciones en Ajustes (el alta llega a la función) y se entra en Recordar.
    Devuelve la página y su service worker."""
    contexto = contexto_de_chrome(e, chrome, ORIGEN_NUBE, timezone_id=MADRID)
    dispositivo(e, NubeFalsa(), usuario=uid, contexto=contexto)
    contexto.add_init_script(JS_SUSCRIPCION_FALSA % endpoint)
    contexto.route(ORIGEN_NUBE + "api/avisos/**", funcion.atender)
    p = e.vigilar(contexto.new_page())
    p.goto(ORIGEN_NUBE)
    esperar_vista(p, "inicio")
    s = ServiceWorkerDeChrome(contexto, p, ORIGEN_NUBE)
    abrir_ajustes(p)
    p.wait_for_timeout(400)  # Pasa la guarda del cambio de vista.
    boton_avisos(p).tap()
    esperar_boton_avisos(p, "Desactivar notificaciones")
    esperar_toast(p, "Notificaciones activadas")
    clave, alta = funcion.peticiones
    igual(((clave["accion"], clave["metodo"], clave["estado"]), (alta["accion"], alta["metodo"], alta["cabeceras"].get("authorization"), alta["cuerpo"], alta["estado"], alta["respuesta"])),
          (("clave", "GET", 200), ("alta", "POST", f"Bearer token-{uid}", {"suscripcion": {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"}}}, 200, {"ok": True})),
          "Activar las notificaciones: la clave de la función y el alta de este dispositivo, con la sesión")
    comprobar(re.fullmatch(r"[A-Za-z0-9_-]{87}", clave["respuesta"]["clave"]), f"La clave pública de la función no es una clave VAPID: {clave['respuesta']}")
    dispositivos = funcion.almacen(f"dispositivos/{uid}/")
    igual(([d["endpoint"] for d in dispositivos.values()], list(funcion.almacen("suscripciones/").values())), ([endpoint], [uid]), "Tras el alta, la función guarda el dispositivo y de quién es")
    volver(p)
    p.wait_for_timeout(400)
    abrir_lista(p)
    p.wait_for_timeout(400)
    abrir_recordar(p)
    return p, s


def apuntar_con_recordatorio(pagina, texto, hora, minuto):
    """Apunta una cosa en Recordar y le pone, en su hoja, un recordatorio de cada día a esa hora. Devuelve su id."""
    campo_en_grupo(pagina).fill(texto)
    campo_en_grupo(pagina).press("Enter")
    esperar_toast(pagina, "Guardado")
    pagina.wait_for_timeout(400)
    abrir_hoja_recordar(pagina, texto)
    elegir_hora(pagina, hora, minuto)
    guardar_hoja_recordar(pagina)
    esperar_toast(pagina, "Recordatorio guardado")
    return next(c["id"] for c in leer_estado(pagina)["cosas"] if c["texto"] == texto)


@prueba("de punta a punta con la función de verdad, «Hecho»: se activan las notificaciones (el alta llega a la función), se apunta una cosa en Recordar y se le pone un recordatorio en su hoja (el envío llega y la función lo agenda en la zona del móvil); la función programada, a esa hora, manda un solo mensaje a este dispositivo, como dice el contrato; entregado tal cual al service worker, enseña el aviso de la cosa; su «Hecho» llega a la función con la ficha del mensaje y la marca: al día siguiente ya no avisa; la app se pone al día sin reenviar nada, y otro dispositivo de la sesión ve el check al preguntar", una_vez=True)
def t_integracion_hecho(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    funcion = None
    try:
        funcion = FuncionDeVerdad()
        e.tolerar += CHOQUE_DE_VERSIONES  # El «Hecho» del aviso cambia el documento, y el siguiente envío de la app choca (y se arregla solo).
        uid, endpoint = "anonima1", ENDPOINT_PRUEBA + "chrome-1"
        funcion.reloj("2027-01-30T23:30")  # En Madrid, las 0:30 del domingo 31 de enero.
        p, s = app_en_chrome_con_la_funcion(e, chrome, funcion, uid, endpoint)
        id = apuntar_con_recordatorio(p, "Llamar al dentista", "00", "35")
        esperar_envios(p, funcion, 1)
        base = esperar_base(p, "la app guarda la base que le da la función", v=1)
        cosa_enviada = recordada_enviada("Llamar al dentista", recordatorio("00:35"))
        pregunta, envio = funcion.peticiones[2:]
        igual(((pregunta["accion"], pregunta["metodo"], pregunta["estado"], pregunta["respuesta"]), (envio["cabeceras"].get("authorization"), envio["cuerpo"], envio["estado"], envio["respuesta"]["v"], envio["respuesta"]["rechazadas"])),
              (("recordar", "GET", 200, SIN_DOCUMENTO), (f"Bearer token-{uid}", parche(0, {id: cosa_enviada}), 200, 1, [])), "La app pregunta y envía, y la función lo acepta")
        documento = funcion.documento(uid)
        igual(({campo: documento[campo] for campo in ("v", "zona", "general", "cosas", "prox", "agenda", "pausado")}, sorted(funcion.almacen("agenda/")), base),
              ({"v": 1, "zona": MADRID, "general": None, "cosas": {id: cosa_enviada}, "prox": {id: instante("2027-01-30T23:35")}, "agenda": "202701302335", "pausado": None},
               [f"agenda/202701302335/{uid}"], base_recordada(uid, documento["nacido"], 1, {id: cosa_enviada})),
              "Lo que guarda la función (las 0:35 de Madrid son las 23:35 UTC), su agenda y la base de la app")
        esperar_nota(p, None)

        # Antes de su hora, nada. A su hora, un mensaje para este dispositivo.
        igual(funcion.pasada("2027-01-30T23:30")[0], [], "Una pasada antes de la hora")
        intentos, consola = funcion.pasada("2027-01-30T23:35")
        igual((len(intentos), resumen_de_la_pasada(consola)), (1, (1, 1, 0, 0)), "La pasada de su hora: un envío, y sus cuentas")
        ficha = comprobar_envio(intentos[0], endpoint, uid, [(id, "Llamar al dentista")], "El envío del recordatorio")[id]
        igual((funcion.documento(uid)["prox"], funcion.documento(uid)["turno"], sorted(funcion.almacen("agenda/"))), ({id: instante("2027-01-31T23:35")}, {}, [f"agenda/202701312335/{uid}"]), "Entregado: el siguiente aviso queda para mañana a la misma hora")
        igual(funcion.pasada("2027-01-30T23:40")[0], [], "La pasada siguiente no lo repite")

        # El mensaje, tal cual salió, entregado al service worker: el aviso de la cosa, con sus botones.
        s.empujar(intentos[0]["bruto"])
        avisos = s.esperar_avisos([f"recordar-{id}"], "el service worker enseña el aviso")
        igual(avisos, [aviso_de_recordatorio(e, id, "Llamar al dentista", uid=uid, ficha=ficha, suena=True, origen=ORIGEN_NUBE)], "El aviso de la cosa")
        # «Hecho»: llega a la función (sin sesión, con la ficha), que la marca; la app, abierta, la marca al momento.
        antes = len(funcion.peticiones)
        s.tocar(f"recordar-{id}", "hecho")
        s.esperar_avisos([], "el aviso contestado se cierra")
        esperar_recordadas(p, "la app marca la cosa", textos=["Llamar al dentista"], hechas=[True])
        funcion.reloj("2027-01-30T23:41")
        base = esperar_base(p, "la app se pone al día con la función", v=2)
        hecho, choque = funcion.peticiones[antes:]
        hecha = recordada_enviada("Llamar al dentista", recordatorio("00:35"), hecha=True)
        igual(((hecho["accion"], hecho["metodo"], hecho["cabeceras"].get("authorization"), hecho["cuerpo"], hecho["estado"], hecho["respuesta"]), (choque["accion"], choque["metodo"], choque["cuerpo"], choque["estado"], choque["respuesta"]["v"])),
              (("hecho", "POST", None, {"uid": uid, "id": id, "ficha": ficha}, 200, {"ok": True}), ("recordar", "POST", parche(1, {id: hecha}), 409, 2)),
              "El «Hecho» del service worker llega a la función; y el envío de la app (que la daba por pendiente allí) choca con lo que ella ya sabe")
        igual((funcion.de_la_sesion(uid)["cosas"], funcion.documento(uid)["prox"], base, s.apuntado()), ({id: hecha}, {}, base_recordada(uid, documento["nacido"], 2, {id: hecha}), []),
              "Tras el «Hecho»: hecha en la función y sin más avisos; la app, al día sin reenviar; y nada apuntado por decir ni por preguntar")
        sin_mas_llamadas(p, funcion, "Con la app al día tras el «Hecho»")
        # Al día siguiente, a su hora: nada.
        intentos, consola = funcion.pasada("2027-01-31T23:35")
        igual((intentos, resumen_de_la_pasada(consola)[1:]), ([], (0, 0, 0)), "La pasada del día siguiente no avisa de una cosa hecha")

        # Otro dispositivo de la misma sesión, con la cosa aún pendiente y sin saber nada del recordatorio: al preguntar, lo ve puesto y hecho.
        pendiente = {**leer_estado(p), "cosas": [{**c, "hecha": False, "hechaEn": None} for c in leer_estado(p)["cosas"]]}
        otro = abrir_con_recordar(dispositivo_con_recordar(e, funcion, usuario=uid, endpoint=ENDPOINT_PRUEBA + "otro"), e, pendiente, sesion=uid, endpoint=ENDPOINT_PRUEBA + "otro")
        esperar_recordadas(otro, "el otro dispositivo trae el recordatorio y el «Hecho»", textos=["Llamar al dentista"], cuandos=["0:35 · Cada día"], hechas=[True])
        igual((leer_recordar(otro)["cosas"], leer_recordar(otro)["base"]["v"], funcion.peticiones[-1]["metodo"], funcion.de_la_sesion(uid)["v"]), ({id: recordatorio("00:35")}, 2, "GET", 2), "En el otro dispositivo: el recordatorio traído, sin enviar nada")
    finally:
        chrome.close()
        if funcion:
            funcion.cerrar()


@prueba("de punta a punta con la función de verdad, «Mantener»: el aviso se cierra y nadie le dice nada a la función, que al día siguiente avisa otra vez; tocado entonces el aviso y contestado «Hecho» en la app, es la app quien se lo cuenta (con su envío de siempre) y deja de avisar; desmarcada después, vuelve a avisar con el recordatorio que tenía; y al desactivar las notificaciones sin cuenta de Google la función olvida los recordatorios antes de dar de baja el dispositivo: no queda de ellos ni el documento", una_vez=True)
def t_integracion_mantener(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    funcion = None
    try:
        funcion = FuncionDeVerdad()
        uid, endpoint = "anonima2", ENDPOINT_PRUEBA + "chrome-2"
        cada_noche = recordatorio("00:35")
        funcion.reloj("2027-01-30T23:30")
        p, s = app_en_chrome_con_la_funcion(e, chrome, funcion, uid, endpoint)
        id = apuntar_con_recordatorio(p, "Sacar la basura", "00", "35")
        esperar_envios(p, funcion, 1)
        esperar_base(p, "el recordatorio está en la función", v=1)
        intentos, _ = funcion.pasada("2027-01-30T23:35")
        igual(len(intentos), 1, "Envíos a su hora")
        ficha = comprobar_envio(intentos[0], endpoint, uid, [(id, "Sacar la basura")], "El envío del primer día")[id]
        s.empujar(intentos[0]["bruto"])
        s.esperar_avisos([f"recordar-{id}"], "llega el aviso")
        # «Mantener»: se cierra. Ni la función ni la app se enteran de nada, y la cosa sigue con su recordatorio.
        antes = len(funcion.peticiones)
        s.tocar(f"recordar-{id}", "mantener")
        s.esperar_avisos([], "el aviso mantenido se cierra")
        s.esperar_apuntado([], "del mantenido no queda nada apuntado")
        p.wait_for_timeout(300)
        pendiente = recordada_enviada("Sacar la basura", cada_noche)
        igual((funcion.peticiones[antes:], funcion.de_la_sesion(uid)["cosas"], funcion.documento(uid)["prox"], [f["hecha"] for f in recordadas(p)]), ([], {id: pendiente}, {id: instante("2027-01-31T23:35")}, [False]),
              "Tras «Mantener»: ninguna llamada, la cosa sigue pendiente allí y aquí, y su aviso queda para mañana")
        # Entretanto, nada; al día siguiente a su hora, otra vez (con la misma ficha: sigue siendo esa cosa de esa sesión).
        igual(funcion.pasada("2027-01-31T12:00")[0], [], "Una pasada a otra hora")
        intentos, consola = funcion.pasada("2027-01-31T23:35")
        igual((len(intentos), resumen_de_la_pasada(consola)), (1, (1, 1, 0, 0)), "Al día siguiente a su hora se avisa otra vez")
        igual(comprobar_envio(intentos[0], endpoint, uid, [(id, "Sacar la basura")], "El envío del segundo día")[id], ficha, "La ficha del segundo día")
        # Esta vez se toca el aviso y se contesta «Hecho» en la app: se lo cuenta ella a la función, con su envío de siempre.
        s.empujar(intentos[0]["bruto"])
        s.esperar_avisos([f"recordar-{id}"], "llega el aviso del segundo día")
        s.tocar(f"recordar-{id}")
        igual(esperar_hoja_avisada(p)["cosas"], [["Sacar la basura", RESPUESTAS]], "La app pregunta por la cosa del aviso tocado")
        responder(p, "Sacar la basura", "Hecho")
        esperar_toast(p, "Hecho")
        envio = esperar_envios(p, funcion, 2)[1]
        esperar_base(p, "la app sube el «Hecho»", v=2)
        hecha = recordada_enviada("Sacar la basura", cada_noche, hecha=True)
        igual((envio["cuerpo"], envio["estado"], funcion.de("hecho"), funcion.de_la_sesion(uid)["cosas"], funcion.documento(uid)["prox"], s.etiquetas()), (parche(1, {id: hecha}), 200, [], {id: hecha}, {}, []),
              "Contestado en la app: viaja como un cambio más (no por «hecho»), la función la da por hecha y no le queda nada que avisar")
        igual(funcion.pasada("2027-02-01T23:35")[0], [], "Hecha, ya no se avisa")
        # Desmarcada en la app: vuelve a avisar, con el recordatorio que tenía, desde la próxima vez que toque.
        funcion.reloj("2027-02-01T23:40")
        p.wait_for_timeout(400)
        check_de(fila_recordada(p, "Sacar la basura")).tap()
        envio = esperar_envios(p, funcion, 3)[2]
        esperar_base(p, "la app sube que vuelve a estar pendiente", v=3)
        igual((envio["cuerpo"], envio["estado"], funcion.documento(uid)["prox"]), (parche(2, {id: pendiente}), 200, {id: instante("2027-02-02T23:35")}), "Desmarcada: pendiente otra vez en la función, con su hora")
        intentos, _ = funcion.pasada("2027-02-02T23:35")
        igual(len(intentos), 1, "Envíos tras desmarcarla")
        comprobar_envio(intentos[0], endpoint, uid, [(id, "Sacar la basura")], "El envío tras desmarcarla")
        # Desactivar las notificaciones (sin cuenta de Google): la función olvida los recordatorios y, después, el dispositivo.
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        p.wait_for_timeout(400)
        volver(p)
        p.wait_for_timeout(400)
        abrir_ajustes(p)
        p.wait_for_timeout(400)
        antes = len(funcion.peticiones)
        boton_avisos(p).tap()
        esperar_boton_avisos(p, "Activar notificaciones")
        esperar_toast(p, "Notificaciones desactivadas: Recordar no te avisará")
        esperar_llamadas(p, funcion, "baja", 1)
        igual([(q["accion"], q["metodo"], q["cabeceras"].get("authorization"), q["cuerpo"], q["estado"], q["respuesta"]) for q in funcion.peticiones[antes:]],
              [("olvidar", "POST", f"Bearer token-{uid}", {}, 200, {"ok": True}), ("baja", "POST", f"Bearer token-{uid}", {"endpoint": endpoint}, 200, {"ok": True})],
              "Al desactivarlas: la función olvida los recordatorios y da de baja el dispositivo, por ese orden")
        igual((funcion.almacen("recordar/"), funcion.almacen("dispositivos/"), funcion.almacen("suscripciones/"), leer_recordar(p)), ({}, {}, {}, recordar_guardado(cosas={id: cada_noche})),
              "En la función no queda ni el documento ni el dispositivo; aquí el recordatorio sigue puesto, sin base")
        intentos, consola = funcion.pasada("2027-02-03T23:35")
        igual((intentos, resumen_de_la_pasada(consola)[1:]), ([], (0, 0, 0)), "Olvidados, ya no se avisa de nada")
    finally:
        chrome.close()
        if funcion:
            funcion.cerrar()


@prueba("de punta a punta con la función de verdad, varias cosas a la vez y un envío que falla: tres cosas con el recordatorio de todas a la misma hora viajan en un solo mensaje; si el servicio push no lo acepta (503) no se da por avisado y una pasada posterior, cuando caduca el turno (no antes), lo envía otra vez; aceptado, no se repite, y el service worker enseña tres avisos; al día siguiente vuelven las tres, y tras un «Hecho» y un «Mantener», al otro, las dos que siguen pendientes", una_vez=True)
def t_integracion_varias_y_reintento(e):
    chrome = abrir_chrome(e)
    if not chrome:
        return
    funcion = None
    try:
        funcion = FuncionDeVerdad()
        e.tolerar += CHOQUE_DE_VERSIONES  # Tras el «Hecho» del aviso, el envío de la app choca con lo que la función ya sabe.
        uid, endpoint = "anonima3", ENDPOINT_PRUEBA + "chrome-3"
        cada_noche = recordatorio("00:35")
        funcion.reloj("2027-01-30T23:30")
        p, s = app_en_chrome_con_la_funcion(e, chrome, funcion, uid, endpoint)
        textos = ["Llamar al dentista", "Sacar la basura", "Regar las plantas"]
        for cuantas, texto in enumerate(textos, 1):
            campo_en_grupo(p).fill(texto)
            campo_en_grupo(p).press("Enter")
            esperar_recordadas(p, f"«{texto}» queda apuntada", textos=textos[:cuantas][::-1])
        ids = {c["texto"]: c["id"] for c in leer_estado(p)["cosas"]}
        de = {id: texto for texto, id in ids.items()}
        p.wait_for_timeout(400)
        abrir_hoja_recordar(p)
        elegir_hora(p, "00", "35")
        guardar_hoja_recordar(p)
        esperar_toast(p, "Recordatorio puesto a 3 cosas")
        envio = esperar_envios(p, funcion, 1)[0]
        esperar_base(p, "los tres recordatorios están en la función", v=1)
        igual((envio["cuerpo"], envio["estado"]), (parche(0, {ids[t]: recordada_enviada(t, cada_noche) for t in textos}, general=cada_noche), 200), "El envío de la app: el de todas y las tres cosas")

        def un_envio(intentos, estado, pendientes, que):
            """Un solo mensaje, con esas cosas (en el orden que sea) y con ese resultado. Devuelve sus fichas."""
            igual((len(intentos), intentos and intentos[0]["estado"], sorted(c["id"] for i in intentos[:1] for c in i["aviso"]["cosas"])), (1, estado, sorted(pendientes)), que)
            return comprobar_envio({**intentos[0], "estado": 201}, endpoint, uid, [(c["id"], de[c["id"]]) for c in intentos[0]["aviso"]["cosas"]], que)

        # A su hora, el servicio push no acepta el mensaje (503): se intentó una vez, con las tres, y no se da nada por avisado.
        funcion.fallar_en(endpoint, 503)
        intentos, consola = funcion.pasada("2027-01-30T23:35")
        un_envio(intentos, 503, ids.values(), "A su hora, con el servicio push fallando: un mensaje con las tres")
        documento = funcion.documento(uid)
        igual((resumen_de_la_pasada(consola), documento["prox"], {id: turno["de"] for id, turno in documento["turno"].items()}),
              ((1, 0, 0, 0), {id: instante("2027-01-30T23:35") for id in ids.values()}, {id: instante("2027-01-30T23:35") for id in ids.values()}),
              "Sin entregar: ninguna cosa avisada, su hora sin mover y el turno tomado")
        # Mientras dura el turno de esa pasada, otra no las toca (aunque el servicio push ya responda).
        funcion.fallar_en(endpoint, None)
        igual(funcion.pasada("2027-01-30T23:36")[0], [], "Una pasada con el turno de la anterior aún en vigor")
        # Caducado el turno, la siguiente lo envía otra vez. Y ya entregado, no se repite.
        intentos, consola = funcion.pasada("2027-01-30T23:40")
        fichas = un_envio(intentos, 201, ids.values(), "La pasada siguiente lo repite: un mensaje con las tres")
        igual((resumen_de_la_pasada(consola), funcion.documento(uid)["prox"], funcion.documento(uid)["turno"]), ((1, 3, 0, 0), {id: instante("2027-01-31T23:35") for id in ids.values()}, {}),
              "Entregado: las tres avisadas y su siguiente aviso, mañana")
        igual(funcion.pasada("2027-01-30T23:45")[0], [], "Entregado, no se repite")
        # El mensaje, entregado al service worker: un aviso por cosa, y solo suena el de la primera.
        primera = intentos[0]["aviso"]["cosas"][0]["id"]
        s.empujar(intentos[0]["bruto"])
        avisos = s.esperar_avisos([f"recordar-{id}" for id in ids.values()], "el service worker enseña los tres avisos")
        igual(avisos, [aviso_de_recordatorio(e, id, de[id], uid=uid, ficha=fichas[id], suena=id == primera, origen=ORIGEN_NUBE) for id in sorted(ids.values())], "Los tres avisos de un mensaje")
        # Se quitan de en medio sin contestar: al día siguiente, las tres otra vez. Entonces, «Hecho» en una y «Mantener» en otra.
        for id in ids.values():
            s.descartar(f"recordar-{id}")
        s.esperar_apuntado([], "descartados los tres avisos, no queda ninguno por contestar")
        intentos, _ = funcion.pasada("2027-01-31T23:35")
        un_envio(intentos, 201, ids.values(), "Al día siguiente, sin contestar ninguna")
        s.empujar(intentos[0]["bruto"])
        s.esperar_avisos([f"recordar-{id}" for id in ids.values()], "llegan los avisos del segundo día")
        s.esperar_apuntado(sorted(f"avisadas/{id}" for id in ids.values()), "los tres quedan apuntados")
        s.tocar(f"recordar-{ids['Llamar al dentista']}", "hecho")
        s.tocar(f"recordar-{ids['Sacar la basura']}", "mantener")
        s.esperar_avisos([f"recordar-{ids['Regar las plantas']}"], "queda el aviso sin contestar")
        esperar_recordadas(p, "la app marca la del «Hecho»", textos=["Regar las plantas", "Sacar la basura", "Llamar al dentista"], hechas=[False, False, True])
        esperar_base(p, "la app se pone al día con la función", v=2)
        igual(({id: c["hecha"] for id, c in funcion.de_la_sesion(uid)["cosas"].items()}, [(q["cuerpo"]["id"], q["estado"]) for q in funcion.peticiones if q["accion"] == "hecho"], [q["estado"] for q in funcion.envios()]),
              ({ids["Llamar al dentista"]: True, ids["Sacar la basura"]: False, ids["Regar las plantas"]: False}, [(ids["Llamar al dentista"], 200)], [200, 409]),
              "Tras contestar: en la función, hecha solo la del «Hecho»; y la app, que la subía como hecha, ve que ya lo estaba")
        intentos, _ = funcion.pasada("2027-02-01T23:35")
        un_envio(intentos, 201, [ids["Sacar la basura"], ids["Regar las plantas"]], "Al otro día: la mantenida y la que nadie contestó")
    finally:
        chrome.close()
        if funcion:
            funcion.cerrar()


@prueba("de punta a punta con la función de verdad, dos dispositivos de la misma cuenta: el recordatorio puesto en uno llega al otro; si los dos cambian algo a la vez, al segundo la función le dice que ha cambiado (409) y él lo junta y lo repite; si cambian lo mismo, queda lo del último; un «Hecho» pulsado en un aviso (con la ficha de un mensaje de verdad) marca el check en los dos; y al final los dos enseñan lo que la función guarda")
def t_integracion_dos_dispositivos(e):
    funcion = FuncionDeVerdad()
    try:
        e.tolerar += CHOQUE_DE_VERSIONES
        nube, uid = NubeFalsa(), "u1"
        movil, tableta = ENDPOINT_PRUEBA + "movil", ENDPOINT_PRUEBA + "tableta"
        diario, tarde, temprano, noche = recordatorio(), recordatorio("18:00"), recordatorio("07:30"), recordatorio("21:00")
        funcion.reloj("2027-01-30T11:00")  # En Madrid, las 12:00 del sábado.
        datos = estado_recordar([recordada(3, "Llamar al dentista"), recordada(2, "Sacar la basura"), recordada(1, "Regar las plantas")])
        a = abrir_con_recordar(dispositivo_con_recordar(e, funcion, usuario=uid, endpoint=movil, nube=nube), e, datos, recordar_guardado(cosas={"id-3": diario}), sesion=uid, endpoint=movil, cuenta=True)
        esperar_nube(a, "al-dia")
        esperar_envios(a, funcion, 1)
        esperar_base(a, "el móvil sube su recordatorio", v=1)
        b = abrir_con_recordar(dispositivo_con_recordar(e, funcion, usuario=uid, endpoint=tableta, nube=nube), e, sesion=uid, endpoint=tableta, cuenta=True)
        esperar_recordadas(b, "a la tableta le llegan las cosas (por la nube) y el recordatorio (por la función)", textos=["Llamar al dentista", "Sacar la basura", "Regar las plantas"],
                           cuandos=["9:00 · Cada día", "Poner recordatorio", "Poner recordatorio"])
        esperar_base(b, "la tableta se queda con lo de la función", v=1)

        def cambiar_hora(pagina, texto, hora, minuto="00"):
            abrir_hoja_recordar(pagina, texto)
            elegir_hora(pagina, hora, minuto)
            guardar_hoja_recordar(pagina)
            esperar_toast(pagina, "Recordatorio guardado")

        # Los dos cambian cosas distintas sin saber del otro: el segundo choca, junta y repite.
        cambiar_hora(a, "Llamar al dentista", "18")
        esperar_envios(a, funcion, 2)
        esperar_base(a, "el cambio del móvil se sube", v=2)
        cambiar_hora(b, "Sacar la basura", "07", "30")
        esperar_envios(b, funcion, 4)
        esperar_base(b, "el cambio de la tableta se sube, tras el choque", v=3)
        basura = {"id-2": recordada_enviada("Sacar la basura", temprano)}
        igual([(q["cuerpo"], q["estado"]) for q in funcion.envios()[1:]], [(parche(1, {"id-3": recordada_enviada("Llamar al dentista", tarde)}), 200), (parche(1, basura), 409), (parche(2, basura), 200)],
              "Dos cambios a la vez: el del móvil entra; el de la tableta choca y se repite sobre lo que hay, solo con lo suyo")
        igual(cuandos(b), {"Llamar al dentista": "18:00 · Cada día", "Sacar la basura": "7:30 · Cada día", "Regar las plantas": "Poner recordatorio"}, "La tableta, tras el choque, enseña también el cambio del móvil")
        a.evaluate(JS_VUELVE_LA_CONEXION)
        esperar_recordadas(a, "el móvil trae el cambio de la tableta", cuandos=["18:00 · Cada día", "7:30 · Cada día", "Poner recordatorio"])
        esperar_base(a, "el móvil se pone al día", v=3)
        # Los dos le ponen recordatorio a la misma cosa, cada uno el suyo: queda el del último que guarda, y el otro lo trae al preguntar.
        cambiar_hora(a, "Regar las plantas", "20")
        esperar_envios(a, funcion, 5)
        esperar_base(a, "el recordatorio del móvil se sube", v=4)
        cambiar_hora(b, "Regar las plantas", "21")
        esperar_envios(b, funcion, 7)
        esperar_base(b, "el de la tableta se sube, tras el choque", v=5)
        a.evaluate(JS_VUELVE_LA_CONEXION)
        esperar_recordadas(a, "el móvil trae el recordatorio de la tableta", cuandos=["18:00 · Cada día", "7:30 · Cada día", "21:00 · Cada día"])
        esperar_base(a, "el móvil se pone al día", v=5)
        cosas = {"id-3": recordada_enviada("Llamar al dentista", tarde), "id-2": recordada_enviada("Sacar la basura", temprano), "id-1": recordada_enviada("Regar las plantas", noche)}
        igual(([q["estado"] for q in funcion.envios()], funcion.de_la_sesion(uid)["cosas"], cuandos(a), cuandos(b), leer_recordar(a)["cosas"], leer_recordar(b)["cosas"]),
              ([200, 200, 409, 200, 200, 409, 200], cosas, cuandos(b), {"Llamar al dentista": "18:00 · Cada día", "Sacar la basura": "7:30 · Cada día", "Regar las plantas": "21:00 · Cada día"},
               {"id-3": tarde, "id-2": temprano, "id-1": noche}, {"id-3": tarde, "id-2": temprano, "id-1": noche}), "Los dos dispositivos y la función acaban con lo mismo")
        # El móvil se da de alta para recibir los avisos; a las 18:00 de Madrid le llega el del dentista, y alguien pulsa «Hecho» en él.
        igual(funcion.pedir("POST", "/api/avisos/alta", {"suscripcion": {"endpoint": movil, "expirationTime": None, "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"}}}, f"Bearer token-{uid}"), (200, {"ok": True}), "El alta del móvil")
        intentos, _ = funcion.pasada("2027-01-30T17:00")
        igual(len(intentos), 1, "Envíos a las 18:00 de Madrid")
        ficha = comprobar_envio(intentos[0], movil, uid, [("id-3", "Llamar al dentista")], "El envío al móvil")["id-3"]
        igual(funcion.pedir("POST", "/api/avisos/hecho", {"uid": uid, "id": "id-3", "ficha": ficha}), (200, {"ok": True}), "El «Hecho» del aviso, con la ficha del mensaje")
        b.evaluate(JS_VUELVE_LA_CONEXION)
        for pagina, cual in ((b, "la tableta"), (a, "el móvil")):
            esperar_recordadas(pagina, f"el «Hecho» del aviso llega a {cual}", textos=["Sacar la basura", "Regar las plantas", "Llamar al dentista"], hechas=[False, False, True])
        a.wait_for_timeout(1300)  # Al llegarle por la nube la cosa hecha, cada uno vuelve a preguntar: nadie tiene nada que enviar.
        igual((len(funcion.envios()), funcion.de_la_sesion(uid)["cosas"]["id-3"], funcion.de_la_sesion(uid)["v"], leer_recordar(a)["base"]["v"], leer_recordar(b)["base"]["v"]),
              (7, recordada_enviada("Llamar al dentista", tarde, hecha=True), 6, 6, 6), "Tras el «Hecho»: nadie reenvía nada y los dos quedan en la versión de la función")
    finally:
        funcion.cerrar()


@prueba("de punta a punta con la función de verdad, cien cosas de texto largo: lo que no cabe en un envío de la app (100000 bytes) viaja en dos seguidos y la función los admite; a su hora, la programada las reparte en mensajes de menos de 3000 bytes, con cada texto recortado a 300 letras, cuarenta cosas por pasada, hasta avisar de todas una sola vez")
def t_integracion_cien_cosas(e):
    funcion = FuncionDeVerdad()
    try:
        uid, endpoint = "u1", ENDPOINT_PRUEBA + "movil"
        cada_noche = recordatorio("00:35")
        funcion.reloj("2027-01-30T23:30")
        largas = [recordada(n, f"{n:03d} " + "ñ" * 496) for n in range(100, 0, -1)]
        todas = {c["id"]: recordada_enviada(c["texto"], cada_noche) for c in largas}
        p = abrir_con_recordar(dispositivo_con_recordar(e, funcion, usuario=uid, endpoint=endpoint), e, estado_recordar(largas), recordar_guardado(general=cada_noche), sesion=uid, endpoint=endpoint)
        esperar_vista(p, "grupo")
        envios = esperar_envios(p, funcion, 2)
        esperar_base(p, "las cien cosas están en la función", v=2)
        igual(([(q["cuerpo"]["siV"], q["estado"], q["respuesta"]["v"], q["respuesta"]["rechazadas"], q["bytes"] <= MAX_BYTES_DE_RECORDAR) for q in envios], [id for q in envios for id in q["cuerpo"]["poner"]], funcion.de_la_sesion(uid)["cosas"]),
              ([(0, 200, 1, [], True), (1, 200, 2, [], True)], list(todas), todas), "Dos envíos, cada uno dentro de lo que la función admite, y ella se queda con las cien cosas")
        esperar_nota(p, None)
        # El dispositivo se da de alta y llega la hora de las cien: cuarenta por pasada, en mensajes que caben en un push.
        igual(funcion.pedir("POST", "/api/avisos/alta", {"suscripcion": {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"}}}, f"Bearer token-{uid}"), (200, {"ok": True}), "El alta del dispositivo")
        avisadas = []
        for ahora, cuantas in (("2027-01-30T23:35", 40), ("2027-01-30T23:40", 40), ("2027-01-30T23:45", 20), ("2027-01-30T23:50", 0)):
            intentos, consola = funcion.pasada(ahora)
            cosas_avisadas = [c for intento in intentos for c in intento["aviso"]["cosas"]]
            igual((len(cosas_avisadas), resumen_de_la_pasada(consola)[1], {(i["endpoint"], i["estado"], json.dumps(i["opciones"], sort_keys=True)) for i in intentos} - {(endpoint, 201, json.dumps(OPCIONES_DE_RECORDATORIO, sort_keys=True))}),
                  (cuantas, cuantas, set()), f"La pasada de las {ahora[-5:]}: cosas avisadas, todas a este dispositivo y con las opciones de un recordatorio")
            for intento in intentos:
                aviso = intento["aviso"]
                # El mensaje del contrato para esas cosas, con cada texto recortado a 300 letras (la línea de resumen, más corta, se mide aparte).
                debido = envio_esperado(uid, [(c["id"], todas[c["id"]]["texto"][:299] + "…") for c in aviso["cosas"]])
                igual(({**aviso, "cosas": [{"id": c["id"], "texto": c["texto"]} for c in aviso["cosas"]], "linea": debido["linea"]}, all(re.fullmatch(r"[A-Za-z0-9_-]{43}", c["ficha"]) for c in aviso["cosas"]),
                       len(aviso["linea"]) <= 120, len(intento["bruto"].encode("utf-8")) <= MAX_BYTES_DE_UN_ENVIO), (debido, True, True, True),
                      f"Un mensaje de la pasada de las {ahora[-5:]}: lo que dice el contrato, cada cosa con su ficha, la línea en 120 letras y todo en menos de {MAX_BYTES_DE_UN_ENVIO} bytes")
            comprobar(len(intentos) < len(cosas_avisadas) or not intentos, f"Las cosas no viajan juntas: {len(intentos)} mensajes para {len(cosas_avisadas)} cosas")
            avisadas += [c["id"] for c in cosas_avisadas]
        igual((len(avisadas), sorted(avisadas)), (100, sorted(todas)), "Entre las tres pasadas se avisa de las cien, una vez de cada una")
    finally:
        funcion.cerrar()


@prueba("de punta a punta con la función de verdad, sin dispositivos a los que avisar: con la cuenta de Google y sin notificaciones aquí los recordatorios se suben igual, pero al llegar su hora la sesión queda en pausa; si pasa un mes así, la limpieza borra el documento, y cuando la app vuelve lo sube entero otra vez sin perder nada (es otro documento: otro «nacido»); al activar las notificaciones, el alta la despierta y lo que tocó hace poco llega con retraso; y desactivarlas con cuenta solo da de baja el dispositivo: el documento sigue")
def t_integracion_pausa_y_limpieza(e):
    funcion = FuncionDeVerdad()
    try:
        uid = "u1"
        cada_noche = recordatorio("00:35")
        enviada = {"id-1": recordada_enviada("Llamar al dentista", cada_noche)}
        funcion.reloj("2027-01-30T23:30")
        contexto = dispositivo_con_recordar(e, funcion, usuario=uid, endpoint=None)
        p = abrir_con_recordar(contexto, e, estado_recordar([recordada(1, "Llamar al dentista")]), recordar_guardado(cosas={"id-1": cada_noche}), sesion=None, cuenta=True)
        esperar_vista(p, "grupo")
        esperar_envios(p, funcion, 1)
        base = esperar_base(p, "los recordatorios se suben con la sesión de la cuenta", v=1)
        nacido = base["nacido"]
        igual((funcion.envios()[0]["cuerpo"], funcion.envios()[0]["cabeceras"].get("authorization"), funcion.almacen("dispositivos/")), (parche(0, enviada), f"Bearer token-{uid}", {}), "Subidos con la cuenta, sin ningún dispositivo dado de alta")
        esperar_nota(p, "apagados", "Activar notificaciones")
        # Llega su hora y no hay a quién avisar: en pausa, fuera de la agenda y con fecha para la limpieza (treinta días después).
        intentos, consola = funcion.pasada("2027-01-30T23:35")
        documento = funcion.documento(uid)
        igual((intentos, resumen_de_la_pasada(consola), documento["pausado"], documento["prox"], sorted(funcion.almacen("limpieza/"))), ([], (1, 0, 1, 0), instante("2027-01-30T23:35"), {"id-1": instante("2027-01-30T23:35")}, [f"limpieza/20270301/{uid}"]),
              "A su hora, sin dispositivos: nada enviado, la sesión en pausa (sin mover lo que tocaba) y apuntada para la limpieza")
        igual(funcion.pasada("2027-01-31T23:35")[0], [], "En pausa no se intenta avisar")
        # Un mes así: la limpieza de las 03:00 (UTC) del día señalado borra el documento. La víspera, aún no.
        igual((resumen_de_la_pasada(funcion.pasada("2027-02-28T03:00")[1])[3], funcion.documento(uid) is not None), (0, True), "La víspera de cumplirse el mes, la limpieza no lo toca")
        igual((resumen_de_la_pasada(funcion.pasada("2027-03-01T03:00")[1])[3], funcion.almacen("recordar/"), funcion.almacen("limpieza/")), (1, {}, {}), "Cumplido el mes sin dispositivos: la limpieza borra el documento")
        # La app vuelve: la función ya no tiene aquel documento. Lo sube todo otra vez, a uno nuevo, sin quitar aquí nada.
        lecturas = len(funcion.lecturas())
        p.evaluate(JS_VUELVE_LA_CONEXION)
        esperar_envios(p, funcion, 2)
        base = esperar_base(p, "la app sube todo otra vez", v=1, nacido=funcion.documento(uid)["nacido"])
        igual((funcion.lecturas()[lecturas]["respuesta"], funcion.envios()[1]["cuerpo"], funcion.envios()[1]["estado"], base["nacido"] != nacido, cuandos(p), leer_recordar(p)["cosas"]),
              (SIN_DOCUMENTO, parche(0, enviada), 200, True, {"Llamar al dentista": "0:35 · Cada día"}, {"id-1": cada_noche}), "Tras la limpieza: se pregunta, se sube entero a otro documento y aquí no se pierde el recordatorio")
        # Sigue sin dispositivos: a su hora, otra vez en pausa. Entonces se activan las notificaciones aquí (desde la nota): el alta la despierta.
        igual(resumen_de_la_pasada(funcion.pasada("2027-03-01T23:35")[1]), (1, 0, 1, 0), "A su hora, de nuevo sin dispositivos")
        funcion.reloj("2027-03-01T23:38")
        p.locator("#nota-recordar").get_by_role("button", name="Activar notificaciones", exact=True).tap()
        esperar_nota(p, None)
        esperar_llamadas(p, funcion, "alta", 1)
        alta = funcion.de("alta")[0]
        endpoint = alta[2]["suscripcion"]["endpoint"]
        igual((alta[1], endpoint, funcion.documento(uid)["pausado"], f"agenda/202703012338/{uid}" in funcion.almacen("agenda/")), (f"Bearer token-{uid}", ENDPOINT_PRUEBA + "prueba-1", None, True),
              "El alta de este dispositivo saca la sesión de la pausa y la devuelve a la agenda (en el minuto de ahora: lo suyo ya iba con retraso)")
        # Lo que tocó hace cinco minutos llega con retraso en la pasada siguiente; y mañana, a su hora.
        intentos, consola = funcion.pasada("2027-03-01T23:40")
        igual((len(intentos), resumen_de_la_pasada(consola)), (1, (1, 1, 0, 0)), "La pasada siguiente al alta")
        comprobar_envio(intentos[0], endpoint, uid, [("id-1", "Llamar al dentista")], "El aviso que tocó durante la pausa llega con retraso")
        igual(funcion.documento(uid)["prox"], {"id-1": instante("2027-03-02T23:35")}, "El siguiente aviso, mañana a su hora")
        # Desactivarlas con la cuenta de Google: se da de baja el dispositivo y nada más (otro dispositivo de la cuenta puede seguir avisando).
        p.get_by_role("button", name="Volver", exact=True).tap()
        esperar_vista(p, "lista")
        p.wait_for_timeout(400)
        volver(p)
        p.wait_for_timeout(400)
        abrir_ajustes(p)
        p.wait_for_timeout(400)
        boton_avisos(p).click()
        esperar_boton_avisos(p, "Activar notificaciones")
        esperar_toast(p, "Notificaciones desactivadas: Recordar no te avisará")
        esperar_llamadas(p, funcion, "baja", 1)
        p.wait_for_timeout(600)
        igual((funcion.de("olvidar"), funcion.de("baja"), funcion.almacen("dispositivos/"), funcion.de_la_sesion(uid)["cosas"], leer_recordar(p)["base"]["nacido"]),
              ([], [("baja", f"Bearer token-{uid}", {"endpoint": endpoint})], {}, enviada, base["nacido"]), "Con cuenta: baja del dispositivo, y el documento y la base siguen")
    finally:
        funcion.cerrar()


@prueba("estático de los avisos de Recordar: sw.js atiende los toques en un aviso (notificationclick con «hecho» y «mantener», notificationclose y sync), sin ninguna dirección absoluta que no sea del alojamiento (la función y lo que abre un aviso cuelgan de donde esté publicada la app) y con su caché de avisos a salvo de la limpieza de versiones; la app y la función coinciden en frecuencias, topes y cada cuánto se mira; no hay dependencias nuevas; y netlify/lib, netlify/functions y la función local de las pruebas pasan node --check", una_vez=True)
def t_recordar_nube_estatico(e):
    def fuente_de(fichero):
        """El código de ese fichero, sin comentarios."""
        return re.sub(r"(?m)(^|\s)//[^\n]*", "", re.sub(r"/\*.*?\*/", "", fichero.read_text(encoding="utf-8"), flags=re.S))

    def constante(fuente, nombre, que):
        valor = re.search(rf"\bconst {nombre} = ([^;]+);", fuente)
        comprobar(valor, f"{que} no declara {nombre}")
        return valor.group(1)

    sw, app = fuente_de(APP / "sw.js"), fuente_de(APP / "app.js")
    for trozo in ("self.addEventListener('notificationclick'", "self.addEventListener('notificationclose'", "self.addEventListener('sync'", "self.addEventListener('push'",
                  "accion === 'hecho'", "accion === 'mantener'", "{ action: 'hecho', title: 'Hecho' }", "{ action: 'mantener', title: 'Mantener' }", "showNotification('Recordar'", "requireInteraction: true"):
        comprobar(trozo in sw, f"sw.js no atiende los avisos de Recordar como se espera: falta «{trozo}»")
    # La app puede publicarse en una subcarpeta: la función de avisos y la dirección que abre un aviso se escriben desde donde esté el service worker.
    for trozo in ("new URL('api/avisos/hecho', self.registration.scope)", "url: `./?recordada=${encodeURIComponent(cosa.id)}#grupo/recordar`"):
        comprobar(trozo in sw, f"sw.js no escribe sus direcciones desde donde está publicado: falta «{trozo}»")
    absolutas = set(re.findall(r"""['"`](/[^'"`]*)['"`]""", sw))
    igual(sorted(absolutas), sorted(["/.netlify/", "/api/", "/con/", "/de/", "/cuenta/", "/__/", "/css/", "/js/", "/firebase-config.js", "/icons/favicon.svg"]),
          "Las únicas rutas absolutas de sw.js: las del alojamiento y las de cosas.info, que no sirve él")
    comprobar(not re.search(r"https?://", sw) and not re.search(r"\bconsole\.", sw), "sw.js contiene alguna dirección externa o escribe en la consola")
    # La caché donde el service worker deja lo de los avisos es la que lee la app, y no empieza como las cachés de la app (se borraría con cada versión).
    cache_de_avisos = constante(sw, "DATOS_DE_AVISOS", "sw.js")
    igual((constante(app, "DATOS_DE_AVISOS", "app.js"), cache_de_avisos.strip("'").startswith(constante(sw, "PREFIJO_CACHE", "sw.js").strip("'"))), (cache_de_avisos, False),
          "La caché de los avisos: la misma en la app y en sw.js, y fuera de las cachés que se limpian con cada versión")
    # Lo que la app y la función tienen que entender igual.
    recordar, horario = fuente_de(RAIZ / "netlify" / "lib" / "recordar.mjs"), fuente_de(RAIZ / "netlify" / "lib" / "horario.mjs")
    igual((constante(app, "FRECUENCIAS", "app.js"), constante(app, "MAX_RECORDADAS", "app.js"), constante(app, "MAX_ID_RECORDADA", "app.js"), constante(app, "MAX_TEXTO", "app.js")),
          (constante(horario, "FRECUENCIAS", "horario.mjs"), constante(recordar, "MAX_COSAS", "recordar.mjs"), constante(recordar, "MAX_ID", "recordar.mjs"), constante(recordar, "MAX_TEXTO", "recordar.mjs")),
          "Frecuencias y topes (cosas por sesión, largo del id y del texto): los de la app y los de la función")
    igual((constante(app, "MAX_RECORDADAS", "app.js"), constante(app, "PASO_MINUTOS", "app.js")), (str(MAX_RECORDADAS), "5"), "El tope de cosas con recordatorio y el paso de los minutos en la app")
    comprobar("export const config = { schedule: '*/5 * * * *' };" in fuente_de(RAIZ / "netlify" / "functions" / "recordatorios.mjs"), "La función programada no se declara cada cinco minutos (los que la app deja elegir)")
    comprobar("export const config = { path: '/api/avisos/:accion' };" in fuente_de(RAIZ / "netlify" / "functions" / "avisos.mjs") and "new URL('api/avisos/'," in app,
              "La función de avisos no está en api/avisos/, donde la busca la app")
    igual(sorted(json.loads((RAIZ / "package.json").read_text(encoding="utf-8"))["dependencies"]), ["@netlify/blobs", "jose", "web-push"], "Dependencias del servidor (sin ninguna nueva)")
    comprobar(NODE.is_file(), f"No se encuentra Node en {NODE}")
    for fichero in sorted((RAIZ / "netlify").rglob("*.mjs")) + [FUNCION_LOCAL, FUNCION_LOCAL.with_name("almacen-falso.mjs")]:
        resultado = subprocess.run([str(NODE), "--check", str(fichero)], capture_output=True, text=True, encoding="utf-8")
        igual(resultado.returncode, 0, f"node --check {fichero.relative_to(RAIZ).as_posix()}: {resultado.stderr.strip()}")


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
    # Recordar: la fila fija en la lista, su página (con la nota y los recordatorios de cada fila), la hoja del recordatorio (de una cosa,
    # para todas y eligiendo) y la de contestar a un aviso, sobre Azul, Blanco y Negro.
    datos_r, propios_r = recordar_variado()
    for nombre, color in (("azul", "#2F6FED"), ("blanco", "#FFFFFF"), ("negro", "#111111")):
        p12 = sembrar_recordar(e, estado_recordar(datos_r["cosas"], color=color), propios_r, ruta="#lista", contexto=e.contexto())
        esperar_filas(p12, 1)
        p12.screenshot(path=str(carpeta / f"{prefijo}_24-recordar-lista-{nombre}.png"))
        abrir_recordar(p12)
        p12.screenshot(path=str(carpeta / f"{prefijo}_25-recordar-pagina-{nombre}.png"))
        abrir_hoja_recordar(p12, "Llamar al dentista")
        p12.screenshot(path=str(carpeta / f"{prefijo}_26-recordar-hoja-una-{nombre}.png"))
        cerrar_hoja_recordar(p12)
        abrir_hoja_recordar(p12)
        p12.screenshot(path=str(carpeta / f"{prefijo}_27-recordar-hoja-todas-{nombre}.png"))
        pastilla(p12, "Elegir")
        casilla(p12, "Sacar la basura").locator("xpath=ancestor::label").tap()
        p12.wait_for_timeout(250)
        p12.screenshot(path=str(carpeta / f"{prefijo}_28-recordar-hoja-elegir-{nombre}.png"))
        cerrar_hoja_recordar(p12)
        p12.evaluate(JS_APUNTAR_AVISADAS, ["id-5", "id-4"])
        p12.evaluate(JS_TOCAR_AVISO, "id-4")
        esperar_hoja_avisada(p12)
        p12.screenshot(path=str(carpeta / f"{prefijo}_29-recordar-contestar-{nombre}.png"))
        p12.context.close()
    p13 = e.pagina(ruta="#grupo/recordar", contexto=e.contexto())
    esperar_vista(p13, "grupo")
    p13.wait_for_timeout(300)
    p13.screenshot(path=str(carpeta / f"{prefijo}_30-recordar-vacio.png"))
    # La nota con su botón (las notificaciones, sin activar) y, en iPhone sin instalar, la que lleva a los pasos.
    for nombre, guion, opciones in (("activar", "", {}), ("instalar", JS_SIN_PUSH, {"user_agent": UA_IPHONE})):
        p14 = sembrar_recordar(e, datos_r, propios_r, contexto=contexto_con_avisos(e, guion, **opciones))
        esperar_vista(p14, "grupo")
        p14.wait_for_timeout(300)
        p14.screenshot(path=str(carpeta / f"{prefijo}_31-recordar-nota-{nombre}.png"))
        p14.context.close()
    for etiqueta, ancho, alto in (("320", 320, 568), ("apaisado", e.alto, e.ancho), ("zoom", 195, 400)):
        p15 = sembrar_recordar(e, datos_r, propios_r, ruta="#lista", contexto=e.contexto(viewport={"width": ancho, "height": alto}))
        esperar_filas(p15, 1)
        p15.screenshot(path=str(carpeta / f"{prefijo}_32-recordar-lista-{etiqueta}.png"))
        abrir_recordar(p15)
        p15.screenshot(path=str(carpeta / f"{prefijo}_33-recordar-pagina-{etiqueta}.png"))
        abrir_hoja_recordar(p15, "Llamar al dentista")
        p15.screenshot(path=str(carpeta / f"{prefijo}_34-recordar-hoja-una-{etiqueta}.png"))
        cerrar_hoja_recordar(p15)
        abrir_hoja_recordar(p15)
        pastilla(p15, "Elegir")
        p15.wait_for_timeout(250)
        p15.screenshot(path=str(carpeta / f"{prefijo}_35-recordar-hoja-elegir-{etiqueta}.png"))
        p15.context.close()


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
