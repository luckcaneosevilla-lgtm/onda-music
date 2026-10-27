# Verificación — 27 de octubre de 2026

## Búsqueda vigente: ytmusicapi y preparación de RAW

Se instaló `ytmusicapi==1.12.3` dentro de `.vendor/` y se añadió a `requirements.txt`. La búsqueda predeterminada usa sus filtros `songs` y `artists`. Se conserva la reproducción con yt-dlp a partir del `videoId`, y se recuperó YouTube Data API como primer respaldo de búsqueda. Piped/Audius siguen disponibles si tampoco funciona ese respaldo.

**Prueba real aprobada:** `python tests/music_search_live.py`. Buscar «Feid» devolvió 20 canciones con título, artistas y duración; entre ellas Prohibidox (Feid, 167 s), XQ Te Pones Así (Feid/Yandel, 219 s) y La Mejor Música (Feid, 159 s). La siguiente página mostró 39 referencias únicas acumuladas. El filtro de artistas mostró Feid, y «Ver canciones» regresó a la lista musical. Un resultado se reprodujo por yt-dlp, nivel 1, con avance de tiempo. Con un fallo de ytmusicapi inducido solo en el servidor temporal, YouTube Data API devolvió resultados reales. No hubo errores JavaScript de página y el viewport de 390 px no desbordó.

El cliente fijado devolvía canciones vacías con localización interna española; se comprobó que `language='en'` devuelve los resultados y se usa esa localización interna, manteniendo interfaz y nombres originales. Se corrigió además la codificación del subprocess en Windows para conservar acentos. Las comprobaciones no requieren cookies ni credenciales del usuario en YouTube.

**18 pruebas unitarias/integración aprobadas**, incluida prioridad, caché, paginación, artistas, fallback a YouTube Data API, privacidad y administración. La comprobación de PWA también está aprobada tras actualizar la versión de caché. Capturas `test-results/ytmusic-*.png` e informe `test-results/ytmusic-live-report.json`.

**RAW:** solo se documentó como destino futuro en README, con Python, ffmpeg, Git, systemd, Tailscale y puerto 8765. No se desplegó ningún servidor ni se modificó una cuenta de alojamiento; la IP y el acceso siguen pendientes.

## Corrección vigente: yt-dlp + Piped puro/muxado + Audius

**Verificación real aprobada en varios niveles**: Feid (Prohibidox), Rick Astley y OhioJimbo (19.700 suscriptores) reprodujeron mediante yt-dlp, nivel 1. Rick Astley reprodujo por Piped muxado, nivel 3, al forzar un fallo de yt-dlp únicamente en el servidor temporal de prueba. El respaldo de Audius reprodujo por nivel 4. Se verificó avance de tiempo, Range/206 de `/api/audio`, ausencia de URLs de googlevideo en el navegador y sincronización de favoritos/playlists. Informe: `test-results/audio-levels-report.json`.

El nivel 2 está probado con fixtures de audio puro; no se afirma reproducción real en ese nivel porque Piped no devolvió `audioStreams` en los casos observados. **15 pruebas unitarias/integración aprobadas** y regresión local/admin aprobada. Los apartados siguientes son históricos y su bloqueo por ausencia de audio puro queda resuelto mediante el muxado en los casos comprobados. Detalles en [README_SECCION_REPRODUCCION.md](../README_SECCION_REPRODUCCION.md).

## Arquitectura vigente: Piped + Audius + HTML5 audio

El reproductor de YouTube descrito más abajo fue sustituido. Resultados actuales y siete comprobaciones en [README_SECCION_REPRODUCCION.md](../README_SECCION_REPRODUCCION.md) y `test-results/hybrid-live-report.json`.

**11 pruebas HTTP aprobadas** y regresión de interfaz local/admin aprobada. Piped se levantó con Compose y su `/healthcheck` responde correctamente. La búsqueda real de Feid devuelve 20 resultados con metadatos. Favoritos/playlists y acceso desde otra sesión funcionan. El audio principal de Piped está pendiente: devuelve `audioStreams` vacío en los casos observados. **Audius sí reproduce audio real**, con tiempo avanzando, barra persistente y gesto inicial en viewport/UA de iPhone emulado. No se afirma iPhone físico ni audio principal de Piped funcionando.

La prueba de error/siguiente provoca un fallo controlado solo en el servidor temporal de prueba y verifica el mensaje y preparación de la siguiente referencia. Se comprobaron cero iframes y cero peticiones a `youtube.com` del reproductor. Capturas actuales `test-results/hybrid-*.png`, inspeccionada la del reproductor móvil.

