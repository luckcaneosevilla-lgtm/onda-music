# Ivox-Astra 6.0

La versión 6.0 hace que la música suene igual de bien en el ordenador que en el iPhone: todos los navegadores reciben el audio por la ruta nativa del servidor (los enlaces de YouTube que resuelve el servidor van ligados a su IP y fallaban en escritorio). La pantalla de novedades estrena un cielo nocturno con estrellas fugaces detrás de Anubis.

## Novedades de la versión 5.0


La versión 5.0 reproduce siempre la canción que eliges (si falla, no salta sola a otra), mantiene la cola sonando con el iPhone bloqueado, guarda descargas dentro de la app para escucharlas sin conexión y evita que el registro y el inicio de sesión se queden esperando al resto de escrituras. Anubis despierta: baila y sus ojos brillan en azul.

## Novedades de la versión 4.0


La versión 4.0 reduce el prefijo preparado de 512 a 64 KiB, reserva capacidad para la canción elegida al pulsar Play y conserva los mismos offsets MP4 entre las peticiones de Safari. Comparte las resoluciones simultáneas y aplica los reintentos una sola vez por intento. El nombre, el arranque, el manifiesto y las novedades muestran **Ivox-Astra 4.0**. Análisis, decisiones y resultados verificables: [Ivox-Astra 4.0](docs/ASTRA_4.md).

## Novedades de la versión 3.0

- **Reproducción ultrarrápida en iOS:** durante la búsqueda, el backend prepara un prefijo del AAC-LC original y coloca los metadatos MP4 al principio. La caché parcial sirve el inicio y continúa con el mismo audio mediante HTTP Range, sin recodificar toda la canción ni cambiar de archivo a los 30 segundos. Esta ruta sustituye la conversión completa con FFmpeg; el propietario había confirmado la mejora inicial de AAC-LC/fast-start en Safari de su iPhone real. Detalles de la nueva ruta: [caché parcial continua](docs/PARTIAL_AUDIO_CACHE.md).
- **Búsqueda con perfiles de artistas y radio infinita:** descubre biografías, canciones populares y artistas relacionados; continúa escuchando recomendaciones al terminar tu cola.
- **Interfaz Anubis con letras y gestos:** reproductor completo, letras sincronizadas cuando están disponibles, cola reordenable y gestos para abrir, cerrar y cambiar de canción.

La caché se prepara en segundo plano y tiene límites de espacio, duración y concurrencia. Si una canción aún no está lista, se conserva el proxy como respaldo; no se garantiza un tiempo de arranque fijo para todas las canciones o redes. Detalles y pruebas: [caché AAC/fast-start](docs/PREPARED_AUDIO_CACHE.md) y [mediciones del reproductor](docs/AUDIO_MEMORY_CACHE_VALIDATION.md).

Descubrimiento musical online para uso privado: búsqueda principal con **ytmusicapi / YouTube Music**, respaldo **YouTube Data API**, y reproducción con **yt-dlp**, Piped y Audius mediante **HTML5 audio**. Se conservan cuentas, biblioteca privada, administración, roles y planes. El recorrido principal ya no depende de importaciones ni de un reproductor de YouTube.

**Reproducción:** copias del dispositivo y archivos AAC preparados → proxy de audio con InnerTube y respaldo yt-dlp → Piped, si está configurado → Audius → error y siguiente. Safari/iOS utiliza el proxy del mismo origen; las URLs temporales de Google permanecen en el backend. [README_SECCION_REPRODUCCION.md](README_SECCION_REPRODUCCION.md) conserva las verificaciones de las etapas anteriores.

## Arranque en Windows

Python 3.12 o posterior y Docker Desktop con contenedores Linux:

```powershell
cd C:\Users\luck_\Desktop\onda-music
python scripts/setup_piped.py
python -m pip install -r requirements.txt
docker compose up -d
python server.py --port 8765
```

Abre http://127.0.0.1:8765. IVOX escucha en `0.0.0.0:8765`: también puedes acceder mediante la IP local o Tailscale del equipo, si las reglas de red y firewall lo permiten. Los puertos Docker de Piped siguen limitados a localhost. No hay contraseñas predeterminadas: el script genera la contraseña de PostgreSQL y conserva los secretos existentes en `.env`. No se requiere la clave de YouTube para este recorrido.

## Pantallas

