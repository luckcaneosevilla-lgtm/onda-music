# Auditoría del progreso, buffer y seek

## Actualización: correcciones aplicadas

Tras la autorización posterior se implementaron las correcciones en `web/player-controls.js`, `web/index.html`, `web/player.css` y `web/online.js`. La barra tiene una superficie táctil real de 44 px; el tiempo previsualizado se mantiene durante el arrastre y el buffer se representa mediante segmentos independientes. Se añadieron volumen desplegable, mute y gestos sobre título/carátula (izquierda siguiente, derecha anterior), excluyendo sliders, botones y cola. En móvil, abrir Más controles para acceder al volumen. Si el navegador impide cambiar el volumen por software, el panel indica usar los botones del dispositivo.

El servidor únicamente añade el nuevo archivo JS a su lista de estáticos. El service worker pasa a caché v6 e incluye ese archivo. No se hizo commit.

Verificación: `python tests/player_controls_check.py`, Chromium con viewport 390×844 y gestos táctiles CDP, más clic/teclado en escritorio. Se comprueba seek real de un WAV de 60 s hasta 45 s, previsualización ante actualizaciones concurrentes, cancelación, segmentos buffered simulados con huecos, volumen 35%, mute, cierre con Escape, swipes y ausencia de cambios de cola durante seek/scroll vertical. Informe `test-results/player-controls-report.json`, captura `test-results/player-controls-mobile.png`. Seis pruebas de seguridad existentes también pasan. La prueba general `tests/browser_check.py` no completó su paso de administración: timeout esperando el título de `/admin`; no se declara como pasada ni se modificó esa prueba.

Alcance: estas pruebas no sustituyen Safari/iPhone ni Chrome/Android físicos, y no ejercitan redes de proveedores. El documento original de investigación se conserva a continuación como referencia histórica.

28 de octubre de 2026. Solo investigación: no se modifica HTML, CSS, JS ni backend mientras Claude trabaja. Las líneas corresponden al código leído en esta revisión; pueden desplazarse con sus cambios.

## Lo que ya está implementado

`web/index.html:17` contiene la pista `.np-seek`, buffer `#np-buffer`, progreso `#np-fill` y un `input[type=range]` `#np-range`, escala 0–1000. `web/online.js:218` actualiza tiempos y progreso; las líneas 223–225 escuchan `timeupdate`, `durationchange` y `progress`. En 231 se previsualiza el arrastre y en 232, al confirmar el cambio, se asigna `audio.currentTime`. Por tanto **ya hay seek personalizado**; no hay que construir otro reproductor. El audio secundario de archivos locales usa controles nativos (`web/index.html:38`).

La compatibilidad de estas APIs en navegadores actuales no demuestra que este diseño funcione bien en un dispositivo real. No se ha accedido a un iPhone con Safari ni a un Android con Chrome en esta investigación. La emulación Chromium en PC no sustituye a esos dispositivos ni a sus políticas de audio.

## Problemas localizados

| Archivo y línea | Hallazgo | Cambio propuesto |
|---|---|---|
| `web/player.css:9` y `:14` | La pista tiene 4 px y `overflow:hidden`; el input se extiende por fuera, pero el padre lo recorta. Ampliar la altura del input no asegura un área táctil mayor. El hover no es solución en móvil. | Separar hit area de 44–48 px de la pista visual de 4–6 px; recortar solo las capas decorativas. Añadir pulgar y foco visibles. |
| `web/online.js:219–220`, `:231` | `seeking` protege el valor del input, pero no el texto ni el relleno. `timeupdate`/`progress` pueden reemplazar la previsualización por el tiempo real mientras se arrastra. | Durante el arrastre mantener preview y no repintar esos dos elementos con tiempo real. |
| `web/online.js:221` | Usa el fin del último intervalo buffered como una barra desde cero. Con `[0,30]` y `[200,240]` pinta 0–240 aunque 30–200 no esté descargado. | Dibujar todos los intervalos reales como segmentos, cada uno con su inicio y fin. |
| `web/online.js:231–232` | Solo valida `duration > 0`: Infinity pasa; no consulta `seekable`, ni maneja cancelación del arrastre o cambio de pista durante la interacción. | Validar valores finitos y rangos, restaurar estado en cancelación/cambio de fuente y capturar fallos al asignar tiempo. |
| `web/online.js:242` | Media Session asigna `seekTime` sin el mismo control de disponibilidad. | Reutilizar la función de seek validada en ambos controles. |
| `web/index.html:17` | Tiene nombre accesible, pero falta una descripción legible del tiempo y no se deshabilita explícitamente mientras falta metadata. | `aria-valuetext` con tiempo/duración; deshabilitar cuando no haya duración finita o el medio no permita seek. |

