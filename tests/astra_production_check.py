"""Production QA: one isolated account, native iPhone route, real media decode.

Chromium with an iPhone UA is not Safari hardware. Credentials remain in memory.
"""
import json
import os
from pathlib import Path
import secrets
from playwright.sync_api import sync_playwright

os.environ.pop('SSLKEYLOGFILE',None)
origin='https://p01--onda-music--ftnmpttbnqmf.code.run'
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
with sync_playwright() as p:
 browser=p.chromium.launch()
 context=browser.new_context(user_agent=UA,viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
 version=context.request.get(origin+'/version.json').json()
 assert version['release']=='5.0',version
 response=context.request.post(origin+'/api/register',data={'name':'Astra playback QA','email':secrets.token_hex(12)+'@ivox-qa.invalid','password':secrets.token_urlsafe(28)})
 assert response.ok,{'registration_status':response.status}
 data=context.request.get(origin+'/api/online/search',params={'q':'Feid','filter':'songs'},timeout=45000).json()
 items=[v for v in data['items'] if v.get('provider')=='piped']
 assert items,'No production search results'
 page=context.new_page();errors=[];sources=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 def record(response):
  if '/api/audio/' in response.url and 'resolve=1' not in response.url:
   headers=response.headers
   sources.append({'status':response.status,'source':headers.get('x-ivox-audio-source'),'mime':headers.get('content-type')})
 page.on('response',record)
 page.goto(origin);page.wait_for_function('()=>typeof playList==="function"&&me!==null')
 page.locator('.release-start').wait_for(state='visible')
 page.locator('.release-start').click()
 page.evaluate("""()=>{
   setAutoplay(false);ensureRadio=()=>{};window.qaTraces=[];
   window.addEventListener('ivox-playback',e=>{const {stage,elapsed_ms}=e.detail;qaTraces.push({stage,elapsed_ms})});
   const b=document.createElement('button');b.id='qa-play';b.textContent='QA Play';
   Object.assign(b.style,{position:'fixed',top:'150px',left:'20px',zIndex:20000});
   b.onclick=()=>{qaTraces=[];playList([window.qaSelected])};document.body.append(b)
 }""")
 results=[]
 for label,index in [('cold-fourth',min(3,len(items)-1)),('warm-first',0)]:
  page.evaluate('item=>{window.qaSelected=item}',items[index]);page.locator('#qa-play').click()
  try:
   page.wait_for_function('()=>!onlineAudio.paused&&onlineAudio.currentTime>6&&onlineAudio.readyState>=2',timeout=45000)
   marks=page.evaluate('()=>qaTraces')
   page.evaluate('()=>{onlineAudio.currentTime=35}')
   page.wait_for_function('()=>onlineAudio.currentTime>35.25&&!onlineAudio.paused&&!onlineAudio.seeking',timeout=15000)
   results.append({'case':label,'marks':marks,'continuation':'PASS','seek_35s':'PASS'})
  except Exception:
   print(json.dumps({'version':version,'failed_case':label,'marks':page.evaluate('()=>qaTraces'),'sources':sources,'state':page.evaluate('()=>({state:document.querySelector("#online-player").dataset.state,message:document.querySelector("#playback-status").textContent,media_error:onlineAudio.error?.code})')}),flush=True)
   raise
  page.evaluate('()=>pausePlayback()')
 assert not errors,errors
 report={'version':version,'engine':'Chromium with iPhone UA; not real Safari','results':results,'sources':sources,'browser_errors':errors}
 out=Path(__file__).resolve().parents[1]/'test-results'/'astra-production.json';out.write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report))
 browser.close()
