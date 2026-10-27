# Reproducción híbrida de IVOX

La búsqueda principal ahora usa **ytmusicapi** con filtros de canciones y artistas, y respaldo de YouTube Data API. El `videoId` que devuelve alimenta la cadena de reproducción descrita aquí. Configuración, paginación y destino futuro RAW: [README.md](README.md). La corrección de búsqueda no sustituye yt-dlp ni modifica cuentas, planes o biblioteca.

## Corrección actual: cadena de cinco niveles

Esta sección sustituye el diagnóstico anterior de «audio principal pendiente» cuando el proveedor devuelve un MP4 muxado válido. `audioStreams: []` por sí solo no significa que el vídeo carezca de audio.

1. **yt-dlp**, audio puro. La dependencia está fijada en `requirements.txt` (`2026.10.26`). `GET /api/audio/{videoId}` ejecuta el equivalente a `yt-dlp -f "bestaudio[ext=m4a]/bestaudio" -g --no-playlist --no-warnings --extractor-args youtube:skip=hls,dash URL` con timeout de 15 segundos (m4a primero porque iOS lo reproduce siempre; omitir HLS/DASH ahorra ~0,4 s). Se añaden opciones para ignorar configuraciones externas y evitar caché en disco. El servidor mantiene dos procesos de yt-dlp ya importados en espera (ahorra ~0,7 s por canción nueva); cada uno recibe el ID por stdin, sin shell, y se descarta tras usarse. La URL HTTPS obtenida se guarda en memoria y en la tabla `audio_url_cache` de SQLite, hasta 30 minutos o su caducidad, así que sobrevive a reinicios. Al empezar a sonar una canción, el navegador pide `POST /api/audio/prefetch` con las dos siguientes de la cola (máximo dos resoluciones en segundo plano a la vez) para que «Siguiente» suene casi al instante; y el backend transmite el audio con Range/206 y `Content-Type: audio/*`. Ante fallo devuelve HTTP 502 con `{"error":"audio_unavailable"}`. No se envía una URL de googlevideo al navegador. `?resolve=1` permite preparar la fuente devolviendo solo la URL local `/api/audio/{videoId}`.
2. **Piped audio puro**: se elige un `audioStreams` compatible, prefiriendo MP4 en navegadores que lo admiten y el bitrate mayor dentro del formato. WebM es la alternativa.
3. **Piped muxado**: si no hay audio puro o falla en el navegador, se usa el primer MP4 de `videoStreams` con **`videoOnly === false`**. Se descartan siempre los que contienen solo vídeo. El reproductor sigue siendo `<audio>`: no se crea un elemento de vídeo ni un iframe.
4. **Audius**: tras fallar las fuentes de la referencia, se busca por título y artista. Solo se cambia automáticamente a un resultado con título y artista coincidentes; se indica la fuente activa. Otras versiones y remixes requieren elección explícita. No se afirma que el catálogo de Audius incluya la grabación solicitada.
5. **No disponible, elige otro**: se informa del fallo y se prepara el siguiente elemento de la cola. El modo automático ofrece alternativas de Audius después de varios fallos de Piped; el usuario también puede seleccionarlas inmediatamente.

El frontend prueba yt-dlp primero. Si la resolución o el stream falla, pasa a Piped; si falla el audio puro intenta el muxado del mismo vídeo antes de abandonar esa referencia. La fuente y el nivel efectivos aparecen en pequeño: `yt-dlp · Nivel 1`, `Piped · Nivel 2`, `Piped · Nivel 3 · MP4 muxado` o `Audius · Nivel 4`. Las referencias guardadas no cambian por el motor que logró reproducirlas.

### Por qué sirve itag 18

En el JSON real de `/streams/dQw4w9WgXcQ` se comprobó `audioStreams: []` y una entrada `itag: 18`, `mimeType: video/mp4`, `videoOnly: false`. Itag 18 identifica en este caso un MP4 que contiene **pista de vídeo y pista de audio**. Un navegador con los códecs necesarios puede reproducir su audio mediante `<audio>`, sin presentar vídeo. No se transcodifica ni se convierte el contenedor: se mantiene su tipo real `video/mp4` y el navegador elige la pista sonora. Esto utiliza más ancho de banda que audio puro y no garantiza compatibilidad en todos los dispositivos.

