# Cosas

App para apuntar cosas que hacer. Es una app web instalable (PWA) que funciona en iPhone, Android y PC, también sin conexión. Está hecha con HTML, CSS y JavaScript, sin dependencias.

Publicada en https://cosas-app.netlify.app

## Carpetas

- `app/` — la app. Es lo único que se publica. En la pantalla de inicio, deslizar el dedo abre el
  botón de la esquina de la que se tira (hacia la derecha, la lista; hacia la izquierda, los
  ajustes), sin nada que se mueva ni se marque; en la lista y en los ajustes, el gesto de vuelta
  (hacia la izquierda en la lista, hacia la derecha en los ajustes) es «Volver». En esas vistas el
  navegador no toma el deslizar de lado (`touch-action` y `overscroll-behavior-x`), así que no
  enseña su círculo de «atrás». Bajo los botones van los
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
  `nube.js` vigila también las **novedades** de Cosas con y Cosas de (`vigilarNovedades`): con la sesión
  que esas páginas dejan en este dominio (anónima o de Google) y si en el dispositivo hay listas suyas
  (`cosascon:ids`), un punto verde arriba a la derecha del icono de Cosas con o Cosas de cuando otra
  persona ha apuntado algo que aún no has visto (la lista guarda `ultima`; lo visto, `usuarios/{uid}.vistos`,
  lo escriben esas páginas). Firebase se carga una sola vez para todo.
- **Notificaciones** (Ajustes → Notificaciones): avisos en el móvil cuando alguien añade una cosa a una de
  tus listas de Cosas con o de Cosas de, y los recordatorios de Recordar (abajo). **Activar notificaciones**
  pide permiso, suscribe este dispositivo (push) con la clave pública de la función de avisos y lo da de
  alta con la sesión de Cosas con y Cosas de en este dominio (`sesionDeAvisos` de `nube.js`; si aún no hay
  ninguna, entra de forma anónima, como harían ellas). El dispositivo lo recuerda en `cosas:avisos`
  (`{ uid, endpoint, en }`); al abrir la app se vuelve a
  dar de alta si el navegador cambió la suscripción o ha pasado un día, y si se quitó el permiso quedan
  desactivadas. En iPhone y iPad solo llegan a la app instalada (Safari no tiene push fuera de ella): allí
  la ayuda lo dice. `sw.js` enseña cada aviso (título de la lista, «Ana ha añadido «Leche»», icono y
  `icons/aviso-96.png` como insignia), junta en uno los de una misma lista (etiqueta `lista-ID`, hasta cinco
  líneas, lo nuevo arriba) y, al tocarlo, abre esa lista (o el grupo donde está la cosa) en la app. Cosas
  con y Cosas de quitan los avisos de una lista al verla.
- **Recordar** — el grupo fijo, arriba de la lista (`#acceso-recordar`; su pantalla, `#grupo/recordar`). A
  cada cosa que se apunta en él se le puede poner un **recordatorio**: una hora (de cinco en cinco minutos)
  y cada cuánto (**Cada día**, **De lunes a viernes**, **Cada semana**, un día, o **Cada mes**, un día del
  mes: en los meses más cortos, el último), de una en una, a varias o a todas. Siempre se repite, hasta que
  la cosa se da por hecha. A su hora llega un aviso por cada cosa, con **Hecho** (la marca, y no se vuelve
  a avisar de ella) y **Mantener** (se volverá a avisar cuando toque). Donde los avisos no tienen botones
  (iPhone, iPad, Firefox) se contesta en la app: al tocar el aviso, o al abrirla, pregunta por lo avisado
  en el último día que siga sin contestar. Lo de este dispositivo vive en `cosas:recordar`
  (`localStorage`: los recordatorios y lo último en que coincidían este dispositivo y la función de
  avisos) y `nube.js` (`sincronizarRecordatorios`) se lo cuenta a la función con la sesión de las
  notificaciones (anónima o de Google) y trae lo que haya cambiado allí: un **Hecho** pulsado en un aviso,
  o lo que se puso en otro dispositivo con la misma cuenta. Solo sube lo que tiene recordatorio (su texto,
  si está hecha y cuándo toca), y solo en la app publicada (https) y con las notificaciones activadas o la
  sesión iniciada. Mientras este dispositivo no pueda avisar (notificaciones sin activar o bloqueadas,
  iPhone sin la app instalada, sin conexión), una nota arriba de Recordar lo dice. Cómo funciona por
  dentro, qué garantiza y qué se guarda: más abajo, en «Recordatorios».
