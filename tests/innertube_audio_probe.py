"""Cold InnerTube -> yt-dlp HTTP first-audio-byte benchmark.

Run: python tests/innertube_audio_probe.py
Does not persist URLs, credentials or media. Writes timings to test-results.
"""
import json
from pathlib import Path
import sys
import time
import http.client
import threading
import gc

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audio_router import AudioRouter
from audio_resolver import YtDlpResolver
from ytmusic_search import MusicSearch


def main():
    music=MusicSearch();fallback=YtDlpResolver(size=1);router=AudioRouter(fallback)
    # Reuse the isolated test server configuration: never touch live users/Turso.
    from test_security import onda, Client, temporary
    onda.resolver=fallback;onda.audio_router=router;onda.init()
    server=onda.ThreadingHTTPServer(('127.0.0.1',0),onda.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    client=Client(server.server_port)
    status,_=client.request('/api/register','POST',{'name':'Benchmark','email':'hybrid@qa.invalid','password':'hybrid-probe-password'})
    assert status==200
    results=[]
    try:
        for query,expected,target in [('Feid Luna','innertube',1),('Bad Bunny DtMF','innertube',1),('Rick Astley Never Gonna Give You Up','innertube',1)]:
            try:
                songs=[music.normalize(item) for item in music.search(query,'songs',5)]
                songs=[s for s in songs if s]
                if not songs:raise RuntimeError('catalog_no_results')
                metadata=songs[0]
                video_id=metadata['provider_id']
                with onda.db() as db:onda.provider.store(db,[metadata]);db.commit()
                # No audio workers or prefetch: first Play includes any fallback startup.
                prepared=time.perf_counter()
                deadline=time.monotonic()+30
                preparation_seconds=round(time.perf_counter()-prepared,3)
                for state in ('cold_audio','repeat'):
                    started=time.perf_counter();connection=None
                    result={'query':query,'title':metadata['title'],'artist':metadata['channel'],
                            'expected_source':expected,'state':state,'target_seconds':target,
                            'resident_ytdlp_workers':0}
                    try:
                        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
                        connection.request('GET','/api/audio/'+video_id,headers={'Cookie':client.cookie,'Range':'bytes=0-32767'})
                        response=connection.getresponse();mime=response.getheader('Content-Type')
                        first=response.read1(1)
                        seconds=time.perf_counter()-started
                        result.update(seconds=round(seconds,6),status=response.status,mime=mime,bytes=len(first),
                                      timings={'source':response.getheader('X-IVOX-Audio-Source'),
                                               'client':response.getheader('X-IVOX-Audio-Client'),
                                               'server_timing':response.getheader('Server-Timing'),
                                               'cache':response.getheader('X-IVOX-Audio-Cache')})
                        result['passed']=response.status in (200,206) and bool(first) and result['timings'].get('source')==expected and seconds<target
                        result['baseline_seconds']=4.03
                        result['speedup_vs_baseline']=round(4.03/seconds,2)
                        result['audio_ok']=response.status in (200,206) and bool(first) and str(mime).startswith('audio/')
                    except Exception as error:
                        result.update(seconds=round(time.perf_counter()-started,3),error=type(error).__name__+': '+str(error),passed=False)
                    finally:
                        if connection:connection.close()
                    results.append(result);print(json.dumps(result),flush=True)
            except Exception as error:
                result={'query':query,'error':type(error).__name__+': '+str(error),'passed':False}
                results.append(result);print(json.dumps(result),flush=True)
    finally:
        server.shutdown();server.server_close()
        router.inner.close()
        if music.process:
            music.process.kill();music.process.wait(timeout=5)
        gc.collect();temporary.cleanup()
    output=Path(__file__).resolve().parents[1]/'test-results'/'innertube-audio-probe.json'
    output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':main()
