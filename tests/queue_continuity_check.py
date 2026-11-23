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
IPHONE='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
try:
 with patch.object(onda.resolver,'prefetch_many'),sync_playwright() as p:
  browser=p.chromium.launch()
  auth=browser.new_context()
  auth.request.post(origin+'/api/register',data={'name':'Queue QA','email':'queue@example.test','password':'isolated-queue-regression'})
  state=auth.storage_state();auth.close()
  for case in ['truncated-stream-recovery','truncated-device-recovery','truncated-native-pause','truncated-recovery-exhausted','truncated-toggle','natural-end','native-pause-no-timers','native-ended-wrong-duration','hidden-interruption','hidden-next-no-timers','hidden-failure-no-timers','hidden-ios-early-handoff','late-ended-after-switch','hidden-explicit-pause','silent-end-no-event','browser-pause-at-end','old-radio-new-queue','pause-during-radio','enqueue-during-radio','enqueue-while-radio-stuck','repeat-next','radio-timeout','tapped-failure-stays']:
   if len(sys.argv)>1 and case not in sys.argv[1:]:continue
   print('Checking '+case,flush=True)
   # Lock-screen cases run the iPhone code path (synchronous native source).
   ios=case in ('hidden-ios-early-handoff','native-pause-no-timers','native-ended-wrong-duration','hidden-next-no-timers','late-ended-after-switch','hidden-failure-no-timers','tapped-failure-stays')
   context=browser.new_context(viewport={'width':390,'height':844},storage_state=state,**({'user_agent':IPHONE} if ios else {}))
   page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('requestfailed',lambda r: print('Network failure: '+r.url+' '+str(r.failure),flush=True) if r.resource_type=='script' else None)
   context.route('**/api/audio/prefetch',lambda r:r.fulfill(status=202,json={}))
   media_requests=[]
   def media(route):
    url=route.request.url
    if url.endswith('/api/audio/prefetch'):return route.fulfill(status=202,json={})
    if 'resolve=1' in url:
     # The resolve call is JSON; answering it with WAV failed every song.
     vid=url.split('/api/audio/')[1].split('?')[0]
     return route.fulfill(json={'url':'/api/audio/'+vid+'?stream=1','level':1,'source':'proxy','expires':9e9})
    if 'broken' in url:return route.fulfill(status=502,body='')
    media_requests.append(url)
    seconds=1 if 'short' in url else 60
    if case=='truncated-stream-recovery' and len(media_requests)==1:seconds=5
    if case=='truncated-recovery-exhausted' and 'first000001' in url:seconds=1
    route.fulfill(body=audio(seconds),content_type='audio/wav')
   context.route('**/api/audio/*',media)
   context.route('**/api/online/items/**',lambda r:r.fulfill(json={}))
   context.route('**/api/online/status',lambda r:r.fulfill(json={'configured':True,'source':'piped','piped':False}))
   context.route('**/api/online/charts',lambda r:r.fulfill(json={'country':'Test','songs':[],'artists':[]}))
   context.route('**/api/online/recommendations*',lambda r:r.fulfill(json={'items':[]}))
   if case in ('silent-end-no-event','browser-pause-at-end'):page.clock.install()
   page.goto(origin)
   assert not errors,errors
   page.wait_for_function('() => me && typeof ensureRadio === "function"')
   page.evaluate("""() => {
     localStorage.setItem('ivox-crossfade','0');prefetchNext=()=>{};
     window.song=id=>({id:'piped:'+id,provider_id:id,provider:'piped',title:id,channel:'Fixture',duration:id.startsWith('short')?1:60});
     window.finishCurrent=()=>{
       lastPlaybackTime=onlineAudio.duration;
       Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:onlineAudio.duration});
       onlineAudio.addEventListener('emptied',()=>{delete onlineAudio.currentTime},{once:true});
       return onlineAudio.onended();
     };
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
   if case.startswith('truncated-'):
    if case=='truncated-device-recovery':
     page.evaluate("""async body => {
       await (await caches.open(AUDIO_CACHE)).put(audioKey('piped:first000001'),new Response(new Uint8Array(body),{headers:{'Content-Type':'audio/wav'}}));
       cachedIds.add('piped:first000001');
     }""",list(audio(1)))
    page.evaluate("() => {setAutoplay(false);playList([song('first000001'),song('next0000001')])}")
    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1')
    if case=='truncated-recovery-exhausted':page.evaluate('() => {pickedId=null}')
    if case in ('truncated-native-pause','truncated-toggle'):
     page.evaluate("""toggle => {
       // Model pause-at-EOF and Play at a truncated native duration. Removing
       // overrides synchronously on load lets the recovered real WAV continue.
       const load=onlineAudio.load.bind(onlineAudio);
       onlineAudio.load=()=>{delete onlineAudio.currentTime;delete onlineAudio.duration;delete onlineAudio.ended;onlineAudio.load=load;load()};
       lastPlaybackTime=5;
       Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:5});
       Object.defineProperty(onlineAudio,'duration',{configurable:true,value:5});
       Object.defineProperty(onlineAudio,'ended',{configurable:true,value:true});
       freshRetried.add(audioQueue[audioIndex].id);
       if(toggle){pausePlayback();toggleOnline()}else{onlineAudio.onpause();onlineAudio.dispatchEvent(new Event('ended'))}
     }""",case=='truncated-toggle')
    if case=='truncated-recovery-exhausted':
     # A song that already played and cannot be recovered must not halt the
     # queue (a locked phone would stay silent): after bounded retries, move on.
     page.wait_for_function("() => audioIndex===1 && onlineAudio.getAttribute('src')?.includes('next0000001')",timeout=12000)
     assert 1<len([u for u in media_requests if 'first000001' in u])<=4,media_requests
    else:
     try:page.wait_for_function('() => audioIndex===0 && !onlineAudio.paused && onlineAudio.currentTime>6',timeout=14000)
     except Exception:
      print(page.evaluate('({index:audioIndex,time:onlineAudio.currentTime,paused:onlineAudio.paused,ended:onlineAudio.ended,wanted:playbackWanted,recoveries:recoveryAttempts,recoveryInFlight,resumeAt,lastPlaybackTime,level:activeLevel,state:document.querySelector("#online-player").dataset.state,status:document.querySelector("#playback-status").textContent,source:onlineAudio.getAttribute("src")})'),media_requests,flush=True)
      raise
     assert page.evaluate('audioQueue[0].provider_id')=='first000001'
     assert not any('next0000001' in url for url in media_requests),media_requests
     if case=='truncated-stream-recovery':
      assert len(media_requests)>=2 and 'refresh=1' in media_requests[1],media_requests
     if case=='truncated-device-recovery':
      assert page.evaluate("!cachedIds.has('piped:first000001')"),'Broken device copy was reused'
   elif case=='tapped-failure-stays':
    context.route('**/api/online/search*',lambda r:r.fulfill(json={'items':[]}))
    page.evaluate("() => {setAutoplay(false);playList([song('broken00001'),song('next0000001')])}")
    page.wait_for_function("() => document.querySelector('#online-player').dataset.state==='error'",timeout=10000)
    page.wait_for_timeout(1600)
    assert page.evaluate("audioIndex===0 && !(onlineAudio.getAttribute('src')||'').includes('next0000001')"),'A tapped song that failed was replaced by the next one'
   elif case=='natural-end':
    page.evaluate("() => {setAutoplay(false);playList([song('short000001'),song('next0000001')])}")
    page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='next0000001' && !onlineAudio.paused && onlineAudio.currentTime>.1",timeout=6000)
   elif case in ('native-pause-no-timers','native-ended-wrong-duration','hidden-interruption','hidden-next-no-timers','hidden-failure-no-timers','hidden-ios-early-handoff','hidden-explicit-pause','late-ended-after-switch'):
    page.evaluate("() => {setAutoplay(false);playList([song('first000001'),song('next0000001')])}")
    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1')
    if case=='late-ended-after-switch':
     page.evaluate("() => {audioQueue.push(song('third000001'));Object.defineProperty(document,'hidden',{configurable:true,value:true});nextTrack();onlineAudio.dispatchEvent(new Event('ended'))}")
     assert page.evaluate('audioIndex===1 && !queueFinished'),'A stale ended event skipped the new song'
    elif case=='hidden-next-no-timers':
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:true});nextTrack()}")
     assert page.evaluate("audioIndex===1 && skipTimer===null && onlineAudio.getAttribute('src').includes('next0000001')"),'Lock-screen Next depends on the debounce timer'
     assert page.evaluate("navigator.mediaSession.playbackState")=='playing','Lock-screen session dropped to none while the next song loads'
    elif case=='hidden-ios-early-handoff':
     # Locked iPhone: chain before audio stops, because iOS suspends the page
     # (and its 'ended' handler) as soon as nothing is playing.
     page.evaluate("() => {Object.defineProperty(document,'hidden',{configurable:true,value:true});const at=onlineAudio.duration-1;lastPlaybackTime=at-.5;Object.defineProperty(onlineAudio,'currentTime',{configurable:true,value:at});onlineAudio.addEventListener('emptied',()=>{delete onlineAudio.currentTime},{once:true});onlineAudio.dispatchEvent(new Event('timeupdate'))}")
     assert page.evaluate("audioIndex===1 && onlineAudio.getAttribute('src').includes('next0000001')"),'Locked iPhone waited for ended before chaining'
    elif case=='hidden-failure-no-timers':
     # Only an unplayed automatic queue entry may be skipped on failure.
     page.evaluate("() => {pickedId=null;lastPlaybackTime=0;Object.defineProperty(document,'hidden',{configurable:true,value:true});playbackFailure('Fixture failure.')}")
     assert page.evaluate("audioIndex===1 && onlineAudio.getAttribute('src').includes('next0000001')"),'A failed song while locked waits for a suspended timer'
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
       if(wrong)audioQueue[0].duration=63;
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
     page.evaluate("() => {onlineData={saves:[{video_id:'piped:fav00000001'}],items:{'piped:fav00000001':song('fav00000001')}};finishCurrent()}")
     page.clock.run_for(12500)
     page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='fav00000001'",timeout=2000)
    else:
     page.evaluate('() => {window.finishPromise=finishCurrent()}')
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