- `netlify/functions/avisos.mjs` — la **función de avisos** (Netlify Functions, en `/api/avisos/…`):
  `clave` (la clave pública VAPID), `alta` y `baja` (este dispositivo, con la sesión de Firebase en
  `Authorization: Bearer`) y `avisar` (lo llama Cosas con / Cosas de, también desde cosas.info, tras
  añadir una cosa). Comprueba el token con las claves públicas de Google y lee la lista y la cosa de
  Firestore **con ese mismo token**, así que mandan las reglas: solo avisa quien está en la lista, de una
  cosa suya, recién añadida y una sola vez; los avisos van a los dispositivos de la demás gente de la
  lista. Solo envía a los servicios push de los navegadores (Google, Apple, Mozilla, Microsoft): la
  dirección de cada dispositivo se mira tal cual, letra a letra (https, uno de esos hosts, sin usuario ni
  otro puerto), al darlo de alta y otra vez antes de cada envío. Olvida las suscripciones que ya no
  existen. Guarda además los recordatorios de cada sesión: `recordar` (con
  GET, la app los lee; con POST envía lo que cambia, qué poner y qué quitar, sobre la versión que vio: si
  ya no es esa, 409 con lo que hay), `hecho` (lo llama el service worker al pulsar **Hecho** en un aviso;
  sin sesión, con la ficha que venía en ese aviso) y `olvidar` (borra los de esa sesión). Todo lo suyo
  (las claves VAPID, que se crean solas la primera vez, los dispositivos y los recordatorios) vive en
  Netlify Blobs (almacén `avisos`): no hay nada que configurar ni secretos en el repositorio. Sus
  dependencias (`package.json`) las instala Netlify al publicar.
  Para probarla en local: `netlify dev --dir app` (con `netlify link` hecho una vez).
- `netlify/functions/recordatorios.mjs` — la **función programada**: Netlify la ejecuta cada cinco minutos
  (`schedule: '*/5 * * * *'`, en UTC) y envía a cada sesión los recordatorios que le tocan; una vez al día
  (en la ejecución de las 03:00 UTC) borra además lo que ya no hace falta guardar. **Solo se ejecuta sola
  en el sitio publicado (producción)**: ni en las vistas previas de un cambio, ni en otras ramas, ni con
  `netlify dev`.
- `netlify/lib/` — lo que comparten las dos funciones (fuera de `netlify/functions/`, donde cada archivo
  sería una función): `horario.mjs` (cuándo toca cada recordatorio, con su zona horaria y los cambios de
  hora), `recordar.mjs` (el documento de cada sesión y las cuentas que se hacen con él, sin almacén ni
  reloj), `recordatorios.mjs` (leer, guardar, la pasada programada y la limpieza, sobre Netlify Blobs) y
  `envio.mjs` (las claves, las fichas de «Hecho» y el envío push).
- `pruebas/servidor/` — pruebas de las dos funciones y de `netlify/lib/` (`node:test`; no salen a
  internet): `npm run probar:servidor`.
- `pruebas/e2e.py` — pruebas de extremo a extremo con Playwright (iPhone 13 y Pixel 7 emulados).
- `herramientas/sellar_version.py` — sella la versión de la caché en `app/sw.js`.
- `herramientas/generar_iconos.py` — genera los iconos PNG.

## Recordatorios

### De punta a punta

1. **La app guarda.** `POST /api/avisos/recordar` lleva lo que cambia (`poner`, `quitar`, `general`) y la
   versión sobre la que se escribe (`siV`). La función guarda un documento por sesión, calcula cuándo toca
   cada cosa en la zona horaria del dispositivo que puso los recordatorios (cambios de hora incluidos) y
   deja la sesión apuntada en la agenda. De ahí no sale ningún aviso.
2. **La función programada pasa cada cinco minutos.** Lista la agenda y, a cada sesión a la que le toca
   algo, le toma esos avisos con un turno (dos minutos), se los envía por push a sus dispositivos y solo a
   los que han llegado les apunta la vez siguiente. Un mensaje push puede llevar varias cosas (hasta 3000
   bytes; las que no caben, en otro).
3. **El service worker (`sw.js`) enseña un aviso por cosa.** **Hecho** llama a `POST /api/avisos/hecho`
   con la ficha que venía en ese aviso (una firma de esa cosa de esa sesión: el service worker no tiene
   sesión): la cosa queda hecha en la función y deja de avisarse. Sin conexión lo deja apuntado y lo
   repite. **Mantener** solo cierra el aviso: la función no se entera, y volverá a avisar cuando toque.
   Donde no hay botones (iPhone, iPad, Firefox), tocar el aviso abre la app, que hace la pregunta.
