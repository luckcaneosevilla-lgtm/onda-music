const CACHE = 'ivox-app-shell-v28';
const SHELL = ['/', '/index.html', '/style.css', '/online.css', '/player.css', '/sheet.css',
  '/app.js', '/player-controls.js', '/online.js', '/sheet.js', '/home.js', '/offline.js', '/crossfade.js', '/pwa.js', '/manifest.json', '/icon-192x192.png', '/icon-512x512.png', '/silence.wav',
  '/fonts/cinzel.woff2', '/fonts/inter.woff2', '/release.css', '/release.js', '/boot.css', '/boot.js'];
const ASSETS = new Set(SHELL);
const PAGES = new Set(['/', '/index.html', '/library', '/plan', '/files', '/privacy']);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(
    SHELL.map(path => new Request(path, {cache: 'reload'}))
  )).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => /^(onda|ivox)-app-shell-/.test(key) && key !== CACHE).map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});
const AUDIO_CACHE='ivox-audio-v1', filling=new Map();
const audioPath=/^\/api\/audio\/([A-Za-z0-9_-]{11})$/;
async function cachedAudio(request,id){
  const cache=await caches.open(AUDIO_CACHE),key='/ivox-audio/'+encodeURIComponent('piped:'+id);
  const hit=await cache.match(key);if(!hit)return null;
  const range=request.headers.get('Range');if(!range)return hit;
  const match=/^bytes=(\d*)-(\d*)$/.exec(range);if(!match)return null;
  const blob=await hit.blob(),size=blob.size;
  const start=match[1]?Number(match[1]):Math.max(0,size-Number(match[2]));
  const end=match[1]&&match[2]?Math.min(Number(match[2]),size-1):size-1;
  if(start>=size||end<start)return new Response(null,{status:416,headers:{'Content-Range':`bytes */${size}`}});
  return new Response(blob.slice(start,end+1),{status:206,headers:{'Content-Type':blob.type,'Content-Length':String(end-start+1),'Content-Range':`bytes ${start}-${end}/${size}`,'Accept-Ranges':'bytes'}});
}
async function savePlayed(id){
  if(filling.has(id))return filling.get(id);
  const task=(async()=>{
    const cache=await caches.open(AUDIO_CACHE),key='/ivox-audio/'+encodeURIComponent('piped:'+id);
    if(await cache.match(key)||(await cache.keys()).length>=40)return;
    const response=await fetch('/api/audio/'+id,{signal:AbortSignal.timeout(120000)});
    if(response.status!==200||!response.headers.get('Content-Type')?.startsWith('audio/'))return;
    const blob=await response.blob();if(blob.size<10000||blob.size>32*1024*1024)return;
    await cache.put(key,new Response(blob,{headers:{'Content-Type':blob.type,'Content-Length':String(blob.size)}}));
    // Automatic fills stop at 40 entries; never evict explicit offline downloads.
    for(const client of await self.clients.matchAll())client.postMessage({type:'audio-cached',id:'piped:'+id});
  })().catch(()=>{}).finally(()=>filling.delete(id));
  filling.set(id,task);return task;
}
// Prefixes belong to a controlled client, not to an account shared across tabs.
const audioPrefixes=new Map();
function prefixKey(client,id){return client+':'+id}
function rememberPrefix(client,data){
  const {id,blob,total,mime}=data;
  if(!/^[A-Za-z0-9_-]{11}$/.test(id)||!(blob instanceof Blob)||blob.size>512000||blob.size<10000||!Number.isSafeInteger(total)||total<blob.size||!/^audio\//.test(mime))return false;
  const url=new URL(data.url,self.location.origin);
  if(!(url.origin===self.location.origin&&url.pathname==='/api/audio/'+id)&&!(url.protocol==='https:'&&url.hostname.endsWith('.googlevideo.com')))return false;
  for(const [key,entry] of audioPrefixes)if(entry.expires<Date.now())audioPrefixes.delete(key);
  while(audioPrefixes.size>=12)audioPrefixes.delete(audioPrefixes.keys().next().value);
  audioPrefixes.set(prefixKey(client,id),{blob,total,mime,url:url.href,expires:Date.now()+60000});return true;
}
function prefixResponse(request,client,id){
  const entry=audioPrefixes.get(prefixKey(client,id));if(!entry||entry.expires<Date.now())return null;
  const range=request.headers.get('Range'),match=range&&/^bytes=(\d*)-(\d*)$/.exec(range);
  if(range&&(!match||(!match[1]&&!match[2])))return null;
  const start=match?(match[1]?Number(match[1]):Math.max(0,entry.total-Number(match[2]))):0;
  const end=match&&match[1]&&match[2]?Math.min(Number(match[2]),entry.total-1):entry.total-1;
  if(start>=entry.total||end<start)return new Response(null,{status:416,headers:{'Content-Range':`bytes */${entry.total}`}});
  const abort=new AbortController();let reader,position=start;
  const body=new ReadableStream({
    async pull(controller){
      try{
        if(position>end){controller.close();return}
        if(position<entry.blob.size){const stop=Math.min(end+1,entry.blob.size);controller.enqueue(new Uint8Array(await entry.blob.slice(position,stop).arrayBuffer()));position=stop;return}
        if(!reader){
          const response=await fetch(entry.url,{headers:{Range:`bytes=${position}-${end}`},signal:abort.signal});
          // Do not splice a different representation or an ignored Range into audio.
          if(response.status!==206||response.headers.get('Content-Range')!==`bytes ${position}-${end}/${entry.total}`||response.headers.get('Content-Type')?.split(';')[0]!==entry.mime.split(';')[0])throw Error('audio_representation_changed');
          reader=response.body.getReader();
        }
        const {value,done}=await reader.read();
        if(done){if(position!==end+1)throw Error('audio_truncated');controller.close();return}
        if(position+value.length>end+1)throw Error('audio_range_overflow');
        position+=value.length;controller.enqueue(value);
      }catch(error){abort.abort();controller.error(error)}
    },
    cancel(){abort.abort();return reader?.cancel()}
  });
  const headers={'Content-Type':entry.mime,'Content-Length':String(end-start+1),'Accept-Ranges':'bytes','Cache-Control':'no-store'};
  if(range)headers['Content-Range']=`bytes ${start}-${end}/${entry.total}`;
  return new Response(body,{status:range?206:200,headers});
}
self.addEventListener('message',event=>{
  if(event.data?.type==='audio-prefix'&&event.source?.id)event.ports[0]?.postMessage({ready:rememberPrefix(event.source.id,event.data)});
  if(event.data?.type==='clear-audio-prefixes')for(const key of audioPrefixes.keys())if(key.startsWith(event.source?.id+':'))audioPrefixes.delete(key);
  if(event.data?.type==='cache-played-audio'&&event.data.native!==true&&/^[A-Za-z0-9_-]{11}$/.test(event.data.id))event.waitUntil(savePlayed(event.data.id));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  const audio=url.origin===self.location.origin&&audioPath.exec(url.pathname);
  // No respondWith: let Safari's native media stack issue its own Range probes.
  // The explicit flag also works while an older app shell is controlling this tab.
  const ua=self.navigator?.userAgent||'';
  const apple=/iPhone|iPad|iPod|CriOS|FxiOS|EdgiOS|OPiOS/i.test(ua)||(/Safari\//.test(ua)&&!/Chrome\/|Chromium\/|Edg\/|OPR\/|Android/.test(ua));
  if(audio&&(apple||url.searchParams.get('native')==='1'))return;
  if(request.method==='GET'&&audio&&(!url.search||url.search==='?memory=1')){
    event.respondWith((async()=>{const hit=await cachedAudio(request,audio[1]);if(hit)return hit;
      if(url.search==='?memory=1'){const response=prefixResponse(request,event.clientId,audio[1]);if(response)return response;return fetch(new Request(url.origin+url.pathname,request))}
      return fetch(request);
    })());return;
  }
  // Never intercept accounts, APIs, audio, third parties or the protected admin page.
  if (request.method !== 'GET' || url.origin !== self.location.origin ||
      request.headers.has('Range') || url.pathname.startsWith('/api/') ||
      url.pathname === '/admin') return;
  if (request.mode === 'navigate' && PAGES.has(url.pathname)) {
    event.respondWith(fetch(request).catch(async () => (await caches.match('/')) || Response.error()));
    return;
  }
  if (!ASSETS.has(url.pathname) || url.search) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(request);
      if (response.ok && response.type === 'basic') await cache.put(url.pathname, response.clone());
      return response;
    } catch {
      return (await cache.match(url.pathname)) || Response.error();
    }
  })());
});