- `/`: búsqueda online real con filtros Canciones/Artistas, recientes privados y selector de fuentes. Si la búsqueda nombra a un artista (p. ej. «Feid», «Olivia Dean»), aparece primero su perfil: foto, nombre, oyentes mensuales, suscriptores, Reproducir/Aleatorio/Radio, sus 5 canciones populares y artistas similares; después, las canciones. El respaldo automático anuncia el cambio de fuente.
- `/artist/<id>`: perfil completo del artista (banner, biografía, populares, «Fans también escuchan»).
- `/library`: favoritos, playlists, historial y escuchar después; se guardan referencias de proveedor, no audio descargado.
- `/plan`: estado y límites del plan. Buscar y reproducir están disponibles en Free y Premium.
- `/admin`: usuarios, resumen real, Premium manual, suspensión/reactivación, roles y auditoría.
- `/files`: importaciones y biblioteca de archivos anteriores, como opción secundaria.
- `/privacy`: explicación de datos y eliminación de la biblioteca online propia.

La barra de audio permanece al navegar. Incluye progreso, play/pausa, volumen (cuando el navegador lo admite), me gusta, cola y anterior/siguiente. En iPhone basta un toque en Play: el primer toque en la app desbloquea el audio. No se promete reproducción con pantalla bloqueada ni offline.

### Reproductor

- **Un solo `<audio>`** para canciones online, copias del dispositivo y archivos importados. Cada cambio de canción aborta con un `AbortController` las peticiones de la anterior; los toques rápidos en anterior/siguiente solo mueven la selección y la canción se carga 200 ms después del último toque, cancelando la descarga previa.
- **Errores:** cadena copia local → yt-dlp → Piped (solo si hay instancia) → misma canción en Audius. Si todo falla se muestra el motivo y se salta a la siguiente; tras 3 fallos seguidos se detiene. Un vigilante trata como fallida una canción que no empieza en 15 s y reabre en el mismo segundo un audio atascado 20 s.
- **Safari:** la versión 3.0 prioriza archivos AAC-LC/MP4 ya preparados con los metadatos al principio. El audio de red evita la interceptación del Service Worker. Si la duración nativa es incoherente, se usa la del catálogo sin buscar artificialmente al final del archivo. La caché preparada admite `206 Partial Content`, `Content-Range` y `Accept-Ranges: bytes`.
- **Autoplay / radio infinita:** cuando quedan 1-2 canciones, el servidor pide la radio de YouTube Music (`get_watch_playlist(radio=True)`) de la última canción y añade 10 similares, marcadas «Radio». Se activa o desactiva en el reproductor completo. Cualquier canción o artista ofrece «Iniciar radio».
- **Reproductor completo** (toca la portada o el título, o desliza hacia arriba): portada grande con el color dominante de la carátula, letras sincronizadas (`get_lyrics(timestamps=True)`; toca una línea para saltar), cola reordenable arrastrando (con dedo o con flechas del teclado), me gusta con corazones flotantes o doble toque en la portada, deslizar portada para cambiar de canción y hacia abajo para cerrar, visualizador radial opcional (Web Audio; no se ofrece en iPhone porque silenciaría el audio en segundo plano). Atajos: espacio, Mayús+←/→, L.

### Búsqueda musical

La búsqueda predeterminada usa **`ytmusicapi==1.12.3`**, sin cookies ni login de Google, con `YTMusic.search(filter='songs')` o `filter='artists'`. Las canciones conservan el título, lista de artistas, álbum, miniatura, duración y `videoId` devueltos por YouTube Music. «Ver canciones» en un artista consulta su nombre con el filtro de canciones. Los resultados no son inventados ni se confunde un artista con un vídeo reproducible.

El `videoId` se guarda como referencia compatible con la biblioteca existente y se pasa al endpoint de **yt-dlp** para reproducir. Buscar y reproducir son procesos separados: no se cambia la cadena de resolución de audio ni se añade un iframe.

Ante fallo de ytmusicapi se intenta **YouTube Data API** con `YOUTUBE_API_KEY` del backend. Si también falla o falta la clave, se mantienen Piped/Audius como respaldo de canciones; la fuente real y el cambio se indican en la interfaz. El filtro Artistas de YouTube Data API devuelve canales, identificados como tales, sin afirmar que todos sean artistas oficiales. Una respuesta válida sin coincidencias se presenta vacía, no se rellena con canciones ficticias.

