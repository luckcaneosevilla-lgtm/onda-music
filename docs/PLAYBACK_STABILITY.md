# Primer Play y cortes de reproducción

Corrección del 28 de octubre de 2026, posterior a la optimización de streaming. Dirección confirmada por el usuario: `https://p01--onda-music--ftnmpttbnqmf.code.run` (la anterior tenía una letra distinta). Se comprobó que el JS público anterior todavía contenía el salto de Safari a `99999` y no protegía los toques repetidos durante carga.

## Fallos y cambios

- El desbloqueo global podía introducir `/silence.wav` justo antes de Play y el botón tratarlo como la canción. Ahora un Play con fuente inmediata conserva el gesto y el botón nunca considera ese sonido temporal una pista preparada. Las fuentes asíncronas mantienen la preparación del elemento.
- `audio.paused` pasa a false mientras `play()` sigue esperando datos. Otro toque podía pausar antes de sonar. Los controles de barra y panel mantienen la petición durante carga; la pausa explícita de Media Session o cierre se respeta.
- Safari provocaba un seek a `99999` al cargar metadata poco fiable. Se elimina: la duración del catálogo corrige la presentación sin forzar una descarga del final.
- Las lecturas asíncronas de copias locales podían revocar el blob de una pista nueva. La URL se asigna/revoca solo tras comprobar la versión de reproducción; una respuesta antigua se descarta.
- Se elimina la segunda descarga completa automática que empezaba tras ocho segundos de escucha. Las copias anteriores siguen legibles; las nuevas escuchas no compiten con esa descarga. Se actualiza el texto de privacidad.
- Las interrupciones ya no esperan veinte segundos ni saltan directamente a otra canción: tras tres segundos sin progreso se recarga la misma fuente y se restaura el segundo, con dos intentos máximos. Una vuelta de conectividad puede acelerar la recuperación. Los reintentos son únicos, conservan la intención de escuchar y se cancelan al pausar, cerrar o cambiar pista. Agotados los intentos se mantiene el respaldo existente y el mensaje de error.
- Un corte del cuerpo HTTP o una continuación de prefijo inválida invalidan la URL del backend. La siguiente recuperación no repite eternamente el mismo prefijo roto. Las desconexiones del cliente al cambiar canción no invalidan URLs correctas.
- La deduplicación de prefetch del frontend deja de durar toda la sesión: caduca a los treinta segundos para que una sesión larga pueda volver a preparar referencias. El service worker cambia a `ivox-app-shell-v9`.
- El panel grande recibe también previsualización estable, buffer por segmentos, validación de duración y cancelación del seek al cambiar de tema o cerrar. Se conserva el área táctil de 44 px; una interacción antigua no puede mover la pista nueva.

## Comprobación

`python tests/play_intent_check.py` ejecuta catorce casos de la app completa en Chromium móvil con audio WAV real y retrasos de red controlados: primer toque, toque repetido en carga, ambos controles, seek del panel grande, desbloqueo en curso, fuente asíncrona, caché local heredada, duración estilo Safari, error de red, espera de buffer, pausa durante espera, cambio durante espera y ausencia de descarga duplicada. Para probar temporizadores de cortes se simulan estados del medio y se avanza el reloj; no se presenta como prueba en un iPhone físico.

`python -m unittest discover -s tests -p 'test_*.py'` incluye comprobación HTTP de cuerpo truncado: el cliente detecta la transferencia incompleta y el servidor invalida la URL para reconectar.

Prueba externa de continuidad:

```powershell
$env:IVOX_BENCH_HOLD_SECONDS='35'
python tests/browser_stream_live.py kJQP7kiw5Fk
Remove-Item Env:IVOX_BENCH_HOLD_SECONDS
```

Perfil nuevo por caso; Play es el primer gesto, sin toque previo de desbloqueo. La pista real reprodujo hasta 35,01 s en frío y con prefetch, sin recuperaciones. El relay completó los 4.557.665 bytes en ambos casos. Clic hasta `playing`: **2245,2 ms en frío**, **33,9 ms con prefijo listo**. Los datos, incluidos los casos controlados, se guardan en [playback-stability-results.json](playback-stability-results.json).

El objetivo universal de un segundo **no está demostrado**: el proveedor sigue siendo el cuello de botella en frío. Estas correcciones resuelven fallos de interacción, competencia de descarga y recuperación; no garantizan red ni disponibilidad del proveedor. Sigue pendiente Safari/Chrome en teléfonos físicos y medición autenticada en el servidor del usuario. Al actualizar el despliegue hay que recargar la aplicación/PWA para sustituir el JS que ya estaba ejecutándose.
