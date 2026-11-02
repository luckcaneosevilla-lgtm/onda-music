"""Bounded server-side AAC-LC/fast-start cache prepared before Play.

One background worker; complete files are atomically published. Playback never
waits for a preparation job and serves stable, seekable bytes from a leased file.
"""
import atexit
from collections import OrderedDict,deque
import http.client
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin
from audio_resolver import AudioUnavailable,YtDlpResolver
from music_provider import get_response
from streaming import content_range,metric
from youtube_provider import ProviderError


def faststart_command(binary,source,target,max_file=32*1024*1024):
    return [binary,'-nostdin','-hide_banner','-loglevel','error','-y',
            '-protocol_whitelist','file,pipe','-i',str(source),'-map','0:a:0',
            '-vn','-sn','-dn','-c:a','aac','-profile:a','aac_low','-b:a','128k',
            '-threads','1','-movflags','+faststart','-fs',str(max_file*2),'-f','mp4',str(target)]


def validate_faststart(path):
    """Check actual top-level boxes, not accidental 'moov' strings in media data."""
    total=path.stat().st_size;offset=0;moov=None;mdat=None
    with path.open('rb') as source:
        while offset+8<=total:
            source.seek(offset);raw=source.read(8);size,kind=struct.unpack('>I4s',raw);header=8
            if size==1:
                raw=source.read(8)
                if len(raw)!=8:raise ValueError('invalid_mp4')
                size=struct.unpack('>Q',raw)[0];header=16
            elif size==0:size=total-offset
            if size<header or offset+size>total:raise ValueError('invalid_mp4')
            if offset==0 and kind!=b'ftyp':raise ValueError('invalid_mp4')
            if kind==b'moov':
                if moov is not None:raise ValueError('invalid_mp4')
                moov=offset
            if kind==b'mdat' and size>header and mdat is None:mdat=offset
            offset+=size
    if offset!=total or moov is None or mdat is None or moov>mdat:raise ValueError('not_faststart')
    return total


class PreparedResponse:
    def __init__(self,cache,key,entry,start,end,partial):
        self.cache=cache;self.key=key;self.entry=entry;self.remaining=end-start+1;self.closed=False
        self.file=entry['path'].open('rb');self.file.seek(start)
        self.status=206 if partial else 200
        self.headers={'content-type':'audio/mp4','accept-ranges':'bytes','content-length':str(self.remaining)}
        if partial:self.headers['content-range']=f"bytes {start}-{end}/{entry['size']}"
        self.ivox_timings={'source':'prepared-aac','prepared_cache_ms':0,'resolve_ms':0,'prefix_hit':False}
    def getheader(self,name,default=None):return self.headers.get(name.lower(),default)
    def read1(self,size=8192):
        if self.closed or not self.remaining:return b''
        data=self.file.read(min(size,self.remaining));self.remaining-=len(data);return data
    read=read1
    def close(self):
        if self.closed:return
        self.closed=True;self.file.close()
        with self.cache.lock:
            self.entry['leases']-=1;self.cache._prune()
        if self.cache.stopping.is_set():self.cache.close()


class UnsatisfiedRange:
    status=416;ivox_timings={'source':'prepared-aac','prepared_cache_ms':0}
    def __init__(self,total):self.total=total
    def getheader(self,name,default=None):return {'content-range':f'bytes */{self.total}','content-length':'0','accept-ranges':'bytes'}.get(name.lower(),default)
    def read1(self,size=8192):return b''
    read=read1
    def close(self):pass


