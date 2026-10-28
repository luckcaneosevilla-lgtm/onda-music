// The audio element is outside #app and survives all SPA navigation. It is the
// only one: online songs, device copies and imported files all play through it.
// Seek bar, buffer and volume belong to player-controls.js (IVoxPlayerControls).
const onlineAudio=$('#online-audio');
let onlineData={saves:[],playlists:[],items:{},recent:[]},onlineStatus;
let searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};
let searchController,searchVersion=0,onlineSection='favorites',artistView=null;
let audioQueue=[],audioIndex=-1,playVersion=0,recordedReference=null,advanceTimer,failedReferences=new Set(),activeLevel=0,activeEngine='',resolutionBusy=false;
// One controller per playback: changing song aborts every request of the previous one.
let playController=null,skipTimer=null,watchdog,failStreak=0,audioUnlocked=false,resumeAt=0,fixingDuration=false;
let autoplay=(()=>{try{return localStorage.getItem('ivox-autoplay')!=='0'}catch{return true}})(),radioFor=null,radioPromise=null;
const duration=n=>n==null?'—':`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
const savedVideo=id=>onlineData.saves.find(s=>s.video_id===id)||{};
const videoById=id=>searchView.items.find(v=>v.id===id)||searchView.artist?.songs?.find(v=>v.id===id)||artistView?.songs?.find(v=>v.id===id)||onlineData.items[id]||audioQueue.find(v=>v.id===id);
const isSafari=/^((?!chrome|android|crios|fxios|edg).)*safari/i.test(navigator.userAgent);
// YouTube Music thumbnails come at 60-120 px; the same URL serves any size.
const bigArt=url=>String(url||'').replace(/=w\d+-h\d+/,'=w600-h600');
// iOS/Chrome only let an element start playback from a gesture; the first touch anywhere unlocks it for later async plays.
function unlockAudio(){
  ['click','touchend','keydown'].forEach(t=>document.removeEventListener(t,unlockAudio,true));
  try{const Context=window.AudioContext||window.webkitAudioContext;if(Context){const context=new Context();context.resume().then(()=>context.close()).catch(()=>{})}}catch{}
  if(onlineAudio.getAttribute('src'))return;
  onlineAudio.src='/silence.wav';onlineAudio.play().then(()=>{audioUnlocked=true;if(onlineAudio.getAttribute('src')==='/silence.wav'){onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load()}}).catch(()=>{});
}
['click','touchend','keydown'].forEach(t=>document.addEventListener(t,unlockAudio,true));
function prefetchNext(){prefetchAudio(audioQueue.slice(audioIndex+1,audioIndex+3))}
// Songs heard for a few seconds are stored whole in Cache Storage, so replaying a
// recent one starts instantly from the device. cachedIds mirrors the cache so the
// common (uncached) path stays synchronous and keeps the click's play gesture.
const AUDIO_CACHE='ivox-audio-v1',AUDIO_KEEP=25,AUDIO_MAX_BYTES=25*1024*1024;
const audioKey=id=>'/ivox-audio/'+encodeURIComponent(id);
let cachedIds=new Set(),storingAudio=new Set(),blobUrl=null;
const audioOrder=()=>{try{return JSON.parse(localStorage.getItem('ivox-audio-order')||'[]')}catch{return []}};
const saveAudioOrder=order=>{try{localStorage.setItem('ivox-audio-order',JSON.stringify(order))}catch{}};
if('caches' in window)caches.open(AUDIO_CACHE).then(c=>c.keys()).then(keys=>{cachedIds=new Set(keys.map(r=>decodeURIComponent(new URL(r.url).pathname.slice(12))))}).catch(()=>{});
async function localAudio(item){
  try{
    const response=await(await caches.open(AUDIO_CACHE)).match(audioKey(item.id));
    if(!response){cachedIds.delete(item.id);return null}
    saveAudioOrder([item.id,...audioOrder().filter(id=>id!==item.id)]);
    if(blobUrl)URL.revokeObjectURL(blobUrl);
    return blobUrl=URL.createObjectURL(await response.blob());
  }catch{return null}
}
async function keepAudio(item,url){
  if(!item||item.provider==='local'||!url||url.startsWith('blob:')||storingAudio.has(item.id)||cachedIds.has(item.id)||!('caches' in window)||navigator.connection?.saveData)return;
  storingAudio.add(item.id);
  try{
    const response=await fetch(url,{credentials:'same-origin'}),size=Number(response.headers.get('Content-Length')||0);
    // Long mixes would crowd out dozens of songs; they keep streaming instead.
    if(response.status!==200||!size||size>AUDIO_MAX_BYTES){response.body?.cancel();return}
    const blob=await response.blob(),cache=await caches.open(AUDIO_CACHE);
    await cache.put(audioKey(item.id),new Response(blob,{headers:{'Content-Type':response.headers.get('Content-Type')||blob.type||'audio/mpeg'}}));
    cachedIds.add(item.id);
    const order=[item.id,...audioOrder().filter(id=>id!==item.id&&cachedIds.has(id))];
    for(const id of cachedIds)if(!order.includes(id))order.push(id);
    for(const old of order.splice(AUDIO_KEEP)){cachedIds.delete(old);cache.delete(audioKey(old)).catch(()=>{})}
    saveAudioOrder(order);
  }catch{}finally{storingAudio.delete(item.id)}
}
function forgetAudio(item){cachedIds.delete(item.id);if('caches' in window)caches.open(AUDIO_CACHE).then(c=>c.delete(audioKey(item.id))).catch(()=>{})}
// Resolving a YouTube audio URL takes ~1-2 s; doing it before the tap (top results,
// hover, touch, the next songs in the queue) makes Play start almost instantly.
const prefetched=new Set();
function prefetchAudio(items){const ids=items.filter(v=>v&&v.provider==='piped'&&!prefetched.has(v.provider_id)&&!cachedIds.has(v.id)).slice(0,5).map(v=>v.provider_id);if(!ids.length||!me)return;ids.forEach(id=>prefetched.add(id));api('/api/audio/prefetch','POST',{video_ids:ids}).catch(()=>ids.forEach(id=>prefetched.delete(id)))}
function playerState(state){
  $('#online-player').dataset.state=state;const playing=state==='playing';
  $('#np-toggle').setAttribute('aria-label',playing?'Pausar':'Reproducir');
  if('mediaSession' in navigator)navigator.mediaSession.playbackState=playing?'playing':state==='paused'?'paused':'none';
  nowPlaying.state(state);syncCards();
}
// Highlights the row of the current song anywhere it is listed, mirroring the bar state.
function syncCards(){const current=audioQueue[audioIndex]?.id,state=$('#online-player').dataset.state;document.querySelectorAll('.video-row[data-video]').forEach(row=>{const on=row.dataset.video===current&&!$('#online-player').hidden;row.classList.toggle('is-current',on);if(on)row.dataset.state=state;else delete row.dataset.state})}
function nowPlayingArt(item){
  const cover=$('#np-cover'),art=bigArt(item.thumbnail);if(art){cover.src=art;cover.hidden=false;cover.onerror=()=>{cover.hidden=true}}else{cover.hidden=true;cover.removeAttribute('src')}
  if('mediaSession' in navigator&&window.MediaMetadata)navigator.mediaSession.metadata=new MediaMetadata({title:item.title,artist:item.channel||'',album:item.album||'IVOX',artwork:art?[{src:art,sizes:'600x600'}]:[{src:'/icon-512x512.png',sizes:'512x512',type:'image/png'}]});
}
async function downloadOnline(v){
  if(!v||v.provider==='local')return;notify('Preparando descarga de «'+v.title+'»…');
  let url;
  if(v.provider==='piped')url='/api/audio/'+v.provider_id;
  else url=(await api('/api/online/play/'+encodeURIComponent(v.id).replace('%3A',':')+'?format=mp4')).url;
  const name=[v.channel,v.title].filter(Boolean).join(' - ');
  const link=document.createElement('a');link.href=url+(url.includes('?')?'&':'?')+'download='+encodeURIComponent(name);link.download='';
  document.body.append(link);link.click();link.remove();
}
window.cancelOnlineSearch=()=>{searchController?.abort();searchVersion++;searchView.loading=false};
const openLink=link=>link.onclick=e=>{e.preventDefault();history.pushState({},'',link.getAttribute('href'));render().catch(e=>notify(e.message))};

async function onlineState(){const [status,data]=await Promise.all([api('/api/online/status'),api('/api/online/library')]);onlineStatus=status;onlineData=data}
async function discover(){
  await onlineState();if(location.pathname!=='/'||!me)return;
  shell(`<div class="discover-heading"><p class="muted">Tu próxima canción está ahí fuera</p><h1>¿Qué quieres escuchar?</h1><p>Busca un artista, una canción o algo que cambie tu día.</p></div>
    <form id="online-search" class="online-search" role="search"><label class="sr-only" for="online-query">Buscar música online</label><input id="online-query" name="q" type="search" maxlength="120" minlength="2" placeholder="Artista o canción, por ejemplo Feid" value="${esc(searchView.query)}" required autocomplete="off"><button class="primary">Buscar</button></form>
    <div class="search-options"><label>Buscar<select id="search-filter"><option value="songs">Canciones</option><option value="artists">Artistas</option></select></label><label>Fuente<select id="search-source"><option value="auto">Catálogo musical · automático</option><option value="ytmusic">YouTube Music</option><option value="youtube">YouTube · respaldo</option><option value="piped">Piped</option><option value="audius">Audius · independiente</option></select></label><span class="muted">Audio online · Tus listas privadas en IVOX</span></div>
    <div class="recent-heading"><h2>Búsquedas recientes</h2>${onlineData.recent.length?'<button id="clear-recent">Borrar</button>':''}</div><div id="recent-searches" class="tools"></div>
    <div class="quick-access"><a href="/library" data-nav>♡ Tus favoritos</a><a href="/library?section=later" data-nav>◷ Escuchar después</a><a href="/library?section=playlists" data-nav>♫ Tus playlists</a></div>
    <section aria-label="Resultados de búsqueda"><div id="search-results" aria-live="polite"></div></section><p class="provider-note">La fuente aparece en cada resultado. La disponibilidad y las versiones publicadas dependen de cada proveedor.</p>`);
  $('#search-source').value=searchView.source;
  $('#search-filter').value=searchView.filter||'songs';
  $('#online-search').onsubmit=e=>{e.preventDefault();runSearch($('#online-query').value,false,$('#search-source').value)};
  $('#online-query').oninput=()=>{cancelOnlineSearch();searchView.error=null;$('#search-results').innerHTML='<p class="muted">Pulsa Buscar para consultar el catálogo online.</p>'};
  $('#search-source').onchange=()=>{cancelOnlineSearch();searchView.source=$('#search-source').value;if(['piped','audius'].includes(searchView.source))$('#search-filter').value='songs'};
  $('#search-filter').onchange=()=>{cancelOnlineSearch();if($('#search-filter').value==='artists'&&['piped','audius'].includes($('#search-source').value))$('#search-source').value='auto'};
  if($('#clear-recent'))$('#clear-recent').onclick=()=>perform(async()=>{await api('/api/online/recent','DELETE');await discover()});drawRecent();drawSearch();
}
function drawRecent(){const el=$('#recent-searches');if(!el)return;el.innerHTML=onlineData.recent.length?onlineData.recent.map(q=>`<button data-recent="${esc(q)}">${esc(q)}</button>`).join(''):'<p class="muted">Tus búsquedas aparecerán aquí.</p>';el.querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#online-query').value=b.dataset.recent;runSearch(b.dataset.recent,false,$('#search-source').value)})}

// Artist profiles. "34.5M" from YouTube Music reads as "34,5 M" in Spanish.
function count(value){
  const m=String(value||'').replace(/,/g,'').match(/([\d.]+)\s*([KMB])?/i);if(!m)return value||'';
  const n=Number(m[1])*({K:1e3,M:1e6,B:1e9}[(m[2]||'').toUpperCase()]||1);
  return new Intl.NumberFormat('es-ES',{notation:'compact',maximumFractionDigits:1}).format(n);
}
function artistHero(a,full=false){
  const stats=[a.monthly_listeners&&`<strong>${esc(count(a.monthly_listeners))}</strong> oyentes mensuales`,a.subscribers&&`${esc(count(a.subscribers))} suscriptores`].filter(Boolean).join(' · ');
  return `<section class="artist-hero${full?' full':''}" aria-label="Artista ${esc(a.name)}">${a.banner?`<img class="artist-banner" src="${esc(a.banner)}" alt="" decoding="async">`:''}<div class="artist-hero-body">${a.avatar?`<img class="artist-avatar" src="${esc(a.avatar)}" alt="" width="148" height="148">`:'<div class="artist-avatar" aria-hidden="true">♫</div>'}<div class="artist-hero-text"><p class="eyebrow">Artista</p><h2>${esc(a.name)}</h2>${stats?`<p class="artist-stats">${stats}</p>`:''}<div class="artist-actions"><button class="primary" data-artist-play>▶ Reproducir</button><button data-artist-shuffle aria-label="Reproducir en aleatorio">⤮ Aleatorio</button><button data-artist-radio>◉ Radio</button>${full?'':`<a class="text-link" href="/artist/${esc(a.id)}" data-artist-open>Ver perfil</a>`}</div></div></div></section>`;
}
function artistSections(a,full=false){
  const related=a.related?.length?`<h3 class="section-title">Fans también escuchan</h3><div class="related-row">${a.related.map(r=>`<a class="related-artist" href="/artist/${esc(r.id)}" data-artist-open>${r.thumbnail?`<img src="${esc(r.thumbnail)}" alt="" loading="lazy" width="96" height="96">`:'<span aria-hidden="true">♫</span>'}<strong>${esc(r.name)}</strong>${r.subscribers?`<small>${esc(count(r.subscribers))} suscriptores</small>`:''}</a>`).join('')}</div>`:'';
  const about=full&&a.description?`<h3 class="section-title">Acerca de</h3><details class="artist-about"><summary>${esc(a.description.slice(0,180))}${a.description.length>180?'…':''}</summary><p>${esc(a.description)}</p></details>`:'';
  return `${a.songs?.length?`<h3 class="section-title">Populares</h3><div class="top-songs">${videoCards(a.songs)}</div>`:''}${related}${about}`;
}
function bindArtist(root,a){
  const play=(list,shuffle=false)=>{if(!list.length)return;audioQueue=shuffle?[...list].sort(()=>Math.random()-.5):[...list];audioIndex=0;failedReferences.clear();radioFor=null;startOnline()};
  root.querySelectorAll('[data-artist-play]').forEach(b=>b.onclick=()=>play(a.songs||[]));
  root.querySelectorAll('[data-artist-shuffle]').forEach(b=>b.onclick=()=>play(a.songs||[],true));
  // Artist radio: the most popular song seeds an endless mix of similar music.
  root.querySelectorAll('[data-artist-radio]').forEach(b=>b.onclick=()=>{if(a.songs?.[0])startRadio(a.songs[0])});
  root.querySelectorAll('[data-artist-open]').forEach(openLink);
  const top=root.querySelector('.top-songs');if(top)bindVideoCards(top,a.songs);
}
function drawSearch(){
  const el=$('#search-results');if(!el||location.pathname!=='/')return;
  if(searchView.loading&&!searchView.items.length){el.innerHTML=(searchView.artist===undefined?'<div class="artist-hero skeleton" aria-hidden="true"></div>':'')+'<div class="empty" role="status">Buscando música online…</div>';return}
  if(searchView.error){el.innerHTML=`<div class="empty error-state"><h2>No se pudo completar la búsqueda</h2><p>${esc(searchView.error.message)}</p><div class="tools"><button id="retry-search">Volver a intentar</button><button id="try-audius">Buscar en Audius</button></div></div>`;$('#retry-search').onclick=()=>runSearch(searchView.query,false,searchView.source);$('#try-audius').onclick=()=>{$('#search-source').value='audius';runSearch(searchView.query,false,'audius')};return}
  if(!searchView.query){el.innerHTML='<div class="discovery-empty">'+LOGO+'<h2>Una búsqueda. Otra forma de sentir.</h2><p class="muted">Escribe ese nombre que no te quitas de la cabeza.</p></div>';return}
  const artists=searchView.artists||[],kind=searchView.filter||'songs',labels={ytmusic:'YouTube Music',youtube:'YouTube Data API',piped:'Piped',audius:'Audius'};
  const artistCards=artists.length?artists.map(a=>`<article class="video-row artist-row"><div class="video-art round">${a.thumbnail?`<img src="${esc(a.thumbnail)}" alt="" loading="lazy">`:'♫'}</div><div class="video-info"><h3>${esc(a.name)}</h3><p>${esc(a.source)}</p></div>${/^UC[\w-]{22}$/.test(a.id)?`<a class="button-link" href="/artist/${esc(a.id)}" data-artist-open data-artist="${esc(a.name)}">Ver artista</a>`:`<button data-artist="${esc(a.name)}">Ver canciones</button>`}</article>`).join(''):'<div class="empty"><h2>No se encontraron artistas</h2><p>Prueba otro nombre o busca canciones.</p></div>';
  // The profile of the artist the query names comes first, then the songs.
  const songs=kind==='songs',profile=songs&&searchView.artist?artistHero(searchView.artist)+artistSections(searchView.artist):songs&&searchView.artist===undefined?'<div class="artist-hero skeleton" aria-hidden="true"></div>':'';
  el.innerHTML=`${profile}<div class="results-heading"><h2>${songs&&searchView.artist?'Canciones':`Resultados para «${esc(searchView.query)}»`}</h2><small>${kind==='artists'?artists.length+' artistas':searchView.items.length+' canciones'} · ${esc(labels[searchView.resolved]||searchView.resolved)}</small></div>${searchView.notice?`<p class="source-notice" role="status">${esc(searchView.notice)}</p>`:''}<div id="online-results">${kind==='artists'?artistCards:videoCards(searchView.items)}</div>${searchView.next?'<button id="more-results" class="load-more">Mostrar más resultados</button>':''}`;
  el.querySelectorAll('button[data-artist]').forEach(b=>b.onclick=()=>{$('#online-query').value=b.dataset.artist;$('#search-filter').value='songs';$('#search-source').value='ytmusic';runSearch(b.dataset.artist,false,'ytmusic','songs')});
  if(songs&&searchView.artist)bindArtist(el,searchView.artist);else el.querySelectorAll('[data-artist-open]').forEach(openLink);
  bindVideoCards($('#online-results'),searchView.items);if($('#more-results'))$('#more-results').onclick=()=>runSearch(searchView.query,true,searchView.resolved);
}
async function runSearch(value,more=false,source='auto',kind=$('#search-filter')?.value||searchView.filter||'songs'){
  const query=value.trim().replace(/\s+/g,' ');if(query.length<2){notify('Escribe al menos dos caracteres.');return}
  searchController?.abort();const version=++searchVersion;searchController=new AbortController();const signal=searchController.signal;
  const previous=more?[...searchView.items]:[],previousArtists=more?[...(searchView.artists||[])]:[],page=more?searchView.next:null,notice=more?searchView.notice:'';
  // undefined = profile pending, null = the query does not name an artist.
  const artist=more?searchView.artist:kind==='songs'&&['auto','ytmusic'].includes(source)?undefined:null;
  searchView={query,source,filter:kind,items:previous,artists:previousArtists,artist,next:null,resolved:source,error:null,loading:true,notice};drawSearch();
  // The profile is looked up in parallel and slots in above the songs when it arrives.
  if(artist===undefined)api('/api/online/artist?q='+encodeURIComponent(query),'GET',undefined,{signal}).then(r=>r.artist,e=>e.name==='AbortError'?undefined:null).then(found=>{if(version!==searchVersion||found===undefined)return;searchView.artist=found;if(found)prefetchAudio(found.songs.slice(0,2));if(!searchView.loading||searchView.error)drawSearch()});
  try{
    const result=await api('/api/online/search?q='+encodeURIComponent(query)+'&source='+source+'&filter='+kind+(page?'&page='+encodeURIComponent(page):''),'GET',undefined,{signal});
    if(version!==searchVersion||location.pathname!=='/')return;
    searchView={query,source,filter:kind,resolved:result.source,items:[...new Map([...previous,...result.items].map(v=>[v.id,v])).values()],artists:[...new Map([...previousArtists,...(result.artists||[])].map(v=>[v.id,v])).values()],artist:searchView.artist,next:result.next_page,error:null,loading:false,notice:result.notice||notice};
    drawSearch();
    // On phones the results start below the fold: bring the artist profile / first songs into view.
    const results=$('#search-results');if(!more&&results&&results.getBoundingClientRect().top>innerHeight*.6)results.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
    await api('/api/online/recent','POST',{query});if(version!==searchVersion||location.pathname!=='/')return;
    onlineData.recent=[query,...onlineData.recent.filter(q=>q.toLowerCase()!==query.toLowerCase())].slice(0,8);drawRecent();
  }catch(e){if(e.name==='AbortError'||version!==searchVersion)return;searchView.loading=false;searchView.error=e;drawSearch()}
}
async function artistPage(){
  const id=location.pathname.split('/')[2]||'';
  shell('<div class="artist-hero skeleton full" aria-hidden="true"></div><div class="empty" role="status">Cargando artista…</div>');
  const el=$('.content');
  if(!/^UC[\w-]{22}$/.test(id)){el.innerHTML='<div class="empty"><h2>Artista no encontrado</h2><a href="/" class="text-link" data-artist-open>Descubrir música</a></div>';el.querySelectorAll('[data-artist-open]').forEach(openLink);return}
  try{
    const [{artist}]=await Promise.all([api('/api/online/artist/'+id),onlineState()]);
    if(location.pathname!=='/artist/'+id)return;
    artistView=artist;el.innerHTML=artistHero(artist,true)+artistSections(artist,true);bindArtist(el,artist);document.title=artist.name+' · IVOX';
  }catch(e){if(location.pathname==='/artist/'+id)el.innerHTML=`<div class="empty error-state"><h2>No se pudo cargar el artista</h2><p>${esc(e.message)}</p></div>`}
}
function videoCards(items,playlist=null){
  if(!items.length)return '<div class="empty"><h2>No hay canciones aquí todavía</h2><p>Prueba otra búsqueda o fuente y guarda lo que te guste.</p><a href="/" class="text-link" data-discover>Descubrir música</a></div>';
  return items.map(v=>{const s=savedVideo(v.id);return `<article class="video-row" data-video="${esc(v.id)}"><div class="video-art">${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" loading="lazy" width="120" height="90">`:'<span aria-hidden="true">♫</span>'}<span class="duration">${duration(v.duration)}</span></div><div class="video-info"><h3>${esc(v.title)}</h3><p>${esc(v.channel)} <span class="source-badge">${esc(v.source||v.provider)}</span></p></div><div class="video-actions"><button class="primary" data-online-play="${v.id}" aria-label="Reproducir ${esc(v.title)}">▶ Play</button><button data-online-fav="${v.id}" aria-label="${s.favorite?'Quitar de':'Añadir a'} favoritos de IVOX" aria-pressed="${!!s.favorite}">${s.favorite?'♥':'♡'}</button><button data-online-menu="${v.id}" aria-label="Opciones de ${esc(v.title)}">•••</button>${playlist?`<button data-online-remove="${v.id}" aria-label="Quitar de playlist">×</button>`:''}</div></article>`}).join('');
}
function bindVideoCards(root,items,playlist=null){
  if(!root)return;
  root.querySelectorAll('[data-discover]').forEach(a=>a.onclick=e=>{e.preventDefault();history.pushState({},'','/');render().catch(e=>notify(e.message))});
  root.querySelectorAll('[data-online-play]').forEach(b=>b.onclick=()=>{const id=b.dataset.onlinePlay;if(audioQueue[audioIndex]?.id===id&&!$('#online-player').hidden){toggleOnline();return}audioQueue=[...items];audioIndex=items.findIndex(v=>v.id===id);failedReferences.clear();radioFor=null;startOnline()});
  // pointerdown fires ~100-300 ms before click: one more head start for the resolver.
  root.querySelectorAll('.video-row[data-video]').forEach(row=>{const warm=()=>prefetchAudio([videoById(row.dataset.video)]);row.addEventListener('pointerenter',warm,{once:true});row.addEventListener('pointerdown',warm,{once:true,passive:true})});
  if(root.querySelector('.video-row'))prefetchAudio(items.slice(0,5));syncCards();
  root.querySelectorAll('[data-online-fav]').forEach(b=>b.onclick=()=>perform(()=>setFavorite(videoById(b.dataset.onlineFav),!savedVideo(b.dataset.onlineFav).favorite,b)));
  root.querySelectorAll('[data-online-menu]').forEach(b=>b.onclick=()=>onlineOptions(videoById(b.dataset.onlineMenu)));
  root.querySelectorAll('[data-online-remove]').forEach(b=>b.onclick=()=>perform(async()=>{await api('/api/online/playlists/'+playlist+'/items','DELETE',{video_id:b.dataset.onlineRemove});await onlineLibrary()}));
}
// Favorites update in place everywhere (rows, mini player, Now Playing) without re-rendering the page.
async function setFavorite(v,value,origin){
  if(!v||v.provider==='local')return;
  await api('/api/online/items/'+v.id,'PATCH',{favorite:value});
  const save=onlineData.saves.find(s=>s.video_id===v.id);if(save)save.favorite=value?1:0;else onlineData.saves.push({video_id:v.id,favorite:value?1:0,later:0,last_played:null});
  if(!onlineData.items[v.id])onlineData.items[v.id]=v;
  document.querySelectorAll(`[data-online-fav="${CSS.escape(v.id)}"]`).forEach(b=>{b.textContent=value?'♥':'♡';b.setAttribute('aria-pressed',String(value));b.setAttribute('aria-label',(value?'Quitar de':'Añadir a')+' favoritos de IVOX')});
  nowPlaying.liked();if(value)nowPlaying.burst(origin);
  notify(value?'Guardado en favoritos de IVOX.':'Quitado de favoritos de IVOX.');
  if(location.pathname==='/library'&&onlineSection==='favorites')drawOnlineLibrary();
}
function externalLink(v){if(v.provider==='piped'&&onlineStatus?.allow_youtube_external)return `<a class="text-link" href="https://www.youtube.com/watch?v=${esc(v.provider_id)}" target="_blank" rel="noopener">Abrir en YouTube (fuera de IVOX)</a>`;if(v.provider==='audius')return `<a class="text-link" href="https://audius.co/tracks/${esc(v.provider_id)}" target="_blank" rel="noopener">Abrir en Audius</a>`;return ''}
function onlineOptions(v){
  if(!v)return;const s=savedVideo(v.id);openDialog(`<h2>${esc(v.title)}</h2><p class="muted">${esc(v.channel)} · ${esc(v.source)}</p><div class="option-list"><button id="play-next">Reproducir a continuación</button><button id="enqueue">Añadir a la cola</button>${v.provider==='piped'?'<button id="start-radio">◉ Iniciar radio de esta canción</button>':''}<button id="save-later">${s.later?'Quitar de':'Guardar en'} escuchar después</button><button id="to-playlist">Guardar en playlist</button><button id="download-online">Descargar</button>${externalLink(v)}</div>`);
  $('#download-online').onclick=()=>{dialog.close();perform(()=>downloadOnline(v))};
  const queueAt=position=>{if(audioIndex<0){audioQueue=[v];audioIndex=0;startOnline();return}audioQueue.splice(position,0,{...v,radio:false});drawQueue();prefetchNext()};
  $('#play-next').onclick=()=>{queueAt(audioIndex+1);dialog.close();notify('Sonará a continuación.')};
  // Songs you add go before the automatic radio songs.
  $('#enqueue').onclick=()=>{const radio=audioQueue.findIndex((x,i)=>i>audioIndex&&x.radio);queueAt(radio<0?audioQueue.length:radio);dialog.close();notify('Añadido a la cola.')};
  if($('#start-radio'))$('#start-radio').onclick=()=>{dialog.close();startRadio(v)};
  $('#save-later').onclick=()=>perform(async()=>{await api('/api/online/items/'+v.id,'PATCH',{later:!s.later});onlineData=await api('/api/online/library');dialog.close();if(location.pathname==='/library')drawOnlineLibrary();notify(s.later?'Quitado de escuchar después.':'Guardado para escuchar después.')});$('#to-playlist').onclick=()=>pickOnlinePlaylist(v);
}
function pickOnlinePlaylist(v){
  openDialog(`<h2>Guardar en playlist</h2><p>${esc(v.title)}</p><form id="online-pick">${onlineData.playlists.length?`<label>Playlist<select name="playlist">${onlineData.playlists.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label><button class="primary">Guardar referencia</button>`:'<p class="muted">Crea tu primera playlist para guardar esta canción.</p>'}</form><button id="create-for-video">Nueva playlist</button>`);
  if(onlineData.playlists.length)$('#online-pick').onsubmit=e=>{e.preventDefault();perform(async()=>{await api('/api/online/playlists/'+new FormData(e.target).get('playlist')+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');dialog.close();notify('Guardado en tu playlist.');if(location.pathname==='/library')drawOnlineLibrary()})};
  $('#create-for-video').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});await api('/api/online/playlists/'+p.id+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');notify('Playlist creada y referencia guardada.');if(location.pathname==='/library')drawOnlineLibrary()});
}
async function onlineLibrary(){
  await onlineState();if(location.pathname!=='/library'||!me)return;const section=new URLSearchParams(location.search).get('section');if(section)onlineSection=section;
  shell(`<div class="topline"><div><p class="muted">Tu espacio en IVOX</p><h1>Mi biblioteca</h1><p>Guarda lo que te mueve. Escúchalo desde cualquier dispositivo.</p></div><button id="new-online-playlist" class="primary">Nueva playlist</button></div><div class="library-tabs" role="group" aria-label="Colecciones"><button data-section="favorites">♡ Favoritos</button><button data-section="later">◷ Escuchar después</button><button data-section="history">Historial</button><button data-section="playlists">Playlists</button></div><div id="online-library-content"></div><p class="provider-note">Referencias privadas de IVOX. No descargamos archivos a tu biblioteca.</p>`);
  $('#new-online-playlist').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});onlineSection=p.id;history.replaceState({},'','/library');await onlineLibrary()});document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.section;history.replaceState({},'','/library');drawOnlineLibrary()});drawOnlineLibrary();
}
function drawOnlineLibrary(){
  const el=$('#online-library-content');if(!el)return;document.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===onlineSection)));
  if(onlineSection==='playlists'){el.innerHTML=`<h2>Tus playlists</h2><div class="playlist-grid">${onlineData.playlists.map(p=>`<button class="playlist-card" data-playlist="${p.id}"><span aria-hidden="true">≋</span><strong>${esc(p.name)}</strong><small>${p.tracks.length} canciones online</small></button>`).join('')||'<p class="muted">Crea una playlist para reunir tus canciones.</p>'}</div>`;el.querySelectorAll('[data-playlist]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.playlist;drawOnlineLibrary()});return}
  const playlist=onlineData.playlists.find(p=>p.id===onlineSection),ids=playlist?playlist.tracks:onlineData.saves.filter(s=>onlineSection==='history'?s.last_played:onlineSection==='later'?s.later:s.favorite).map(s=>s.video_id),items=ids.map(id=>onlineData.items[id]).filter(Boolean);
  const title=playlist?playlist.name:onlineSection==='history'?'Historial de reproducción':onlineSection==='later'?'Escuchar después':'Favoritos de IVOX';
  el.innerHTML=`<div class="topline"><h2>${esc(title)}</h2>${playlist?'<div class="tools"><button id="rename-online">Renombrar</button><button id="delete-online" class="danger">Eliminar playlist</button></div>':onlineSection==='history'?'<button id="clear-history">Borrar historial</button>':''}</div><div id="library-videos">${videoCards(items,playlist?.id)}</div>`;bindVideoCards(el,items,playlist?.id);
  if(playlist){$('#rename-online').onclick=()=>textForm('Renombrar playlist','Nombre',async name=>{await api('/api/playlists/'+playlist.id,'PATCH',{name});await onlineLibrary()},playlist.name);$('#delete-online').onclick=()=>confirmAction('¿Eliminar esta playlist?','Se eliminan sus referencias online y asociaciones locales. Los archivos de biblioteca y el contenido del proveedor no se borran.',async()=>{await api('/api/playlists/'+playlist.id,'DELETE');onlineSection='playlists';await onlineLibrary()})}
  if($('#clear-history'))$('#clear-history').onclick=()=>confirmAction('¿Borrar tu historial?','Solo se elimina el historial de IVOX.',async()=>{await api('/api/online/history','DELETE');await onlineLibrary()});
}

// ---- Playback core ----------------------------------------------------------
// Every start aborts the previous playback's requests and timers; late answers
// and media events from an older version are ignored.
function beginPlayback(){
  playController?.abort();playController=new AbortController();window.IVoxPlayerControls.cancelSeek();
  clearTimeout(advanceTimer);clearTimeout(skipTimer);skipTimer=null;clearTimeout(watchdog);
  resumeAt=0;fixingDuration=false;return {version:++playVersion,signal:playController.signal};
}
function showTrack(item){
  $('#online-player').hidden=false;document.body.classList.add('has-online-player');
  $('#online-playing').textContent=item.title;$('#online-source').textContent=[item.channel,item.source||item.provider].filter(Boolean).join(' · ');$('#external-playback').innerHTML='';
  nowPlayingArt(item);progress(0,item.duration||0);nowPlaying.track(item);drawQueue();
}
// A song that never reaches 'playing' is treated as failed instead of spinning forever.
function armWatchdog(version,ms=15000){clearTimeout(watchdog);watchdog=setTimeout(()=>{if(version!==playVersion||$('#online-player').dataset.state!=='loading')return;resolutionBusy=false;retryAudioLevel(version)},ms)}
async function startOnline(autostart=true,minimumLevel=1){
  const item=audioQueue[audioIndex];if(!item)return;const {version,signal}=beginPlayback();
  resolutionBusy=true;activeLevel=0;recordedReference=null;
  if(onlineAudio.getAttribute('src'))onlineAudio.pause();
  showTrack(item);$('#playback-status').textContent='Preparando audio…';playerState('loading');armWatchdog(version);
  try{
    let result;
    if(item.provider==='local')result={url:'/api/tracks/'+item.provider_id+'/audio',level:0,source:'local'};
    // Level 0: a copy on this device (the element was already unlocked by an earlier gesture).
    if(!result&&minimumLevel<=1&&cachedIds.has(item.id)){const local=await localAudio(item);if(version!==playVersion||!me)return;if(local)result={url:local,level:0,source:'device'}}
    // Level 1 plays in the same tick as the click (no await before play), so the gesture is kept;
    // the server starts relaying bytes as soon as yt-dlp returns the URL. Failures arrive via onerror.
    if(!result&&item.provider==='piped'&&minimumLevel<=1)result={url:'/api/audio/'+item.provider_id,level:1,source:'yt-dlp'};
    else if(!result){
      onlineAudio.removeAttribute('src');onlineAudio.load();
      const format=onlineAudio.canPlayType('audio/mp4')?'mp4':'webm';
      result=await api('/api/online/play/'+encodeURIComponent(item.id).replace('%3A',':')+'?format='+format+(minimumLevel>=3?'&minimum_level=3':''),'GET',undefined,{signal});
      if(version!==playVersion||!me)return;
    }
    activeLevel=result.level??4;activeEngine={device:'este dispositivo',local:'tus archivos','yt-dlp':'yt-dlp',piped:'Piped'}[result.source]||'Audius';
    if(blobUrl&&result.url!==blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null}
    onlineAudio.src=result.url;resolutionBusy=false;
    if(!autostart){clearTimeout(watchdog);playerState('paused');$('#playback-status').textContent='Preparado. Pulsa ▶ para escuchar.';return}
    try{await onlineAudio.play()}catch(e){if(version!==playVersion||e.name==='AbortError')return;if(e.name==='NotAllowedError'){clearTimeout(watchdog);playerState('paused');$('#playback-status').textContent='Pulsa ▶ para empezar.'}else retryAudioLevel(version)}
  }catch(e){if(version!==playVersion||e.name==='AbortError'||!me)return;resolutionBusy=false;retryAudioLevel(version)}
}
// Fallback chain for one song: device copy → yt-dlp → Piped (only when an instance runs) → same song on Audius → next song.
async function retryAudioLevel(version=playVersion){
  if(version!==playVersion||resolutionBusy)return;const item=audioQueue[audioIndex];if(!item)return;
  if(activeLevel===0&&item.provider!=='local'&&cachedIds.has(item.id)){forgetAudio(item);return startOnline(true,1)}
  if(item.provider==='piped'&&activeLevel<3&&onlineStatus?.piped)return startOnline(true,activeLevel<=1?2:3);
  resolutionBusy=true;
  if(await tryAudiusFallback(item,version))return;
  if(version===playVersion){resolutionBusy=false;playbackFailure('No se pudo reproducir «'+item.title+'».')}
}
async function tryAudiusFallback(item,version,autostart=true){
  if(item.provider!=='piped')return false;
  try{
    const q=(item.channel+' '+item.title).slice(0,120),response=await api('/api/online/search?source=audius&q='+encodeURIComponent(q),'GET',undefined,{signal:playController?.signal});
    if(version!==playVersion||!me)return true;
    const canonical=v=>String(v).normalize('NFKD').toLowerCase().replace(/\p{M}/gu,'').replace(/[^\p{L}\p{N}]/gu,'');
    // A different remix or artist must remain an explicit user choice.
    const match=response.items.find(v=>canonical(v.title)===canonical(item.title)&&canonical(v.channel)===canonical(item.channel));
    if(!match)return false;
    audioQueue[audioIndex]=match;notify('Reproducción de respaldo en Audius: '+match.title);await startOnline(autostart,4);return true;
  }catch{return false}
}
// Never hang on a broken song: say so, then move on. Three failures in a row stop the loop.
async function playbackFailure(message){
  const current=audioQueue[audioIndex];if(!current)return;const version=playVersion;
  failedReferences.add(current.id);clearTimeout(watchdog);failStreak++;
  onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();playerState('error');
  $('#external-playback').innerHTML=externalLink(current)+(current.provider==='piped'?'<button id="playback-audius">Buscar alternativas en Audius</button>':'');
  if($('#playback-audius'))$('#playback-audius').onclick=()=>perform(async()=>{const q=(current.channel+' '+current.title).slice(0,120);history.pushState({},'','/');await discover();$('#online-query').value=q;$('#search-source').value='audius';await runSearch(q,false,'audius')});
  if(failStreak>=3){$('#playback-status').textContent=message+' Se detuvo tras varios fallos seguidos. Prueba otra canción.';notify(message+' La reproducción se ha detenido tras varios fallos.');return}
  if(audioIndex>=audioQueue.length-1)await ensureRadio(true);
  if(version!==playVersion)return;
  if(audioIndex>=audioQueue.length-1){$('#playback-status').textContent=message+' Busca otra canción o cambia de fuente.';notify(message);return}
  $('#playback-status').textContent=message+' Pasamos a la siguiente…';notify(message+' Pasamos a la siguiente.');
  advanceTimer=setTimeout(()=>{if(!me||version!==playVersion)return;audioIndex++;startOnline()},1200);
}
// Rapid taps on next/previous only move the selection; the song loads once the taps stop,
// and the previous request is cancelled at once so the server never piles them up.
function skipTo(index){
  if(index<0||index>=audioQueue.length)return;
  audioIndex=index;failStreak=0;
  if(!audioUnlocked){startOnline();return}
  beginPlayback();onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();
  showTrack(audioQueue[index]);playerState('loading');$('#playback-status').textContent='Preparando audio…';
  skipTimer=setTimeout(()=>{skipTimer=null;startOnline()},200);
}
const skipBy=delta=>skipTo(audioIndex+delta);
function nextTrack(){if(audioIndex<audioQueue.length-1)return skipBy(1);ensureRadio(true).then(added=>{if(added)skipBy(1);else notify('No hay más canciones en la cola.')})}
// Autoplay radio: when the queue is about to run out, 10 similar songs are appended.
async function ensureRadio(force=false){
  if(!autoplay||!audioQueue.length)return false;
  if(!force&&audioIndex<audioQueue.length-2)return false;
  const seed=[...audioQueue].reverse().find(v=>v.provider==='piped'&&!failedReferences.has(v.id));if(!seed)return false;
  if(radioFor===seed.id)return radioPromise;
  radioFor=seed.id;const queue=audioQueue;
  radioPromise=api('/api/online/radio/'+seed.id).then(r=>{
    if(queue!==audioQueue)return false;
    const have=new Set(audioQueue.map(v=>v.id)),fresh=r.items.filter(v=>!have.has(v.id)&&!failedReferences.has(v.id)).slice(0,10).map(v=>({...v,radio:true}));
    if(!fresh.length)return false;
    audioQueue.push(...fresh);drawQueue();prefetchNext();nowPlaying.radioAdded(fresh.length);return true;
  }).catch(()=>{radioFor=null;return false});
  return radioPromise;
}
function startRadio(v){audioQueue=[v];audioIndex=0;failedReferences.clear();radioFor=null;startOnline();ensureRadio(true).then(added=>{if(added)notify('Radio de «'+v.title+'»: canciones similares en la cola.')})}
function setAutoplay(on){autoplay=on;try{localStorage.setItem('ivox-autoplay',on?'1':'0')}catch{}if(on)ensureRadio();nowPlaying.state($('#online-player').dataset.state)}
window.playLocal=(items,index)=>{audioQueue=items;audioIndex=index;failedReferences.clear();radioFor=null;startOnline()};

onlineAudio.onplaying=()=>{const current=audioQueue[audioIndex];if(!current||onlineAudio.getAttribute('src')==='/silence.wav')return;audioUnlocked=true;failStreak=0;clearTimeout(watchdog);playerState('playing');$('#playback-status').textContent=activeLevel===0?(current.provider==='local'?'Reproduciendo tus archivos':'Reproduciendo al instante desde este dispositivo'):'Reproduciendo · '+activeEngine+' · Nivel '+activeLevel;if(recordedReference!==current.id){recordedReference=current.id;prefetchNext();ensureRadio();if(current.provider!=='local')api('/api/online/items/'+current.id,'PATCH',{played:true}).catch(()=>{})}};
onlineAudio.onerror=()=>{const src=onlineAudio.getAttribute('src');if(!src||src==='/silence.wav')return;resolutionBusy=false;retryAudioLevel(playVersion)};
onlineAudio.onended=async()=>{
  if(fixingDuration||onlineAudio.getAttribute('src')==='/silence.wav')return;const version=playVersion;
  if(audioIndex<audioQueue.length-1){audioIndex++;startOnline();return}
  playerState('loading');const added=await ensureRadio(true);
  if(version!==playVersion)return;
  if(added&&audioIndex<audioQueue.length-1){audioIndex++;startOnline()}else playerState('paused');
};
onlineAudio.onpause=()=>{if(!fixingDuration&&!resolutionBusy&&onlineAudio.getAttribute('src')&&onlineAudio.getAttribute('src')!=='/silence.wav'&&!['loading','error'].includes($('#online-player').dataset.state))playerState('paused')};
// Mid-song stalls (network drop): after 20 s waiting, reopen the same source at the same second.
onlineAudio.onwaiting=()=>{if(onlineAudio.paused||fixingDuration)return;playerState('loading');const version=playVersion,at=onlineAudio.currentTime;clearTimeout(watchdog);watchdog=setTimeout(()=>{if(version!==playVersion||onlineAudio.readyState>=3||onlineAudio.paused)return;resumeAt=at;onlineAudio.src=onlineAudio.getAttribute('src');onlineAudio.play().catch(()=>{});armWatchdog(version)},20000)};
onlineAudio.oncanplay=()=>{if(!onlineAudio.paused&&$('#online-player').dataset.state==='loading'&&onlineAudio.readyState>=3)playerState('playing')};
// Safari often reports twice the real length for streamed AAC. Seeking far past the end
// makes it read the real duration; the catalogue length also corrects the display and seeks.
const unreliable=(d,known)=>!Number.isFinite(d)||d<=0||(known>0&&Math.abs(d-known)>Math.max(3,known*.1));
function trackDuration(){const d=onlineAudio.duration,known=audioQueue[audioIndex]?.duration;return known>0&&unreliable(d,known)?known:d}
window.IVoxPlayerControls.duration=trackDuration;
onlineAudio.onloadedmetadata=()=>{
  const version=playVersion;if(resumeAt){onlineAudio.currentTime=resumeAt;resumeAt=0}
  if(!isSafari||fixingDuration||!unreliable(onlineAudio.duration,audioQueue[audioIndex]?.duration))return;
  fixingDuration=true;const back=onlineAudio.currentTime;
  const done=()=>{onlineAudio.removeEventListener('seeked',done);if(version!==playVersion||!fixingDuration)return;onlineAudio.currentTime=back;setTimeout(()=>{if(version===playVersion)fixingDuration=false;progress(onlineAudio.currentTime,trackDuration())},250)};
  onlineAudio.addEventListener('seeked',done);onlineAudio.currentTime=99999;setTimeout(done,1500);
};
let positionStamp=0;
function progress(time,total){
  window.IVoxPlayerControls.update(time,total);
  const buffered=onlineAudio.buffered,loaded=total>0&&buffered.length?Math.min(1,buffered.end(buffered.length-1)/total):0;
  nowPlaying.progress(time,total,loaded);
  // Lock screen scrubber with the corrected duration, at most once a second.
  if('mediaSession' in navigator&&navigator.mediaSession.setPositionState&&Number.isFinite(total)&&total>0&&Date.now()-positionStamp>1000){positionStamp=Date.now();try{navigator.mediaSession.setPositionState({duration:total,position:Math.min(time,total),playbackRate:onlineAudio.playbackRate||1})}catch{}}
}
onlineAudio.ontimeupdate=onlineAudio.ondurationchange=onlineAudio.onprogress=()=>{
  if(onlineAudio.getAttribute('src')==='/silence.wav'||fixingDuration)return;progress(onlineAudio.currentTime,trackDuration());
  if(activeLevel>0&&onlineAudio.currentTime>8)keepAudio(audioQueue[audioIndex],onlineAudio.currentSrc);
};
['#online-player .player-heading','#online-player .np-art'].forEach(s=>$(s).onclick=()=>nowPlaying.open());
$('#np-expand').onclick=()=>nowPlaying.open();
function toggleOnline(){
  if(!audioQueue[audioIndex])return;
  if(!onlineAudio.getAttribute('src')||failedReferences.has(audioQueue[audioIndex].id)){failedReferences.delete(audioQueue[audioIndex].id);failStreak=0;startOnline();return}
  if(onlineAudio.paused)onlineAudio.play().catch(e=>{if(e.name!=='AbortError')retryAudioLevel()});else onlineAudio.pause();
}
$('#np-toggle').onclick=toggleOnline;
$('#np-download').onclick=()=>perform(()=>downloadOnline(audioQueue[audioIndex]));
$('#np-like').onclick=e=>{const v=audioQueue[audioIndex];if(v)perform(()=>setFavorite(v,!savedVideo(v.id).favorite,e.currentTarget))};
// Lock screen / headphones / Control Center controls on iPhone and Android.
if('mediaSession' in navigator){
  const actions={play:()=>onlineAudio.play().catch(()=>{}),pause:()=>onlineAudio.pause(),previoustrack:()=>$('#online-previous').click(),nexttrack:()=>nextTrack(),seekto:d=>{if(d.seekTime!=null)window.IVoxPlayerControls.seek(d.seekTime)}};
  for(const [name,handler] of Object.entries(actions)){try{navigator.mediaSession.setActionHandler(name,(d)=>{if(!$('#online-player').hidden)handler(d)})}catch{}}
}
document.addEventListener('keydown',e=>{
  if($('#online-player').hidden||e.target.closest('input,textarea,select,[contenteditable]')||e.metaKey||e.ctrlKey||e.altKey)return;
  if(e.code==='Space'&&!e.target.closest('button')){e.preventDefault();toggleOnline()}
  else if(e.shiftKey&&e.code==='ArrowRight'){e.preventDefault();nextTrack()}else if(e.shiftKey&&e.code==='ArrowLeft'){e.preventDefault();skipBy(-1)}
  else if(e.key==='l'||e.key==='L')$('#np-like').click();
});
window.stopOnline=function(){beginPlayback();onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();audioQueue=[];audioIndex=-1;recordedReference=null;radioFor=null;failStreak=0;if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null}$('#online-player').hidden=true;document.body.classList.remove('has-online-player');nowPlaying.close(true);playerState('idle');if('mediaSession' in navigator)navigator.mediaSession.metadata=null};
window.resetOnlineSession=function(){stopOnline();cancelOnlineSearch();searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};onlineData={saves:[],playlists:[],items:{},recent:[]}};
window.pauseOnline=()=>onlineAudio.pause();
// "Previous" restarts the song after the first few seconds, like every music app.
$('#close-online').onclick=()=>stopOnline();$('#online-previous').onclick=()=>{if(onlineAudio.currentTime>4&&!skipTimer){window.IVoxPlayerControls.seek(0);return}skipBy(-1)};$('#online-next').onclick=()=>nextTrack();
$('#queue-toggle').onclick=()=>nowPlaying.open('queue');

