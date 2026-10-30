// External script preserves IVOX's CSP: no inline script or unsafe-inline needed.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/', updateViaCache: 'none'})
      .catch(error => console.warn('No se pudo registrar el app shell de IVOX:', error));
  });
}

// Compare the version loaded with this document with the deployed release.
(() => {
  const loaded=document.querySelector('meta[name="ivox-version"]')?.content;
  if(!loaded)return;
  const badge=document.createElement('small');badge.className='app-version';badge.textContent='Versi?n '+loaded;badge.title='Versi?n cargada de IVOX';document.body.append(badge);
  const notice=document.createElement('aside');notice.className='app-update';notice.hidden=true;notice.setAttribute('role','status');
  const text=document.createElement('span'),button=document.createElement('button');button.textContent='Recargar ahora';button.className='primary';
  notice.append(text,button);document.body.append(notice);
  let updateReady=false;
  button.onclick=async()=>{if(!updateReady){notice.hidden=true;return}button.disabled=true;button.textContent='Actualizando?';try{const r=await navigator.serviceWorker?.getRegistration();await r?.update()}catch{}location.reload()};
  let checking=false;
  async function check(){
    if(document.hidden||checking)return;checking=true;
    try{const r=await fetch('/version.json',{cache:'no-store'});if(!r.ok)return;const {version}=await r.json();if(typeof version!=='string')return;
      if(version!==loaded){updateReady=true;button.textContent='Recargar ahora';button.hidden=false;text.textContent='Nueva actualizaci?n disponible ? '+version;notice.hidden=false}
      else{notice.hidden=true;const previous=localStorage.getItem('ivox:last-version');if(previous!==loaded){text.textContent='Nueva actualizaci?n instalada ? '+loaded;notice.hidden=false;button.textContent='Entendido';button.hidden=false}localStorage.setItem('ivox:last-version',loaded)}
    }catch{}finally{checking=false}
  }
  document.addEventListener('visibilitychange',check);window.addEventListener('online',check);setInterval(check,60000);check();
})();
