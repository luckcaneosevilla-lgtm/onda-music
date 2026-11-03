const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/online.js','utf8');
const body=source.slice(source.indexOf('async function downloadOnline('),source.indexOf('\nwindow.cancelOnlineSearch'));
const track={provider:'piped',provider_id:'abcdefghijk',title:'Canción / prueba',channel:'Artista'};
function fixture(ios=true){
  const button={},calls={shares:[],links:[],dialogs:[],notices:[]};
  const context={File,URL,navigator:{userAgent:ios?'iPhone':'Chrome',canShare:()=>true,share:async data=>calls.shares.push(data)},
    fetch:async()=>new Response(new Blob(['audio bytes'],{type:'audio/mp4'})),
    notify:message=>calls.notices.push(message),openDialog:html=>calls.dialogs.push(html),$:()=>button,
    dialog:{close(){calls.closed=true}},document:{createElement(){const link={click(){calls.links.push(this)},remove(){}};return link},body:{append(){}}}};
  vm.createContext(context);vm.runInContext(body,context);return {context,calls,button};
}
(async()=>{
  const {context,calls,button}=fixture();
  await context.downloadOnline(track);
  assert.equal(calls.shares.length,0,'Fetching must not trigger share after user activation expires');
  assert.equal(calls.links.length,0,'iPhone must not navigate to the audio endpoint');
  await button.onclick();
  const file=calls.shares[0].files[0];
  assert.equal(file.name,'Artista - Canción   prueba.m4a');
  assert.equal(file.type,'audio/mp4');assert.equal(await file.text(),'audio bytes');
  assert.equal(calls.closed,true);
  for(const response of [new Response('{}',{status:502,headers:{'Content-Type':'application/json'}}),new Response('{}',{headers:{'Content-Type':'application/json'}}),new Response('',{headers:{'Content-Type':'audio/mp4'}}),new Response('partial',{status:206,headers:{'Content-Type':'audio/mp4'}})]){
    const f=fixture();f.context.fetch=async()=>response;
    await assert.rejects(f.context.downloadOnline(track));assert.equal(f.calls.dialogs.length,0);
  }
  const cancel=fixture();await cancel.context.downloadOnline(track);
  cancel.context.navigator.share=async()=>{throw {name:'AbortError'}};
  await cancel.button.onclick();assert.equal(cancel.button.disabled,false);assert.equal(cancel.calls.closed,undefined);
  const desktop=fixture(false);desktop.context.fetch=()=>{throw Error('Unexpected full fetch')};
  await desktop.context.downloadOnline(track);assert.match(desktop.calls.links[0].href,/download=/);
  console.log('PASS: iPhone file preparation, explicit share gesture, audio validation, cancellation, desktop download');
})().catch(error=>{console.error(error);process.exitCode=1});
