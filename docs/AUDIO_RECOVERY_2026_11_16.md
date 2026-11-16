# Reproducción: diagnóstico del 16 de noviembre de 2026

El usuario comunica fallos generalizados en la app instalada en iPhone. Todavía
no se ha reproducido su caso en un iPhone físico ni identificado una canción
concreta que falle allí. No se considera resuelto el incidente.

## Correcciones locales

- La continuación del MP4 preparado admite dos reconexiones por respuesta ante
  cortes de red o lectura truncada. Conserva la URL y pide desde el primer byte
  que aún no se entregó. Se vuelven a validar tamaño y ETag/Last-Modified;
  un cambio de representación sigue rechazándose sin mezclar archivos.
- El aviso de cookies rechazadas funciona también durante los primeros diez
  minutos de arranque del sistema; el valor inicial cero lo silenciaba.
- La recuperación del reproductor renueva una vez el identificador nativo y
  pide una URL fresca, conservando la posición y el límite de reintentos.
  Antes recargaba el mismo intento, que podía seguir ligado a una URL caducada.
- La renovación explícita vuelve a permitir InnerTube sin bloquearlo durante
  treinta segundos. Los oyentes concurrentes comparten la renovación durante
  esa ventana; no se borran repetidamente resultados recién recuperados.
- El diagnóstico local conserva el código numérico de error del elemento de
  audio y su estado de red, sin mensajes ni URLs privados.
- El diagnóstico publicado cierra las novedades antes de pulsar Play, distingue
  motores y permite comprobar rangos nativos con `--native-ranges`.

## Evidencia y límites

- Suite completa: 106 pruebas correctas, incluidas reconexiones, rechazo de
  ETag tras reconexión y renovación de InnerTube después de un fallo temporal.
- Reproductor: 15 escenarios correctos; el atasco a los doce segundos renueva
  la URL nativa y continúa desde esa posición. Pausa y cambio de canción
  cancelan las recuperaciones. La prueba usa un seek real en la fixture;
  una propiedad currentTime de solo lectura impedía validar la nueva carga.
- Cola: 17 escenarios correctos, incluidos callbacks sin temporizadores,
  interrupciones en segundo plano, fin natural y canción elegida que falla.
- Service Worker: prueba Node correcta; sintaxis de online.js correcta.
- Servidor publicado: 16 canciones distintas entregaron 32.768 bytes de
  audio/mp4 con HTTP 206. Esto demuestra entrega inicial, no escucha completa.
- Verificación adicional: 12 canciones pasan las peticiones del mismo intento
  nativo a bytes 0-1, mitad y final. Tamaños y Content-Range son coherentes en
  todas ellas. Informe: test-results/production-audio-probe.json.
- Chromium con perfil de iPhone: NUEVAYoL reproduce ocho segundos; al buscar
  su final, Tití Me Preguntó avanza ocho segundos automáticamente, sin errores
  JavaScript. El arranque medido es 222 ms. Es emulación, no hardware iPhone.
- Chromium local: Play, pausa, búsqueda temporal, siguiente y final natural
  pasan en 390 y 1440 px.
- WebKit de Windows: la prueba aislada con WAV también devuelve error 4 del
  decodificador del host. La UI pasa, pero este entorno no valida reproducción
  de Safari/iPhone. `canPlayType` por sí solo anuncia soporte y no basta.

La publicación se comprueba con el hash de `/version.json` y una prueba de
rangos nativos contra el servidor publicado. Los ajustes de resolución que
ya estaban en audio_resolver.py y tests/test_ytdlp_layers.py se han conservado
y validado con la suite completa; los archivos de remux y reels quedan fuera.
