const CACHE = 'ivox-app-shell-v23';
const SHELL = ['/', '/index.html', '/style.css', '/online.css', '/player.css', '/sheet.css',
  '/app.js', '/player-controls.js', '/online.js', '/sheet.js', '/home.js', '/offline.js', '/crossfade.js', '/pwa.js', '/manifest.json', '/icon-192x192.png', '/icon-512x512.png', '/silence.wav',
  '/fonts/cinzel.woff2', '/fonts/inter.woff2', '/release.css', '/release.js'];
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
self.addEventListener('message',event=>{
  if(event.data?.type==='cache-played-audio'&&/^[A-Za-z0-9_-]{11}$/.test(event.data.id))event.waitUntil(savePlayed(event.data.id));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  const audio=url.origin===self.location.origin&&audioPath.exec(url.pathname);
  if(request.method==='GET'&&audio&&!url.search){
    event.respondWith(cachedAudio(request,audio[1]).then(hit=>hit||fetch(request)));return;
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
