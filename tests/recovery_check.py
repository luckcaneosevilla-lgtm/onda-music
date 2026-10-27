"""Recovery form browser check against a disposable local account/database."""
import secrets
import threading
from test_security import onda,Client,temporary
from playwright.sync_api import sync_playwright
onda.init();server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
client=Client(server.server_port);password=secrets.token_urlsafe(20)
try:
    code,_=client.request('/api/register','POST',{'name':'Recovery fixture','email':'recovery@example.com','password':secrets.token_urlsafe(20)});assert code==200
    link=onda.create_admin_recovery('recovery@example.com',server.server_port)
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(link);assert not page.url.split('#')[-1]==link.split('#')[-1]
        page.get_by_label('Nueva contraseña',exact=True).fill(password)
        page.get_by_label('Repetir contraseña',exact=True).fill(password)
        page.get_by_role('button',name='Guardar y abrir administración',exact=True).click()
        page.get_by_role('heading',name='Cuida de tu comunidad.',exact=True).wait_for()
        assert page.locator('tr').filter(has_text='recovery@example.com').count()==1
        assert not errors,errors;browser.close()
    print('Recuperación aprobada: formulario, token fuera de historial y apertura autenticada del panel.')
finally:server.shutdown();server.server_close();temporary.cleanup()