La comprobación HTTP real obtuvo **206 y 65.536 bytes** desde el proxy de Piped. Las URLs de Piped siguen pasando por el relay autenticado de IVOX: así funcionan también desde el iPhone sin que `127.0.0.1:8081` apunte al propio teléfono. Las URLs de yt-dlp nunca aparecen en JSON, frontend ni logs.

### Instalar y actualizar yt-dlp

```powershell
python -m pip install -r requirements.txt
```

En esta máquina se instaló de forma aislada en `.vendor/` (excluido de Git), sin modificar paquetes globales. El backend usa ese directorio como respaldo cuando el intérprete no tiene yt-dlp instalado.

Si YouTube cambia y deja de funcionar la extracción, actualiza yt-dlp y repite las pruebas. En un entorno virtual o instalación normal, el comando es **`pip install -U yt-dlp`** (preferiblemente `python -m pip install -U yt-dlp` para usar el intérprete correcto). Para la instalación aislada de esta entrega:

```powershell
python -m pip install --upgrade --target .vendor yt-dlp
```

Después de validar la nueva versión, actualiza también la versión fijada en `requirements.txt` y reinicia IVOX. Actualizar yt-dlp no garantiza disponibilidad: puede requerir componentes adicionales del proyecto o recibir rechazos del proveedor. El timeout, el error JSON y los niveles siguientes continúan funcionando.

### Verificación específica de niveles

**Comprobado en Chromium el 27 de octubre de 2026:** se reprodujeron los niveles **1, 3 y 4**, con el tiempo del `<audio>` avanzando. No se trata de un resultado basado en un único nivel. Quince pruebas de integración/unitarias y la regresión de interfaz local/admin están aprobadas.

| Caso real | Resultado observado |
| --- | --- |
| Feid — Prohibidox (`06LYe7q0MnE`) | **Nivel 1, yt-dlp**, audio reproducido |
| Rick Astley — Never Gonna Give You Up (`dQw4w9WgXcQ`), cadena normal | **Nivel 1, yt-dlp**, audio reproducido |
| OhioJimbo — Tourist Trap (`SBKJNIRdZLE`), canal con **19.700 suscriptores** según Piped | **Nivel 1, yt-dlp**, audio reproducido |
| Rick Astley, con fallo de yt-dlp inducido solo en el proceso de prueba | **Nivel 3, Piped muxado**, audio reproducido; sin iframe ni elemento de vídeo |
| LUNA (Another Mexican Dude Remix), Audius | **Nivel 4, Audius**, audio reproducido |

El endpoint real de yt-dlp respondió **206**, `Content-Type: audio/webm`, `Content-Range: bytes 0-1023/3215662` y 1.024 bytes al solicitar ese rango. No se observaron URLs de googlevideo en las solicitudes del navegador. Favoritos y playlist de la referencia de Rick Astley reaparecieron en una segunda sesión de la misma cuenta. No hubo errores JavaScript de página.

**Nivel 2:** cubierto con respuestas simuladas que verifican prioridad del audio puro, bitrate, fallback muxado y rechazo de `videoOnly=true`; **no se afirma reproducción real por este nivel** porque las respuestas de Piped consultadas seguían sin `audioStreams`. No se ha probado un iPhone físico, sonido mediante micrófono ni continuidad con pantalla bloqueada.

`python tests/audio_levels_live.py` ejecuta el caso Rick Astley forzando un fallo de yt-dlp **solo en el servidor temporal de prueba**, prueba la cadena normal con un resultado real de Feid, el vídeo antiguo y un canal pequeño cuyo número de suscriptores se obtiene de Piped, comprueba el respaldo Audius y la sincronización de favoritos/playlists entre sesiones. El informe sin URLs firmadas ni credenciales se guarda en `test-results/audio-levels-report.json`. El resultado de cada nivel debe leerse en ese informe; las comprobaciones unitarias con datos simulados no equivalen a reproducción real.

