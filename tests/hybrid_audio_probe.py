"""Live backend first-audio-byte benchmark (not browser playing latency).

Run: python tests/hybrid_audio_probe.py
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
        for query,expected,target in [('RAC Next To You','audius',.5),('Rick Astley Never Gonna Give You Up','yt-dlp',2)]:
            try:
                songs=[music.normalize(item) for item in music.search(query,'songs',5)]
                songs=[s for s in songs if s]
                if not songs:raise RuntimeError('catalog_no_results')
                metadata=songs[0]
                video_id=metadata['provider_id']
                with onda.db() as db:onda.provider.store(db,[metadata]);db.commit()
                # Match a search response preparing this result before Play.
                prepared=time.perf_counter();router.prefetch_many([metadata])
                deadline=time.monotonic()+30
                while router.running and time.monotonic()<deadline:time.sleep(.05)
                preparation_seconds=round(time.perf_counter()-prepared,3)
                for state in ('after_search','repeat'):
                    started=time.perf_counter();connection=None
                    result={'query':query,'title':metadata['title'],'artist':metadata['channel'],
                            'expected_source':expected,'state':state,'target_seconds':target,
                            'preparation_seconds':preparation_seconds,'preparation_complete':not router.running}
                    try:
                        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
                        connection.request('GET','/api/audio/'+video_id,headers={'Cookie':client.cookie,'Range':'bytes=0-32767'})
                        response=connection.getresponse();mime=response.getheader('Content-Type')
                        first=response.read1(4096)
                        seconds=time.perf_counter()-started
                        result.update(seconds=round(seconds,6),status=response.status,mime=mime,bytes=len(first),
                                      timings={'source':response.getheader('X-IVOX-Audio-Source'),
                                               'server_timing':response.getheader('Server-Timing'),
                                               'cache':response.getheader('X-IVOX-Audio-Cache')})
                        result['passed']=bool(first) and result['timings'].get('source')==expected and seconds<target
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
        if music.process:
            music.process.kill();music.process.wait(timeout=5)
        gc.collect();temporary.cleanup()
    output=Path(__file__).resolve().parents[1]/'test-results'/'hybrid-audio-probe.json'
    output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':main()
