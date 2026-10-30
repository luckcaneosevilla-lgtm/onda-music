# InnerTube con visitorData: 30 de octubre de 2026

El motor usa peticiones HTTP directas, sin subprocesos. Obtiene visitorData con
POST /youtubei/v1/visitor_id: la comprobacion real del GET devolvio HTTP 405.
Si el POST no entrega token, extrae VISITOR_DATA del HTML de watch. El token vive
solo en memoria durante 30 minutos, se envia en context.client.visitorData y en
X-Goog-Visitor-Id, y se renueva una vez al recibir LOGIN_REQUIRED. Una cerradura
coordina las renovaciones concurrentes; los tokens nunca se registran en logs.

Se probaron ANDROID_VR 1.60.19, VISIONOS 1.02 y TVHTML5 7.20260707.07.00.
ANDROID_VR con visitorData funciono para Rick Astley pero siguio devolviendo
LOGIN_REQUIRED para Feid incluso despues de renovarlo. VISIONOS entrego audio
para las tres canciones. TVHTML5 respondio UNPLAYABLE en la prueba de Rick.
VISIONOS es por ello la primera opcion; los otros clientes y yt-dlp siguen como
respaldo, con intentos acotados. El cliente preferido se actualiza al comprobar
que su URL entrega audio.

Un pool conserva hasta cuatro conexiones TLS ociosas reutilizables entre los
hilos del servidor HTTP. Las URLs directas duran hasta 30 minutos en memoria,
respetando su expiracion real. Se aceptan solo destinos HTTPS googlevideo.com,
formatos de audio MP4/WebM y coincidencia del video solicitado. Se conserva Range,
se prefiere AAC-LC para Safari y las URLs rechazadas se descartan. Los fallos se
recuerdan 30 segundos. El router intenta InnerTube, Audius ya preparado y yt-dlp.
Play no inicia una busqueda en Audius.

## Medicion reproducible

Ejecutar `python tests/innertube_audio_probe.py` con acceso de red. El script usa
HTTP local autenticado, SQLite y cuenta temporales: no toca Turso ni usuarios de
produccion. Busca el catalogo con ytmusicapi, arranca el worker de respaldo, pero
no resuelve ni prepara el audio antes de medir. Lee exactamente el primer byte
de la respuesta de /api/audio/VIDEO_ID. Guarda el informe sin tokens ni URLs en
`test-results/innertube-audio-probe.json` (ignorado por Git).

| Cancion | Primera solicitud | Repeticion | Fuente / cliente |
| --- | ---: | ---: | --- |
| Feid / ATL Jacob - LUNA | 0.690 s | 0.297 s | InnerTube / VISIONOS |
| Bad Bunny - DtMF | 0.541 s | 0.326 s | InnerTube / VISIONOS |
| Rick Astley - Never Gonna Give You Up | 0.458 s | 0.411 s | InnerTube / VISIONOS |

La primera solicitud de Feid incluye obtener visitorData desde cero (212 ms).
Las otras canciones no tenian URL de audio cacheada; reutilizaron el token y TLS.
Los seis intentos devolvieron audio MP4 con HTTP 206. Comparados con la referencia
previa de 4.03 s, los primeros intentos fueron 5.84x, 7.45x y 8.81x mas rapidos.
La referencia pertenece a otro ensayo de red, no a un control simultaneo.

Son TTFB locales hasta recibir audio, no tiempos hasta el evento playing del
navegador ni mediciones desde Northflank. Tres canciones no garantizan el mismo
resultado para cualquier video, IP, region o restriccion de YouTube.

## Verificacion

El conjunto completo pasa 54 pruebas: contexto y cabecera visitorData, renovacion
ante LOGIN_REQUIRED, expiracion y cache, extraccion alternativa desde HTML,
cambio de cliente, formato cifrado/restringido, destinos no permitidos, timeout,
Range y fallback yt-dlp tras un HTTP 403. Se conserva el pool de respaldo.

Las definiciones de los clientes alternativos se contrastaron con el codigo
primario de yt-dlp:
https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_base.py
