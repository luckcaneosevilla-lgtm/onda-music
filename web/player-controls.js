/* Persistent HTML5 controls. No provider URLs or queue state live here. */
(() => {
  'use strict';
  const player = document.querySelector('#online-player');
  const audio = document.querySelector('#online-audio');
  const range = document.querySelector('#np-range');
  const fill = document.querySelector('#np-fill');
  const buffer = document.querySelector('#np-buffer');
  const current = document.querySelector('#np-current');
  const duration = document.querySelector('#np-duration');
  const clock = n => Number.isFinite(n) ? `${Math.floor(Math.max(0,n)/60)}:${String(Math.floor(Math.max(0,n)%60)).padStart(2,'0')}` : '0:00';
  const clamp = (n,a,b) => Math.min(b,Math.max(a,n));
  // Optional IVoxPlayerControls.duration(): the player may know a better length than the
  // element (Safari can report twice the real one); without it, the element's duration.
  const length = () => { const provider = window.IVoxPlayerControls?.duration; const value = provider ? provider() : audio.duration; return Number.isFinite(value) && value > 0 ? value : audio.duration; };
  let dragging = false, dragSource = '', dragVersion = 0, version = 0;
  function paint(time,total) {
    const ratio = total > 0 ? clamp(time/total,0,1) : 0;
    fill.style.width = `${ratio*100}%`;
    current.textContent = clock(time);
    range.setAttribute('aria-valuetext',`${clock(time)} de ${clock(total)}`);
  }
  function update(time=audio.currentTime,total=length()) {
    const valid = Number.isFinite(total) && total > 0;
    duration.textContent = clock(total);
    range.disabled = !valid || !audio.seekable.length;
    if (!dragging) {
      paint(valid ? time : 0,valid ? total : 0);
      range.value = valid ? Math.round(clamp(time/total,0,1)*1000) : 0;
    }
    const fragments = document.createDocumentFragment();
    if (valid) {
      const buffered = audio.buffered;
      for (let i=0;i<buffered.length;i++) {
        const start=clamp(buffered.start(i),0,total), end=clamp(buffered.end(i),0,total);
        if (end <= start) continue;
        const segment=document.createElement('span');
        segment.style.left=`${start/total*100}%`;
        segment.style.width=`${(end-start)/total*100}%`;
        fragments.append(segment);
      }
    }
    buffer.replaceChildren(fragments);
  }
  function cancelSeek() { dragging=false; version++; update(); }
  function beginSeek() {
    if (range.disabled || dragging) return;
    dragging=true; dragSource=audio.getAttribute('src'); dragVersion=version;
  }
  function seek(time) {
    const total=length(), ranges=audio.seekable;
    if (!Number.isFinite(time) || !Number.isFinite(total) || total<=0 || !ranges.length) return false;
    time=clamp(time,0,total);
    let nearest=null, distance=Infinity;
    for(let i=0;i<ranges.length;i++) {
      const start=clamp(ranges.start(i),0,total),end=clamp(ranges.end(i),0,total);
      if(end<start) continue;
      const candidate=clamp(time,start,end), delta=Math.abs(time-candidate);
      if(delta<distance){nearest=candidate;distance=delta;}
    }
    if(nearest===null) return false;
    try { audio.currentTime=nearest; return true; } catch { return false; }
  }
  range.addEventListener('pointerdown',beginSeek);
  range.addEventListener('input',() => {
    beginSeek();
    if(dragging) paint(Number(range.value)/1000*length(),length());
  });
  range.addEventListener('change',() => {
    const valid=dragging && dragVersion===version && dragSource===audio.getAttribute('src');
    const target=Number(range.value)/1000*length();
    dragging=false;
    if(valid) seek(target);
    update();
  });
  range.addEventListener('pointercancel',cancelSeek);
  range.addEventListener('blur',cancelSeek);
  range.addEventListener('pointerup',() => {
    // Native change is dispatched on release; a tap with no value change needs cleanup too.
    const releasedVersion=version;
    setTimeout(() => {if(dragging && version===releasedVersion) cancelSeek();},0);
  });
  for(const event of ['loadedmetadata','durationchange','timeupdate','progress','seeked','canplay']) audio.addEventListener(event,() => update());
  audio.addEventListener('emptied',cancelSeek);
  window.IVoxPlayerControls={update,cancelSeek,seek,duration:null};

  const toggle=document.querySelector('#np-volume-toggle');
  const panel=document.querySelector('#np-volume-panel');
  const volume=document.querySelector('#np-volume-range');
  const mute=document.querySelector('#np-mute');
  const note=document.querySelector('#np-volume-note');
  function closeVolume(){panel.hidden=true;toggle.setAttribute('aria-expanded','false');}
  toggle.onclick=() => {panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));};
  function syncVolume() {
    volume.value=Math.round(audio.volume*100);
    volume.setAttribute('aria-valuetext',`${volume.value}%`);
    const silent=audio.muted || audio.volume===0;
    mute.setAttribute('aria-pressed',String(silent));
    mute.textContent=silent?'Activar sonido':'Silenciar';
    toggle.setAttribute('aria-label',silent?'Volumen: silenciado':'Volumen');
    toggle.classList.toggle('is-muted',silent);
  }
  volume.addEventListener('input',() => {
    const requested=Number(volume.value)/100;
    try {audio.volume=requested;if(requested>0) audio.muted=false;} catch {note.hidden=false;syncVolume();return;}
    // Some mobile browsers leave volume under hardware control.
    note.hidden=Math.abs(audio.volume-requested)<0.015;
    syncVolume();
  });
  mute.onclick=() => {audio.muted=!(audio.muted || audio.volume===0);if(!audio.muted && audio.volume===0) audio.volume=1;syncVolume();};
  audio.addEventListener('volumechange',syncVolume);
  document.addEventListener('pointerdown',event => {if(!event.target.closest('.np-volume')) closeVolume();});
  document.addEventListener('keydown',event => {if(event.key==='Escape' && !panel.hidden){closeVolume();toggle.focus();}});
  syncVolume();update();

  // Swipes belong to the non-interactive row, never the seek/volume/queue controls.
  const row=player.querySelector('.np-row');
  let swipe=null, suppressClickUntil=0;
  row.addEventListener('pointerdown',event => {
    if(event.pointerType!=='touch' || !event.isPrimary || event.target.closest('button,input,a,.np-volume')) {swipe=null;return;}
    swipe={id:event.pointerId,x:event.clientX,y:event.clientY,time:performance.now()};
  });
  row.addEventListener('pointerup',event => {
    if(!swipe || swipe.id!==event.pointerId) return;
    const dx=event.clientX-swipe.x,dy=event.clientY-swipe.y,elapsed=performance.now()-swipe.time;
    swipe=null;
    if(Math.abs(dx)<60 || Math.abs(dx)<Math.abs(dy)*1.8 || elapsed>1000) return;
    suppressClickUntil=performance.now()+500;
    const button=document.querySelector(dx<0?'#online-next':'#online-previous');
    if(!button.disabled) button.click();
  });
  for(const event of ['pointercancel','lostpointercapture']) row.addEventListener(event,() => {swipe=null;});
  player.addEventListener('click',event => {
    if(event.isTrusted && performance.now()<suppressClickUntil){event.preventDefault();event.stopPropagation();}
  },true);
})();
