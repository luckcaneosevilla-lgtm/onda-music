"""Installed-iPhone simulation: isolated storage, real SW, reconnect and upgrades.

Runs a local disposable server. It does not install iOS or simulate its OS media
suspension; Chromium provides decoding, WebKit provides the Safari engine.
"""
import io
import json
import re
from pathlib import Path
import sys
import threading
import wave
from urllib.parse import urlparse
from unittest.mock import patch

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from test_security import onda, temporary

state = {'me_failure': False, 'slow_install': False, 'install_failure': False}
install_gate = threading.Event()
install_started = threading.Event()


def audio(seconds):
    data = io.BytesIO()
    with wave.open(data, 'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000)
        f.writeframes(b'\0\0' * 8000 * seconds)
    return data.getvalue()


class Fixture(onda.Handler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/me' and state['me_failure']:
            self.send(503, {'error': 'Red temporalmente no disponible'}); return
        if path == '/api/online/status':
            self.send(200, {'configured': True, 'source': 'piped', 'piped': False}); return
        if path == '/api/online/charts':
            self.send(200, {'songs': [], 'artists': []}); return
        if path.startswith('/api/audio/') and path.split('/')[-1] in ('short000001', 'next0000001'):
            body = audio(2 if path.endswith('short000001') else 60)
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
            start = int(match[1]) if match else 0
            end = min(int(match[2]), len(body)-1) if match and match[2] else len(body)-1
            self.send_response(206 if match else 200)
            self.headers_common('audio/wav')
            self.send_header('Accept-Ranges', 'bytes')
            if match: self.send_header('Content-Range', f'bytes {start}-{end}/{len(body)}')
            self.send_header('Content-Length', str(end-start+1));self.end_headers()
            self.wfile.write(body[start:end+1]);return
        if path == '/sheet.js' and state['slow_install']:
            install_started.set()
            install_gate.wait(15)
        if path == '/sheet.js' and state['install_failure']:
            self.send(503, {'error': 'Interrupted shell download'});return
        super().do_GET()


onda.init()
server = onda.OndaHTTPServer(('127.0.0.1', 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{server.server_port}'
original_version = onda.APP_VERSION
report = []
try:
    with patch.object(onda.resolver, 'prefetch_many'), sync_playwright() as p:
        for engine in ['chromium', 'webkit']:
            print('Checking installed iPhone in ' + engine, flush=True)
            browser = getattr(p, engine).launch()
            context = browser.new_context(**p.devices['iPhone 13'])
            context.add_init_script("Object.defineProperty(navigator,'standalone',{get:()=>true});Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5})")
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            signup = browser.new_context()
            auth = signup.request.post(origin + '/api/register', data={
                'name': 'iPhone QA', 'email': engine + '@pwa.test',
                'password': 'isolated-installed-iphone',
            })
            assert auth.ok, auth.text()
            signup.close()
            with onda.db() as c:
                c.execute("UPDATE users SET plan='premium' WHERE email=?", (engine+'@pwa.test',))
                c.commit()
            onda.forget_sessions()
            page.goto(origin)
            page.locator('#auth').wait_for()
            page.get_by_label('Email', exact=True).fill(engine + '@pwa.test')
            page.get_by_label('Contraseña', exact=True).fill('isolated-installed-iphone')
            page.get_by_role('button', name='Entrar', exact=True).click()
            page.locator('#online-search').wait_for()
            page.locator('.release-start').click()
            page.wait_for_function('() => !!navigator.serviceWorker.controller')
            assert page.evaluate('navigator.standalone && navigator.maxTouchPoints > 0')
            # Reopening uses the actual installed app shell, not routed JS mocks.
            page.reload()
            page.locator('#online-search').wait_for()
            assert page.evaluate('!!me'), 'Installed session was lost'
            for width, height in [(320, 568), (375, 667), (390, 844), (430, 932), (844, 390)]:
                page.set_viewport_size({'width': width, 'height': height})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal overflow at {width}px'
            report.append({'engine': engine, 'case': 'installed-shell-session-layout', 'status': 'passed'})
            print('Checking weak-network reopen', flush=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            state['me_failure'] = True
            page.reload()
            page.locator('#startup-retry').wait_for(timeout=5000)
            assert page.locator('#auth').count() == 0, 'Transient network failure asked for a new login'
            page.screenshot(animations='disabled', path=str(Path(__file__).resolve().parents[1] / 'test-results' / f'iphone-pwa-{engine}-reconnect.png'))
            state['me_failure'] = False
            page.locator('#startup-retry').click()
            page.locator('#online-search').wait_for()
            report.append({'engine': engine, 'case': 'weak-network-reopen-retry', 'status': 'passed'})
            print('Checking offline installed-app reopen', flush=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            context.set_offline(True)
            page.reload()
            page.locator('#startup-retry').wait_for()
            assert page.locator('#auth').count() == 0
            context.set_offline(False)
            page.locator('#online-search').wait_for()
            report.append({'engine': engine, 'case': 'offline-empty-reopen-auto-reconnect', 'status': 'passed'})
            page.evaluate("""() => {
              window.fixtureSong=id=>({id:'piped:'+id,provider_id:id,provider:'piped',title:'Offline fixture',channel:'Local test',duration:id==='short000001'?2:60});
              offline.add([fixtureSong('next0000001'),fixtureSong('short000001')]);
            }""")
            page.wait_for_function("() => !offline.running && offline.has('piped:short000001') && offline.has('piped:next0000001')")
            context.set_offline(True)
            page.reload()
            page.locator('.offline-page').wait_for()
            assert page.locator('[data-offline-play]').count() == 2
            page.screenshot(animations='disabled', path=str(Path(__file__).resolve().parents[1] / 'test-results' / f'iphone-pwa-{engine}-offline.png'))
            if engine == 'chromium':
                page.locator('[data-downloads="play"]').click()
                page.wait_for_function("() => !onlineAudio.paused && onlineAudio.currentTime>.1 && onlineAudio.getAttribute('src').startsWith('blob:')")
                page.wait_for_function("() => audioQueue[audioIndex]?.provider_id==='next0000001' && !onlineAudio.paused && onlineAudio.currentTime>.1", timeout=10000)
                page.evaluate('pausePlayback()')
            context.set_offline(False)
            page.locator('#online-search').wait_for()
            report.append({'engine': engine, 'case': 'offline-downloads-reopen-reconnect', 'status': 'passed', 'audio': 'played and chained' if engine=='chromium' else 'not tested: host WebKit decoder unavailable'})
            print('Checking slow installed-app update', flush=True)
            previous = page.locator('meta[name="ivox-version"]').get_attribute('content')
            target = previous + '-upgrade'
            state['slow_install'] = True
            install_gate.clear();install_started.clear()
            onda.APP_VERSION = target
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.locator('.app-update').filter(has_text='Nueva actualización').wait_for()
            page.locator('.app-update button').click()
            assert install_started.wait(3), 'New worker did not start installing'
            page.wait_for_timeout(300)
            assert page.locator('meta[name="ivox-version"]').get_attribute('content') == previous, 'Reloaded before the updated shell finished installing'
            state['slow_install'] = False;install_gate.set()
            page.wait_for_function('(target) => document.querySelector(\'meta[name="ivox-version"]\').content===target', arg=target)
            page.locator('#online-search').wait_for()
            report.append({'engine': engine, 'case': 'slow-update-waits-for-shell', 'status': 'passed'})
            print('Checking failed update preserves working app', flush=True)
            state['install_failure'] = True
            onda.APP_VERSION = target + '-retry'
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.locator('.app-update').filter(has_text='Nueva actualización').wait_for()
            page.locator('.app-update button').click()
            page.locator('.app-update button').filter(has_text='Reintentar').wait_for()
            assert page.locator('meta[name="ivox-version"]').get_attribute('content') == target
            state['install_failure'] = False
            page.locator('.app-update button').click()
            page.wait_for_function('(target) => document.querySelector(\'meta[name="ivox-version"]\').content===target', arg=onda.APP_VERSION)
            page.locator('#online-search').wait_for()
            report.append({'engine': engine, 'case': 'failed-update-keeps-shell-and-retries', 'status': 'passed'})
            onda.APP_VERSION = original_version
            assert not errors, errors
            context.close()
            browser.close()
    out = Path(__file__).resolve().parents[1] / 'test-results'
    out.mkdir(exist_ok=True)
    (out / 'iphone-pwa-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
finally:
    install_gate.set()
    state['me_failure'] = False
    onda.APP_VERSION = original_version
    server.shutdown()
    server.server_close()
    temporary.cleanup()
