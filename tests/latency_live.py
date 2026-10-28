"""Live playback latency: time from 'Play' to the first audio bytes for NEW songs.

Starts IVOX on a temporary database, searches real queries, and measures for
songs that were never resolved before (cold), the same request after a
background prefetch, and the artist/radio endpoints. Report:
test-results/latency-report.json. Needs network access; no browser.
"""
import http.client
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as onda

out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
temp=tempfile.TemporaryDirectory();onda.DATA=Path(temp.name);onda.TURSO=None;onda.init()
# Same warm-up as `python server.py serve`.
onda.resolver.start(warm='jNQXAC9IVRw');onda.provider.music.start()
for _ in range(100):
    if len(onda.resolver.idle)>=3:break
    time.sleep(.2)
server=onda.OndaHTTPServer(('127.0.0.1',0),onda.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
port=server.server_port;cookie='';csrf=''

def call(path,method='GET',data=None,headers=None):
    global cookie,csrf
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=40)
    conn.request(method,path,json.dumps(data).encode() if data is not None else None,{'Cookie':cookie,'X-CSRF-Token':csrf,**(headers or {})})
    r=conn.getresponse();body=r.read()
    if r.getheader('Set-Cookie'):cookie=r.getheader('Set-Cookie').split(';')[0]
    conn.close();result=json.loads(body)
    if isinstance(result,dict) and 'csrf' in result:csrf=result['csrf']
    return r.status,result

def first_bytes(video_id):
    """Seconds until the first 16 KB of audio arrive, as a browser's first Range request."""
    start=time.perf_counter();conn=http.client.HTTPConnection('127.0.0.1',port,timeout=40)
    conn.request('GET','/api/audio/'+video_id,headers={'Cookie':cookie,'Range':'bytes=0-'})
    r=conn.getresponse();chunk=r.read(16384);elapsed=time.perf_counter()-start
    info={'status':r.status,'content_range':r.getheader('Content-Range'),'accept_ranges':r.getheader('Accept-Ranges'),'bytes':len(chunk)}
    conn.close();return round(elapsed,3),info

report={'cold':[],'prefetched':[],'errors':[]}
call('/api/register','POST',{'name':'Latencia','email':'latency@example.com','password':'latency-test-password'})
queries=sys.argv[1:] or ['Olivia Dean','Feid','Rosalía','Quevedo']
seen=set()
for q in queries:
    t=time.perf_counter();status,result=call('/api/online/search?q='+quote(q)+'&source=ytmusic&filter=songs');search_s=round(time.perf_counter()-t,3)
    if status!=200:report['errors'].append({'query':q,'status':status,'error':result});continue
    t=time.perf_counter();_,profile=call('/api/online/artist?q='+quote(q));profile_s=round(time.perf_counter()-t,3)
    report.setdefault('search',[]).append({'query':q,'seconds':search_s,'items':len(result['items']),'artist_profile_seconds':profile_s,'artist':(profile.get('artist') or {}).get('name')})
    items=[v for v in result['items'] if v['provider_id'] not in seen]
    # Cold: a song deep in the list that the server has not prefetched yet.
    for v in items[-2:]:
        seen.add(v['provider_id']);seconds,info=first_bytes(v['provider_id'])
        report['cold'].append({'title':v['title'],'seconds':seconds,**info})
    # Prefetched: the client asks the server to resolve the top results while the user reads them.
    top=[v['provider_id'] for v in items[:5]];seen.update(top)
    call('/api/audio/prefetch','POST',{'video_ids':top});time.sleep(4)
    for video_id in top[:3]:
        seconds,info=first_bytes(video_id);report['prefetched'].append({'video_id':video_id,'seconds':seconds,**info})
for key in ('cold','prefetched'):
    values=[r['seconds'] for r in report[key] if r['status'] in (200,206)]
    if values:report[key+'_summary']={'n':len(values),'median':round(statistics.median(values),3),'max':max(values),'under_1_5s':sum(v<1.5 for v in values)}
first=next((s for s in report.get('search',[])),None)
if first:
    status,result=call('/api/online/search?q='+quote(queries[0])+'&source=ytmusic&filter=songs')
    if result.get('items'):
        t=time.perf_counter();status,radio=call('/api/online/radio/'+result['items'][0]['id']);report['radio']={'status':status,'seconds':round(time.perf_counter()-t,3),'tracks':len(radio.get('items',[])) if status==200 else radio}
        t=time.perf_counter();status,lyrics=call('/api/online/lyrics/'+result['items'][0]['id']);report['lyrics']={'status':status,'seconds':round(time.perf_counter()-t,3),'synced':lyrics.get('synced') if status==200 else lyrics}
(out/'latency-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k.endswith('summary') or k in ('search','radio','lyrics','errors')},ensure_ascii=False,indent=2))
server.shutdown();server.server_close()