class PreparedAudioCache:
    def __init__(self,resolve,binary=None,ttl=1800,max_bytes=256*1024*1024,max_file=32*1024*1024,max_entries=24,job_timeout=40):
        self.resolve=resolve;self.binary=binary;self.ttl=ttl;self.max_bytes=max_bytes;self.max_file=max_file
        self.max_entries=max_entries;self.job_timeout=job_timeout
        self.lock=threading.RLock();self.entries=OrderedDict();self.pending=deque(maxlen=3)
        self.inflight=set();self.failures={};self.worker=None;self.temp=None;self.process=None
        self.stopping=threading.Event();atexit.register(self.close)
    def _prune(self,needed=0):
        now=time.monotonic()
        for key,entry in list(self.entries.items()):
            over=sum(e['size'] for e in self.entries.values())+needed>self.max_bytes or len(self.entries)+(1 if needed else 0)>self.max_entries
            if entry['leases'] or (entry['expires']>now and not over):continue
            try:entry['path'].unlink(missing_ok=True)
            except OSError:continue
            self.entries.pop(key,None)
    def prefetch_many(self,items):
        selected=[]
        for item in items:
            vid=item.get('provider_id','')
            if item.get('provider')=='piped' and isinstance(vid,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):
                duration=item.get('duration')
                if isinstance(duration,(int,float)) and duration>1800:continue
                if not any(v['provider_id']==vid for v in selected):selected.append(dict(item))
            if len(selected)==3:break
        if not selected:return
        with self.lock:
            if self.stopping.is_set():return
            self._prune();now=time.monotonic();self.failures={v:t for v,t in self.failures.items() if t>now}
            wanted={v['provider_id'] for v in selected}
            self.pending=deque((v for v in self.pending if v['provider_id'] in wanted),maxlen=3)
            queued={v['provider_id'] for v in self.pending}
            for item in selected:
                vid=item['provider_id']
                if vid not in self.entries and vid not in queued and vid not in self.inflight and vid not in self.failures:self.pending.append(item)
            if self.pending and (not self.worker or not self.worker.is_alive()):
                self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start()
    def _work(self):
        while not self.stopping.is_set():
            with self.lock:
                if not self.pending:
                    self.worker=None;return
                item=self.pending.popleft();vid=item['provider_id'];self.inflight.add(vid)
            started=time.perf_counter();success=False
            try:
                self._prepare(vid,item,started);success=True
            except (AudioUnavailable,OSError,ValueError,http.client.HTTPException,ProviderError,subprocess.SubprocessError):
                with self.lock:self.failures[vid]=time.monotonic()+60
            finally:
                with self.lock:self.inflight.discard(vid)
                metric('audio_prepared',video_id=vid,success=success,prepare_ms=round((time.perf_counter()-started)*1000,2))
    def _prepare(self,vid,metadata,started):
        binary=self.binary or shutil.which('ffmpeg')
        if not binary:raise AudioUnavailable('ffmpeg_unavailable')
        resolved=self.resolve(vid,metadata)
        url=resolved.get('url');connection=None
        if not YtDlpResolver.allowed(url):raise AudioUnavailable('not_youtube_audio')
        with self.lock:
            if self.temp is None:self.temp=tempfile.TemporaryDirectory(prefix='ivox-prepared-')
            directory=Path(self.temp.name)
        stem=secrets.token_hex(12);source=directory/(stem+'.input');target=directory/(stem+'.part.mp4');published=directory/(stem+'.mp4')
        try:
            headers={'User-Agent':resolved.get('user_agent','Mozilla/5.0')}
            for _ in range(4):
                remaining=self.job_timeout-(time.perf_counter()-started)
                if remaining<=0 or self.stopping.is_set():raise AudioUnavailable('preparation_timeout')
                if not YtDlpResolver.allowed(url):raise AudioUnavailable('invalid_redirect')
                connection,response=get_response(url,headers,timeout=min(3,remaining))
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                length=response.getheader('Content-Length');expected=int(length) if length and length.isdigit() else None
                if expected is not None and (expected<=0 or expected>self.max_file):raise AudioUnavailable('preparation_size')
                if response.status==206:
                    bounds=content_range(response)
                    if not bounds or bounds[0]!=0 or bounds[1]!=bounds[2]-1:raise AudioUnavailable('partial_download')
                    expected=bounds[2]
                    if expected>self.max_file:raise AudioUnavailable('preparation_size')
                elif response.status!=200:raise AudioUnavailable('preparation_status')
                count=0
                with source.open('xb') as output:
                    while True:
                        if self.stopping.is_set() or time.perf_counter()-started>self.job_timeout:raise AudioUnavailable('preparation_timeout')
                        chunk=response.read1(65536)
                        if not chunk:break
                        count+=len(chunk)
                        if count>self.max_file:raise AudioUnavailable('preparation_size')
                        output.write(chunk)
                connection.close();connection=None
                if not count or (expected is not None and count!=expected):raise AudioUnavailable('preparation_truncated')
                break
            else:raise AudioUnavailable('redirect_limit')
            remaining=min(20,self.job_timeout-(time.perf_counter()-started))
            if remaining<=0:raise AudioUnavailable('preparation_timeout')
            with self.lock:
                if self.stopping.is_set():raise AudioUnavailable('preparation_cancelled')
                process=subprocess.Popen(faststart_command(binary,source,target,self.max_file),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));self.process=process
            try:
                process.wait(timeout=remaining)
                if process.returncode:raise AudioUnavailable('preparation_encode_failed')
            finally:
                if process.poll() is None:process.kill()
                process.wait(timeout=3)
                with self.lock:self.process=None
            size=validate_faststart(target)
            if size>self.max_file or size>self.max_bytes:raise AudioUnavailable('preparation_size')
            with self.lock:
                if self.stopping.is_set():raise AudioUnavailable('preparation_cancelled')
                self._prune(needed=size)
                if len(self.entries)>=self.max_entries or sum(e['size'] for e in self.entries.values())+size>self.max_bytes:raise AudioUnavailable('prepared_cache_full')
                os.replace(target,published)
                self.entries[vid]={'path':published,'size':size,'expires':time.monotonic()+self.ttl,'leases':0}
        finally:
            if connection:connection.close()
            source.unlink(missing_ok=True);target.unlink(missing_ok=True)
    def open(self,vid,range_header=None):
        with self.lock:
            self._prune();entry=self.entries.get(vid)
            if not entry or entry['expires']<=time.monotonic():return None
            total=entry['size'];start=0;end=total-1
            if range_header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
                if not match or not any(match.groups()):return UnsatisfiedRange(total)
                if match[1]:
                    start=int(match[1]);end=min(end,int(match[2])) if match[2] else end
                else:
                    if int(match[2])==0:return UnsatisfiedRange(total)
                    start=max(0,total-int(match[2]))
                if start>end or start>=total:return UnsatisfiedRange(total)
            try:response=PreparedResponse(self,vid,entry,start,end,bool(range_header))
            except OSError:
                entry['expires']=0;self._prune();return None
            entry['leases']+=1;self.entries.move_to_end(vid);return response
    def invalidate(self,vid):
        with self.lock:
            entry=self.entries.get(vid)
            if entry:entry['expires']=0
            self._prune();self.failures.pop(vid,None)
    def close(self):
        self.stopping.set()
        with self.lock:
            self.pending.clear();process=self.process;worker=self.worker
            for entry in self.entries.values():entry['expires']=0
        if process and process.poll() is None:
            try:process.kill()
            except OSError:pass
        if worker and worker is not threading.current_thread():worker.join(timeout=15)
        with self.lock:
            if (not worker or not worker.is_alive()) and not any(e['leases'] for e in self.entries.values()) and self.temp:
                self.temp.cleanup();self.temp=None;self.entries.clear()