## Historial de la entrega anterior (antes del fallback muxado)

## Estado comprobado — 27 de octubre de 2026

La reproducción principal ya **no carga YouTube IFrame API**. Se usa un elemento HTML5 `<audio>` persistente, con controles de reproducción/pausa, progreso, volumen, anterior/siguiente y cola. La interfaz y la política CSP impiden incrustar iframes. Las cuentas, contraseñas, sesiones, administración, roles, planes y playlists existentes se conservan.

**Funcionando y comprobado:** Docker Compose con Piped propio en localhost, `/healthcheck`, búsqueda real de Feid con título/canal/miniatura/duración, paginación, favoritos y playlists privados, lectura desde otra sesión y reproducción real del respaldo Audius en Chromium. Se verificó que el tiempo del audio avanza y la reproducción persiste al navegar. A 390 × 844 px, con UA y gestos de iPhone emulados en Chromium, se comprobó que el audio permanece pausado hasta un gesto y después reproduce y persiste.

**Limitación pendiente de la fuente principal:** en los tres vídeos de Feid consultados directamente, Piped devolvió `audioStreams: []`. La búsqueda funciona y la instancia está sana, pero **no se ha conseguido reproducir audio de Feid desde Piped**. IVOX muestra el error, prepara el siguiente resultado y ofrece Audius. No usa un stream de vídeo como si fuera audio ni vuelve al iframe. No se declara completo el objetivo de reproducir el catálogo principal por Piped hasta resolver/verificar ese comportamiento del proveedor.

**Respaldo comprobado:** la búsqueda de Feid en Audius devolvió 19 pistas reproducibles en la prueba, incluida **FEID, ATL Jacob - LUNA (Another Mexican Dude Remix)**. Se verificó HTTP 206, tipo `audio/mpeg`, decodificación y avance de reproducción en navegador. Es un remix alojado en Audius, **no una afirmación de que Audius tenga la grabación original o el catálogo oficial de Feid**. Cada resultado indica la fuente.

El navegador de prueba es automatizado; no se comprobó un iPhone físico ni se verificó sonido mediante micrófono/altavoces. La comprobación de reproducción se basa en estado no pausado, datos de audio válidos y avance de tiempo.

## Arrancar y reiniciar Piped

Necesitas Docker Desktop funcionando con contenedores Linux y Docker Compose. Desde PowerShell:

```powershell
cd C:\Users\luck_\Desktop\onda-music
python scripts/setup_piped.py
docker compose up -d
docker compose ps
```

`setup_piped.py` genera una contraseña aleatoria para PostgreSQL y completa las variables de Piped en `.env`; preserva claves existentes y no imprime secretos. No hay una contraseña de base de datos predeterminada. El proyecto Compose se llama **onda-piped**, por lo que sus comandos no afectan a otros proyectos Docker.

Servicios:

| Servicio | Acceso | Uso |
| --- | --- | --- |
| Piped backend | `127.0.0.1:8080` | Búsqueda y resolución de streams |
| Piped proxy | `127.0.0.1:8081` | Transporte de audio e imágenes |
| PostgreSQL | Red interna de Compose | Datos operativos de Piped |
| bg-helper | Red interna de Compose | Componente requerido por Piped |

Solo se publican puertos de loopback. No se instala un frontend de Piped ni se abre acceso exterior. La biblioteca y cuentas de IVOX siguen en SQLite; **no se envían a la base de datos de Piped**.

Comprobar salud:

```powershell
Invoke-WebRequest http://127.0.0.1:8080/healthcheck
```

Reiniciar y consultar logs:

```powershell
docker compose restart piped proxy bg-helper
docker compose logs --tail 60 piped
```

Actualizar imágenes después de revisar los cambios del proveedor:

```powershell
docker compose pull
docker compose up -d
```

Las etiquetas `latest` pueden cambiar. Las imágenes realmente ejecutadas durante esta entrega se registran en `test-results/piped-images.json`. Una actualización puede corregir o introducir incompatibilidades con YouTube; debe repetirse la prueba de audio.

