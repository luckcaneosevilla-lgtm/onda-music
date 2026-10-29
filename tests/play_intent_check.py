"""Full-app regression: first tap, repeated loading taps, delayed sources and Safari metadata.

Uses actual Chromium audio decoding with a WAV fixture, isolated local accounts,
and no external music services. Safari UA is logic coverage, not real Safari.
"""
import io
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
import wave
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda

raw=io.BytesIO()
with wave.open(raw,'wb') as wav:
    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0\0'*8000*60)
body=raw.getvalue()
onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
report=[]
def media(route):
    # Deliberate network delay makes the pre-playing repeat-tap race deterministic.
    time.sleep(.25)
    route.fulfill(body=body,content_type='audio/wav',headers={'Accept-Ranges':'bytes'})
try:
 with patch.object(onda.resolver,'prefetch_many'),sync_playwright() as p:
  browser=p.chromium.launch()
  auth=browser.new_context()
  auth.request.post(base+'/api/register',data={'name':'Playback tests','email':'playback@example.test','password':'isolated-playback-tests'})
  state=auth.storage_state();auth.close()
  for mode in ['first-tap','repeat-loading','sheet-first-tap','sheet-repeat-loading','sheet-seek','unlock-in-flight','async-source','legacy-cache','safari-duration','recover-network','stall-reconnect','pause-stall','switch-stall','no-copy-download']:
   print('Checking '+mode,flush=True)
   options={'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True,'storage_state':state}
   if mode=='safari-duration':options['user_agent']='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1'
   context=browser.new_context(**options);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   media_requests=[]
   def tracked_media(route):media_requests.append(route.request.url);media(route)
   context.route('**/api/audio/testVideo*',tracked_media)
   context.route('**/fixture-audio',media)
   if mode=='async-source':
    def delayed_source(route):
     time.sleep(.15);route.fulfill(json={'url':'/fixture-audio','source':'audius','level':4})
    context.route('**/api/online/play/**',delayed_source)
   page.goto(base);page.wait_for_function("() => typeof startOnline === 'function' && me !== null")
   provider='audius' if mode=='async-source' else 'piped'
   page.evaluate("""provider => {
     ensureRadio=()=>{};prefetchNext=()=>{};
     audioQueue=[{id:provider+':testVideo01',provider_id:'testVideo01',provider,title:'Regression track',duration:10}];audioIndex=0;
     document.querySelector('#online-player').hidden=false;
     window.realPlaying=false;window.playMs=null;window.tapAt=null;
     onlineAudio.addEventListener('playing',()=>{if(onlineAudio.getAttribute('src')!=='/silence.wav'){realPlaying=true;playMs=performance.now()-tapAt}});
     document.querySelector('#np-toggle').addEventListener('click',()=>{if(tapAt===null)tapAt=performance.now()});
   }""",provider)
   if mode=='unlock-in-flight':
    # Generic touch begins unlocking before the real Play, without waiting for its promise.
    page.evaluate('() => unlockAudio()')
   if mode=='legacy-cache':
    page.evaluate("async () => {const cache=await caches.open(AUDIO_CACHE);await cache.put(audioKey('piped:testVideo01'),await fetch('/fixture-audio'));cachedIds.add('piped:testVideo01')}")
   control='#sheet-toggle' if mode.startswith('sheet-') else '#np-toggle'
   if mode.startswith('sheet-'):
    page.evaluate("() => {nowPlaying.open();document.querySelector('#sheet-toggle').addEventListener('click',()=>{if(tapAt===null)tapAt=performance.now()})}")
   if mode.endswith('repeat-loading'):
    page.evaluate("selector => document.querySelector(selector).addEventListener('click',()=>setTimeout(()=>document.querySelector(selector).click(),40),{once:true})",control)
   page.locator(control).click()
   try:page.wait_for_function('() => realPlaying && onlineAudio.currentTime>0',timeout=6000)
   except Exception:
    print(page.evaluate('({state:document.querySelector("#online-player").dataset.state,wanted:playbackWanted,paused:onlineAudio.paused,time:onlineAudio.currentTime,source:onlineAudio.getAttribute("src"),errors:window.realPlaying})'),flush=True)
    raise
   result=page.evaluate('({ms:playMs,paused:onlineAudio.paused,time:onlineAudio.currentTime,fixing:fixingDuration,source:onlineAudio.getAttribute("src")})')
   assert not result['paused'] and result['time']<3 and not result['fixing'],(mode,result)
   assert result['source']!='/silence.wav'
   assert not errors,errors
   if mode=='sheet-seek':
    page.evaluate("""() => {
     const r=document.querySelector('#sheet-range');r.value=800;r.dispatchEvent(new Event('input'));progress(1,10);
    }""")
    assert page.locator('#sheet-current').inner_text()=='0:08'
    assert page.locator('#sheet-fill').evaluate('(e)=>e.style.width')=='80%'
    page.evaluate("document.querySelector('#sheet-range').dispatchEvent(new Event('change'))")
    page.wait_for_function('() => onlineAudio.currentTime>=8 && !onlineAudio.seeking')
    page.evaluate("""() => {Object.defineProperty(onlineAudio,'buffered',{configurable:true,value:{length:2,start:i=>[0,6][i],end:i=>[2,8][i]}});progress(8,10)}""")
    assert page.locator('#sheet-buffer>span').count()==2
    assert page.locator('#sheet-buffer>span').nth(1).evaluate('(e)=>e.style.left')=='60%'
    assert page.locator('#sheet-range').bounding_box()['height']>=44
   if mode in ('recover-network','no-copy-download'):
    page.evaluate('() => {audioQueue[audioIndex].duration=60}')
    page.evaluate('() => {onlineAudio.currentTime=12;lastPlaybackTime=12}')
    page.wait_for_function('() => onlineAudio.currentTime>=12 && !onlineAudio.seeking')
    if mode=='recover-network':
     page.evaluate('() => {realPlaying=false;onlineAudio.onerror();recoverCurrent();if(recoveryAttempts!==1)throw new Error("Duplicate recovery") }')
     page.wait_for_function('() => realPlaying && recoveryAttempts===1 && onlineAudio.currentTime>=12',timeout=6000)
     assert page.evaluate('audioQueue[audioIndex].provider_id')=='testVideo01'
     assert len(media_requests)>=2
    else:
     before=len(media_requests)
     page.evaluate("() => onlineAudio.dispatchEvent(new Event('timeupdate'))")
     page.wait_for_timeout(350)
     assert len(media_requests)==before,'A second full download competes with playback'
   if mode in ('stall-reconnect','pause-stall','switch-stall'):
    # This case stalls at 12 s in the 60 s fixture, not past a 10 s song's end.
    page.evaluate('() => {audioQueue[audioIndex].duration=60}')
    page.clock.install()
    page.evaluate("""() => {
      lastPlaybackTime=12;
      Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:12});
      Object.defineProperty(onlineAudio,'readyState',{configurable:true,value:1});
      onlineAudio.dispatchEvent(new Event('waiting'));
    }""")
    assert page.evaluate('stallTimer !== null')
    if mode=='pause-stall':
     page.evaluate('pausePlayback()');page.clock.fast_forward(4000)
     assert page.evaluate('!playbackWanted && onlineAudio.paused && recoveryAttempts===0')
    elif mode=='switch-stall':
     page.evaluate("""() => {
       delete onlineAudio.currentTime;delete onlineAudio.readyState;stopOnline();
       audioQueue=[{id:'piped:testVideo02',provider_id:'testVideo02',provider:'piped',title:'New track'}];audioIndex=0;startOnline();
     }""")
     page.wait_for_function('() => !onlineAudio.paused && onlineAudio.readyState>=2')
     page.clock.fast_forward(4000)
     assert page.evaluate("audioQueue[audioIndex].provider_id==='testVideo02' && recoveryAttempts===0")
    else:
     page.clock.fast_forward(3100)
     assert page.evaluate('recoveryAttempts===1 && audioIndex===0')
     page.evaluate('() => {delete onlineAudio.currentTime;delete onlineAudio.readyState}')
     page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>=12 && onlineAudio.readyState>=2',timeout=6000)
    page.evaluate('() => {delete onlineAudio.currentTime;delete onlineAudio.readyState}')
   if mode=='pause-stall':
    # Already paused deliberately; do not toggle it back on.
    pass
   else:
    page.locator(control).click()
    assert page.evaluate('onlineAudio.paused')
   report.append({'case':mode,'playing_ms':round(result['ms'],2),'status':'passed'})
   context.close()
  browser.close()
 out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
 (out/'play-intent-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report))
finally:server.shutdown();server.server_close()
