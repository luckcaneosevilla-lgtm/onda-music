"""Partial fast-start MP4 view: cached AAC prefix + byte-exact upstream continuation.

Only already selected AAC-LC/MP4 is accepted. Chunk offsets are relocated with
moov; media samples are never re-encoded or concatenated across representations.
"""
import http.client
import re
import struct
import time
from urllib.parse import parse_qs,urljoin,urlparse
from audio_resolver import AudioUnavailable,YtDlpResolver
from music_provider import get_response
from streaming import content_range
from youtube_provider import ProviderError

PREFIX=512*1024
MAX_MOOV=1024*1024
CONTAINERS={b'moov',b'trak',b'mdia',b'minf',b'stbl',b'edts',b'dinf',b'udta'}
UNSUPPORTED={b'cmov',b'saio',b'iloc',b'tfhd',b'trun',b'mvex'}

def atom_header(data,offset=0,limit=None):
    limit=len(data) if limit is None else limit
    if offset+8>limit:raise ValueError('truncated_mp4_header')
    size,kind=struct.unpack_from('>I4s',data,offset);header=8
    if size==1:
        if offset+16>limit:raise ValueError('truncated_mp4_header')
        size=struct.unpack_from('>Q',data,offset+8)[0];header=16
    if size==0:size=limit-offset
    if size<header:raise ValueError('invalid_mp4_atom')
    return size,kind,header

def relocate_moov(raw,insert,old_start,total):
    result=bytearray(raw);moved=len(raw);tables=0
    def walk(start,end,depth=0):
        nonlocal tables
        if depth>16:raise ValueError('mp4_nesting_limit')
        position=start
        while position<end:
            size,kind,header=atom_header(result,position,end)
            if position+size>end:raise ValueError('invalid_mp4_atom')
            if kind in UNSUPPORTED:raise ValueError('unsupported_mp4_offsets')
            if kind in (b'stco',b'co64'):
                width=4 if kind==b'stco' else 8;fmt='>I' if width==4 else '>Q';base=position+header
                if base+8>position+size:raise ValueError('invalid_chunk_table')
                count=struct.unpack_from('>I',result,base+4)[0]
                if base+8+count*width!=position+size:raise ValueError('invalid_chunk_table')
                for index in range(count):
                    at=base+8+index*width;value=struct.unpack_from(fmt,result,at)[0]
                    if value>=total or old_start<=value<old_start+moved:raise ValueError('invalid_chunk_offset')
                    if insert<=value<old_start:value+=moved
                    if value>=1<<(width*8):raise ValueError('chunk_offset_overflow')
                    struct.pack_into(fmt,result,at,value)
                tables+=1
            elif kind in CONTAINERS:walk(position+header,position+size,depth+1)
            position+=size
    walk(0,len(result))
    if not tables:raise ValueError('missing_chunk_offsets')
    return bytes(result)

def url_expiry(url):
    """Wall-clock expiry signed into a googlevideo URL, or None."""
    try:return float(parse_qs(urlparse(url).query)['expire'][0])
    except (KeyError,IndexError,ValueError,TypeError):return None

class RangeSource:
    def __init__(self,item,deadline=None):
        self.url=item['url'];self.mime=item.get('mime');self.agent=item.get('user_agent','Mozilla/5.0')
        self.deadline=deadline;self.total=None;self.validators={};self.fetch_ms=0
        if self.mime!='audio/mp4' or not YtDlpResolver.allowed(self.url):raise AudioUnavailable('partial_requires_aac_mp4')
    def open(self,start,end,preparing=False):
        url=self.url;connection=None
        try:
            for _ in range(4):
                timeout=3
                if preparing and self.deadline is not None:
                    timeout=min(timeout,self.deadline-time.perf_counter())
                    if timeout<=0:raise AudioUnavailable('partial_prepare_timeout')
                if not YtDlpResolver.allowed(url):raise AudioUnavailable('invalid_partial_redirect')
                connection,response=get_response(url,{'Range':f'bytes={start}-{end}','User-Agent':self.agent},timeout=timeout)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                bounds=content_range(response)
                if response.status!=206 or not bounds or bounds[0]!=start or bounds[1]!=min(end,bounds[2]-1):raise AudioUnavailable('partial_range_mismatch')
                if self.total is not None and bounds[2]!=self.total:raise AudioUnavailable('partial_representation_changed')
                if response.getheader('Content-Type','').split(';')[0].strip().lower()!='audio/mp4':raise AudioUnavailable('partial_mime_mismatch')
                if self.total is None:
                    if bounds[2]>512*1024*1024:raise AudioUnavailable('partial_source_too_large')
                    self.total=bounds[2];self.validators={key:response.getheader(key) for key in ('ETag','Last-Modified') if response.getheader(key)}
                    self.url=url
                elif any(response.getheader(key)!=value for key,value in self.validators.items()):raise AudioUnavailable('partial_validator_changed')
                return connection,response,bounds
            raise AudioUnavailable('partial_redirect_limit')
        except BaseException:
            if connection:connection.close()
            raise
    def fetch(self,start,end):
        began=time.perf_counter();connection,response,bounds=self.open(start,end,True)
        try:
            remaining=bounds[1]-bounds[0]+1;parts=[]
            while remaining:
                if self.deadline is not None and time.perf_counter()>self.deadline:raise AudioUnavailable('partial_prepare_timeout')
                data=response.read1(min(65536,remaining))
                if not data:raise AudioUnavailable('partial_truncated')
                remaining-=len(data);parts.append(data)
            return b''.join(parts)
        finally:connection.close();self.fetch_ms+=(time.perf_counter()-began)*1000

