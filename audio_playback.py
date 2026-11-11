"""Pin a native playback attempt to one MP4 byte representation.

Safari can probe two bytes before requesting metadata/audio ranges. A background
cache publication between those requests must never change their byte offsets.
"""
from collections import OrderedDict
import re
import threading
import time
from audio_partial import PartialResponse
from audio_prepared import UnsatisfiedRange
from audio_resolver import AudioUnavailable


class NativePlayback:
    def __init__(self,cache,max_entries=2048,max_bytes=32*1024*1024,ttl=1800,refresh=None):
        self.cache=cache;self.max_entries=max_entries;self.max_bytes=max_bytes;self.ttl=ttl
        self.lock=threading.Lock();self.choices=OrderedDict();self.refresh=refresh

    def open(self,key,vid,range_header,metadata=None,refresh=False):
        now=time.monotonic()
        with self.lock:
            for old,choice in list(self.choices.items()):
                if choice['ready'].is_set() and choice['expires']<=now:
                    # Retire offsets before releasing memory; an old native
                    # request must fail instead of selecting a different file.
                    choice['view']=None;choice['error']=True
                    if choice['expires']+self.ttl<=now:self.choices.pop(old)
            choice=self.choices.get(key)
            owner=choice is None
            if owner:
                # Do not evict an active attempt and silently change its offsets.
                if len(self.choices)>=self.max_entries:raise AudioUnavailable('playback_capacity')
                choice={'ready':threading.Event(),'expires':now+self.ttl,'view':None,'error':False}
                self.choices[key]=choice
        if owner:
            try:
                if refresh:
                    self.cache.invalidate(vid)
                    if self.refresh:self.refresh(vid)
                self.cache.ensure(vid,metadata,timeout=2)
                with self.cache.lock:
                    entry=self.cache.entries.get(vid)
                    view=entry.get('view') if entry and entry['expires']>time.monotonic() else None
                with self.lock:
                    views={id(v['view']):v['view'] for v in self.choices.values() if v['view'] is not None}
                    if view is not None and (id(view) in views or sum(v.memory_size for v in views.values())+view.memory_size<=self.max_bytes):
                        # Bound the attempt by the signed URL, not by the cache
                        # entry: an entry nearing its reuse window must not break
                        # a song that is already playing.
                        signed=getattr(view,'url_expires',None)
                        choice['hard']=time.monotonic()+(signed-time.time()-60) if signed else choice['expires']
                        choice['view']=view;choice['expires']=min(choice['expires'],choice['hard'])
            except BaseException:
                choice['error']=True
                raise
            finally:choice['ready'].set()
        elif not choice['ready'].wait(2.5):raise AudioUnavailable('playback_preparing')
        if choice['error']:raise AudioUnavailable('playback_prepare_failed')
        view=choice['view']
        if view is None:return None  # This attempt stays on the original route.
        with self.lock:
            # Each request of a live attempt extends it (paused or long songs),
            # never past the URL's own validity.
            if not choice['error']:choice['expires']=max(choice['expires'],min(time.monotonic()+self.ttl,choice.get('hard',choice['expires'])))
        start=0;end=view.total-1
        if range_header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            if not match or not any(match.groups()):return UnsatisfiedRange(view.total)
            if match[1]:start=int(match[1]);end=min(end,int(match[2])) if match[2] else end
            elif int(match[2]):start=max(0,view.total-int(match[2]))
            else:return UnsatisfiedRange(view.total)
            if start>end or start>=view.total:return UnsatisfiedRange(view.total)
        return PartialResponse(view,start,end,bool(range_header))
