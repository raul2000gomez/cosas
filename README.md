# Cosas

App para apuntar cosas que hacer. Es una app web instalable (PWA) que funciona en iPhone, Android y PC, también sin conexión. Está hecha con HTML, CSS y JavaScript, sin dependencias.

Publicada en https://cosas-app.netlify.app

## Carpetas

- `app/` — la app. Es lo único que se publica. En la pantalla de inicio, deslizar el dedo abre el
  botón de la esquina de la que se tira (hacia la derecha, la lista; hacia la izquierda, los
  ajustes), sin nada que se mueva ni se marque; ahí el navegador no toma el deslizar de lado
  (`touch-action` y `overscroll-behavior-x`), así que no enseña su círculo de «atrás». Bajo los botones van los
  accesos «Cosas con» y «Cosas de» a las listas compartidas de cosas.info (`?desde=app` hace que la
  flecha de esas páginas vuelva a la app). Se abren en este mismo dominio, en `/con/` y `/de/`:
  `netlify.toml` las trae de cosas.info (proxy, el código sigue solo en el repositorio `Web`), así la
  app instalada las abre sin la barra del navegador y ellas usan el color de fondo de la app. El
  service worker no las sirve ni las guarda. Sin proxy (`file://`, servidor local), van a cosas.info.
  En Ajustes → Datos personales, **Iniciar sesión** abre del mismo modo `cuenta/` (Tu cuenta, de
  cosas.info), que entra con Google (la cuenta de Cosas con y Cosas de) y vuelve a los ajustes; con
  sesión, el botón es **Cerrar sesión**. La app solo lee la cuenta que esa página deja en
  `localStorage` (`cosascon:cuenta`). `netlify.toml` también sirve `/__/` desde firebaseapp.com para
  que entrar con Google por redirección funcione en la app instalada.
  En Ajustes → Aplicación, bajo **Convertir en aplicación**, **Compartir aplicación** envía el enlace
  de la app con el menú de compartir del sistema (o lo copia, si no hay). La vista previa del enlace
  son las etiquetas `og:` de `index.html` y la tarjeta `icons/compartir.png` (1200x630, la misma
  que las de las invitaciones de Cosas con y Cosas de).
- `app/nube.js` — con sesión, **tus cosas en todos tus dispositivos**: las cosas, los grupos, el color
  y el nombre se guardan también en Firestore (proyecto `cosas-info`, el de cosas.info), en
  `personales/{uid}/elementos` (un documento por cosa, por grupo y para los ajustes; las reglas están
  en `firestore.rules` del repositorio `Web`). `localStorage` sigue mandando y la app funciona igual
  sin conexión: `nube.js` reconcilia documento a documento con la última versión en la que coincidían
  los dos lados (`cosas:nube`): lo que cambió solo en la nube se trae, lo que cambió solo aquí se sube
  y, si cambió en los dos, gana este dispositivo (los ajustes de la cuenta mandan la primera vez que se
  entra en un dispositivo). La primera vez se juntan las cosas de los dos lados. El ojo de cada grupo
  (abierto o cerrado) no viaja. `app.js` solo carga `nube.js` (y Firebase, de gstatic) con sesión y en
  https (la app publicada: la configuración es la `firebase-config.js` de cosas.info que sirve
  `netlify.toml`); `data-nube` en `<html>` dice cómo va (`conectando`, `guardando`, `al-dia`, `error`).
  Sin sesión, todo se queda en el dispositivo, como siempre.
- `pruebas/e2e.py` — pruebas de extremo a extremo con Playwright (iPhone 13 y Pixel 7 emulados).
- `herramientas/sellar_version.py` — sella la versión de la caché en `app/sw.js`.
- `herramientas/generar_iconos.py` — genera los iconos PNG.

## Antes de publicar un cambio

1. `python herramientas/sellar_version.py` (sin esto, los móviles con la app instalada no reciben la versión nueva).
2. `python pruebas/e2e.py` (tiene que salir todo PASS).
3. `git push` (el sitio de Netlify está conectado al repositorio y publica `app/` con cada cambio
   en `main`, según `netlify.toml`). A mano también vale: `netlify deploy --prod --dir app`.

Ojo: si el repositorio es privado, el plan gratuito de Netlify solo construye los commits cuyo
autor sea el dueño de la cuenta (en GitHub, `raul2000gomez`); cualquier otro autor se queda en
«Build blocked: Unrecognized Git contributor» y no se publica. Por eso el repositorio es público.
