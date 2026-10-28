"""Live typeahead and instant start against the real YouTube Music / yt-dlp.

Isolated local account and database (never Turso). Types a partial name,
checks the suggestions, plays a suggested song and times tap -> 'playing'.
Then lets a search result sit on screen (visible prefetch) and times its tap.
Network dependent: results describe this machine and connection only.
"""
import json
from pathlib import Path
import sys
import threading
import time
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda

onda.init()
onda.resolver.start(warm='jNQXAC9IVRw');onda.provider.music.start()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
QUERY=sys.argv[1] if len(sys.argv)>1 else 'peso plu'
report={'query':QUERY}

def tap_to_playing(page,selector):
    return page.evaluate("""selector => new Promise((done,fail) => {
      const t=performance.now();const timer=setTimeout(()=>fail(new Error('no playing in 20 s')),20000);
      onlineAudio.addEventListener('playing',function on(){if(onlineAudio.getAttribute('src')==='/silence.wav')return;onlineAudio.removeEventListener('playing',on);clearTimeout(timer);done(Math.round(performance.now()-t))});
      document.querySelector(selector).click();
    })""",selector)

try:
    time.sleep(4)  # warm worker pools, as a running server would be
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        context.request.post(base+'/api/register',data={'name':'Live','email':'live@example.test','password':'isolated-live-typeahead'})
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base);page.wait_for_selector('#online-query')
        started=time.perf_counter()
        page.locator('#online-query').press_sequentially(QUERY,delay=90)
        page.wait_for_selector('#suggest .suggest-head:not(.suggest-wait)',timeout=8000)
        report['suggest_visible_ms_after_typing']=round((time.perf_counter()-started)*1000-90*len(QUERY))
        page.wait_for_timeout(1200)  # the user reads the list; deep pass and prefetch may land
        report['suggestions']=page.locator('#suggest .suggest-option').all_inner_texts()[:8]
        song=page.locator('#suggest .suggest-option:has(.suggest-play)').first
        assert song.count(),'No song suggestions'
        page.wait_for_timeout(2500)  # time to read: the top song is prefetched meanwhile
        song.evaluate('e=>e.id="pick-me"')
        report['suggested_song_tap_to_playing_ms']=tap_to_playing(page,'#pick-me')
        report['now_playing']=page.evaluate('audioQueue[audioIndex].title+" · "+audioQueue[audioIndex].channel')
        page.wait_for_function('() => onlineAudio.currentTime>1',timeout=10000)
        # Search results: the third row sits on screen for a few seconds, then is tapped.
        page.locator('#online-query').fill(QUERY);page.locator('#online-search button').click()
        page.wait_for_selector('#online-results .video-row',timeout=15000)
        row=page.locator('#online-results .video-row').nth(2);row.scroll_into_view_if_needed()
        page.wait_for_timeout(5000)
        row.locator('[data-online-play]').evaluate('e=>e.id="row-play"')
        report['visible_row_tap_to_playing_ms']=tap_to_playing(page,'#row-play')
        page.wait_for_function('() => onlineAudio.currentTime>1',timeout=10000)
        report['page_errors']=errors
        browser.close()
    print(json.dumps(report,indent=1))
finally:
    server.shutdown()
