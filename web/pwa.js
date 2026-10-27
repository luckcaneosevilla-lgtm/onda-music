// External script preserves IVOX's CSP: no inline script or unsafe-inline needed.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/', updateViaCache: 'none'})
      .catch(error => console.warn('No se pudo registrar el app shell de IVOX:', error));
  });
}
