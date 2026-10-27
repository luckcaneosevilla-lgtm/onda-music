"""Real decoding checks, independent temporary users and actual provider metadata."""
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
from audio_resolver import AudioUnavailable
from playwright.sync_api import sync_playwright

out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
tmp=tempfile.TemporaryDirectory();onda.DATA=Path(tmp.name);onda.TURSO=None;onda.init()
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}';password=secrets.token_urlsafe(24)
report={'cases':[],'errors':[],'network_urls_exposed':False}
def detail(raw):
    data=onda.provider.request('piped','/streams/'+raw)
    item=onda.provider.normalize(dict(data,id=raw),'piped')
    with onda.db() as c:onda.provider.store(c,[item])
    return item,data
def select(page,item):
    page.evaluate("item => {searchView.items=[item];searchView.query=item.title;searchView.next=null;searchView.error=null;searchView.loading=false;drawSearch()}",item)
    page.locator('[data-online-play]').first.click()
def check(page,item,label):
    select(page,item)
    try:
        page.wait_for_function('() => !onlineAudio.paused && onlineAudio.currentTime>0.8',timeout=60000)
        result={'case':label,'id':item['id'],'title':item['title'],'channel':item['channel'],'played':True,'level':page.evaluate('() => activeLevel'),'engine':page.evaluate('() => activeEngine'),'time':page.evaluate('() => onlineAudio.currentTime')}
    except Exception:
        result={'case':label,'id':item['id'],'played':False,'message':page.locator('#playback-status').inner_text()}
    report['cases'].append(result);return result
try:
    rick,rick_data=detail('dQw4w9WgXcQ')
    report['rick_streams']={'audio':len(rick_data.get('audioStreams',[])),'video':[{'itag':s.get('itag'),'mime':s.get('mimeType'),'videoOnly':s.get('videoOnly')} for s in rick_data.get('videoStreams',[])]}
    with onda.db() as c:mainstream=onda.provider.search(c,'test','Feid',source='piped')['items'][0]
    # Select a genuinely small channel using provider subscriber counts, not a guess.
    candidates=onda.provider.request('piped','/search',{'q':'my first original song guitar 2026','filter':'videos'}).get('items',[])
    small=None
    for candidate in candidates[:8]:
        item=onda.provider.normalize(candidate,'piped')
        if not item:continue
        item,data=detail(item['provider_id']);count=data.get('uploaderSubscriberCount')
        if isinstance(count,int) and 0<count<=25000:
            small=item;report['small_channel_subscribers']=count;break
    if not small:report['small_channel_pending']='No se encontró un canal con recuento comprobable <=25000 en los candidatos consultados.'
    with sync_playwright() as p:
        browser=p.chromium.launch(env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'})
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:report['errors'].append(str(e)))
        page.on('request',lambda r:report.update(network_urls_exposed=True) if 'googlevideo.com' in r.url else None)
        page.goto(url);page.get_by_role('button',name='Crear una cuenta',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Pruebas de niveles')
        page.get_by_label('Email',exact=True).fill('levels-test@example.com');page.get_by_label('Contraseña',exact=True).fill(password)
        page.get_by_role('button',name='Crear cuenta',exact=True).click();page.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for()
        # Forced failure is confined to the test process; media and Piped are real.
        with patch.object(onda.resolver,'resolve',side_effect=AudioUnavailable()):
            result=check(page,rick,'rick_level3_with_ytdlp_failure')
            assert result.get('played') and result.get('level')==3,result
        page.locator('#close-online').click()
        check(page,mainstream,'mainstream_normal_chain');page.locator('#close-online').click()
        response=page.request.get(url+'/api/audio/'+mainstream['provider_id'],headers={'Range':'bytes=0-1023'})
        report['ytdlp_range']={'status':response.status,'content_type':response.headers.get('content-type'),'content_range':response.headers.get('content-range'),'bytes':len(response.body())}
        assert response.status==206 and response.headers.get('content-type','').startswith('audio/'),report['ytdlp_range']
        check(page,rick,'old_video_normal_chain');page.locator('#close-online').click()
        if small:check(page,small,'small_channel_normal_chain');page.locator('#close-online').click()
        # References are independent from the resolution engine used to play them.
        select(page,rick)
        page.locator('[data-online-fav]').first.click();page.get_by_text('Guardado en favoritos de IVOX.',exact=True).wait_for()
        page.locator('[data-online-menu]').first.click();page.get_by_role('button',name='Guardar en playlist',exact=True).click();page.get_by_role('button',name='Nueva playlist',exact=True).click()
        page.get_by_label('Nombre',exact=True).fill('Niveles · Referencia real');page.get_by_role('button',name='Guardar',exact=True).click();page.get_by_text('Playlist creada y referencia guardada.',exact=True).wait_for()
        second=browser.new_context(viewport={'width':390,'height':844});mobile=second.new_page();mobile.goto(url)
        mobile.get_by_label('Email',exact=True).fill('levels-test@example.com');mobile.get_by_label('Contraseña',exact=True).fill(password);mobile.get_by_role('button',name='Entrar',exact=True).click()
        mobile.get_by_role('heading',name='¿Qué quieres escuchar?',exact=True).wait_for();mobile.get_by_role('link',name='♫ Mi biblioteca',exact=True).click();mobile.locator('.video-row').first.wait_for()
        assert mobile.locator('.video-row').first.get_attribute('data-video')==rick['id']
        mobile.get_by_role('button',name='Playlists',exact=True).click();mobile.get_by_role('button',name='Niveles · Referencia real',exact=False).click();mobile.locator('.video-row').first.wait_for()
        assert mobile.locator('.video-row').first.get_attribute('data-video')==rick['id'];report['favorites_playlist_sync']=True
        with onda.db() as c:audius=onda.provider.search(c,'test','Feid',source='audius')['items'][0]
        result=check(page,audius,'audius_level4');assert result.get('played') and result.get('level')==4,result
        page.screenshot(path=str(out/'audio-levels-desktop.png'),full_page=True)
        assert not report['errors'],report['errors'];assert not report['network_urls_exposed']
        browser.close()
    report['status']='passed_multiple_levels' if all(r['played'] for r in report['cases']) and small else 'partial'
finally:
    (out/'audio-levels-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
    server.shutdown();server.server_close();tmp.cleanup()
