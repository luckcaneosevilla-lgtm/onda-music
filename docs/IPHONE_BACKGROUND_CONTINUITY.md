# Continuidad al bloquear un iPhone

El usuario comunicó el 29 de octubre que el fin de canción y el botón Siguiente
fallaban con el iPhone bloqueado. Las pruebas anteriores en Chromium no
demostraban ese comportamiento físico de iOS.

Se reprodujo una dependencia concreta en un escenario sin ejecutar temporizadores:
el evento pause cerca del final programaba el avance 1,5 s después. Si la página
se suspendía antes del temporizador, no se asignaba la siguiente fuente. El
botón Siguiente también usaba un debounce de 200 ms en segundo plano.

Cambios:

- Encadenar dentro del callback del medio al detectar la pausa de fin; tener en
  cuenta ended y el final nativo aunque el catálogo indique otra duración.
- Ejecutar Siguiente sin debounce cuando la página está oculta.
- Declarar la sesión de audio como playback donde la API está disponible y
  dejar autoplay nativo activo solo mientras la app solicita reproducción.
- Respetar pausa/cierre explícitos; distinguirlos de una interrupción del
  navegador y recuperar el estado al regresar mediante visibilitychange/pageshow.
- Preparar cola y audio próximo al ocultarse la página; preparar los nuevos
  resultados de radio en cuanto llegan, sin esperar el umbral final.
- Continuar inmediatamente si llega una canción para una cola cuyo fin ya se
  procesó, aunque la promesa de radio siga pendiente.
- Usar la duración real recibida del servidor aunque hubiera duración de catálogo.
- Ignorar un ended tardío de la fuente anterior tras encadenar desde pause.
- Actualizar el app shell a v20.

Validación: tests/queue_continuity_check.py añade final sin temporizadores,
duración incorrecta, interrupción oculta, Siguiente oculto sin temporizador,
pausa explícita oculta y llegada de una canción mientras radio sigue bloqueada.
tests/play_intent_check.py cubre además la corrección de duración desde servidor.
Son pruebas de lógica y medios reales en Chromium: no sustituyen bloquear un
iPhone físico ni garantizan que iOS nunca suspenda una PWA.

La comprobación pública opcional `--iphone-path` selecciona el código de iOS
mediante User-Agent y simula la página oculta; sigue siendo Chromium, no Safari
en un dispositivo físico.

Fuentes primarias de contexto sobre medios y AudioSession de WebKit:
https://developer.apple.com/documentation/webkitjs/htmlmediaelement
https://bugs.webkit.org/show_bug.cgi?id=261554
