// The audio element is outside #app and survives all SPA navigation. It is the
// only one: online songs, device copies and imported files all play through it.
// Seek bar, buffer and volume belong to player-controls.js (IVoxPlayerControls).
const onlineAudio=$('#online-audio');
function prepareAudioSession(){try{if(navigator.audioSession)navigator.audioSession.type='playback'}catch{}}
prepareAudioSession();
let onlineData={saves:[],playlists:[],items:{},recent:[]},onlineStatus;
let searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};
let searchController,searchVersion=0,onlineSection='favorites',artistView=null;
let audioQueue=[],audioIndex=-1,playVersion=0,recordedReference=null,advanceTimer,failedReferences=new Set(),activeLevel=0,activeEngine='',resolutionBusy=false;
// One controller per playback: changing song aborts every request of the previous one.
let playController=null,skipTimer=null,watchdog,failStreak=0,audioUnlocked=false,resumeAt=0,fixingDuration=false;
let playbackWanted=false,stallTimer=null,recoveryAttempts=0,lastPlaybackTime=0,recoveryInFlight=false;
let autoplay=(()=>{try{return localStorage.getItem('ivox-autoplay')!=='0'}catch{return true}})(),radioTried=new Set(),freshRetried=new Set(),radioPromise=null,radioQueue=null,radioController=null,transitionIntent=0,queueFinished=false,nearEnd=false,loadingSince=0;
const duration=n=>n==null?'—':`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
const savedVideo=id=>onlineData.saves.find(s=>s.video_id===id)||{};
const videoById=id=>searchView.items.find(v=>v.id===id)||searchView.artist?.songs?.find(v=>v.id===id)||artistView?.songs?.find(v=>v.id===id)||onlineData.items[id]||audioQueue.find(v=>v.id===id)||window.homeLookup?.(id)||window.offlineLookup?.(id);
const isSafari=/^((?!chrome|android|crios|fxios|edg).)*safari/i.test(navigator.userAgent);
// YouTube Music thumbnails come at 60-120 px; the same URL serves any size.
const bigArt=url=>String(url||'').replace(/=w\d+-h\d+/,'=w600-h600');
// iOS/Chrome only let an element start playback from a gesture; the first touch anywhere unlocks it for later async plays.
function unlockAudio(event){
  ['click','touchend','keydown'].forEach(t=>document.removeEventListener(t,unlockAudio,true));
  try{const Context=window.AudioContext||window.webkitAudioContext;if(Context){const context=new Context();context.resume().then(()=>context.close()).catch(()=>{})}}catch{}
  // A direct Play already supplies the browser gesture. Do not replace its
  // source with the unlock sound in the capture phase of that same click.
  const action=event?.target?.closest('[data-online-play],#np-toggle,#sheet-toggle,[data-queue-play]');
  const target=action?.matches('[data-online-play]')?videoById(action.dataset.onlinePlay):action?.matches('[data-queue-play]')?audioQueue[Number(action.dataset.queuePlay)]:audioQueue[audioIndex];
  if(action&&target&&(target.provider==='local'||(target.provider==='piped'&&!cachedIds.has(target.id))))return;
  if(onlineAudio.getAttribute('src'))return;
  onlineAudio.src='/silence.wav';onlineAudio.play().then(()=>{audioUnlocked=true;if(onlineAudio.getAttribute('src')==='/silence.wav'){onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load()}}).catch(()=>{});
}
['click','touchend','keydown'].forEach(t=>document.addEventListener(t,unlockAudio,true));
function prefetchNext(){prefetchAudio(audioQueue.slice(audioIndex+1,audioIndex+3))}
// Reuse legacy device copies, but never compete with playback by downloading a
// second full copy of every song. New prefetch is bounded on the server.
const AUDIO_CACHE='ivox-audio-v1';
const audioKey=id=>'/ivox-audio/'+encodeURIComponent(id);
let cachedIds=new Set(),blobUrl=null;
const audioOrder=()=>{try{return JSON.parse(localStorage.getItem('ivox-audio-order')||'[]')}catch{return []}};
const saveAudioOrder=order=>{try{localStorage.setItem('ivox-audio-order',JSON.stringify(order))}catch{}};
const cachedReady=('caches' in window?caches.open(AUDIO_CACHE).then(c=>c.keys()).then(keys=>{cachedIds=new Set(keys.map(r=>decodeURIComponent(new URL(r.url).pathname.slice(12))))}):Promise.resolve()).catch(()=>{});
async function localAudio(item){
  try{
    const response=await(await caches.open(AUDIO_CACHE)).match(audioKey(item.id));
    if(!response){cachedIds.delete(item.id);return null}
    saveAudioOrder([item.id,...audioOrder().filter(id=>id!==item.id)]);
    return URL.createObjectURL(await response.blob());
  }catch{return null}
}
function forgetAudio(item){cachedIds.delete(item.id);window.offlineForget?.(item.id);if('caches' in window)caches.open(AUDIO_CACHE).then(c=>c.delete(audioKey(item.id))).catch(()=>{})}
// Resolving a YouTube audio URL takes ~1-2 s; doing it before the tap (top results,
// hover, touch, the next songs in the queue) makes Play start almost instantly.
const prefetched=new Map(),prefetchedReset=v=>{if(v)prefetched.delete(v.provider_id)};
// On phones there is no hover and a tap gives ~0.1 s of warning, so songs are
// prepared while they sit on screen: a row visible for 0.6 s joins the next batch.
const visibleRows=new Map(),visiblePrefetch='IntersectionObserver' in window?new IntersectionObserver(entries=>{
  for(const e of entries){
    const id=e.target.dataset.video;
    if(e.isIntersecting&&!visibleRows.has(e.target))visibleRows.set(e.target,setTimeout(()=>{visibleRows.delete(e.target);visiblePrefetch.unobserve(e.target);if(e.target.isConnected)queueVisible(id)},600));
    else if(!e.isIntersecting&&visibleRows.has(e.target)){clearTimeout(visibleRows.get(e.target));visibleRows.delete(e.target)}
  }
},{threshold:.6}):{observe(){},unobserve(){}};
let visibleBatch=[],visibleTimer=0;
function queueVisible(id){const v=videoById(id);if(!v)return;visibleBatch.push(v);clearTimeout(visibleTimer);visibleTimer=setTimeout(()=>{const batch=visibleBatch;visibleBatch=[];for(let i=0;i<batch.length;i+=5)prefetchAudio(batch.slice(i,i+5))},150)}
function prefetchAudio(items){const now=Date.now();for(const [id,until] of prefetched)if(until<=now)prefetched.delete(id);const ids=items.filter(v=>v&&v.provider==='piped'&&!prefetched.has(v.provider_id)&&!cachedIds.has(v.id)).slice(0,5).map(v=>v.provider_id);if(!ids.length||!me)return;ids.forEach(id=>prefetched.set(id,now+30000));api('/api/audio/prefetch','POST',{video_ids:ids}).catch(()=>ids.forEach(id=>prefetched.delete(id)))}
function playerState(state){
  if(state==='loading'&&$('#online-player').dataset.state!=='loading')loadingSince=Date.now();
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
window.cancelOnlineSearch=()=>{closeSuggest();searchController?.abort();searchVersion++;searchView.loading=false};
const openLink=link=>link.onclick=e=>{e.preventDefault();history.pushState({},'',link.getAttribute('href'));render().catch(e=>notify(e.message))};

async function onlineState(){const [status,data]=await Promise.all([api('/api/online/status'),api('/api/online/library')]);onlineStatus=status;onlineData=data;onlineLoaded=true}
// Pages open instantly with the last known library; a background refresh redraws them.
let onlineLoaded=false,onlineRefresh=null;
function refreshOnline(redraw){onlineRefresh??=onlineState().finally(()=>{onlineRefresh=null});onlineRefresh.then(()=>{if(me)redraw()},()=>{})}
async function ensureOnline(redraw){if(onlineLoaded)refreshOnline(redraw);else await(onlineRefresh??=onlineState().finally(()=>{onlineRefresh=null}))}
window.preloadOnline=()=>{if(!onlineLoaded)refreshOnline(()=>{})};
async function discover(){
  await ensureOnline(()=>{if(location.pathname==='/')drawRecent()});if(location.pathname!=='/'||!me)return;
  shell(`<div class="discover-heading"><p class="muted">Tu próxima canción está ahí fuera</p><h1>¿Qué quieres escuchar?</h1><p>Busca un artista, una canción o algo que cambie tu día.</p></div>
    <form id="online-search" class="online-search" role="search"><label class="sr-only" for="online-query">Buscar música online</label><input id="online-query" name="q" type="search" maxlength="120" minlength="2" placeholder="Artista o canción, por ejemplo Feid" value="${esc(searchView.query)}" required autocomplete="off" role="combobox" aria-autocomplete="list" aria-controls="suggest" aria-expanded="false"><button class="primary">Buscar</button><div id="suggest" class="suggest" role="listbox" aria-label="Sugerencias" hidden></div></form>
    <div class="search-options"><label>Buscar<select id="search-filter"><option value="songs">Canciones</option><option value="artists">Artistas</option></select></label><label>Fuente<select id="search-source"><option value="auto">Catálogo musical · automático</option><option value="ytmusic">YouTube Music</option><option value="youtube">YouTube · respaldo</option><option value="piped">Piped</option><option value="audius">Audius · independiente</option></select></label><span class="muted">Audio online · Tus listas privadas en IVOX</span></div>
    <div class="recent-heading"><h2>Búsquedas recientes</h2>${onlineData.recent.length?'<button id="clear-recent">Borrar</button>':''}</div><div id="recent-searches" class="tools"></div>
    <div class="quick-access"><a href="/library" data-nav>♡ Tus favoritos</a><a href="/library?section=later" data-nav>◷ Escuchar después</a><a href="/library?section=playlists" data-nav>♫ Tus playlists</a></div>
    <section aria-label="Resultados de búsqueda"><div id="search-results" aria-live="polite"></div></section><p class="provider-note">La fuente aparece en cada resultado. La disponibilidad y las versiones publicadas dependen de cada proveedor.</p>`);
  $('#search-source').value=searchView.source;
  $('#search-filter').value=searchView.filter||'songs';
  $('#online-search').onsubmit=e=>{e.preventDefault();closeSuggest();runSearch($('#online-query').value,false,$('#search-source').value)};
  $('#online-query').oninput=onSuggestInput;$('#online-query').onkeydown=suggestKeys;
  $('#online-query').onfocus=()=>{
    // On phones the field sits mid-screen: lift it so the suggestions have room above the keyboard.
    const top=$('#online-search').getBoundingClientRect().top,header=Math.max(0,$('.sidebar')?.getBoundingClientRect().bottom||0);
    if(matchMedia('(max-width: 640px)').matches&&top>innerHeight*.3)scrollBy({top:top-header-12});
    if($('#online-query').value.trim()&&$('#suggest').hidden)onSuggestInput();
  };
  // Keep focus in the field while an option is pressed, so the click lands.
  $('#suggest').onpointerdown=e=>e.preventDefault();
  $('#search-source').onchange=()=>{cancelOnlineSearch();searchView.source=$('#search-source').value;if(['piped','audius'].includes(searchView.source))$('#search-filter').value='songs'};
  $('#search-filter').onchange=()=>{cancelOnlineSearch();if($('#search-filter').value==='artists'&&['piped','audius'].includes($('#search-source').value))$('#search-source').value='auto'};
  bindClearRecent();drawRecent();drawSearch();
}
// ---- Typeahead ---------------------------------------------------------------
// For people who only remember part of a name: the listener's own songs appear at once,
// then YouTube Music's artists, songs and completions (~0.1-0.3 s). A song plays on tap
// (the radio continues after it) and the top one is prefetched while the user decides.
let suggestTimer=0,prefetchTimer=0,suggestController=null,suggestSeq=0,suggestActive=-1,suggestOptions=[];
const fold=v=>String(v||'').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
function closeSuggest(){clearTimeout(suggestTimer);clearTimeout(prefetchTimer);suggestController?.abort();suggestSeq++;const box=$('#suggest');if(box){box.hidden=true;box.innerHTML=''}const input=$('#online-query');if(input){input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant')}suggestActive=-1;suggestOptions=[]}
function localMatches(q){
  const f=fold(q);if(f.length<2)return [];const seen=new Set();
  return Object.values(onlineData.items).filter(v=>v.available!==false&&!seen.has(v.id)&&(fold(v.title).includes(f)||fold(v.channel).includes(f))&&seen.add(v.id)).slice(0,3);
}
function onSuggestInput(){
  const q=$('#online-query').value.trim();clearTimeout(suggestTimer);clearTimeout(prefetchTimer);suggestController?.abort();
  if(!q){closeSuggest();if(searchView.query){cancelOnlineSearch();searchView={query:'',source:searchView.source,filter:searchView.filter,resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};drawSearch()}return}
  drawSuggest(null,q);suggestTimer=setTimeout(()=>fetchSuggest(q,false),120);
}
async function fetchSuggest(q,deep){
  const seq=++suggestSeq;suggestController=new AbortController();
  try{
    const r=await api('/api/online/suggest?q='+encodeURIComponent(q)+(deep?'&deep=1':''),'GET',undefined,{signal:suggestController.signal});
    if(seq!==suggestSeq||$('#online-query')?.value.trim()!==q)return;
    drawSuggest(r,q);
    if(r.songs.length)prefetchTimer=setTimeout(()=>prefetchAudio(r.songs.slice(0,2)),deep?0:300);
    if(!deep&&r.songs.length<2&&q.length>=3)suggestTimer=setTimeout(()=>fetchSuggest(q,true),350);
  }catch(e){if(e.name!=='AbortError'&&seq===suggestSeq)drawSuggest({queries:[],artists:[],songs:[]},q)}
}
function drawSuggest(r,q){
  const box=$('#suggest');if(!box)return;
  // Bold the typed part when it maps 1:1 onto the original text (accents folded).
  const mark=text=>{const t=String(text||''),f=fold(t),i=f.indexOf(fold(q));return i<0||f.length!==t.length?esc(t):esc(t.slice(0,i))+'<b>'+esc(t.slice(i,i+q.length))+'</b>'+esc(t.slice(i+q.length))};
  const local=localMatches(q),have=new Set(local.map(v=>v.id));
  const songs=(r?.songs||[]).filter(v=>!have.has(v.id)),artists=r?.artists||[],queries=(r?.queries||[]).filter(x=>fold(x)!==fold(q)).slice(0,5);
  suggestOptions=[{kind:'query',value:q},...local.map(v=>({kind:'song',value:v})),...artists.map(a=>({kind:'artist',value:a})),...songs.map(v=>({kind:'song',value:v})),...queries.map(x=>({kind:'query',value:x}))];
  const art=(src,round)=>`<span class="suggest-art${round?' round':''}">${src?`<img src="${esc(src)}" alt="" loading="lazy" width="44" height="44">`:'♫'}</span>`;
  const row=(i,inner)=>`<div class="suggest-option" role="option" id="suggest-${i}" data-suggest="${i}" aria-selected="${i===suggestActive}">${inner}</div>`;
  const song=v=>`${art(v.thumbnail)}<span class="suggest-text"><strong>${mark(v.title)}</strong><small>${mark(v.channel)}</small></span><span class="suggest-play" aria-hidden="true">▶</span>`;
  let i=0,html=row(i++,`<span class="suggest-art icon" aria-hidden="true">⌕</span><span class="suggest-text"><strong>Buscar «${esc(q)}»</strong></span>`);
  const section=(title,list,draw)=>{if(list.length){html+=`<p class="suggest-head">${title}</p>`;for(const item of list)html+=row(i++,draw(item))}};
  section('Tu música',local,song);
  section('Artistas',artists,a=>`${art(a.thumbnail,true)}<span class="suggest-text"><strong>${mark(a.name)}</strong><small>Artista</small></span>`);
  section('Canciones',songs,song);
  section('Búsquedas',queries,x=>`<span class="suggest-art icon" aria-hidden="true">⌕</span><span class="suggest-text"><span>${mark(x)}</span></span>`);
  if(!r)html+='<p class="suggest-head suggest-wait" role="status">Buscando sugerencias…</p>';
  box.innerHTML=html;box.hidden=false;$('#online-query').setAttribute('aria-expanded','true');
  // Fit above the on-screen keyboard and the phone tab bar.
  const view=window.visualViewport?.height||innerHeight,bar=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--tabbar'))||0;
  box.style.maxHeight=Math.max(200,Math.min(520,view-box.getBoundingClientRect().top-bar-12))+'px';
  box.querySelectorAll('[data-suggest]').forEach(el=>el.onclick=()=>pickSuggest(Number(el.dataset.suggest)));
}
function pickSuggest(i){
  const option=suggestOptions[i];if(!option)return;closeSuggest();
  if(option.kind==='query'){$('#online-query').value=option.value;runSearch(option.value,false,$('#search-source').value);return}
  if(option.kind==='artist'){history.pushState({},'','/artist/'+option.value.id);render().catch(e=>notify(e.message));return}
  // A suggested song plays at once; autoplay radio keeps similar music coming after it.
  const v=option.value;if(!onlineData.items[v.id])onlineData.items[v.id]=v;playList([v]);
}
function suggestKeys(e){
  const box=$('#suggest');
  if(e.key==='Escape'&&box&&!box.hidden){e.preventDefault();closeSuggest();return}
  if(!box||box.hidden||!suggestOptions.length)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    // -1 is the text field itself; the list wraps around through it.
    e.preventDefault();const n=suggestOptions.length+1;suggestActive=((suggestActive+1+(e.key==='ArrowDown'?1:-1))%n+n)%n-1;
    box.querySelectorAll('[data-suggest]').forEach(el=>el.setAttribute('aria-selected',String(Number(el.dataset.suggest)===suggestActive)));
    const input=$('#online-query');
    if(suggestActive>=0){input.setAttribute('aria-activedescendant','suggest-'+suggestActive);$('#suggest-'+suggestActive)?.scrollIntoView({block:'nearest'});const o=suggestOptions[suggestActive];if(o.kind==='song')prefetchAudio([o.value])}
    else input.removeAttribute('aria-activedescendant');
  }else if(e.key==='Enter'&&suggestActive>=0){e.preventDefault();pickSuggest(suggestActive)}
}
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#online-search'))closeSuggest()});
function bindClearRecent(){if($('#clear-recent'))$('#clear-recent').onclick=()=>perform(async()=>{await api('/api/online/recent','DELETE');onlineData.recent=[];drawRecent()})}
function drawRecent(){const el=$('#recent-searches');if(!el)return;const head=$('.recent-heading');if(head&&!$('#clear-recent')!==!onlineData.recent.length){head.innerHTML='<h2>Búsquedas recientes</h2>'+(onlineData.recent.length?'<button id="clear-recent">Borrar</button>':'');bindClearRecent()}el.innerHTML=onlineData.recent.length?onlineData.recent.map(q=>`<button data-recent="${esc(q)}">${esc(q)}</button>`).join(''):'<p class="muted">Tus búsquedas aparecerán aquí.</p>';el.querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#online-query').value=b.dataset.recent;runSearch(b.dataset.recent,false,$('#search-source').value)})}

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
  const play=(list,shuffle=false)=>playList(list,0,shuffle);
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
  if(!searchView.query){drawHome(el);return}
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
    const [{artist}]=await Promise.all([api('/api/online/artist/'+id),ensureOnline(()=>{})]);
    if(location.pathname!=='/artist/'+id)return;
    artistView=artist;el.innerHTML=artistHero(artist,true)+artistSections(artist,true);bindArtist(el,artist);document.title=artist.name+' · IVOX';
  }catch(e){if(location.pathname==='/artist/'+id)el.innerHTML=`<div class="empty error-state"><h2>No se pudo cargar el artista</h2><p>${esc(e.message)}</p></div>`}
}
function videoCards(items,playlist=null){
  if(!items.length)return '<div class="empty"><h2>No hay canciones aquí todavía</h2><p>Prueba otra búsqueda o fuente y guarda lo que te guste.</p><a href="/" class="text-link" data-discover>Descubrir música</a></div>';
  return items.map(v=>{const s=savedVideo(v.id);return `<article class="video-row" data-video="${esc(v.id)}"><div class="video-art">${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" loading="lazy" width="120" height="90">`:'<span aria-hidden="true">♫</span>'}<span class="duration">${duration(v.duration)}</span></div><div class="video-info"><h3>${esc(v.title)}</h3><p>${esc(v.channel)} <span class="source-badge">${esc(v.source||v.provider)}</span>${window.offlineHas?.(v.id)?'<span class="offline-badge" title="Disponible sin conexión" aria-label="Disponible sin conexión">↓</span>':''}</p></div><div class="video-actions"><button class="primary" data-online-play="${v.id}" aria-label="Reproducir ${esc(v.title)}">▶ Play</button><button data-online-fav="${v.id}" aria-label="${s.favorite?'Quitar de':'Añadir a'} favoritos de IVOX" aria-pressed="${!!s.favorite}">${s.favorite?'♥':'♡'}</button><button data-online-menu="${v.id}" aria-label="Opciones de ${esc(v.title)}">•••</button>${playlist?`<button data-online-remove="${v.id}" aria-label="${playlist==='downloads'?'Quitar de descargas':'Quitar de playlist'}">×</button>`:''}</div></article>`}).join('');
}
function bindVideoCards(root,items,playlist=null){
  if(!root)return;
  root.querySelectorAll('[data-discover]').forEach(a=>a.onclick=e=>{e.preventDefault();history.pushState({},'','/');render().catch(e=>notify(e.message))});
  root.querySelectorAll('[data-online-play]').forEach(b=>b.onclick=()=>{const id=b.dataset.onlinePlay;if(audioQueue[audioIndex]?.id===id&&!$('#online-player').hidden){toggleOnline();return}playList(items,items.findIndex(v=>v.id===id))});
  // pointerdown fires ~100-300 ms before click: one more head start for the resolver.
  root.querySelectorAll('.video-row[data-video]').forEach(row=>{const warm=()=>prefetchAudio([videoById(row.dataset.video)]);row.addEventListener('pointerenter',warm,{once:true});row.addEventListener('pointerdown',warm,{once:true,passive:true})});
  if(root.querySelector('.video-row'))prefetchAudio(items.slice(0,5));syncCards();
  root.querySelectorAll('.video-row[data-video]').forEach(row=>visiblePrefetch.observe(row));
  root.querySelectorAll('[data-online-fav]').forEach(b=>b.onclick=()=>perform(()=>setFavorite(videoById(b.dataset.onlineFav),!savedVideo(b.dataset.onlineFav).favorite,b)));
  root.querySelectorAll('[data-online-menu]').forEach(b=>b.onclick=()=>onlineOptions(videoById(b.dataset.onlineMenu)));
  root.querySelectorAll('[data-online-remove]').forEach(b=>b.onclick=()=>perform(async()=>{if(playlist==='downloads'){await offline.remove(b.dataset.onlineRemove);drawOnlineLibrary();return}await api('/api/online/playlists/'+playlist+'/items','DELETE',{video_id:b.dataset.onlineRemove});await onlineLibrary(true)}));
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
  if(!v)return;const s=savedVideo(v.id);openDialog(`<h2>${esc(v.title)}</h2><p class="muted">${esc(v.channel)} · ${esc(v.source)}</p><div class="option-list"><button id="play-next">Reproducir a continuación</button><button id="enqueue">Añadir a la cola</button>${v.provider==='piped'?'<button id="start-radio">◉ Iniciar radio de esta canción</button>':''}<button id="save-later">${s.later?'Quitar de':'Guardar en'} escuchar después</button><button id="to-playlist">Guardar en playlist</button>${v.provider!=='local'?`<button id="offline-toggle">${offline.has(v.id)?'✓ Disponible sin conexión · quitar':'↓ Guardar sin conexión'}${offline.premium()?'':' <span class="pill">Premium</span>'}</button>`:''}<button id="download-online">Descargar archivo</button>${externalLink(v)}</div>`);
  if($('#offline-toggle'))$('#offline-toggle').onclick=()=>{dialog.close();offline.has(v.id)?perform(()=>offline.remove(v.id)):offline.add([v])};
  $('#download-online').onclick=()=>{dialog.close();perform(()=>downloadOnline(v))};
  $('#play-next').onclick=()=>{dialog.close();notify(enqueue([v],true)?'Reproduciendo «'+v.title+'».':'Sonará a continuación.')};
  $('#enqueue').onclick=()=>{dialog.close();notify(enqueue([v])?'Reproduciendo «'+v.title+'».':'Añadido a la cola.')};
  if($('#start-radio'))$('#start-radio').onclick=()=>{dialog.close();startRadio(v)};
  $('#save-later').onclick=()=>perform(async()=>{await api('/api/online/items/'+v.id,'PATCH',{later:!s.later});onlineData=await api('/api/online/library');dialog.close();if(location.pathname==='/library')drawOnlineLibrary();notify(s.later?'Quitado de escuchar después.':'Guardado para escuchar después.')});$('#to-playlist').onclick=()=>pickOnlinePlaylist(v);
}
function pickOnlinePlaylist(v){
  openDialog(`<h2>Guardar en playlist</h2><p>${esc(v.title)}</p><form id="online-pick">${onlineData.playlists.length?`<label>Playlist<select name="playlist">${onlineData.playlists.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label><button class="primary">Guardar referencia</button>`:'<p class="muted">Crea tu primera playlist para guardar esta canción.</p>'}</form><button id="create-for-video">Nueva playlist</button>`);
  if(onlineData.playlists.length)$('#online-pick').onsubmit=e=>{e.preventDefault();perform(async()=>{await api('/api/online/playlists/'+new FormData(e.target).get('playlist')+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');dialog.close();notify('Guardado en tu playlist.');if(location.pathname==='/library')drawOnlineLibrary()})};
  $('#create-for-video').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});await api('/api/online/playlists/'+p.id+'/items','POST',{video_id:v.id});onlineData=await api('/api/online/library');notify('Playlist creada y referencia guardada.');if(location.pathname==='/library')drawOnlineLibrary()});
}
async function onlineLibrary(fresh=false){
  if(fresh)await onlineState();else await ensureOnline(()=>{if(location.pathname==='/library')drawOnlineLibrary()});if(location.pathname!=='/library'||!me)return;const section=new URLSearchParams(location.search).get('section');if(section)onlineSection=section;
  shell(`<div class="topline"><div><p class="muted">Tu espacio en IVOX</p><h1>Mi biblioteca</h1><p>Guarda lo que te mueve. Escúchalo desde cualquier dispositivo.</p></div><button id="new-online-playlist" class="primary">Nueva playlist</button></div><div class="library-tabs" role="group" aria-label="Colecciones"><button data-section="favorites">♡ Favoritos</button><button data-section="later">◷ Escuchar después</button><button data-section="history">Historial</button><button data-section="playlists">Playlists</button><button data-section="downloads">↓ Descargas</button></div><div id="online-library-content"></div><p class="provider-note">Referencias privadas de IVOX. Las descargas sin conexión se guardan solo en este dispositivo.</p>`);
  $('#new-online-playlist').onclick=()=>textForm('Nueva playlist','Nombre',async name=>{const p=await api('/api/playlists','POST',{name});onlineSection=p.id;history.replaceState({},'','/library');await onlineLibrary(true)});document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.section;history.replaceState({},'','/library');drawOnlineLibrary()});drawOnlineLibrary();
}
function drawOnlineLibrary(){
  const el=$('#online-library-content');if(!el)return;document.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===onlineSection)));
  if(onlineSection==='downloads'){drawDownloads(el);return}
  if(onlineSection==='playlists'){el.innerHTML=`<h2>Tus playlists</h2><div class="playlist-grid">${onlineData.playlists.map(p=>`<button class="playlist-card" data-playlist="${p.id}"><span aria-hidden="true">≋</span><strong>${esc(p.name)}</strong><small>${p.tracks.length} canciones online</small></button>`).join('')||'<p class="muted">Crea una playlist para reunir tus canciones.</p>'}</div>`;el.querySelectorAll('[data-playlist]').forEach(b=>b.onclick=()=>{onlineSection=b.dataset.playlist;drawOnlineLibrary()});return}
  const playlist=onlineData.playlists.find(p=>p.id===onlineSection),ids=playlist?playlist.tracks:onlineData.saves.filter(s=>onlineSection==='history'?s.last_played:onlineSection==='later'?s.later:s.favorite).map(s=>s.video_id),items=ids.map(id=>onlineData.items[id]).filter(Boolean),playable=items.filter(v=>v.available!==false);
  const title=playlist?playlist.name:onlineSection==='history'?'Historial de reproducción':onlineSection==='later'?'Escuchar después':'Favoritos de IVOX';
  el.innerHTML=`<div class="topline"><h2>${esc(title)}</h2>${playlist?'<div class="tools"><button id="rename-online">Renombrar</button><button id="delete-online" class="danger">Eliminar playlist</button></div>':onlineSection==='history'?'<button id="clear-history">Borrar historial</button>':''}</div>${playable.length?`<div class="artist-actions collection-actions"><button class="primary" data-collection="play">▶ Reproducir</button><button data-collection="shuffle" aria-label="Reproducir en aleatorio">⤮ Aleatorio</button><button data-collection="queue">+ Añadir a la cola</button><button data-collection="offline">↓ Sin conexión${offline.premium()?'':' <span class="pill">Premium</span>'}</button></div>`:''}<div id="library-videos">${videoCards(items,playlist?.id)}</div>`;bindVideoCards(el,items,playlist?.id);
  el.querySelectorAll('[data-collection]').forEach(b=>b.onclick=()=>{const kind=b.dataset.collection;if(kind==='offline'){offline.add(playable);return}if(kind==='queue'){notify(enqueue(playable)?'Reproduciendo «'+title+'».':playable.length+' canciones añadidas a la cola.');return}playList(playable,0,kind==='shuffle')});
  if(playlist){$('#rename-online').onclick=()=>textForm('Renombrar playlist','Nombre',async name=>{await api('/api/playlists/'+playlist.id,'PATCH',{name});await onlineLibrary(true)},playlist.name);$('#delete-online').onclick=()=>confirmAction('¿Eliminar esta playlist?','Se eliminan sus referencias online y asociaciones locales. Los archivos de biblioteca y el contenido del proveedor no se borran.',async()=>{await api('/api/playlists/'+playlist.id,'DELETE');onlineSection='playlists';await onlineLibrary(true)})}
  if($('#clear-history'))$('#clear-history').onclick=()=>confirmAction('¿Borrar tu historial?','Solo se elimina el historial de IVOX.',async()=>{await api('/api/online/history','DELETE');await onlineLibrary(true)});
}

