# Investigación REA real — corrección de ONDA

## Herramienta y superficie

Se utilizó el CLI REA 6.4.0 instalado, mediante `rea.cmd` en Windows, sin modificar la política de ejecución. Se consultaron ayuda, esquema y contrato del comando `capture-browser-scenario`. Se abrió únicamente la búsqueda pública `https://open.spotify.com/search/Feid`, sin login, sin pulsar reproducción y sin acceder a una cuenta ajena.

Comando: `rea.cmd capture-browser-scenario docs/rea-public-search.json --format json`. Los primeros intentos fallaron antes de capturar la página. Se ejecutó `rea doctor`; se repitió la observación sin heredar `SSLKEYLOGFILE` en ese proceso, y con acceso de red permitido. El último intento capturó DOM, URL y screenshot correctamente. No se cambiaron instalaciones ni configuraciones globales de REA.

## Observado realmente

La captura muestra el buscador en la barra superior, navegación lateral de biblioteca, filtros por tipo de resultado, un resultado principal para Feid y una lista de canciones con miniatura, título, artista y duración. Hay un aviso de cookies que tapa parte inferior de la página. Se observan controles de login/registro para la sesión anónima. No se comprobó reproducción, cola autenticada, guardado ni sincronización en Spotify.

## Decisiones aplicadas

- Entrada principal por búsqueda online; biblioteca como colección de referencias guardadas.
- Resultados con miniatura, título, canal, duración y acción Play explícita, accesible sin hover.
- Navegación separada de Descubrir, Mi biblioteca y archivos locales opcionales.
- Reproductor persistente fuera del contenido que se reemplaza al navegar.
- ONDA mantiene marca, estilos y código propios. No se copió el catálogo de Spotify ni se usa Spotify como fuente de reproducción.

## Evidencia

`docs/rea-public-search-final.json` conserva el resultado íntegro de REA (incluye DOM público y screenshot codificado); `docs/rea-public-search-summary.json` resume pasos y completitud. `test-results/rea-spotify-search.png` es la imagen extraída de esa evidencia, inspeccionada visualmente. Los errores iniciales y el diagnóstico se conservan en los otros archivos `docs/rea-*.json`.

## Integración del proveedor

Para implementar YouTube se utilizó documentación oficial, no ingeniería inversa:

- https://developers.google.com/youtube/v3/docs/search/list — categoría musical, vídeos insertables, región y paginación.
- https://developers.google.com/youtube/v3/docs/videos/list — detalles, duración y disponibilidad.
- https://developers.google.com/youtube/iframe_api_reference — controles oficiales, dimensiones, eventos y errores.
- https://developers.google.com/youtube/terms/developer-policies — atribución, publicidad, reproducción y metadatos.
- https://developers.google.com/youtube/v3/getting-started — credenciales públicas de API.

La prueba de ONDA sí ejecutó búsquedas reales de Feid con la clave del backend. Ver `docs/verification.md` para lo comprobado y los bloqueos de reproducción observados.
