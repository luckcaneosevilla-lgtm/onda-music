"""On-demand, single-flight yt-dlp fallback; no resident audio subprocesses."""
from contextlib import closing
import http.client
import gc
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import socket
from urllib.parse import urlparse,parse_qs,urljoin
from music_provider import get_response
from youtube_provider import ProviderError
from streaming import PrefixCache, content_range, prefix_response, metric
from audio_cache import SharedAudioCache

# Set by speculative preparation threads. Speculative work never runs yt-dlp
# (docs/AUDIO_EXTRACTION_RECOVERY.md): with one slot it made a tapped song fail.
speculative=threading.local()
def is_speculative():return getattr(speculative,'on',False)

class AudioUnavailable(Exception):pass

_cookie_warned=[0.0]
def cookie_warning(reason):
    """Expired/rejected account cookies: say so in the logs, at most every 10 minutes."""
    if time.monotonic()-_cookie_warned[0]<600:return
    _cookie_warned[0]=time.monotonic()
    metric('ytdlp_cookies',status='rejected',reason=reason,action='export a fresh cookies.txt from the secondary account into IVOX_YTDLP_COOKIES')

# m4a first: Safari/iOS plays AAC everywhere; HLS/DASH manifests are never used and cost ~0.4 s.
# Plain AAC-LC (itag 140) before any other m4a: Safari reports HE-AAC (139, mp4a.40.5) at
# about twice its length, so the song ends and minutes of silence follow.
OPTIONS={'quiet':True,'no_warnings':True,'noprogress':True,'format':'bestaudio[ext=m4a][acodec^=mp4a.40.2]','noplaylist':True,'cachedir':False,'extractor_args':{'youtube':{'skip':['hls','dash']}}}
# Python equivalents of --http-chunk-size 8M and -N 8. Extraction uses
# download=False: these options only apply if yt-dlp itself downloads media.
OPTIONS.update(http_chunk_size=8*1024*1024,concurrent_fragment_downloads=8,
               socket_timeout=3,retries=0,extractor_retries=0)
# --force-ipv4: datacenter IPv6 ranges are flagged by YouTube more often.
OPTIONS['source_address']='0.0.0.0'

# Layer 1 - PO tokens. POT_PROVIDER_URL points at a private bgutil-ytdlp-pot-provider
# service; the yt-dlp plugin (requirements.txt) mints tokens there. With it, the first
# profile uses the clients that work best with PO tokens (mweb, android_vr).
POT_PROVIDER_URL=os.environ.get('POT_PROVIDER_URL','').strip().rstrip('/')
if POT_PROVIDER_URL:
    OPTIONS['extractor_args']['youtubepot-bgutilhttp']={'base_url':[POT_PROVIDER_URL]}

_pot_state={'checked':0.0,'up':None};_pot_lock=threading.Lock()
def pot_available():
    """True while the PO-token provider answers /ping (checked at most once a minute).

    A configured but unreachable provider cost every extraction a failed token
    attempt (8.7 s measured): yt-dlp then uses its usual clients directly.
    """
    if not POT_PROVIDER_URL:return False
    with _pot_lock:
        if time.monotonic()-_pot_state['checked']<60:return bool(_pot_state['up'])
        _pot_state['checked']=time.monotonic()
    try:
        parsed=urlparse(POT_PROVIDER_URL)
        factory=http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection
        with closing(factory(parsed.netloc,timeout=1)) as conn:
            conn.request('GET',(parsed.path or '')+'/ping');up=conn.getresponse().status==200
    except (OSError,http.client.HTTPException):up=False
    with _pot_lock:
        # The first check is always logged (up or down), later ones only on change.
        if up!=_pot_state['up']:metric('pot_provider',status='up' if up else 'down',url=POT_PROVIDER_URL)
        _pot_state['up']=up
    return up

# Layer 4 - request pacing, opt-in. Each extraction makes several YouTube requests,
# so a 1 s pause per request would exceed the extraction timeout and fail Play.
# (--sleep-interval/--max-sleep-interval only apply to downloads, which never happen here.)
try:_sleep=float(os.environ.get('IVOX_YTDLP_SLEEP_REQUESTS','0') or 0)
except ValueError:_sleep=0
if _sleep>0:OPTIONS['sleep_interval_requests']=min(_sleep,2)