// ---- Playback core ----------------------------------------------------------
// Every start aborts the previous playback's requests and timers; late answers
// and media events from an older version are ignored.
function beginPlayback(){
  onlineAudio.autoplay=false;
  transitionIntent++;
  if(radioQueue&&radioQueue!==audioQueue){radioController?.abort();radioPromise=null;radioQueue=null}
  playController?.abort();playController=new AbortController();window.IVoxPlayerControls.cancelSeek();
  clearTimeout(advanceTimer);clearTimeout(skipTimer);skipTimer=null;clearTimeout(watchdog);
  clearTimeout(stallTimer);stallTimer=null;playbackWanted=false;recoveryAttempts=0;lastPlaybackTime=0;recoveryInFlight=false;
  resumeAt=0;fixingDuration=false;queueFinished=false;nearEnd=false;return {version:++playVersion,signal:playController.signal};
}
function showTrack(item){
  $('#online-player').hidden=false;document.body.classList.add('has-online-player');
  $('#online-playing').textContent=item.title;$('#online-source').textContent=[item.channel,item.source||item.provider].filter(Boolean).join(' · ');$('#external-playback').innerHTML='';
  nowPlayingArt(item);progress(0,item.duration||0);nowPlaying.track(item);drawQueue();
}
// A song that never reaches 'playing' is treated as failed instead of spinning forever.
function armWatchdog(version,ms=30000){clearTimeout(watchdog);watchdog=setTimeout(()=>{if(version!==playVersion||!playbackWanted||$('#online-player').dataset.state!=='loading')return;resolutionBusy=false;retryAudioLevel(version)},ms)}
async function startOnline(autostart=true,minimumLevel=1,resumeFrom=0){
  prepareAudioSession();
  const item=audioQueue[audioIndex];if(!item)return;const {version,signal}=beginPlayback();resumeAt=resumeFrom;
  playbackWanted=autostart;
  resolutionBusy=true;activeLevel=0;recordedReference=null;
  if(onlineAudio.getAttribute('src'))onlineAudio.pause();
  showTrack(item);$('#playback-status').textContent='Preparando audio…';playerState('loading');armWatchdog(version);
  try{
    let result;
    if(item.provider==='local')result={url:'/api/tracks/'+item.provider_id+'/audio',level:0,source:'local'};
    // Level 0: a copy on this device (the element was already unlocked by an earlier gesture).
    if(!result&&minimumLevel<=1&&cachedIds.has(item.id)){const local=await localAudio(item);if(version!==playVersion||!me){if(local)URL.revokeObjectURL(local);return}if(local)result={url:local,level:0,source:'device'}}
    // Level 1 plays in the same tick as the click (no await before play), so the gesture is kept;
    // the server starts relaying bytes as soon as yt-dlp returns the URL. Failures arrive via onerror.
    if(!result&&item.provider==='piped'&&minimumLevel<=1)result={url:'/api/audio/'+item.provider_id+(freshRetried.has(item.id)?'?refresh=1':''),level:1,source:'yt-dlp'};
    else if(!result){
      const format=onlineAudio.canPlayType('audio/mp4')?'mp4':'webm';
      result=await api('/api/online/play/'+encodeURIComponent(item.id).replace('%3A',':')+'?format='+format+(minimumLevel>=3?'&minimum_level=3':''),'GET',undefined,{signal});
      if(version!==playVersion||!me)return;
    }
    activeLevel=result.level??4;activeEngine={device:'este dispositivo',local:'tus archivos','yt-dlp':'yt-dlp',piped:'Piped'}[result.source]||'Audius';
    if(blobUrl&&result.url!==blobUrl&&blobUrl!==window.crossfadeHold)URL.revokeObjectURL(blobUrl);
    blobUrl=result.source==='device'?result.url:null;
    onlineAudio.autoplay=autostart;onlineAudio.src=result.url;resolutionBusy=false;
    if(!autostart){clearTimeout(watchdog);playerState('paused');$('#playback-status').textContent='Preparado. Pulsa ▶ para escuchar.';return}
    try{await onlineAudio.play()}catch(e){if(version!==playVersion||e.name==='AbortError')return;if(e.name==='NotAllowedError'){playbackWanted=false;clearTimeout(watchdog);playerState('paused');$('#playback-status').textContent='Pulsa ▶ para empezar.'}else retryAudioLevel(version)}
  }catch(e){if(version!==playVersion||e.name==='AbortError'||!me)return;resolutionBusy=false;retryAudioLevel(version)}
}
// Fallback chain for one song: device copy → yt-dlp → Piped (only when an instance runs) → same song on Audius → next song.
async function retryAudioLevel(version=playVersion){
  if(version!==playVersion||resolutionBusy||!playbackWanted)return;const item=audioQueue[audioIndex];if(!item)return;
  if(activeLevel===0&&item.provider!=='local'&&cachedIds.has(item.id)){forgetAudio(item);return startOnline(true,1)}
  // YouTube can cut or refuse one stream: the server then resolves a fresh one (other
  // clients), so a song gets one more try from the same point before it counts as failed.
  if(item.provider==='piped'&&activeLevel===1&&!freshRetried.has(item.id)){freshRetried.add(item.id);$('#playback-status').textContent='Reconectando con YouTube…';return startOnline(true,1,lastPlaybackTime>.25?lastPlaybackTime:0)}
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
  failedReferences.add(current.id);clearTimeout(watchdog);clearTimeout(stallTimer);stallTimer=null;playbackWanted=false;failStreak++;
  onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();playerState('error');
  $('#external-playback').innerHTML=externalLink(current)+(current.provider==='piped'?'<button id="playback-audius">Buscar alternativas en Audius</button>':'');
  if($('#playback-audius'))$('#playback-audius').onclick=()=>perform(async()=>{const q=(current.channel+' '+current.title).slice(0,120);history.pushState({},'','/');await discover();$('#online-query').value=q;$('#search-source').value='audius';await runSearch(q,false,'audius')});
  if(failStreak>=3){queueFinished=true;$('#playback-status').textContent=message+' Se detuvo tras varios fallos seguidos. Prueba otra canción.';notify(message+' La reproducción se ha detenido tras varios fallos.');return}
  if(audioIndex>=audioQueue.length-1)await ensureRadio(true);
  if(version!==playVersion)return;
  if(audioIndex>=audioQueue.length-1){queueFinished=true;$('#playback-status').textContent=message+' Busca otra canción o cambia de fuente.';notify(message);return}
  $('#playback-status').textContent=message+' Pasamos a la siguiente…';notify(message+' Pasamos a la siguiente.');
  advanceTimer=setTimeout(()=>{if(!me||version!==playVersion)return;audioIndex++;startOnline()},1200);
}
// Rapid taps on next/previous only move the selection; the song loads once the taps stop,
// and the previous request is cancelled at once so the server never piles them up.
function skipTo(index){
  if(index<0||index>=audioQueue.length)return;
  audioIndex=index;failStreak=0;
  // Lock-screen controls must keep the native media callback; iOS can suspend
  // the debounce timer as soon as that callback returns.
  if(!audioUnlocked||document.hidden){startOnline();return}
  beginPlayback();onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();
  showTrack(audioQueue[index]);playerState('loading');$('#playback-status').textContent='Preparando audio…';
  skipTimer=setTimeout(()=>{skipTimer=null;startOnline()},200);
}
const skipBy=delta=>skipTo(audioIndex+delta);
function nextTrack(){
  if(audioIndex<audioQueue.length-1)return skipBy(1);
  const queue=audioQueue,index=audioIndex,version=playVersion,intent=transitionIntent;
  notify('Buscando canciones similares…');
  ensureRadio(true).then(()=>{
    if(queue!==audioQueue||index!==audioIndex||version!==playVersion||intent!==transitionIntent)return;
    if(audioIndex<audioQueue.length-1)skipBy(1);else notify('No hay más canciones en la cola.');
  });
}
// Autoplay radio: when the queue is about to run out, similar songs are appended.
// Seeds go from the most recent songs back; one that adds nothing new is not asked again
// (a failed request may be retried). Without a radio (Audius, own files, catalogue down)
// songs by the same artist follow, then the listener's own favourites and history.
const radioArtist=v=>v.provider==='local'?(v.title.includes(' - ')?v.title.split(' - ')[0].trim():''):mainArtist(v).replace(/ - Topic$/,'');
async function ensureRadio(force=false){
  if(!autoplay||!audioQueue.length)return false;
  if(!force&&audioIndex<audioQueue.length-2)return false;
  if(radioPromise&&radioQueue===audioQueue)return radioPromise;
  radioController?.abort();
  const queue=audioQueue;
  const controller=new AbortController(),signal=controller.signal;
  radioController=controller;radioQueue=queue;
  // A slow recommendation service must not block the end of a song indefinitely.
  const timeout=setTimeout(()=>controller.abort(),8000);
  const append=items=>{if(queue!==audioQueue||!autoplay)return false;const have=new Set(audioQueue.map(v=>v.id)),fresh=(items||[]).filter(v=>v&&v.available!==false&&!have.has(v.id)&&!failedReferences.has(v.id)&&have.add(v.id)).slice(0,10).map(v=>({...v,radio:true}));if(!fresh.length)return false;audioQueue.push(...fresh);drawQueue();nowPlaying.radioAdded(fresh.length);if(!resumePendingEnd())prefetchNext();return true};
  const task=(async()=>{
    const seeds=[...new Map([...audioQueue.slice(0,audioIndex+1).reverse(),...audioQueue.slice(audioIndex+1).reverse()].filter(v=>v.provider==='piped'&&!failedReferences.has(v.id)&&!radioTried.has(v.id)).map(v=>[v.id,v])).values()];
    // Prefer the last song in the queue, then what is playing now.
    const ordered=[audioQueue.at(-1),audioQueue[audioIndex],...seeds].filter((v,i,a)=>v&&seeds.includes(v)&&a.indexOf(v)===i).slice(0,3);
    for(const seed of ordered){
      if(signal.aborted)break;
      radioTried.add(seed.id);
      try{const r=await api('/api/online/radio/'+seed.id,'GET',undefined,{signal});if(queue!==audioQueue)return false;if(append(r.items))return true}catch{if(queue!==audioQueue)return false;radioTried.delete(seed.id)}
    }
    const artists=[...new Set([audioQueue[audioIndex],audioQueue.at(-1)].filter(Boolean).map(radioArtist))].filter(n=>n.length>=2&&!radioTried.has('artist:'+n.toLowerCase()));
    for(const name of artists){
      if(signal.aborted)break;
      radioTried.add('artist:'+name.toLowerCase());
      try{const r=await api('/api/online/search?q='+encodeURIComponent(name.slice(0,120)),'GET',undefined,{signal});if(queue!==audioQueue)return false;if(append(r.items))return true}catch{if(queue!==audioQueue)return false;radioTried.delete('artist:'+name.toLowerCase())}
    }
    if(queue!==audioQueue)return false;
    return append(onlineData.saves.map(x=>onlineData.items[x.video_id]).filter(v=>v&&v.provider!=='local').sort(()=>Math.random()-.5));
  })().finally(()=>{clearTimeout(timeout);if(radioPromise===task){radioPromise=null;radioQueue=null;radioController=null}});
  radioPromise=task;return task;
}
function playList(list,index=0,shuffle=false){
  if(!list.length)return;const items=shuffle?[...list].sort(()=>Math.random()-.5):[...list];
  audioQueue=items;audioIndex=shuffle?0:Math.max(0,index);failedReferences.clear();radioTried.clear();freshRetried.clear();failStreak=0;startOnline();
}
// Adds songs after what the user queued, before automatic radio picks. If the music had
// already finished (or failed), the first added song starts right away.
function enqueue(items,next=false){
  items=items.filter(v=>v&&v.available!==false).map(v=>({...v,radio:false}));if(!items.length)return false;
  if(audioIndex<0||!audioQueue.length){playList(items);return true}
  const radio=audioQueue.findIndex((x,i)=>i>audioIndex&&x.radio),at=next?audioIndex+1:radio<0?audioQueue.length:radio;
  audioQueue.splice(at,0,...items);
  if(queueFinished||$('#online-player').dataset.state==='error'||$('#online-player').hidden){audioIndex=at;failStreak=0;startOnline();return true}
  drawQueue();if(resumePendingEnd())return true;prefetchNext();return false;
}
function startRadio(v){audioQueue=[v];audioIndex=0;failedReferences.clear();radioTried.clear();freshRetried.clear();failStreak=0;startOnline();ensureRadio(true).then(added=>{if(added&&audioQueue[0]?.id===v.id)notify('Radio de «'+v.title+'»: canciones similares en la cola.')})}
function setAutoplay(on){autoplay=on;try{localStorage.setItem('ivox-autoplay',on?'1':'0')}catch{}if(on)ensureRadio();else radioController?.abort();drawQueue();nowPlaying.state($('#online-player').dataset.state)}
window.playLocal=(items,index)=>playList(items,index);

