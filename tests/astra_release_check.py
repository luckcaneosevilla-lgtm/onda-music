"""Isolated 4.0 release, mobile layout and service-worker update smoke check."""
import json
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch
os.environ.pop('SSLKEYLOGFILE',None)
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_security import onda,temporary
from playwright.sync_api import sync_playwright

onda.init()
server=onda.ThreadingHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}'
out=Path(__file__).resolve().parents[1]/'test-results'
out.mkdir(exist_ok=True)
errors=[];results=[]
try:
 with patch.object(onda.provider,'search',return_value={'items':[],'next':None}),sync_playwright() as p:
  browser=p.chromium.launch()
  context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(url)
  assert page.title().startswith('Ivox-Astra 4.0')
  assert page.request.get(url+'/version.json').json()['release']=='4.0'
  assert page.request.get(url+'/manifest.json').json()['name']=='Ivox-Astra 4.0'
  page.get_by_role('button',name='Crear una cuenta',exact=True).click()
  page.get_by_label('Nombre',exact=True).fill('Astra QA')
  page.get_by_label('Email',exact=True).fill('astra@example.invalid')
  page.get_by_label('Contraseña',exact=True).fill('isolated-astra-test-password')
  page.get_by_role('button',name='Crear cuenta',exact=True).click()
  dialog=page.locator('.release-dialog');dialog.wait_for(state='visible')
  assert 'Ivox-Astra 4.0' in dialog.inner_text()
  page.screenshot(path=str(out/'astra-4-mobile-release.png'),full_page=True)
  page.get_by_role('button',name='Entrar a Ivox-Astra 4.0',exact=True).click()
  for width in (320,390,768,1440):
   page.set_viewport_size({'width':width,'height':900})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'overflow at {width}'
   results.append({'width':width,'overflow':False})
  page.set_viewport_size({'width':390,'height':844})
  page.screenshot(path=str(out/'astra-4-mobile-home.png'),full_page=True)
  assert page.locator('.app-version').inner_text()=='Ivox-Astra 4.0'
  page.wait_for_function('()=>!!navigator.serviceWorker.controller')
  caches=page.evaluate('()=>window.caches.keys()')
  assert 'ivox-app-shell-v29' in caches,caches
  context.set_offline(True);page.reload(wait_until='domcontentloaded')
  assert page.title().startswith('Ivox-Astra 4.0')
  assert not errors,errors
  print(json.dumps({'release':'Ivox-Astra 4.0','layouts':results,'offline_shell':'PASS','browser_errors':errors}))
  browser.close()
finally:
 server.shutdown();server.server_close();onda.prepared_audio.close();temporary.cleanup()
