# Eficiencia del reproductor — 12 de noviembre de 2026

Cambios aplicados en el proyecto local:

- Los scripts, estilos y fuentes del app shell se sirven directamente desde
  su caché de versión. Una visita con caché caliente evita volver a descargar
  esos recursos. La navegación conserva su comprobación de red y respaldo offline.
- El servidor identifica la caché con el hash real de la aplicación. Antes
  intentaba sustituir un literal v26 que ya no coincidía con el worker v33;
  ahora admite el número de versión del archivo y distribuye el hash actual.
- El reproductor evita procesar dos veces los mismos eventos de progreso.
  Las barras de búfer conservan sus nodos cuando los rangos no cambian.
  Cien actualizaciones consecutivas reutilizan el mismo segmento en las pruebas.
- Con la página oculta se omiten los dibujos de progreso y se detienen los
  ciclos de letras y visualizador. Los callbacks de audio, cola, recuperación
  y Media Session siguen ejecutándose. Las letras y el visualizador reanudan
  su ciclo al volver o al reabrir el reproductor.
- La sesión deja de consultarse cada quince segundos con la página oculta
  o sin conexión; vuelve a comprobarse al regresar o recuperar la red.

Validación reproducible con medios WAV y cuentas locales desechables:

```text
python -m unittest discover -s tests -p 'test_*.py'
python tests/player_controls_check.py
python tests/play_intent_check.py
python tests/queue_continuity_check.py
python tests/cross_browser_player_check.py
node tests/service_worker_audio_check.cjs
node tests/prefetch_priority_check.cjs
```

Las pruebas de controles cubren toque, arrastre, teclado, volumen, rangos
discontinuos y reutilización de nodos. Los quince escenarios de intención
cubren primer toque, cambios durante carga, búsqueda de posición, duración,
recuperación y reapertura de letras. Los diecisiete escenarios de continuidad
incluyen fin natural, callbacks sin temporizadores, página oculta, pausas
explícitas y carreras de radio/cola. La prueba del worker confirma que un
script ya guardado se devuelve sin ninguna petición de red.

La prueba entre motores verifica reproducción, pausa, seek, siguiente y fin
natural en Chromium a 390 y 1440 píxeles. WebKit verifica carga, cola,
reproductor completo y ausencia de desbordamiento a ambos anchos. Su audio
queda explícitamente parcial: el WebKit instalado en este Windows rechaza
también un WAV válido en una página independiente (error de medio 4), aunque
canPlayType lo anuncie compatible.

Esto no mide la latencia real de YouTube ni demuestra la continuidad con un
iPhone físico bloqueado. No se ha desplegado ni reiniciado un servidor de
producción. Los cambios previos de resolución/remux y los archivos de reels
del espacio de trabajo se han conservado.
