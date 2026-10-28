// Crossfade: near the end of a song its last seconds are played by a private second
// <audio> while the shared element moves on to the next song, so queue, radio,
// recovery and Media Session keep working unchanged. Volumes follow equal-power
// curves. iOS ignores audio.volume, so there songs simply chain without a gap.
const crossfade=(()=>{
  const tail=new Audio(),controls=window.IVoxPlayerControls,choices=[0,3,6,10,12],supported=!isIOS;
  tail.preload='auto';
  let seconds=(()=>{try{const raw=localStorage.getItem('ivox-crossfade'),v=Number(raw);return raw!==null&&choices.includes(v)?v:6}catch{return 6}})();
  let armed=null,fade=null,timer=0;
  const playing=()=>!onlineAudio.paused&&$('#online-player').dataset.state==='playing';
  function clearTail(){tail.pause();tail.removeAttribute('src');tail.load()}
  function disarm(){if(armed&&!fade)clearTail();armed=null;if(!fade)stopTimer()}
  function startTimer(){if(!timer)timer=setInterval(tick,50)}
  function stopTimer(){clearInterval(timer);timer=0}
  // Restores the listener's volume whatever happened (skip, pause, failure).
  function finish(){
    if(!fade)return;const base=fade.base;fade=null;controls.fading=false;stopTimer();clearTail();
    onlineAudio.volume=base;
    const hold=window.crossfadeHold;window.crossfadeHold=null;if(hold&&hold.startsWith('blob:')&&hold!==onlineAudio.getAttribute('src'))URL.revokeObjectURL(hold);
  }
  function check(){
    if(fade)return;
    const src=onlineAudio.getAttribute('src'),total=trackDuration(),t=onlineAudio.currentTime;
    if(!supported||!seconds||!playing()||!src||src==='/silence.wav'||!(total>seconds*2+8)||audioIndex>=audioQueue.length-1||onlineAudio.muted){disarm();return}
    const remaining=total-t;
    if(armed&&armed.version!==playVersion){disarm();return}
    // A few seconds early the tail element starts buffering the song's ending.
    if(!armed&&remaining<=seconds+5&&remaining>seconds+.5){
      armed={version:playVersion,at:total-seconds};
      tail.src=onlineAudio.currentSrc;tail.volume=0;tail.currentTime=armed.at;startTimer();
    }
    if(armed&&t>=armed.at-.05)begin();
  }
  function begin(){
    const ready=armed;armed=null;
    if(tail.readyState<3){clearTail();stopTimer();return}  // not buffered: a normal transition at the end
    const t=onlineAudio.currentTime;if(Math.abs(tail.currentTime-t)>.08)tail.currentTime=t;
    tail.play().then(()=>{
      if(ready.version!==playVersion||!playing()){clearTail();stopTimer();return}
      const base=onlineAudio.volume;
      // Same position on both elements: swap which one is audible, then move on.
      tail.volume=base;onlineAudio.volume=0;controls.fading=true;window.crossfadeHold=onlineAudio.getAttribute('src');
      fade={base,start:performance.now(),incoming:0,version:0};
      advance();fade.version=playVersion;startTimer();
    },()=>{clearTail();stopTimer()});
  }
  function tick(){
    if(!fade){check();return}
    // The listener skipped, stopped or paused during the fade: end it now.
    if(fade.version!==playVersion||(!playbackWanted&&$('#online-player').dataset.state!=='loading')){finish();return}
    const now=performance.now(),span=seconds*1000,out=Math.min(1,(now-fade.start)/span);
    tail.volume=fade.base*Math.cos(out*Math.PI/2);
    if(!fade.incoming&&playing())fade.incoming=now;
    const into=fade.incoming?Math.min(1,(now-fade.incoming)/span):0;
    onlineAudio.volume=fade.base*Math.sin(into*Math.PI/2);
    // The next song may fail or load slowly: never stay faded for long.
    if((out>=1&&into>=1)||(out>=1&&now-fade.start>span+15000)||$('#online-player').dataset.state==='error')finish();
  }
  onlineAudio.addEventListener('timeupdate',check);
  onlineAudio.addEventListener('seeking',()=>{if(!fade)disarm()});
  onlineAudio.addEventListener('pause',()=>{if(!fade)disarm()});
  function label(){
    const chip=$('#sheet-crossfade');if(!chip)return;chip.hidden=!supported;
    chip.setAttribute('aria-pressed',String(seconds>0));chip.querySelector('.chip-label').textContent=seconds?`Transición ${seconds} s`:'Sin transición';
  }
  $('#sheet-crossfade').onclick=()=>{
    seconds=choices[(choices.indexOf(seconds)+1)%choices.length];try{localStorage.setItem('ivox-crossfade',String(seconds))}catch{}
    disarm();label();notify(seconds?`Transición de ${seconds} segundos entre canciones.`:'Transición desactivada: las canciones se suceden sin mezcla.');
  };
  label();
  return {get seconds(){return seconds},get active(){return !!fade}};
})();
