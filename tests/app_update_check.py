"""An installed app (old service worker + old shell cache) must run the new
release's scripts on the next open, not its cached copy of the old ones."""
from pathlib import Path
import sys,threading
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda,temporary

onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
old=onda.APP_VERSION
try:
 with sync_playwright() as p:
  browser=p.chromium.launch();context=browser.new_context()
  page=context.new_page();page.goto(origin)
  page.wait_for_function('async () => (await navigator.serviceWorker.ready) && !!navigator.serviceWorker.controller',timeout=20000)
  page.reload();page.wait_for_function('() => !!navigator.serviceWorker.controller')
  # Deploy: a new release ID. The old worker is still installed and controlling.
  onda.APP_VERSION='newrelease1'
  seen=[]
  page.on('response',lambda r:seen.append((r.url.split(origin)[-1],r.from_service_worker)) if '/app.js' in r.url or '/online.js' in r.url else None)
  page.reload();page.wait_for_load_state('load')
  scripts=page.evaluate("[...document.scripts].map(s=>s.getAttribute('src')).filter(s=>s&&s.includes('app.js'))")
  assert scripts==['/app.js?v=newrelease1'],scripts
  assert any(u=='/app.js?v=newrelease1' and not sw for u,sw in seen),seen
  assert page.evaluate("document.querySelector('meta[name=ivox-version]').content")=='newrelease1'
  print('passed: installed app loads the new release scripts',seen)
  browser.close()
finally:
 server.shutdown()