## Historial: integración previa de YouTube (sustituida)

## Corrección a descubrimiento online

Se ejecutó `python -m unittest discover -s tests -v`: **10 pruebas aprobadas**, incluidas las seis de administración original. Las cuatro nuevas prueban normalización de metadatos, filtros de inserción/región, duración, paginación, caché, falta de clave sin resultados ficticios, cuota, referencias privadas, favoritos, escuchar después, historial, playlists, segunda sesión y eliminación de datos. Estas pruebas usan un proveedor simulado identificado como fixture de prueba, no son una prueba de acceso a YouTube.

**Prueba real:** `python tests/online_live_check.py`, con clave real solo en backend y red permitida. La búsqueda «Feid» devuelve 20 resultados reales (por ejemplo Ryan Castro, Feid — MENTIRA, canal Ryan Castro, 162 segundos). La paginación llegó a 39 vídeos únicos. Se guardó un resultado en favoritos y una playlist, y ambos se abrieron desde otra sesión de navegador de la misma cuenta, a 390 × 844 px. Sin errores JavaScript de página. Se verificó que navegar a Mi plan conserva el mismo iframe, y que el reproductor móvil mide 352 × 200 px. El informe de ejecución está en `test-results/online-live-report.json`.

**Reproducción pendiente de verificar con éxito:** el reproductor oficial cargó y sus controles están visibles, pero los cuatro vídeos de Feid intentados devolvieron un error de inserción de YouTube en Chromium automatizado. Se mostró el mensaje de no disponibilidad y se pudo elegir otro vídeo. No se afirma que haya comenzado reproducción de Feid: no se observó estado PLAYING ni avance de tiempo. El recorrido online es parcial hasta verificar ese paso en un navegador y una región donde el proveedor lo permita. No se ocultan ni se sortean bloqueos de YouTube.

La clave quedó configurada y el servidor local se reinició con la integración actualizada. La API no expone esa clave al cliente. Capturas online en `test-results/online-*.png`. Se inspeccionó visualmente la captura de Mi plan con el reproductor oficial y su mensaje de bloqueo.

Las pruebas de móvil son emulación de viewport en Chromium, no un iPhone físico. No se ha comprobado reproducción con pantalla bloqueada, ni se ofrece. YouTube puede mostrar publicidad; Premium de ONDA no la elimina.

## Verificación anterior de biblioteca local y administración

## Ejecutado

- `python -m unittest discover -s tests -v`: **6 pruebas de integración aprobadas**, sobre SQLite temporal y peticiones HTTP reales.
- `node --check web/app.js`: correcto.
- `python tests/browser_check.py`: **aprobado en Chromium**, sin errores JavaScript de página.

Las pruebas HTTP verifican accesos anónimos y no administradores, CSRF, origen ajeno, bloqueo de cambios propios, separación rol/plan, Premium indefinido y con vencimiento, pruebas, ampliaciones, retirada y confirmación, vencimiento con sesión abierta, suspensión/reactivación, revocación de rol en sesión abierta, aislamiento de canciones y playlists, favoritos, asociaciones privadas, streaming Range, límites por archivo/almacenamiento/playlists, conservación al bajar a Free, actualización inmediata de límites, auditoría, búsqueda, resumen real y persistencia entre dos sesiones.

El recorrido de navegador registra dos cuentas, importa un WAV generado de un segundo y verifica su duración cargada, crea una playlist, muestra Mi plan, concede y retira Premium con confirmación, cambia límites y comprueba que el segundo usuario no ve la canción del primero. Verifica ausencia de desbordamiento de página a 390 px. Capturas en `test-results/`: biblioteca móvil, Mi plan en escritorio y administración en escritorio y móvil. Se inspeccionó la captura de administración en escritorio.

## Límites de la verificación

No se ha probado iPhone físico, HTTPS externo, carga masiva, recuperación ante corte de energía o pagos. Las pruebas de vencimiento adelantan el estado persistido para verificar el reloj del servidor sin esperas largas. Las cuentas de prueba y su audio se crean en directorios temporales y se eliminan al finalizar; no son cuentas de la aplicación real.

No se ha ejecutado el bootstrap con una contraseña real del propietario: requiere su registro y entrada interactiva local. El comando está documentado en README y no tiene credenciales predeterminadas. No se ha asignado una identidad de prueba como administrador en los datos reales.
