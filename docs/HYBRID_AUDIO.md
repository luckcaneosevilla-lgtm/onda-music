# Audio hibrido

La busqueda sigue usando YouTube Music (`ytmusicapi`). Al devolver resultados,
`audio_router.prefetch_many` prepara hasta diez canciones en un worker de fondo
con cola acotada a veinte. El trabajo no bloquea la respuesta de busqueda.

El router busca coincidencias publicas de Audius por titulo, artista y duracion.
En segundo plano hace una segunda consulta por artista si la primera solo devuelve
remixes. Normaliza las etiquetas `feat.` y `ft.`, pero conserva las versiones.
La cache en memoria dura 30 minutos y admite 500 coincidencias o ausencias.
Los fallos transitorios se recuerdan 60 segundos. Cada replica mantiene su cache.

Para coincidencias de Audius prepara los primeros 64 KiB mediante Range y conserva
la URL final. El cache de prefijos esta limitado a 16 MiB y 64 entradas; la
continuacion solicita el resto de la misma representacion. Las URLs nunca salen
al cliente. Todas las conexiones y redirecciones pasan por la validacion de
destinos publicos del proxy. Si la preparacion aun esta en curso, Play puede usar
el fallback sin esperar a Audius.

El fallback conserva el pool de procesos yt-dlp arrancados al iniciar el servidor,
reutiliza YoutubeDL y mantiene el prefetch existente. Los parametros Python
`http_chunk_size=8388608` y `concurrent_fragment_downloads=8` equivalen a los flags
solicitados; no aceleran la extraccion con `download=False` ni el proxy HTTP.

`X-IVOX-Audio-Source`, `X-IVOX-Audio-Cache`, `Server-Timing` y los eventos del
backend permiten distinguir proveedor, cache y latencia. `IVOX_AUDIUS_TIMEOUT`
controla la espera por peticion de busqueda; por defecto es 1.5 segundos.

## Medicion

Ejecutar `python tests/hybrid_audio_probe.py` con acceso de red. Usa una cuenta y
base SQLite temporales; no toca Turso ni usuarios reales. Busca en YouTube Music,
prepara el resultado como hace la busqueda de IVOX y mide HTTP local autenticado
hasta recibir el primer bloque de audio. El informe se guarda en
`test-results/hybrid-audio-probe.json`. La preparacion y el TTFB se informan por
separado. TTFB no incluye el tiempo de decodificacion hasta el evento `playing`
del navegador, ni reproduce la latencia de red de Northflank.

La disponibilidad de un stream publico no constituye una comprobacion de derechos
de cada grabacion; el fallback de YouTube conserva sus condiciones de uso.
