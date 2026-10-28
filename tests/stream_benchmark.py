"""Live resolver benchmark. Never prints signed URLs or reads user accounts.

python tests/stream_benchmark.py baseline
Measures cold IDs with a warm resolver, URL-cache hits and prefix hits separately.
First bytes are not equivalent to audible browser playback.
"""
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audio_resolver import YtDlpResolver, OPTIONS

label=sys.argv[1] if len(sys.argv)>1 else 'current'
ids=sys.argv[2:] or ['dQw4w9WgXcQ','kJQP7kiw5Fk','fJ9rUzIMcZQ']
r=YtDlpResolver(size=1)
r.start(warm='jNQXAC9IVRw')
deadline=time.monotonic()+40
while not r.idle and time.monotonic()<deadline:time.sleep(.1)
report={'label':label,'metric':'resolver to first upstream audio byte; not browser playing','cold':[],'url_cached':[],'prefetched':[]}
try:
 for video_id in ids:
  r.invalidate(video_id)
  for mode in ('cold','url_cached','prefetched'):
   if mode=='prefetched':
    if not hasattr(r,'prefetch_fragment'):continue
    try:r.prefetch_fragment(video_id)
    except Exception:pass
   start=time.perf_counter();connection=None
   try:
    t=time.perf_counter();r.resolve(video_id);resolve_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();connection,response,mime=r.open(video_id,'bytes=0-')
    headers_ms=(time.perf_counter()-t)*1000
    first=response.read(1);first_ms=(time.perf_counter()-start)*1000
    report[mode].append({'id':video_id,'status':response.status,'bytes':len(first),'resolve_ms':round(resolve_ms,2),'headers_ms':round(headers_ms,2),'first_byte_ms':round(first_ms,2)})
   except Exception as error:
    report[mode].append({'id':video_id,'error':type(error).__name__,'elapsed_ms':round((time.perf_counter()-start)*1000,2)})
   finally:
    if connection:connection.close()
 for mode in ('cold','url_cached','prefetched'):
  values=[x['first_byte_ms'] for x in report[mode] if x.get('status') in (200,206) and x.get('bytes')]
  report[mode+'_summary']={'successes':len(values),'attempts':len(report[mode]),'median_ms':round(statistics.median(values),2) if values else None,'under_1000ms':sum(x<1000 for x in values)}
 out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
 (out/('stream-'+label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report),flush=True)
finally:
 for worker in list(r.idle):worker.kill()
