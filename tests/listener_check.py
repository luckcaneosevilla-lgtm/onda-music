"""Listener scenarios from real use, in the full app with Chromium audio.

- Opening song options or adding to the queue must never stop the music.
- When the queue runs out the music keeps going (radio, then the listener's
  favourites when the radio has nothing).
- A song added after the queue finished starts at once.
- Library collections can be played or queued as a whole.
- Revisiting the library renders from cache without waiting for the network.

Provider calls are mocked; audio is a real WAV decoded by Chromium.
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

def wav(seconds):
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*8000*seconds)
    return raw.getvalue()
SHORT,LONG=wav(2),wav(60)
song=lambda vid,title:{'id':'piped:'+vid,'provider':'piped','provider_id':vid,'title':title,'channel':'Test','source':'YouTube Music','thumbnail':'','duration':2 if vid.startswith('short') else 60}

onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
report=[]

def run(browser,state,name,radio=None,favorites=(),library_delay=0):
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,storage_state=state)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    context.route('**/api/audio/prefetch',lambda r:r.fulfill(status=202,json={}))
    context.route('**/api/audio/*',lambda r:r.fulfill(body=SHORT if '/short' in r.request.url else LONG,content_type='audio/wav',headers={'Accept-Ranges':'bytes'}))
    context.route('**/api/online/status',lambda r:r.fulfill(json={'configured':True,'source':'piped','piped':False,'allow_youtube_external':False}))
    context.route('**/api/online/charts',lambda r:r.fulfill(json={'country':'Test','songs':[],'artists':[]}))
    context.route('**/api/online/search?*',lambda r:r.fulfill(json={'items':[]}))
    items={v['id']:v for v in favorites}
    def library(route):
        time.sleep(library_delay[0] if isinstance(library_delay,list) else library_delay)
        route.fulfill(json={'saves':[{'video_id':v['id'],'favorite':1,'later':0,'last_played':None} for v in favorites],'playlists':[],'items':items,'recent':[]})
    context.route('**/api/online/library',library)
    context.route('**/api/online/radio/**',lambda r:r.fulfill(json={'items':radio}) if radio is not None else r.fulfill(status=502,json={'error':'radio down'}))
    context.route('**/api/online/items/**',lambda r:r.fulfill(json={}))
    page.goto(base);page.wait_for_function("() => typeof startOnline === 'function' && me !== null")
    return context,page,errors

def playing(page,vid,timeout=8000):
    page.wait_for_function("vid => audioQueue[audioIndex]?.provider_id===vid && !onlineAudio.paused && onlineAudio.currentTime>0.2 && document.querySelector('#online-player').dataset.state==='playing'",arg=vid,timeout=timeout)

try:
 with patch.object(onda.resolver,'prefetch_many'),sync_playwright() as p:
  browser=p.chromium.launch()
  auth=browser.new_context()
  auth.request.post(base+'/api/register',data={'name':'Listener','email':'listener@example.test','password':'isolated-listener-tests'})
  state=auth.storage_state();auth.close()
  long1,long2=song('longSong001','Primera'),song('longSong002','Segunda')

  print('Checking options-and-queue-keep-playing',flush=True)
  context,page,errors=run(browser,state,'dialog',radio=[])
  page.evaluate('song => playList([song])',long1);playing(page,'longSong001')
  page.evaluate('song => onlineOptions(song)',long2)
  page.wait_for_timeout(600)
  assert page.evaluate('dialog.open && !onlineAudio.paused'),'Opening options paused the music'
  page.locator('#enqueue').click();page.wait_for_timeout(400)
  assert page.evaluate("!onlineAudio.paused && audioQueue.length===2 && audioQueue[1].provider_id==='longSong002'")
  page.evaluate('song => pickOnlinePlaylist(song)',long2);page.wait_for_timeout(400)
  assert page.evaluate('!onlineAudio.paused'),'The playlist dialog paused the music'
  assert not errors,errors
  report.append({'case':'options-and-queue-keep-playing','status':'passed'});context.close()

  print('Checking queue-end-continues-with-radio',flush=True)
  context,page,errors=run(browser,state,'radio',radio=[song('radioSong01','Similar 1'),song('radioSong02','Similar 2')])
  page.evaluate("song => playList([song])",song('shortSong01','Corta'));playing(page,'shortSong01')
  playing(page,'radioSong01',timeout=10000)
  assert page.evaluate('audioQueue[audioIndex].radio===true')
  assert not errors,errors
  report.append({'case':'queue-end-continues-with-radio','status':'passed'});context.close()

  print('Checking radio-down-falls-back-to-favourites',flush=True)
  context,page,errors=run(browser,state,'fallback',radio=None,favorites=[song('favSong0001','Favorita')])
  page.evaluate('preloadOnline()');page.wait_for_function("() => onlineLoaded")
  page.evaluate("song => playList([song])",song('shortSong02','Corta'));playing(page,'shortSong02')
  playing(page,'favSong0001',timeout=10000)
  assert not errors,errors
  report.append({'case':'radio-down-falls-back-to-favourites','status':'passed'});context.close()

  print('Checking enqueue-after-finish-starts-now',flush=True)
  context,page,errors=run(browser,state,'finished',radio=[])
  page.evaluate("song => playList([song])",song('shortSong03','Corta'));playing(page,'shortSong03')
  page.wait_for_function("() => queueFinished && onlineAudio.paused",timeout=10000)
  page.evaluate('song => onlineOptions(song)',long1);page.locator('#enqueue').click()
  playing(page,'longSong001')
  assert not errors,errors
  report.append({'case':'enqueue-after-finish-starts-now','status':'passed'});context.close()

  print('Checking library-collection-play-and-cache',flush=True)
  delay=[0]
  context,page,errors=run(browser,state,'library',radio=[],favorites=[long1,long2],library_delay=delay)
  page.locator('nav a[href="/library"]').first.click()
  page.locator('[data-collection="play"]').click();playing(page,'longSong001')
  assert page.evaluate('audioQueue.length===2')
  page.locator('nav a[href="/"]').first.click();page.wait_for_selector('#online-query')
  # A 2 s library response (slow phone network) must not delay the page.
  page.evaluate("() => {const f=window.fetch;window.fetch=(u,o)=>String(u).includes('/api/online/library')?new Promise(r=>setTimeout(r,2000)).then(()=>f(u,o)):f(u,o)}")
  shown=page.evaluate("""() => new Promise(done => {
    const t=performance.now();document.querySelector('nav a[href="/library"]').click();
    const check=()=>document.querySelector('[data-collection="play"]')?done(Math.round(performance.now()-t)):requestAnimationFrame(check);check();
  })""")
  assert shown<300,shown
  assert page.evaluate('!onlineAudio.paused'),'Navigation stopped the music'
  page.wait_for_timeout(2300)
  assert not errors,errors
  report.append({'case':'library-collection-play-and-cache','status':'passed','library_ms_with_2s_network':shown});context.close()
  browser.close()
 print(json.dumps(report))
finally:
    server.shutdown()
