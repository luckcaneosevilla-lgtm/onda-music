# Simulación de la app instalada en iPhone

12 de noviembre de 2026. Correcciones guardadas en el proyecto local.

## Fallos reproducidos y corregidos

1. **Reabrir con una conexión inestable mostraba el acceso de nuevo.** Una
   respuesta temporal 503 de `/api/me` terminaba en `auth()` aunque la cookie
   siguiera siendo válida. Ahora aparece una pantalla de reconexión que permite
   reintentar y reanuda automáticamente al volver la red. Los errores reales
   401 y la suspensión de cuenta siguen requiriendo acceso. La petición de
   sesión tiene además un plazo de doce segundos para no esperar indefinidamente.
2. **La actualización recargaba antes de guardar todos los archivos.** Se
   reprodujo reteniendo la descarga de `sheet.js` durante la instalación del
   worker. `registration.update()` había terminado, pero la caché seguía
   incompleta. Ahora se espera a la activación; si falla, la app permanece en
   su versión funcional y ofrece reintentar. La prueba interrumpe la descarga
   con 503 y comprueba también la recuperación posterior.
3. **Conexiones rechazadas durante ráfagas de archivos.** En las pruebas
   completas aparecieron `ERR_CONNECTION_REFUSED` al solicitar scripts. El
   servidor de Python 3.12 usaba una cola de escucha de cinco conexiones.
   Se amplió a 128. Una prueba HTTP abre 32 conexiones simultáneas y verifica
   que todos los recursos se reciben completos con estado 200.

## Qué se simuló

`python tests/iphone_pwa_check.py` utiliza Chromium y WebKit, perfil de iPhone
13, viewport táctil, User-Agent de iPhone, indicador `navigator.standalone`
simulado y almacenamiento separado. La cuenta y el servidor son temporales.
El acceso se realiza por el formulario real. Se instala el Service Worker real
y se prueba su caché, sin sustituirlo por un mock.

Se comprueban anchos de 320, 375, 390 y 430 píxeles y orientación horizontal de
844 × 390. Los doce escenarios (seis por motor) cubren acceso/reapertura con
sesión conservada, red inestable, reapertura offline sin descargas,
reapertura con descargas, actualización lenta y actualización fallida con
reintento. Chromium también reproduce y encadena las dos descargas sin red.

El informe se genera en `test-results/iphone-pwa-report.json`. Las capturas
de reconexión y descargas se guardan en `test-results/iphone-pwa-*-*.png`.

Comprobaciones adicionales:

```text
python -m unittest discover -s tests -p 'test_*.py'
python tests/play_intent_check.py
python tests/queue_continuity_check.py
node --check web/app.js
node --check web/pwa.js
git diff --check
```

## Límites

Esta prueba simula las condiciones de una PWA instalada; no descarga una app
en un iPhone físico ni ejecuta su sistema operativo. WebKit para Windows no
puede decodificar el WAV de prueba en esta máquina (también falla en una página
independiente), por lo que su reproducción permanece sin validar. Chromium
sí decodifica el audio, pero no demuestra el comportamiento de iOS con la
pantalla bloqueada. Tampoco se midió aquí un proveedor musical real.

Apple explica las diferencias de almacenamiento entre Safari y una app de
pantalla de inicio en [What’s new in web apps](https://developer.apple.com/videos/play/wwdc2023/10120/).
La prueba usa contextos separados para no asumir que comparten sesión.

No se desplegó en producción ni se reinstaló la app del usuario. No se
modificaron las cuentas reales ni los cambios previos de resolución/remux.
