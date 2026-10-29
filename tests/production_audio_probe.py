"""Probe the published audio path. Keeps a dedicated QA session only in memory.

Type retry on stdin to repeat without creating another diagnostic account.
No tokens, signed URLs, or credentials are printed or saved.
"""
import concurrent.futures
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
            result.update(status=response.status,mime=response.headers.get('Content-Type'),bytes=len(response.read(32768)))
    except urllib.error.HTTPError as error:
        result.update(status=error.code,error=error.read(2048).decode(errors='replace'))
    except Exception as error:result.update(error=type(error).__name__)
    result['seconds']=round(time.monotonic()-started,2)
    return result


def main():
    api('/api/register',{'name':'Diagnóstico de reproducción','email':secrets.token_hex(12)+'@ivox-qa.invalid','password':secrets.token_urlsafe(28)})
    queries=sys.argv[1:] or ['CNCO','Manuel Turizo']
    tracks=[]
    for query in queries:tracks.extend(api('/api/online/search?q='+urllib.parse.quote(query))['items'][:2])
    out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
    runs=[]
    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(probe,tracks))
        runs.append(results)
        (out/'production-audio-probe.json').write_text(json.dumps(runs,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=True),flush=True)
        print('READY: retry or quit',flush=True)
        if sys.stdin.readline().strip()!='retry':break


if __name__=='__main__':main()