Detener:

```powershell
docker compose stop
```

No uses `down -v` si quieres conservar la base operativa de Piped. No cambies `PIPED_DB_PASSWORD` después de crear el volumen sin actualizar también la contraseña dentro de PostgreSQL.

## Configurar y arrancar IVOX

Valores de `.env`:

```env
PIPED_API_URL=http://127.0.0.1:8080
PIPED_PROXY_URL=http://127.0.0.1:8081
AUDIUS_API_URL=https://api.audius.co
ALLOW_YOUTUBE_EXTERNAL=0
ONDA_SECURE_COOKIE=0
```

La contraseña generada `PIPED_DB_PASSWORD` queda en ese mismo archivo. `.env` está excluido de Git. La antigua `YOUTUBE_API_KEY` puede permanecer allí, pero **no es necesaria ni se usa en el recorrido actual de reproducción o búsqueda**. No se muestra en el frontend, respuestas ni logs.

```powershell
python server.py --port 8765
```

Abre http://127.0.0.1:8765. Si hay un servidor anterior, detenlo antes con Ctrl+C. Reinicia IVOX tras modificar `.env`.

### Acceso desde red local y Tailscale

IVOX ahora escucha en **`0.0.0.0:8765`**, en todas las interfaces IPv4 del equipo. El acceso local sigue siendo `http://127.0.0.1:8765`. Desde otro dispositivo usa `http://IP_LOCAL_DEL_EQUIPO:8765` en la misma red, o `http://IP_TAILSCALE_DEL_EQUIPO:8765` si ambos dispositivos están conectados a tu red Tailscale. `0.0.0.0` es la dirección de escucha, no una dirección para abrir en el navegador.

El acceso remoto depende de que el firewall de Windows y las reglas de Tailscale permitan la conexión al puerto 8765. Este cambio no modifica esas reglas ni configura reenvío de puertos en el router. Los servicios internos de Piped permanecen limitados a localhost; el audio y las imágenes se sirven al dispositivo remoto a través de IVOX. Si configuras HTTPS, activa `ONDA_SECURE_COOKIE=1`; para el acceso HTTP descrito mantenlo en `0`.

### Instalar IVOX como PWA

IVOX incluye `manifest.json`, iconos PNG de 192 y 512 px, etiquetas iOS y un service worker para el app shell. El modo `standalone` permite abrirla en su propia ventana desde el icono, sin las barras habituales del navegador. Es una aplicación web instalable, no una aplicación nativa de tienda.

- **iPhone:** abre IVOX en Safari → Compartir → Añadir a pantalla de inicio. Si aparece «Abrir como app web», actívalo y pulsa Añadir.
- **Android:** abre IVOX en Chrome y usa «Instalar aplicación» o «Añadir a pantalla de inicio», según la versión del navegador.
- **PC:** abre `http://127.0.0.1:8765` en Chrome/Edge; se puede registrar el service worker porque loopback se considera un origen seguro para desarrollo.

Para usar el service worker y la instalación completa desde otro dispositivo, utiliza **HTTPS con certificado confiable**, también si accedes mediante Tailscale. `http://IP_TAILSCALE:8765` o `http://IP_LOCAL:8765` permite acceder a IVOX, pero no es un contexto seguro equivalente a localhost. Tailscale transporta la conexión de forma privada, pero una URL HTTP remota no se convierte automáticamente en HTTPS. Esta entrega no configura certificados ni modifica Tailscale. Safari permite añadir webs al inicio sin exigir un service worker, pero eso no activa las capacidades que requieren un origen seguro.

Solo se cachean HTML genérico, CSS, JavaScript, manifest e iconos. Se excluyen las API, cuentas, biblioteca, audio, peticiones Range y `/admin`. El shell puede cargar sin conexión; **buscar, iniciar sesión, sincronizar y escuchar siguen necesitando conexión**. No se añade reproducción offline ni se cambia la gestión de cuentas o sesiones. El registro se realiza en `/pwa.js`, cargado al final del HTML para respetar la política CSP sin permitir scripts inline.

