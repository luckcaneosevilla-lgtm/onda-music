const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const entries=new Map();let fetches=0;
const cache={match:async key=>entries.get(key)?.clone(),keys:async()=>[...entries.keys()],put:async(key,value)=>entries.set(key,value)};
const context={URL,Request,Response,Blob,AbortSignal,Map,Set,Promise,console,caches:{open:async()=>cache},fetch:async()=>{fetches++;return new Response(new Uint8Array(20000).fill(7),{headers:{'Content-Type':'audio/mpeg'}})},self:{location:{origin:'https://ivox.test'},addEventListener(){},clients:{matchAll:async()=>[]}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/service-worker.js','utf8'),context);
(async()=>{
 await vm.runInContext("savePlayed('abcdefghijk')",context);
 assert.equal(fetches,1);assert.equal(entries.size,1);
 await vm.runInContext("savePlayed('abcdefghijk')",context);assert.equal(fetches,1);
 context.request=new Request('https://ivox.test/api/audio/abcdefghijk',{headers:{Range:'bytes=10-19'}});
 const response=await vm.runInContext("cachedAudio(request,'abcdefghijk')",context);
 assert.equal(response.status,206);assert.equal(response.headers.get('Content-Range'),'bytes 10-19/20000');assert.equal((await response.arrayBuffer()).byteLength,10);
 context.request=new Request('https://ivox.test/api/audio/abcdefghijk',{headers:{Range:'bytes=-5'}});
 assert.equal((await vm.runInContext("cachedAudio(request,'abcdefghijk')",context)).headers.get('Content-Length'),'5');
 context.request=new Request('https://ivox.test/api/audio/abcdefghijk',{headers:{Range:'bytes=30000-'}});
 assert.equal((await vm.runInContext("cachedAudio(request,'abcdefghijk')",context)).status,416);
 console.log('PASS: full audio persistence, cache hit without network, byte ranges, suffix ranges, 416');
})().catch(error=>{console.error(error);process.exitCode=1});