# Layer 2 - cookies of a secondary, disposable YouTube account (Netscape format), from
# a secret's contents (IVOX_YTDLP_COOKIES) or a mounted file (IVOX_YTDLP_COOKIES_FILE).
# They are copied to a private temporary file because yt-dlp writes refreshed cookies
# back and secret mounts are read-only. Contents are never logged.
def _prepare_cookies():
    text=os.environ.get('IVOX_YTDLP_COOKIES','')
    source=os.environ.get('IVOX_YTDLP_COOKIES_FILE','').strip()
    if not text and source:
        try:text=Path(source).read_text(encoding='utf-8')
        except OSError:metric('ytdlp_cookies',status='unreadable');return None
    if not text.strip():return None
    # Secret UIs often store a pasted file with literal \t and \n escapes.
    if '\t' not in text and '\t' in text:text=text.replace('\t','\t').replace('\n','\n')
    if not text.lstrip().startswith(('# Netscape','# HTTP Cookie File')):
        text='# Netscape HTTP Cookie File\n'+text
    try:
        handle,path=tempfile.mkstemp(prefix='ivox-cookies-',suffix='.txt')
        with os.fdopen(handle,'w',encoding='utf-8') as f:f.write(text if text.endswith('\n') else text+'\n')
        os.chmod(path,0o600)
    except OSError:metric('ytdlp_cookies',status='unwritable');return None
    metric('ytdlp_cookies',status='loaded',entries=sum(1 for line in text.splitlines() if line.strip() and not line.startswith('#')))
    return path
COOKIE_FILE=_prepare_cookies()
if COOKIE_FILE:OPTIONS['cookiefile']=COOKIE_FILE
# One request per child. Only one YoutubeDL instance exists at a time, and its
# context manager closes any challenge runtime before trying another profile.
WORKER='''import copy,gc,json,re,sys
from yt_dlp import YoutubeDL
class Quiet:
    def debug(self,m):pass
    info=warning=error=debug
options=json.loads(sys.argv[1]);pot=options.pop('ivox_pot',False)
parts=sys.stdin.readline().strip().split();video_id=parts[0] if parts else ''
mode=parts[1] if len(parts)>1 else '';url=None;failure='extract_failed';engine='yt-dlp'
def reason(error):
    text=str(error).lower()
    for needle,code in [('cookies are no longer valid','cookies_expired'),('not a bot','bot_check'),('sign in','sign_in_required'),('javascript runtime','javascript_required'),('requested format','format_unavailable'),('http error 403','upstream_403'),('timed out','network_timeout'),('not available','video_unavailable')]:
        if needle in text:return code
    return 'extract_failed'
profiles=[('yt-dlp-direct',['visionos']),('yt-dlp-compatible',None),('yt-dlp-alt',['tv','web_safari','mweb','visionos'])]
if pot:profiles.insert(0,('yt-dlp-pot',['mweb','android_vr']))
if mode=='standard':profiles=profiles[1:]
elif mode=='compatible':profiles=profiles[2:]+profiles[1:2]
if re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
    for engine,clients in profiles:
        config=copy.deepcopy(options)
        if clients:config['extractor_args']['youtube']['player_client']=clients
        try:
            with YoutubeDL(dict(config,logger=Quiet())) as ydl:
                info=ydl.extract_info('https://www.youtube.com/watch?v='+video_id,download=False)
                url=info.get('url');del info
        except Exception as error:failure=reason(error)
        gc.collect()
        if url:break
print(json.dumps({'id':video_id,'url':url,'engine':engine,'error':failure if not url else None}),flush=True)
'''


def kill_process_tree(process):
    """A timed-out yt-dlp must not leave a Deno challenge solver behind."""
    try:
        if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
        elif process.poll() is None:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired):
        if process.poll() is None:process.kill()
    if process.poll() is None:process.kill()
    process.wait(timeout=3)


