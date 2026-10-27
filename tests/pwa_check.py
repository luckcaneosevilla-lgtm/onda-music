"""Read-only checks on the running local app, isolated Chromium browser storage."""
import http.client
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

base='http://127.0.0.1:8765';root=Path(__file__).resolve().parents[1]
report={'resources':{},'browser_errors':[]}
for path,mime in [('/manifest.json','application/manifest+json'),('/service-worker.js','text/javascript'),('/pwa.js','text/javascript'),('/icon-192x192.png','image/png'),('/icon-512x512.png','image/png')]:
    c=http.client.HTTPConnection('127.0.0.1',8765);c.request('GET',path);r=c.getresponse();body=r.read()
    report['resources'][path]={'status':r.status,'type':r.getheader('Content-Type'),'bytes':len(body)}
    assert r.status==200 and r.getheader('Content-Type').startswith(mime),report['resources'][path]
    if path=='/manifest.json':
        manifest=json.loads(body);assert manifest['name']=='IVOX' and manifest['display']=='standalone';assert {i['sizes'] for i in manifest['icons']}=={'192x192','512x512'}
    if path.endswith('.png'):
        assert body[:8]==b'\x89PNG\r\n\x1a\n'
        width=int.from_bytes(body[16:20],'big');height=int.from_bytes(body[20:24],'big');expected=192 if '192' in path else 512;assert width==height==expected
    c.close()
with sync_playwright() as p:
    browser=p.chromium.launch();context=browser.new_context();page=context.new_page()
    page.on('pageerror',lambda e:report['browser_errors'].append(str(e)))
    page.goto(base);page.wait_for_function('() => !!navigator.serviceWorker.controller',timeout=15000)
    report['worker_scope']=page.evaluate('async () => (await navigator.serviceWorker.ready).scope')
    assert report['worker_scope']==base+'/'
    cdp=context.new_cdp_session(page);manifest_result=cdp.send('Page.getAppManifest');assert not manifest_result.get('errors'),manifest_result.get('errors')
    page.evaluate('() => fetch("/api/me")')
    cached=page.evaluate('async () => {const name=(await caches.keys()).find(k=>k.startsWith("ivox-app-shell-"));const c=await caches.open(name);return (await c.keys()).map(r=>new URL(r.url).pathname)}')
    report['cached_paths']=cached;assert '/app.js' in cached and '/' in cached
    assert all(not path.startswith('/api/') and path!='/admin' for path in cached)
    context.set_offline(True);page.reload(wait_until='domcontentloaded');page.locator('.auth').wait_for()
    report['offline_shell']=True
    assert not report['browser_errors'],report['browser_errors']
    browser.close()
report['status']='passed'
(root/'test-results').mkdir(exist_ok=True)
(root/'test-results'/'pwa-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
