"""Isolated mobile browser test: real audio decoding, deterministic range fixture.
Does not claim to measure production Google/Northflank latency.
"""
import base64, io, json, math, wave
from pathlib import Path
from playwright.sync_api import sync_playwright

stream=io.BytesIO()
with wave.open(stream,'wb') as out:
    out.setparams((1,2,22050,0,'NONE','not compressed'))
    out.writeframes(b''.join(int(1000*math.sin(i*2*math.pi*440/22050)).to_bytes(2,'little',signed=True) for i in range(22050*20)))
data=stream.getvalue()
source=Path('web/online.js').read_text(encoding='utf-8')
block=source[source.index('const memoryAudio='):source.index('// Resolving a YouTube')]
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(**p.devices['Pixel 7'])
    page=context.new_page()
    requests=[]
    def route(r):
        if '/audio' in r.request.url:
            requests.append(r.request.headers.get('range'))
            r.fulfill(status=206,body=data[:512000],headers={'Content-Type':'audio/wav','Content-Range':f'bytes 0-511999/{len(data)}'})
        else:r.fulfill(body='<audio></audio>',content_type='text/html')
    page.route('**/*',route)
    page.goto('https://fixture.test/')
    page.evaluate('''()=>{window.me={};window.cachedIds=new Set();window.onlineAudio=document.querySelector('audio');window.api=async()=>({url:'/audio',fallback_url:'/audio',mime:'audio/wav'});window.startOnline=()=>{};window.playbackWanted=true}''')
    page.add_script_tag(content=block)
    page.evaluate("async()=>{await warmMemoryAudio({id:'piped:abcdefghijk',provider:'piped',provider_id:'abcdefghijk'});if(memoryAudio.size!==1)throw Error('prefix missing')}")
    timings=[]
    for _ in range(5):
        timings.append(page.evaluate('''()=>new Promise((resolve,reject)=>{const entry=memoryAudio.get('piped:abcdefghijk');onlineAudio.pause();onlineAudio.src=entry.url;const t=performance.now();onlineAudio.addEventListener('playing',()=>resolve(Math.round(performance.now()-t)),{once:true});onlineAudio.play().catch(reject)})'''))
    assert requests==['bytes=0-511999'], requests
    print(json.dumps({'profile':'Pixel 7 Chromium','fixture':'synthetic WAV, 500 KiB prefix','click_to_playing_ms':timings,'network_requests_during_play':0,'all_under_500ms':max(timings)<500}))
    browser.close()

