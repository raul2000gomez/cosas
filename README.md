# Cosas

App para apuntar cosas que hacer. Es una app web instalable (PWA) que funciona en iPhone, Android y PC, también sin conexión. Está hecha con HTML, CSS y JavaScript, sin dependencias.

Publicada en https://cosas-app.netlify.app

## Carpetas

- `app/` — la app. Es lo único que se publica. En la pantalla de inicio, bajo los botones, van los
  accesos «Cosas con» y «Cosas de» a las listas compartidas de cosas.info (`?desde=app` hace que la
  flecha de esas páginas vuelva a la app).
- `pruebas/e2e.py` — pruebas de extremo a extremo con Playwright (iPhone 13 y Pixel 7 emulados).
- `herramientas/sellar_version.py` — sella la versión de la caché en `app/sw.js`.
- `herramientas/generar_iconos.py` — genera los iconos PNG.

## Antes de publicar un cambio

1. `python herramientas/sellar_version.py` (sin esto, los móviles con la app instalada no reciben la versión nueva).
2. `python pruebas/e2e.py` (tiene que salir todo PASS).
3. `netlify deploy --prod --dir app`
