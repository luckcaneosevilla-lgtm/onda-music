"""Seven live checks for the hybrid player. Test users live in a temporary DB."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as onda
from youtube_provider import ProviderError
from playwright.sync_api import sync_playwright

out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
temporary=tempfile.TemporaryDirectory();onda.DATA=Path(temporary.name);onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}';password=secrets.token_urlsafe(24)
report={'architecture':'Piped + Audius / HTML5 audio','checks':{},'page_errors':[],'query':'Feid'}
def search(page,source):
    page.get_by_role('searchbox',name='Buscar música online',exact=True).fill('Feid')
    page.locator('#search-source').select_option(source)
    page.get_by_role('button',name='Buscar',exact=True).click()
    try:page.locator('.video-row').first.wait_for(timeout=40000)
    except Exception:
        report['search_failure']=page.locator('#search-results').inner_text();page.screenshot(path=str(out/'hybrid-search-failure.png'),full_page=True);raise
try:
    report['checks']['1_piped_health']={'passed':onda.provider.health(),'endpoint':'http://127.0.0.1:8080/healthcheck'}
    with sync_playwright() as p:
        browser=p.chromium.launch(env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'})
        context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page()
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        youtube_requests=[];page.on('request',lambda req:youtube_requests.append(req.url) if 'youtube.com' in req.url else None)
        page.goto(url);page.get_by_role('button',name='Crear una cuenta',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Verificación temporal')
        page.get_by_label('Email',exact=True).fill('hybrid-test@example.com');page.get_by_label('Contraseña',exact=True).fill(password)
        page.get_by_role('button',name='Crear cuenta',exact=True).click();page.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for()
        search(page,'piped');first=page.locator('.video-row').first;reference=first.get_attribute('data-video')
        report['checks']['2_real_feid_search']={'passed':True,'count':page.locator('.video-row').count(),'id':reference,'title':first.locator('h3').inner_text(),'channel':first.locator('.video-info p').inner_text(),'duration':first.locator('.duration').inner_text(),'thumbnail':bool(first.locator('img').get_attribute('src'))}
        page.screenshot(path=str(out/'hybrid-search-desktop.png'),full_page=True)
        first.locator('[data-online-fav]').click();page.get_by_text('Guardado en favoritos de IVOX.',exact=True).wait_for()
        page.locator('[data-online-menu]').first.click();page.get_by_role('button',name='Guardar en playlist',exact=True).click();page.get_by_role('button',name='Nueva playlist',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Feid · Referencias reales');page.get_by_role('button',name='Guardar',exact=True).click();page.get_by_text('Playlist creada y referencia guardada.',exact=True).wait_for()
        report['checks']['4_favorite_and_playlist']={'passed':True,'reference':reference}
        page.locator('[data-online-play]').first.click()
        # Exercise Piped directly; yt-dlp has its own live test suite.
        page.evaluate('() => startOnline(true,2)')
        try:
            page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime > 0.5',timeout=12000)
            report['checks']['3_piped_audio']={'passed':True,'reference':reference}
        except Exception:
            report['checks']['3_piped_audio']={'passed':False,'reason':page.locator('#playback-status').inner_text(),'note':'No se sustituye por un stream de vídeo.'}
        page.locator('#close-online').click()
        search(page,'audius');report['audius_results']=page.locator('.video-row').count();report['audius_title']=page.locator('.video-row h3').first.inner_text()
        page.locator('[data-online-play]').first.click();page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime > 0.5',timeout=30000)
        report['audius_audio']={'passed':True,'current_time':page.evaluate('() => onlineAudio.currentTime'),'source':page.locator('#online-source').inner_text()}
        source=page.locator('#online-audio').get_attribute('src');page.get_by_role('link',name='Mi plan',exact=True).click();page.get_by_role('heading',name='Mi plan',exact=True).wait_for()
        assert page.locator('#online-audio').get_attribute('src')==source;assert page.evaluate('() => !onlineAudio.paused')
        assert page.locator('iframe').count()==0 and not youtube_requests
        report['persistent_desktop_audio']=True;report['no_youtube_iframe_requests']=True
        page.screenshot(path=str(out/'hybrid-player-desktop.png'),full_page=True)
        mobile_context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1')
        mobile=mobile_context.new_page();mobile.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        mobile.goto(url);mobile.get_by_label('Email',exact=True).fill('hybrid-test@example.com');mobile.get_by_label('Contraseña',exact=True).fill(password);mobile.get_by_role('button',name='Entrar',exact=True).click();mobile.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for()
        mobile.get_by_role('link',name='♫ Mi biblioteca',exact=True).click();mobile.locator('.video-row').first.wait_for();assert mobile.locator('.video-row').first.get_attribute('data-video')==reference
        mobile.get_by_role('button',name='Playlists',exact=True).click();mobile.get_by_role('button',name='Feid · Referencias reales',exact=False).click();mobile.locator('.video-row').first.wait_for();assert mobile.locator('.video-row').first.get_attribute('data-video')==reference
        report['checks']['5_second_session_sync']={'passed':True,'reference':reference};mobile.screenshot(path=str(out/'hybrid-library-mobile.png'),full_page=True)
        mobile.get_by_role('link',name='⌕ Descubrir',exact=True).click();mobile.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for();search(mobile,'audius')
        mobile.locator('[data-online-play]').first.click();mobile.wait_for_function('() => !!onlineAudio.getAttribute("src")',timeout=25000)
        assert mobile.evaluate('() => onlineAudio.paused')
        mobile.locator('#online-audio').click(position={'x':25,'y':27})
        mobile.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime > 0.5',timeout=25000)
        source=mobile.locator('#online-audio').get_attribute('src');mobile.get_by_role('link',name='Mi plan',exact=True).click();mobile.get_by_role('heading',name='Mi plan',exact=True).wait_for();assert mobile.locator('#online-audio').get_attribute('src')==source
        assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth');mobile.screenshot(path=str(out/'hybrid-player-mobile.png'),full_page=True)
        report['checks']['6_mobile_gesture_and_persistence']={'passed':True,'engine':'Chromium, viewport and iPhone UA emulated; not physical iPhone','current_time':mobile.evaluate('() => onlineAudio.currentTime')}
        mobile.locator('#close-online').click()
        page.get_by_role('link',name='⌕ Descubrir',exact=True).click();page.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for();search(page,'audius')
        # A deliberate unavailable-track failure, isolated to this test server.
        original_stream=onda.provider.stream;failed_id=page.locator('.video-row').first.get_attribute('data-video')
        def unavailable(c,uid,ref,*args):
            if ref==failed_id:raise ProviderError(422,'no_audio','Canción no disponible (fallo inducido de prueba).')
            return original_stream(c,uid,ref,*args)
        with patch.object(onda.provider,'stream',side_effect=unavailable):
            page.locator('[data-online-play]').first.click();page.get_by_text('Canción no disponible (fallo inducido de prueba).',exact=True).wait_for()
            page.wait_for_function('() => audioIndex === 1',timeout=10000);page.wait_for_function('() => !!onlineAudio.getAttribute("src")',timeout=20000)
            report['checks']['7_unavailable_and_next']={'passed':True,'induced_failure':True,'next_index':page.evaluate('() => audioIndex')}
        assert not report['page_errors'],report['page_errors'];browser.close()
    report['status']='passed' if report['checks']['3_piped_audio']['passed'] else 'partial_primary_audio_unavailable_fallback_verified'
finally:
    (out/'hybrid-live-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
    server.shutdown();server.server_close();temporary.cleanup()
