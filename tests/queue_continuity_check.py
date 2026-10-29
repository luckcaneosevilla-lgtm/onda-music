"""Full-app queue/radio race regressions using real Chromium audio decoding."""
import io
import json
from pathlib import Path
import sys
import threading
import wave
from unittest.mock import patch
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from test_security import onda, temporary


def audio(seconds):
    data=io.BytesIO()
    with wave.open(data,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000)
        f.writeframes(b'\0\0'*8000*seconds)
    return data.getvalue()


onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
report=[]
try:
 with patch.object(onda.resolver,'prefetch_many'),sync_playwright() as p:
  browser=p.chromium.launch()
  auth=browser.new_context()
  auth.request.post(origin+'/api/register',data={'name':'Queue QA','email':'queue@example.test','password':'isolated-queue-regression'})
  state=auth.storage_state();auth.close()
  for case in ['natural-end','native-pause-no-timers','native-ended-wrong-duration','hidden-interruption','hidden-next-no-timers','late-ended-after-switch','hidden-explicit-pause','silent-end-no-event','browser-pause-at-end','old-radio-new-queue','pause-during-radio','enqueue-during-radio','enqueue-while-radio-stuck','repeat-next','radio-timeout']:
   print('Checking '+case,flush=True)
   context=browser.new_context(viewport={'width':390,'height':844},storage_state=state)
   page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   context.route('**/api/audio/prefetch',lambda r:r.fulfill(status=202,json={}))
   context.route('**/api/audio/*',lambda r:r.fulfill(body=audio(1 if 'short' in r.request.url else 60),content_type='audio/wav'))
   context.route('**/api/online/items/**',lambda r:r.fulfill(json={}))
   context.route('**/api/online/status',lambda r:r.fulfill(json={'configured':True,'source':'piped','piped':False}))
   context.route('**/api/online/charts',lambda r:r.fulfill(json={'country':'Test','songs':[],'artists':[]}))
   context.route('**/api/online/recommendations*',lambda r:r.fulfill(json={'items':[]}))
   if case in ('silent-end-no-event','browser-pause-at-end'):page.clock.install()
   page.goto(origin);page.wait_for_function('() => me && typeof ensureRadio === "function"')
   page.evaluate("""() => {
     localStorage.setItem('ivox-crossfade','0');prefetchNext=()=>{};
     window.song=id=>({id:'piped:'+id,provider_id:id,provider:'piped',title:id,channel:'Fixture',duration:id.startsWith('short')?1:60});
     window.originalApi=api;window.radioCalls=[];window.releaseRadio=null;
     api=(url,method,body,options={})=>{
       if(url.startsWith('/api/online/radio/')){
         radioCalls.push(url);
         return new Promise((resolve,reject)=>{
           releaseRadio=resolve;
           options.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
         });
       }
       if(url.startsWith('/api/online/search'))return Promise.resolve({items:[]});
       return originalApi(url,method,body,options);
     };
   }""")
   if case=='natural-end':
    page.evaluate("() => {setAutoplay(false);playList([song('short000001'),song('next0000001')])}")
    page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='next0000001' && !onlineAudio.paused && onlineAudio.currentTime>.1",timeout=6000)
   elif case in ('native-pause-no-timers','native-ended-wrong-duration','hidden-interruption','hidden-next-no-timers','hidden-explicit-pause','late-ended-after-switch'):
    page.evaluate("() => {setAutoplay(false);playList([song('first000001'),song('next0000001')])}")
    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1')
    if case=='late-ended-after-switch':
     page.evaluate("() => {audioQueue.push(song('third000001'));Object.defineProperty(document,'hidden',{configurable:true,value:true});nextTrack();onlineAudio.dispatchEvent(new Event('ended'))}")
     assert page.evaluate('audioIndex===1 && !queueFinished'),'A stale ended event skipped the new song'
    elif case=='hidden-next-no-timers':
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:true});nextTrack()}")
     assert page.evaluate("audioIndex===1 && skipTimer===null && onlineAudio.getAttribute('src').includes('next0000001')"),'Lock-screen Next depends on the debounce timer'
    elif case=='hidden-explicit-pause':
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:true});pausePlayback();Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))}")
     page.wait_for_timeout(150)
     assert page.evaluate('onlineAudio.paused && !playbackWanted && !onlineAudio.autoplay')
    elif case=='hidden-interruption':
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:true});onlineAudio.pause()}")
     page.wait_for_timeout(100)
     assert page.evaluate('playbackWanted'),'Browser background pause erased listening intent'
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))}")
     page.wait_for_function('() => !onlineAudio.paused')
    else:
     page.evaluate("""wrong => {
       onlineAudio.addEventListener('emptied',()=>{delete onlineAudio.currentTime;delete onlineAudio.paused;delete onlineAudio.ended},{once:true});
       if(wrong)audioQueue[0].duration=120;
       lastPlaybackTime=59.5;
       Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:59.5});
       Object.defineProperty(onlineAudio,'paused',{configurable:true,value:true});
       if(wrong)Object.defineProperty(onlineAudio,'ended',{configurable:true,value:true});
       onlineAudio.onpause();
     }""",case=='native-ended-wrong-duration')
     # Do not run a clock: a locked iPhone can suspend JavaScript timers.
     assert page.evaluate('audioIndex')==1,'End transition incorrectly depends on a JavaScript timer'
   elif case in ('silent-end-no-event','browser-pause-at-end'):
    page.evaluate("() => {setAutoplay(false);playList([song('first000001'),song('next0000001')])}")
    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1')
    page.evaluate("""paused => {
      onlineAudio.addEventListener('emptied',()=>{delete onlineAudio.currentTime;delete onlineAudio.paused},{once:true});
      lastPlaybackTime=59.5;
      Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:59.5});
      Object.defineProperty(onlineAudio,'paused',{configurable:true,value:paused});
      if(paused)onlineAudio.onpause();
    }""",case=='browser-pause-at-end')
    page.clock.run_for(5000)
    assert page.evaluate('audioIndex')==1,page.evaluate('({index:audioIndex,wanted:playbackWanted,frozenAt,frozenSince,version:playVersion,time:onlineAudio.currentTime,paused:onlineAudio.paused})')
    page.evaluate('() => {delete onlineAudio.currentTime;delete onlineAudio.paused}')
   elif case=='old-radio-new-queue':
    page.evaluate("() => {playList([song('old00000001')]);ensureRadio(true);window.oldRelease=releaseRadio;playList([song('new00000001')]);ensureRadio(true)}")
    page.wait_for_function('() => radioCalls.length===2',timeout=2000)
    page.evaluate("() => releaseRadio({items:[song('next0000001')]})")
    page.wait_for_function("() => audioQueue.some(v=>v.provider_id==='next0000001')")
    page.evaluate("() => oldRelease({items:[song('stale000001')]})")
    assert not page.evaluate("audioQueue.some(v=>v.provider_id==='stale000001')")
   else:
    if case=='radio-timeout':page.clock.install()
    page.evaluate("() => playList([song('first000001')])")
    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1 && releaseRadio')
    if case=='repeat-next':
     page.evaluate("() => {nextTrack();nextTrack();releaseRadio({items:[song('next0000001'),song('next0000002')]})}")
     page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='next0000001' && !onlineAudio.paused && onlineAudio.currentTime>.1")
     page.wait_for_timeout(300)
     assert page.evaluate('audioIndex')==1
    elif case=='radio-timeout':
     page.evaluate("() => {onlineData={saves:[{video_id:'piped:fav00000001'}],items:{'piped:fav00000001':song('fav00000001')}};onlineAudio.onended()}")
     page.clock.run_for(12500)
     page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='fav00000001'",timeout=2000)
    else:
     page.evaluate('() => {window.finishPromise=onlineAudio.onended()}')
     if case=='pause-during-radio':
      page.evaluate("() => {pausePlayback();releaseRadio({items:[song('next0000001')]})}")
      page.wait_for_timeout(350)
      assert page.evaluate('audioIndex===0 && onlineAudio.paused && !playbackWanted'), 'Pause was ignored after radio returned'
     else:
      if case=='enqueue-while-radio-stuck':page.evaluate("() => enqueue([song('queued00001')])")
      else:page.evaluate("() => {enqueue([song('queued00001')]);releaseRadio({items:[]})}")
      page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='queued00001' && !onlineAudio.paused",timeout=2000)
   assert not errors,errors
   report.append({'case':case,'status':'passed'});context.close()
  browser.close()
 print(json.dumps(report))
finally:
 server.shutdown();server.server_close();temporary.cleanup()
