"""Independent Chromium smoke test; requires playwright and its Chromium binary."""
import io
import json
from pathlib import Path
import struct
import sys
import threading
import wave

sys.path.insert(0,str(Path(__file__).parent))
from test_security import onda, temporary
from playwright.sync_api import sync_playwright

onda.init()
server=onda.ThreadingHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}'
out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
errors=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url)
        page.get_by_role('button',name='Crear una cuenta',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Administradora')
        page.get_by_label('Email',exact=True).fill('owner@example.com')
        page.get_by_label('Contraseña',exact=True).fill('temporary-test-password')
        page.get_by_role('button',name='Crear cuenta',exact=True).click()
        page.get_by_role('heading',name='¿Qué quieres escuchar?').wait_for()
        page.get_by_role('link',name='Archivos locales',exact=True).click()
        page.get_by_role('heading',name='La música es tuya.').wait_for()
        page.get_by_role('button',name='Nueva playlist',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('En movimiento')
        page.get_by_role('button',name='Guardar',exact=True).click()
        page.get_by_role('button',name='En movimiento',exact=True).wait_for()
        sample=io.BytesIO()
        with wave.open(sample,'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(struct.pack('<h',0)*8000)
        page.locator('#files').set_input_files({'name':'Silencio de prueba.wav','mimeType':'audio/wav','buffer':sample.getvalue()})
        page.get_by_role('button',name='Reproducir Silencio de prueba.wav',exact=True).click()
        page.wait_for_function('() => document.querySelector("#audio").duration === 1')
        page.get_by_role('link',name='Mi plan',exact=True).click()
        page.get_by_role('heading',name='Mi plan',exact=True).wait_for()
        page.screenshot(path=str(out/'plan-desktop.png'),full_page=True)
        with onda.db() as c:c.execute("UPDATE users SET role='admin' WHERE email='owner@example.com'")
        page.goto(url+'/admin')
        page.get_by_role('heading',name='Cuida de tu comunidad.').wait_for()
        # A second real account; no private titles appear in the admin table.
        context=browser.new_context(viewport={'width':390,'height':844})
        mobile=context.new_page();mobile.on('pageerror',lambda e:errors.append(str(e)))
        mobile.goto(url);mobile.get_by_role('button',name='Crear una cuenta',exact=True).click()
        mobile.get_by_label('Nombre',exact=True).fill('Oyente')
        mobile.get_by_label('Email',exact=True).fill('listener@example.com')
        mobile.get_by_label('Contraseña',exact=True).fill('temporary-test-password')
        mobile.get_by_role('button',name='Crear cuenta',exact=True).click()
        mobile.get_by_role('heading',name='¿Qué quieres escuchar?').wait_for()
        assert mobile.get_by_text('Silencio de prueba.wav',exact=True).count()==0
        mobile.screenshot(path=str(out/'library-mobile.png'),full_page=True)
        assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.reload();page.get_by_role('heading',name='Cuida de tu comunidad.').wait_for()
        row=page.locator('tr').filter(has_text='listener@example.com')
        row.get_by_role('button',name='Gestionar',exact=True).click()
        page.get_by_role('button',name='Aplicar cambio',exact=True).click()
        page.get_by_text('Cambio guardado y registrado.',exact=True).wait_for()
        row=page.locator('tr').filter(has_text='listener@example.com')
        assert 'Premium' in row.inner_text()
        row.get_by_role('button',name='Gestionar',exact=True).click()
        page.locator('select[name="action"]').select_option('revoke')
        page.get_by_role('button',name='Aplicar cambio',exact=True).click()
        page.get_by_role('heading',name='¿Retirar Premium?',exact=True).wait_for()
        page.get_by_role('button',name='Confirmar',exact=True).click()
        page.get_by_text('Cambio guardado y registrado.',exact=True).wait_for()
        page.get_by_role('button',name='Configurar planes',exact=True).click()
        page.locator('input[name="playlists"]').fill('12')
        page.get_by_role('button',name='Guardar límites',exact=True).click()
        page.get_by_text('Límites actualizados.',exact=True).wait_for()
        page.screenshot(path=str(out/'admin-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(out/'admin-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        browser.close()
    print(json.dumps({'status':'passed','browser_errors':errors,'screenshots':str(out)}))
finally:
    server.shutdown();server.server_close();temporary.cleanup()
