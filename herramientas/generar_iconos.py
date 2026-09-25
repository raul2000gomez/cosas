# -*- coding: utf-8 -*-
"""Genera todos los iconos PNG de Cosas a partir de un SVG en línea.

Cada icono se dibuja con Chromium (Playwright) en una página del tamaño exacto
en píxeles y se captura. Se puede ejecutar tantas veces como haga falta:

    "C:/Users/Raul/AppData/Local/Programs/Python/Python312/python.exe" herramientas/generar_iconos.py
"""
import struct
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

COLOR_FONDO = "#2F6FED"
COLOR_TRAZO = "#FFFFFF"
CARPETA_ICONOS = Path(__file__).resolve().parent.parent / "app" / "icons"

# nombre, lado en px, ancho del check (fracción del lado), grosor del trazo
# (fracción del lado), radio de las esquinas (fracción del lado; 0 = cuadrado
# opaco a sangre).
ICONOS = [
    ("icon-192.png", 192, 0.50, 0.090, 0.22),
    ("icon-512.png", 512, 0.50, 0.090, 0.22),
    # Maskable: fondo a sangre y dibujo dentro del 80 % central (zona segura).
    ("icon-maskable-512.png", 512, 0.38, 0.072, 0.0),
    # iOS redondea el icono por su cuenta: cuadrado, opaco y sin transparencia.
    ("apple-touch-icon.png", 180, 0.50, 0.090, 0.0),
    ("favicon-32.png", 32, 0.56, 0.125, 0.22),
]

# Check de referencia: M20 6 L9 17 L4 12 (16 de ancho, 11 de alto, centro en 12, 11.5).
ANCHO_CHECK = 16.0
CENTRO_CHECK = (12.0, 11.5)


def svg_icono(lado, ancho_check, grosor, radio):
    """Devuelve el SVG del icono: fondo plano y un check blanco redondeado."""
    escala = (ancho_check * lado) / ANCHO_CHECK
    trazo = (grosor * lado) / escala
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{lado}" height="{lado}" viewBox="0 0 {lado} {lado}">'
        f'<rect width="{lado}" height="{lado}" rx="{radio * lado}" fill="{COLOR_FONDO}"/>'
        f'<g transform="translate({lado / 2} {lado / 2}) scale({escala}) '
        f'translate({-CENTRO_CHECK[0]} {-CENTRO_CHECK[1]})">'
        f'<path d="M20 6 9 17 4 12" fill="none" stroke="{COLOR_TRAZO}" stroke-width="{trazo}" '
        f'stroke-linecap="round" stroke-linejoin="round"/></g></svg>'
    )


def dimensiones_png(ruta):
    """Lee ancho y alto de la cabecera IHDR del PNG."""
    cabecera = ruta.read_bytes()[:24]
    if cabecera[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{ruta.name} no es un PNG")
    return struct.unpack(">II", cabecera[16:24])


def aplanar_si_se_puede(ruta):
    """Quita el canal alfa de un icono opaco (iOS pinta de negro la transparencia)."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(ruta) as imagen:
        plana = imagen.convert("RGB")
    plana.save(ruta, format="PNG", optimize=True)


def generar():
    CARPETA_ICONOS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch()
        try:
            for nombre, lado, ancho_check, grosor, radio in ICONOS:
                contexto = navegador.new_context(
                    viewport={"width": lado, "height": lado}, device_scale_factor=1
                )
                pagina = contexto.new_page()
                pagina.set_content(
                    "<!DOCTYPE html><html><head><style>html,body{margin:0;background:transparent}"
                    "svg{display:block}</style></head><body>"
                    + svg_icono(lado, ancho_check, grosor, radio)
                    + "</body></html>"
                )
                ruta = CARPETA_ICONOS / nombre
                opaco = radio == 0
                pagina.screenshot(
                    path=str(ruta),
                    clip={"x": 0, "y": 0, "width": lado, "height": lado},
                    omit_background=not opaco,
                )
                contexto.close()
                if opaco:
                    aplanar_si_se_puede(ruta)
        finally:
            navegador.close()


def comprobar():
    """Confirma que cada PNG existe y mide exactamente lo que debe."""
    correcto = True
    for nombre, lado, *_ in ICONOS:
        ruta = CARPETA_ICONOS / nombre
        if not ruta.is_file():
            print(f"FALTA   {nombre}")
            correcto = False
            continue
        ancho, alto = dimensiones_png(ruta)
        bien = (ancho, alto) == (lado, lado)
        correcto = correcto and bien
        print(f"{'OK' if bien else 'ERROR':7} {nombre}: {ancho}x{alto} ({ruta.stat().st_size} bytes)")
    return correcto


if __name__ == "__main__":
    generar()
    sys.exit(0 if comprobar() else 1)
