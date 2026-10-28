// "Now Playing": full-screen sheet with big cover, synced lyrics, a reorderable
// queue, swipe gestures, likes and colour taken from the artwork. It only drives
// the shared <audio> through online.js (skipBy, toggleOnline, setFavorite...).
const sheet=$('#np-sheet'),sheetCover=$('#sheet-cover'),lyricsBox=$('#lyrics');
const isIOS=/iP(hone|ad|od)/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const clock=n=>Number.isFinite(n)?`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`:'0:00';
let sheetTab='queue',ignorePop=false,lastFocus=null;

const nowPlaying={
  open(tab){
    if(!audioQueue[audioIndex])return;if(tab)selectTab(tab);
    if(sheet.hidden){lastFocus=document.activeElement;sheet.hidden=false;document.body.classList.add('sheet-open');requestAnimationFrame(()=>sheet.classList.add('open'));
      // Android's back button (and the browser's) closes the sheet instead of leaving the page.
      if(!history.state?.sheet)history.pushState({...(history.state||{}),sheet:true},'',location.href);
      $('#sheet-close').focus({preventScroll:true});}
    this.track(audioQueue[audioIndex]);drawQueue();if(sheetTab==='lyrics')loadLyrics();visualizer.sync();
  },
  close(silent=false){
    if(sheet.hidden)return;sheet.classList.remove('open');document.body.classList.remove('sheet-open');
    setTimeout(()=>{if(!sheet.classList.contains('open'))sheet.hidden=true},reducedMotion.matches?0:320);
    if(!silent&&history.state?.sheet){ignorePop=true;history.back()}
    visualizer.sync();lastFocus?.focus?.({preventScroll:true});
  },
  isOpen:()=>!sheet.hidden&&sheet.classList.contains('open'),
  state(state){
    sheet.dataset.state=state;$('#sheet-toggle').setAttribute('aria-label',state==='playing'?'Pausar':'Reproducir');
    $('#sheet-autoplay').setAttribute('aria-pressed',String(autoplay));visualizer.sync();
  },
  track(item){
    if(!item)return;const art=bigArt(item.thumbnail);
    $('#sheet-title').textContent=item.title;$('#sheet-artist').textContent=item.channel||'';
    $('#sheet-context').textContent=item.radio?'Radio · canciones similares':item.provider==='local'?'Tus archivos':item.album||'Cola de reproducción';
    if(art){sheetCover.src=art;sheetCover.hidden=false;$('#sheet-backdrop-img').src=art}else{sheetCover.hidden=true;sheetCover.removeAttribute('src');$('#sheet-backdrop-img').removeAttribute('src')}
    sheetCover.onerror=()=>{sheetCover.hidden=true};
    tint(art);this.liked();
    const local=item.provider==='local';$('#sheet-like').hidden=local;$('#np-like').hidden=local;$('#sheet-download').hidden=local;
    if(lyricsFor!==item.id&&sheetTab==='lyrics'&&!sheet.hidden)loadLyrics();
  },
  progress(time,total,loaded){
    if(sheet.hidden)return;const ratio=total>0?Math.min(1,time/total):0;
    $('#sheet-current').textContent=clock(time);$('#sheet-duration').textContent=clock(total);
    $('#sheet-fill').style.width=ratio*100+'%';$('#sheet-buffer').style.width=(loaded||0)*100+'%';
    if(!sheetSeeking)$('#sheet-range').value=Math.round(ratio*1000);
  },
  liked(){
    const item=audioQueue[audioIndex],on=!!(item&&savedVideo(item.id).favorite);
    for(const b of [$('#sheet-like'),$('#np-like')]){b.setAttribute('aria-pressed',String(on));b.setAttribute('aria-label',on?'Quitar de favoritos':'Me gusta')}
  },
  // Floating hearts rise from whatever was tapped (button, cover or row).
  burst(origin){
    if(reducedMotion.matches)return;const rect=(origin?.getBoundingClientRect?.()||$('#np-like').getBoundingClientRect());
    const layer=document.createElement('div');layer.className='heart-layer';layer.setAttribute('aria-hidden','true');
    layer.style.left=rect.left+rect.width/2+'px';layer.style.top=rect.top+rect.height/2+'px';
    for(let i=0;i<9;i++){const h=document.createElement('span');h.textContent='♥';h.style.setProperty('--dx',(Math.random()*120-60).toFixed(0)+'px');h.style.setProperty('--rot',(Math.random()*60-30).toFixed(0)+'deg');h.style.setProperty('--size',(14+Math.random()*16).toFixed(0)+'px');h.style.animationDelay=(i*45)+'ms';layer.append(h)}
    document.body.append(layer);setTimeout(()=>layer.remove(),1500);
  },
  radioAdded(n){if(!sheet.hidden)drawQueue();const status=$('#queue-status');if(status)status.textContent=`Radio: ${n} canciones similares añadidas`}
};
window.sheetPop=()=>{if(ignorePop){ignorePop=false;return true}if(!sheet.hidden){nowPlaying.close(true);return true}return false};

