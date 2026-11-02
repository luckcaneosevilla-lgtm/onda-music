"""AAC/MP4 A/B: previous prefix worker vs native proxy. Synthetic, not iPhone hardware."""
import http.client,json,subprocess,sys,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import imageio_ffmpeg
from playwright.sync_api import sync_playwright
baseline=subprocess.run(['git','show','bcb1895:web/service-worker.js'],capture_output=True,check=True).stdout
with tempfile.TemporaryDirectory() as temp:
 fixture=Path(temp)/'tone.m4a'
 subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','sine=frequency=440:duration=40','-c:a','aac','-profile:a','aac_low','-b:a','128k','-movflags','+faststart',str(fixture)],check=True)
 audio=fixture.read_bytes()
 assert audio.find(b'moov')<audio.find(b'mdat'),'fixture must have early metadata'
 ranges=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   if self.path.startswith('/api/audio/'):
    value=self.headers.get('Range');ranges.append(value);start,end=0,len(audio)-1
    if value:
     left,right=value.removeprefix('bytes=').split('-');start=int(left) if left else max(0,len(audio)-int(right));end=min(int(right),end) if left and right else end
    time.sleep(.2);body=audio[start:end+1];self.send_response(206 if value else 200);self.send_header('Content-Type','audio/mp4');self.send_header('Accept-Ranges','bytes')
    if value:self.send_header('Content-Range',f'bytes {start}-{end}/{len(audio)}')
   elif self.path.startswith('/service-worker.js'):
    body=baseline if 'baseline' in self.path else Path('web/service-worker.js').read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript')
   else:
    body=b'<meta name="viewport" content="width=device-width,initial-scale=1"><audio preload="auto" playsinline></audio><button>Play</button>';self.send_response(200);self.send_header('Content-Type','text/html')
   self.send_header('Content-Length',str(len(body)));self.end_headers()
   try:
    for offset in range(0,len(body),8192):self.wfile.write(body[offset:offset+8192]);self.wfile.flush()
   except OSError:pass
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   webkit='--webkit' in sys.argv;browser=p.webkit.launch() if webkit else p.chromium.launch();results=[]
   for mode in ['native','prefix','prefix','native']:
    context=browser.new_context(**p.devices['iPhone 13']);page=context.new_page();page.goto(f'http://127.0.0.1:{server.server_port}/')
    page.evaluate("async mode=>{await navigator.serviceWorker.register('/service-worker.js'+(mode==='prefix'?'?baseline=1':''));await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));}",mode)
    codec=page.evaluate('()=>document.querySelector("audio").canPlayType(\'audio/mp4; codecs="mp4a.40.2"\')')
    if mode=='prefix':
     page.evaluate("""async()=>{const response=await fetch('/api/audio/abcdefghijk?prefix=1',{headers:{Range:'bytes=0-511999'}});const blob=await response.blob();const channel=new MessageChannel();const ready=new Promise(resolve=>channel.port1.onmessage=event=>resolve(event.data.ready));navigator.serviceWorker.controller.postMessage({type:'audio-prefix',id:'abcdefghijk',blob,total:Number(response.headers.get('Content-Range').split('/')[1]),mime:'audio/mp4',url:'/api/audio/abcdefghijk'},[channel.port2]);if(!await ready)throw Error('prefix registration failed')}""")
    page.evaluate("""mode=>{window.result=new Promise(resolve=>{const a=document.querySelector('audio'),marks={};let t;for(const name of ['loadedmetadata','loadeddata','playing','error'])a.addEventListener(name,()=>{marks[name]=Math.round(performance.now()-t);if(name==='playing'||name==='error')resolve({marks,error:a.error?.code||null})},{once:true});document.querySelector('button').onclick=()=>{t=performance.now();a.src='/api/audio/abcdefghijk?'+(mode==='native'?'native=1':'memory=1');marks.play_called=Math.round(performance.now()-t);a.play().catch(error=>resolve({marks,error:error.name}));setTimeout(()=>resolve({marks,error:'timeout'}),12000)}})}""",mode)
    page.locator('button').click();result=page.evaluate('()=>window.result');results.append({'mode':mode,'codec':codec,**result});context.close()
   print(json.dumps({'engine':'WebKit' if webkit else 'Chromium','profile':'iPhone 13 emulated on Windows','fixture':'AAC-LC in faststart MP4, 200ms upstream delay','results':results}))
   browser.close()
 finally:server.shutdown()
