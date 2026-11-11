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
from audio_resolver import AudioUnavailable,YtDlpResolver,speculative
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
    def __init__(self,resolve,binary=None,ttl=1800,max_bytes=256*1024*1024,max_file=32*1024*1024,max_entries=24,job_timeout=40,partial=False,prefix_bytes=512*1024):
        self.resolve=resolve;self.binary=binary;self.partial=partial;self.ttl=ttl;self.max_bytes=max_bytes;self.max_file=max_file
        self.max_entries=max_entries;self.job_timeout=job_timeout;self.prefix_bytes=prefix_bytes
        self.lock=threading.RLock();self.entries=OrderedDict();self.pending=deque(maxlen=8)
        self.inflight=set();self.failures={};self.details=OrderedDict();self.worker=None;self.temp=None;self.process=None
        self.stopping=threading.Event();self.changed=threading.Condition(self.lock)
        self.demand_slot=threading.BoundedSemaphore(1);self.demand_worker=None
        atexit.register(self.close)
    def _prune(self,needed=0):
        now=time.monotonic()
        for key,entry in list(self.entries.items()):
            over=sum(e['size'] for e in self.entries.values())+needed>self.max_bytes or len(self.entries)+(1 if needed else 0)>self.max_entries
            if entry['leases'] or (entry['expires']>now and not over):continue
            if entry.get('kind')!='partial':
                try:entry['path'].unlink(missing_ok=True)
                except OSError:continue
            self.entries.pop(key,None)
    def prefetch_many(self,items,priority=False):
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
            # Visibility warming must not discard jobs from the current search.
            # A real Play/search can promote its candidates without duplicating work.
            ordered=list(reversed(selected)) if priority else selected
            for item in ordered:
                vid=item['provider_id']
                if vid in self.entries or vid in self.inflight or vid in self.failures:continue
                queued=next((v for v in self.pending if v['provider_id']==vid),None)
                if queued:
                    if not priority:continue
                    self.pending.remove(queued)
                if len(self.pending)>=8:
                    if not priority:continue
                    dropped=self.pending.pop();self.details.pop(dropped['provider_id'],None)
                if priority:self.pending.appendleft(item)
                else:self.pending.append(item)
                self.details[vid]={'status':'queued','stage':'queued'}
            while len(self.details)>128:
                removable=next((v for v in self.details if v not in self.inflight and not any(i['provider_id']==v for i in self.pending)),None)
                if removable is None:break
                self.details.pop(removable,None)
            if self.pending and (not self.worker or not self.worker.is_alive()):
                self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start()
    def _work(self):
        while not self.stopping.is_set():
            with self.lock:
                if not self.pending:
                    self.worker=None;return
                item=self.pending.popleft();vid=item['provider_id'];self.inflight.add(vid)
            started=time.perf_counter();success=False;reason=None
            self._stage(vid,'resolve')
            try:
                speculative.on=True
                try:self._prepare(vid,item,started);success=True
                finally:speculative.on=False
            except (AudioUnavailable,OSError,ValueError,http.client.HTTPException,ProviderError,subprocess.SubprocessError) as error:
                reason=str(error) if isinstance(error,AudioUnavailable) else type(error).__name__
                reason=re.sub(r'[^A-Za-z0-9_]','',reason)[:60]
                with self.lock:
                  # Deferred speculative work never blocks the listener's own Play.
                  if reason!='background_deferred':
                    retry=2 if reason in ('worker_unavailable','resolution_busy') else 10 if isinstance(error,(OSError,http.client.HTTPException)) or 'timeout' in reason.lower() else 60
                    self.failures[vid]=time.monotonic()+retry;stage=self.details.get(vid,{}).get('stage','resolve')
                    self.details[vid]={'status':'failed','stage':stage,'reason':reason}
                  else:self.details.pop(vid,None)
            finally:
                with self.changed:self.inflight.discard(vid);self.changed.notify_all()
                metric('audio_prepared',video_id=vid,success=success,reason=reason,prepare_ms=round((time.perf_counter()-started)*1000,2))
    def ensure(self,vid,metadata=None,timeout=2):
        """Give cold Play a reserved slot instead of waiting behind speculative work.

        Join an existing preparation rather than resolving/downloading it twice.
        The caller's wait is bounded; a late result remains useful for next Play.
        """
        deadline=time.monotonic()+timeout
        with self.changed:
            if not self.partial:return False
            self._prune()
            if vid in self.entries:return True
            if self.stopping.is_set() or self.failures.get(vid,0)>time.monotonic():return False
            if vid not in self.inflight:
                if not self.demand_slot.acquire(blocking=False):return False
                queued=next((item for item in self.pending if item['provider_id']==vid),None)
                if queued:self.pending.remove(queued)
                self.inflight.add(vid)
                def prepare():
                    try:
                        self._prepare(vid,metadata or {},time.perf_counter())
                    except (AudioUnavailable,OSError,ValueError,http.client.HTTPException,ProviderError,subprocess.SubprocessError):
                        with self.lock:self.failures[vid]=time.monotonic()+2
                    finally:
                        with self.changed:
                            self.inflight.discard(vid);self.demand_slot.release();self.changed.notify_all()
                self.demand_worker=threading.Thread(target=prepare,daemon=True,name='ivox-play-prepare')
                self.demand_worker.start()
            while vid in self.inflight:
                remaining=deadline-time.monotonic()
                if remaining<=0 or self.stopping.is_set():break
                self.changed.wait(remaining)
            entry=self.entries.get(vid)
            return bool(entry and entry['expires']>time.monotonic())
    def _prepare(self,vid,metadata,started):
        if self.partial:return self._prepare_partial(vid,metadata,started)
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
            self._stage(vid,'download')
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
            self._stage(vid,'encode')
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
                self.details[vid]={'status':'ready','stage':'ready'}
        finally:
            if connection:connection.close()
            source.unlink(missing_ok=True);target.unlink(missing_ok=True)
    def _prepare_partial(self,vid,metadata,started):
        from audio_partial import PartialMP4
        resolved=self.resolve(vid,metadata)
        # Selection already guarantees AAC-LC. Preserve its samples and prepare
        # only the metadata/prefix instead of encoding another complete file.
        self._stage(vid,'partial_download')
        view=PartialMP4(resolved,deadline=started+self.job_timeout,prefix_bytes=self.prefix_bytes)
        view.prepare_ms=round((time.perf_counter()-started)*1000,2)
        # The resolver's cache expiry is a local reuse window (30 min from
        # resolution), not the URL's validity. Bounding the view by it made a
        # song resolved 25 min earlier break a few minutes into playback.
        valid_until=view.url_expires-60 if view.url_expires else float(resolved.get('expires',time.time()+self.ttl))-30
        lifetime=min(self.ttl,valid_until-time.time())
        if lifetime<=0:raise AudioUnavailable('partial_url_expired')
        with self.lock:
            if self.stopping.is_set():raise AudioUnavailable('preparation_cancelled')
            self._prune(needed=view.memory_size)
            if len(self.entries)>=self.max_entries or sum(e['size'] for e in self.entries.values())+view.memory_size>self.max_bytes:raise AudioUnavailable('prepared_cache_full')
            self.entries[vid]={'kind':'partial','view':view,'size':view.memory_size,'expires':time.monotonic()+lifetime,'leases':0}
            self.details[vid]={'status':'ready','stage':'ready','mode':'partial','prepare_ms':view.prepare_ms,'metadata_ms':view.metadata_ms,'prefix_bytes':len(view.prefix),'total_bytes':view.total}
        metric('audio_partial_prepared',video_id=vid,partial_prepare_ms=view.prepare_ms,partial_metadata_ms=view.metadata_ms,partial_fetch_ms=round(view.source.fetch_ms,2),prefix_bytes=len(view.prefix),total_bytes=view.total)
    def _stage(self,vid,stage):
        with self.lock:self.details[vid]={'status':'preparing','stage':stage}
    def status(self,vid):
        with self.lock:
            entry=self.entries.get(vid)
            if entry and entry['expires']>time.monotonic():return dict(self.details.get(vid,{'status':'ready','stage':'ready'}))
            state=self.details.get(vid)
            if state and state['status']=='failed' and self.failures.get(vid,0)<=time.monotonic():return {'status':'missing','stage':'missing'}
            if state and state['status']!='ready':return dict(state)
            return {'status':'missing','stage':'missing'}
    def open(self,vid,range_header=None):
        with self.lock:
            self._prune();entry=self.entries.get(vid)
            if not entry or entry['expires']<=time.monotonic():return None
            partial_mode=entry.get('kind')=='partial'
            total=entry['view'].total if partial_mode else entry['size'];start=0;end=total-1
            if range_header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
                if not match or not any(match.groups()):return UnsatisfiedRange(total)
                if match[1]:
                    start=int(match[1]);end=min(end,int(match[2])) if match[2] else end
                else:
                    if int(match[2])==0:return UnsatisfiedRange(total)
                    start=max(0,total-int(match[2]))
                if start>end or start>=total:return UnsatisfiedRange(total)
            if partial_mode:
                from audio_partial import PartialResponse
                self.entries.move_to_end(vid)
                return PartialResponse(entry['view'],start,end,bool(range_header))
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
            self.pending.clear();process=self.process;worker=self.worker;demand=self.demand_worker
            for entry in self.entries.values():entry['expires']=0
        if process and process.poll() is None:
            try:process.kill()
            except OSError:pass
        if worker and worker is not threading.current_thread():worker.join(timeout=15)
        if demand and demand is not threading.current_thread():demand.join(timeout=10)
        with self.lock:
            if (not worker or not worker.is_alive()) and not any(e['leases'] for e in self.entries.values()):
                if self.temp:self.temp.cleanup();self.temp=None
                self.entries.clear()
