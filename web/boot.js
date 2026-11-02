// Local diagnostics: bounded records, no signed URLs, credentials or uploads.
(() => {
  const records=[];let taps=0,lastTap=0,panel,output,summary;
  function render(){if(!panel||panel.hidden)return;output.textContent=records.map(row=>`[IVOX playback] ${JSON.stringify(row)}`).join('\n');output.scrollTop=output.scrollHeight;const last=[...records].reverse().find(row=>row.stage==='playing');summary.textContent=last?`Play → playing: ${last.elapsed_ms} ms · intento ${last.version}`:'Pulsa Play para medir el arranque.'}
  function show(){
    if(!panel){panel=document.createElement('aside');panel.id='ivox-debug';panel.hidden=true;panel.setAttribute('aria-label','Diagnóstico de audio IVOX');panel.innerHTML='<header><strong>Audio · diagnóstico local</strong><button data-debug="copy">Copiar</button><button data-debug="clear">Limpiar</button><button data-debug="close" aria-label="Cerrar diagnóstico">Cerrar</button></header><p class="debug-summary"></p><p>Tiempo hasta el evento playing; no mide la salida del altavoz. Sin validación de iPhone todavía.</p><pre tabindex="0" aria-label="Eventos de reproducción"></pre>';document.body.append(panel);output=panel.querySelector('pre');summary=panel.querySelector('.debug-summary');panel.querySelector('[data-debug="close"]').onclick=()=>panel.hidden=true;panel.querySelector('[data-debug="clear"]').onclick=()=>{records.length=0;render()};panel.querySelector('[data-debug="copy"]').onclick=async event=>{try{await navigator.clipboard.writeText(output.textContent);event.target.textContent='Copiado'}catch{event.target.textContent='Selecciona los logs'}}}
    panel.hidden=!panel.hidden;render();
  }
  window.addEventListener('ivox-playback',event=>{
    const source=event.detail,row={};for(const key of ['stage','version','elapsed_ms','readyState','buffer_seconds','engine','ttfb_ms','request_after_play_ms','response_after_play_ms'])if(typeof source?.[key]==='number'||typeof source?.[key]==='string')row[key]=source[key];records.push(row);if(records.length>150)records.shift();render();
  });
  document.addEventListener('click',event=>{if(!event.target.closest('.logo'))return;const now=performance.now();taps=now-lastTap<1800?taps+1:1;lastTap=now;if(taps===5){event.preventDefault();event.stopImmediatePropagation();taps=0;show()}},true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&panel)panel.hidden=true});
  const dismiss=()=>{const splash=document.querySelector('#ivox-splash');if(!splash)return;splash.classList.add('is-ready');setTimeout(()=>splash.remove(),400)};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',dismiss,{once:true});else dismiss();
})();