La consulta a ytmusicapi vive en un proceso trabajador persistente (no uno por búsqueda), con peticiones concurrentes y límite de 10 s por petición HTTP y 25 s por operación. Perfiles de artista (30 min), radios (10 min) y letras (24 h) tienen caché propia y un límite de 60 peticiones por minuto, aparte del de búsquedas. Se conserva caché de 10 minutos por consulta/filtro/fuente, límite de frecuencia, cancelación de respuestas antiguas y paginación con cursores firmados. ytmusicapi no expone aquí un cursor público de continuación: se amplía su límite de 20 en 20 y se presenta el siguiente tramo, hasta **100 resultados** por consulta. Para más resultados, ajusta la búsqueda. La localización interna del cliente se fija a inglés porque se observó una respuesta vacía de canciones con `language='es'` en esta versión; los títulos originales, acentos y la interfaz española se conservan. Cada consulta del cliente tiene límite de tiempo y el proceso completo se corta a los 25 segundos.

Instala con `python -m pip install -r requirements.txt`. En esta máquina la dependencia y sus paquetes se instalaron en `.vendor/`, aislados de los paquetes globales. Esta biblioteca es un cliente no oficial y puede necesitar actualizaciones si cambia el proveedor. Prueba real y capturas: `python tests/music_search_live.py`, informe en `test-results/ytmusic-live-report.json`.

## Rendimiento medido

`python tests/latency_live.py` arranca IVOX en una base temporal, busca consultas reales y mide hasta los primeros bytes de audio; `python tests/player_features_live.py` mide en Chromium desde el clic hasta el evento `playing`. Resultados en este equipo (Windows, misma red) del 28/10/2026, antes y después de esta versión:

| | Antes | Ahora |
| --- | --- | --- |
| Búsqueda de canciones | 2,0–2,4 s | 0,5–0,7 s |
| Canción precargada → primeros bytes | mediana 2,95 s (0 de 12 bajo 1,5 s) | mediana 0,49 s (12 de 12 bajo 1,5 s) |
| Canción precargada → suena, en navegador | — | 0,5–0,8 s |
| Canción **no** precargada → primeros bytes | mediana 3,3 s | mediana 2,4 s |

Cómo: un grupo de 3 procesos yt-dlp **calientes** que conservan en memoria el JavaScript del reproductor y las funciones de firma (antes se lanzaba un proceso nuevo por canción, ~2,7 s); los toques del usuario tienen prioridad y ninguna precarga nueva empieza mientras se resuelve una canción pedida; se precargan las 5 primeras canciones de cada búsqueda, 2 populares del artista, las 2 siguientes de la cola y la fila bajo el dedo (`pointerdown`). La comprobación de Piped ya no bloquea (en Windows un puerto local cerrado tardaba 2 s por petición). Una canción que nadie precargó depende del tiempo de respuesta de YouTube para extraerla (1–2 s desde esta conexión): no baja de 1,5 s de forma fiable.

**Sobre aria2c:** no se usa. IVOX no descarga archivos con yt-dlp: obtiene la URL (`-g`) y retransmite los bytes a medida que llegan, con soporte de rangos; los primeros 16 KB llegan en ~0,3 s desde que la URL está resuelta. aria2c solo acelera descargas completas a disco (y fragmentadas, que con `skip=hls,dash` no hay), así que añadiría una dependencia sin efecto en el tiempo hasta que suena la música. `IVOX_TRACE=1` imprime la duración de cada petición de la API para diagnosticar latencias.

yt-dlp extrae hoy con su cliente sin JavaScript (`visionos`) porque no hay entorno JS (deno) instalado; yt-dlp lo marca como obsoleto. Si YouTube lo retira, habrá que instalar deno y el paquete `yt-dlp-ejs` en el servidor y en el Dockerfile.

## Primer administrador

Registra tu cuenta en IVOX con una contraseña de al menos 12 caracteres. Abre otra terminal en esta carpeta:

```powershell
python server.py bootstrap-admin --email "tu-email@ejemplo.com"
```

Introduce la contraseña de esa cuenta cuando se solicite; no se verá mientras escribes. El comando exige acceso local, verifica la contraseña y solo funciona si no existe un administrador activo. Registra el cambio en la auditoría. No mantiene una transacción de escritura mientras espera la contraseña. Recarga IVOX para entrar en `/admin`.