function clearStall(){clearTimeout(stallTimer);stallTimer=null;}
function pausePlayback(){transitionIntent++;onlineAudio.autoplay=false;playbackWanted=false;recoveryInFlight=false;clearStall();clearTimeout(watchdog);onlineAudio.pause();if(audioQueue[audioIndex])playerState('paused');}
function recoverCurrent(){
  const src=onlineAudio.getAttribute('src');
  if(recoveryInFlight)return playbackWanted;
  if(!playbackWanted||!src||src==='/silence.wav'||recoveryAttempts>=2||!audioQueue[audioIndex])return false;
  recoveryInFlight=true;recoveryAttempts++;clearStall();clearTimeout(watchdog);resolutionBusy=false;
  resumeAt=onlineAudio.currentTime>0?onlineAudio.currentTime:lastPlaybackTime;
  const version=playVersion;playerState('loading');$('#playback-status').textContent='Reconectando la misma canción…';
  onlineAudio.load();
  onlineAudio.play().catch(e=>{if(version!==playVersion||!playbackWanted||e.name==='AbortError')return;if(e.name==='NotAllowedError'){pausePlayback();playerState('paused');$('#playback-status').textContent='Pulsa ▶ para continuar.'}else retryAudioLevel(version)});
  armWatchdog(version,8000);return true;
}
onlineAudio.onplaying=()=>{const current=audioQueue[audioIndex];if(!current||onlineAudio.getAttribute('src')==='/silence.wav'||onlineAudio.paused||onlineAudio.readyState<2)return;playbackWanted=true;audioUnlocked=true;failStreak=0;clearTimeout(watchdog);clearStall();playerState('playing');$('#playback-status').textContent=activeLevel===0?(current.provider==='local'?'Reproduciendo tus archivos':'Reproduciendo al instante desde este dispositivo'):'Reproduciendo · '+activeEngine+' · Nivel '+activeLevel;if(recordedReference!==current.id){recordedReference=current.id;const version=playVersion;setTimeout(()=>{if(version===playVersion)prefetchAudio(audioQueue.slice(audioIndex+1,audioIndex+2))},5000);ensureRadio();if(current.provider!=='local')api('/api/online/items/'+current.id,'PATCH',{played:true}).then(r=>{if(version===playVersion&&r?.duration>0){current.duration=r.duration;progress(onlineAudio.currentTime,trackDuration())}}).catch(()=>{})}};
// The last seconds of a song. Safari can stop there without 'ended' (a failed last range
// request of the stream, or a catalogue length a little longer than the audio): that is the end.
const nearTrackEnd=(margin=3)=>{const total=trackDuration();return total>0&&lastPlaybackTime>.25&&total-onlineAudio.currentTime<margin};
let endedVersion=-1,endedIntent=-1;
// Do not leave a ready next song behind a suspended radio promise/timer.
function resumePendingEnd(){
  if(endedVersion!==playVersion||endedIntent!==transitionIntent||!playbackWanted||audioIndex>=audioQueue.length-1)return false;
  advance();return true;
}
function nativeTrackEnded(){
  const raw=onlineAudio.duration,t=onlineAudio.currentTime;
  return onlineAudio.ended||nearTrackEnd()||(!onlineAudio.seeking&&Number.isFinite(raw)&&raw>0&&lastPlaybackTime>.25&&raw-t<.75);
}
onlineAudio.onerror=()=>{const src=onlineAudio.getAttribute('src');if(!playbackWanted||!src||src==='/silence.wav')return;resolutionBusy=false;recoveryInFlight=false;if(nearTrackEnd()){onlineAudio.onended();return}if(lastPlaybackTime>.25&&recoverCurrent())return;retryAudioLevel(playVersion)};
onlineAudio.addEventListener('playing',()=>{if(onlineAudio.getAttribute('src')!=='/silence.wav'){recoveryInFlight=false;endedVersion=-1}});
onlineAudio.onended=async event=>{
  // Assigning the next src in pause-at-EOF can precede the old source's queued
  // ended event. That stale event must not finish the new song at second zero.
  if(event?.type==='ended'&&!onlineAudio.ended&&onlineAudio.currentTime<.25)return;
  if(fixingDuration||onlineAudio.getAttribute('src')==='/silence.wav')return;
  if(endedVersion===playVersion){resumePendingEnd();return}
  const version=playVersion,intent=transitionIntent;endedVersion=version;endedIntent=intent;
  clearStall();clearTimeout(watchdog);
  if(audioIndex<audioQueue.length-1){advance();return}
  playerState('loading');$('#playback-status').textContent='Buscando más música para ti…';let added=await ensureRadio(true);
  // One more try after a moment: a request that just failed (network, rate limit) may answer now.
  if(version!==playVersion||intent!==transitionIntent)return;
  if(!added&&autoplay&&audioIndex>=audioQueue.length-1){await new Promise(r=>setTimeout(r,3000));if(version===playVersion&&intent===transitionIntent)added=await ensureRadio(true)}
  if(version!==playVersion||intent!==transitionIntent)return;
  // The listener may have queued a song while recommendations were pending.
  if(audioIndex<audioQueue.length-1)advance();else{playbackWanted=false;queueFinished=true;playerState('paused');$('#playback-status').textContent='Fin de la cola. Añade una canción y sonará al momento.'}
};
function advance(){
  if(!audioQueue[audioIndex]?.radio&&audioQueue[audioIndex+1]?.radio)notify('Tu cola terminó: seguimos con recomendaciones similares.');
  audioIndex++;startOnline();
}
// Explicit Pause clears playbackWanted first. A native pause at EOF instead
// chains synchronously, even when background JavaScript timers cannot run.
onlineAudio.onpause=()=>{
  const src=onlineAudio.getAttribute('src');
  if(fixingDuration||resolutionBusy||!src||src==='/silence.wav')return;
  // iOS may suspend timers immediately after pausing at EOF. Chain the source
  // synchronously while the native media callback still has background access.
  if(playbackWanted&&nativeTrackEnded()){onlineAudio.onended();return}
  if(document.hidden&&playbackWanted){clearStall();playerState('paused');return}
  if(!onlineAudio.error&&!['loading','error'].includes($('#online-player').dataset.state)){playbackWanted=false;clearStall();clearTimeout(watchdog);playerState('paused')}
};
function restoreOnlinePlayback(){
  if(document.hidden||!me||!playbackWanted||!audioQueue[audioIndex])return;
  if(onlineAudio.ended||(onlineAudio.paused&&nativeTrackEnded())){
    if(!resumePendingEnd()){endedVersion=-1;onlineAudio.onended()}return;
  }
  if(onlineAudio.paused&&onlineAudio.getAttribute('src')){
    onlineAudio.autoplay=true;
    onlineAudio.play().catch(e=>{if(e.name==='NotAllowedError'){pausePlayback();$('#playback-status').textContent='Pulsa ▶ para continuar.'}else if(e.name!=='AbortError')retryAudioLevel()});
  }
}
document.addEventListener('visibilitychange',()=>{
  if(document.hidden&&playbackWanted){prepareAudioSession();ensureRadio();prefetchNext()}
  else restoreOnlinePlayback();
});
window.addEventListener('pageshow',restoreOnlinePlayback);
// Do not replace the startup watchdog or postpone reconnection on every waiting event.
onlineAudio.onwaiting=()=>{
  if(!playbackWanted||onlineAudio.paused||fixingDuration)return;
  playerState('loading');if(lastPlaybackTime<=.25||stallTimer!==null)return;
  const version=playVersion,at=onlineAudio.currentTime;
  stallTimer=setTimeout(()=>{stallTimer=null;if(version!==playVersion||!playbackWanted||onlineAudio.paused)return;if(onlineAudio.currentTime>at+.1){onlineAudio.onwaiting();return}if(nearTrackEnd()){onlineAudio.onended();return}if(!recoverCurrent()){resolutionBusy=false;retryAudioLevel(version)}},3000);
};
// Some browsers stop advancing without any 'waiting' or 'ended' event. A clock that has not
// moved for 3 s while it should play ends the song (last seconds) or goes through the stall path.
let frozenAt=-1,frozenSince=0;
setInterval(()=>{
  const src=onlineAudio.getAttribute('src'),t=onlineAudio.currentTime;
  if(!playbackWanted||onlineAudio.paused||!src||src==='/silence.wav'||fixingDuration||resolutionBusy||recoveryInFlight||lastPlaybackTime<=.25||t!==frozenAt){frozenAt=t;frozenSince=Date.now();return}
  if(Date.now()-frozenSince<3000)return;
  frozenSince=Date.now();
  if(nearTrackEnd())onlineAudio.onended();else if(stallTimer===null)onlineAudio.onwaiting();
},1000);
onlineAudio.addEventListener('seeked',()=>{lastPlaybackTime=onlineAudio.currentTime});
window.addEventListener('online',()=>{if(playbackWanted&&$('#online-player').dataset.state==='loading'&&lastPlaybackTime>.25)recoverCurrent()});
onlineAudio.oncanplay=()=>{if(!onlineAudio.paused&&$('#online-player').dataset.state==='loading'&&onlineAudio.readyState>=3)playerState('playing')};
// Use catalogue metadata when Safari reports an unreliable streamed duration.
// Never seek to the end during startup: that triggers extra Range requests and stalls playback.
const unreliable=(d,known)=>!Number.isFinite(d)||d<=0||(known>0&&Math.abs(d-known)>Math.max(3,known*.1));
function trackDuration(){const d=onlineAudio.duration,known=audioQueue[audioIndex]?.duration;return known>0&&unreliable(d,known)?known:d}
window.IVoxPlayerControls.duration=trackDuration;
onlineAudio.onloadedmetadata=()=>{
  if(resumeAt){const at=resumeAt;resumeAt=0;try{onlineAudio.currentTime=at}catch{}}
  progress(onlineAudio.currentTime,trackDuration());
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
  if(onlineAudio.getAttribute('src')==='/silence.wav'||fixingDuration)return;const total=trackDuration();progress(onlineAudio.currentTime,total);
  // Some browsers (Safari) can skip or misorder 'playing': audible progress means it plays.
  if(!onlineAudio.paused&&onlineAudio.readyState>=3&&onlineAudio.currentTime>lastPlaybackTime+.05&&$('#online-player').dataset.state==='loading'&&!recoveryInFlight)onlineAudio.onplaying();
  if(onlineAudio.readyState>=2)lastPlaybackTime=onlineAudio.currentTime;
  // 30 s before the end the next song is resolved (and the radio fetched) so it starts at once.
  if(!nearEnd&&total>0&&total-onlineAudio.currentTime<30&&!onlineAudio.paused){nearEnd=true;prefetchedReset(audioQueue[audioIndex+1]);prefetchNext();ensureRadio()}
  // Safari can report a stream far longer than the song (HE-AAC at twice its length): past
  // the catalogue length only silence is left, so the next song starts.
  const known=audioQueue[audioIndex]?.duration,raw=onlineAudio.duration;
  if(known>0&&playbackWanted&&!onlineAudio.paused&&(Number.isFinite(raw)?raw>known*1.4&&onlineAudio.currentTime>known+2:onlineAudio.currentTime>known+5))onlineAudio.onended();
};
['#online-player .player-heading','#online-player .np-art'].forEach(s=>$(s).onclick=()=>nowPlaying.open());
$('#np-expand').onclick=()=>nowPlaying.open();
function toggleOnline(){
  if(!audioQueue[audioIndex])return;
  if(onlineAudio.ended){
    if(audioIndex<audioQueue.length-1)skipBy(1);
    else{playbackWanted=true;endedVersion=-1;onlineAudio.onended()}
    return;
  }
  const source=onlineAudio.getAttribute('src');
  if(!source||source==='/silence.wav'||failedReferences.has(audioQueue[audioIndex].id)){failedReferences.delete(audioQueue[audioIndex].id);failStreak=0;startOnline();return}
  // paused becomes false as soon as play() is requested, before any audio is
  // ready. A repeat Play while loading must not silently cancel that request.
  if($('#online-player').dataset.state==='loading'){
    if(Date.now()-loadingSince<6000)return;
    playbackWanted=true;if(lastPlaybackTime>.25){recoveryAttempts=0;recoveryInFlight=false;if(recoverCurrent())return}
    startOnline();return;
  }
  if(onlineAudio.paused){playbackWanted=true;onlineAudio.play().catch(e=>{if(e.name!=='AbortError')retryAudioLevel()})}else pausePlayback();
}
$('#np-toggle').onclick=toggleOnline;
$('#np-download').onclick=()=>perform(()=>downloadOnline(audioQueue[audioIndex]));
$('#np-like').onclick=e=>{const v=audioQueue[audioIndex];if(v)perform(()=>setFavorite(v,!savedVideo(v.id).favorite,e.currentTarget))};
// Lock screen / headphones / Control Center controls on iPhone and Android.
if('mediaSession' in navigator){
  const actions={play:()=>{if(onlineAudio.paused||!onlineAudio.getAttribute('src'))toggleOnline()},pause:pausePlayback,previoustrack:()=>$('#online-previous').click(),nexttrack:()=>nextTrack(),seekto:d=>{if(d.seekTime!=null)window.IVoxPlayerControls.seek(d.seekTime)}};
  for(const [name,handler] of Object.entries(actions)){try{navigator.mediaSession.setActionHandler(name,(d)=>{if(!$('#online-player').hidden)handler(d)})}catch{}}
}
document.addEventListener('keydown',e=>{
  if($('#online-player').hidden||e.target.closest('input,textarea,select,[contenteditable]')||e.metaKey||e.ctrlKey||e.altKey)return;
  if(e.code==='Space'&&!e.target.closest('button')){e.preventDefault();toggleOnline()}
  else if(e.shiftKey&&e.code==='ArrowRight'){e.preventDefault();nextTrack()}else if(e.shiftKey&&e.code==='ArrowLeft'){e.preventDefault();skipBy(-1)}
  else if(e.key==='l'||e.key==='L')$('#np-like').click();
});
window.stopOnline=function(){beginPlayback();onlineAudio.pause();onlineAudio.removeAttribute('src');onlineAudio.load();audioQueue=[];audioIndex=-1;recordedReference=null;radioTried.clear();failStreak=0;if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null}$('#online-player').hidden=true;document.body.classList.remove('has-online-player');nowPlaying.close(true);playerState('idle');if('mediaSession' in navigator)navigator.mediaSession.metadata=null};
window.resetOnlineSession=function(){stopOnline();cancelOnlineSearch();prefetched.clear();onlineLoaded=false;searchView={query:'',source:'auto',resolved:'piped',items:[],next:null,error:null,loading:false,notice:''};onlineData={saves:[],playlists:[],items:{},recent:[]}};
window.pauseOnline=pausePlayback;
// "Previous" restarts the song after the first few seconds, like every music app.
$('#close-online').onclick=()=>stopOnline();$('#online-previous').onclick=()=>{if(onlineAudio.currentTime>4&&!skipTimer){window.IVoxPlayerControls.seek(0);return}skipBy(-1)};$('#online-next').onclick=()=>nextTrack();
$('#queue-toggle').onclick=()=>nowPlaying.open('queue');

