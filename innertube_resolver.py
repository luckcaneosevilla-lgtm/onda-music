"""Visitor-aware InnerTube audio with pooled TLS and bounded client retries."""
import http.client
import json
import re
import queue
import threading
import time
from urllib.parse import urlparse, parse_qs

from audio_resolver import YtDlpResolver
from music_provider import secure_context, get_response
from streaming import metric
from youtube_provider import ProviderError

CLIENT={'clientName':'ANDROID_VR','clientVersion':'1.60.19','deviceMake':'Oculus',
        'deviceModel':'Quest 3','osName':'Android','osVersion':'12L','hl':'en','gl':'US'}
HEADERS={'Content-Type':'application/json','User-Agent':'com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 12L; Quest 3) gzip',
         'X-YouTube-Client-Name':'28','X-YouTube-Client-Version':'1.60.19'}

PROFILES=[(CLIENT,HEADERS),
    ({'clientName':'VISIONOS','clientVersion':'1.02','deviceMake':'Apple','deviceModel':'RealityDevice17,1',
      'osName':'visionOS','osVersion':'26.5.23O471','hl':'en','gl':'US'},
     {'Content-Type':'application/json','User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15',
      'X-YouTube-Client-Name':'101','X-YouTube-Client-Version':'1.02'}),
    ({'clientName':'TVHTML5','clientVersion':'7.20260707.07.00','hl':'en','gl':'US'},
     {'Content-Type':'application/json','User-Agent':'Mozilla/5.0 (ChromiumStylePlatform) Cobalt/25.lts.30.1034943-gold',
      'X-YouTube-Client-Name':'7','X-YouTube-Client-Version':'7.20260707.07.00'})]


