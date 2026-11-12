// External script preserves Ivox-Astra's CSP: no inline script or unsafe-inline needed.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/', updateViaCache: 'none'})
      .catch(error => console.warn('No se pudo registrar el app shell de Ivox-Astra:', error));
  });
}

// Compare the version loaded with this document with the deployed release.
(() => {
  const loaded=document.querySelector('meta[name="ivox-version"]')?.content;
  if(!loaded)return;
  const release=document.querySelector('meta[name="ivox-release"]')?.content||'6.0';
  const badge=document.createElement('small');badge.className='app-version';badge.textContent='Ivox-Astra '+release;badge.title='Compilación '+loaded;document.body.append(badge);
  const notice=document.createElement('aside');notice.className='app-update';notice.hidden=true;notice.setAttribute('role','status');
  const text=document.createElement('span'),button=document.createElement('button');button.textContent='Recargar ahora';button.className='primary';
  notice.append(text,button);document.body.append(notice);
  let updateReady=false;
  function activated(worker){
    return new Promise((resolve,reject)=>{
      const done=error=>{clearTimeout(timer);worker.removeEventListener('statechange',check);error?reject(error):resolve()};
      const check=()=>{if(worker.state==='activated')done();else if(worker.state==='redundant')done(Error('No se pudo instalar la actualización.'))};
      const timer=setTimeout(()=>done(Error('La actualización sigue pendiente. Vuelve a intentarlo.')),25000);
      worker.addEventListener('statechange',check);check();
    });
  }
  button.onclick=async()=>{
    if(!updateReady){notice.hidden=true;return}
    button.disabled=true;button.textContent='Actualizando…';
    try{
      const registration=await navigator.serviceWorker?.getRegistration();
      await registration?.update();
      // update() can resolve while cache.addAll is still downloading. Reload
      // only after the new worker has cached the shell and claimed this page.
      const worker=registration?.installing||registration?.waiting||registration?.active;
      if(worker)await activated(worker);
      location.reload();
    }catch{
      text.textContent='No se pudo completar la actualización. Comprueba la conexión y reintenta.';
      button.disabled=false;button.textContent='Reintentar';
    }
  };
  let checking=false;
  async function check(){
    if(document.hidden||checking)return;checking=true;
    try{const r=await fetch('/version.json',{cache:'no-store'});if(!r.ok)return;const {version,release:availableRelease}=await r.json();if(typeof version!=='string')return;
      if(version!==loaded){updateReady=true;button.textContent='Recargar ahora';button.hidden=false;text.textContent='Nueva actualización disponible · Ivox-Astra '+(typeof availableRelease==='string'?availableRelease:release);notice.hidden=false}
      else{notice.hidden=true;const previous=localStorage.getItem('ivox:last-version');if(previous!==loaded){text.textContent='Actualización instalada · Ivox-Astra '+release;notice.hidden=false;button.textContent='Entendido';button.hidden=false}localStorage.setItem('ivox:last-version',loaded)}
    }catch{}finally{checking=false}
  }
  document.addEventListener('visibilitychange',check);window.addEventListener('online',check);setInterval(check,60000);check();
})();
