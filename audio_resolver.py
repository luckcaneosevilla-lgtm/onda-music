"""Level 1: resolve pure audio in a bounded subprocess; keep URLs server-side."""
from contextlib import closing
import http.client
import importlib.util
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse,parse_qs,urljoin
from music_provider import get_response
from youtube_provider import ProviderError

class AudioUnavailable(Exception):pass

# m4a first: Safari/iOS plays AAC everywhere; HLS/DASH manifests are never used and cost ~0.4 s.
ARGS=['--ignore-config','-f','bestaudio[ext=m4a]/bestaudio','-g','--no-playlist','--no-warnings','--no-cache-dir','--no-progress','--extractor-args','youtube:skip=hls,dash']
# A spare process imports yt-dlp ahead of time (~0.7 s) and waits for one video id on stdin.
WORKER='import sys\nfrom yt_dlp import main\nvideo_id=sys.stdin.readline().strip()\nif not video_id:sys.exit(1)\nmain([*sys.argv[1:],"https://www.youtube.com/watch?v="+video_id])'

class YtDlpResolver:
    def __init__(self,spares=0):
        self.cache={};self.failures={};self.guard=threading.Lock()
        self.locks=[threading.Lock() for _ in range(32)];self.workers=threading.BoundedSemaphore(4);self.store=None
        self.prefetching=threading.BoundedSemaphore(2);self.spare_count=spares;self.spares=[];self.spawning=threading.Lock()
    def persist(self,path):
        # Resolved URLs survive restarts; rows still honour the per-URL expiry.
        self.store=str(path)
        try:
            with closing(sqlite3.connect(self.store,timeout=5)) as c,c:
                c.execute('CREATE TABLE IF NOT EXISTS audio_url_cache(video_id TEXT PRIMARY KEY,url TEXT NOT NULL,mime TEXT NOT NULL,expires REAL NOT NULL)')
                c.execute('DELETE FROM audio_url_cache WHERE expires<=?',(time.time(),))
                rows=c.execute('SELECT video_id,url,mime,expires FROM audio_url_cache ORDER BY expires DESC LIMIT 200').fetchall()
        except sqlite3.Error:self.store=None;rows=[]
        self.warm_later()
        with self.guard:
            for video_id,url,mime,expires in reversed(rows):
                if self.allowed(url):self.cache[video_id]={'url':url,'mime':mime,'expires':expires}
    def save(self,sql,args):
        if not self.store:return
        try:
            with closing(sqlite3.connect(self.store,timeout=5)) as c,c:c.execute(sql,args)
        except sqlite3.Error:pass
    def spawn(self):
        env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'}
        vendor=Path(__file__).resolve().parent/'.vendor'
        if vendor.exists() and importlib.util.find_spec('yt_dlp') is None:env['PYTHONPATH']=str(vendor)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        return subprocess.Popen([sys.executable,'-c',WORKER,*ARGS],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def warm(self):
        if not self.spawning.acquire(blocking=False):return
        try:
            while True:
                with self.guard:
                    self.spares=[p for p in self.spares if p.poll() is None]
                    if len(self.spares)>=self.spare_count:return
                spare=self.spawn()
                with self.guard:self.spares.append(spare)
        except OSError:pass
        finally:self.spawning.release()
    def warm_later(self):
        if self.spare_count:threading.Thread(target=self.warm,daemon=True).start()
    def run(self,video_id):
        process=None
        with self.guard:
            while self.spares and process is None:
                spare=self.spares.pop()
                if spare.poll() is None:process=spare
        process=process or self.spawn();self.warm_later()
        try:stdout,_=process.communicate(video_id+'\n',timeout=15)
        except subprocess.TimeoutExpired:
            process.kill();process.communicate();raise
        return process.returncode,stdout
    def prefetch(self,video_id):
        # Bounded background resolution; user-initiated plays always keep free workers.
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id) or not self.prefetching.acquire(blocking=False):return
        def work():
            try:self.resolve(video_id)
            except AudioUnavailable:pass
            finally:self.prefetching.release()
        threading.Thread(target=work,daemon=True).start()
    @staticmethod
    def allowed(url):
        try:
            p=urlparse(url)
            return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and bool(p.hostname and p.hostname.endswith('.googlevideo.com'))
        except ValueError:return False
    def resolve(self,video_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise AudioUnavailable()
        lock=self.locks[sum(video_id.encode())%len(self.locks)]
        if not lock.acquire(timeout=16):raise AudioUnavailable()
        try:
            with self.guard:
                cached=self.cache.get(video_id)
                if cached and cached['expires']>time.time():return cached
                if self.failures.get(video_id,0)>time.time():raise AudioUnavailable()
            if not self.workers.acquire(timeout=5):raise AudioUnavailable()
            try:
                returncode,stdout=self.run(video_id)
                urls=stdout.strip().splitlines()
                if returncode!=0 or len(urls)!=1 or not self.allowed(urls[0]):raise AudioUnavailable()
                params=parse_qs(urlparse(urls[0]).query);mime=params.get('mime',['audio/webm'])[0]
                if not mime.startswith('audio/'):raise AudioUnavailable()
                expiry=time.time()+1800
                try:expiry=min(expiry,int(params.get('expire',[expiry+60])[0])-30)
                except (ValueError,TypeError):pass
                if expiry<=time.time():raise AudioUnavailable()
                item={'url':urls[0],'mime':mime,'expires':expiry}
                with self.guard:
                    self.cache={k:v for k,v in self.cache.items() if v['expires']>time.time()}
                    if len(self.cache)>=200:self.cache.pop(next(iter(self.cache)))
                    self.cache[video_id]=item;self.failures.pop(video_id,None)
                self.save('INSERT OR REPLACE INTO audio_url_cache VALUES(?,?,?,?)',(video_id,item['url'],mime,expiry))
                return item
            except (OSError,subprocess.TimeoutExpired,AudioUnavailable):
                with self.guard:
                    self.failures={k:v for k,v in self.failures.items() if v>time.time()}
                    if len(self.failures)>=200:self.failures.pop(next(iter(self.failures)))
                    self.failures[video_id]=time.time()+30
                raise AudioUnavailable() from None
            finally:self.workers.release()
        finally:lock.release()
    def invalidate(self,video_id):
        with self.guard:self.cache.pop(video_id,None)
        self.save('DELETE FROM audio_url_cache WHERE video_id=?',(video_id,))
    def open(self,video_id,range_header=None,retry=True):
        item=self.resolve(video_id);url=item['url'];connection=None
        try:
            for _ in range(4):
                if not self.allowed(url):raise AudioUnavailable()
                connection,response=get_response(url,{'Range':range_header} if range_header else {},timeout=15)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                if response.status==416:return connection,response,item['mime']
                mime=response.getheader('Content-Type','').split(';')[0]
                if response.status not in (200,206) or not (mime.startswith('audio/') or mime=='application/octet-stream'):raise AudioUnavailable()
                return connection,response,mime if mime.startswith('audio/') else item['mime']
            raise AudioUnavailable()
        except (OSError,http.client.HTTPException,AudioUnavailable,ProviderError):
            if connection:connection.close()
            self.invalidate(video_id)
            # A cached URL can be rejected early (e.g. new public IP); resolve once more.
            if retry:return self.open(video_id,range_header,False)
            raise AudioUnavailable() from None

resolver=YtDlpResolver(spares=2)