class InnerTubeResolver:
    def __init__(self,timeout=.7,preferred='VISIONOS'):
        self.timeout=timeout;self.cache={};self.failures={};self.lock=threading.Lock()
        self.connections=queue.LifoQueue(maxsize=4);self.visitor_lock=threading.Lock()
        self.visitor=None;self.visitor_expires=0;self.visitor_retry=0
        self.preferred=preferred if preferred in {p[0]['clientName'] for p in PROFILES} else 'VISIONOS'
        self.context=secure_context()

    def request(self,method,path,body=None,headers=None):
        # Connections are checked out exclusively; a new HTTP handler can reuse
        # the TLS session created for a previous visitor/player request.
        for attempt in range(2):
            try:connection=self.connections.get_nowait();reused=True
            except queue.Empty:
                connection=http.client.HTTPSConnection('www.youtube.com',timeout=self.timeout,context=self.context);reused=False
            try:
                connection.request(method,path,body,headers or HEADERS)
                response=connection.getresponse();raw=response.read(3*1024*1024+1)
                if len(raw)>3*1024*1024:raise ValueError('response_too_large')
                status=response.status
                try:self.connections.put_nowait(connection)
                except queue.Full:connection.close()
                return status,raw
            except (OSError,http.client.HTTPException,ValueError):
                connection.close()
                if not reused or attempt:raise

    @staticmethod
    def extract_visitor(raw):
        text=raw.decode('utf-8',errors='replace')
        match=re.search(r'"(?:visitorData|VISITOR_DATA)"\s*:\s*("(?:\\.|[^"\\])*")',text)
        if not match:return None
        token=json.loads(match[1])
        return token if isinstance(token,str) and 0<len(token)<=2048 and re.fullmatch(r'[A-Za-z0-9_%.=+/-]+',token) else None

    def visitor_data(self,video_id,rejected=None):
        # Only one request refreshes a rejected token; concurrent callers reuse it.
        with self.visitor_lock:
            now=time.monotonic()
            if self.visitor and self.visitor_expires>now and self.visitor!=rejected:return self.visitor
            if self.visitor_retry>now:return None
            started=time.perf_counter();token=None
            try:
                status,raw=self.request('POST','/youtubei/v1/visitor_id?prettyPrint=false',
                                        json.dumps({'context':{'client':CLIENT}}),HEADERS)
                if status==200:token=self.extract_visitor(raw)
                if not token:
                    status,raw=self.request('GET','/watch?v='+video_id,headers={'User-Agent':'Mozilla/5.0'})
                    if status==200:token=self.extract_visitor(raw)
            except (OSError,http.client.HTTPException,ValueError,TypeError):pass
            self.visitor=token;self.visitor_expires=now+1800 if token else 0
            self.visitor_retry=0 if token else now+10
            metric('innertube_visitor',success=bool(token),refreshed=rejected is not None,
                   visitor_ms=round((time.perf_counter()-started)*1000,2))
            return token

    def close(self):
        while True:
            try:self.connections.get_nowait().close()
            except queue.Empty:break

    @staticmethod
    def select(data,video_id):
        if data.get('playabilityStatus',{}).get('status')!='OK':return None
        if data.get('videoDetails',{}).get('videoId')!=video_id:return None
        candidates=[f for f in data.get('streamingData',{}).get('adaptiveFormats',[])
                    if isinstance(f,dict) and f.get('mimeType','').startswith(('audio/mp4','audio/webm'))
                    and f.get('url') and YtDlpResolver.allowed(f['url']) and f.get('itag')!=139]
        # Preserve AAC-LC for Safari; never choose HE-AAC's incorrect duration.
        candidates.sort(key=lambda f:(f.get('itag')!=140,not f['mimeType'].startswith('audio/mp4'),-int(f.get('bitrate') or 0)))
        for f in candidates:
            expires=time.time()+1800
            try:expires=min(expires,float(parse_qs(urlparse(f['url']).query).get('expire',[expires+60])[0])-60)
            except (ValueError,TypeError):continue
            if expires<=time.time():continue
            return {'url':f['url'],'mime':f['mimeType'].split(';')[0],'source':'innertube','expires':expires}
        return None

    def resolve(self,video_id):
        if not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):return None
        with self.lock:
            hit=self.cache.get(video_id)
            if hit and hit['expires']>time.time():return hit
            if self.failures.get(video_id,0)>time.monotonic():return None
        started=time.perf_counter();item=None;status='visitor_unavailable';client_name=None
        visitor=self.visitor_data(video_id);refreshed=False
        profiles=sorted(PROFILES,key=lambda p:p[0]['clientName']!=self.preferred)
        for client,base_headers in profiles if visitor else []:
            client_name=client['clientName']
            for attempt in range(2):
                try:
                    headers=dict(base_headers,**{'X-Goog-Visitor-Id':visitor})
                    body=json.dumps({'context':{'client':dict(client,visitorData=visitor)},'videoId':video_id})
                    code,raw=self.request('POST','/youtubei/v1/player?prettyPrint=false',body,headers)
                    data=json.loads(raw) if code==200 else {}
                    status=data.get('playabilityStatus',{}).get('status','http_'+str(code))
                    item=self.select(data,video_id)
                    metric('innertube_client',video_id=video_id,client=client_name,status=status,success=bool(item))
                    if item:
                        item.update(client=client_name,user_agent=base_headers['User-Agent'])
                        break
                    if status=='LOGIN_REQUIRED' and not refreshed:
                        refreshed=True;visitor=self.visitor_data(video_id,rejected=visitor)
                        if visitor:continue
                except (OSError,http.client.HTTPException,ValueError,TypeError,AttributeError):status='network_error'
                break
            if item or not visitor:break
        with self.lock:
            self.cache={k:v for k,v in self.cache.items() if v['expires']>time.time()}
            self.failures={k:v for k,v in self.failures.items() if v>time.monotonic()}
            if item:
                if len(self.cache)>=500:self.cache.pop(next(iter(self.cache)))
                self.cache[video_id]=item
            else:
                if len(self.failures)>=500:self.failures.pop(next(iter(self.failures)))
                self.failures[video_id]=time.monotonic()+30
        metric('innertube_resolve',video_id=video_id,status=status,client=client_name,success=bool(item),resolve_ms=round((time.perf_counter()-started)*1000,2))
        return item

    def invalidate(self,video_id):
        with self.lock:
            self.cache.pop(video_id,None);self.failures[video_id]=time.monotonic()+30

    def open(self,video_id,range_header=None):
        started=time.perf_counter();item=self.resolve(video_id)
        if not item:return None
        resolved=time.perf_counter();url=item['url'];connection=None
        try:
            from urllib.parse import urljoin
            for _ in range(4):
                if not YtDlpResolver.allowed(url):break
                headers={'User-Agent':item.get('user_agent',HEADERS['User-Agent'])}
                if range_header:headers['Range']=range_header
                connection,response=get_response(url,headers,timeout=1)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                mime=response.getheader('Content-Type','').split(';')[0]
                if response.status==416 or (response.status in (200,206) and mime.startswith('audio/')):
                    response.ivox_timings={'source':'innertube','resolve_ms':round((resolved-started)*1000,2),
                                          'upstream_headers_ms':round((time.perf_counter()-resolved)*1000,2),'prefix_hit':False,'client':item.get('client','ANDROID_VR')}
                    self.preferred=item.get('client','ANDROID_VR')
                    return connection,response,mime if mime.startswith('audio/') else item['mime']
                break
        except (OSError,http.client.HTTPException,ValueError,ProviderError):pass
        if connection:connection.close()
        self.invalidate(video_id)
        return None


resolver=InnerTubeResolver()


def resolve_url(video_id):
    item=resolver.resolve(video_id)
    return item['url'] if item else None
