"""Real AAC partial fast-start validation, decode equivalence and native playback."""
import http.client,json,subprocess,sys,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audio_prepared import PreparedAudioCache,validate_faststart
from audio_playback import NativePlayback
from server import Handler
ff=imageio_ffmpeg.get_ffmpeg_exe();VID='abcdefghijk'
with tempfile.TemporaryDirectory() as temp:
 original=Path(temp)/'original.mp4';virtual=Path(temp)/'virtual.mp4'
 subprocess.run([ff,'-v','error','-f','lavfi','-i','sine=frequency=440:duration=40','-c:a','aac','-profile:a','aac_low','-b:a','128k',str(original)],check=True)
 raw=original.read_bytes();requests=[]
 class Origin(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   start,end=map(int,self.headers['Range'][6:].split('-'));end=min(end,len(raw)-1);requests.append((start,end));time.sleep(.08)
   self.send_response(206);self.send_header('Content-Type','audio/mp4');self.send_header('ETag','fixture-one');self.send_header('Content-Length',str(end-start+1));self.send_header('Content-Range',f'bytes {start}-{end}/{len(raw)}');self.end_headers()
   try:self.wfile.write(raw[start:end+1])
   except OSError:pass
 origin=ThreadingHTTPServer(('127.0.0.1',0),Origin);threading.Thread(target=origin.serve_forever,daemon=True).start()
 def opener(url,headers,timeout=3):
  conn=http.client.HTTPConnection('127.0.0.1',origin.server_port,timeout=timeout);conn.request('GET','/file',headers=headers);return conn,conn.getresponse()
 cache=PreparedAudioCache(lambda *a:{'url':'https://r1.googlevideo.com/file?mime=audio%2Fmp4','mime':'audio/mp4'},partial=True,job_timeout=8,prefix_bytes=65536 if '--astra' in sys.argv else 524288)
 native=NativePlayback(cache)
 try:
  with patch('audio_partial.get_response',side_effect=opener):
   began=time.perf_counter();cache._prepare(VID,{},began);prepare_ms=round((time.perf_counter()-began)*1000,2);prepared_requests=list(requests)
   response=cache.open(VID);initial_requests=len(requests);t=time.perf_counter();first=response.read1(8192);cached_first_ms=round((time.perf_counter()-t)*1000,3)
   assert len(requests)==initial_requests,'cached prefix must not touch origin'
   with virtual.open('wb') as out:
    out.write(first)
    while True:
     chunk=response.read1(8192)
     if not chunk:break
     out.write(chunk)
   response.close();assert validate_faststart(virtual)==len(raw)
   def digest(path):return subprocess.check_output([ff,'-v','error','-i',str(path),'-f','md5','pipe:1'],timeout=15)
   assert digest(original)==digest(virtual),'decoded media samples must remain identical'
   served=virtual.read_bytes()
   for header,start,end in [('bytes=0-1',0,1),('bytes=-64',len(raw)-64,len(raw)-1),('bytes=520000-540000',520000,540000)]:
    response=cache.open(VID,header);parts=[]
    while True:
     chunk=response.read1(8192)
     if not chunk:break
     parts.append(chunk)
    response.close();assert b''.join(parts)==served[start:end+1]
   class Player(Handler):
    def session(self,*args):return None
    def do_GET(self):
     if self.path=='/audio':
      self.started=time.perf_counter();self.audio_request_id='partial-fixture';self.audio_video_id=None
      response=native.open(('fixture','attempt'),VID,self.headers.get('Range')) if '--astra' in sys.argv else cache.open(VID,self.headers.get('Range'))
      assert response is not None,'native cold preparation did not complete'
      self.relay(None,response,response,'audio/mp4',1);return
     body=b'<meta name="viewport" content="width=device-width,initial-scale=1"><audio preload="auto" playsinline></audio><button>Play</button>'
     self.send_response(200);self.headers_common('text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   player=ThreadingHTTPServer(('127.0.0.1',0),Player);threading.Thread(target=player.serve_forever,daemon=True).start()
   try:
    if '--astra' in sys.argv:cache.invalidate(VID)
    with patch('server.metric'),sync_playwright() as p:
     webkit='--webkit' in sys.argv;browser=(p.webkit if webkit else p.chromium).launch();results=[]
     for _ in range(3):
      context=browser.new_context(**p.devices['iPhone 13']);page=context.new_page();page.goto(f'http://127.0.0.1:{player.server_port}/')
      page.evaluate("""()=>{window.result=new Promise(resolve=>{const a=document.querySelector('audio'),marks={};let t;for(const event of ['loadedmetadata','loadeddata','playing','error'])a.addEventListener(event,()=>{marks[event]=Math.round(performance.now()-t);if(event==='playing'||event==='error')resolve({marks,error:a.error?.code||null,duration:a.duration})},{once:true});document.querySelector('button').onclick=()=>{t=performance.now();a.src='/audio';a.play().catch(error=>resolve({marks,error:error.name}));setTimeout(()=>resolve({marks,error:'timeout'}),10000)}})}""")
      page.locator('button').click();result=page.evaluate('()=>window.result')
      if not webkit:
       assert result['error'] is None,result
       if '--astra' in sys.argv:
        page.evaluate('()=>{document.querySelector("audio").currentTime=35}')
        page.wait_for_function('()=>document.querySelector("audio").currentTime>35.25&&!document.querySelector("audio").paused',timeout=5000)
        result['seek_beyond_prefix']='PASS'
      results.append(result);context.close()
     browser.close()
   finally:player.shutdown();player.server_close()
   print(json.dumps({'engine':'WebKit' if webkit else 'Chromium','upstream_delay_ms':80,'partial_prepare_ms':prepare_ms,'cached_first_8k_ms':cached_first_ms,'preparation_ranges':prepared_requests,'preparation_bytes':sum(e-s+1 for s,e in prepared_requests),'total_bytes':len(raw),'decode_equivalence':'PASS','random_access':'PASS','browser':results}))
 finally:cache.close();origin.shutdown();origin.server_close()
