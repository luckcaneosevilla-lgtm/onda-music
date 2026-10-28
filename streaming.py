"""Bounded prefix cache and byte-exact lazy continuation for progressive audio.

Never joins different signed representations. No transcoding, HLS or full downloads.
"""
from collections import OrderedDict
import datetime
import http.client
import json
import logging
import re
import threading
import time

log = logging.getLogger('ivox.streaming')
if not log.handlers:
    handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('%(message)s'));log.addHandler(handler)
log.setLevel(logging.INFO);log.propagate=False

def metric(event, **fields):
    # Callers pass IDs/timings only: never signed URLs, cookies or tokens.
    log.info(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds'),'event':event,**fields},separators=(',',':')))

class PrefixCache:
    def __init__(self, limit=16*1024*1024, fragment=500*1024):
        self.limit=limit;self.fragment=fragment;self.size=0
        self.items=OrderedDict();self.lock=threading.Lock()
    def get(self, video_id, url):
        with self.lock:
            item=self.items.get(video_id)
            if not item:return None
            if item['expires']<=time.time()+60 or item['url']!=url:
                self.size-=len(self.items.pop(video_id)['data']);return None
            self.items.move_to_end(video_id);return item
    def remove(self, video_id):
        with self.lock:
            item=self.items.pop(video_id,None)
            if item:self.size-=len(item['data'])
    def put(self, video_id, item):
        if len(item['data'])>self.fragment or len(item['data'])>self.limit:return
        with self.lock:
            old=self.items.pop(video_id,None)
            if old:self.size-=len(old['data'])
            for key in list(self.items):
                if self.items[key]['expires']<=time.time()+60:self.size-=len(self.items.pop(key)['data'])
            while self.items and (self.size+len(item['data'])>self.limit or len(self.items)>=64):
                _,old=self.items.popitem(last=False);self.size-=len(old['data'])
            self.items[video_id]=item;self.size+=len(item['data'])

def content_range(response):
    match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.getheader('Content-Range',''))
    return tuple(map(int,match.groups())) if match else None

class PrefixResponse:
    """HTTPResponse-shaped reader: prefix now, same representation's remainder on demand."""
    def __init__(self,item,start,end,partial,opener):
        self.item=item;self.offset=start;self.end=end;self.opener=opener
        self.status=206 if partial else 200;self.connection=None;self.response=None
        self.headers={'content-type':item['mime'],'content-length':str(end-start+1),'accept-ranges':'bytes'}
        if partial:self.headers['content-range']=f"bytes {start}-{end}/{item['total']}"
        self.ivox_timings={'prefix_hit':True,'upstream_headers_ms':0}
    def getheader(self,key,default=None):return self.headers.get(key.lower(),default)
    def read1(self,size=16384):
        size=min(max(size,0),self.end-self.offset+1)
        if not size:return b''
        if self.offset<len(self.item['data']):
            chunk=self.item['data'][self.offset:min(self.offset+size,len(self.item['data']))]
            self.offset+=len(chunk);return chunk
        if self.response is None:
            self.connection,self.response=self.opener(self.item['url'],{'Range':f'bytes={self.offset}-{self.end}'},timeout=10)
            bounds=content_range(self.response)
            mime=self.response.getheader('Content-Type','').split(';')[0]
            if self.response.status!=206 or bounds!=(self.offset,self.end,self.item['total']) or mime!=self.item['mime']:
                self.close();raise OSError('audio representation changed during continuation')
            for key in ('ETag','Last-Modified'):
                expected=self.item.get(key)
                if expected and self.response.getheader(key)!=expected:
                    self.close();raise OSError('audio validator changed during continuation')
        reader=getattr(self.response,'read1',self.response.read)
        chunk=reader(size)
        if not chunk:raise http.client.IncompleteRead(b'')
        self.offset+=len(chunk);return chunk
    def read(self,size=-1):
        # Compatibility for bounded reads. The relay intentionally uses read1.
        if size<0:size=self.end-self.offset+1
        chunks=[]
        while size:
            chunk=self.read1(size)
            if not chunk:break
            chunks.append(chunk);size-=len(chunk)
        return b''.join(chunks)
    def close(self):
        if self.connection:self.connection.close();self.connection=None

def prefix_response(item,range_header,opener):
    total=item['total'];start=0;end=total-1
    if range_header:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
        if not match or not any(match.groups()):return None
        if not match[1]:start=max(0,total-int(match[2]))
        else:
            start=int(match[1])
            if match[2]:end=min(end,int(match[2]))
    if start>end or start>=len(item['data']):return None
    return PrefixResponse(item,start,end,bool(range_header),opener)
