// Offline listening (IVOX Premium). Whole songs are kept on this device with the
// Cache API under the keys the player already checks first (level 0 in startOnline),
// so a downloaded song starts at once and uses no data. Titles and covers live in
// localStorage; the audio never leaves the device.
const OFFLINE_KEY='ivox-offline';
const offline={
  read(){try{return JSON.parse(localStorage.getItem(OFFLINE_KEY)||'{}')}catch{return {}}},
  write(map){try{localStorage.setItem(OFFLINE_KEY,JSON.stringify(map))}catch{}},
  has(id){return !!this.read()[id]&&cachedIds.has(id)},
  entries(){return Object.values(this.read()).filter(e=>cachedIds.has(e.item.id)).sort((a,b)=>b.saved-a.saved)},
  items(){return this.entries().map(e=>e.item)},
  premium(){return me?.plan==='premium'},
  pending:[],running:false,done:0,failed:0,
  add(items){
    if(!this.premium()){offlineUpsell();return}
    if(!('caches' in window)){notify('Este navegador no permite guardar música sin conexión.');return}
    const fresh=items.filter(v=>v&&v.provider!=='local'&&v.available!==false&&!this.has(v.id)&&!this.pending.some(p=>p.id===v.id));
    if(!fresh.length){notify(items.length>1?'Estas canciones ya están disponibles sin conexión.':'Ya está disponible sin conexión.');return}
    this.pending.push(...fresh);
    // Ask the browser not to evict the downloads when space runs low.
    navigator.storage?.persist?.().catch(()=>{});
    notify(fresh.length>1?`Descargando ${fresh.length} canciones para escuchar sin conexión…`:`Descargando «${fresh[0].title}» para escuchar sin conexión…`);
    this.run();
  },
  // One song at a time, so downloads never compete with what is playing.
  async run(){
    if(this.running)return;this.running=true;this.done=0;this.failed=0;
    while(this.pending.length){
      this.progress();
      try{await this.fetchOne(this.pending[0]);this.done++}
      catch(e){this.failed++;if(e.name==='QuotaExceededError'){notify('No queda espacio en este dispositivo para más descargas.');this.pending=[];break}}
      this.pending.shift();
    }
    this.running=false;this.progress();
    notify(this.failed?`Descargas listas: ${this.done}. No se pudieron guardar ${this.failed}.`:this.done===1?'Lista para escuchar sin conexión.':`${this.done} canciones listas para escuchar sin conexión.`);
  },
  async fetchOne(v){
    const url=v.provider==='piped'?'/api/audio/'+v.provider_id:(await api('/api/online/play/'+encodeURIComponent(v.id).replace('%3A',':')+'?format=mp4')).url;
    const response=await fetch(url),type=(response.headers.get('Content-Type')||'').split(';')[0];
    if(!response.ok||!/^(audio|video)\//.test(type))throw Error('audio_unavailable');
    const blob=await response.blob();if(blob.size<10000)throw Error('audio_unavailable');
    await(await caches.open(AUDIO_CACHE)).put(audioKey(v.id),new Response(blob,{headers:{'Content-Type':type,'Content-Length':String(blob.size)}}));
    cachedIds.add(v.id);
    const map=this.read();map[v.id]={item:{...v,radio:false},saved:Date.now(),size:blob.size};this.write(map);
    refreshOffline();
  },
  async remove(id){
    const map=this.read();delete map[id];this.write(map);cachedIds.delete(id);
    try{await(await caches.open(AUDIO_CACHE)).delete(audioKey(id))}catch{}
    refreshOffline();notify('Quitado de descargas.');
  },
  progress(){const el=$('#offline-progress');if(el)el.textContent=this.running?`Descargando ${this.done+this.failed+1} de ${this.done+this.failed+this.pending.length}…`:''}
};
window.offlineHas=id=>offline.has(id);
window.offlineLookup=id=>offline.read()[id]?.item;
window.offlineForget=id=>{const map=offline.read();if(map[id]){delete map[id];offline.write(map)}};

function offlineUpsell(){
  openDialog(`<h2>Escucha sin conexión con Premium</h2><p>Guarda canciones y playlists completas en este dispositivo: suenan al instante, sin gastar datos y aunque no tengas señal.</p><div class="tools"><button id="see-plan" class="primary">Ver Mi plan</button></div>`);
  $('#see-plan').onclick=()=>{dialog.close();history.pushState({},'','/plan');render().catch(e=>notify(e.message))};
}
// Redraw whatever shows download state: the Downloads tab, row badges and the sheet chip.
function refreshOffline(){
  if(location.pathname==='/library'&&onlineSection==='downloads')drawOnlineLibrary();
  syncOfflineChip();
}
function drawDownloads(el){
  const entries=offline.entries(),items=entries.map(e=>e.item),total=entries.reduce((n,e)=>n+(e.size||0),0);
  const empty=`<div class="empty"><h2>Tu música, también sin señal</h2><p>Abre ••• en cualquier canción y elige «Guardar sin conexión», o usa «↓ Sin conexión» en una playlist o en tus favoritos.</p></div>`;
  el.innerHTML=`<div class="topline"><h2>Descargas</h2><small class="muted">${items.length} ${items.length===1?'canción':'canciones'} · ${bytes(total)} en este dispositivo</small></div><p id="offline-progress" class="muted" role="status"></p>${offline.premium()?'':'<p class="source-notice">Guardar nuevas descargas es parte de IVOX Premium.</p>'}${items.length?`<div class="artist-actions collection-actions"><button class="primary" data-downloads="play">▶ Reproducir</button><button data-downloads="shuffle" aria-label="Reproducir en aleatorio">⤮ Aleatorio</button></div><div id="library-videos">${videoCards(items,'downloads')}</div>`:empty}`;
  bindVideoCards(el,items,'downloads');
  el.querySelectorAll('[data-downloads]').forEach(b=>b.onclick=()=>playList(items,0,b.dataset.downloads==='shuffle'));
  offline.progress();
}

// Sheet chip: save or remove the song that is playing.
function syncOfflineChip(){
  const chip=$('#sheet-offline'),v=audioQueue[audioIndex];if(!chip)return;
  chip.hidden=!v||v.provider==='local';const on=!!v&&offline.has(v.id);
  chip.setAttribute('aria-pressed',String(on));chip.querySelector('.chip-label').textContent=on?'Sin conexión ✓':'Sin conexión';
}
$('#sheet-offline').onclick=()=>{const v=audioQueue[audioIndex];if(!v)return;offline.has(v.id)?perform(()=>offline.remove(v.id)):offline.add([v])};
{const track=nowPlaying.track;nowPlaying.track=function(item){track.call(this,item);syncOfflineChip()}}

// No network at startup: play the downloads instead of showing the sign-in form.
// Back online, the regular session check in app.js restores the full app.
window.offlineStart=async error=>{
  if(navigator.onLine&&!(error instanceof TypeError))return false;
  await cachedReady;const items=offline.items();if(!items.length)return false;
  me={id:'',name:'',role:'user',plan:'premium',offline:true};
  app.innerHTML=`<main class="content offline-page"><div class="logo">${LOGO}IVOX</div><p class="eyebrow">Sin conexión</p><h1>Tu música sigue aquí.</h1><p class="muted">Escucha tus descargas. Cuando vuelva la señal, IVOX se conecta solo.</p><div class="artist-actions collection-actions"><button class="primary" data-downloads="play">▶ Reproducir todo</button><button data-downloads="shuffle">⤮ Aleatorio</button></div><div id="offline-list">${items.map((v,i)=>`<button class="offline-row" data-offline-play="${i}">${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" width="48" height="48">`:'<span aria-hidden="true">♫</span>'}<span><strong>${esc(v.title)}</strong><small>${esc(v.channel||'')}</small></span></button>`).join('')}</div></main>`;
  app.querySelectorAll('[data-downloads]').forEach(b=>b.onclick=()=>playList(items,0,b.dataset.downloads==='shuffle'));
  app.querySelectorAll('[data-offline-play]').forEach(b=>b.onclick=()=>playList(items,Number(b.dataset.offlinePlay)));
  return true;
};
window.addEventListener('online',()=>{if(me?.offline)load().catch(()=>{})});