// ---- Controls mirrored from the mini player ----------------------------------
let sheetSeeking=false;
$('#sheet-close').onclick=()=>nowPlaying.close();
$('#sheet-toggle').onclick=()=>toggleOnline();
$('#sheet-prev').onclick=()=>$('#online-previous').click();
$('#sheet-next').onclick=()=>nextTrack();
$('#sheet-download').onclick=()=>perform(()=>downloadOnline(audioQueue[audioIndex]));
$('#sheet-like').onclick=e=>{const v=audioQueue[audioIndex];if(v)perform(()=>setFavorite(v,!savedVideo(v.id).favorite,e.currentTarget))};
$('#sheet-autoplay').onclick=()=>{setAutoplay(!autoplay);notify(autoplay?'Autoplay activado: la música no se detendrá.':'Autoplay desactivado.')};
$('#sheet-range').oninput=e=>{sheetSeeking=true;const total=trackDuration();if(total>0){$('#sheet-fill').style.width=e.target.value/10+'%';$('#sheet-current').textContent=clock(total*e.target.value/1000)}};
$('#sheet-range').onchange=e=>{sheetSeeking=false;window.IVoxPlayerControls.seek(trackDuration()*e.target.value/1000)};
// Escape closes the sheet wherever focus is (a drag can leave it on <body>), unless a dialog is on top.
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!sheet.hidden&&!dialog.open){e.preventDefault();nowPlaying.close()}});
sheet.addEventListener('keydown',e=>{
  // Keep keyboard focus inside the open sheet.
  if(e.key==='Tab'){const items=[...sheet.querySelectorAll('button:not([hidden]),input,[tabindex="0"]')].filter(el=>el.offsetParent);if(!items.length)return;const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}
});
function selectTab(tab){
  sheetTab=tab;for(const name of ['queue','lyrics']){const on=name===tab;$('#tab-'+name).setAttribute('aria-selected',String(on));$('#tab-'+name).tabIndex=on?0:-1;$('#panel-'+name).hidden=!on}
  if(tab==='lyrics')loadLyrics();
}
$('#tab-queue').onclick=()=>selectTab('queue');$('#tab-lyrics').onclick=()=>selectTab('lyrics');

