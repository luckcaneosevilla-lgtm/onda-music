"""Live click -> HTML5 playing measurement, isolated local accounts and Chromium.

No search/prefetch for cold IDs. Public upstreams only. A playing event confirms
decoder start, not a microphone measurement or production network SLA.
"""
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda
from playwright.sync_api import sync_playwright

onda.init();onda.resolver.start(warm='jNQXAC9IVRw')
deadline=time.monotonic()+40
while len(onda.resolver.idle)<3 and time.monotonic()<deadline:time.sleep(.1)
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
report={'metric':'click to HTMLMediaElement playing on local Chromium','cold':[],'prefetched':[]}
hold_seconds=float(os.environ.get('IVOX_BENCH_HOLD_SECONDS','0'))
try:
 with sync_playwright() as p:
  browser=p.chromium.launch()
  context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
  context.request.post(base+'/api/register',data={'name':'Streaming benchmark','email':'streaming@example.test','password':'isolated-streaming-benchmark'})
  auth=context.storage_state();context.close()
  ids=sys.argv[1:] or ['dQw4w9WgXcQ','kJQP7kiw5Fk','fJ9rUzIMcZQ']
  for video_id in ids:
   with onda.db() as c:c.execute('INSERT OR REPLACE INTO online_catalog VALUES(?,?,?)',('piped:'+video_id,json.dumps({'id':'piped:'+video_id,'provider_id':video_id,'provider':'piped','title':'Benchmark'}),int(time.time())))
   onda.resolver.invalidate(video_id)
   for mode in ('cold','prefetched'):
    if mode=='prefetched':onda.resolver.prefetch_fragment(video_id)
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,storage_state=auth)
    page=context.new_page();page.goto(base)
    page.wait_for_function("() => typeof startOnline === 'function' && me !== null")
    # The measured Play is the first gesture: never hide a first-tap failure.
    # Keep network work under test limited to this track; no local audio cache is reused.
    page.evaluate('() => {ensureRadio=()=>{};prefetchNext=()=>{}}')
    page.evaluate("""id => {
      stopOnline();audioQueue=[{id:'piped:'+id,provider_id:id,provider:'piped',source:'YouTube',title:'Live benchmark',channel:'Public video'}];audioIndex=0;
      document.querySelector('#online-player').hidden=false;
      window.playingMs=null;window.playStarted=null;
      const button=document.querySelector('#np-toggle');
      button.addEventListener('click',()=>{window.playStarted=performance.now()},{once:true});
      onlineAudio.addEventListener('playing',()=>{window.playingMs=performance.now()-window.playStarted},{once:true});
    }""",video_id)
    try:
     page.locator('#np-toggle').click()
     page.wait_for_function('() => window.playingMs !== null && onlineAudio.currentTime>0',timeout=30000)
     milliseconds=page.evaluate('window.playingMs')
     row={'id':video_id,'playing_ms':round(milliseconds,2)}
     if hold_seconds:
      page.wait_for_function('seconds => onlineAudio.currentTime>=seconds && !onlineAudio.paused',arg=hold_seconds,timeout=(hold_seconds+20)*1000)
      row.update(played_until_s=round(page.evaluate('onlineAudio.currentTime'),2),recoveries=page.evaluate('recoveryAttempts'))
     report[mode].append(row)
    except Exception as error:report[mode].append({'id':video_id,'error':type(error).__name__,'detail':str(error)[:350]})
    page.evaluate('onlineAudio.pause()')
    context.close()
  browser.close()
 for mode in ('cold','prefetched'):
  values=[r['playing_ms'] for r in report[mode] if 'playing_ms' in r]
  report[mode+'_summary']={'attempts':len(report[mode]),'successes':len(values),'median_ms':round(statistics.median(values),2) if values else None,'under_1000ms':sum(v<1000 for v in values)}
 out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
 (out/'browser-stream-live.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report),flush=True)
finally:
 server.shutdown();server.server_close()
 for worker in list(onda.resolver.idle):worker.kill()
