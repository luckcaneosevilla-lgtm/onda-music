"""Bounded, unauthenticated YouTube Music catalogue: search, artist profiles, radio and lyrics."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unicodedata
from youtube_provider import ProviderError

def https_image(thumbnails):
    # The last thumbnail is the largest; only https URLs are rendered by the browser.
    return next((t.get('url','') for t in reversed(thumbnails or []) if isinstance(t,dict) and str(t.get('url','')).startswith('https://')),'')

def canonical(value):
    return re.sub(r'[^0-9a-z]','',unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode().lower())

class MusicSearch:
    """Talks to one long-lived worker process; many requests share it concurrently."""
    def __init__(self):
        self.process=None;self.guard=threading.Lock();self.waiting={};self.sequence=0;self.timeouts=0
    def spawn(self):
        env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'}
        env['PYTHONIOENCODING']='utf-8'
        vendor=Path(__file__).resolve().parent/'.vendor'
        if vendor.exists():env['PYTHONPATH']=str(vendor)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',bufsize=1,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self.pump,args=(process,),daemon=True).start()
        return process
    def pump(self,process):
        for line in process.stdout:
            try:answer=json.loads(line)
            except ValueError:continue
            with self.guard:slot=self.waiting.pop(answer.get('id'),None)
            if slot:slot['answer']=answer;slot['done'].set()
        # The worker exited: everyone still waiting on it fails now instead of timing out.
        with self.guard:
            if self.process is process:self.process=None
            orphans=[s for s in self.waiting.values() if s['process'] is process]
            self.waiting={k:v for k,v in self.waiting.items() if v['process'] is not process}
        for slot in orphans:slot['done'].set()
    def start(self,warm=True):
        with self.guard:
            if self.process is None or self.process.poll() is not None:self.process=self.spawn()
        # One tiny search opens the TLS connection so the first real search is not ~1.5 s slower.
        if warm:threading.Thread(target=self.warm,daemon=True).start()
    def warm(self):
        try:self.search('IVOX','songs',1)
        except ProviderError:pass
    def call(self,op,timeout=25,**args):
        slot={'done':threading.Event(),'answer':None}
        try:
            with self.guard:
                if self.process is None or self.process.poll() is not None:self.process=self.spawn()
                self.sequence+=1;key=self.sequence;process=self.process;slot['process']=process
                self.waiting[key]=slot
                process.stdin.write(json.dumps({'id':key,'op':op,'args':args})+'\n');process.stdin.flush()
        except (OSError,ValueError):
            with self.guard:self.waiting.pop(self.sequence,None)
            raise ProviderError(502,'ytmusic_unavailable','YouTube Music no está disponible temporalmente. Se intentará la búsqueda de respaldo.') from None
        answered=slot['done'].wait(timeout)
        with self.guard:
            self.waiting.pop(key,None)
            if answered:self.timeouts=0
            else:
                # Two consecutive stalls mean the worker is wedged; the next call starts a fresh one.
                self.timeouts+=1
                if self.timeouts>=2 and self.process is process:
                    self.timeouts=0;process.kill();self.process=None
        answer=slot['answer']
        if not answer or not answer.get('ok'):
            raise ProviderError(502,'ytmusic_unavailable','YouTube Music no está disponible temporalmente. Se intentará la búsqueda de respaldo.')
        return answer['result']
    def search(self,query,kind='songs',limit=20):
        entries=self.call('search',query=query,kind=kind,limit=min(limit,100))
        if not isinstance(entries,list):raise ProviderError(502,'ytmusic_unavailable','YouTube Music devolvió una respuesta inesperada.')
        return entries
    def profile(self,query):
        """The artist the query names, or None: a top result that is an artist, or an exact-name artist match."""
        return self.call('profile',query=query)
    def get_artist(self,browse_id):return self.call('artist',browse_id=browse_id)
    def radio(self,video_id,limit=25):return self.call('radio',video_id=video_id,limit=limit)
    def lyrics(self,video_id):return self.call('lyrics',video_id=video_id)
    def charts(self,country):
        result=self.call('charts',country=country)
        if not isinstance(result,dict):raise ProviderError(502,'ytmusic_unavailable','YouTube Music devolvió una respuesta inesperada.')
        return result
    def suggest(self,query):
        result=self.call('suggest',timeout=4,query=query)
        if not isinstance(result,dict):raise ProviderError(502,'ytmusic_unavailable','YouTube Music devolvió una respuesta inesperada.')
        return result

    @staticmethod
    def normalize(entry):
        raw=entry.get('videoId','')
        if not isinstance(raw,str) or not re.fullmatch('[A-Za-z0-9_-]{11}',raw) or entry.get('isAvailable') is False:return None
        artists=[str(a['name']) for a in (entry.get('artists') or []) if isinstance(a,dict) and a.get('name')]
        seconds=entry.get('duration_seconds')
        if not isinstance(seconds,(int,float)):
            value=entry.get('duration') or entry.get('length') or ''
            try:
                parts=[int(p) for p in value.split(':')];seconds=sum(v*60**i for i,v in enumerate(reversed(parts))) if 1<=len(parts)<=3 else None
            except (TypeError,ValueError,AttributeError):seconds=None
        album=entry.get('album')
        if isinstance(album,dict):album=album.get('name')
        image=https_image(entry.get('thumbnails') or entry.get('thumbnail'))
        return {'id':'piped:'+raw,'provider_id':raw,'provider':'piped','source':'YouTube Music','title':str(entry.get('title',''))[:300],'channel':', '.join(artists)[:200],'artists':artists,'album':str(album)[:200] if album else None,'thumbnail':image,'duration':seconds,'available':True}

    @staticmethod
    def artist(entry):
        raw=entry.get('browseId','');name=entry.get('artist',entry.get('name',entry.get('title','')))
        if not raw or not name:return None
        return {'id':str(raw)[:100],'name':str(name)[:200],'thumbnail':https_image(entry.get('thumbnails')),'source':'YouTube Music'}

    @staticmethod
    def artist_profile(data,browse_id):
        """Shape a get_artist() answer: header, popular songs and related artists."""
        if not isinstance(data,dict) or not data.get('name'):return None
        thumbnails=data.get('thumbnails') or []
        # YouTube Music gives a wide banner (2880x1200) and a square avatar at w544.
        banner=https_image(thumbnails)
        avatar=re.sub(r'=w\d+-h\d+','=w544-h544',banner) if banner else ''
        songs=[MusicSearch.normalize(s) for s in (data.get('songs') or {}).get('results') or []]
        related=[]
        for r in ((data.get('related') or {}).get('results') or [])[:12]:
            if isinstance(r,dict) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}',str(r.get('browseId',''))) and r.get('title'):
                related.append({'id':r['browseId'],'name':str(r['title'])[:200],'thumbnail':https_image(r.get('thumbnails')),'subscribers':str(r.get('subscribers') or '')[:20]})
        description=str(data.get('description') or '').strip()
        return {'id':browse_id,'name':str(data['name'])[:200],'banner':banner,'avatar':avatar,
            'monthly_listeners':str(data.get('monthlyListeners') or '')[:20] or None,'subscribers':str(data.get('subscribers') or '')[:20] or None,
            'views':str(data.get('views') or '')[:40] or None,'description':description[:600],
            'songs':[s for s in songs if s],'related':related,'radio_id':str(data.get('radioId') or '')[:80] or None,'source':'YouTube Music'}

def worker():
    # The subprocess keeps third-party TLS/client globals out of the threaded
    # application server; every request is bounded by a session timeout.
    from concurrent.futures import ThreadPoolExecutor
    from ytmusicapi import YTMusic
    import requests
    class BoundedSession(requests.Session):
        def request(self,*args,**kwargs):
            kwargs.setdefault('timeout',10)
            return super().request(*args,**kwargs)
    local=threading.local();write=threading.Lock()
    def client():
        # The pinned parser returns empty song lists with the Spanish localization.
        # English internal labels work; original song/artist names and IVOX stay unchanged.
        if not hasattr(local,'client'):local.client=YTMusic(language='en',location='ES',requests_session=BoundedSession())
        return local.client
    def profile(query):
        results=client().search(query,limit=10)
        wanted=canonical(query);browse=None
        for entry in results:
            if entry.get('resultType')!='artist':continue
            ids=[entry.get('browseId')]+[a.get('id') for a in entry.get('artists') or [] if isinstance(a,dict)]
            names=[entry.get('artist')]+[a.get('name') for a in entry.get('artists') or [] if isinstance(a,dict)]
            # Accept the top result, or any artist whose name is exactly what was typed.
            if entry.get('category')=='Top result' or wanted in {canonical(n) for n in names if n}:
                browse=next((i for i in ids if isinstance(i,str) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}',i)),None)
                if browse:break
        if not browse:return None
        return MusicSearch.artist_profile(client().get_artist(browse),browse)
    def radio(video_id,limit):
        tracks=client().get_watch_playlist(videoId=video_id,radio=True,limit=limit).get('tracks') or []
        return [t for t in tracks if isinstance(t,dict) and t.get('videoId')!=video_id]
    def lyrics(video_id):
        browse=client().get_watch_playlist(videoId=video_id,limit=1).get('lyrics')
        if not browse:return None
        data=client().get_lyrics(browse,timestamps=True)
        if not data or not data.get('lyrics'):return None
        if data.get('hasTimestamps'):
            lines=[{'start':int(l.start_time),'end':int(l.end_time),'text':str(l.text)} for l in data['lyrics']]
            return {'synced':True,'lines':lines,'source':str(data.get('source') or '')}
        return {'synced':False,'text':str(data['lyrics']),'source':str(data.get('source') or '')}
    def suggest(query):
        # The typeahead endpoint answers in ~80 ms with query completions plus the
        # artists and songs YouTube Music would offer for a half-typed name.
        data=client()._send_request('music/get_search_suggestions',{'input':query})
        queries,artists,songs=[],[],[]
        for section in data.get('contents') or []:
            for item in (section.get('searchSuggestionsSectionRenderer') or {}).get('contents') or []:
                text=item.get('searchSuggestionRenderer')
                if text:
                    value=''.join(r.get('text','') for r in (text.get('suggestion') or {}).get('runs') or []).strip()
                    if value:queries.append(value)
                    continue
                entry=item.get('musicResponsiveListItemRenderer')
                if not entry:continue
                runs=[r for col in entry.get('flexColumns') or [] for r in ((col.get('musicResponsiveListItemFlexColumnRenderer') or {}).get('text') or {}).get('runs') or []]
                if not runs:continue
                thumbnails=(((entry.get('thumbnail') or {}).get('musicThumbnailRenderer') or {}).get('thumbnail') or {}).get('thumbnails')
                nav=entry.get('navigationEndpoint') or {}
                if nav.get('watchEndpoint',{}).get('videoId'):
                    names=[r['text'] for r in runs[1:] if str(((r.get('navigationEndpoint') or {}).get('browseEndpoint') or {}).get('browseId','')).startswith('UC')]
                    if not names:names=[r['text'] for r in runs[3:4] if r.get('text','').strip(' •&,')]
                    songs.append({'videoId':nav['watchEndpoint']['videoId'],'title':runs[0].get('text',''),'artists':[{'name':n} for n in names],'thumbnails':thumbnails,'kind':runs[1].get('text','') if len(runs)>1 else ''})
                elif str((nav.get('browseEndpoint') or {}).get('browseId','')).startswith('UC'):
                    artists.append({'browseId':nav['browseEndpoint']['browseId'],'artist':runs[0].get('text',''),'thumbnails':thumbnails})
        return {'queries':queries[:8],'artists':artists[:4],'songs':songs[:5]}
    def charts(country):
        # The country's daily chart is a playlist; its tracks carry artists, art and length.
        data=client().get_charts(country=country)
        lists=[v for v in data.get('videos') or [] if isinstance(v,dict) and v.get('playlistId')]
        daily=next((v for v in lists if 'daily' in str(v.get('title','')).lower()),lists[0] if lists else None)
        tracks=(client().get_playlist(daily['playlistId'],limit=30).get('tracks') or [])[:30] if daily else []
        return {'tracks':tracks,'artists':[a for a in data.get('artists') or [] if isinstance(a,dict)][:15]}
    def handle(request):
        op,args=request.get('op'),request.get('args') or {}
        try:
            if op=='search':
                kind,limit=args['kind'],int(args['limit'])
                if kind not in ('songs','artists') or not 1<=limit<=100:raise ValueError()
                result=client().search(args['query'],filter=kind,limit=limit)
            elif op=='profile':result=profile(args['query'])
            elif op=='artist':result=MusicSearch.artist_profile(client().get_artist(args['browse_id']),args['browse_id'])
            elif op=='radio':result=radio(args['video_id'],int(args.get('limit',25)))
            elif op=='lyrics':result=lyrics(args['video_id'])
            elif op=='charts':result=charts(str(args['country'])[:2])
            elif op=='suggest':result=suggest(str(args['query'])[:80])
            else:raise ValueError()
            answer={'id':request.get('id'),'ok':True,'result':result}
        except Exception:answer={'id':request.get('id'),'ok':False}
        line=json.dumps(answer,ensure_ascii=True,default=str)
        with write:sys.stdout.write(line+'\n');sys.stdout.flush()
    with ThreadPoolExecutor(4) as pool:
        for line in sys.stdin:
            try:pool.submit(handle,json.loads(line))
            except ValueError:continue

if __name__=='__main__' and sys.argv[1:2]==['--worker']:worker()
