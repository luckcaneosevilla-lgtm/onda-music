"""YouTube Data API only. No scraping, media proxy or client-side API keys."""
import html
import http.client
import json
import os
import re
import ssl
import threading
import time
from urllib.parse import urlencode


class ProviderError(Exception):
    def __init__(self, status, code, message, retry_after=0):
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def load_environment(path):
    """Small .env reader; explicit process environment always takes precedence."""
    if not path.exists(): return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key,value=line.split('=',1)
        if key.strip() in ('YOUTUBE_API_KEY','YOUTUBE_REGION','ONDA_DATA','ONDA_SECURE_COOKIE','PIPED_API_URL','PIPED_PROXY_URL','AUDIUS_API_URL','ALLOW_YOUTUBE_EXTERNAL','TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','IVOX_AUDIO_CACHE_SCOPE','IVOX_YTDLP_FAST','IVOX_AUDIO_PREFETCH'):
            os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))


def duration_seconds(value):
    m=re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?',value or '')
    return sum(int(v or 0)*factor for v,factor in zip(m.groups(),(86400,3600,60,1))) if m else None


class YouTube:
    def __init__(self, connection_factory=None):
        self.lock=threading.Lock()
        self.search_cache={}
        self.rate={}
        self.blocked_until=0
        self.connection_factory=connection_factory

    @property
    def configured(self): return bool(os.environ.get('YOUTUBE_API_KEY','').strip())

    @property
    def region(self):
        value=os.environ.get('YOUTUBE_REGION','ES').upper()
        return value if re.fullmatch('[A-Z]{2}',value) else 'ES'

    def request(self,resource,params):
        key=os.environ.get('YOUTUBE_API_KEY','').strip()
        if not key: raise ProviderError(503,'not_configured','La búsqueda online aún no está configurada. El administrador debe añadir YOUTUBE_API_KEY al backend. Consulta Configuración de YouTube.')
        if time.time()<self.blocked_until:
            raise ProviderError(429,'quota_exceeded','La cuota de YouTube está temporalmente agotada. Inténtalo más tarde.',int(self.blocked_until-time.time())+1)
        # Do not inherit SSLKEYLOGFILE: API credentials must not enter TLS key logs.
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.load_default_certs()
        conn=self.connection_factory() if self.connection_factory else http.client.HTTPSConnection('www.googleapis.com',timeout=12,context=context)
        try:
            conn.request('GET','/youtube/v3/'+resource+'?'+urlencode(params),headers={'X-Goog-Api-Key':key})
            response=conn.getresponse(); raw=response.read(2*1024*1024)
            try: result=json.loads(raw)
            except (ValueError,UnicodeDecodeError): raise ProviderError(502,'provider_error','YouTube devolvió una respuesta no válida. Inténtalo de nuevo.')
            if response.status!=200:
                reasons={e.get('reason') for e in result.get('error',{}).get('errors',[])}
                if reasons & {'quotaExceeded','dailyLimitExceeded','rateLimitExceeded','userRateLimitExceeded'} or response.status==429:
                    self.blocked_until=time.time()+300
                    raise ProviderError(429,'quota_exceeded','Se ha alcanzado la cuota de YouTube. Espera y vuelve a intentarlo; el administrador puede revisar la cuota en Google Cloud.',300)
                if reasons & {'keyInvalid','accessNotConfigured','ipRefererBlocked','forbidden'} or response.status in (401,403):
                    raise ProviderError(503,'configuration_error','YouTube rechazó la configuración. El administrador debe revisar la clave, las restricciones y la activación de YouTube Data API v3.')
                if response.status==400: raise ProviderError(400,'invalid_search','YouTube no acepta esta búsqueda o la página ha caducado. Inicia de nuevo la búsqueda.')
                raise ProviderError(502,'provider_error','YouTube no está disponible temporalmente. Inténtalo más tarde.')
            return result
        except (OSError,TimeoutError,http.client.HTTPException):
            raise ProviderError(502,'network_error','No se pudo conectar con YouTube. Comprueba la conexión del servidor y vuelve a intentarlo.') from None
        finally: conn.close()

    def normalize(self,v):
        s=v.get('snippet',{}); content=v.get('contentDetails',{}); status=v.get('status',{})
        blocked=content.get('regionRestriction',{})
        if status.get('embeddable') is not True or status.get('privacyStatus')!='public': return None
        if self.region in blocked.get('blocked',[]) or ('allowed' in blocked and self.region not in blocked['allowed']): return None
        thumb=s.get('thumbnails',{}); image=next((thumb[k].get('url','') for k in ('medium','high','default') if k in thumb),'')
        if not re.fullmatch(r'https://(?:i\.ytimg\.com|img\.youtube\.com)/[^\s]+',image): image=''
        title=html.unescape(s.get('title',''))[:300]; channel=html.unescape(s.get('channelTitle',''))[:200]
        # A textual hint is explicitly not a verified artist/channel badge.
        hint=bool(re.search(r'\b(official|oficial)\b|vevo$',title+' '+channel,re.I))
        return {'provider':'youtube','id':v['id'],'title':title,'channel':channel,'channel_id':s.get('channelId',''), 'thumbnail':image,'duration':duration_seconds(content.get('duration')), 'official_hint':hint,'available':True}

    def details(self,c,ids):
        ids=list(dict.fromkeys(ids))
        if not ids: return []
        response=self.request('videos',{'part':'snippet,contentDetails,status','id':','.join(ids)})
        found={}
        for v in response.get('items',[]):
            item=self.normalize(v)
            if item and re.fullmatch('[A-Za-z0-9_-]{11}',item['id']):
                found[item['id']]=item
                c.execute('INSERT INTO online_catalog VALUES(?,?,?) ON CONFLICT(video_id) DO UPDATE SET metadata=excluded.metadata,refreshed=excluded.refreshed',(item['id'],json.dumps(item),int(time.time())))
        for vid in ids:
            if vid not in found:
                c.execute('DELETE FROM online_catalog WHERE video_id=?',(vid,))
        return [found[vid] for vid in ids if vid in found]

    def search(self,c,user_id,query,page='',official=False):
        if not self.configured: raise ProviderError(503,'not_configured','La búsqueda online aún no está configurada. Añade YOUTUBE_API_KEY al backend; consulta Configuración de YouTube.')
        cache_key=(query.casefold(),page,official,self.region)
        # Single flight: concurrent identical searches share the first response.
        with self.lock:
            stamp=time.time()
            cached=self.search_cache.get(cache_key)
            if cached and cached[0]>stamp: return dict(cached[1],cached=True)
            until,count=self.rate.get(user_id,(stamp+60,0))
            if stamp>until: until,count=stamp+60,0
            if count>=12: raise ProviderError(429,'search_rate_limit','Demasiadas búsquedas seguidas. Espera un minuto.',max(1,int(until-stamp)))
            self.rate[user_id]=(until,count+1)
            q=query+' official' if official else query
            response=self.request('search',{'part':'snippet','q':q,'type':'video','videoCategoryId':'10','videoEmbeddable':'true','videoSyndicated':'true','order':'relevance','maxResults':20,'regionCode':self.region,'relevanceLanguage':'es',**({'pageToken':page} if page else {})})
            ids=[v.get('id',{}).get('videoId','') for v in response.get('items',[])]
            ids=[vid for vid in ids if re.fullmatch('[A-Za-z0-9_-]{11}',vid)]
            items=self.details(c,ids)
            items.sort(key=lambda v:not v['official_hint'])
            result={'items':items,'next_page':response.get('nextPageToken'), 'region':self.region,'cached':False}
            self.search_cache={k:v for k,v in self.search_cache.items() if v[0]>stamp}
            if len(self.search_cache)>=200: self.search_cache.pop(next(iter(self.search_cache)))
            self.search_cache[cache_key]=(stamp+600,result)
            return result

    def metadata(self,c,ids):
        result={}
        for vid in dict.fromkeys(ids):
            row=c.execute('SELECT metadata,refreshed FROM online_catalog WHERE video_id=?',(vid,)).fetchone()
            if row and row['refreshed']>time.time()-86400:
                result[vid]=json.loads(row['metadata'])
        # Stored user references survive; provider metadata is refreshed or removed.
        missing=[vid for vid in dict.fromkeys(ids) if vid not in result]
        if self.configured:
            for offset in range(0,len(missing),50):
                try:
                    for item in self.details(c,missing[offset:offset+50]): result[item['id']]=item
                except ProviderError: break
        return {vid:result.get(vid,{'provider':'youtube','id':vid,'title':'Vídeo no disponible o pendiente de actualizar','channel':'YouTube','thumbnail':'','duration':None,'available':False}) for vid in dict.fromkeys(ids)}


provider=YouTube()