Los siguientes administradores se conceden en el panel. Nadie modifica su propio rol, plan o estado, y siempre debe quedar un administrador activo. No se asignó una cuenta de prueba como administrador en tus datos reales.

### Si olvidas tu contraseña

Con acceso local al ordenador y a los datos del servidor:

```powershell
python server.py recover-admin --email "tu-email@ejemplo.com"
```

Abre el enlace privado que imprime y elige una contraseña nueva. Caduca en 10 minutos, es de un solo uso y solo sirve para la cuenta indicada. Al completar el formulario se activa su rol de administrador, se revocan todas sus sesiones anteriores y se abre `/admin`. La creación y consumo del enlace se auditan sin registrar contraseñas ni tokens en texto plano. No compartas ese enlace. No hay una API pública para generar enlaces: solo el comando local con acceso a SQLite puede hacerlo. Protege el acceso al ordenador y a la carpeta de datos. Las contraseñas existentes no pueden consultarse porque se guardan como hash.

## Planes y administración

Los roles (`admin`, `user`) y planes (`free`, `premium`) son independientes. El panel concede Premium indefinido, hasta fecha concreta, prueba de N días o ampliación; también lo retira y suspende/reactiva cuentas. Retirada y suspensión requieren confirmación. Cada cambio guarda actor, fecha, motivo opcional y valores anteriores/posteriores. Se muestran los últimos 200 cambios; los anteriores se conservan en SQLite.

| Regla propia de IVOX | Free inicial | Premium inicial |
| --- | --- | --- |
| Playlists | 10 | 200 |
| Almacenamiento local opcional | 1 GB | 20 GB |
| Archivo local individual | 50 MB | 250 MB |

El administrador puede cambiar los límites. La cuota de playlists se comparte entre las secciones online y local. Al perder Premium se conserva el contenido y se bloquean nuevas incorporaciones que excedan límites. No se bloquea escuchar ni editar listas existentes. No se ofrecen cobros, eliminación contractual de publicidad ni funciones inexistentes.

La autorización se calcula en el servidor con el plan efectivo y vencimiento actual. Un proveedor de pagos futuro deberá verificar y deduplicar sus eventos y actualizar concesiones con auditoría; no hará falta cambiar las comprobaciones de acceso. `plans.features` reserva reglas para funciones futuras, sin activarlas por sí solo.

## Despliegue de IVOX 3.0

IVOX se despliega en **Northflank** desde la rama `main`. El `Dockerfile` instala FFmpeg y el runtime de JavaScript de yt-dlp. Los cambios generan una nueva compilación y el cliente ofrece recargar cuando detecta una actualización. La versión visible es **3.0**; el identificador interno de compilación sigue cambiando con el contenido del código.

Para un despliegue Linux alternativo:

- Python 3.12 o posterior, `pip` y entorno virtual para instalar `requirements.txt`, que incluye **yt-dlp** y **ytmusicapi** con versiones fijadas.
- **ffmpeg** disponible en el PATH para preparar la caché AAC-LC/fast-start. La reproducción de un archivo ya preparado no ejecuta FFmpeg; los fallos de preparación conservan el proxy de respaldo.
- Git para subir/actualizar el código y **systemd** para ejecutar IVOX como servicio con usuario propio, directorio de trabajo y archivo de entorno privado.
- **Tailscale**, conectado a la red privada del propietario. IVOX ya escucha en **`0.0.0.0:8765`**; el puerto **8765/TCP** debe ser accesible desde los dispositivos autorizados de esa red.
- Almacenamiento persistente para SQLite, `.env` y, si se usan, archivos locales. Se debe copiar la base con IVOX detenido y conservar sus permisos privados.
- Docker Engine y Compose si se desea mantener la instancia local de Piped como respaldo; sus puertos 8080/8081 deben permanecer internos/loopback.
- Conectividad saliente a los proveedores musicales. Para instalar la PWA desde móviles se necesita HTTPS confiable, también al acceder por Tailscale.

La caché preparada es temporal y local a cada proceso o réplica; sus archivos no forman parte de la biblioteca persistente. En Northflank, las credenciales y variables privadas se configuran en el servicio. Los límites y condiciones de alojamiento se administran en la cuenta del proveedor.