function privacy(){shell(`<h1>Privacidad y condiciones</h1><section class="panel"><h2>Tu música online, tus listas privadas</h2><p>IVOX guarda cuentas, contraseñas mediante hash, sesiones y referencias de favoritos, playlists, escuchar después, historial y búsquedas recientes en su servidor. Se comparten únicamente entre tus sesiones.</p><p>Las consultas se envían a YouTube Music, a la instancia privada de Piped o a Audius. El audio se transmite sin guardarlo en disco, mediante un enlace temporal protegido por tu sesión. Para que repetir una canción sea instantáneo, este dispositivo guarda una copia de las 25 últimas que escuchaste (hasta 25 MB cada una) en el almacenamiento del navegador; no se envía a ningún sitio y se borra al limpiar los datos del sitio. Los proveedores reciben las peticiones y la dirección IP del servidor; las imágenes externas pueden recibir la IP de tu navegador. Las letras proceden de los socios de YouTube Music indicados en cada una.</p><p>IVOX no necesita tus credenciales de YouTube o Audius. No modifica tus cuentas de esos servicios. Piped no es un servicio oficial de YouTube; puede dejar de funcionar o ser bloqueado.</p><p>Puedes borrar referencias, playlists, recientes e historial. Este botón elimina tus referencias online de IVOX; conserva tu cuenta, playlists vacías y archivos locales.</p><button id="clear-online-data" class="danger">Borrar mis datos de biblioteca online</button></section>`);$('#clear-online-data').onclick=()=>confirmAction('¿Borrar tus datos online de IVOX?','Se eliminarán favoritos, escuchar después, historial, búsquedas y referencias de todas tus playlists. No se puede deshacer.',async()=>{await api('/api/online/library','DELETE');stopOnline();notify('Datos online eliminados de IVOX.')})}
