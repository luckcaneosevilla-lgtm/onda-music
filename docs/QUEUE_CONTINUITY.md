# Continuidad de cola y recomendaciones

Corrección del 29 de octubre de 2026.

Se reprodujo un bloqueo al cambiar de canción o lista mientras la radio anterior
seguía pendiente: la cola nueva reutilizaba una promesa ligada a la anterior.
Las peticiones ahora pertenecen a su cola, se cancelan al sustituirla y sus
respuestas antiguas no pueden modificar ni desbloquear una cola distinta.

Otros cambios:

- La radio tiene un límite total de ocho segundos; al agotarlo se intenta continuar
  con la biblioteca disponible. El proveedor puede seguir sin ofrecer canciones;
  no se garantiza continuidad sin red o sin pistas disponibles.
- Una canción añadida durante la espera se reproduce aunque la radio no devuelva
  recomendaciones. La transición consulta la cola actual, no solo la respuesta.
- Pausar durante la espera cancela el avance pendiente.
- Pulsar varias veces «siguiente» al final no salta varias canciones cuando llega
  una única respuesta de recomendaciones.
- La radio no incorpora duplicados dentro de una misma respuesta, respeta su
  desactivación y una nueva radio reinicia el contador de fallos anterior.
- El estado de los botones siguiente se actualiza al cambiar Autoplay.
- Se actualiza el app shell a v17 para distribuir el JavaScript corregido.

Validación:

```text
python tests/queue_continuity_check.py
python tests/listener_check.py
python tests/play_intent_check.py
python tests/player_controls_check.py
python -m unittest discover -s tests -p 'test_*.py'
```

Las pruebas de navegador usan la app completa con cuentas desechables, audio WAV
real y respuestas controladas. Incluyen final natural, fin sin evento y pausa del
navegador cerca del final. La lógica de temporizadores se verifica con reloj
controlado; no equivale a una prueba en un iPhone físico. Se corrige además la
duración del fixture de reconexión: un atasco en el segundo 12 de un WAV de 60 s
no debe identificarse como el final de una canción de 10 s.
