"""Live browser check of the new player: artist profile, real click-to-sound latency,
rapid taps, automatic skip on errors, autoplay radio, synced lyrics, likes, queue
reordering and the Now Playing sheet. Needs network, Playwright and Chromium.
Report and screenshots: test-results/player-features-*.
"""
import json
import os
from pathlib import Path
import secrets
import statistics
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as onda
from playwright.sync_api import sync_playwright

out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
temp=tempfile.TemporaryDirectory();onda.DATA=Path(temp.name);onda.TURSO=None;onda.init()
# Same warm-up as `python server.py serve`.
onda.resolver.start(warm='jNQXAC9IVRw');onda.provider.music.start()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}';report={'errors':[],'checks':{}}
def check(name,value,detail=None):
    report['checks'][name]={'ok':bool(value),**({'detail':detail} if detail is not None else {})}
    print(('OK  ' if value else 'FAIL'),name,detail if detail is not None else '',flush=True)
# Click → 'playing' event, measured inside the page.
PLAY_TIMER='''id=>new Promise(resolve=>{const t=performance.now();const done=()=>{onlineAudio.removeEventListener('playing',done);resolve(Math.round(performance.now()-t))};onlineAudio.addEventListener('playing',done);document.querySelector(`[data-online-play="${id}"]`).click();setTimeout(()=>resolve(-1),20000)})'''
for _ in range(100):
    if len(onda.resolver.idle)>=3:break
    time.sleep(.2)
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'},args=['--autoplay-policy=no-user-gesture-required'])
        context=browser.new_context(viewport={'width':1440,'height':960});page=context.new_page()
        page.on('pageerror',lambda e:report['errors'].append(str(e)))
        page.goto(url);page.get_by_role('button',name='Crear una cuenta',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Prueba reproductor');page.get_by_label('Email',exact=True).fill('player@example.com');page.get_by_label('Contraseña',exact=True).fill(secrets.token_urlsafe(24))
        page.get_by_role('button',name='Crear cuenta',exact=True).click();page.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for()

        # 1. Artist search: profile first, then popular songs, then the song results.
        timings={}
        for q in ('Olivia Dean','Feid'):
            t=time.perf_counter();page.get_by_role('searchbox',name='Buscar música online',exact=True).fill(q);page.get_by_role('button',name='Buscar',exact=True).click()
            page.locator('#online-results .video-row').first.wait_for(timeout=30000);songs=time.perf_counter()-t
            page.locator('.artist-hero:not(.skeleton) h2').wait_for(timeout=30000);profile=time.perf_counter()-t
            timings[q]={'songs_s':round(songs,2),'profile_s':round(profile,2)}
            hero=page.evaluate('''()=>{const h=document.querySelector('.artist-hero:not(.skeleton)');return {name:h.querySelector('h2').textContent,stats:h.querySelector('.artist-stats')?.textContent,avatar:!!h.querySelector('img.artist-avatar')?.naturalWidth,banner:!!h.querySelector('img.artist-banner'),top:document.querySelectorAll('.top-songs .video-row').length,related:document.querySelectorAll('.related-artist').length,heroFirst:document.querySelector('#search-results').firstElementChild.classList.contains('artist-hero')}}''')
            check(f'artist profile «{q}»',hero['name'].lower()==q.lower() and 'oyentes mensuales' in (hero['stats'] or '') and hero['top']>=3 and hero['heroFirst'],{**hero,**timings[q]})
            page.wait_for_timeout(600);page.screenshot(path=str(out/f'player-features-artist-{q.split()[0].lower()}.png'))
        page.get_by_role('searchbox',name='Buscar música online',exact=True).fill('despacito');page.get_by_role('button',name='Buscar',exact=True).click()
        page.locator('#online-results .video-row').first.wait_for(timeout=30000);page.wait_for_timeout(2500)
        check('no profile for a song query',page.locator('.artist-hero').count()==0)

        # 2. Real click-to-sound for songs nobody prefetched (deep in the list) and for prefetched ones (top 5).
        ids=page.evaluate('()=>searchView.items.map(v=>v.id)')
        cold=[];warm=[]
        for i in (12,15,18):
            page.locator(f'[data-online-play="{ids[i]}"]').scroll_into_view_if_needed()
            cold.append(page.evaluate(PLAY_TIMER,ids[i]));page.wait_for_timeout(800)
        page.wait_for_timeout(3000)
        for i in (1,2,3):
            warm.append(page.evaluate(PLAY_TIMER,ids[i]));page.wait_for_timeout(800)
        report['latency_ms']={'new_song_not_prefetched':cold,'prefetched_top5':warm}
        check('new song plays (not prefetched)',all(v>0 for v in cold),cold)
        check('prefetched song under 1.5 s',all(0<v<1500 for v in warm),warm)

        # 3. Rapid taps: 6 quick "next" presses end on one song that really plays.
        page.evaluate('()=>{audioQueue=searchView.items.slice(0,12);audioIndex=0}')
        for _ in range(6):page.locator('#online-next').click(delay=0);page.wait_for_timeout(70)
        page.wait_for_function('()=>!onlineAudio.paused&&onlineAudio.currentTime>0.3',timeout=25000)
        state=page.evaluate('()=>({index:audioIndex,src:onlineAudio.getAttribute("src"),id:audioQueue[audioIndex].provider_id,state:document.querySelector("#online-player").dataset.state})')
        check('rapid taps settle on the last song and play',state['index']==6 and state['src'].endswith(state['id']) and state['state']=='playing',state)

        # 4. Broken audio: the song fails, a message appears and the next one plays by itself.
        bad=page.evaluate('()=>audioQueue[audioIndex+1].provider_id')
        page.route(f'**/api/audio/{bad}',lambda route:route.fulfill(status=502,body='{"error":"audio_unavailable"}',content_type='application/json'))
        page.locator('#online-next').click();page.wait_for_timeout(300)
        page.wait_for_function(f'()=>audioQueue[audioIndex].provider_id!=="{bad}"&&!onlineAudio.paused&&onlineAudio.currentTime>0.2',timeout=30000)
        check('failed song is skipped automatically',True,page.evaluate('()=>document.querySelector("#notice").textContent'))

        # 5. Autoplay radio: playing the last song of the queue appends similar songs.
        page.evaluate('()=>{const v=searchView.items[0];audioQueue=[v];audioIndex=0;radioFor=null;startOnline()}')
        page.wait_for_function('()=>audioQueue.length>=6&&audioQueue.slice(1).every(v=>v.radio)',timeout=30000)
        radio=page.evaluate('()=>audioQueue.slice(1,6).map(v=>v.title+" · "+v.channel)')
        check('autoplay radio fills the queue',len(radio)==5,radio)
        page.evaluate('()=>{onlineAudio.currentTime=Math.max(0,trackDuration()-1.5)}')
        page.wait_for_function('()=>audioIndex===1&&!onlineAudio.paused',timeout=30000)
        check('music continues into the radio song',True,page.evaluate('()=>audioQueue[audioIndex].title'))

        # 6. Now Playing sheet: synced lyrics, like animation, reorder.
        page.evaluate('()=>{const v=searchView.items.find(x=>/olivia|feid/i.test(x.channel))||searchView.items[0]}')
        page.goto(url+'/');page.get_by_role('searchbox',name='Buscar música online',exact=True).fill('Olivia Dean Man I Need');page.get_by_role('button',name='Buscar',exact=True).click()
        page.locator('#online-results [data-online-play]').first.wait_for(timeout=30000);page.locator('#online-results [data-online-play]').first.click()
        page.wait_for_function('()=>!onlineAudio.paused&&onlineAudio.currentTime>0.3',timeout=30000)
        page.locator('#np-expand').click();page.locator('#np-sheet.open').wait_for()
        page.get_by_role('tab',name='Letra').click()
        page.locator('.lyrics-synced p, .lyrics-plain, .lyrics-empty').first.wait_for(timeout=20000)
        synced=page.locator('.lyrics-synced p').count()
        if synced:
            page.evaluate('()=>{const l=lyricLines[Math.min(8,lyricLines.length-1)];onlineAudio.currentTime=l.start/1000+.3}');page.wait_for_timeout(900)
        active=page.evaluate('()=>document.querySelector(".lyrics-synced p.active")?.textContent||null')
        check('synced lyrics follow the song',synced>5 and bool(active),{'lines':synced,'active':active})
        page.screenshot(path=str(out/'player-features-sheet-lyrics.png'))
        page.locator('#sheet-like').click();page.wait_for_function('()=>document.querySelector("#sheet-like").getAttribute("aria-pressed")==="true"')
        check('like from the sheet',page.locator('.heart-layer span').count()>0 and page.locator('#np-like[aria-pressed=true]').count()==1)
        page.get_by_role('tab',name='Cola').click();page.wait_for_function('()=>audioQueue.length>=4',timeout=30000)
        before=page.evaluate('()=>audioQueue.map(v=>v.id)')
        page.locator('[data-grip="2"]').focus();page.keyboard.press('ArrowDown')
        after=page.evaluate('()=>audioQueue.map(v=>v.id)')
        check('queue reorder (keyboard)',after[3]==before[2] and after[2]==before[3])
        grip=page.locator('[data-grip="1"]').bounding_box();page.mouse.move(grip['x']+10,grip['y']+10);page.mouse.down();page.mouse.move(grip['x']+10,grip['y']+10+grip['height']*2.2,steps=8);page.mouse.up()
        dragged=page.evaluate('()=>audioQueue.map(v=>v.id)')
        check('queue reorder (drag)',dragged[3]==after[1],{'moved':after[1],'now_at':dragged.index(after[1])})
        page.screenshot(path=str(out/'player-features-sheet-queue.png'))
        tint=page.evaluate('()=>getComputedStyle(document.documentElement).getPropertyValue("--np-tint")')
        check('colour taken from the cover',tint.strip() not in ('','#2a3f8f'),tint)
        page.keyboard.press('Escape')
        try:page.locator('#np-sheet').wait_for(state='hidden',timeout=3000);closed=True
        except Exception:closed=False
        check('Escape closes the sheet',closed)

        # 7. iPhone-size layout of the profile and the sheet.
        phone=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=3,is_mobile=True,has_touch=True,storage_state=context.storage_state())
        m=phone.new_page();m.on('pageerror',lambda e:report['errors'].append('mobile: '+str(e)));m.goto(url)
        m.get_by_role('searchbox',name='Buscar música online',exact=True).fill('Feid');m.get_by_role('button',name='Buscar',exact=True).click()
        m.locator('.artist-hero:not(.skeleton) h2').wait_for(timeout=30000);m.wait_for_timeout(800)
        width=m.evaluate('()=>document.documentElement.scrollWidth');check('no horizontal scroll on iPhone',width<=390,width)
        m.screenshot(path=str(out/'player-features-iphone-artist.png'))
        m.locator('.top-songs [data-online-play]').first.tap();m.wait_for_function('()=>!onlineAudio.paused',timeout=30000)
        m.locator('#online-player .player-heading').tap();m.locator('#np-sheet.open').wait_for();m.wait_for_timeout(700)
        m.screenshot(path=str(out/'player-features-iphone-sheet.png'))
        browser.close()
finally:
    server.shutdown();server.server_close()
check('no JavaScript errors',not report['errors'],report['errors'])
(out/'player-features-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
failed=[k for k,v in report['checks'].items() if not v['ok']]
print('\nLatency (ms):',json.dumps(report.get('latency_ms')));print('FAILED:' if failed else 'ALL CHECKS PASSED',failed or '')
sys.exit(1 if failed else 0)
