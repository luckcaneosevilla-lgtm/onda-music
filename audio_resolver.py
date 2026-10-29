"""Level 1: resolve pure audio with a pool of warm yt-dlp workers; keep URLs server-side."""
from collections import deque
from contextlib import closing
import http.client
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import socket
from urllib.parse import urlparse,parse_qs,urljoin
from music_provider import get_response
from youtube_provider import ProviderError
from streaming import PrefixCache, content_range, prefix_response, metric
from audio_cache import SharedAudioCache

class AudioUnavailable(Exception):pass

# m4a first: Safari/iOS plays AAC everywhere; HLS/DASH manifests are never used and cost ~0.4 s.
# Plain AAC-LC (itag 140) before any other m4a: Safari reports HE-AAC (139, mp4a.40.5) at
# about twice its length, so the song ends and minutes of silence follow.
OPTIONS={'quiet':True,'no_warnings':True,'noprogress':True,'format':'bestaudio[acodec^=mp4a.40.2]/bestaudio[ext=m4a]/bestaudio','noplaylist':True,'cachedir':False,'extractor_args':{'youtube':{'skip':['hls','dash']}}}
# A worker imports yt-dlp once and keeps one YoutubeDL alive: the player JS and
# signature functions stay in memory, so each new song costs ~1 s instead of a
# fresh ~2.7 s process. One video id per stdin line, one JSON line back.
WORKER='''import copy,json,os,re,sys
from yt_dlp import YoutubeDL
class Quiet:
    def debug(self,m):pass
    info=warning=error=debug
options=json.loads(sys.argv[1]);fast_options=copy.deepcopy(options)
fast_options['extractor_args']['youtube']['player_skip']=['webpage','configs','initial_data']
# Retry after YouTube refused a stream (datacenter IPs are often blocked for one client):
# other player clients. tv/web_safari/mweb need a JS runtime (deno + yt-dlp-ejs, see Dockerfile).
alt_options=copy.deepcopy(options);alt_options['extractor_args']['youtube']['player_client']=['tv','web_safari','mweb','visionos']
ydl=YoutubeDL(dict(options,logger=Quiet()))
fast=YoutubeDL(dict(fast_options,logger=Quiet()))
alt=YoutubeDL(dict(alt_options,logger=Quiet()))
print("ready",flush=True)
for line in sys.stdin:
    parts=line.strip().split();video_id=parts[0] if parts else '';url=None;engine='yt-dlp-fast'
    if re.fullmatch(r"[A-Za-z0-9_-]{11}",video_id):
        # Off by default: with yt-dlp 2026.10.26 the skip-webpage extraction failed on every
        # tested song and only added ~0.1 s before the compatible path. IVOX_YTDLP_FAST=1 re-enables it.
        if len(parts)==1 and os.environ.get('IVOX_YTDLP_FAST','0')=='1':
            try:url=fast.extract_info("https://www.youtube.com/watch?v="+video_id,download=False).get("url")
            except Exception:pass
        if not url:
            for engine,client in ((('yt-dlp-alt',alt),('yt-dlp-compatible',ydl)) if len(parts)>1 else (('yt-dlp-compatible',ydl),)):
                try:url=client.extract_info("https://www.youtube.com/watch?v="+video_id,download=False).get("url")
                except Exception:pass
                if url:break
    print(json.dumps({"id":video_id,"url":url,"engine":engine}),flush=True)
'''

class Worker:
    """One warm yt-dlp process. A reader thread turns stdout into a queue so every answer can time out."""
    def __init__(self,process):
        self.process=process;self.lines=queue.Queue();self.served=0
        threading.Thread(target=self.pump,daemon=True).start()
    def pump(self):
        try:
            for line in self.process.stdout:self.lines.put(line)
        except (OSError,ValueError):pass
        self.lines.put(None)
    def alive(self):return self.process.poll() is None
    def ready(self,timeout):
        line=self.lines.get(timeout=timeout)
        if line is None or line.strip()!='ready':raise OSError('worker did not start')
    def ask(self,video_id,timeout,compatible=False):
        self.process.stdin.write(video_id+(' compatible' if compatible else '')+'\n');self.process.stdin.flush()
        line=self.lines.get(timeout=timeout)
        if line is None:raise OSError('worker exited')
        self.served+=1
        answer=json.loads(line)
        if answer.get('id')!=video_id:raise OSError('worker out of sync')
        self.last_engine=answer.get('engine','yt-dlp')
        return answer.get('url')
    def kill(self):
        try:self.process.kill()
        except OSError:pass
        try:
            if hasattr(self.process,'wait'):self.process.wait(timeout=1)
            for name in ('stdin','stdout'):
                stream=getattr(self.process,name,None)
                if hasattr(stream,'close'):stream.close()
        except (OSError,ValueError,subprocess.TimeoutExpired):pass

