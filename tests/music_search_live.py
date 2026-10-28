"""Live songs/artists search, pagination, yt-dlp playback and API fallback."""
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
temp=tempfile.TemporaryDirectory();onda.DATA=Path(temp.name);onda.TURSO=None;onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}';report={'query':'Feid','errors':[]}
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'})
        context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
        page.goto(url);page.get_by_role('button',name='Crear una cuenta',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Prueba catálogo temporal');page.get_by_label('Email',exact=True).fill('catalog-test@example.com');page.get_by_label('Contraseña',exact=True).fill(secrets.token_urlsafe(24))
        page.get_by_role('button',name='Crear cuenta',exact=True).click();page.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for()
        page.get_by_role('searchbox',name='Buscar música online',exact=True).fill('Feid');page.get_by_role('button',name='Buscar',exact=True).click();page.locator('.video-row').first.wait_for(timeout=35000)
        report['song_source']=page.evaluate('() => searchView.resolved');assert report['song_source']=='ytmusic'
        samples=page.evaluate('() => searchView.items.slice(0,3).map(v=>({title:v.title,artists:v.artists,duration:v.duration,videoId:v.provider_id}))')
        assert all(s['title'] and 'Feid' in s['artists'] and s['duration']>0 and len(s['videoId'])==11 for s in samples),samples
        report['songs']=samples;report['count']=page.locator('[data-online-play]').count()
        page.screenshot(path=str(out/'ytmusic-songs-desktop.png'),full_page=True)
        page.get_by_role('button',name='Mostrar más resultados',exact=True).click();page.wait_for_function('() => document.querySelectorAll("[data-online-play]").length>20',timeout=35000)
        report['paginated_count']=page.locator('[data-online-play]').count()
        page.locator('#search-filter').select_option('artists');page.get_by_role('button',name='Buscar',exact=True).click();page.locator('[data-artist="Feid"]').first.wait_for(timeout=35000)
        report['artist']=page.evaluate('() => searchView.artists.find(a=>a.name==="Feid")')
        page.screenshot(path=str(out/'ytmusic-artists-desktop.png'),full_page=True)
        page.locator('[data-artist="Feid"]').first.click();page.locator('[data-online-play]').first.wait_for(timeout=35000)
        # "Ver artista" opens the artist page: profile, popular songs, related artists.
        assert page.evaluate('() => location.pathname').startswith('/artist/UC');assert page.locator('.artist-hero h2').inner_text()=='Feid'
        page.locator('[data-online-play]').first.click();page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>0.8',timeout=45000)
        report['playback']={'engine':page.evaluate('() => activeEngine'),'level':page.evaluate('() => activeLevel'),'current_time':page.evaluate('() => onlineAudio.currentTime')}
        assert report['playback']['engine']=='yt-dlp',report['playback']
        page.locator('#close-online').click()
        with patch.object(onda.provider.music,'search',side_effect=ProviderError(502,'ytmusic_unavailable','Fallo inducido de prueba')):
            response=page.request.get(url+'/api/online/search?q=Feid%20LUNA');data=response.json();assert response.status==200 and data['source']=='youtube' and data['items'],data
            report['forced_failure_fallback']={'source':data['source'],'count':len(data['items']),'real_youtube_data_api':True}
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'ytmusic-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not report['errors'];browser.close()
    report['status']='passed'
finally:
    (out/'ytmusic-live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True))
    server.shutdown();server.server_close();temp.cleanup()