Los tres primeros se comprobaron también mediante una prueba aislada y de solo lectura en Chromium de escritorio, con viewport de 390×844 y los HTML/CSS actuales: pista de 4 px, input de 20 px y el punto situado 5 px bajo la pista no recibe el input, sino `online-player`. Al ejecutar la función actual `progress()` con un audio simulado, una previsualización en 80%/240 s volvió a 10%/30 s tras un evento de progreso. Dos intervalos simulados `[0,30]` y `[200,240]` sobre 300 s produjeron una barra continua de 80%. Esto reproduce los defectos de layout y lógica; no es una prueba de sonido, Safari, Android ni de red real. El manejo exacto de gestos, eventos y rangos con cada proveedor queda pendiente de reproducción en dispositivos.

## Significado de las APIs

- `currentTime`: posición en segundos; asignarla solicita un salto. `duration` puede ser NaN antes de metadata o Infinity en un directo. Solo calcular porcentajes con duración finita mayor que cero. [HTMLMediaElement.currentTime](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime).
- `buffered`: intervalos TimeRanges ya disponibles en el buffer, no un porcentaje global descargado. Puede tener huecos o perder datos; se consultan `start(i)` y `end(i)` de cada intervalo. [Buffering y rangos](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Audio_and_video_delivery/buffering_seeking_time_ranges).
- `seekable`: intervalos a los que el navegador puede saltar; no equivale a buffered. Con peticiones Range el medio puede permitir saltar a casi toda la canción aunque aún no esté descargada. Si el medio o servidor no permiten acceso aleatorio, ningún slider garantiza cualquier minuto. [HTMLMediaElement.seekable](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/seekable).

## Diseño de implementación para unificar después

Conservar el range nativo para ratón, dedo, flechas y tecnologías de asistencia. Una envoltura de 44–48 px recibe la interacción; la pista fina centrada contiene segmentos de buffer claros y el progreso dorado/azul encima. Los elementos decorativos no interceptan punteros. Evitar que la pista intercepte el scroll vertical con un manejo de touch innecesario; validar el comportamiento nativo antes de añadir gestos propios.

Algoritmo de renderizado propuesto, no aplicado:

1. Consultar duración finita. Si no existe, desactivar seek y mostrar tiempo desconocido.
2. Para cada intervalo buffered, limitar inicio/fin a la duración y pintar `left = 100 * inicio / duración`, `width = 100 * (fin-inicio) / duración`. No unir huecos.
3. Fuera del arrastre, pintar progreso `100 * currentTime / duración`, valor del range y tiempo accesible. Durante el arrastre, pintar el tiempo de preview.
4. En `input`, actualizar únicamente preview. Al confirmar `change`, convertir escala a segundos y limitarla a `[0,duración]`; comprobar el snapshot de `seekable`. Si el objetivo está fuera, usar el punto válido más cercano, informándolo; si no hay rangos, esperar metadata/progreso y mostrar que todavía no se puede saltar. No limitar a buffered: impediría saltos válidos mediante Range.
5. Asignar `currentTime` una sola vez al confirmar, conservando el estado play/pausa. Capturar errores; esperar `seeked` para reflejar el tiempo que realmente aceptó el navegador. Evitar solicitar nuevas URLs o reinsertar `<audio>` por cada movimiento del dedo.
6. Cancelar preview en `pointercancel`, pérdida de interacción y cambio de pista; evitar que un gesto antiguo aplique su tiempo a la nueva canción. No confirmar dos veces por mezclar eventos pointer y change.
7. Escuchar `loadedmetadata`, `durationchange`, `timeupdate`, `progress`, `seeking`, `seeked` y `emptied`. Opcionalmente usar requestAnimationFrame solo mientras reproduce o se arrastra para suavidad; timeupdate no ocurre a 60 Hz.

