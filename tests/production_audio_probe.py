"""Probe the published audio path. Keeps a dedicated QA session only in memory.

Type retry on stdin to repeat without creating another diagnostic account.
No tokens, signed URLs, or credentials are printed or saved.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

os.environ.pop('SSLKEYLOGFILE',None)
origin='https://p01--onda-music--ftnmpttbnqmf.code.run'
jar=http.cookiejar.CookieJar()
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def api(path,data=None):
    request=urllib.request.Request(origin+path,data=json.dumps(data).encode() if data else None,
                                   headers={'Content-Type':'application/json'})
    with client.open(request,timeout=45) as response:return json.load(response)


def probe(item):
    started=time.monotonic()
    result={'id':item['id'],'title':item['title']}
    req=urllib.request.Request(origin+'/api/audio/'+item['provider_id'],headers={'Range':'bytes=0-32767'})
    try:
        with client.open(req,timeout=45) as response:
            result.update(status=response.status,mime=response.headers.get('Content-Type'),server_timing=response.headers.get('Server-Timing'),cache=response.headers.get('X-IVOX-Audio-Cache'),bytes=len(response.read(32768)))
    except urllib.error.HTTPError as error:
        result.update(status=error.code,error=error.read(2048).decode(errors='replace'))
    except Exception as error:result.update(error=type(error).__name__)
    result['seconds']=round(time.monotonic()-started,2)
    return result


def browser_probe(tracks):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'})
        context=browser.new_context(viewport={'width':390,'height':844})
        context.add_cookies([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,
                             'secure':c.secure,'httpOnly':True,'sameSite':'Strict'} for c in jar])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(origin)
        page.wait_for_function('() => me && typeof playList === "function"')
        page.evaluate("items => {setAutoplay(false);audioQueue=items;audioIndex=0;document.querySelector('#online-player').hidden=false}",tracks[:2])
        page.evaluate("""() => {
          window.tapAt=0;window.nextAt=0;window.startMs=null;window.nextMs=null;
          document.querySelector('#np-toggle').addEventListener('click',()=>{tapAt=performance.now()},{once:true});
          onlineAudio.addEventListener('ended',()=>{nextAt=performance.now()});
          onlineAudio.addEventListener('playing',()=>{
            if(onlineAudio.getAttribute('src')==='/silence.wav')return;
            if(audioIndex===0&&startMs===null)startMs=performance.now()-tapAt;
            if(audioIndex===1&&nextMs===null&&nextAt)nextMs=performance.now()-nextAt;
          });
        }""")
        page.locator('#np-toggle').click()
        try:
            page.wait_for_function('() => audioIndex===0 && !onlineAudio.paused && onlineAudio.currentTime>8 && onlineAudio.readyState>=2',timeout=45000)
        except Exception:
            detail=page.evaluate('({index:audioIndex,id:audioQueue[audioIndex]?.id,time:onlineAudio.currentTime,state:document.querySelector("#online-player").dataset.state,message:document.querySelector("#playback-status").textContent,level:activeLevel,failures:failStreak,wanted:playbackWanted})')
            print(json.dumps({'browser_failure':detail},ensure_ascii=True),flush=True)
            browser.close();raise
        first=page.evaluate('({id:audioQueue[audioIndex].id,time:onlineAudio.currentTime,playing_ms:startMs,state:document.querySelector("#online-player").dataset.state})')
        # Seek to the real ending so the deployed media element must emit ended
        # and advance itself, without clicking Next or synthesizing that event.
        page.evaluate('() => {onlineAudio.currentTime=trackDuration()-1.5}')
        page.wait_for_function('() => audioIndex===1 && !onlineAudio.paused && onlineAudio.currentTime>8 && onlineAudio.readyState>=2',timeout=45000)
        second=page.evaluate('({id:audioQueue[audioIndex].id,time:onlineAudio.currentTime,transition_ms:nextMs,state:document.querySelector("#online-player").dataset.state})')
        page.evaluate('stopOnline()');browser.close()
    result={'first':first,'automatic_next':second,'page_errors':errors}
    assert not errors,errors
    return result


def main():
    api('/api/register',{'name':'Diagnóstico de reproducción','email':secrets.token_hex(12)+'@ivox-qa.invalid','password':secrets.token_urlsafe(28)})
    queries=[a for a in sys.argv[1:] if not a.startswith('--')] or ['CNCO','Manuel Turizo']
    tracks=[]
    for query in queries:
        found=api('/api/online/search?q='+urllib.parse.quote(query))['items']
        tracks.extend([found[2],found[0]] if '--cold-third' in sys.argv else found[:2])
    out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
    runs=[]
    while True:
        if '--browser-first' in sys.argv:
            result=browser_probe(tracks)
            (out/'production-browser-latency.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)
        # Match one listener's playback rather than creating an extraction burst.
        results=[probe(item) for item in tracks]
        runs.append(results)
        (out/'production-audio-probe.json').write_text(json.dumps(runs,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=True),flush=True)
        if '--browser' in sys.argv and '--browser-first' not in sys.argv and all(r.get('status') in (200,206) for r in results):
            result=browser_probe(tracks)
            (out/'production-browser-audio.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)
        print('READY: retry or quit',flush=True)
        if sys.stdin.readline().strip()!='retry':break


if __name__=='__main__':main()
