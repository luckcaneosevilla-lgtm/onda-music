# Fallos consecutivos de audio: diagnóstico y recuperación

El 29 de octubre se reprodujeron tres respuestas 502 consecutivas del servidor
publicado para canciones de Manuel Turizo. La búsqueda funcionaba; la entrega de
audio fallaba. Esta comprobación externa es distinta de las pruebas locales con
WAV y proveedores simulados.

Se detectó una incompatibilidad en la imagen: Deno 2.1.4 era anterior al mínimo
2.3.0 documentado por yt-dlp-ejs. Se actualiza a la imagen oficial bin-2.3.3 y se
comprueban el runtime y la presencia del paquete EJS durante la construcción.

Referencia primaria: https://github.com/yt-dlp/yt-dlp/wiki/EJS

El reintento del navegador tampoco garantizaba una extracción nueva: el fallo
seguía almacenado durante treinta segundos. El reintento explícito ahora pide
una renovación, limpia ese fallo y selecciona el recorrido compatible. Se limita
a una renovación por vídeo cada treinta segundos para compartir el resultado
entre solicitudes Range y oyentes, sin provocar extracciones ilimitadas.

Los workers devuelven categorías de fallo permitidas, sin mensajes originales,
credenciales ni URLs firmadas; estas permiten distinguir fallo de formato,
runtime, red o una restricción del proveedor. El watchdog inicial deja treinta
segundos para el presupuesto del backend (hasta doce de cola y quince de
extracción), en lugar de cancelar una extracción válida a los quince segundos.

Estas medidas no garantizan disponibilidad universal de un proveedor externo.
La verificación final debe volver a pedir audio al despliegue real, incluyendo
CNCO, el artista comunicado por el usuario.

En la primera verificación posterior al cambio se observaron 502 seguidos de
503 con conexión rechazada. Se limita por ello el resolver compartido a un
worker y se elimina la extracción triple de calentamiento al arrancar. Esto
reduce los solvers JavaScript simultáneos y deja arrancar el HTTP sin esperar
extracciones. La saturación/reinicio es una hipótesis basada en esas respuestas;
no se dispone de los registros privados del proveedor para afirmar un OOM.

La siguiente prueba secuencial confirmó `resolution_busy` y `worker_unavailable`:
las precargas de búsqueda ocupaban el único worker antes del Play. En este modo
se desactivan las extracciones especulativas y se prioriza la intención de Play
desde antes de esperar el bloqueo por canción. Una cola ocupada deja de guardar
un fallo negativo de la canción durante treinta segundos.