class PartialMP4:
    def __init__(self,item,deadline=None,prefix_bytes=PREFIX):
        self.url_expires=url_expiry(item['url'])
        self.source=RangeSource(item,deadline);self.initial=self.source.fetch(0,prefix_bytes-1);self.total=self.source.total
        windows=[(0,self.initial)];boxes=[];position=0
        def read_at(at,size,header=False):
            for start,data in windows:
                if start<=at and at+size<=start+len(data):return data[at-start:at-start+size]
            length=max(size,65536) if header else size
            data=self.source.fetch(at,min(self.total-1,at+length-1));windows.append((at,data))
            return data[:size]
        while position<self.total:
            if len(boxes)>=64:raise ValueError('mp4_atom_limit')
            raw=read_at(position,min(16,self.total-position),True);size,kind,header=atom_header(raw)
            # A size-zero top-level atom extends to the end of the original file.
            if raw[:4]==b'\0\0\0\0':size=self.total-position
            if position+size>self.total:raise ValueError('invalid_mp4_atom')
            boxes.append((position,size,kind));position+=size
            moov_front=next((b[0] for b in boxes if b[2]==b'moov'),None)
            mdat_front=next((b[0] for b in boxes if b[2]==b'mdat'),None)
            if moov_front is not None and mdat_front is not None and moov_front<mdat_front:break
        if not boxes or boxes[0][2]!=b'ftyp':raise ValueError('missing_mp4_ftyp')
        moovs=[box for box in boxes if box[2]==b'moov']
        if len(moovs)!=1 or not any(box[2]==b'mdat' for box in boxes):raise ValueError('invalid_mp4_layout')
        old,size,_=moovs[0];insert=boxes[0][1]
        if size>MAX_MOOV:raise ValueError('mp4_metadata_limit')
        raw=read_at(old,size);began=time.perf_counter()
        first_mdat=next(box[0] for box in boxes if box[2]==b'mdat')
        if old>first_mdat:
            if any(box[2] in {b'moof',b'mfra',b'sidx',b'ssix',b'styp'} for box in boxes):raise ValueError('unsupported_fragment_relocation')
            self.moov=relocate_moov(raw,insert,old,self.total)
            self.segments=[(0,insert,'source',0),(insert,size,'metadata',0)]
            if old>insert:self.segments.append((insert+size,old-insert,'source',insert))
            if old+size<self.total:self.segments.append((old+size,self.total-old-size,'source',old+size))
        else:
            self.moov=raw;self.segments=[(0,old,'source',0),(old,size,'metadata',0)]
            if old+size<self.total:self.segments.append((old+size,self.total-old-size,'source',old+size))
        self.metadata_ms=round((time.perf_counter()-began)*1000,2)
        pieces=[]
        for _,length,kind,origin in self.segments:
            if kind=='metadata':pieces.append(self.moov);continue
            cached=max(0,min(length,len(self.initial)-origin))
            if cached:pieces.append(self.initial[origin:origin+cached])
            if cached<length:break
        self.prefix=b''.join(pieces);self.memory_size=len(self.prefix)+len(self.moov)
        # The original prefix is now represented in self.prefix; do not keep a second copy.
        self.initial=None;self.prepare_ms=0
    def locate(self,offset):
        for start,length,kind,origin in self.segments:
            if start<=offset<start+length:return kind,origin+offset-start,start+length
        raise ValueError('invalid_virtual_offset')

class PartialResponse:
    def __init__(self,representation,start,end,partial):
        self.view=representation;self.offset=start;self.end=end;self.connection=None;self.response=None;self.closed=False;self.source_end=-1
        self.status=206 if partial else 200
        self.headers={'content-type':'audio/mp4','accept-ranges':'bytes','content-length':str(end-start+1)}
        if partial:self.headers['content-range']=f'bytes {start}-{end}/{representation.total}'
        self.ivox_timings={'source':'prepared-partial','prepared_cache_ms':0,'partial_cache_ms':0,'partial_prepare_ms':representation.prepare_ms,'partial_metadata_ms':representation.metadata_ms,'partial_fetch_ms':round(representation.source.fetch_ms,2),'prefix_hit':True}
    def getheader(self,name,default=None):return self.headers.get(name.lower(),default)
    def read1(self,size=8192):
        size=min(size,self.end-self.offset+1)
        if self.closed or size<=0:return b''
        if self.offset<len(self.view.prefix):
            data=self.view.prefix[self.offset:min(self.offset+size,len(self.view.prefix))];self.offset+=len(data);return data
        kind,origin,segment_end=self.view.locate(self.offset);limit=min(self.end+1,segment_end)
        if kind=='metadata':
            data=self.view.moov[origin:origin+min(size,limit-self.offset)];self.offset+=len(data);return data
        if self.response is None or self.offset>self.source_end:
            if self.connection:self.connection.close()
            try:self.connection,self.response,_=self.view.source.open(origin,origin+limit-self.offset-1)
            except (AudioUnavailable,ProviderError,ValueError):raise OSError('partial_continuation_failed') from None
            self.source_end=limit-1
        data=self.response.read1(min(size,limit-self.offset))
        if not data:raise http.client.IncompleteRead(b'')
        self.offset+=len(data);return data
    read=read1
    def close(self):
        if self.closed:return
        self.closed=True
        if self.connection:self.connection.close();self.connection=None
