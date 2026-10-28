# IVOX · Anubis Premium

Landing estática sin frameworks. Negro `#0a0a0c`, oro `#D4AF37`, lapislázuli `#1E3A8A`. El Ojo de Horus vectorial y el sello LC conectan la marca con su fundador, Lucas Caneo.

- `index.html`, `style.css`, `script.js`: presentación adaptable con apariciones al hacer scroll, navegación por teclado y respeto por movimiento reducido.
- `logo.svg`: vector transparente, color dorado, viewBox escalable.
- `loader.html`, `loader.css`, `loader.js`: anillo animado para grabaciones verticales. Oculta los controles antes de grabar; toca la esquina inferior derecha o pulsa Escape para recuperarlos. Pantalla completa depende del navegador; en iPhone se puede grabar la pantalla en vertical aunque esa API no esté disponible.
- `taglines.json`: cinco frases para Reels.

## Abrir

Con el servidor del proyecto: `/landing/` y `/landing/loader.html`. El botón Abrir IVOX apunta a `/`, donde está la app. Para publicar en otro dominio, cambia `APP_URL` en `script.js` y también los enlaces `data-app-link` del HTML (respaldo sin JavaScript) por la URL real de la app.

Las fuentes Cinzel e Inter se copian en `fonts/` con su licencia OFL. La carpeta se puede alojar como sitio estático independiente, sin dependencias externas.

## Mensajes de producto

El titular sigue el concepto solicitado. El texto aclara que IVOX no añade anuncios propios y que la disponibilidad depende del proveedor; no garantiza eliminar anuncios de terceros ni un catálogo ilimitado. Buscar y escuchar no requieren Premium. Existe descarga en la app actual; el modo offline integrado no está implementado y figura como próximamente. No se promete audio sin pérdida.
