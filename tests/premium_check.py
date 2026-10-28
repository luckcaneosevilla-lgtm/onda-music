"""Premium features in the full app with Chromium audio (providers mocked).

- "Para ti" home: greeting, continue listening, daily mixes, recommendations,
  country chart; tapping a chart song plays the chart in order.
- Offline (Premium): download a song, then reload with the network cut and
  play it from the device. Free accounts get the Premium explanation instead.
- Crossfade: the next song starts before the current one ends, the outgoing
  tail is audible meanwhile, and the listener's volume is restored after.
"""
import io
import json
import math
from pathlib import Path
import struct
import sys
import threading
from unittest.mock import patch
import wave
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda

def tone(seconds,freq):
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000)
        w.writeframes(b''.join(struct.pack('<h',int(8000*math.sin(2*math.pi*freq*i/8000))) for i in range(8000*seconds)))
    return raw.getvalue()
AUDIO={'songAAAAAAA':tone(24,440),'songBBBBBBB':tone(24,660)}
def song(vid,title,artist,duration=24):
    return {'id':'piped:'+vid,'provider':'piped','provider_id':vid,'title':title,'channel':artist,'artists':[artist],'source':'YouTube Music','thumbnail':'','duration':duration,'available':True}
A,B=song('songAAAAAAA','Primera','Feid'),song('songBBBBBBB','Segunda','Karol G')
CHART=[song('chartSong0'+str(i),'Top '+str(i),'Artista '+str(i)) for i in range(1,4)]

onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
report=[]

def account(browser,email,premium):
    ctx=browser.new_context()
    ctx.request.post(base+'/api/register',data={'name':'Lucas Test','email':email,'password':'isolated-premium-tests'})
    if premium:
        with onda.db() as c:c.execute("UPDATE users SET plan='premium',expires=NULL WHERE email=?",(email,));c.commit()
    state=ctx.storage_state();ctx.close();return state

def open_app(browser,state,library):
    ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,storage_state=state)
    page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    ctx.route('**/api/audio/prefetch',lambda r:r.fulfill(status=202,json={}))
    def audio(route):
        vid=route.request.url.split('/api/audio/')[1].split('?')[0]
        route.fulfill(body=AUDIO.get(vid,AUDIO['songAAAAAAA']),content_type='audio/wav',headers={'Accept-Ranges':'bytes'})
    ctx.route('**/api/audio/*',audio)
    ctx.route('**/api/online/status',lambda r:r.fulfill(json={'configured':True,'source':'piped','piped':False,'allow_youtube_external':False}))
    ctx.route('**/api/online/library',lambda r:r.fulfill(json=library))
    ctx.route('**/api/online/charts',lambda r:r.fulfill(json={'country':'Chile','songs':CHART,'artists':[{'id':'UCbBaYg2UToDaoOwo-R6xi4g','name':'Anuel AA','thumbnail':''}]}))
    ctx.route('**/api/online/radio/**',lambda r:r.fulfill(json={'items':[B,*CHART]}))
    ctx.route('**/api/online/items/**',lambda r:r.fulfill(json={}))
    page.goto(base);page.wait_for_function("() => typeof startOnline === 'function' && me !== null")
    return ctx,page,errors

def playing(page,vid,timeout=8000):
    page.wait_for_function("vid => audioQueue[audioIndex]?.provider_id===vid && !onlineAudio.paused && onlineAudio.currentTime>0.1",arg=vid,timeout=timeout)