class YtDlpResolver:
    def __init__(self,size=1,recycle=None):
        # Retain constructor compatibility, but never create a process pool.
        self.cache={};self.failures={};self.failure_reasons={};self.refreshes={};self.guard=threading.Lock();self.store=None
        self.locks={};self.timeout=max(1,min(12,float(os.environ.get('IVOX_EXTRACT_TIMEOUT','9'))));self.slot=threading.BoundedSemaphore(1)
        self.engines={};self.standard={};self.compatible={}
        self.prefixes=PrefixCache(limit=2*1024*1024,fragment=65536)
        self.shared=None;self.cache_ttl=1800
    def persist(self,path,cloud_factory=None,scope=None):
        # Resolved URLs survive restarts; rows still honour the per-URL expiry.
        self.store=str(path)
        try:
            with closing(sqlite3.connect(self.store,timeout=5)) as c,c:
                c.execute('CREATE TABLE IF NOT EXISTS audio_url_cache(video_id TEXT PRIMARY KEY,url TEXT NOT NULL,mime TEXT NOT NULL,expires REAL NOT NULL)')
                c.execute('DELETE FROM audio_url_cache WHERE expires<=?',(time.time(),))
                rows=c.execute('SELECT video_id,url,mime,expires FROM audio_url_cache ORDER BY expires DESC LIMIT 300').fetchall()
        except sqlite3.Error:self.store=None;rows=[]
        with self.guard:
            for video_id,url,mime,expires in reversed(rows):
                if self.reusable(url):self.cache[video_id]={'url':url,'mime':mime,'expires':expires}
        if self.shared:self.shared.close();self.shared=None
        if cloud_factory:
            self.shared=SharedAudioCache(cloud_factory,scope or socket.gethostname(),self.merge_shared)
    def merge_shared(self,rows):
        with self.guard:
            for video_id,url,mime,expires in rows:
                if not self.reusable(url) or not mime.startswith('audio/') or expires<=time.time()+120:continue
                current=self.cache.get(video_id)
                if not current or current['expires']<expires:self.cache[video_id]={'url':url,'mime':mime,'expires':expires}
            while len(self.cache)>300:self.cache.pop(next(iter(self.cache)))
    def save(self,sql,args):
        if not self.store:return
        try:
            with closing(sqlite3.connect(self.store,timeout=5)) as c,c:c.execute(sql,args)
        except sqlite3.Error:pass
    def run(self,video_id,background=False):
        if background:raise AudioUnavailable('background_deferred')
        if is_speculative():raise AudioUnavailable('background_deferred')
        # Bounded wait behind another listener's Play instead of failing in 0.2 s.
        if not self.slot.acquire(timeout=4):raise AudioUnavailable('worker_unavailable')
        process=None;started=time.perf_counter();engine='yt-dlp';url=None
        try:
            env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'};env['PYTHONIOENCODING']='utf-8'
            vendor=Path(__file__).resolve().parent/'.vendor'
            if vendor.exists() and importlib.util.find_spec('yt_dlp') is None:
                env['PYTHONPATH']=str(vendor)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            process=subprocess.Popen([sys.executable,'-c',WORKER,json.dumps(dict(OPTIONS,ivox_pot=pot_available()))],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',
                env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
            mode=' standard' if self.standard.get(video_id,0)>time.time() else ' compatible' if self.compatible.get(video_id,0)>time.time() else ''
            output,_=process.communicate(video_id+mode+'\n',timeout=self.timeout)
            if process.returncode!=0:raise AudioUnavailable('worker_failed')
            if len(output)>16384:raise AudioUnavailable('invalid_worker_response')
            answer=json.loads(output)
            if answer.get('id')!=video_id:raise AudioUnavailable('invalid_worker_response')
            engine=answer.get('engine','yt-dlp');url=answer.get('url')
            if not url:
                error=answer.get('error') or 'extract_failed'
                if COOKIE_FILE and error in ('cookies_expired','sign_in_required'):cookie_warning(error)
                raise AudioUnavailable(error)
            with self.guard:
                if len(self.engines)>=300:self.engines.pop(next(iter(self.engines)))
                self.engines[video_id]=engine
            return url
        except subprocess.TimeoutExpired:raise AudioUnavailable('extraction_timeout') from None
        except (OSError,ValueError):raise AudioUnavailable('worker_failed') from None
        finally:
            try:
                if process:
                    try:kill_process_tree(process)
                    finally:
                        for stream in (process.stdin,process.stdout):
                            if stream:stream.close()
            finally:
                self.slot.release();gc.collect()
                metric('audio_resolve',video_id=video_id,engine=engine,background=False,
                       resolve_ms=round((time.perf_counter()-started)*1000,2),success=bool(url))

    def prefetch(self,video_id):pass
    def prefetch_many(self,ids):
        """Compatibility for older clients. yt-dlp runs only on actual fallback."""
        return
    def duration(self,video_id):
        # The resolved stream URL carries the audio's real length ('dur', in seconds).
        with self.guard:hit=self.cache.get(video_id)
        try:value=float(parse_qs(urlparse(hit['url']).query)['dur'][0]) if hit else 0
        except (KeyError,ValueError,IndexError):value=0
        return round(value,1) if 0<value<6*3600 else None
    @staticmethod
    def reusable(url):
        # URLs saved before AAC-LC was preferred may point at HE-AAC (itag 139): resolve again.
        return YtDlpResolver.allowed(url) and parse_qs(urlparse(url).query).get('itag',[''])[0]!='139' and parse_qs(urlparse(url).query).get('mime',[''])[0]=='audio/mp4'
    @staticmethod
    def allowed(url):
        try:
            p=urlparse(url)
            return isinstance(url,str) and len(url)<=8192 and p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and bool(p.hostname and p.hostname.endswith('.googlevideo.com'))
        except ValueError:return False
    def resolve(self,video_id,background=False):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise AudioUnavailable()
        # Valid hits never wait behind an in-flight refresh or unrelated video.
        with self.guard:hit=self.cache.get(video_id)
        if not background and hit and hit['expires']>time.time():
            return hit
        with self.guard:
            entry=self.locks.setdefault(video_id,[threading.Lock(),0]);entry[1]+=1
        # A Play joins an extraction already running for this song (preparation,
        # Safari's parallel range probe) and then reuses its cached result.
        lock=entry[0];acquired=lock.acquire(timeout=.2 if background or is_speculative() else 2*self.timeout+2)
        if not acquired:
            with self.guard:
                entry[1]-=1
                if not entry[1]:self.locks.pop(video_id,None)
            raise AudioUnavailable('resolution_busy')
        try:
            with self.guard:
                cached=self.cache.get(video_id)
                failed=self.failures.get(video_id,0)>time.time()
            if cached and cached['expires']>time.time()+(600 if background else 0):
                return cached
            if failed:raise AudioUnavailable(self.failure_reasons.get(video_id,'recent_extraction_failure'))
            try:
                url=self.run(video_id,background)
                if not url or not self.allowed(url):raise AudioUnavailable('invalid_stream_url')
                params=parse_qs(urlparse(url).query);mime=params.get('mime',['audio/webm'])[0]
                if mime!='audio/mp4' or not self.reusable(url):raise AudioUnavailable('invalid_stream_type')
                expiry=time.time()+self.cache_ttl
                try:expiry=min(expiry,int(params.get('expire',[expiry+120])[0])-120)
                except (ValueError,TypeError):pass
                if expiry<=time.time():raise AudioUnavailable('expired_stream_url')
                item={'url':url,'mime':mime,'expires':expiry,'engine':self.engines.get(video_id,'yt-dlp')}
                with self.guard:
                    self.cache={k:v for k,v in self.cache.items() if v['expires']>time.time()}
                    if len(self.cache)>=300:self.cache.pop(next(iter(self.cache)))
                    self.cache[video_id]=item;self.failures.pop(video_id,None)
                self.save('INSERT OR REPLACE INTO audio_url_cache VALUES(?,?,?,?)',(video_id,url,mime,expiry))
                if self.shared:self.shared.enqueue(video_id,item)
                return item
            except AudioUnavailable as error:
                # A busy fallback slot is not an unplayable song.
                # Neither is a deferred speculative attempt: recording it made the
                # listener's Play right after a search fail with background_deferred.
                if not background and not is_speculative() and str(error) not in ('worker_unavailable','background_deferred'):
                    with self.guard:
                        self.failures={k:v for k,v in self.failures.items() if v>time.time()}
                        if len(self.failures)>=200:self.failures.pop(next(iter(self.failures)))
                        self.failures[video_id]=time.time()+30
                        self.failure_reasons={k:v for k,v in self.failure_reasons.items() if k in self.failures}
                        self.failure_reasons[video_id]=str(error) or 'extract_failed'
                raise
        finally:
            lock.release()
            with self.guard:
                entry[1]-=1
                if not entry[1]:self.locks.pop(video_id,None)
    def invalidate(self,video_id):
        with self.guard:self.cache.pop(video_id,None)
        self.prefixes.remove(video_id)
        self.save('DELETE FROM audio_url_cache WHERE video_id=?',(video_id,))
        if self.shared:self.shared.enqueue(video_id,None)
    def refresh(self,video_id):
        """One explicit fresh attempt per video/30 s, shared by concurrent listeners."""
        with self.guard:
            stamp=time.time()
            self.refreshes={k:v for k,v in self.refreshes.items() if v>stamp}
            if self.refreshes.get(video_id,0)>stamp:return False
            if len(self.refreshes)>=300:self.refreshes.pop(next(iter(self.refreshes)))
            self.refreshes[video_id]=stamp+30
            self.failures.pop(video_id,None);self.failure_reasons.pop(video_id,None)
            self.compatible[video_id]=stamp+600
        self.invalidate(video_id)
        return True
    def prefetch_fragment(self,video_id):
        started=time.perf_counter();item=self.resolve(video_id,background=True)
        if self.prefixes.get(video_id,item['url']):return
        connection=None
        try:
            connection,response=get_response(item['url'],{'Range':f'bytes=0-{self.prefixes.fragment-1}'},timeout=5)
            bounds=content_range(response);mime=response.getheader('Content-Type','').split(';')[0]
            if response.status!=206 or not bounds or bounds[0]!=0 or bounds[1]!=min(self.prefixes.fragment-1,bounds[2]-1) or mime!=item['mime']:return
            # A fixed upper bound, even if an upstream lies about its body length.
            data=response.read(min(self.prefixes.fragment,bounds[1]+1))
            if len(data)!=bounds[1]+1:return
            prefix={**item,'data':data,'total':bounds[2],'ETag':response.getheader('ETag'),'Last-Modified':response.getheader('Last-Modified')}
            self.prefixes.put(video_id,prefix)
            metric('audio_prefetch',video_id=video_id,bytes=len(data),total_ms=round((time.perf_counter()-started)*1000,2))
        except (OSError,http.client.HTTPException,ProviderError):pass
        finally:
            if connection:connection.close()
    def open(self,video_id,range_header=None,retry=True):
        started=time.perf_counter()
        item=self.resolve(video_id);url=item['url'];connection=None
        resolved=time.perf_counter()
        prefix=self.prefixes.get(video_id,url)
        if prefix:
            response=prefix_response(prefix,range_header,get_response)
            if response:
                response.ivox_timings['resolve_ms']=round((resolved-started)*1000,2)
                return response,response,item['mime']
        try:
            for _ in range(4):
                if not self.allowed(url):raise AudioUnavailable()
                remaining=3-(time.perf_counter()-resolved)
                if remaining<=0:raise AudioUnavailable('upstream_timeout')
                connection,response=get_response(url,{'Range':range_header} if range_header else {},timeout=remaining)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                if response.status==416:return connection,response,item['mime']
                mime=response.getheader('Content-Type','').split(';')[0]
                if response.status not in (200,206):raise AudioUnavailable(f'upstream_{response.status}')
                if mime not in ('audio/mp4','application/octet-stream'):raise AudioUnavailable('upstream_type')
                response.ivox_timings={'resolve_ms':round((resolved-started)*1000,2),'upstream_headers_ms':round((time.perf_counter()-resolved)*1000,2),'prefix_hit':False}
                return connection,response,mime if mime.startswith('audio/') else item['mime']
            raise AudioUnavailable()
        except (OSError,http.client.HTTPException,AudioUnavailable,ProviderError) as error:
            if connection:connection.close()
            self.invalidate(video_id)
            with self.guard:
                self.compatible={k:v for k,v in self.compatible.items() if v>time.time()}
                if len(self.compatible)>=300:self.compatible.pop(next(iter(self.compatible)))
                self.compatible[video_id]=time.time()+600
                self.standard={k:v for k,v in self.standard.items() if v>time.time()}
                if item.get('engine')=='yt-dlp-direct':self.standard[video_id]=time.time()+600
                else:self.standard.pop(video_id,None)
            # A cached URL can be rejected early (e.g. new public IP); resolve once more.
            if retry:return self.open(video_id,range_header,False)
            raise AudioUnavailable(str(error) if isinstance(error,AudioUnavailable) else type(error).__name__) from None

# At most one on-demand fallback process; no subprocess is kept between plays.
resolver=YtDlpResolver(size=1)