## Contrato del backend: comprobar antes de culpar al slider

`server.py:299–301` pasa Range al resolvedor yt-dlp y `:382–384` al proveedor. `server.py:450–460` conserva el estado HTTP y `Content-Length`, `Content-Range`, `Accept-Ranges`; `audio_resolver.py:214–219` contempla 200/206 y 416. Hay infraestructura para seek, pero no demuestra que todas las fuentes lo soporten.

Cuando Claude termine, comprobar con una sesión autorizada y una pista real: pedir un rango de bytes no inicial, verificar 206, Content-Range coherente, longitud correcta y que no se convierta silenciosamente en una descarga completa. No confundir rango de bytes con segundos; el navegador hace ese cálculo. El service worker ya evita interceptar APIs y solicitudes Range (`web/service-worker.js:24–26` aproximadamente); conservar ese comportamiento.

Si un upstream ignora Range y devuelve 200, el proxy no debe inventar 206 ni prometer acceso arbitrario. En MP4 la ubicación de metadata y el formato también influyen. Una URL caducada, un 403/416 o una duración desconocida requieren un mensaje o recuperación de la fuente, no solo una animación de carga.

## Pruebas pendientes en iOS y Android reales

Usar la URL HTTPS final, cuentas de prueba, una pista larga que admita seek y, cuando esté disponible, una por cada nivel de reproducción. Registrar modelo, versión de OS/navegador, proveedor, duration y snapshots buffered/seekable. Probar Safari en pestaña y PWA instalada, Chrome Android en pestaña y posteriormente TWA.

1. Reproducir tras un toque y confirmar que tiempo y progreso avanzan.
2. Clic/tap en 25%, arrastre hasta 75% y atrás a 10%; tras `seeked`, comprobar tiempo con tolerancia razonable (p. ej. 2 s para VOD), que se oye ese punto y que sigue en play si antes reproducía.
3. Repetir pausado: cambia tiempo sin arrancar la canción. Probar inicio y final sin activar un salto de cola accidental por duplicidad de eventos.
4. Saltar lejos de buffered cuando seekable lo permita: comprobar petición de red y recuperación, no bloqueo artificial por buffer.
5. Verificar buffer discontinuo, metadata ausente, duración Infinity, red lenta, pérdida de conexión y fuente sin rangos.
6. Arrastrar despacio mientras llegan timeupdate/progress: la previsualización no debe retroceder. Cambiar canción o cancelar gesto: no aplicar el seek antiguo.
7. En iPhone, probar barra compacta/expandida, arrastre cerca del borde inferior, scroll y rotación. No depender de hover. En Android probar igual y comprobar que el gesto no desplaza toda la página.
8. Teclado: Tab, flechas, Home/End según soporte nativo. VoiceOver/TalkBack: nombre del control y tiempo comprensible. No anunciar cada frame.

**Criterio de cierre:** no declarar “verificado en iOS/Android” hasta ejecutar estas pruebas en esos navegadores reales. La revisión actual confirma implementación y problemas de lógica/CSS; la reproducción real, Range y los gestos siguen pendientes. No se modificaron archivos de aplicación.