Comprobación en PC: `python tests/pwa_check.py`. Comprueba URLs/MIME, dimensiones de iconos, manifest reconocido por Chromium, service worker activo, caché limitada al shell y carga offline del shell. La instalación en un teléfono físico queda pendiente. Guías: [instalación PWA](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable) y [añadir una app web en iPhone](https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios).

## Cómo funciona

La búsqueda consulta `/search?q=...&filter=music_songs` de tu Piped. La siguiente página consulta `/nextpage/search` con el cursor devuelto por Piped. IVOX firma y vincula los cursores a búsqueda/fuente para que no se reutilicen con otra consulta. La caché de 10 minutos y la deduplicación de peticiones simultáneas reducen el tráfico; se limita la frecuencia de búsquedas sin caché. El navegador cancela/ignora solicitudes obsoletas.

Para Play, el servidor consulta `/streams/{videoId}` y elige entre `audioStreams` compatibles, priorizando el formato admitido por el navegador y el bitrate más alto dentro de ese formato. La selección en iOS favorece MP4 cuando está disponible. No se utiliza un vídeo multiplexado como sustituto silencioso.

El audio se transmite a través de IVOX con un ticket temporal vinculado al usuario y sesión autenticada, conservando Range. La URL de proveedor no se guarda en SQLite, no se descarga a disco y no se convierte IVOX en un proxy de URLs arbitrarias. Solo se admiten streams del proxy de Piped configurado; las redirecciones públicas de Audius se validan y no pueden apuntar a IPs privadas. La suspensión/expiración de sesión también se comprueba durante la transmisión. Los bytes ya recibidos por el dispositivo no pueden revocarse.

Así se evita que un iPhone intente contactar con `127.0.0.1:8081` de su propio dispositivo: audio e imágenes locales pasan por la instancia de IVOX a la que está conectado. El acceso real desde iPhone sigue necesitando configurar HTTPS/red privada; no se ha abierto automáticamente ningún puerto al exterior.

## Respaldo y modo degradado

- El selector permite elegir Piped, Audius o Piped con respaldo.
- Si la búsqueda de Piped falla en modo automático, IVOX consulta Audius y lo **anuncia explícitamente**. Una búsqueda sin coincidencias no se rellena con contenido inventado.
- Si tres referencias distintas de Piped no ofrecen audio en modo automático, se muestran alternativas de Audius. El usuario elige qué versión reproducir; no se sustituye silenciosamente la canción por un remix.
- En modo Piped explícito se conserva la elección de fuente. Ante audio no disponible se muestra el error, se prepara la siguiente referencia y se ofrece «Buscar alternativas en Audius».
- Con `ALLOW_YOUTUBE_EXTERNAL=1` aparece una opción para abrir un vídeo de Piped en YouTube **en otra pestaña**. El valor predeterminado es `0`. Nunca se incrusta YouTube como fallback ni se habilita ese enlace automáticamente.

Audius se consulta por su API pública, sin clave, en `/v1/tracks/search` y `/v1/tracks/{id}/stream`, con `app_name=IVOX`. No se incluyen pistas señaladas como no disponibles, no reproducibles o con acceso restringido. La API pública puede limitar peticiones o cambiar disponibilidad. No hay credenciales ni acciones sobre una cuenta de Audius del usuario.

## Biblioteca, planes y dispositivos

Se guardan referencias con proveedor (`piped` o `audius`), identificador, título, canal/artista, miniatura y duración. Los favoritos, playlists, historial y escuchar después se consultan siempre por usuario. La migración conserva referencias anteriores de YouTube como referencias de Piped con el mismo ID de vídeo; no borra cuentas ni archivos. Los metadatos antiguos pueden necesitar actualización al reproducir.

La importación permanece solo en `/files`. No hay mensajes que exijan importar para empezar. En Free y Premium se puede buscar y reproducir; las cuotas de playlists y archivos locales son funciones propias de IVOX. No se vende «sin anuncios» como ventaja de Premium ni se promete disponibilidad permanente.