## Seguridad y persistencia

Contraseñas con scrypt y sal aleatoria, sesiones opacas de 180 días que se renuevan solas con el uso (el iPhone no pide volver a entrar mientras abras IVOX de vez en cuando), almacenadas como hash, cookies HttpOnly/SameSite Strict, CSRF para escrituras y comprobación de Origin cuando existe. Rate limit persistente de intentos fallidos de login por IP. Roles, planes, vencimientos y límites se validan en el servidor; suspensión y cambios se aplican a sesiones abiertas. Los streams comprueban sesión durante la transmisión. Los bytes ya recibidos no pueden revocarse.

Las consultas y asociaciones de biblioteca verifican propietario. El administrador recibe datos de cuenta y uso agregado, sin archivos ni títulos privados de otras cuentas. Las mutaciones usan transacciones SQLite y las importaciones validan firmas y usan nombres internos aleatorios.

`data/onda.sqlite` guarda cuentas, sesiones y referencias; `data/audio/` conserva archivos locales. Detén IVOX antes de copiar **toda** la carpeta `data` como copia coherente. Protege su acceso en Windows y no la publiques. `ONDA_DATA` permite una ruta privada alternativa.

Con `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` definidas, cuentas, sesiones, playlists y guardados viven en Turso (SQLite en la nube) y sobreviven a reinicios del contenedor; todos los dispositivos ven los mismos datos. Sin ellas se usa `data/onda.sqlite`. La caché de URLs de audio sigue en local y los archivos importados siguen en `data/audio/`, que no se sincroniza. `python scripts/migrate_to_turso.py` copia una base local existente a Turso sin sobrescribir filas.

La migración añade tablas y convierte referencias antiguas de YouTube a referencias de Piped con el mismo identificador, conservando favoritos y playlists. No borra cuentas, archivos ni asociaciones locales.

## Variables y despliegue privado

`.env.example` lista la configuración. El servidor carga `.env` al arrancar; las variables del proceso prevalecen. Nunca publiques el archivo ni pegues sus claves en el chat. Los tickets de audio son temporales, privados y no se guardan como URLs permanentes en SQLite. El backend valida los destinos del relay para evitar un proxy abierto.

Desde un iPhone conectado a la misma red local o a Tailscale usa `http://IP_DEL_EQUIPO:8765`; el firewall debe permitir esa conexión. Para HTTPS se necesita un certificado confiable y un proxy a `127.0.0.1:8765`; activa `ONDA_SECURE_COOKIE=1` solo detrás de HTTPS. No se han modificado el firewall ni el router. Las URLs locales de audio e imágenes de Piped se transmiten por IVOX, evitando que el teléfono contacte su propio localhost.

El servidor HTTP incluido está orientado a uso local privado. Para exposición pública se necesita un servicio HTTP de producción y revisión operativa. Las importaciones locales se leen en memoria; no están diseñadas para cargas masivas. No se configura evasión de bloqueos de proveedores.

## Pruebas

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
node --check web/online.js
```

Pruebas de navegador (requieren Playwright y Chromium):

```powershell
python tests/browser_check.py
python tests/online_live_check.py
python tests/audio_levels_live.py
python tests/pwa_check.py
python tests/music_search_live.py
python tests/player_controls_check.py
python tests/player_features_live.py   # perfil de artista, latencia real, toques rápidos, saltos por error, radio, letras, cola, gestos
python tests/latency_live.py           # tiempos de búsqueda y primeros bytes de audio
```

`tests/pwa_check.py` necesita IVOX ya en marcha en el puerto 8765. `tests/online_live_check.py` necesita la instancia local de Piped (`docker compose up -d`) y `tests/browser_check.py` falla al abrir `/admin` igual en la versión anterior (fallo previo, pendiente de investigar; la parte de reproducir archivos importados pasa).

Las cuentas y listas de prueba se crean en una base temporal, separada de tus datos. Resultados actuales en [README_SECCION_REPRODUCCION.md](README_SECCION_REPRODUCCION.md); informes y capturas en `test-results/`. La investigación de interfaz realizada con REA se documenta en [docs/rea-findings.md](docs/rea-findings.md). Los apartados anteriores de [docs/verification.md](docs/verification.md) conservan el historial de verificación del reproductor previo; no describen la arquitectura vigente.
