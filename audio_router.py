"""Try direct InnerTube, prepared Audius audio, then warm yt-dlp.

Only public, ungated tracks qualify. Catalog presence is not a rights clearance.
URLs remain server-side; get_response validates public destinations on every hop.
"""
import http.client
import json
import os
import re
import threading
import time
import unicodedata
from collections import deque
from urllib.parse import urlencode, urljoin

from audio_resolver import AudioUnavailable, resolver
from music_provider import get_response
from streaming import metric, PrefixCache, prefix_response, content_range
from youtube_provider import ProviderError
from innertube_resolver import InnerTubeResolver


def normalized(value):
    value=unicodedata.normalize('NFKD',str(value or '').casefold())
    value=''.join(c for c in value if not unicodedata.combining(c))
    value=re.sub(r'\((?:official (?:audio|video)|audio|lyrics?)\)|\[(?:official (?:audio|video)|audio|lyrics?)\]',' ',value)
    value=re.sub(r'\b(?:ft|feat)\.?\s+', 'featuring ',value)
    return ' '.join(re.findall(r'[^\W_]+',value))


class AudioRouter:
    def __init__(self,fallback):
        self.fallback=fallback;self.cache={};self.lock=threading.Lock()
        self.inner=InnerTubeResolver()
        self.pending=deque(maxlen=20);self.preparing=set();self.running=False
        self.prefixes=PrefixCache(limit=2*1024*1024,fragment=65536)

    def prefetch_many(self,items):
        """Bound speculation to one background worker and the latest 20 results."""
        with self.lock:
            for metadata in items[:10]:
                video_id=metadata.get('provider_id','')
                if metadata.get('provider')!='piped' or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):continue
                key=self.key(video_id,metadata)
                hit=self.cache.get(key)
                if key in self.preparing or (hit and hit[0]>time.monotonic()):continue
                if not any(self.key(v,m)==key for v,m in self.pending):self.pending.append((video_id,dict(metadata)))
            if self.running or not self.pending:return
            self.running=True
        threading.Thread(target=self.prepare,daemon=True).start()

    @staticmethod
    def key(video_id,metadata):
        return (video_id,metadata.get('title'),metadata.get('channel'),metadata.get('duration'))

    def prepare(self):
        while True:
            with self.lock:
                if not self.pending:self.running=False;return
                video_id,metadata=self.pending.popleft();key=self.key(video_id,metadata);self.preparing.add(key)
            try:
                # Warm the same lightweight resolver Play uses; never spawn yt-dlp.
                if self.inner.resolve(video_id):continue
                item=self.find(video_id,metadata,thorough=True)
                if item:self.prepare_prefix(video_id,item)
                else:self.fallback.prefetch(video_id)
            finally:
                with self.lock:self.preparing.discard(key)

    def prepare_prefix(self,video_id,item):
        connection=None;url=item['url']
        try:
            for _ in range(5):
                connection,response=get_response(url,{'Range':f'bytes=0-{self.prefixes.fragment-1}'},timeout=3)
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                bounds=content_range(response);mime=response.getheader('Content-Type','').split(';')[0]
                if response.status!=206 or not bounds or bounds[:2]!=(0,min(self.prefixes.fragment,bounds[2])-1) or not mime.startswith('audio/'):return
                data=response.read(self.prefixes.fragment)
                if len(data)!=bounds[1]+1:return
                self.prefixes.put(video_id,dict(item,url=url,mime=mime,data=data,total=bounds[2],expires=time.time()+1800,
                                              ETag=response.getheader('ETag'),**{'Last-Modified':response.getheader('Last-Modified')}))
                return
        except (OSError,http.client.HTTPException,ProviderError,ValueError):pass
        finally:
            if connection:connection.close()

    @staticmethod
    def matches(metadata,track):
        if not isinstance(track,dict):return False
        if not re.fullmatch(r'[A-Za-z0-9]{1,40}',str(track.get('id',''))):return False
        if any(track.get(k) for k in ('is_delete','is_stream_gated')):return False
        if track.get('is_streamable') is False or track.get('is_available') is False:return False
        if normalized(metadata.get('title'))!=normalized(track.get('title')):return False
        artist=normalized(metadata.get('channel'))
        if not artist or artist!=normalized((track.get('user') or {}).get('name')):return False
        expected=metadata.get('duration');actual=track.get('duration')
        if not isinstance(actual,(int,float)) or actual<=0:return False
        if isinstance(expected,(int,float)) and expected>0 and abs(expected-actual)>max(5,expected*.03):return False
        return True

    def find(self,video_id,metadata,thorough=False):
        if not metadata or not metadata.get('title') or not metadata.get('channel'):return None
        key=self.key(video_id,metadata)
        with self.lock:
            hit=self.cache.get(key)
            if hit and hit[0]>time.monotonic():return hit[1]
            # Play never waits behind a speculative network search.
            if not thorough and key in self.preparing:return None
        connection=None;item=None;started=time.perf_counter();ttl=60
        try:
            query=urlencode({'query':metadata['title']+' '+metadata['channel'],'limit':10,'app_name':'IVOX'})
            connection,response=get_response('https://api.audius.co/v1/tracks/search?'+query,
                                             timeout=float(os.environ.get('IVOX_AUDIUS_TIMEOUT','1.5')))
            if response.status!=200:raise ValueError('search_status')
            body=response.read(1024*1024+1)
            if len(body)>1024*1024:raise ValueError('search_too_large')
            data=json.loads(body)
            for track in data.get('data',[]):
                if self.matches(metadata,track):
                    item={'url':'https://api.audius.co/v1/tracks/'+str(track['id'])+'/stream?app_name=IVOX',
                          'mime':'audio/mpeg','source':'audius','track_id':str(track['id']),
                          'duration':track['duration']}
                    break
            ttl=1800
        except (OSError,http.client.HTTPException,ProviderError,ValueError,TypeError,AttributeError):pass
        finally:
            if connection:connection.close()
        if thorough and not item:
            # Artist-only search recovers originals buried under remix results.
            connection=None
            try:
                query=urlencode({'query':metadata['channel'],'limit':50,'app_name':'IVOX'})
                connection,response=get_response('https://api.audius.co/v1/tracks/search?'+query,timeout=1.5)
                body=response.read(1024*1024+1)
                if response.status==200 and len(body)<=1024*1024:
                    for track in json.loads(body).get('data',[]):
                        if self.matches(metadata,track):
                            item={'url':'https://api.audius.co/v1/tracks/'+str(track['id'])+'/stream?app_name=IVOX',
                                  'mime':'audio/mpeg','source':'audius','track_id':str(track['id']),'duration':track['duration']}
                            ttl=1800;break
            except (OSError,http.client.HTTPException,ProviderError,ValueError,TypeError,AttributeError):pass
            finally:
                if connection:connection.close()
        with self.lock:
            if len(self.cache)>=500:self.cache.pop(next(iter(self.cache)))
            self.cache[key]=(time.monotonic()+ttl,item)
        metric('audio_route',video_id=video_id,source='audius' if item else 'yt-dlp',search_ms=round((time.perf_counter()-started)*1000,2))
        return item

    def resolve(self,video_id,metadata=None):
        return self.inner.resolve(video_id) or self.cached_audius(video_id,metadata) or dict(self.fallback.resolve(video_id),source='yt-dlp')

    def cached_audius(self,video_id,metadata):
        with self.lock:cached=self.cache.get(self.key(video_id,metadata)) if metadata else None
        return cached[1] if cached and cached[0]>time.monotonic() else None

    def open(self,video_id,range_header=None,metadata=None):
        started=time.perf_counter()
        direct=self.inner.open(video_id,range_header)
        if direct:return direct
        # Audius preparation remains useful, but Play never launches its search.
        item=self.cached_audius(video_id,metadata)
        if item:
            prefix=self.prefixes.get(video_id,None)
            if prefix and prefix.get('track_id')==item['track_id']:
                response=prefix_response(prefix,range_header,get_response)
                if response:
                    response.ivox_timings.update(source='audius',resolve_ms=round((time.perf_counter()-started)*1000,2))
                    return response,response,prefix['mime']
            connection=None;url=item['url'];resolved=time.perf_counter()
            try:
                for _ in range(5):
                    connection,response=get_response(url,{'Range':range_header} if range_header else {},timeout=2)
                    if response.status in (301,302,303,307,308):
                        url=urljoin(url,response.getheader('Location',''));connection.close();connection=None;continue
                    mime=response.getheader('Content-Type','').split(';')[0]
                    if response.status not in (200,206,416) or (response.status!=416 and not mime.startswith('audio/')):
                        raise AudioUnavailable('audius_stream_unavailable')
                    response.ivox_timings={'source':'audius','resolve_ms':round((resolved-started)*1000,2),
                                          'upstream_headers_ms':round((time.perf_counter()-resolved)*1000,2),'prefix_hit':False}
                    return connection,response,mime or item['mime']
                raise AudioUnavailable('audius_redirects')
            except (OSError,http.client.HTTPException,ProviderError,AudioUnavailable,ValueError):
                if connection:connection.close()
                # A broken stream must not be retried on every Range request.
                key=(video_id,metadata.get('title'),metadata.get('channel'),metadata.get('duration'))
                with self.lock:self.cache[key]=(time.monotonic()+60,None)
        connection,response,mime=self.fallback.open(video_id,range_header)
        response.ivox_timings=getattr(response,'ivox_timings',{})
        response.ivox_timings['source']='yt-dlp'
        response.ivox_timings['router_ms']=round((time.perf_counter()-started)*1000,2)
        return connection,response,mime


audio_router=AudioRouter(resolver)