El elemento `<audio>` permanece al navegar entre vistas. La cola vive en la sesión del navegador, no se sincroniza la posición de reproducción. El primer toque en cualquier parte de la app reproduce un silencio corto (`/silence.wav`) en el elemento de audio y reanuda un `AudioContext`, lo que lo desbloquea en iOS/Chrome. Además, el Play de nivel 1 asigna la fuente y llama a `play()` en el mismo clic, sin esperas previas, así que basta un toque. La política del navegador manda. No se promete reproducción con pantalla bloqueada, en segundo plano ni offline.

## Piped, condiciones y limitaciones

Piped es un proyecto independiente que obtiene contenido de YouTube mediante su propio backend y proxy; no es la API oficial ni implica una licencia del catálogo. El uso de extracción/proxy y separación de audio entra en conflicto con las condiciones que YouTube impone a sus servicios y clientes. Por eso este enfoque es una **zona gris respecto a esas condiciones**, no una autorización de YouTube ni una garantía de legalidad para cualquier uso. Tener una instancia propia no elimina derechos de autor ni las restricciones aplicables.

Esta configuración se destina al **uso personal, privado y no comercial con un grupo pequeño de amigos**. No se publica como servicio abierto. Piped no inserta los anuncios del reproductor oficial, pero IVOX no garantiza contractualmente ausencia de publicidad o mensajes promocionales incorporados en el propio contenido, ni disponibilidad del proveedor.

La IP de salida de **tu servidor Piped/proxy** es la que realiza las solicitudes de origen; YouTube puede bloquearla, requerir comprobaciones o cambiar formatos, y una instancia aparentemente sana puede buscar pero no devolver streams de audio. Eso se ha observado en esta verificación. IVOX no configura rotación de IP, servicios de captcha ni mecanismos para sortear esos bloqueos. Si ocurre, revisa logs/versiones, repite la comprobación y usa Audius mientras tanto.

Referencias técnicas oficiales de los proyectos: [Piped self-hosting](https://docs.piped.video/docs/self-hosting/), [Piped API](https://docs.piped.video/docs/api-documentation/), [Audius API](https://docs.audius.co/api/). Para el contexto de condiciones: [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies).

## Siete pruebas solicitadas

Ejecutado: `python tests/online_live_check.py`, con dos sesiones y una base de datos temporal. El resultado íntegro está en `test-results/hybrid-live-report.json`; no contiene contraseñas ni claves.

| Prueba | Resultado |
| --- | --- |
| 1. Levantar Piped y comprobar salud | **Comprobado**: Compose activo y `/healthcheck` correcto |
| 2. Buscar Feid, con metadatos reales | **Comprobado**: 20 resultados de Piped, imagen, canal y duración |
| 3. Reproducir Feid por Piped sin vídeo | **No logrado**: `audioStreams` vacío. **Audius sí reproduce audio real**, con avance de tiempo, sin iframe |
| 4. Favorito y playlist | **Comprobado** con referencia real de Piped |
| 5. Otra sesión con la misma cuenta | **Comprobado**: favorito y playlist conservados |
| 6. Viewport iPhone, gesto y persistencia | **Comprobado con Audius en Chromium emulado**; no iPhone físico |
| 7. Contenido no disponible y siguiente | **Comprobado** con fallo inducido aislado al servidor de prueba; mensaje y preparación del siguiente |

Se comprobó además la ausencia de peticiones del reproductor a `youtube.com` y de cualquier iframe. Capturas: `hybrid-search-desktop.png`, `hybrid-player-desktop.png`, `hybrid-library-mobile.png` y `hybrid-player-mobile.png` en `test-results/`. Se inspeccionó la captura del reproductor móvil.

Las pruebas automatizadas HTTP de cuentas, administración, aislamiento, migración, fallback, caché, cursores y tickets: `python -m unittest discover -s tests -v` (**11 aprobadas**). La regresión de interfaz local/admin: `python tests/browser_check.py` (**aprobada**).
