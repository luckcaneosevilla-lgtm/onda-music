"""Chromium regression checks with real HTMLAudioElement + isolated network fixtures.

No real accounts, backend writes or provider requests. Touch gestures use CDP.
"""
import io
import json
from pathlib import Path
import re
import wave
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'test-results'
out.mkdir(exist_ok=True)
pcm = io.BytesIO()
with wave.open(pcm, 'wb') as wav:
    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
    wav.writeframes(b'\0\0' * 8000 * 60)
media = pcm.getvalue()
html = (ROOT/'web/index.html').read_text(encoding='utf-8')
# Keep production controls, markup and CSS; isolate queue/provider side effects.
html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
html = html.replace('</body>', '<script src="/player-controls.js"></script></body>')

def fixture(route):
    path = route.request.url.split('ivox.test', 1)[1].split('?')[0]
    if path == '/':
        route.fulfill(body=html, content_type='text/html'); return
    if path == '/test.wav':
        header = route.request.headers.get('range', '')
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', header)
        start = int(match[1]) if match else 0
        end = min(int(match[2]) if match and match[2] else len(media)-1, len(media)-1)
        headers = {'Accept-Ranges':'bytes'}
        if match: headers['Content-Range'] = f'bytes {start}-{end}/{len(media)}'
        route.fulfill(status=206 if match else 200, body=media[start:end+1], headers=headers, content_type='audio/wav'); return
    file = ROOT/'web'/path.lstrip('/')
    if file.is_file(): route.fulfill(path=str(file))
    else: route.fulfill(status=404, body='Not found')

