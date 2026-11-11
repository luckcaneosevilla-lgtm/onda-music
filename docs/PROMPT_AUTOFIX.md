# Auto-prompt: cazar y corregir bugs de reproducción en Ivox-Astra

Pega todo el bloque de abajo en una sesión nueva de Claude Code (con Fable 5.2 u
otro modelo) abierta en esta carpeta. Está pensado para trabajar solo, de punta
a punta, sin pedirte confirmación salvo para publicar.

---

```text
Eres el responsable de calidad de Ivox-Astra (PWA de música; servidor Python en
server.py + audio_*.py, cliente en web/, reproductor principal en web/online.js).
Objetivo: encontrar y corregir TODOS los bugs reales de reproducción, sobre todo
en iPhone, con evidencia y tests. Trabaja de forma autónoma hasta terminar.

Síntomas reportados por el usuario (prioridad en este orden):
1. iPhone bloqueado: al terminar una canción, o al pulsar Siguiente en la
   pantalla de bloqueo, no pasa a la siguiente.
2. A veces una canción falla, salta a la siguiente y esa también falla (cadena).
3. Velocidad de arranque: debe sentirse instantáneo.

Contexto obligatorio antes de tocar código:
- Lee docs/IPHONE_BACKGROUND_CONTINUITY.md, docs/QUEUE_CONTINUITY.md,
  docs/PLAYBACK_STABILITY.md, docs/ASTRA_4.md y `git log --oneline -15`.
- Reglas de iOS: con la pantalla bloqueada, en cuanto el <audio> deja de sonar,
  iOS puede congelar la página. setTimeout/Promise/fetch pueden no completarse.
  Toda transición (fin de canción, Siguiente, fallo) debe asignar el nuevo src y
  llamar play() de forma SÍNCRONA dentro del callback del evento de medio o del
  handler de mediaSession. Nunca dejes navigator.mediaSession.playbackState en
  'none' mientras se quiere seguir reproduciendo.
- En iPhone el camino es proxyOnlyAudio: /api/audio/<id>?native=1&playback=<id>
  servido por NativePlayback (audio_playback.py) sobre PreparedAudioCache
  (audio_prepared.py) y PartialMP4 (audio_partial.py).

Método (repite hasta que no queden hallazgos):
1. Para cada síntoma, traza el flujo exacto en el código (cita archivo:línea) y
   enumera cada punto donde la transición depende de un timer, de una promesa,
   de red, o de un estado que caduca (expires/ttl). Sospecha también de cachés
   cuya caducidad local se use como validez real de una URL.
2. Reproduce cada hipótesis con un test que FALLE antes del arreglo:
   - Lógica de servidor: tests/test_*.py (unittest).
   - Reproductor: tests/queue_continuity_check.py y tests/play_intent_check.py
     (Playwright + audio real). Para los casos de iPhone usa el user agent de
     iPhone en el contexto, simula document.hidden y NO avances el reloj.
3. Corrige con el cambio mínimo, en el estilo compacto del archivo. Sin
   refactors no pedidos. Comenta el porqué solo cuando no sea obvio.
4. Verifica con todo esto, y que todo pase:
     python -m unittest discover -s tests
     python tests/queue_continuity_check.py
     python tests/play_intent_check.py
     python tests/player_controls_check.py
     node tests/prefetch_priority_check.cjs
     node tests/service_worker_audio_check.cjs
     node --check web/online.js
   Si un test ya fallaba antes de tus cambios, averigua si es el test o la app;
   arregla el que esté mal y dilo.
5. Si cambias algo en web/, sube la versión de CACHE en web/service-worker.js
   para que los iPhone descarguen el código nuevo.

Velocidad: mide producción con una cuenta QA aislada (como hace
tests/astra_production_check.py) y como mínimo 12 canciones de varias búsquedas:
tiempo del sondeo de 2 bytes, de los primeros 256 KB y de un rango a mitad de
canción; cabecera x-ivox-audio-source; porcentaje de éxito. Reporta medianas y
peores casos. No es un test de carga: no más de ~20 canciones.

Límites que debes respetar:
- Chromium con user agent de iPhone NO es un iPhone real. Di claramente qué
  quedó probado en automático y qué requiere prueba física (bloquear el iPhone
  con la PWA instalada, dejar terminar 3 canciones, pulsar Siguiente bloqueado).
- No hagas commit, push ni despliegue sin preguntarme. Push a main = producción.
- No borres archivos sin seguimiento (videos, scripts del reel, etc.).

Entrega final, en español y breve:
- Tabla: bug → causa (archivo:línea) → arreglo → test que lo cubre.
- Resultados de los tests y de la medición de velocidad, con números.
- Qué falta probar en un iPhone físico y cómo.
```
