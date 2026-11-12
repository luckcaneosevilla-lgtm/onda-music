"""Local audio/queue smoke checks in Chromium and WebKit, with no music providers.

WebKit on Windows is engine coverage, not a physical iPhone background test.
"""
import io
import json
import re
from pathlib import Path
import sys
import threading
import wave
from unittest.mock import patch

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from test_security import onda, temporary


def wav(seconds):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000 * seconds)
    return data.getvalue()


def media(route):
    body = wav(2 if 'short000001' in route.request.url else 60)
    match = re.fullmatch(r'bytes=(\d+)-(\d*)', route.request.headers.get('range', ''))
    start = int(match[1]) if match else 0
    end = min(int(match[2]), len(body)-1) if match and match[2] else len(body)-1
    headers = {'Accept-Ranges': 'bytes'}
    if match:
        headers['Content-Range'] = f'bytes {start}-{end}/{len(body)}'
    route.fulfill(status=206 if match else 200, body=body[start:end+1], headers=headers, content_type='audio/wav')


onda.init()
server = onda.OndaHTTPServer(('127.0.0.1', 0), onda.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{server.server_port}'
report = []
try:
    with patch.object(onda.resolver, 'prefetch_many'), sync_playwright() as p:
        for engine in ['chromium', 'webkit']:
            browser = getattr(p, engine).launch()
            probe = browser.new_page()
            probe.route('**/engine-probe.wav', lambda r: r.fulfill(body=wav(3), content_type='audio/wav'))
            probe.set_content('<audio id="probe" src="https://ivox.test/engine-probe.wav"></audio><button onclick="probe.play().catch(()=>{})">Play</button>')
            probe.locator('button').click()
            probe.wait_for_function('() => probe.currentTime>.05 || !!probe.error', timeout=10000)
            decoder_error = probe.evaluate('probe.error?.code || 0')
            assert decoder_error in (0, 4), f'Unexpected media probe error: {decoder_error}'
            probe.close()
            auth = browser.new_context()
            response = auth.request.post(origin + '/api/register', data={
                'name': 'Engine QA', 'email': engine + '@example.test',
                'password': 'isolated-engine-regression',
            })
            assert response.ok, response.text()
            state = auth.storage_state()
            auth.close()
            for width in [390, 1440]:
                print(f'Checking {engine} {width}px', flush=True)
                context = browser.new_context(storage_state=state, viewport={'width': width, 'height': 900})
                context.route('**/api/online/status', lambda r: r.fulfill(json={'configured': True, 'source': 'piped', 'piped': False}))
                context.route('**/api/online/charts', lambda r: r.fulfill(json={'songs': [], 'artists': []}))
                context.route('**/api/online/recommendations*', lambda r: r.fulfill(json={'items': []}))
                context.route('**/api/audio/prefetch', lambda r: r.fulfill(status=202, json={}))
                context.route('**/api/online/items/**', lambda r: r.fulfill(json={}))
                context.route('**/api/audio/*', media)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script("new MutationObserver(()=>document.querySelectorAll('dialog.release-dialog[open]').forEach(d=>d.close())).observe(document,{subtree:true,attributes:true,childList:true})")
                page.goto(origin)
                page.wait_for_function('() => !!me && onlineLoaded')
                page.evaluate("""() => {
                    localStorage.setItem('ivox-crossfade','0');
                    setAutoplay(false);prefetchNext=()=>{};
                    window.song=id=>({id:'piped:'+id,provider_id:id,provider:'piped',title:'Engine fixture',duration:id==='short000001'?2:60});
                    audioQueue=[song('first000001'),song('next0000001')];audioIndex=0;
                    document.querySelector('#online-player').hidden=false;
                }""")
                if decoder_error:
                    # Windows WebKit can advertise WAV support while its native
                    # media backend rejects even a standalone, valid local WAV.
                    page.evaluate('() => {showTrack(audioQueue[0]);nowPlaying.open()}')
                    page.wait_for_function('() => nowPlaying.isOpen()')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors, errors
                    report.append({'engine': engine, 'width': width, 'status': 'partial', 'checks': ['app-load', 'queue', 'full-player', 'horizontal-fit'], 'media': 'unsupported by host decoder (standalone WAV error 4)'})
                    context.close()
                    continue
                page.locator('#np-toggle').click()
                try:
                    page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime > .1', timeout=10000)
                except Exception:
                    print(page.evaluate("() => ({paused:onlineAudio.paused,ready:onlineAudio.readyState,error:onlineAudio.error?.code,source:onlineAudio.getAttribute('src'),status:document.querySelector('#playback-status').textContent,wav:onlineAudio.canPlayType('audio/wav')})"), flush=True)
                    print(errors, flush=True)
                    raise
                page.locator('#np-toggle').click()
                assert page.evaluate('onlineAudio.paused && !playbackWanted')
                page.evaluate('() => IVoxPlayerControls.seek(20)')
                page.wait_for_function('() => onlineAudio.currentTime >= 19 && !onlineAudio.seeking')
                page.locator('#np-toggle').click()
                page.locator('#online-next').click()
                page.wait_for_function("() => audioQueue[audioIndex].provider_id==='next0000001' && !onlineAudio.paused && onlineAudio.currentTime>.1")
                page.evaluate("() => playList([song('short000001'),song('next0000001')])")
                page.wait_for_function("() => audioIndex===1 && !onlineAudio.paused && onlineAudio.currentTime>.1", timeout=10000)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflows horizontally'
                assert not errors, errors
                report.append({'engine': engine, 'width': width, 'status': 'passed', 'checks': ['play', 'pause', 'seek', 'next', 'natural-end', 'horizontal-fit']})
                context.close()
            browser.close()
    out = Path(__file__).resolve().parents[1] / 'test-results'
    out.mkdir(exist_ok=True)
    (out / 'cross-browser-player-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
finally:
    server.shutdown()
    server.server_close()
    temporary.cleanup()
