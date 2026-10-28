"""Read-only feasibility probe; never logs signed media URLs or credentials."""
import http.client
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from music_provider import secure_context,get_response
from audio_resolver import YtDlpResolver

report=[]
for name,version in [('WEB','2.20260708.00.00'),('IOS','21.26.4')]:
 start=time.perf_counter();connection=None;row={'client':name,'id':'dQw4w9WgXcQ'}
 try:
  connection=http.client.HTTPSConnection('www.youtube.com',timeout=6,context=secure_context())
  body=json.dumps({'context':{'client':{'clientName':name,'clientVersion':version,'hl':'en','gl':'US'}},'videoId':row['id'],'contentCheckOk':True,'racyCheckOk':True})
  connection.request('POST','/youtubei/v1/player?prettyPrint=false',body,{'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
  response=connection.getresponse();data=json.loads(response.read(2*1024*1024));row['status']=response.status;row['playability']=data.get('playabilityStatus',{}).get('status')
  formats=data.get('streamingData',{}).get('adaptiveFormats',[])
  audio=[f for f in formats if f.get('mimeType','').startswith('audio/')]
  row.update(audio_formats=len(audio),direct_urls=sum(bool(f.get('url')) for f in audio),ciphered=sum(bool(f.get('signatureCipher')) for f in audio))
  direct=next((f['url'] for f in audio if f.get('url') and YtDlpResolver.allowed(f['url'])),None)
  if direct:
   media_conn,media=get_response(direct,{'Range':'bytes=0-1023'},timeout=6)
   try:row['media_status']=media.status;row['bytes']=len(media.read(1)) if media.status in (200,206) else 0
   finally:media_conn.close()
 except Exception as error:row['error']=type(error).__name__
 finally:
  if connection:connection.close()
 row['elapsed_ms']=round((time.perf_counter()-start)*1000,2);report.append(row)
out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
(out/'innertube-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