function privacy(){shell(`<h1>Privacidad y condiciones</h1><section class="panel"><h2>Tu música online, tus listas privadas</h2><p>IVOX guarda cuentas, contraseñas mediante hash, sesiones y referencias de favoritos, playlists, escuchar después, historial y búsquedas recientes en su servidor. Se comparten únicamente entre tus sesiones.</p><p>Las consultas se envían a YouTube Music, a la instancia privada de Piped o a Audius. El audio se transmite sin guardarlo en disco, mediante un enlace temporal protegido por tu sesión. Las copias guardadas por versiones anteriores pueden seguir en este navegador y se eliminan al limpiar los datos del sitio. La reproducción actual no descarga una segunda copia completa en segundo plano. Los proveedores reciben las peticiones y la dirección IP del servidor; las imágenes externas pueden recibir la IP de tu navegador. Las letras proceden de los socios de YouTube Music indicados en cada una.</p><p>IVOX no necesita tus credenciales de YouTube o Audius. No modifica tus cuentas de esos servicios. Piped no es un servicio oficial de YouTube; puede dejar de funcionar o ser bloqueado.</p><p>Puedes borrar referencias, playlists, recientes e historial. Este botón elimina tus referencias online de IVOX; conserva tu cuenta, playlists vacías y archivos locales.</p><button id="clear-online-data" class="danger">Borrar mis datos de biblioteca online</button></section>`);$('#clear-online-data').onclick=()=>confirmAction('¿Borrar tus datos online de IVOX?','Se eliminarán favoritos, escuchar después, historial, búsquedas y referencias de todas tus playlists. No se puede deshacer.',async()=>{await api('/api/online/library','DELETE');stopOnline();notify('Datos online eliminados de IVOX.')})}
