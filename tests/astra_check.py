"""Tu Astra (wrapped), sleep timer, repeat and song sharing in a real browser.

Plays are recorded through the real PATCH played endpoint against catalogue
rows inserted locally; no provider network is used.
"""
import io
import json
from pathlib import Path
import sys
import threading
import time
import wave
from unittest.mock import patch
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from test_security import onda, temporary

OUT=Path(__file__).parent.parent/'test-results'/'astra';OUT.mkdir(parents=True,exist_ok=True)


def audio(seconds):
    data=io.BytesIO()
    with wave.open(data,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\0\0'*8000*seconds)
    return data.getvalue()


SONGS=[('aaaaaaaaaa1','Ojitos Lindos','Bad Bunny',26),('aaaaaaaaaa2','Tití Me Preguntó','Bad Bunny',24),
       ('aaaaaaaaaa3','Provenza','KAROL G',21),('aaaaaaaaaa4','La Bachata','Manuel Turizo',16),('aaaaaaaaaa5','Hey Mor','Ozuna, Feid',20)]
onda.init()
with onda.db() as c:
    for vid,title,channel,length in SONGS:
        item={'id':'piped:'+vid,'provider':'piped','provider_id':vid,'source':'Piped','title':title,'channel':channel,'duration':length,'thumbnail':''}
        c.execute('INSERT OR REPLACE INTO online_catalog VALUES(?,?,?)',(item['id'],json.dumps(item),onda.now()))
    c.commit()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
report=[]
try:
 with patch.object(onda.resolver,'prefetch_many'),patch.object(onda.resolver,'duration',return_value=None),sync_playwright() as p:
  browser=p.chromium.launch()
  context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
  r=context.request.post(origin+'/api/register',data={'name':'Astra QA','email':'astra@example.test','password':'isolated-astra-regression'})
  assert r.ok,r.status
  csrf=r.json()['csrf']
  # Bad Bunny twice as often: he must be the top artist.
  plan=['aaaaaaaaaa1','aaaaaaaaaa1','aaaaaaaaaa2','aaaaaaaaaa2','aaaaaaaaaa3','aaaaaaaaaa4','aaaaaaaaaa5','aaaaaaaaaa1']
  with onda.db() as c:pass
  for i,vid in enumerate(plan):
   r=context.request.patch(origin+'/api/online/items/piped:'+vid,data={'played':True},headers={'X-CSRF-Token':csrf})
   assert r.ok,(r.status,r.text())
   # Spread the plays over three days (one minute apart would be merged).
   with onda.db() as c:
    c.execute('UPDATE online_plays SET played=played-? WHERE rowid=(SELECT MAX(rowid) FROM online_plays)',(86400*(i%3)+120*(i+1),));c.commit()
  w=context.request.get(origin+'/api/online/wrapped?period=month&tz=60').json()
  assert w['plays']==8 and w['songs']==5 and w['top_artists'][0]['name']=='Bad Bunny' and w['top_songs'][0]['title']=='Ojitos Lindos',w
  assert w['streak']>=2 and w['peak_hour'] is not None and w['minutes']>0,w
  # A reconnect within a minute is the same listen.
  context.request.patch(origin+'/api/online/items/piped:aaaaaaaaaa3',data={'played':True},headers={'X-CSRF-Token':csrf})
  context.request.patch(origin+'/api/online/items/piped:aaaaaaaaaa3',data={'played':True},headers={'X-CSRF-Token':csrf})
  assert context.request.get(origin+'/api/online/wrapped?period=all').json()['plays']==9
  report.append({'case':'wrapped-api','status':'passed'})

  page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  def media(route):
   url=route.request.url
   if 'prefetch' in url:return route.fulfill(status=202,json={})
   body=audio(next((l for v,_,_,l in SONGS if v in url),30));rng=route.request.headers.get('range')
   if rng:
    start,_,end=rng.split('=')[1].partition('-');start=int(start or 0);end=int(end) if end else len(body)-1
    return route.fulfill(status=206,body=body[start:end+1],headers={'Content-Type':'audio/wav','Accept-Ranges':'bytes','Content-Range':f'bytes {start}-{end}/{len(body)}'})
   route.fulfill(body=body,headers={'Content-Type':'audio/wav','Accept-Ranges':'bytes'})
  context.route('**/api/audio/**',media)
  page.goto(origin+'/astra');page.wait_for_function('() => typeof playList==="function" && me')
  release=page.locator('dialog[open] .release-start')
  if release.count():release.click()
  page.wait_for_selector('#astra-open')
  assert page.locator('.sidebar nav a[href="/astra"]').count()==1
  page.screenshot(path=str(OUT/'page-mobile.png'),full_page=True)
  page.click('#astra-open');page.wait_for_selector('#astra-story .s-intro')
  page.screenshot(path=str(OUT/'story-1.png'))
  names=[]
  for n in range(6):
   page.click('.astra-next',position={'x':200,'y':300});page.wait_for_timeout(700)
   names.append(page.evaluate("document.querySelector('.astra-slide').className"))
   page.screenshot(path=str(OUT/f'story-{n+2}.png'))
  assert names[-1].endswith('s-final'),names
  assert 'Bad Bunny' in page.locator('#astra-story').inner_text()
  page.evaluate("() => {window.shared=null;navigator.canShare=()=>true;navigator.share=d=>{window.shared={files:d.files.length,type:d.files[0].type,size:d.files[0].size};return Promise.resolve()}}")
  page.click('[data-story-share]');page.wait_for_function('() => window.shared')
  shared=page.evaluate('window.shared');assert shared['type']=='image/png' and shared['size']>50000,shared
  # Save the generated image for a visual check.
  import base64
  png=page.evaluate("async () => {const b=await (async()=>{let blob;navigator.share=d=>{blob=d.files[0];return Promise.resolve()};document.querySelector('[data-story-share]').click();while(!blob)await new Promise(r=>setTimeout(r,50));return blob})();const r=new FileReader();return await new Promise(ok=>{r.onload=()=>ok(r.result.split(',')[1]);r.readAsDataURL(b)})}")
  (OUT/'share-card.png').write_bytes(base64.b64decode(png))
  page.click('[data-story-play]');page.wait_for_function('() => !document.querySelector("#astra-story") && audioQueue.length===5')
  report.append({'case':'stories-share-play','status':'passed'})

  # Repeat one: the end of the song starts it again instead of the next one.
  page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1',timeout=10000)
  page.wait_for_function('() => onlineAudio.buffered.length && onlineAudio.buffered.end(0)>=onlineAudio.duration-.5',timeout=8000)
  page.evaluate("() => {IVoxRepeat.mode='one';window.loopFrom=onlineAudio.currentTime;onlineAudio.currentTime=onlineAudio.duration-.3}")
  page.wait_for_function('() => audioIndex===0 && onlineAudio.currentTime<5 && onlineAudio.currentTime>0 && !onlineAudio.paused',timeout=8000)
  # Repeat all: Next on the last song goes back to the first.
  page.evaluate("() => {IVoxRepeat.mode='all';audioIndex=audioQueue.length-1;nextTrack()}")
  page.wait_for_function('() => audioIndex===0',timeout=3000)
  page.evaluate("() => {IVoxRepeat.mode='off'}")
  report.append({'case':'repeat','status':'passed'})

  # Sleep timer: chip label, then a deadline in the past pauses playback.
  page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1',timeout=10000)
  page.evaluate("() => nowPlaying.open()");page.click('#sheet-sleep');page.click('[data-sleep="15"]')
  assert 'Pausa en 15 min' in page.locator('#sheet-sleep').inner_text()
  page.screenshot(path=str(OUT/'sheet-chips.png'))
  page.evaluate("() => IVoxSleep.set(0.0005)");page.wait_for_function('() => onlineAudio.paused && !playbackWanted',timeout=5000)
  assert page.locator('#sheet-sleep').get_attribute('aria-pressed')=='false'
  # End of song: the queue does not move on.
  page.evaluate("() => {toggleOnline()}");page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>.1',timeout=8000)
  page.wait_for_function('() => onlineAudio.buffered.length && onlineAudio.buffered.end(0)>=onlineAudio.duration-.5',timeout=8000)
  page.evaluate("() => {IVoxSleep.set('end');onlineAudio.currentTime=onlineAudio.duration-.3}")
  try:page.wait_for_function('() => onlineAudio.paused && !playbackWanted',timeout=8000)
  except Exception:
   print(page.evaluate('({i:audioIndex,t:onlineAudio.currentTime,d:onlineAudio.duration,p:onlineAudio.paused,w:playbackWanted,s:IVoxSleep.state,st:document.querySelector("#playback-status").textContent,ended:onlineAudio.ended})'),flush=True);raise
  assert page.evaluate('audioIndex')==0
  report.append({'case':'sleep-timer','status':'passed'})

  # Share a song, then open its link in a fresh page.
  page.evaluate("() => {navigator.share=d=>{window.sharedSong=d.url;return Promise.resolve()}}")
  page.click('#sheet-share');page.wait_for_function('() => window.sharedSong')
  url=page.evaluate('window.sharedSong');assert '/?song=piped%3Aaaaaaaaaaa1' in url,url
  other=context.new_page();other.goto(url.replace('http://'+url.split('/')[2],origin))
  other.wait_for_selector('#shared-play',timeout=8000)
  assert 'Ojitos Lindos' in other.locator('#dialog').inner_text() and 'song=' not in other.url
  other.screenshot(path=str(OUT/'shared-song.png'))
  report.append({'case':'share-song','status':'passed'})

  # Clearing history also clears Tu Astra.
  r=context.request.delete(origin+'/api/online/history',headers={'X-CSRF-Token':csrf});assert r.ok
  assert context.request.get(origin+'/api/online/wrapped?period=all').json()['plays']==0
  report.append({'case':'history-clears-astra','status':'passed'})
  assert not errors,errors
  browser.close()
finally:
 server.shutdown()
print(json.dumps(report))