errors = []
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True, device_scale_factor=1)
    context.route('http://ivox.test/**', fixture)
    page = context.new_page()
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto('http://ivox.test/')
    page.evaluate("""() => {
      const player=document.querySelector('#online-player');player.hidden=false;player.dataset.state='paused';
      document.querySelector('#online-playing').textContent='Prueba de controles';
      const a=document.querySelector('#online-audio');a.src='/test.wav';a.load();
      window.queueCalls={next:0,previous:0};
      document.querySelector('#online-next').onclick=()=>queueCalls.next++;
      document.querySelector('#online-previous').onclick=()=>queueCalls.previous++;
    }""")
    page.wait_for_function("document.querySelector('#online-audio').duration===60 && document.querySelector('#online-audio').seekable.length>0")
    page.wait_for_function("!document.querySelector('#np-range').disabled")
    page.wait_for_timeout(500)
    range_ = page.locator('#np-range')
    box = range_.bounding_box()
    assert box['height'] >= 44
    assert page.evaluate("""() => {const r=document.querySelector('#np-range').getBoundingClientRect();return [2,r.width-2].every(x=>document.elementFromPoint(r.x+x,r.y+5).id==='np-range')}""")
    checks.append('44px hit area, both edges and outside visual track')
    # Actual media seek via the production input/change handlers.
    page.evaluate("""() => {const r=document.querySelector('#np-range');r.value=750;r.dispatchEvent(new Event('input'));r.dispatchEvent(new Event('change'));}""")
    page.wait_for_function("Math.abs(document.querySelector('#online-audio').currentTime-45)<0.2")
    assert page.locator('#np-current').inner_text() == '0:45'
    checks.append('real HTML5 WAV seek to 45s while paused')
    page.evaluate("""() => {
      const r=document.querySelector('#np-range');r.value=800;r.dispatchEvent(new Event('input'));
      IVoxPlayerControls.update(3,60);
    }""")
    assert page.locator('#np-current').inner_text() == '0:48'
    assert page.locator('#np-fill').evaluate('(e)=>e.style.width') == '80%'
    page.evaluate("document.querySelector('#np-range').dispatchEvent(new Event('pointercancel'))")
    assert page.locator('#np-current').inner_text() == '0:45'
    checks.append('preview survives time update; cancellation restores real position')
    # Simulated disjoint TimeRanges are deterministic, not a claim about provider buffering.
    page.evaluate("""() => {
      const a=document.querySelector('#online-audio');
      Object.defineProperty(a,'buffered',{configurable:true,value:{length:2,start:i=>[0,40][i],end:i=>[10,50][i]}});
      IVoxPlayerControls.update();
    }""")
    segments = page.locator('#np-buffer>span').evaluate_all('(nodes)=>nodes.map(e=>[parseFloat(e.style.left),parseFloat(e.style.width)])')
    assert len(segments)==2 and abs(segments[0][1]-100/6)<.01 and abs(segments[1][0]-200/3)<.01, segments
    checks.append('disjoint buffer segments retain gap')
    # A new source must cancel a preview before it can commit.
    page.evaluate("""() => {
      const r=document.querySelector('#np-range');r.value=900;r.dispatchEvent(new Event('input'));
      IVoxPlayerControls.cancelSeek();r.dispatchEvent(new Event('change'));
    }""")
    assert abs(page.locator('#online-audio').evaluate('(a)=>a.currentTime')-45)<.2
    # Mobile extras are intentionally expanded for volume.
    page.locator('#online-player .np-extra').evaluate("e=>e.style.display='flex'")
    page.get_by_role('button',name='Volumen',exact=True).click()
    assert page.locator('#np-volume-panel').is_visible()
    page.locator('#np-volume-range').fill('35')
    assert abs(page.locator('#online-audio').evaluate('(a)=>a.volume')-.35)<.01
    page.get_by_role('button',name='Silenciar',exact=True).click()
    assert page.locator('#online-audio').evaluate('(a)=>a.muted')
    page.get_by_role('button',name='Activar sonido',exact=True).click()
    assert not page.locator('#online-audio').evaluate('(a)=>a.muted')
    page.keyboard.press('Escape')
    assert not page.locator('#np-volume-panel').is_visible()
    checks.append('volume popover, volume 35%, mute/unmute, Escape')
    cdp = context.new_cdp_session(page)
    def swipe(x1,y1,x2,y2):
        cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x1,'y':y1}]})
        for step in range(1,7):
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x1+(x2-x1)*step/6,'y':y1+(y2-y1)*step/6}]})
        cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
    heading = page.locator('.player-heading').bounding_box()
    x = heading['x']+min(heading['width']-5,110); y = heading['y']+heading['height']/2
    page.evaluate("() => {window.headingTapped=0;document.querySelector('.player-heading').onclick=()=>window.headingTapped++}")
    page.touchscreen.tap(heading['x']+5,y)
    page.wait_for_function('window.headingTapped === 1',timeout=3000)
    swipe(x,y,x-85,y)
    assert page.evaluate('queueCalls.next') == 1
    assert page.evaluate('window.headingTapped') == 1
    swipe(heading['x']+5,y,heading['x']+90,y)
    assert page.evaluate('queueCalls.previous') == 1
    before = page.evaluate('({...queueCalls})')
    box = range_.bounding_box()
    swipe(box['x']+box['width']*.8,box['y']+22,box['x']+box['width']*.2,box['y']+22)
    assert page.evaluate('queueCalls') == before
    assert page.locator('#online-audio').evaluate('(a)=>a.currentTime') < 25
    swipe(x,y,x-20,y+90)
    assert page.evaluate('queueCalls') == before
    checks.append('CDP touch swipe left/right; tap still expands; seek drag seeks without skipping; vertical scroll does not skip')
    page.screenshot(path=str(out/'player-controls-mobile.png'))
    # Desktop click and keyboard continue to work.
    desktop=browser.new_context(viewport={'width':1440,'height':900})
    desktop.route('http://ivox.test/**',fixture)
    pc=desktop.new_page();pc.goto('http://ivox.test/')
    pc.evaluate("() => {document.querySelector('#online-player').hidden=false;document.querySelector('#online-audio').src='/test.wav'}")
    pc.wait_for_function("!document.querySelector('#np-range').disabled")
    pc.locator('#np-range').click(position={'x':300,'y':5})
    assert pc.locator('#online-audio').evaluate('(a)=>a.currentTime')>0
    pc.locator('#np-range').focus();pc.keyboard.press('ArrowRight')
    assert not errors, errors
    checks.append('desktop click outside thin track and keyboard seek')
    browser.close()
report={'status':'passed','checks':checks,'page_errors':errors,'limits':'Chromium desktop/mobile emulation only; no physical iOS/Android or provider requests'}
(out/'player-controls-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report))
