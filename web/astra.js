// Tu Astra: the listener's own "wrapped". The server counts every song start
// (/api/online/wrapped); this page turns it into full-screen stories, a summary
// and a 1080×1920 image to share. Also: sleep timer, repeat and song sharing chips.
(()=>{
const PERIODS={week:'esta semana',month:'este mes',year:'este año',all:'desde siempre'};
const DAYS=['los lunes','los martes','los miércoles','los jueves','los viernes','los sábados','los domingos'];
let period=(()=>{try{const v=localStorage.getItem('ivox-astra-period');return v in PERIODS?v:'month'}catch{return 'month'}})();
let data=null,story=null;
const fmt=n=>Number(n||0).toLocaleString('es');
const hourLabel=h=>h==null?'—':`${String(h).padStart(2,'0')}:00`;
// The hour the listener plays most decides their sign in the sky.
function sign(h){
  if(h==null)return {name:'Estrella naciente',text:'Tu cielo apenas empieza a llenarse.',glyph:'✦'};
  if(h<6)return {name:'Alma de Anubis',text:'Escuchas cuando el mundo duerme. La noche es tuya.',glyph:'☾'};
  if(h<12)return {name:'Luz de Ra',text:'La música te despierta y te pone en marcha.',glyph:'☀'};
  if(h<18)return {name:'Ojo de Horus',text:'Tus tardes tienen banda sonora propia.',glyph:'𓂀'};
  return {name:'Estrella de Isis',text:'Cuando cae el sol, subes el volumen.',glyph:'✶'};
}
const load=p=>api('/api/online/wrapped?period='+p+'&tz='+(-new Date().getTimezoneOffset()));
const songItem=s=>s.item&&s.item.available!==false?{...s.item}:null;
function playTop(){const items=(data?.top_songs||[]).map(songItem).filter(Boolean);if(!items.length){notify('Estas canciones ya no están en el catálogo. Búscalas de nuevo.');return}closeStory();playList(items,0);notify('Sonando tu top '+PERIODS[data.period]+'.')}

// ---- Page -------------------------------------------------------------------
window.astraPage=async function(){
  shell('<div class="astra-page"><div class="astra-hero skeleton" aria-hidden="true"></div><p class="empty" role="status">Leyendo tu cielo…</p></div>');
  try{data=await load(period)}catch(e){if(location.pathname==='/astra')$('.content').innerHTML=`<div class="empty error-state"><h2>No se pudo cargar Tu Astra</h2><p>${esc(e.message)}</p><button id="astra-retry" class="primary">Reintentar</button></div>`;const r=$('#astra-retry');if(r)r.onclick=()=>astraPage();return}
  if(location.pathname!=='/astra')return;document.title='Tu Astra · Ivox-Astra';drawPage();
};
function drawPage(){
  const el=$('.content');if(!el)return;const d=data,s=sign(d.peak_hour),empty=!d.plays;
  el.innerHTML=`<div class="astra-page">
    <section class="astra-hero"><div class="astra-sky" aria-hidden="true"></div>
      <p class="astra-kicker">Ivox-Astra · Anubis</p><h1>Tu Astra</h1>
      <p class="astra-lead">${empty?'Tu cielo todavía está vacío. Escucha música y cada canción se convertirá en una estrella.':`${fmt(d.minutes)} minutos de música ${PERIODS[d.period]}. Este es tu cielo.`}</p>
      <div class="astra-periods" role="group" aria-label="Periodo">${Object.entries(PERIODS).map(([k,v])=>`<button class="chip" data-period="${k}" aria-pressed="${k===d.period}">${v[0].toUpperCase()+v.slice(1)}</button>`).join('')}</div>
      ${empty?'<a href="/" class="primary astra-cta" data-nav>Descubrir música</a>':'<button id="astra-open" class="primary astra-cta">✦ Ver mi Astra</button>'}
    </section>
    ${empty?'':`<section class="astra-stats" aria-label="Resumen">
      <div class="astra-stat"><strong>${fmt(d.minutes)}</strong><span>minutos</span></div>
      <div class="astra-stat"><strong>${fmt(d.plays)}</strong><span>reproducciones</span></div>
      <div class="astra-stat"><strong>${fmt(d.songs)}</strong><span>canciones</span></div>
      <div class="astra-stat"><strong>${fmt(d.artists)}</strong><span>artistas</span></div>
    </section>
    <div class="astra-grid">
      <section class="astra-card"><h2>Tus artistas</h2><ol class="astra-rank">${d.top_artists.map((a,i)=>`<li><span class="astra-n">${i+1}</span><div><strong>${esc(a.name)}</strong><small>${fmt(a.plays)} ${a.plays===1?'reproducción':'reproducciones'} · ${fmt(a.minutes)} min</small></div></li>`).join('')}</ol></section>
      <section class="astra-card"><div class="astra-card-head"><h2>Tus canciones</h2><button id="astra-play" class="chip">▶ Reproducir</button></div><ol class="astra-rank">${d.top_songs.map((v,i)=>`<li><span class="astra-n">${i+1}</span>${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" width="44" height="44" loading="lazy">`:''}<div><strong>${esc(v.title)}</strong><small>${esc(v.artist)} · ${fmt(v.plays)} ${v.plays===1?'vez':'veces'}</small></div></li>`).join('')}</ol></section>
      <section class="astra-card astra-sign"><span class="astra-glyph" aria-hidden="true">${s.glyph}</span><h2>${s.name}</h2><p>${s.text}</p><p class="muted">Tu hora: ${hourLabel(d.peak_hour)} · Sobre todo ${DAYS[d.peak_day]||'—'}</p><p class="muted">Racha: ${fmt(d.streak)} ${d.streak===1?'día':'días'} seguidos · ${fmt(d.days)} ${d.days===1?'día':'días'} con música</p></section>
    </div>
    <p class="provider-note">Tu Astra es privado: solo tú lo ves. Se borra al borrar tu historial.</p>`}
  </div>`;
  el.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>perform(async()=>{period=b.dataset.period;try{localStorage.setItem('ivox-astra-period',period)}catch{}data=await load(period);drawPage()}));
  el.querySelectorAll('[data-nav]').forEach(a=>a.onclick=e=>{e.preventDefault();history.pushState({},'',a.getAttribute('href'));render().catch(e=>notify(e.message))});
  if($('#astra-open'))$('#astra-open').onclick=openStory;
  if($('#astra-play'))$('#astra-play').onclick=playTop;
}

// ---- Stories ----------------------------------------------------------------
function slides(d){
  const s=sign(d.peak_hour),[a1,...artists]=d.top_artists,hours=Math.floor(d.minutes/60);
  return [
    {cls:'intro',html:`<span class="astra-eye" aria-hidden="true">${typeof LOGO!=='undefined'?LOGO:'✦'}</span><p class="astra-kicker">Ivox-Astra presenta</p><h2 class="astra-big">Tu Astra</h2><p>Lo que sonó en tu vida ${PERIODS[d.period]}.</p>`},
    {cls:'minutes',html:`<p class="astra-kicker">Tiempo en las estrellas</p><h2 class="astra-huge" data-count="${d.minutes}">${fmt(d.minutes)}</h2><p class="astra-unit">minutos de música</p><p>${hours>=1?`Más de ${fmt(hours)} ${hours===1?'hora':'horas'}. `:''}${fmt(d.songs)} canciones distintas de ${fmt(d.artists)} artistas.</p>`},
    {cls:'artist',html:`<p class="astra-kicker">Tu artista número uno</p><h2 class="astra-big">${esc(a1.name)}</h2><p>${fmt(a1.plays)} ${a1.plays===1?'reproducción':'reproducciones'} · ${fmt(a1.minutes)} minutos juntos</p>${artists.length?`<ol class="astra-list" start="2">${artists.map(a=>`<li><strong>${esc(a.name)}</strong> <small>${fmt(a.plays)}</small></li>`).join('')}</ol>`:''}`},
    {cls:'songs',html:`<p class="astra-kicker">Las canciones que no soltaste</p><ol class="astra-songs">${d.top_songs.map((v,i)=>`<li><span>${i+1}</span>${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" width="56" height="56">`:'<i aria-hidden="true">♫</i>'}<div><strong>${esc(v.title)}</strong><small>${esc(v.artist)} · ${fmt(v.plays)} ${v.plays===1?'vez':'veces'}</small></div></li>`).join('')}</ol><button class="primary" data-story-play>▶ Reproducir tu top</button>`},
    {cls:'sign',html:`<p class="astra-kicker">Tu signo musical</p><span class="astra-glyph" aria-hidden="true">${s.glyph}</span><h2 class="astra-big">${s.name}</h2><p>${s.text}</p><p class="astra-note">Tu hora favorita: ${hourLabel(d.peak_hour)} · sobre todo ${DAYS[d.peak_day]||''}</p>`},
    {cls:'streak',html:`<p class="astra-kicker">Constancia</p><h2 class="astra-huge">${fmt(d.streak)}</h2><p class="astra-unit">${d.streak===1?'día':'días'} seguidos con música</p><p>Sonaste ${fmt(d.days)} ${d.days===1?'día':'días'} ${PERIODS[d.period]}.</p>`},
    {cls:'final',html:`<p class="astra-kicker">Tu Astra ${PERIODS[d.period]}</p><div class="astra-summary"><div><small>Minutos</small><strong>${fmt(d.minutes)}</strong></div><div><small>Artista</small><strong>${esc(a1.name)}</strong></div><div><small>Canción</small><strong>${esc(d.top_songs[0]?.title||'—')}</strong></div><div><small>Signo</small><strong>${s.name}</strong></div></div><div class="astra-actions"><button class="primary" data-story-share>Compartir mi Astra</button><button data-story-play>▶ Reproducir mi top</button></div>`}
  ];
}
function openStory(){
  if(!data?.plays)return;closeStory();
  const list=slides(data),root=document.createElement('section');
  root.id='astra-story';root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');root.setAttribute('aria-label','Tu Astra');
  root.innerHTML=`<div class="astra-sky" aria-hidden="true"></div><div class="astra-bars" aria-hidden="true">${list.map(()=>'<span><i></i></span>').join('')}</div><button class="astra-close np-icon" aria-label="Cerrar Tu Astra">×</button><div class="astra-slide" aria-live="polite"></div><button class="astra-prev" aria-label="Anterior"></button><button class="astra-next" aria-label="Siguiente"></button>`;
  document.body.append(root);document.body.classList.add('astra-open');
  story={root,list,index:0,timer:0,started:0,held:false,elapsed:0};
  root.querySelector('.astra-close').onclick=closeStory;
  root.querySelector('.astra-prev').onclick=()=>go(story.index-1);
  root.querySelector('.astra-next').onclick=()=>go(story.index+1);
  // Holding a finger on the story pauses it, like every stories viewer.
  root.addEventListener('pointerdown',e=>{if(e.target.closest('button:not(.astra-prev):not(.astra-next)'))return;hold(true)});
  ['pointerup','pointercancel','pointerleave'].forEach(t=>root.addEventListener(t,()=>hold(false)));
  root.addEventListener('keydown',e=>{if(e.key==='Escape')closeStory();else if(e.key==='ArrowRight')go(story.index+1);else if(e.key==='ArrowLeft')go(story.index-1)});
  go(0);root.querySelector('.astra-close').focus();
}
const SLIDE_MS=6500;
function go(i){
  if(!story)return;if(i<0)i=0;if(i>=story.list.length){closeStory();return}
  story.index=i;story.elapsed=0;const slide=story.list[i],box=story.root.querySelector('.astra-slide');
  box.className='astra-slide s-'+slide.cls;box.innerHTML=slide.html;
  box.querySelectorAll('[data-story-play]').forEach(b=>b.onclick=playTop);
  box.querySelectorAll('[data-story-share]').forEach(b=>b.onclick=()=>perform(shareAstra));
  const count=box.querySelector('[data-count]');if(count&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const target=Number(count.dataset.count),t0=performance.now();const step=now=>{const k=Math.min(1,(now-t0)/1400);count.textContent=fmt(Math.round(target*(1-Math.pow(1-k,3))));if(k<1&&story)requestAnimationFrame(step)};requestAnimationFrame(step)}
  story.root.querySelectorAll('.astra-bars span').forEach((b,n)=>{b.classList.toggle('done',n<i);b.classList.toggle('now',n===i);b.querySelector('i').style.transform=n<i?'scaleX(1)':'scaleX(0)'});
  tick();
}
function tick(){
  if(!story)return;cancelAnimationFrame(story.timer);story.started=performance.now()-story.elapsed;
  const last=story.index===story.list.length-1;
  const frame=now=>{
    if(!story)return;if(story.held){story.timer=requestAnimationFrame(frame);story.started=now-story.elapsed;return}
    story.elapsed=now-story.started;const k=Math.min(1,story.elapsed/SLIDE_MS);
    const bar=story.root.querySelectorAll('.astra-bars i')[story.index];if(bar)bar.style.transform=`scaleX(${k})`;
    // The last story waits for the listener (share / play / close).
    if(k>=1){if(!last)go(story.index+1);return}
    story.timer=requestAnimationFrame(frame);
  };
  story.timer=requestAnimationFrame(frame);
}
function hold(on){if(story)story.held=on}
function closeStory(){if(!story)return;cancelAnimationFrame(story.timer);story.root.remove();story=null;document.body.classList.remove('astra-open');$('#astra-open')?.focus()}

// ---- Share image ------------------------------------------------------------
function wrapText(ctx,text,max){const words=String(text).split(' ');let line='';const lines=[];for(const w of words){const test=line?line+' '+w:w;if(ctx.measureText(test).width>max&&line){lines.push(line);line=w}else line=test}if(line)lines.push(line);return lines}
function fit(ctx,text,max){let t=String(text);if(ctx.measureText(t).width<=max)return t;while(t.length>1&&ctx.measureText(t+'…').width>max)t=t.slice(0,-1);return t+'…'}
async function astraImage(d){
  try{await Promise.all([document.fonts.load('700 80px Cinzel'),document.fonts.load('600 40px Inter')])}catch{}
  const c=document.createElement('canvas');c.width=1080;c.height=1920;const x=c.getContext('2d');
  const bg=x.createLinearGradient(0,0,0,1920);bg.addColorStop(0,'#0b0a14');bg.addColorStop(.55,'#101a3d');bg.addColorStop(1,'#07070a');x.fillStyle=bg;x.fillRect(0,0,1080,1920);
  // Seeded stars so the same summary always draws the same sky.
  let seed=d.minutes*7919+d.plays*104729+1;const rnd=()=>(seed=(seed*16807)%2147483647)/2147483647;
  for(let i=0;i<220;i++){const r=rnd()*2.4+.4;x.globalAlpha=.25+rnd()*.75;x.fillStyle=rnd()>.85?'#f0d77c':'#ffffff';x.beginPath();x.arc(rnd()*1080,rnd()*1920,r,0,Math.PI*2);x.fill()}
  x.globalAlpha=1;
  const glow=x.createRadialGradient(540,330,10,540,330,420);glow.addColorStop(0,'#d4af3755');glow.addColorStop(1,'#d4af3700');x.fillStyle=glow;x.fillRect(0,0,1080,800);
  const gold=x.createLinearGradient(200,0,880,0);gold.addColorStop(0,'#f6e08f');gold.addColorStop(.5,'#d4af37');gold.addColorStop(1,'#a8841f');
  x.textAlign='center';x.fillStyle='#9b97a6';x.font='600 34px Inter, sans-serif';x.fillText('IVOX-ASTRA · '+PERIODS[d.period].toUpperCase(),540,200);
  x.fillStyle=gold;x.font='700 132px Cinzel, Georgia, serif';x.fillText('Tu Astra',540,350);
  x.fillStyle='#f2ede0';x.font='700 190px Cinzel, Georgia, serif';x.fillText(fmt(d.minutes),540,610);
  x.fillStyle='#9b97a6';x.font='500 42px Inter, sans-serif';x.fillText('minutos de música',540,680);
  const s=sign(d.peak_hour);x.fillStyle=gold;x.font='700 54px Cinzel, Georgia, serif';x.fillText(s.name,540,790);
  x.strokeStyle='#d4af3766';x.lineWidth=2;x.beginPath();x.moveTo(140,850);x.lineTo(940,850);x.stroke();
  x.textAlign='left';
  const column=(title,rows,left)=>{x.fillStyle='#d4af37';x.font='700 34px Inter, sans-serif';x.fillText(title,left,930);rows.slice(0,5).forEach((r,i)=>{const y=1010+i*118;x.fillStyle='#f0d77c';x.font='700 44px Cinzel, Georgia, serif';x.fillText(String(i+1),left,y);x.fillStyle='#f2ede0';x.font='600 36px Inter, sans-serif';x.fillText(fit(x,r[0],360),left+56,y-8);x.fillStyle='#9b97a6';x.font='400 28px Inter, sans-serif';x.fillText(fit(x,r[1],360),left+56,y+32)})};
  column('ARTISTAS',d.top_artists.map(a=>[a.name,fmt(a.plays)+(a.plays===1?' reproducción':' reproducciones')]),110);
  column('CANCIONES',d.top_songs.map(v=>[v.title,v.artist]),570);
  x.textAlign='center';x.fillStyle='#f2ede0';x.font='600 40px Inter, sans-serif';
  x.fillText(`${fmt(d.songs)} canciones · ${fmt(d.artists)} artistas · racha de ${fmt(d.streak)} ${d.streak===1?'día':'días'}`,540,1660);
  x.fillStyle=gold;x.font='700 46px Cinzel, Georgia, serif';x.fillText('Ivox-Astra',540,1790);
  x.fillStyle='#9b97a6';x.font='400 28px Inter, sans-serif';x.fillText(location.host,540,1840);
  return new Promise((ok,fail)=>c.toBlob(b=>b?ok(b):fail(Error('No se pudo crear la imagen.')),'image/png'));
}
async function shareAstra(){
  const blob=await astraImage(data),file=new File([blob],'tu-astra.png',{type:'image/png'});
  const text=`Mi Astra ${PERIODS[data.period]}: ${fmt(data.minutes)} minutos de música en Ivox-Astra ✦`;
  if(navigator.canShare?.({files:[file]})){try{await navigator.share({files:[file],title:'Tu Astra',text});return}catch(e){if(e.name==='AbortError')return}}
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='tu-astra.png';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  notify('Imagen de Tu Astra guardada. Compártela en tus historias.');
}

// ---- Share a song -----------------------------------------------------------
window.shareSong=async function(v){
  if(!v||v.provider==='local'){notify('Tus archivos propios no se pueden compartir.');return}
  const url=location.origin+'/?song='+encodeURIComponent(v.id),title=v.title+(v.channel?' · '+v.channel:'');
  if(navigator.share){try{await navigator.share({title,text:'Escucha «'+v.title+'» en Ivox-Astra',url});return}catch(e){if(e.name==='AbortError')return}}
  try{await navigator.clipboard.writeText(url);notify('Enlace copiado: pégalo donde quieras.')}catch{openDialog(`<h2>Compartir canción</h2><p>${esc(title)}</p><label>Enlace<input id="share-url" readonly value="${esc(url)}"></label>`);$('#share-url').select()}
};
// A shared link (/?song=piped:ID) offers the song; playback still starts from a tap.
window.openSharedSong=async function(){
  const params=new URLSearchParams(location.search),id=params.get('song');if(!id)return;
  params.delete('song');history.replaceState(history.state,'',location.pathname+(params.toString()?'?'+params:''));
  if(!/^(piped|audius):[A-Za-z0-9_-]{1,64}$/.test(id))return;
  try{
    const {item}=await api('/api/online/item/'+encodeURIComponent(id).replace('%3A',':'));
    openDialog(`<div class="shared-song">${item.thumbnail?`<img src="${esc(bigArt(item.thumbnail))}" alt="" width="220" height="220">`:''}<p class="muted">Te compartieron una canción</p><h2>${esc(item.title)}</h2><p>${esc(item.channel||'')}</p><button id="shared-play" class="primary">▶ Reproducir</button></div>`);
    $('#shared-play').onclick=()=>{dialog.close();playList([item],0)};
  }catch(e){notify(e.message)}
};

// ---- Player chips: repeat, sleep timer, share ------------------------------
const REPEAT={off:['↻','Repetir'],all:['↻','Repetir cola'],one:['↻¹','Repetir canción']};
function drawRepeat(){const b=$('#sheet-repeat');if(!b)return;const m=IVoxRepeat.mode;b.setAttribute('aria-pressed',String(m!=='off'));b.querySelector('[aria-hidden]').textContent=REPEAT[m][0];b.querySelector('.chip-label').textContent=REPEAT[m][1]}
$('#sheet-repeat').onclick=()=>{const order=['off','all','one'];IVoxRepeat.mode=order[(order.indexOf(IVoxRepeat.mode)+1)%3];drawRepeat();notify({off:'Repetición desactivada.',all:'Se repetirá toda la cola.',one:'Se repetirá esta canción.'}[IVoxRepeat.mode])};
function drawSleep(){
  const b=$('#sheet-sleep');if(!b)return;const {until,endOfSong}=IVoxSleep.state,on=!!until||endOfSong;
  b.setAttribute('aria-pressed',String(on));
  b.querySelector('.chip-label').textContent=endOfSong?'Pausa al final':until?'Pausa en '+Math.max(1,Math.ceil((until-Date.now())/60000))+' min':'Temporizador';
}
$('#sheet-sleep').onclick=()=>{
  const {until,endOfSong}=IVoxSleep.state;
  openDialog(`<h2>Temporizador</h2><p class="muted">La música se pausará sola${appleMobile?'':' bajando el volumen poco a poco'}.</p><div class="option-list sleep-options">${[15,30,45,60,90].map(m=>`<button data-sleep="${m}">${m} minutos</button>`).join('')}<button data-sleep="end">Al terminar esta canción</button>${until||endOfSong?'<button data-sleep="off" class="danger">Desactivar temporizador</button>':''}</div>`);
  dialog.querySelectorAll('[data-sleep]').forEach(btn=>btn.onclick=()=>{const v=btn.dataset.sleep;IVoxSleep.set(v==='off'?0:v==='end'?'end':Number(v));dialog.close();notify(v==='off'?'Temporizador desactivado.':v==='end'?'La música se pausará al terminar esta canción.':`La música se pausará en ${v} minutos.`)});
};
IVoxSleep.onChange(drawSleep);setInterval(drawSleep,20000);
$('#sheet-share').onclick=()=>shareSong(audioQueue[audioIndex]);
drawRepeat();drawSleep();
})();
