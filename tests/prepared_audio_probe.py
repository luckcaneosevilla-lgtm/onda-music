"""Actual AAC re-encode/cache/Range/HTML audio integration; synthetic input only."""
import http.client,json,subprocess,sys,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
import av
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audio_prepared import PreparedAudioCache,validate_faststart
from server import Handler
ff=imageio_ffmpeg.get_ffmpeg_exe();VID='abcdefghijk'
with tempfile.TemporaryDirectory() as directory:
 source=Path(directory)/'original.mp4'
 subprocess.run([ff,'-v','error','-f','lavfi','-i','sine=frequency=440:duration=20','-c:a','aac','-b:a','96k',str(source)],check=True)
 original=source.read_bytes();downloads=[]
 class Upstream(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   downloads.append(self.path);time.sleep(.15)
   self.send_response(200);self.send_header('Content-Type','audio/mp4');self.send_header('Content-Length',str(len(original)));self.end_headers()
   try:self.wfile.write(original)
   except OSError:pass
 upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream);threading.Thread(target=upstream.serve_forever,daemon=True).start()
 def opener(url,headers,timeout=3):
  connection=http.client.HTTPConnection('127.0.0.1',upstream.server_port,timeout=timeout);connection.request('GET','/fixture',headers=headers);return connection,connection.getresponse()
 resolutions=[]
 def resolve(vid,metadata):
  resolutions.append(vid);return {'url':'https://r1.googlevideo.com/file?mime=audio%2Fmp4','mime':'audio/mp4'}
 cache=PreparedAudioCache(resolve,binary=ff,max_bytes=2*1024*1024)
 try:
  with patch('audio_prepared.get_response',side_effect=opener):
   item={'provider':'piped','provider_id':VID,'duration':20}
   started=time.perf_counter();cache.prefetch_many([item,item]);assert cache.open(VID) is None
   deadline=time.monotonic()+15
   while time.monotonic()<deadline:
    with cache.lock:ready=VID in cache.entries;busy=bool(cache.inflight or cache.pending)
    if ready:break
    if not busy:raise AssertionError('preparation failed')
    time.sleep(.01)
   assert ready,'preparation timeout';prepare_ms=round((time.perf_counter()-started)*1000)
   entry=cache.entries[VID];output=entry['path'].read_bytes();assert validate_faststart(entry['path'])==len(output)
   with av.open(str(entry['path'])) as media:
    audio=media.streams.audio[0];codec=audio.codec_context.name;profile=audio.codec_context.profile;bitrate=audio.bit_rate
   assert codec=='aac' and profile=='LC',(codec,profile)
   decoded=subprocess.run([ff,'-v','error','-i',str(entry['path']),'-f','null','-'],capture_output=True,timeout=10);assert decoded.returncode==0
   for header,start,end in [('bytes=0-1',0,1),('bytes=100-199',100,199),('bytes=-8',len(output)-8,len(output)-1),('bytes=100-',100,len(output)-1)]:
    response=cache.open(VID,header)
    try:
     parts=[]
     while True:
      chunk=response.read1(8192)
      if not chunk:break
      parts.append(chunk)
     assert b''.join(parts)==output[start:end+1],header
     assert response.getheader('Content-Range')==f'bytes {start}-{end}/{len(output)}'
    finally:response.close()
   for header in ['bytes=-0',f'bytes={len(output)}-','bytes=4-2','bytes=0-2,4-5']:assert cache.open(VID,header).status==416
   cache.prefetch_many([item]);time.sleep(.05);assert len(downloads)==1 and len(resolutions)==1
   assert not list(entry['path'].parent.glob('*.input')) and not list(entry['path'].parent.glob('*.part.mp4'))
  class Player(Handler):
   def session(self,*args):return None
   def do_GET(self):
    if self.path=='/audio':
     self.started=time.perf_counter();self.audio_request_id='prepared-fixture';self.audio_video_id=None
     response=cache.open(VID,self.headers.get('Range'))
     if response is None:self.send_error(404);return
     self.relay(None,response,response,'audio/mp4',1);return
    body=b'<meta name="viewport" content="width=device-width,initial-scale=1"><audio preload="auto" playsinline></audio><button>Play</button>'
    self.send_response(200);self.headers_common('text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  metric_patch=patch('server.metric');metric_patch.start()
  player=ThreadingHTTPServer(('127.0.0.1',0),Player);threading.Thread(target=player.serve_forever,daemon=True).start()
  browser_results=[]
  try:
   with sync_playwright() as p:
    webkit='--webkit' in sys.argv;browser=(p.webkit if webkit else p.chromium).launch()
    for attempt in range(3):
     context=browser.new_context(**p.devices['iPhone 13']);page=context.new_page();page.goto(f'http://127.0.0.1:{player.server_port}/')
     page.evaluate("""()=>{window.result=new Promise(resolve=>{const a=document.querySelector('audio'),marks={};let t;for(const event of ['loadedmetadata','loadeddata','playing','error'])a.addEventListener(event,()=>{marks[event]=Math.round(performance.now()-t);if(event==='playing'||event==='error')resolve({marks,error:a.error?.code||null})},{once:true});document.querySelector('button').onclick=()=>{t=performance.now();a.src='/audio';a.play().catch(error=>resolve({marks,error:error.name}));setTimeout(()=>resolve({marks,error:'timeout'}),10000)}})}""")
     page.locator('button').click();browser_results.append(page.evaluate('()=>window.result'));context.close()
    browser.close()
  finally:player.shutdown();player.server_close();metric_patch.stop()
  assert len(downloads)==1 and len(resolutions)==1,'Play must not re-download or resolve cached audio'
  print(json.dumps({'engine':'WebKit' if webkit else 'Chromium','profile':'iPhone 13 emulated on Windows','prepare_total_ms':prepare_ms,'codec':codec,'profile_aac':profile,'bitrate':bitrate,'cache_range_decode_atomicity':'PASS','upstream_downloads':len(downloads),'browser':browser_results,'safari_release_gate_passed':webkit and all(result['error'] is None and result['marks'].get('loadedmetadata',9999)<500 for result in browser_results)}))
 finally:cache.close();upstream.shutdown();upstream.server_close()