// ---- Queue: tap to play, × to remove, drag the grip (or arrow keys) to reorder -
function drawQueue(){
  const el=$('#online-queue');if(!el)return;
  const upcoming=audioQueue.length-audioIndex-1;
  $('#queue-count').textContent=audioQueue.length?`${upcoming} a continuación`:'';
  el.innerHTML=audioQueue.map((v,i)=>`<li data-index="${i}" class="${i===audioIndex?'current':i<audioIndex?'played':''}"><button class="queue-grip" data-grip="${i}" aria-label="Mover ${esc(v.title)}. Usa las flechas arriba y abajo"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h2v2H8zm6 0h2v2h-2zM8 11h2v2H8zm6 0h2v2h-2zM8 16h2v2H8zm6 0h2v2h-2z"/></svg></button><button class="queue-title" data-queue-play="${i}" ${i===audioIndex?'aria-current="true"':''}>${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" loading="lazy" width="40" height="40">`:'<span class="queue-glyph" aria-hidden="true">♫</span>'}<span><strong>${esc(v.title)}</strong><small>${esc(v.channel||'')}${v.radio?' · <em>Radio</em>':''}</small></span></button><button class="queue-remove" data-queue-remove="${i}" aria-label="Quitar ${esc(v.title)} de la cola">×</button></li>`).join('')||'<li class="queue-empty">La cola está vacía.</li>';
  el.querySelectorAll('[data-queue-play]').forEach(b=>b.onclick=()=>{if(dragMoved)return;const n=Number(b.dataset.queuePlay);if(n===audioIndex)toggleOnline();else skipTo(n)});
  el.querySelectorAll('[data-queue-remove]').forEach(b=>b.onclick=()=>{const n=Number(b.dataset.queueRemove);audioQueue.splice(n,1);if(n<audioIndex)audioIndex--;else if(n===audioIndex){if(!audioQueue.length)return stopOnline();audioIndex=Math.min(n,audioQueue.length-1);startOnline()}drawQueue();ensureRadio()});
  el.querySelectorAll('[data-grip]').forEach(grip=>{grip.onpointerdown=startDrag;grip.onkeydown=e=>{const n=Number(grip.dataset.grip),to=e.key==='ArrowUp'?n-1:e.key==='ArrowDown'?n+1:-1;if(to<0||to>=audioQueue.length)return;e.preventDefault();moveQueue(n,to);drawQueue();el.querySelector(`[data-grip="${to}"]`)?.focus()}});
  // With autoplay on, "next" at the end of the queue fetches the radio instead of being disabled.
  $('#online-previous').disabled=!audioQueue.length;$('#online-next').disabled=audioIndex>=audioQueue.length-1&&!autoplay;
  $('#sheet-prev').disabled=$('#online-previous').disabled;$('#sheet-next').disabled=$('#online-next').disabled;
}
function moveQueue(from,to){
  if(from===to)return;const [item]=audioQueue.splice(from,1);audioQueue.splice(to,0,item);
  // A song moved by hand is a choice, no longer an automatic radio pick.
  if(item.radio&&to<=audioIndex+1)item.radio=false;
  if(from===audioIndex)audioIndex=to;else if(from<audioIndex&&to>=audioIndex)audioIndex--;else if(from>audioIndex&&to<=audioIndex)audioIndex++;
  prefetchNext();
}
// Pointer-based so it works with fingers on iOS, where HTML5 drag and drop does not.
let dragMoved=false;
function startDrag(e){
  if(e.button>0)return;e.preventDefault();
  const grip=e.currentTarget,li=grip.closest('li'),list=[...li.parentElement.children],from=list.indexOf(li),height=li.getBoundingClientRect().height,startY=e.clientY;
  const scroller=$('#panel-queue');let to=from;dragMoved=false;
  grip.setPointerCapture(e.pointerId);li.classList.add('dragging');
  const move=ev=>{
    const dy=ev.clientY-startY;if(Math.abs(dy)>4)dragMoved=true;li.style.transform=`translateY(${dy}px)`;
    to=Math.max(0,Math.min(list.length-1,from+Math.round(dy/height)));
    list.forEach((other,i)=>{if(other===li)return;const shift=from<to&&i>from&&i<=to?-height:from>to&&i<from&&i>=to?height:0;other.style.transform=shift?`translateY(${shift}px)`:''});
    // Auto-scroll near the panel edges for long queues.
    const box=scroller.getBoundingClientRect();if(ev.clientY<box.top+40)scroller.scrollTop-=8;else if(ev.clientY>box.bottom-40)scroller.scrollTop+=8;
  };
  const end=()=>{grip.removeEventListener('pointermove',move);grip.removeEventListener('pointerup',end);grip.removeEventListener('pointercancel',end);list.forEach(other=>other.style.transform='');li.classList.remove('dragging');moveQueue(from,to);drawQueue();setTimeout(()=>{dragMoved=false},0)};
  grip.addEventListener('pointermove',move);grip.addEventListener('pointerup',end);grip.addEventListener('pointercancel',end);
}

// ---- Synced lyrics -----------------------------------------------------------
const lyricsCache=new Map();let lyricsFor=null,lyricLines=[],lyricActive=-1,lyricsController=null,lyricsFrame=0,manualScrollUntil=0;
async function loadLyrics(){
  const item=audioQueue[audioIndex];if(!item||lyricsFor===item.id)return;
  lyricsFor=item.id;lyricLines=[];lyricActive=-1;lyricsController?.abort();
  if(item.provider!=='piped'){lyricsBox.innerHTML='<p class="lyrics-empty">La letra solo está disponible para canciones del catálogo de YouTube Music.</p>';return}
  let data=lyricsCache.get(item.id);
  if(!data){
    lyricsBox.innerHTML='<div class="lyrics-loading" role="status"><span></span><span></span><span></span><span class="sr-only">Cargando letra…</span></div>';
    lyricsController=new AbortController();
    try{data=await api('/api/online/lyrics/'+item.id,'GET',undefined,{signal:lyricsController.signal})}
    catch(e){if(e.name==='AbortError')return;data={missing:true,message:e.message}}
    lyricsCache.set(item.id,data);if(lyricsFor!==item.id)return;
  }
  if(data.missing){lyricsBox.innerHTML=`<p class="lyrics-empty">${esc(data.message||'Letra no disponible para esta canción.')}</p>`;return}
  const source=data.source?`<p class="lyrics-source">${esc(data.source.replace(/^Source:\s*/i,'Fuente: '))}</p>`:'';
  if(data.synced){
    lyricLines=data.lines.filter(l=>l.text.trim());
    lyricsBox.innerHTML=`<div class="lyrics-synced">${lyricLines.map((l,i)=>`<p data-line="${i}" tabindex="-1">${esc(l.text.trim()==='♪'?'♪ ♪ ♪':l.text)}</p>`).join('')}</div>${source}`;
    lyricsBox.querySelectorAll('[data-line]').forEach(p=>p.onclick=()=>{window.IVoxPlayerControls.seek(lyricLines[p.dataset.line].start/1000);manualScrollUntil=0});
    syncLyrics(true);
  }else lyricsBox.innerHTML=`<p class="lyrics-plain">${esc(data.text).replace(/\n/g,'<br>')}</p>${source}`;
}
function syncLyrics(force=false){
  if(!lyricLines.length||$('#panel-lyrics').hidden)return;
  const ms=onlineAudio.currentTime*1000+250;let lo=0,hi=lyricLines.length-1,found=-1;
  while(lo<=hi){const mid=(lo+hi)>>1;if(lyricLines[mid].start<=ms){found=mid;lo=mid+1}else hi=mid-1}
  if(found===lyricActive&&!force)return;lyricActive=found;
  lyricsBox.querySelectorAll('[data-line]').forEach((p,i)=>{p.classList.toggle('active',i===found);p.classList.toggle('past',i<found)});
  const line=lyricsBox.querySelector(`[data-line="${Math.max(0,found)}"]`);
  if(line&&Date.now()>manualScrollUntil)line.scrollIntoView({block:'center',behavior:reducedMotion.matches||force?'auto':'smooth'});
}
// The user scrolling the lyrics pauses auto-scroll for a few seconds.
for(const ev of ['wheel','touchmove'])$('#panel-lyrics').addEventListener(ev,()=>{manualScrollUntil=Date.now()+4000},{passive:true});
function lyricsLoop(){lyricsFrame=0;if(sheet.hidden||$('#panel-lyrics').hidden||onlineAudio.paused)return;syncLyrics();lyricsFrame=requestAnimationFrame(lyricsLoop)}
onlineAudio.addEventListener('playing',()=>{if(!lyricsFrame)lyricsFrame=requestAnimationFrame(lyricsLoop)});
onlineAudio.addEventListener('seeked',()=>syncLyrics(true));
$('#tab-lyrics').addEventListener('click',()=>{if(!lyricsFrame)lyricsFrame=requestAnimationFrame(lyricsLoop)});

// ---- Gestures ----------------------------------------------------------------
// Sheet cover: swipe left/right changes song, swipe down closes, double tap likes.
(()=>{
  const stage=$('#sheet-cover-wrap');let start=null,lastTap=0;
  stage.addEventListener('pointerdown',e=>{if(!e.isPrimary)return;start={x:e.clientX,y:e.clientY,t:performance.now()};stage.setPointerCapture(e.pointerId)});
  stage.addEventListener('pointermove',e=>{if(!start)return;const dx=e.clientX-start.x,dy=e.clientY-start.y;if(Math.abs(dx)>Math.abs(dy))stage.style.transform=`translateX(${dx*.5}px) rotate(${dx*.02}deg)`;else if(dy>0)stage.style.transform=`translateY(${dy*.4}px) scale(${1-Math.min(dy,300)/3000})`});
  const end=e=>{
    if(!start)return;const dx=e.clientX-start.x,dy=e.clientY-start.y,quick=performance.now()-start.t<800;start=null;stage.style.transform='';
    if(quick&&Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy)*1.5){stage.classList.add(dx<0?'swipe-left':'swipe-right');setTimeout(()=>stage.classList.remove('swipe-left','swipe-right'),350);skipBy(dx<0?1:-1);return}
    if(quick&&dy>110&&dy>Math.abs(dx)*1.5){nowPlaying.close();return}
    if(Math.abs(dx)<10&&Math.abs(dy)<10){
      const now=performance.now();
      if(now-lastTap<320){lastTap=0;const v=audioQueue[audioIndex];if(v&&v.provider!=='local'){stage.classList.remove('pop');void stage.offsetWidth;stage.classList.add('pop');if(!savedVideo(v.id).favorite)perform(()=>setFavorite(v,true,stage));else nowPlaying.burst(stage)}}
      else lastTap=now;
    }
  };
  stage.addEventListener('pointerup',end);stage.addEventListener('pointercancel',()=>{start=null;stage.style.transform=''});
})();
// Sheet header: drag down to close (the panel below keeps its own scrolling).
(()=>{
  const handle=$('.sheet-top');let y=null;
  handle.addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;y=e.clientY});
  handle.addEventListener('pointerup',e=>{if(y!==null&&e.clientY-y>70)nowPlaying.close();y=null});
})();
// Mini player: swipe up opens the sheet (player-controls.js handles left/right).
(()=>{
  const row=$('#online-player .np-row');let start=null;
  row.addEventListener('pointerdown',e=>{if(e.pointerType!=='touch'||e.target.closest('button,input,a,.np-volume'))return;start={x:e.clientX,y:e.clientY}});
  row.addEventListener('pointerup',e=>{if(!start)return;const dx=e.clientX-start.x,dy=e.clientY-start.y;start=null;if(dy<-45&&Math.abs(dy)>Math.abs(dx)*1.5)nowPlaying.open()});
})();

// ---- Colour from the artwork -------------------------------------------------
// Covers are served with CORS, so a 24x24 sample gives the dominant vivid colour,
// kept dark enough for gold text to stay readable on top.
let tintFor=null;
function tint(url){
  if(url===tintFor)return;tintFor=url;
  if(!url){setTint(null);return}
  const img=new Image();img.crossOrigin='anonymous';img.decoding='async';
  img.onload=()=>{
    if(tintFor!==url)return;
    try{
      const c=document.createElement('canvas');c.width=c.height=24;const x=c.getContext('2d',{willReadFrequently:true});x.drawImage(img,0,0,24,24);
      const d=x.getImageData(0,0,24,24).data;let r=0,g=0,b=0,w=0;
      for(let i=0;i<d.length;i+=4){const R=d[i],G=d[i+1],B=d[i+2],max=Math.max(R,G,B),min=Math.min(R,G,B),s=max?(max-min)/max:0,l=max/255,weight=s*s*(l>.18&&l<.96?1:.08)+.004;r+=R*weight;g+=G*weight;b+=B*weight;w+=weight}
      const [h,s]=hsl(r/w,g/w,b/w);setTint(`hsl(${h.toFixed(0)} ${Math.max(35,Math.min(80,s)).toFixed(0)}% 38%)`);
    }catch{setTint(null)}
  };
  img.onerror=()=>setTint(null);img.src=url;
}
function hsl(r,g,b){r/=255;g/=255;b/=255;const max=Math.max(r,g,b),min=Math.min(r,g,b),l=(max+min)/2;if(max===min)return [220,0,l*100];const d=max-min,s=l>.5?d/(2-max-min):d/(max+min);let h=max===r?(g-b)/d+(g<b?6:0):max===g?(b-r)/d+2:(r-g)/d+4;return [h*60,s*100,l*100]}
function setTint(color){document.documentElement.style.setProperty('--np-tint',color||'#2a3f8f')}

// ---- Radial visualizer (opt-in) ----------------------------------------------
// Routing the element through Web Audio would silence it in the iOS background, so
// iPhone/iPad never get it; elsewhere it is created only from the user's tap.
const visualizer={
  ctx:null,analyser:null,data:null,on:false,frame:0,canvas:$('#sheet-visualizer'),
  supported:!isIOS&&!!(window.AudioContext||window.webkitAudioContext),
  toggle(){
    if(!this.supported)return;
    if(!this.ctx){
      try{
        const Context=window.AudioContext||window.webkitAudioContext;this.ctx=new Context();
        const source=this.ctx.createMediaElementSource(onlineAudio);this.analyser=this.ctx.createAnalyser();
        this.analyser.fftSize=256;this.analyser.smoothingTimeConstant=.78;source.connect(this.analyser);this.analyser.connect(this.ctx.destination);
        this.data=new Uint8Array(this.analyser.frequencyBinCount);
        // If the context is ever suspended while music plays, the music would go silent: resume it.
        this.ctx.onstatechange=()=>{if(this.ctx.state!=='running'&&!onlineAudio.paused)this.ctx.resume().catch(()=>{})};
      }catch{this.supported=false;$('#sheet-viz').hidden=true;notify('Este navegador no permite el visualizador.');return}
    }
    this.ctx.resume().catch(()=>{});this.on=!this.on;$('#sheet-viz').setAttribute('aria-pressed',String(this.on));this.sync();
  },
  sync(){
    const active=this.on&&!sheet.hidden&&sheet.dataset.state==='playing';this.canvas.hidden=!this.on;sheet.classList.toggle('viz-on',this.on);
    if(active&&!this.frame)this.frame=requestAnimationFrame(()=>this.draw());
  },
  draw(){
    this.frame=0;if(!this.on||sheet.hidden||sheet.dataset.state!=='playing')return;
    const c=this.canvas,dpr=Math.min(2,devicePixelRatio||1),size=c.clientWidth;
    if(c.width!==Math.round(size*dpr)){c.width=c.height=Math.round(size*dpr)}
    const x=c.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);x.clearRect(0,0,size,size);
    this.analyser.getByteFrequencyData(this.data);
    const bars=72,center=size/2,radius=size*.36,tintColor=getComputedStyle(document.documentElement).getPropertyValue('--np-tint').trim()||'#2a3f8f';
    const gradient=x.createRadialGradient(center,center,radius*.9,center,center,size/2);gradient.addColorStop(0,'#f0d77c');gradient.addColorStop(.5,'#d4af37');gradient.addColorStop(1,tintColor);
    x.strokeStyle=gradient;x.lineCap='round';x.lineWidth=Math.max(2,size/160);
    for(let i=0;i<bars;i++){
      // Mirror the useful (lower) part of the spectrum around the circle.
      const bin=Math.floor((i<bars/2?i:bars-i)/(bars/2)*this.data.length*.7),v=this.data[bin]/255,len=radius*.08+v*v*radius*.42,a=i/bars*Math.PI*2-Math.PI/2;
      x.globalAlpha=.35+v*.65;x.beginPath();x.moveTo(center+Math.cos(a)*radius,center+Math.sin(a)*radius);x.lineTo(center+Math.cos(a)*(radius+len),center+Math.sin(a)*(radius+len));x.stroke();
    }
    x.globalAlpha=1;this.frame=requestAnimationFrame(()=>this.draw());
  }
};
$('#sheet-viz').hidden=!visualizer.supported;
// Volume in the sheet too (the bar's volume panel is hidden on phones). iOS ignores
// audio.volume and only obeys the hardware buttons, so it is not offered there.
$('#sheet-volume-wrap').hidden=isIOS;
$('#sheet-volume').oninput=e=>{onlineAudio.volume=e.target.value/100;if(onlineAudio.volume>0)onlineAudio.muted=false};
onlineAudio.addEventListener('volumechange',()=>{$('#sheet-volume').value=onlineAudio.muted?0:Math.round(onlineAudio.volume*100)});
$('#sheet-viz').onclick=()=>visualizer.toggle();
setTint(null);
