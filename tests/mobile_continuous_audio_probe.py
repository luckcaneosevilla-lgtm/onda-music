"""Real worker + mobile browser fixture; no production/iPhone latency claims."""
import io,json,math,sys,threading,time,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
buffer=io.BytesIO()
with wave.open(buffer,'wb') as out:
    out.setparams((1,2,22050,0,'NONE','not compressed'))
    out.writeframes(b''.join(int(1000*math.sin(i*2*math.pi*440/22050)).to_bytes(2,'little',signed=True) for i in range(22050*20)))
audio=buffer.getvalue();ranges=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/api/audio/'):
            value=self.headers.get('Range');ranges.append(value)
            start,end=0,len(audio)-1
            if value:
                left,right=value.removeprefix('bytes=').split('-');start=int(left or 0);end=min(int(right) if right else end,end)
            if start>=512000:time.sleep(.3)
            data=audio[start:end+1];self.send_response(206 if value else 200);self.send_header('Content-Type','audio/wav');self.send_header('Accept-Ranges','bytes')
            if value:self.send_header('Content-Range',f'bytes {start}-{end}/{len(audio)}')
        else:
            name=self.path.split('?',1)[0].lstrip('/')
            if name in ('service-worker.js','boot.js','boot.css'):
                data=Path('web',name).read_bytes();self.send_response(200);self.send_header('Content-Type','text/css' if name.endswith('.css') else 'text/javascript')
            else:
                data=b'<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/boot.css"><style>body{background:#0a0a0c;color:#d4af37;font-family:system-ui}</style><a class="logo" href="#">IVOX</a><audio></audio><script src="/boot.js"></script>';self.send_response(200);self.send_header('Content-Type','text/html')
        self.send_header('Content-Length',str(len(data)));self.end_headers()
        try:self.wfile.write(data)
        except OSError:pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
source=Path('web/online.js').read_text(encoding='utf-8');block=source[source.index('const memoryAudio='):source.index('// Resolving a YouTube')]
try:
 with sync_playwright() as p:
    webkit='--webkit' in sys.argv
    browser=p.webkit.launch() if webkit else p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    profile='iPhone 13' if webkit else 'Pixel 7'
    page=browser.new_page(**p.devices[profile])
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(f'http://127.0.0.1:{server.server_port}/')
    page.evaluate("async()=>{await navigator.serviceWorker.register('/service-worker.js');await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}))}")
    page.evaluate("()=>{window.me={};window.cachedIds=new Set();window.onlineAudio=document.querySelector('audio');window.api=async()=>({url:'/api/audio/abcdefghijk',fallback_url:'/api/audio/abcdefghijk',mime:'audio/wav'});window.playbackWanted=true}")
    page.add_script_tag(content=block)
    page.evaluate("async()=>{await warmMemoryAudio({id:'piped:abcdefghijk',provider:'piped',provider_id:'abcdefghijk'});if(!memoryAudio.get('piped:abcdefghijk')?.streamUrl)throw Error('Worker prefix not ready')}")
    page.evaluate("""()=>{window.audioResult=new Promise((resolve,reject)=>{const a=onlineAudio,start=performance.now();let first=0,waiting=0,loads=0;a.addEventListener('loadstart',()=>loads++);a.addEventListener('waiting',()=>{if(first)waiting++});a.addEventListener('playing',()=>{if(!first)first=Math.round(performance.now()-start)});a.onerror=()=>reject(Error('media error '+a.error?.code));a.addEventListener('timeupdate',()=>{if(a.currentTime>=14){a.pause();resolve({play_to_playing_ms:first,waiting_after_playing:waiting,source_loads:loads,position:a.currentTime})}});a.src=memoryAudio.get('piped:abcdefghijk').streamUrl;const button=document.createElement('button');button.id='play-test';button.textContent='Play';button.onclick=()=>a.play().catch(reject);document.body.append(button);setTimeout(()=>reject(Error('Timeout')),25000)})}""")
    page.locator('#play-test').click()
    result=page.evaluate('()=>window.audioResult')
    assert result['source_loads']==1,result
    assert result['waiting_after_playing']==0,result
    for _ in range(5):page.locator('.logo').click()
    assert page.locator('#ivox-debug').is_visible()
    page.evaluate("()=>window.dispatchEvent(new CustomEvent('ivox-playback',{detail:{stage:'playing',version:1,elapsed_ms:123,url:'SECRET'}}))")
    assert '123 ms' in page.locator('.debug-summary').inner_text()
    assert 'SECRET' not in page.locator('#ivox-debug pre').inner_text()
    page.screenshot(path='test-results/audio-mobile-debug.png')
    splash=browser.new_page(**p.devices[profile])
    markup=Path('web/index.html').read_text(encoding='utf-8').split('<body>',1)[1].split('<noscript>',1)[0]
    splash.set_content('<meta name="viewport" content="width=device-width,initial-scale=1">'+markup)
    splash.add_style_tag(content=Path('web/boot.css').read_text())
    splash.screenshot(path='test-results/audio-mobile-splash.png')
    splash.emulate_media(reduced_motion='reduce')
    assert splash.locator('.horus-eye').evaluate("element=>getComputedStyle(element).animationName")=='none'
    splash.add_script_tag(content=Path('web/boot.js').read_text(encoding='utf-8'))
    splash.wait_for_selector('#ivox-splash',state='detached')
    splash.close()
    assert not errors,errors
    print(json.dumps({'profile':profile+(' WebKit' if webkit else ' Chromium'),'fixture':'20s WAV, real service worker, 300ms tail latency','result':result,'upstream_ranges':ranges,'debug_five_taps':'PASS','no_signed_url_in_logs':'PASS'}))
    browser.close()
finally:server.shutdown()
