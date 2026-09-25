# -*- coding: utf-8 -*-
"""Sella en app/sw.js la versión de la caché con una huella del contenido de la app.

El service worker sirve la app desde la caché y solo la renueva cuando cambia el
nombre de la caché (VERSION_CACHE). Este guion calcula una huella SHA-256 de los
ficheros precargados y la escribe en esa constante, de modo que cualquier cambio
en la app llega a quienes ya la tienen instalada. Hay que ejecutarlo antes de
publicar; repetirlo sin cambios no toca nada:

    "C:/Users/Raul/AppData/Local/Programs/Python/Python312/python.exe" herramientas/sellar_version.py

Con --comprobar no escribe: solo dice si el sello está al día (código 1 si no).
"""
import hashlib
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
SW = APP / "sw.js"

# const VERSION_CACHE = 'cosas-v1.0.0' o 'cosas-v1.0.0-1a2b3c4d' (versión + huella).
PATRON_VERSION = re.compile(r"^(?P<antes>const VERSION_CACHE = ')(?P<version>(?P<base>cosas-v[0-9.]+)(?:-[0-9a-f]{8})?)(?P<despues>';)", re.M)
PATRON_RECURSOS = re.compile(r"const RECURSOS = \[(.*?)\];", re.S)


def recursos_precargados(fuente_sw):
    """Rutas de los ficheros que precarga el service worker, en su mismo orden."""
    bloque = PATRON_RECURSOS.search(fuente_sw)
    if not bloque:
        raise ValueError("sw.js no declara la lista RECURSOS")
    rutas = re.findall(r"'\./([^']+)'", bloque.group(1))  # «./» (la portada) es index.html.
    if not rutas:
        raise ValueError("La lista RECURSOS de sw.js está vacía")
    return rutas


def huella(rutas):
    """Primeros 8 caracteres del SHA-256 de los nombres y el contenido de los ficheros."""
    resumen = hashlib.sha256()
    for ruta in rutas:
        resumen.update(ruta.encode("utf-8") + b"\0")
        resumen.update((APP / ruta).read_bytes() + b"\0")
    return resumen.hexdigest()[:8]


def leer_sw():
    with open(SW, encoding="utf-8", newline="") as fichero:  # Conserva los finales de línea.
        return fichero.read()


def declaracion(fuente_sw):
    coincidencia = PATRON_VERSION.search(fuente_sw)
    if not coincidencia:
        raise ValueError("sw.js no declara «const VERSION_CACHE = 'cosas-v…';»")
    return coincidencia


def version_actual(fuente_sw):
    return declaracion(fuente_sw)["version"]


def version_sellada(fuente_sw):
    """La versión que debería llevar sw.js según el contenido actual de la app."""
    return f"{declaracion(fuente_sw)['base']}-{huella(recursos_precargados(fuente_sw))}"


def sellar():
    """Escribe el sello si ha cambiado. Devuelve (versión anterior, versión nueva)."""
    fuente = leer_sw()
    anterior, nueva = version_actual(fuente), version_sellada(fuente)
    if nueva != anterior:
        sellada = PATRON_VERSION.sub(lambda c: f"{c['antes']}{nueva}{c['despues']}", fuente, count=1)
        with open(SW, "w", encoding="utf-8", newline="") as fichero:
            fichero.write(sellada)
    return anterior, nueva


def main():
    if "--comprobar" in sys.argv[1:]:
        fuente = leer_sw()
        anterior, nueva = version_actual(fuente), version_sellada(fuente)
        if anterior == nueva:
            print(f"OK       {anterior}")
            return 0
        print(f"ANTIGUO  sw.js lleva {anterior} y la app actual es {nueva}: ejecuta sellar_version.py")
        return 1
    anterior, nueva = sellar()
    print(f"SIN CAMBIOS  {nueva}" if anterior == nueva else f"SELLADO  {anterior} -> {nueva}")
    return 0


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
