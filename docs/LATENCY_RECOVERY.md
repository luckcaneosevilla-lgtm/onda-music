# Recuperar velocidad sin bloquear Play

El 29 de octubre, tras corregir los fallos de audio, las primeras peticiones de
CNCO seguían tardando 11,69 y 12,25 segundos. Server-Timing separó la causa:
11,00–11,18 s resolviendo la dirección y 0,26–0,39 s conectando con el audio.
Repetir las mismas peticiones con la dirección en caché tardó 0,74–0,84 s.

La mitigación anterior desactivó las precargas en la instancia de un worker.
Se recuperan ahora con un máximo de dos candidatos pendientes y una única
extracción simultánea. Si Play pide otra canción, se cancela la extracción
especulativa, incluido su proceso JavaScript hijo, y se libera el worker para
la canción elegida. Si es la misma canción, se comparte el trabajo en curso.
Se mantienen la prioridad del foreground y la ausencia de fallos negativos por
ocupación. No se reintroduce el calentamiento triple al arrancar.

Se intenta primero el cliente ligero sin omitir peticiones de configuración;
si no extrae audio se conserva el recorrido compatible. Si la URL es rechazada,
se conserva la renovación compatible del relay. Las pruebas ejecutan el worker
con un proveedor controlado para verificar ambas rutas de respaldo, y simulan
una precarga bloqueada para exigir que otro Play la interrumpa rápidamente.

El prefijo de audio vuelve a prepararse con el mecanismo existente de 500 KiB.
La medición pública posterior debe distinguir una canción preparada de una
canción fría, y tiempo de primeros bytes de tiempo hasta HTMLMediaElement.playing.
No se promete menos de un segundo universal ni se considera el tamaño de la
caché una prueba de reproducción.

La prueba pública inicial con precarga dio 122 ms desde Play hasta playing y
165 ms desde el fin del primer audio hasta playing del siguiente. Las dos
canciones reprodujeron más de ocho segundos, sin errores JavaScript.

Al probar fuera de los primeros resultados se detectó además que una URL del
cliente ligero podía llevar al respaldo alternativo antes de probar la ruta
normal. Se conserva el motor de origen en memoria para pedir primero la ruta
normal cuando se rechaza esa URL; los demás rechazos conservan el respaldo
alternativo. Hay una prueba específica de URL ligera rechazada con HTTP 403.
