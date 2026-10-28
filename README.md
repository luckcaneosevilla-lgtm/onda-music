# IVOX

Descubrimiento musical online para uso privado: búsqueda principal con **ytmusicapi / YouTube Music**, respaldo **YouTube Data API**, y reproducción con **yt-dlp**, Piped y Audius mediante **HTML5 audio**. Se conservan cuentas, biblioteca privada, administración, roles y planes. El recorrido principal ya no depende de importaciones ni de un reproductor de YouTube.

**Cadena actual:** yt-dlp (audio puro) → Piped audio puro → Piped MP4 muxado con audio → Audius → error y siguiente. Los MP4 con `videoOnly=false` ya no se descartan cuando `audioStreams` está vacío. El estado comprobado de cada nivel figura en [README_SECCION_REPRODUCCION.md](README_SECCION_REPRODUCCION.md), con informes separados de las verificaciones anteriores.

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

- `/`: búsqueda online real con filtros Canciones/Artistas, recientes privados y selector de fuentes. El respaldo automático anuncia el cambio de fuente.
- `/library`: favoritos, playlists, historial y escuchar después; se guardan referencias de proveedor, no audio descargado.
- `/plan`: estado y límites del plan. Buscar y reproducir están disponibles en Free y Premium.
- `/admin`: usuarios, resumen real, Premium manual, suspensión/reactivación, roles y auditoría.
- `/files`: importaciones y biblioteca de archivos anteriores, como opción secundaria.
- `/privacy`: explicación de datos y eliminación de la biblioteca online propia.

La barra de audio permanece al navegar. Incluye los controles nativos de progreso, play/pausa y volumen (cuando el navegador lo admite), cola y anterior/siguiente. En iPhone basta un toque en Play: el primer toque en la app desbloquea el audio. No se promete reproducción con pantalla bloqueada ni offline.

### Búsqueda musical

La búsqueda predeterminada usa **`ytmusicapi==1.12.3`**, sin cookies ni login de Google, con `YTMusic.search(filter='songs')` o `filter='artists'`. Las canciones conservan el título, lista de artistas, álbum, miniatura, duración y `videoId` devueltos por YouTube Music. «Ver canciones» en un artista consulta su nombre con el filtro de canciones. Los resultados no son inventados ni se confunde un artista con un vídeo reproducible.

El `videoId` se guarda como referencia compatible con la biblioteca existente y se pasa al endpoint de **yt-dlp** para reproducir. Buscar y reproducir son procesos separados: no se cambia la cadena de resolución de audio ni se añade un iframe.

Ante fallo de ytmusicapi se intenta **YouTube Data API** con `YOUTUBE_API_KEY` del backend. Si también falla o falta la clave, se mantienen Piped/Audius como respaldo de canciones; la fuente real y el cambio se indican en la interfaz. El filtro Artistas de YouTube Data API devuelve canales, identificados como tales, sin afirmar que todos sean artistas oficiales. Una respuesta válida sin coincidencias se presenta vacía, no se rellena con canciones ficticias.

Se conserva caché de 10 minutos por consulta/filtro/fuente, límite de frecuencia, cancelación de respuestas antiguas y paginación con cursores firmados. ytmusicapi no expone aquí un cursor público de continuación: se amplía su límite de 20 en 20 y se presenta el siguiente tramo, hasta **100 resultados** por consulta. Para más resultados, ajusta la búsqueda. La localización interna del cliente se fija a inglés porque se observó una respuesta vacía de canciones con `language='es'` en esta versión; los títulos originales, acentos y la interfaz española se conservan. Cada consulta del cliente tiene límite de tiempo y el proceso completo se corta a los 25 segundos.

Instala con `python -m pip install -r requirements.txt`. En esta máquina la dependencia y sus paquetes se instalaron en `.vendor/`, aislados de los paquetes globales. Esta biblioteca es un cliente no oficial y puede necesitar actualizaciones si cambia el proveedor. Prueba real y capturas: `python tests/music_search_live.py`, informe en `test-results/ytmusic-live-report.json`.

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

## Destino de despliegue: RAW (rawhq)

El destino elegido es **RAW (rawhq), en lugar de Oracle**. El despliegue aún no se ha realizado: está pendiente que el propietario facilite la IP y el acceso al servidor. La disponibilidad, límites y condiciones del plan de alojamiento deben comprobarse en la cuenta del proveedor; este repositorio no configura facturación.

Requisitos del servidor Linux:

- Python 3.12 o posterior, `pip` y entorno virtual para instalar `requirements.txt`, que incluye **yt-dlp** y **ytmusicapi** con versiones fijadas.
- **ffmpeg** disponible en el PATH para compatibilidad de formatos y operaciones de audio que lo necesiten; no es obligatorio para cada stream directo de la versión actual.
- Git para subir/actualizar el código y **systemd** para ejecutar IVOX como servicio con usuario propio, directorio de trabajo y archivo de entorno privado.
- **Tailscale**, conectado a la red privada del propietario. IVOX ya escucha en **`0.0.0.0:8765`**; el puerto **8765/TCP** debe ser accesible desde los dispositivos autorizados de esa red.
- Almacenamiento persistente para SQLite, `.env` y, si se usan, archivos locales. Se debe copiar la base con IVOX detenido y conservar sus permisos privados.
- Docker Engine y Compose si se desea mantener la instancia local de Piped como respaldo; sus puertos 8080/8081 deben permanecer internos/loopback.
- Conectividad saliente a los proveedores musicales. Para instalar la PWA desde móviles se necesita HTTPS confiable, también al acceder por Tailscale.

Cuando esté disponible la IP se prepararán los comandos de instalación, transferencia y configuración del servicio, y la conexión a Tailscale. **Por ahora solo queda documentado**: no se ha desplegado código, instalado software en RAW, unido ningún servidor a Tailscale ni modificado reglas de red externas.

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
```

Las cuentas y listas de prueba se crean en una base temporal, separada de tus datos. Resultados actuales en [README_SECCION_REPRODUCCION.md](README_SECCION_REPRODUCCION.md); informes y capturas en `test-results/`. La investigación de interfaz realizada con REA se documenta en [docs/rea-findings.md](docs/rea-findings.md). Los apartados anteriores de [docs/verification.md](docs/verification.md) conservan el historial de verificación del reproductor previo; no describen la arquitectura vigente.