class YtDlpResolver:
    def __init__(self,size=3,recycle=150):
        self.cache={};self.failures={};self.guard=threading.Lock();self.store=None
        self.locks={}
        # Plays always find a worker: prefetching uses at most size-1 of them.
        self.size=size;self.recycle=recycle;self.timeout=15;self.idle=[];self.count=0;self.available=threading.Condition(self.guard)
        self.pending=deque(maxlen=15);self.prefetchers=0;self.playing=0
        self.prefixes=PrefixCache();self.shared=None;self.cache_ttl=7200;self.compatible={}
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
    def spawn(self):
        env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'};env['PYTHONIOENCODING']='utf-8'
        vendor=Path(__file__).resolve().parent/'.vendor'
        if vendor.exists() and importlib.util.find_spec('yt_dlp') is None:env['PYTHONPATH']=str(vendor)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        process=subprocess.Popen([sys.executable,'-c',WORKER,json.dumps(OPTIONS)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',bufsize=1,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        worker=Worker(process)
        try:worker.ready(20)
        except (OSError,queue.Empty):
            worker.kill();raise OSError('yt-dlp worker did not start') from None
        return worker
    def start(self,warm=None):
        """Boot the whole pool in the background; optionally resolve one id per worker so the player JS is cached too."""
        def boot(_):
            try:worker=self.spawn()
            except OSError:
                with self.guard:self.count-=1;self.available.notify()
                return
            if warm:
                try:worker.ask(warm,20)
                except (OSError,ValueError,queue.Empty):
                    self.release(worker,True);return
            self.release(worker)
        with self.guard:
            missing=self.size-self.count;self.count+=max(0,missing)
        for i in range(max(0,missing)):threading.Thread(target=boot,args=(i,),daemon=True).start()
    def acquire(self,timeout,background=False):
        deadline=time.monotonic()+timeout
        with self.guard:
            while True:
                dead=[w for w in self.idle if not w.alive()]
                if dead:self.idle=[w for w in self.idle if w.alive()];self.count-=len(dead)
                if self.idle:return self.idle.pop()
                if self.count<self.size:self.count+=1;break
                left=deadline-time.monotonic()
                if left<=0 or background:return None
                self.available.wait(left)
        try:return self.spawn()
        except OSError:
            with self.guard:self.count-=1;self.available.notify()
            return None
    def release(self,worker,broken=False):
        if broken or not worker.alive() or worker.served>=self.recycle:
            worker.kill()
            with self.guard:self.count-=1;self.available.notify()
            if not broken:self.start()
            return
        with self.guard:self.idle.append(worker);self.available.notify_all()
    def run(self,video_id,background=False):
        started=time.perf_counter()
        worker=self.acquire(4 if background else 12,background)
        if worker is None:raise AudioUnavailable()
        acquired=time.perf_counter()
        try:url=worker.ask(video_id,self.timeout,compatible=True) if self.compatible.get(video_id,0)>time.time() else worker.ask(video_id,self.timeout)
        except (OSError,ValueError,queue.Empty) as error:
            self.release(worker,True)
            metric('audio_resolve',video_id=video_id,engine='yt-dlp',background=background,queue_ms=round((acquired-started)*1000,2),resolve_ms=round((time.perf_counter()-acquired)*1000,2),success=False,error=type(error).__name__)
            raise AudioUnavailable() from None
        self.release(worker)
        metric('audio_resolve',video_id=video_id,engine=getattr(worker,'last_engine','yt-dlp'),background=background,queue_ms=round((acquired-started)*1000,2),resolve_ms=round((time.perf_counter()-acquired)*1000,2),success=bool(url))
        return url
    def prefetch(self,video_id):self.prefetch_many([video_id])
    def prefetch_many(self,ids):
        if os.environ.get('IVOX_AUDIO_PREFETCH','1')=='0':return
        # The whole batch is queued before any drainer starts, newest batch and top result first;
        # a bounded deque drops requests the user has scrolled away from.
        ids=[v for v in ids if isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',v)]
        with self.guard:
            for video_id in reversed(ids):
                cached=self.cache.get(video_id)
                if cached and cached['expires']>time.time()+600 and self.prefixes.get(video_id,cached['url']):continue
                if video_id in self.pending:self.pending.remove(video_id)
                self.pending.appendleft(video_id)
            starting=max(0,min(len(self.pending),max(1,self.size-1)-self.prefetchers));self.prefetchers+=starting
        for _ in range(starting):threading.Thread(target=self.drain,daemon=True).start()
    def drain(self):
        retries={}
        while True:
            with self.guard:
                # A song the user just tapped gets the CPU to itself: no new prefetch starts meanwhile.
                while self.pending and not (self.playing==0 and (self.idle or self.count<self.size)):self.available.wait(1)
                if not self.pending:self.prefetchers-=1;return
                video_id=self.pending.popleft()
            try:self.prefetch_fragment(video_id)
            except AudioUnavailable:
                # Lost the race for a free worker: try again later instead of dropping the song.
                with self.guard:
                    if retries.get(video_id,0)<2 and video_id not in self.failures and video_id not in self.cache:
                        retries[video_id]=retries.get(video_id,0)+1;self.pending.append(video_id)
    def duration(self,video_id):
        # The resolved stream URL carries the audio's real length ('dur', in seconds).
        with self.guard:hit=self.cache.get(video_id)
        try:value=float(parse_qs(urlparse(hit['url']).query)['dur'][0]) if hit else 0
        except (KeyError,ValueError,IndexError):value=0
        return round(value,1) if 0<value<6*3600 else None
    @staticmethod
    def reusable(url):
        # URLs saved before AAC-LC was preferred may point at HE-AAC (itag 139): resolve again.
        return YtDlpResolver.allowed(url) and parse_qs(urlparse(url).query).get('itag',[''])[0]!='139'
    @staticmethod
    def allowed(url):
        try:
            p=urlparse(url)
            return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and bool(p.hostname and p.hostname.endswith('.googlevideo.com'))
        except ValueError:return False
    def resolve(self,video_id,background=False):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise AudioUnavailable()
        # Valid hits never wait behind an in-flight refresh or unrelated video.
        with self.guard:hit=self.cache.get(video_id)
        if not background and hit and hit['expires']>time.time():
            if hit['expires']<time.time()+300:self.prefetch(video_id)
            return hit
        with self.guard:
            entry=self.locks.setdefault(video_id,[threading.Lock(),0]);entry[1]+=1
        lock=entry[0];acquired=lock.acquire(timeout=20)
        if not acquired:
            with self.guard:
                entry[1]-=1
                if not entry[1]:self.locks.pop(video_id,None)
            raise AudioUnavailable()
        try:
            with self.guard:
                cached=self.cache.get(video_id)
                failed=self.failures.get(video_id,0)>time.time()
            if cached and cached['expires']>time.time()+(600 if background else 0):
                if not background and cached['expires']<time.time()+300:self.prefetch(video_id)
                return cached
            if failed:raise AudioUnavailable()
            if not background:
                with self.guard:self.playing+=1
            try:
                url=self.run(video_id,background)
                if not url or not self.allowed(url):raise AudioUnavailable()
                params=parse_qs(urlparse(url).query);mime=params.get('mime',['audio/webm'])[0]
                if not mime.startswith('audio/'):raise AudioUnavailable()
                expiry=time.time()+self.cache_ttl
                try:expiry=min(expiry,int(params.get('expire',[expiry+120])[0])-120)
                except (ValueError,TypeError):pass
                if expiry<=time.time():raise AudioUnavailable()
                item={'url':url,'mime':mime,'expires':expiry}
                with self.guard:
                    self.cache={k:v for k,v in self.cache.items() if v['expires']>time.time()}
                    if len(self.cache)>=300:self.cache.pop(next(iter(self.cache)))
                    self.cache[video_id]=item;self.failures.pop(video_id,None)
                self.save('INSERT OR REPLACE INTO audio_url_cache VALUES(?,?,?,?)',(video_id,url,mime,expiry))
                if self.shared:self.shared.enqueue(video_id,item)
                return item
            except AudioUnavailable:
                # A busy pool is not the song's fault: only real extraction failures are remembered.
                if not background:
                    with self.guard:
                        self.failures={k:v for k,v in self.failures.items() if v>time.time()}
                        if len(self.failures)>=200:self.failures.pop(next(iter(self.failures)))
                        self.failures[video_id]=time.time()+30
                raise
            finally:
                if not background:
                    with self.guard:self.playing-=1;self.available.notify_all()
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
                connection,response=get_response(url,{'Range':range_header} if range_header else {},timeout=15)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                if response.status==416:return connection,response,item['mime']
                mime=response.getheader('Content-Type','').split(';')[0]
                if response.status not in (200,206):raise AudioUnavailable(f'upstream_{response.status}')
                if not (mime.startswith('audio/') or mime=='application/octet-stream'):raise AudioUnavailable('upstream_type')
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
            # A cached URL can be rejected early (e.g. new public IP); resolve once more.
            if retry:return self.open(video_id,range_header,False)
            raise AudioUnavailable(str(error) if isinstance(error,AudioUnavailable) else type(error).__name__) from None

resolver=YtDlpResolver()