4. **La app lee** (`GET /api/avisos/recordar`) al abrirse y al volver a ella, y junta lo de la función con
   lo suyo: marca lo que se dio por hecho en un aviso y sube lo que haya cambiado aquí.

### Lo que hay en Netlify Blobs (almacén `avisos`)

```
vapid                         las claves VAPID (se crean solas la primera vez)
secreto                       la clave con la que se firman las fichas de «Hecho» (también se crea sola)
dispositivos/{uid}/{huella}   la suscripción push de un dispositivo de esa sesión
suscripciones/{huella}        de quién es ese dispositivo (avisa a una sola sesión)
enviados/{lista}              lo último ya avisado de esa lista de Cosas con o Cosas de
recordar/{uid}                los recordatorios de esa sesión (abajo)
agenda/{AAAAMMDDHHmm}/{uid}   índice, sin datos: el minuto (UTC) del aviso más cercano de esa sesión
limpieza/{AAAAMMDD}/{uid}     índice, sin datos: el día (UTC) desde el que su documento se puede borrar
```

`recordar/{uid}` es `{ nacido, v, zona, general, cosas: { id: { texto, hecha, r } } }` (lo que comparte
con la app: `v` es su versión y `nacido` distingue un documento del que se creara después de borrarlo) más
lo que la función apunta para sí: cuándo toca cada cosa, los turnos, desde cuándo está en pausa, cuántos
avisos lleva hoy y cuándo lo cambió su dueño por última vez. La función programada solo lista `agenda/`:
no recorre los documentos de todo el mundo. Cada cambio de un documento se escribe comparando su etiqueta
(ETag), y las lecturas son de consistencia fuerte.

### Lo que garantiza, y lo que no

- **Un aviso puede repetirse; un fallo a medias no se lo come.** Lo que una pasada toma y no llega a
  confirmar (se corta, el servicio push no responde, Netlify la ejecuta dos veces) lo repite la siguiente
  cuando caduca su turno. Solo se da por avisado lo que ha llegado a algún dispositivo, o lo que todos
  rechazan de un modo que repetir no arregla.
- **A su hora, o en la pasada siguiente.** Las horas se eligen de cinco en cinco y la función pasa cada
  cinco minutos: lo normal es que salga en la pasada de su minuto. Una hora que no sea múltiplo de cinco
  (la función las admite) sale hasta cinco minutos tarde; y lo que una pasada no consigue enviar, o no le
  da tiempo (trabaja 18 de los 30 segundos que le da Netlify), sale en la siguiente. Un envío que no
  acaba se corta a los 8 segundos y se repite después: un dispositivo que no contesta no retiene a los demás.
- **Lo atrasado se sigue intentando seis horas; después se salta** hasta la vez siguiente que toque (y el
  servicio push tampoco lo guarda más de seis horas para un dispositivo apagado).
- **Sin dispositivos, pausa.** A la sesión a la que le toca un aviso y no tiene a quién enviarlo (se
  desactivaron las notificaciones, se desinstaló la app) no se le envía nada. Si vuelve (activa las
  notificaciones, o la app lee o guarda teniendo ya dispositivo), despierta, y lo que le tocó en las
  últimas seis horas sale con retraso, en la pasada siguiente a la que lo ve; aunque la pausa haya durado
  días, es un solo aviso por cosa, no uno por cada día que faltó.
- **Límites**: 100 cosas con recordatorio por sesión (texto de hasta 500 caracteres; en el aviso, hasta
  300); 40 avisos por sesión en cada pasada (los demás, en la siguiente) y 300 por sesión al día (por
  encima se saltan: algo va mal, o alguien abusa); se avisa a los diez dispositivos más recientes de la
  sesión; lo que envía la app de una vez no pasa de 100000 bytes.
- **No garantiza** que el aviso se vea: de eso se encargan el servicio push del navegador y el sistema del
  móvil (ahorro de batería, no molestar). Ni el minuto exacto: depende de cuándo lance Netlify la función.

### Qué se guarda y hasta cuándo

De cada sesión (la de Firebase, anónima o de Google), y solo de las cosas de Recordar **que tienen
recordatorio**: el identificador de la cosa, su texto, si está hecha y su recordatorio; además, el
recordatorio puesto para todas, la zona horaria y lo que la función apunta para sí. Y, de cada dispositivo
con las notificaciones activadas, su suscripción push. El aviso viaja cifrado para ese dispositivo a
través del servicio push de su navegador (Google, Apple, Mozilla o Microsoft). Nada se guarda para
siempre:

- **El texto de una cosa** se borra en cuanto la app le dice a la función que ya no tiene recordatorio (se
  le quitó, salió de Recordar o se eliminó): en el siguiente guardado, que es enseguida si hay conexión.
- **Todos los recordatorios de la sesión** se borran al desactivar las notificaciones en un dispositivo
  sin cuenta de Google (`olvidar`; si en ese momento no hay conexión no se insiste, y se van por lo de
  abajo).
- **Un documento en pausa** se borra a los treinta días si la sesión sigue sin dispositivos.
- **Un documento que ya no espera ningún aviso** (todo hecho, o ya sin cosas) se borra a los treinta días
  de que su dueño lo tocara por última vez (la app al guardar, o un **Hecho** en un aviso; leerlo no
  cuenta). El que se queda sin cosas no se borra en el acto: ya no guarda ningún texto, y es lo que dice a
  los demás dispositivos de la cuenta que aquello se quitó.
- Si después de un borrado su dueño vuelve, la app ve que ya no hay documento y sube otra vez lo que tenga
  con recordatorio (también lo hecho).
- La limpieza pasa una vez al día (03:00 UTC), así que «treinta días» es la primera limpieza del día en
  que se cumplen: puede ser unas horas antes o después. Las claves de `agenda/` y `limpieza/` que sobran
  (llevan el identificador de la sesión, ningún texto) las quita la función cuando las encuentra.
- **Las suscripciones push** no caducan solas: se olvidan cuando el servicio push dice que ya no existen,
  cuando no sirven (unas claves que ningún navegador daría) o al desactivar las notificaciones.

### Probarlo

- `npm install` (una vez) y `npm run probar:servidor`, con Node 22: las funciones enteras y `netlify/lib/`
  con un almacén, un Google y un servicio push de mentira, incluidos los cruces (la app guarda mientras
  pasa la programada, o mientras la limpieza borra). `blobs-reales.test.mjs` usa el cliente de verdad de
  Netlify Blobs contra su servidor local (también cuando una escritura pierde una carrera y el almacén
  responde 409: por eso `@netlify/blobs` va fijado en 11.1.7, la primera versión que no la da por
  guardada), y `web-push.test.mjs` el `web-push` de verdad, sin salir a la red.
- `python pruebas/e2e.py` (`--filtro TEXTO`, `--dispositivo "Pixel 7"`): la app en un navegador. Casi
  todo, con la función de avisos y el push simulados; el service worker, en Google Chrome de verdad; y el
  recorrido entero, contra las dos funciones de verdad servidas en local por
  `pruebas/servidor/funcion-local.mjs` (con el almacén, Google y el servicio push de mentira).
- **Lo que no se puede probar fuera de producción:**
  - *Que Netlify lance la función programada.* Solo lo hace en el sitio publicado. En local,
    `netlify dev` no la ejecuta a su hora: `netlify functions:invoke recordatorios` la lanza una vez, a
    mano. Publicada, sus registros dicen cada cinco minutos «Recordatorios: N sesiones atendidas, …», y el
    botón **Run now** del panel de Netlify la lanza sin esperar.
  - *Los servicios push de verdad* (que acepten el mensaje y lo entreguen) y lo que hace cada sistema con
    el aviso: los botones en Android y en el ordenador, el iPhone sin ellos, el «Hecho» sin conexión.
  - *Netlify Blobs en producción*: el servidor local no devuelve la etiqueta al leer (la prueba se la
    pone), y que las lecturas vean siempre lo último escrito solo se puede comprobar allí.
  - *La sesión*: la comprobación del token de Firebase con las claves de Google se sustituye en las
    pruebas.

## Antes de publicar un cambio

1. `python herramientas/sellar_version.py` (sin esto, los móviles con la app instalada no reciben la versión nueva).
2. `python pruebas/e2e.py` (tiene que salir todo PASS).
3. Si se ha tocado `netlify/`: `npm run probar:servidor` (todo `pass`).
4. `git push` (el sitio de Netlify está conectado al repositorio y publica `app/` y las funciones con
   cada cambio en `main`, según `netlify.toml`). A mano también vale: `netlify deploy --prod`.

Los recordatorios solo salen del sitio publicado: un cambio en las funciones no se ve avisar hasta que
está en producción (ver «Probarlo»).

Ojo: si el repositorio es privado, el plan gratuito de Netlify solo construye los commits cuyo
autor sea el dueño de la cuenta (en GitHub, `raul2000gomez`); cualquier otro autor se queda en
«Build blocked: Unrecognized Git contributor» y no se publica. Por eso el repositorio es público.
