"""Authenticated production preparation probe. Creates a dedicated QA account.
Credentials/cookies stay in memory; no signed URLs are printed or saved.
"""
import http.client,http.cookiejar,json,os,secrets,time,urllib.request,urllib.error,urllib.parse
os.environ.pop('SSLKEYLOGFILE',None)
ORIGIN='https://p01--onda-music--ftnmpttbnqmf.code.run'
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf=''
def api(path,data=None):
    headers={'User-Agent':UA,'Content-Type':'application/json'}
    if csrf:headers['X-CSRF-Token']=csrf
    request=urllib.request.Request(ORIGIN+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    with client.open(request,timeout=20) as response:return json.load(response)
def audio(item):
    request=urllib.request.Request(ORIGIN+'/api/audio/'+item['provider_id']+'?native=1',headers={'User-Agent':UA,'Range':'bytes=0-8191'})
    # Report HTTP request-to-headers separately from TCP/TLS connection setup.
    for handler in client.handlers:
        if isinstance(handler,urllib.request.HTTPCookieProcessor):handler.cookiejar.add_cookie_header(request)
    connection=http.client.HTTPSConnection(urllib.parse.urlsplit(ORIGIN).hostname,timeout=15)
    began=time.perf_counter()
    try:
        connection.connect();connected=time.perf_counter()
        connection.request('GET',request.selector,headers=dict(request.header_items()))
        response=connection.getresponse();headers_at=time.perf_counter();data=response.read(8192)
        return {'id':item['provider_id'],'status':response.status,'source':response.getheader('X-IVOX-Audio-Source'),'bytes':len(data),'connect_ms':round((connected-began)*1000),'ttfb_ms':round((headers_at-connected)*1000),'first_8k_ms':round((time.perf_counter()-connected)*1000),'server_timing':response.getheader('Server-Timing')}
    except Exception as error:return {'id':item['provider_id'],'error':type(error).__name__}
    finally:connection.close()
print('deployed',api('/version.json'),flush=True)
csrf=api('/api/register',{'name':'Audio preparation QA','email':secrets.token_hex(12)+'@ivox-qa.invalid','password':secrets.token_urlsafe(28)})['csrf']
items=api('/api/online/search?q='+urllib.parse.quote('Feid')+'&filter=songs')['items'][:2]
api('/api/audio/prefetch',{'video_ids':[item['provider_id'] for item in items],'priority':True})
for attempt in range(6):
    states=[{'id':item['provider_id'],'prepared':api('/api/audio/'+item['provider_id']+'?resolve=1').get('prepared')} for item in items]
    print(json.dumps({'attempt':attempt,'states':states}),flush=True)
    if all(state['prepared'] and state['prepared']['status'] in ('ready','failed') for state in states):break
    time.sleep(3)
for item in items:print(json.dumps(audio(item)),flush=True)