library={'saves':[{'video_id':A['id'],'favorite':1,'later':0,'last_played':1_900_000_000},{'video_id':B['id'],'favorite':1,'later':0,'last_played':1_800_000_000}],'playlists':[],'items':{A['id']:A,B['id']:B},'recent':[]}
try:
 with patch.object(onda.resolver,'prefetch_many'),sync_playwright() as p:
  browser=p.chromium.launch()
  premium=account(browser,'premium@example.test',True);free=account(browser,'free@example.test',False)

  print('Checking home',flush=True)
  ctx,page,errors=open_app(browser,premium,library)
  page.wait_for_selector('.home-greeting');page.wait_for_selector('[data-home-play^="charts:"]')
  text=page.locator('.home').inner_text()
  for expected in ['Lucas','Seguir escuchando','Tus mixes del día','Mix de Feid','Recomendado para ti','Lo más escuchado hoy en Chile','Tus artistas','Artistas del momento']:
      assert expected in text,(expected,text[:500])
  page.locator('[data-home-play="charts:1"]').click();playing(page,'chartSong02')
  assert page.evaluate('audioQueue.slice(0,3).map(v=>v.provider_id).join()')=='chartSong01,chartSong02,chartSong03'
  page.locator('[data-home-mix="0"]').click();page.wait_for_function("() => ['songAAAAAAA','songBBBBBBB'].includes(audioQueue[audioIndex]?.provider_id) && !onlineAudio.paused")
  assert not errors,errors
  report.append({'case':'home','status':'passed'});ctx.close()

  print('Checking offline-free-upsell',flush=True)
  ctx,page,errors=open_app(browser,free,library)
  page.evaluate('v => onlineOptions(v)',A);page.locator('#offline-toggle').click()
  assert 'Premium' in page.locator('#dialog').inner_text()
  assert not errors,errors
  report.append({'case':'offline-free-upsell','status':'passed'});ctx.close()

  print('Checking offline-download-and-play-without-network',flush=True)
  ctx,page,errors=open_app(browser,premium,library)
  page.evaluate('() => navigator.serviceWorker.ready')
  page.evaluate('v => onlineOptions(v)',A);page.locator('#offline-toggle').click()
  page.wait_for_function("() => offline.has('piped:songAAAAAAA') && !offline.running",timeout=10000)
  page.locator('nav a[href="/library"]').first.click();page.locator('[data-section="downloads"]').click()
  assert 'Primera' in page.locator('#online-library-content').inner_text()
  ctx.set_offline(True);page.reload()
  page.wait_for_selector('.offline-page',timeout=10000)
  page.locator('[data-offline-play="0"]').click();playing(page,'songAAAAAAA')
  assert page.evaluate("onlineAudio.getAttribute('src').startsWith('blob:')")
  ctx.set_offline(False)
  page.wait_for_function("() => !me.offline",timeout=20000)
  assert not errors,errors
  report.append({'case':'offline-download-and-play-without-network','status':'passed'});ctx.close()

  print('Checking crossfade',flush=True)
  ctx,page,errors=open_app(browser,premium,library)
  page.evaluate("() => {localStorage.setItem('ivox-crossfade','3');window.sawTail=false;window.minVolume=1;onlineAudio.volume=.8;onlineAudio.addEventListener('volumechange',()=>{minVolume=Math.min(minVolume,onlineAudio.volume)})}")
  page.reload();page.wait_for_function("() => typeof crossfade === 'object' && me !== null")
  page.evaluate("() => {onlineAudio.volume=.8;window.minVolume=1;onlineAudio.addEventListener('volumechange',()=>{minVolume=Math.min(minVolume,onlineAudio.volume)})}")
  page.locator('#sheet-crossfade').evaluate('e=>e.click()')  # 3 -> 6 s
  page.locator('#sheet-crossfade').evaluate('e=>e.click()')  # 6 -> 10 s
  for _ in range(3):page.locator('#sheet-crossfade').evaluate('e=>e.click()')  # back to 3 s
  assert page.evaluate('crossfade.seconds')==3,page.evaluate('crossfade.seconds')
  page.evaluate('list => playList(list)',[A,B]);playing(page,'songAAAAAAA')
  page.evaluate('() => {onlineAudio.currentTime=14}')
  page.wait_for_function("() => crossfade.active",timeout=12000)
  playing(page,'songBBBBBBB',timeout=8000)
  page.wait_for_function("() => !crossfade.active",timeout=12000)
  final=page.evaluate("({volume:onlineAudio.volume,min:minVolume,slider:Number(document.querySelector('#np-volume-range').value),time:onlineAudio.currentTime})")
  assert abs(final['volume']-.8)<.01 and final['min']<.3 and final['slider']==80,final
  assert not errors,errors
  report.append({'case':'crossfade','status':'passed'});ctx.close()
  browser.close()
 print(json.dumps(report))
finally:
    server.shutdown()
