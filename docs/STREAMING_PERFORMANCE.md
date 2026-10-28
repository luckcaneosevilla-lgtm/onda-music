# Arranque de streaming: cambios y mediciones

Investigación y pruebas locales del 28 de octubre de 2026. Base anterior: `d9981a4`. No se garantiza un SLA de menos de un segundo para cualquier canción inédita: dos de las tres pistas de la prueba de navegador siguen por encima de ese objetivo en frío.

## Cuello de botella confirmado

El backend ya tenía un pool persistente de tres procesos yt-dlp, cada uno con YoutubeDL reutilizado, y `download=False`. No lanzaba Python por canción ni esperaba a descargar el archivo completo. La mayor demora era la extracción HTTP del proveedor. Además, el relay usaba `HTTPResponse.read(65536)`, que podía esperar a completar 64 KiB antes de entregar datos al navegador.

Se mantiene el servidor HTTP estándar existente: no es FastAPI. No se introduce una migración de framework ni se recodifica a HLS. Un archivo de audio progresivo con Range ya permite streaming y seek; HLS añadiría segmentación, manifests y una ruta distinta de compatibilidad sin eliminar el tiempo de extracción.

## Estrategias evaluadas

1. **InnerTube directo:** se probaron dos peticiones públicas `/youtubei/v1/player` para `dQw4w9WgXcQ`. WEB devolvió HTTP 200 pero UNPLAYABLE y cero formatos de audio. IOS devolvió OK y dos formatos, pero ninguna URL directa utilizable. No se adoptó una sustitución que fallaba en la prueba. InnerTube es una interfaz interna; no se presenta como API pública documentada y estable. Las políticas de clientes, firmas y PO tokens se reflejan en la [implementación de yt-dlp](https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/yt_dlp/extractor/youtube/_base.py) y su [guía de PO tokens](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).
2. **Otras librerías:** [pytube](https://github.com/pytube/pytube) también extrae información y debe adaptarse al proveedor; no se midió una ventaja real y no se añadió como dependencia especulativa. El propio README de [youtube-search-python](https://github.com/alexmercerind/youtube-search-python) utiliza yt-dlp para su StreamURLFetcher: cambiar la envoltura no elimina ese coste.
3. **Resolvedor reutilizable:** se conserva el pool acotado y se añade una primera extracción sin webpage/configs/initial_data. Si no produce URL, el mismo worker usa su instancia compatible. Si una URL es rechazada, se invalida y el reintento fuerza la ruta compatible durante diez minutos. Se conserva el timeout y el reciclado de workers. [Embedding de yt-dlp](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp).
4. **Passthrough inmediato:** el relay usa `read1`, escribe y vacía el primer bloque sin esperar a llenar 64 KiB. Se mantienen Range, 206 y Content-Range. Solo se anuncia Accept-Ranges si el upstream lo anuncia o realmente responde 206. `Cache-Control: private, no-store` y `X-Accel-Buffering: no` evitan caché HTTP compartida y solicitan no acumular el stream en proxies compatibles. El buffering del alojamiento debe comprobarse tras desplegar.
5. **Prefetch de bytes:** las búsquedas encolan sus cinco primeras referencias de YouTube, sin esperar al prefetch para responder. Cada tarea resuelve y pide solo `bytes=0-511999`: 500 KiB (512.000 bytes), no toda la canción. RAM de prefijos limitada a 16 MiB y 64 entradas, cola de 15, hasta dos consumidores de fondo en el pool habitual. Se conserva capacidad para Play. La API de prefetch autenticada filtra referencias desconocidas y valida IDs.
6. **Caché compartida:** con Turso configurado se crea `audio_resolved_urls` y se publican/hidratan URLs en segundo plano, sin consultas a Turso desde `resolve()`/`open()`. Sincronización eventual cada 30 s, cola acotada y reintentos con backoff. La copia local SQLite se conserva. No se envían los fragmentos binarios a Turso.

## Prefijo inmediato sin romper el archivo

`streaming.PrefixResponse` conserva URL, mime, tamaño total y validadores del fragmento. Devuelve el prefijo inmediatamente y abre el resto de **esa misma URL** al necesitarlo. Verifica estado 206, inicio/final/total de Content-Range, mime y ETag/Last-Modified cuando existen. No une un fragmento AAC a un formato distinto tras resolver de nuevo. Rangos fuera del prefijo pasan al upstream; rangos íntegramente contenidos se satisfacen en RAM.

Si el proveedor ignora Range, no se almacena el fragmento. Si cambia el medio durante la continuación se cierra esa respuesta, evitando entregar bytes corruptos. No se promete continuidad invisible frente a cualquier fallo externo. El navegador conserva su recuperación habitual mediante una nueva petición.

Los bloqueos ahora son por videoId, sin colisiones entre canciones diferentes. Un hit válido no espera detrás de una renovación en curso. A menos de cinco minutos del vencimiento, se encola la actualización y se sigue sirviendo el enlace aún válido. El máximo de caché es dos horas, **limitado por `expire` del proveedor menos 120 s**; ampliar el TTL no prolonga una firma.

## Configuración de Turso y rollback

Usa las variables de Turso existentes. `IVOX_AUDIO_CACHE_SCOPE` es una etiqueta de la **IP pública de salida compartida**, no del usuario. Solo configura el mismo valor en réplicas con la misma salida, porque las URLs pueden estar ligadas a ella. Vacío usa hostname y aísla instancias; no ofrece automáticamente reutilización entre nodos diferentes. Un cambio de IP puede invalidar enlaces antes del TTL, por lo que se mantiene la renovación tras rechazo.

```dotenv
IVOX_AUDIO_CACHE_SCOPE=
IVOX_YTDLP_FAST=0  # desactivado por defecto desde el 28-10-2026: falló en todas las canciones probadas y añadía ~0,1 s
IVOX_AUDIO_PREFETCH=1
```

Se puede desactivar cada optimización con `0` y reiniciar. La degradación de la caché remota no bloquea el resolvedor local; se registra `audio_cache_error` sin imprimir credenciales ni URLs. La interoperabilidad de la caché compartida se prueba con una conexión SQLite que implementa el mismo contrato; **no se ejecutó esta migración contra la cuenta real de Turso en estas pruebas**. La creación de tabla y la sincronización real deben comprobarse con logs en el despliegue.

## Resultados reales, separados por métrica

Muestra de tres IDs nuevos para la caché local: Rick Astley (`dQw4w9WgXcQ`), Despacito (`kJQP7kiw5Fk`) y Queen (`fJ9rUzIMcZQ`). Workers calientes mediante un cuarto ID distinto; los tres de la muestra no se resolvieron ni precargaron antes de su prueba fría. Son mediciones desde este PC y esta red, no desde Koyeb ni desde un teléfono físico. El estado de cachés internas del proveedor no se controla, y la muestra no es un ensayo estadístico de producción.

| Medición | Antes: mediana | Después: mediana |
|---|---:|---:|
| Resolvedor → primer byte upstream, frío | 3587,36 ms | 1590,40 ms |
| Resolvedor → primer byte, URL ya cacheada | 223,99 ms | 268,15 ms |
| Resolvedor → primer byte, prefijo ya listo | No implementado | 0,44 ms |

El arranque frío hasta el primer byte mejoró en esta muestra, pero el tiempo de red con URL cacheada varió ligeramente: no se atribuye toda diferencia a código.

**Chromium móvil emulado, clic → evento HTMLMediaElement `playing`, perfil nuevo por cada caso para excluir caché del navegador:**

| Pista | Sin precarga | Prefijo listo |
|---|---:|---:|
| Rick Astley | 790,6 ms | 33,1 ms |
| Despacito | 2051,9 ms | 36,8 ms |
| Queen | 1984,6 ms | 39,4 ms |
| Mediana | **1984,6 ms** | **36,8 ms** |

Se desbloqueó el audio con un gesto previo, como ocurre al usar el buscador, antes de iniciar cada cronómetro. `playing` verifica que el elemento comienza a reproducir, no el instante físico de salida de un altavoz. Sin precarga, solo 1/3 de esta muestra cumple <1 s. Con prefijo preparado, 3/3. Los datos sanitizados completos están en [streaming-results.json](streaming-results.json).

No se declara alcanzado el objetivo global para canciones nuevas. Para un presupuesto estricto de arranque hay que controlar también resolución y disponibilidad del catálogo, red y región de alojamiento; no es correcto convertir un enlace firmado externo en un SLA garantizado mediante un TTL mayor.

## Métricas y reproducción de pruebas

Logs JSON con timestamp UTC y request_id para correlacionar primer byte y fin de la misma petición:

- `audio_resolve`: videoId, engine fast/compatible, espera del pool, extracción y resultado.
- `audio_prefetch`: videoId, bytes y coste total del trabajo de fondo.
- `audio_first_byte`: tiempo desde entrada de petición hasta primera escritura al cliente, resolución/headers y prefix_hit.
- `audio_complete`: tiempo total de transferencia, bytes y resultado/fase de fallo. No equivale a la duración de la canción.
- `audio_request_failed`, `audio_cache_error`, `audio_cache_queue_full`: fallos sin secretos.

Cada respuesta incluye Server-Timing y `X-IVOX-Audio-Cache: prefix|passthrough`. Ningún log ni API nueva expone la URL googlevideo, tokens o cookies. El coste de conexión al proveedor se mide hasta sus headers; TTFB del cliente incluye además autenticación, primer read y escritura del relay.

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python tests/probe_innertube.py
python tests/stream_benchmark.py optimized
python tests/browser_stream_live.py
```

Las pruebas live requieren red; no cambian cuentas reales. El benchmark de navegador usa SQLite temporal y desactiva radio/prefetch adicional para aislar la pista medida. Los tests unitarios comprueban continuación exacta, rangos, rechazo de representaciones incompatibles, RAM acotada, TTL, caché compartida por scope, hits durante renovación y autenticación. Quedan pendientes medición en Koyeb y dispositivos físicos, y validación del caché contra Turso real.
