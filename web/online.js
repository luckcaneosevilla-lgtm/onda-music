// The audio element is outside #app and survives all SPA navigation.
const onlineAudio=$('#online-audio');
let onlineData={saves:[],playlists:[],items:{},recent:[]},onlineStatus;
let searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};
let searchController,searchVersion=0,onlineSection='favorites';
let audioQueue=[],audioIndex=-1,playVersion=0,recordedReference=null,advanceTimer,failedReferences=new Set(),activeLevel=0,activeEngine='',resolutionBusy=false;
const duration=n=>n==null?'—':`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
const savedVideo=id=>onlineData.saves.find(s=>s.video_id===id)||{};
const videoById=id=>searchView.items.find(v=>v.id===id)||onlineData.items[id]||audioQueue.find(v=>v.id===id);
const isIOS=()=>/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
window.cancelOnlineSearch=()=>{searchController?.abort();searchVersion++;searchView.loading=false};

async function onlineState(){const [status,data]=await Promise.all([api('/api/online/status'),api('/api/online/library')]);onlineStatus=status;onlineData=data}
async function discover(){
  await onlineState();if(location.pathname!=='/'||!me)return;
  shell(`<div class="discover-heading"><p class="muted">Tu próxima canción está ahí fuera</p><h1>¿Qué quieres escuchar?</h1><p>Busca un artista, una canción o algo que cambie tu día.</p></div>
    <form id="online-search" class="online-search" role="search"><label class="sr-only" for="online-query">Buscar música online</label><input id="online-query" name="q" type="search" maxlength="120" minlength="2" placeholder="Artista o canción, por ejemplo Feid" value="${esc(searchView.query)}" required autocomplete="off"><button class="primary">Buscar</button></form>
    <div class="search-options"><label>Buscar<select id="search-filter"><option value="songs">Canciones</option><option value="artists">Artistas</option></select></label><label>Fuente<select id="search-source"><option value="auto">Catálogo musical · automático</option><option value="ytmusic">YouTube Music</option><option value="youtube">YouTube · respaldo</option><option value="piped">Piped</option><option value="audius">Audius · independiente</option></select></label><span class="muted">Audio online · Tus listas privadas en ONDA</span></div>
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
function drawSearch(){
  const el=$('#search-results');if(!el||location.pathname!=='/')return;
  if(searchView.loading){el.innerHTML='<div class="empty" role="status">Buscando música online…</div>';return}
  if(searchView.error){el.innerHTML=`<div class="empty error-state"><h2>No se pudo completar la búsqueda</h2><p>${esc(searchView.error.message)}</p><div class="tools"><button id="retry-search">Volver a intentar</button><button id="try-audius">Buscar en Audius</button></div></div>`;$('#retry-search').onclick=()=>runSearch(searchView.query,false,searchView.source);$('#try-audius').onclick=()=>{$('#search-source').value='audius';runSearch(searchView.query,false,'audius')};return}
  if(!searchView.query){el.innerHTML='<div class="discovery-empty"><span aria-hidden="true">≋</span><h2>Una búsqueda. Otra forma de sentir.</h2><p class="muted">Escribe ese nombre que no te quitas de la cabeza.</p></div>';return}
  const artists=searchView.artists||[],kind=searchView.filter||'songs',labels={ytmusic:'YouTube Music',youtube:'YouTube Data API',piped:'Piped',audius:'Audius'};
  const artistCards=artists.length?artists.map(a=>`<article class="video-row"><div class="video-art">${a.thumbnail?`<img src="${esc(a.thumbnail)}" alt="" loading="lazy">`:'♫'}</div><div class="video-info"><h3>${esc(a.name)}</h3><p>${esc(a.source)}</p></div><button data-artist="${esc(a.name)}">Ver canciones</button></article>`).join(''):'<div class="empty"><h2>No se encontraron artistas</h2><p>Prueba otro nombre o busca canciones.</p></div>';
  el.innerHTML=`<div class="results-heading"><h2>Resultados para «${esc(searchView.query)}»</h2><small>${kind==='artists'?artists.length+' artistas':searchView.items.length+' canciones'} · ${esc(labels[searchView.resolved]||searchView.resolved)}</small></div>${searchView.notice?`<p class="source-notice" role="status">${esc(searchView.notice)}</p>`:''}<div id="online-results">${kind==='artists'?artistCards:videoCards(searchView.items)}</div>${searchView.next?'<button id="more-results" class="load-more">Mostrar más resultados</button>':''}`;
  el.querySelectorAll('[data-artist]').forEach(b=>b.onclick=()=>{$('#online-query').value=b.dataset.artist;$('#search-filter').value='songs';$('#search-source').value='ytmusic';runSearch(b.dataset.artist,false,'ytmusic','songs')});
  bindVideoCards(el,searchView.items);if($('#more-results'))$('#more-results').onclick=()=>runSearch(searchView.query,true,searchView.resolved);
}
async function runSearch(value,more=false,source='auto',kind=$('#search-filter')?.value||searchView.filter||'songs'){
  const query=value.trim().replace(/\s+/g,' ');if(query.length<2){notify('Escribe al menos dos caracteres.');return}
  searchController?.abort();const version=++searchVersion;searchController=new AbortController();
  const previous=more?[...searchView.items]:[],previousArtists=more?[...(searchView.artists||[])]:[],page=more?searchView.next:null,notice=more?searchView.notice:'';
  searchView={query,source,filter:kind,items:previous,artists:previousArtists,next:null,resolved:source,error:null,loading:true,notice};drawSearch();
  try{
    const result=await api('/api/online/search?q='+encodeURIComponent(query)+'&source='+source+'&filter='+kind+(page?'&page='+encodeURIComponent(page):''),'GET',undefined,{signal:searchController.signal});
    if(version!==searchVersion||location.pathname!=='/')return;
    searchView={query,source,filter:kind,resolved:result.source,items:[...new Map([...previous,...result.items].map(v=>[v.id,v])).values()],artists:[...new Map([...previousArtists,...(result.artists||[])].map(v=>[v.id,v])).values()],next:result.next_page,error:null,loading:false,notice:result.notice||notice};
    await api('/api/online/recent','POST',{query});if(version!==searchVersion||location.pathname!=='/')return;
    onlineData.recent=[query,...onlineData.recent.filter(q=>q.toLowerCase()!==query.toLowerCase())].slice(0,8);drawRecent();drawSearch();
  }catch(e){if(e.name==='AbortError'||version!==searchVersion)return;searchView.loading=false;searchView.error=e;drawSearch()}
}
function videoCards(items,playlist=null){
  if(!items.length)return '<div class="empty"><h2>No hay canciones aquí todavía</h2><p>Prueba otra búsqueda o fuente y guarda lo que te guste.</p><a href="/" class="text-link" data-discover>Descubrir música</a></div>';
  return items.map(v=>{const s=savedVideo(v.id);return `<article class="video-row" data-video="${esc(v.id)}"><div class="video-art">${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" loading="lazy" width="120" height="90">`:'<span aria-hidden="true">♫</span>'}<span class="duration">${duration(v.duration)}</span></div><div class="video-info"><h3>${esc(v.title)}</h3><p>${esc(v.channel)} <span class="source-badge">${esc(v.source||v.provider)}</span></p></div><div class="video-actions"><button class="primary" data-online-play="${v.id}" aria-label="Reproducir ${esc(v.title)}">▶ Play</button><button data-online-fav="${v.id}" aria-label="${s.favorite?'Quitar de':'Añadir a'} favoritos de ONDA" aria-pressed="${!!s.favorite}">${s.favorite?'♥':'♡'}</button><button data-online-menu="${v.id}" aria-label="Opciones de ${esc(v.title)}">•••</button>${playlist?`<button data-online-remove="${v.id}" aria-label="Quitar de playlist">×</button>`:''}</div></article>`}).join('');
}
function bindVideoCards(root,items,playlist=null){
  root.querySelectorAll('[data-discover]').forEach(a=>a.onclick=e=>{e.preventDefault();history.pushState({},'','/');render().catch(e=>notify(e.message))});
  root.querySelectorAll('[data-online-play]').forEach(b=>b.onclick=()=>{audioQueue=[...items];audioIndex=items.findIndex(v=>v.id===b.dataset.onlinePlay);failedReferences.clear();startOnline()});
  root.querySelectorAll('[data-online-fav]').forEach(b=>b.onclick=()=>perform(async()=>{const id=b.dataset.onlineFav,next=!savedVideo(id).favorite;await api('/api/online/items/'+id,'PATCH',{favorite:next});onlineData=await api('/api/online/library');if(location.pathname==='/')drawSearch();else drawOnlineLibrary();notify(next?'Guardado en favoritos de ONDA.':'Quitado de favoritos de ONDA.')}));
  root.querySelectorAll('[data-online-menu]').forEach(b=>b.onclick=()=>onlineOptions(videoById(b.dataset.onlineMenu)));
  root.querySelectorAll('[data-online-remove]').forEach(b=>b.onclick=()=>perform(async()=>{await api('/api/online/playlists/'+playlist+'/items','DELETE',{video_id:b.dataset.onlineRemove});await onlineLibrary()}));
}
function externalLink(v){if(v.provider==='piped'&&onlineStatus?.allow_youtube_external)return `<a class="text-link" href="https://www.youtube.com/watch?v=${esc(v.provider_id)}" target="_blank" rel="noopener">Abrir en YouTube (fuera de ONDA)</a>`;if(v.provider==='audius')return `<a class="text-link" href="https://audius.co/tracks/${esc(v.provider_id)}" target="_blank" rel="noopener">Abrir en Audius</a>`;return ''}
function onlineOptions(v){
  if(!v)return;const s=savedVideo(v.id);openDialog(`<h2>${esc(v.title)}</h2><p class="muted">${esc(v.channel)} · ${esc(v.source)}</p><div class="option-list"><button id="enqueue">Añadir a la cola</button><button id="save-later">${s.later?'Quitar de':'Guardar en'} escuchar después</button><button id="to-playlist">Guardar en playlist</button>${externalLink(v)}</div>`);
  $('#enqueue').onclick=()=>{audioQueue.push(v);drawQueue();dialog.close();notify('Añadido a la cola.')};
  $('#save-later').onclick=()=>perform(async()=>{await api('/api/online/items/'+v.id,'PATCH',{later:!s.later});onlineData=await api('/api/online/library');dialog.close();if(location.pathname==='/library')drawOnlineLibrary();notify(s.later?'Quitado de escuchar después.':'Guardado para escuchar después.')});$('#to-playlist').onclick=()=>pickOnlinePlaylist(v);
}
function pickOnlinePlaylist(v){
  openDialog(`<h2>Guardar en playlist</h2><p>${esc(v.title)}</p><form id="online-pick">${onlineData.playlists.length?`<label>Playlist<select name="playlist">${onlineData.playlists.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label><button class="primary">Guardar referencia</button>`:'<p class="muted">Crea tu primera playlist para guardar esta canción.</p>'}</form><button id="create-for-video">Nueva playlist</button>`);
  if(onlineData.playlists.length)$('#online-pick').onsubmit=e=>{e.preventDefault();perform(async()=>{await api('/api/online/playlists/'+new FormData(e.target).get('playlist')+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');dialog.close();notify('Guardado en tu playlist.');if(location.pathname==='/library')drawOnlineLibrary()})};
  $('#create-for-video').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});await api('/api/online/playlists/'+p.id+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');notify('Playlist creada y referencia guardada.');if(location.pathname==='/library')drawOnlineLibrary()});
}
async function onlineLibrary(){
  await onlineState();if(location.pathname!=='/library'||!me)return;const section=new URLSearchParams(location.search).get('section');if(section)onlineSection=section;
  shell(`<div class="topline"><div><p class="muted">Tu espacio en ONDA</p><h1>Mi biblioteca</h1><p>Guarda lo que te mueve. Escúchalo desde cualquier dispositivo.</p></div><button id="new-online-playlist" class="primary">Nueva playlist</button></div><div class="library-tabs" role="group" aria-label="Colecciones"><button data-section="favorites">♡ Favoritos</button><button data-section="later">◷ Escuchar después</button><button data-section="history">Historial</button><button data-section="playlists">Playlists</button></div><div id="online-library-content"></div><p class="provider-note">Referencias privadas de ONDA. No descargamos archivos a tu biblioteca.</p>`);
  $('#new-online-playlist').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});onlineSection=p.id;history.replaceState({},'','/library');await onlineLibrary()});document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.section;history.replaceState({},'','/library');drawOnlineLibrary()});drawOnlineLibrary();
}
function drawOnlineLibrary(){
  const el=$('#online-library-content');if(!el)return;document.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===onlineSection)));
  if(onlineSection==='playlists'){el.innerHTML=`<h2>Tus playlists</h2><div class="playlist-grid">${onlineData.playlists.map(p=>`<button class="playlist-card" data-playlist="${p.id}"><span aria-hidden="true">≋</span><strong>${esc(p.name)}</strong><small>${p.tracks.length} canciones online</small></button>`).join('')||'<p class="muted">Crea una playlist para reunir tus canciones.</p>'}</div>`;el.querySelectorAll('[data-playlist]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.playlist;drawOnlineLibrary()});return}
  const playlist=onlineData.playlists.find(p=>p.id===onlineSection),ids=playlist?playlist.tracks:onlineData.saves.filter(s=>onlineSection==='history'?s.last_played:onlineSection==='later'?s.later:s.favorite).map(s=>s.video_id),items=ids.map(id=>onlineData.items[id]).filter(Boolean);
  const title=playlist?playlist.name:onlineSection==='history'?'Historial de reproducción':onlineSection==='later'?'Escuchar después':'Favoritos de ONDA';
  el.innerHTML=`<div class="topline"><h2>${esc(title)}</h2>${playlist?'<div class="tools"><button id="rename-online">Renombrar</button><button id="delete-online" class="danger">Eliminar playlist</button></div>':onlineSection==='history'?'<button id="clear-history">Borrar historial</button>':''}</div><div id="library-videos">${videoCards(items,playlist?.id)}</div>`;bindVideoCards(el,items,playlist?.id);
  if(playlist){$('#rename-online').onclick=()=>textForm('Renombrar playlist','Nombre',async name=>{await api('/api/playlists/'+playlist.id,'PATCH',{name});await onlineLibrary()},playlist.name);$('#delete-online').onclick=()=>confirmAction('¿Eliminar esta playlist?','Se eliminan sus referencias online y asociaciones locales. Los archivos de biblioteca y el contenido del proveedor no se borran.',async()=>{await api('/api/playlists/'+playlist.id,'DELETE');onlineSection='playlists';await onlineLibrary()})}
  if($('#clear-history'))$('#clear-history').onclick=()=>confirmAction('¿Borrar tu historial?','Solo se elimina el historial de ONDA.',async()=>{await api('/api/online/history','DELETE');await onlineLibrary()});
}

async function startOnline(autostart=true,minimumLevel=1){
  clearTimeout(advanceTimer);const item=audioQueue[audioIndex];if(!item)return;const version=++playVersion;
  resolutionBusy=true;activeLevel=0;onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();recordedReference=null;
  audio.pause();$('#player').hidden=true;$('#online-player').hidden=false;document.body.classList.add('has-online-player');
  $('#online-playing').textContent=item.title;$('#online-source').textContent=item.source||item.provider;$('#playback-status').textContent='Preparando audio…';$('#external-playback').innerHTML='';drawQueue();
  try{
    const format=onlineAudio.canPlayType('audio/mp4')?'mp4':'webm';
    let result;
    if(item.provider==='piped'&&minimumLevel<=1){
      try{result=await api('/api/audio/'+item.provider_id+'?resolve=1')}catch(e){if(!me||version!==playVersion)return;}
    }
    if(!result)result=await api('/api/online/play/'+encodeURIComponent(item.id).replace('%3A',':')+'?format='+format+(minimumLevel>=3?'&minimum_level=3':''));
    if(version!==playVersion||!me)return;
    activeLevel=result.level||4;activeEngine=result.source==='yt-dlp'?'yt-dlp':result.source==='piped'?'Piped':'Audius';
    $('#online-source').textContent=activeEngine+' · Nivel '+activeLevel+(result.muxed?' · MP4 muxado':'');
    onlineAudio.src=result.url;onlineAudio.load();resolutionBusy=false;
    if(!autostart||isIOS()){$('#playback-status').textContent='Preparado. Pulsa ▶ en los controles de audio para escuchar.';return}
    try{await onlineAudio.play()}catch(e){if(version!==playVersion)return;if(e.name==='NotAllowedError')$('#playback-status').textContent='Pulsa ▶ en los controles de audio para empezar.';else retryAudioLevel()}
  }catch(e){if(version===playVersion&&me){if(await tryAudiusFallback(item,version,autostart))return;if(version===playVersion){resolutionBusy=false;playbackFailure('No disponible, elige otro.')}}}
}
async function retryAudioLevel(){if(resolutionBusy)return;const item=audioQueue[audioIndex];if(!item)return;if(item.provider==='piped'&&activeLevel<3){const next=activeLevel===1?2:3;resolutionBusy=true;startOnline(true,next);return}const version=playVersion;resolutionBusy=true;if(await tryAudiusFallback(item,version))return;if(version===playVersion){resolutionBusy=false;playbackFailure('No disponible, elige otro.')}}
async function tryAudiusFallback(item,version,autostart=true){
  if(item.provider!=='piped')return false;
  resolutionBusy=true;
  try{
    const q=(item.channel+' '+item.title).slice(0,120),response=await api('/api/online/search?source=audius&q='+encodeURIComponent(q));
    if(version!==playVersion||!me)return true;
    const canonical=v=>String(v).normalize('NFKD').toLowerCase().replace(/\p{M}/gu,'').replace(/[^\p{L}\p{N}]/gu,'');
    // A different remix or artist must remain an explicit user choice.
    const match=response.items.find(v=>canonical(v.title)===canonical(item.title)&&canonical(v.channel)===canonical(item.channel));
    if(!match)return false;
    audioQueue[audioIndex]=match;notify('Reproducción de respaldo en Audius: '+match.title);await startOnline(autostart,4);return true;
  }catch{return false}
}
function playbackFailure(message){
  const current=audioQueue[audioIndex];if(!current)return;failedReferences.add(current.id);onlineAudio.pause();
  const hasNext=audioIndex<audioQueue.length-1;$('#playback-status').textContent=message+(hasNext?' Se preparará la siguiente canción.':' Busca otra canción o cambia de fuente.');$('#external-playback').innerHTML=externalLink(current)+(current.provider==='piped'?'<button id="playback-audius">Buscar alternativas en Audius</button>':'');notify(message);
  const fallback=async()=>{const q=(searchView.query||current.channel+' '+current.title).slice(0,120);stopOnline();history.pushState({},'','/');await discover();$('#online-query').value=q;$('#search-source').value='audius';await runSearch(q,false,'audius');searchView.notice='No se ha podido obtener audio de Piped. Se muestran alternativas de Audius; pueden ser versiones distintas o contenido independiente.';drawSearch()};
  if($('#playback-audius'))$('#playback-audius').onclick=()=>perform(fallback);
  if(current.provider==='piped'&&searchView.source==='auto'&&[...failedReferences].filter(id=>id.startsWith('piped:')).length>=3){perform(fallback);return}
  if(hasNext){clearTimeout(advanceTimer);advanceTimer=setTimeout(()=>{if(!me)return;audioIndex++;startOnline(!isIOS())},1800)}
}
onlineAudio.onplaying=()=>{const current=audioQueue[audioIndex];if(!current)return;$('#playback-status').textContent='Reproduciendo · '+activeEngine+' · Nivel '+activeLevel;if(recordedReference!==current.id){recordedReference=current.id;perform(async()=>{await api('/api/online/items/'+current.id,'PATCH',{played:true});onlineData=await api('/api/online/library')})}};
onlineAudio.onerror=()=>{if(onlineAudio.getAttribute('src'))retryAudioLevel()};
onlineAudio.onended=()=>{if(audioIndex<audioQueue.length-1){audioIndex++;startOnline(!isIOS())}};
function drawQueue(){const el=$('#online-queue');el.innerHTML=audioQueue.map((v,i)=>`<li class="${i===audioIndex?'current':''}"><button data-queue-play="${i}" ${i===audioIndex?'aria-current="true"':''}>${esc(v.title)} · ${esc(v.source||v.provider)}</button><button data-queue-remove="${i}" aria-label="Quitar ${esc(v.title)} de la cola">×</button></li>`).join('');el.querySelectorAll('[data-queue-play]').forEach(b=>b.onclick=()=>{audioIndex=Number(b.dataset.queuePlay);startOnline()});el.querySelectorAll('[data-queue-remove]').forEach(b=>b.onclick=()=>{const n=Number(b.dataset.queueRemove);audioQueue.splice(n,1);if(n<audioIndex)audioIndex--;else if(n===audioIndex){if(!audioQueue.length)return stopOnline();audioIndex=Math.min(n,audioQueue.length-1);startOnline(false)}drawQueue()});$('#online-previous').disabled=audioIndex<=0;$('#online-next').disabled=audioIndex>=audioQueue.length-1}
window.stopOnline=function(){playVersion++;clearTimeout(advanceTimer);onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();audioQueue=[];audioIndex=-1;recordedReference=null;$('#online-player').hidden=true;document.body.classList.remove('has-online-player')};
window.resetOnlineSession=function(){stopOnline();cancelOnlineSearch();searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};onlineData={saves:[],playlists:[],items:{},recent:[]}};
window.pauseOnline=()=>onlineAudio.pause();
$('#close-online').onclick=()=>stopOnline();$('#online-previous').onclick=()=>{if(audioIndex>0){audioIndex--;startOnline()}};$('#online-next').onclick=()=>{if(audioIndex<audioQueue.length-1){audioIndex++;startOnline()}};
$('#queue-toggle').onclick=()=>{const el=$('#online-queue');el.hidden=!el.hidden;$('#queue-toggle').setAttribute('aria-expanded',String(!el.hidden));$('#queue-toggle').textContent=el.hidden?'Ver cola':'Ocultar cola'};

function privacy(){shell(`<h1>Privacidad y condiciones</h1><section class="panel"><h2>Tu música online, tus listas privadas</h2><p>ONDA guarda cuentas, contraseñas mediante hash, sesiones y referencias de favoritos, playlists, escuchar después, historial y búsquedas recientes en su servidor. Se comparten únicamente entre tus sesiones.</p><p>Las consultas se envían a la instancia privada de Piped o a Audius. El audio se transmite sin guardarlo en disco, mediante un enlace temporal protegido por tu sesión. Los proveedores reciben las peticiones y la dirección IP del servidor; las imágenes externas pueden recibir la IP de tu navegador.</p><p>ONDA no necesita tus credenciales de YouTube o Audius. No modifica tus cuentas de esos servicios. Piped no es un servicio oficial de YouTube; puede dejar de funcionar o ser bloqueado.</p><p>Puedes borrar referencias, playlists, recientes e historial. Este botón elimina tus referencias online de ONDA; conserva tu cuenta, playlists vacías y archivos locales.</p><button id="clear-online-data" class="danger">Borrar mis datos de biblioteca online</button></section>`);$('#clear-online-data').onclick=()=>confirmAction('¿Borrar tus datos online de ONDA?','Se eliminarán favoritos, escuchar después, historial, búsquedas y referencias de todas tus playlists. No se puede deshacer.',async()=>{await api('/api/online/library','DELETE');stopOnline();notify('Datos online eliminados de ONDA.')})}
