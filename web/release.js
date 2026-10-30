// Release notice shared by existing accounts and first-time listeners.
(() => {
  const version='2.0',seen=new Set();
  const mascot=`<svg class="anubis" viewBox="0 0 240 260" aria-hidden="true" fill="none">
    <ellipse cx="120" cy="242" rx="66" ry="7" fill="#d4af37" opacity=".15"/>
    <g class="anubis-body" stroke="#d4af37" stroke-width="3" stroke-linejoin="round">
      <path d="M78 221 87 158h66l9 63-42 17Z" fill="#152858"/>
      <path d="m89 161 31 33 31-33M84 180l36 32 36-32M81 199l39 30 39-30"/>
      <path d="M108 143h25v28l-13 13-13-13Z" fill="#17171d"/>
      <g class="anubis-head">
        <path d="M79 100 72 20q0-9 6-1l35 65M133 82l29-67q5-9 6 1l-5 86" fill="#17171d"/>
        <path d="m81 39 7 49 15-3ZM159 39l-16 47 14 5Z" fill="#8f6f1d" stroke="none"/>
        <path d="m80 92 38-18 44 19 9 40-29 30-38-8-24-34Z" fill="#17171d"/>
        <path d="m145 110 48 14-7 14-46 4-23-14" fill="#17171d"/>
        <path d="m184 123 9 1-7 9-7-4Z" fill="#d4af37"/>
        <path d="m136 102 16 5-14 5" fill="#f0d77c"/>
        <path d="m91 109 13 33M98 101l14 44M109 93l13 52" stroke="#8f6f1d" stroke-width="2"/>
        <path d="m145 136 21 1" stroke-width="2"/>
      </g>
      <path d="m85 174-15 31 10 10M155 174l20 24 12-14" stroke-width="10" stroke-linecap="round"/>
      <path d="M190 150v72m-8-68 8-9 8 9-8 10Z" fill="#d4af37"/>
    </g>
    <g class="anubis-notes" fill="#f0d77c"><path d="M42 105v24a6 6 0 1 1-4-6v-22l17-4v10Z"/><path d="M195 63v20a5 5 0 1 1-4-5V60l14 5v7Z"/></g>
  </svg>`;
  const modal=document.createElement('dialog');modal.className='release-dialog';
  modal.setAttribute('aria-labelledby','release-title');modal.setAttribute('aria-describedby','release-description');
  modal.innerHTML=`<button class="release-close" aria-label="Cerrar novedades">×</button>
    <div class="release-art">${mascot}<span class="release-version">IVOX ${version}</span></div>
    <div class="release-copy"><h1 id="release-title">Anubis te da la bienvenida.</h1>
    <p id="release-description">Tu música tiene una nueva versión. Descubre lo que llega con IVOX 2.0.</p>
    <ul><li><strong>Menos espera al pulsar Play.</strong><span>Preparamos el audio mientras exploras los resultados.</span></li>
    <li><strong>Más caminos hacia tu música.</strong><span>Audius cuando hay una coincidencia disponible y una fuente alternativa cuando hace falta.</span></li>
    <li><strong>Tu colección sigue contigo.</strong><span>Conservas tus favoritos, playlists y archivos.</span></li></ul>
    <button class="primary release-start">Entrar a IVOX 2.0</button></div>`;
  document.body.append(modal);
  let account=null;
  const key=id=>`ivox:release:${version}:${id}`;
  const dismiss=()=>{if(account!==null){seen.add(account);try{localStorage.setItem(key(account),'seen')}catch{}}modal.close()};
  modal.querySelector('.release-close').onclick=dismiss;modal.querySelector('.release-start').onclick=dismiss;
  modal.addEventListener('cancel',event=>{event.preventDefault();dismiss()});
  function sync(){
    if(typeof me==='undefined'||!me){if(modal.open)modal.close();account=null;return}
    const container=document.querySelector('.account');if(!container)return;
    account=String(me.id);
    if(!container.querySelector('.release-trigger')){
      const button=document.createElement('button');button.className='release-trigger';button.textContent='IVOX 2.0';button.setAttribute('aria-label','Novedades de IVOX 2.0');
      button.onclick=()=>{if(!modal.open)modal.showModal()};container.prepend(button);
    }
    let dismissed=seen.has(account);try{dismissed ||= localStorage.getItem(key(account))==='seen'}catch{}
    if(!dismissed&&!modal.open&&!document.querySelector('#dialog[open]'))modal.showModal();
  }
  new MutationObserver(sync).observe(document.querySelector('#app'),{childList:true,subtree:true});sync();
})();
