"""Private Piped + public Audius adapters. References only; no media storage."""
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlencode,urlparse,urljoin,parse_qs
from youtube_provider import ProviderError,YouTube
from ytmusic_search import MusicSearch


def base(name,default):return os.environ.get(name,default).rstrip('/')
def valid_id(value):return bool(re.fullmatch(r'(piped:[A-Za-z0-9_-]{11}|audius:[A-Za-z0-9]{1,40})',value))
def secure_context():
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.load_default_certs();return context

def get_response(url,headers=None,timeout=15,local=False):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password or not parsed.hostname:
        raise ProviderError(502,'invalid_url','El proveedor devolvió una URL inválida.')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    if not local:
        addresses=socket.getaddrinfo(parsed.hostname,port,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ProviderError(502,'invalid_url','El proveedor devolvió un destino no permitido.')
        # Pin the validated IP; preserve TLS server name and Host.
        ip=addresses[0][4][0]
        class PublicHTTPS(http.client.HTTPSConnection):
            def connect(self):
                self.sock=socket.create_connection((ip,port),self.timeout)
                self.sock=self._context.wrap_socket(self.sock,server_hostname=parsed.hostname)
        if parsed.scheme!='https':raise ProviderError(502,'invalid_url','El proveedor debe usar HTTPS.')
        connection=PublicHTTPS(parsed.hostname,port,timeout=timeout,context=secure_context())
    else:
        connection=(http.client.HTTPSConnection(parsed.hostname,port,timeout=timeout,context=secure_context()) if parsed.scheme=='https' else http.client.HTTPConnection(parsed.hostname,port,timeout=timeout))
    try:
        connection.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers=headers or {})
        return connection,connection.getresponse()
    except Exception:
        connection.close();raise


class MusicProvider:
    def __init__(self):
        self.lock=threading.Lock();self.cache={};self.cursor_key=secrets.token_bytes(32);self.tickets={};self.rate={};self.music=MusicSearch();self.youtube=YouTube()
    @property
    def configured(self):return True
    @property
    def region(self):return 'proveedor'
    @property
    def piped(self):return base('PIPED_API_URL','http://127.0.0.1:8080')
    @property
    def proxy(self):return base('PIPED_PROXY_URL','http://127.0.0.1:8081')
    @property
    def audius(self):return base('AUDIUS_API_URL','https://api.audius.co')

    def request(self,source,path,params=None):
        url=(self.piped if source=='piped' else self.audius)+path
        if params:url+='?'+urlencode(params)
        connection=None
        try:
            connection,response=get_response(url,timeout=12 if source=='piped' else 15,local=source=='piped')
            body=response.read(4*1024*1024)
            if response.status==429:raise ProviderError(429,'quota_exceeded',f'{source.title()} limita temporalmente las peticiones. Espera y vuelve a intentarlo.',60)
            if response.status!=200:raise ProviderError(502,'provider_unavailable',f'{source.title()} no pudo completar la petición (HTTP {response.status}). Prueba otra fuente o canción.')
            result=json.loads(body)
            if isinstance(result,dict) and result.get('error'):raise ProviderError(502,'provider_unavailable',f'{source.title()} no pudo obtener el contenido. Puede estar bloqueado o no disponible.')
            return result
        except (OSError,TimeoutError,http.client.HTTPException,ValueError):
            raise ProviderError(502,'provider_unavailable',f'No se pudo conectar con {source.title()}. '+('Comprueba docker compose y la conexión de la instancia.' if source=='piped' else 'Prueba de nuevo más tarde.')) from None
        finally:
            if connection:connection.close()

    def health(self):
        conn=None
        try:
            conn,r=get_response(self.piped+'/healthcheck',timeout=3,local=True);r.read(1000)
            return r.status==200
        except (OSError,ValueError,http.client.HTTPException):return False
        finally:
            if conn:conn.close()

    def cursor(self,q,source,value):
        raw=json.dumps({'q':q,'source':source,'value':value,'expires':int(time.time())+1800},separators=(',',':')).encode()
        return base64.urlsafe_b64encode(hmac.digest(self.cursor_key,raw,'sha256')+raw).decode().rstrip('=')
    def uncursor(self,page,q,source):
        try:
            raw=base64.urlsafe_b64decode(page+'='*(-len(page)%4));signature,payload=raw[:32],raw[32:]
            data=json.loads(payload)
            if not hmac.compare_digest(signature,hmac.digest(self.cursor_key,payload,'sha256')) or data['q']!=q or data['source']!=source or data['expires']<time.time():raise ValueError()
            return data['value']
        except (ValueError,KeyError,TypeError):raise ProviderError(400,'invalid_search','La página ha caducado. Inicia de nuevo la búsqueda.') from None

    def normalize(self,item,source):
        if source=='piped':
            url=item.get('url','');raw=parse_qs(urlparse(url).query).get('v',[''])[0]
            if not raw and item.get('id'):raw=item['id']
            if not re.fullmatch('[A-Za-z0-9_-]{11}',raw):return None
            title=item.get('title','');artist=item.get('uploaderName',item.get('uploader',''));image=item.get('thumbnail',item.get('thumbnailUrl',''));seconds=item.get('duration')
        else:
            raw=str(item.get('id',''))
            if not re.fullmatch('[A-Za-z0-9]{1,40}',raw) or item.get('is_delete') or item.get('is_stream_gated') or item.get('is_streamable') is False or item.get('is_available') is False:return None
            title=item.get('title','');artist=item.get('user',{}).get('name','');art=item.get('artwork') or {};image=art.get('480x480',art.get('150x150',''));seconds=item.get('duration')
        image=image if isinstance(image,str) and image.startswith(('http://','https://')) else ''
        original_image=image
        if image.startswith(self.proxy+'/'):
            # Loopback images are relayed via the authenticated ONDA backend.
            image='/api/online/art/'+source+':'+raw
        return {'id':source+':'+raw,'provider_id':raw,'provider':source,'source':'Piped' if source=='piped' else 'Audius','title':str(title)[:300],'channel':str(artist)[:200],'thumbnail':image,'art_url':original_image,'duration':seconds if isinstance(seconds,(int,float)) and seconds>=0 else None,'available':True}

    def store(self,c,items):
        for item in items:c.execute('INSERT INTO online_catalog VALUES(?,?,?) ON CONFLICT(video_id) DO UPDATE SET metadata=excluded.metadata,refreshed=excluded.refreshed',(item['id'],json.dumps(item),int(time.time())))

    def search_one(self,c,q,page,source):
        cursor=self.uncursor(page,q,source) if page else None
        if source=='piped':
            params={'q':q,'filter':'music_songs'}
            if cursor:params['nextpage']=cursor
            data=self.request(source,'/nextpage/search' if cursor else '/search',params)
            items=[self.normalize(item,source) for item in data.get('items',[]) if item.get('type','stream')=='stream']
            nextpage=data.get('nextpage')
        else:
            offset=int(cursor or 0)
            data=self.request(source,'/v1/tracks/search',{'query':q,'limit':20,'offset':offset,'app_name':'ONDA'})
            entries=data.get('data',[]);items=[self.normalize(item,source) for item in entries]
            nextpage=offset+20 if len(entries)==20 else None
        items=[item for item in items if item];self.store(c,items)
        if isinstance(nextpage,(dict,list)):nextpage=json.dumps(nextpage,separators=(',',':'))
        return {'items':items,'source':source,'next_page':self.cursor(q,source,nextpage) if nextpage not in (None,'',False) else None,'cached':False}

    def music_search(self,c,q,page,kind):
        context='ytmusic-'+kind;offset=int(self.uncursor(page,q,context)) if page else 0
        if not 0<=offset<100:raise ProviderError(400,'invalid_search','Límite de paginación alcanzado. Ajusta la búsqueda.')
        requested=min(100,offset+20);entries=self.music.search(q,kind,requested)
        if kind=='songs':
            normalized=[self.music.normalize(e) for e in entries];normalized=[e for e in normalized if e]
            unique=list({e['id']:e for e in normalized}.values());items=unique[offset:offset+20];self.store(c,items);artists=[]
        else:
            normalized=[self.music.artist(e) for e in entries];unique=list({e['id']:e for e in normalized if e}.values());artists=unique[offset:offset+20];items=[]
        next_offset=offset+len(items if kind=='songs' else artists)
        more=len(entries)>=requested and offset<next_offset<100
        return {'items':items,'artists':artists,'source':'ytmusic','filter':kind,'next_page':self.cursor(q,context,next_offset) if more else None,'cached':False}

    def youtube_search(self,c,q,page,kind):
        context='youtube-'+kind;token=self.uncursor(page,q,context) if page else ''
        data=self.youtube.request('search',{'part':'snippet','q':q,'type':'channel' if kind=='artists' else 'video','maxResults':20,**({} if kind=='artists' else {'videoCategoryId':'10'}),**({'pageToken':token} if token else {})})
        items=[];artists=[]
        if kind=='artists':
            for e in data.get('items',[]):
                s=e.get('snippet',{});thumb=s.get('thumbnails',{}).get('medium',{}).get('url','')
                artists.append({'id':e['id']['channelId'],'name':s.get('title',''),'thumbnail':thumb,'source':'YouTube · canal'})
        else:
            ids=[e.get('id',{}).get('videoId','') for e in data.get('items',[])];ids=[v for v in ids if re.fullmatch('[A-Za-z0-9_-]{11}',v)]
            details=self.youtube.request('videos',{'part':'snippet,contentDetails,status','id':','.join(ids)}) if ids else {'items':[]}
            for v in details.get('items',[]):
                item=self.youtube.normalize(v)
                if item:
                    raw=item['id'];item.update(id='piped:'+raw,provider_id=raw,provider='piped',source='YouTube Data API');items.append(item)
            self.store(c,items)
        nextpage=data.get('nextPageToken')
        return {'items':items,'artists':artists,'source':'youtube','filter':kind,'next_page':self.cursor(q,context,nextpage) if nextpage else None,'cached':False,'notice':'Búsqueda de respaldo de YouTube Data API: vídeos o canales; pueden diferir del catálogo musical.'}

    def search(self,c,user_id,q,page='',source='auto',kind='songs'):
        if source not in ('auto','ytmusic','youtube','piped','audius') or kind not in ('songs','artists'):raise ProviderError(400,'invalid_search','Fuente o filtro inválido.')
        key=(q.casefold(),page,source,kind)
        with self.lock:
            cached=self.cache.get(key)
            if cached and cached[0]>time.time():self.store(c,cached[1]['items']);return dict(cached[1],cached=True)
            stamp=time.time();until,count=self.rate.get(user_id,(stamp+60,0))
            if stamp>until:until,count=stamp+60,0
            if count>=20:raise ProviderError(429,'search_rate_limit','Demasiadas búsquedas seguidas. Espera un minuto.',60)
            self.rate[user_id]=(until,count+1)
            if source=='auto' and page:raise ProviderError(400,'invalid_search','Selecciona la fuente de la página antes de continuar.')
            if source in ('auto','ytmusic'):
                try:result=self.music_search(c,q,page,kind)
                except ProviderError as error:
                    if error.status==400:raise
                    try:
                        result=self.youtube_search(c,q,'',kind);result['fallback']=True
                    except ProviderError:
                        if kind=='artists':raise ProviderError(502,'provider_unavailable','No se pudieron consultar los artistas. Prueba de nuevo o busca canciones.') from None
                        try:result=self.search_one(c,q,'','piped')
                        except ProviderError:result=self.search_one(c,q,'','audius')
                        result['notice']='YouTube Music y YouTube Data API no están disponibles. Resultados de respaldo de '+result['source'].title()+'.';result['fallback']=True
            elif source=='youtube':result=self.youtube_search(c,q,page,kind)
            else:
                if kind!='songs':raise ProviderError(400,'invalid_search','El filtro de artistas requiere YouTube Music o YouTube Data API.')
                result=self.search_one(c,q,page,source)
            self.cache={k:v for k,v in self.cache.items() if v[0]>stamp}
            if len(self.cache)>=200:self.cache.pop(next(iter(self.cache)))
            self.cache[key]=(stamp+600,result);return result

    def metadata(self,c,ids):
        result={}
        for vid in dict.fromkeys(ids):
            row=c.execute('SELECT metadata FROM online_catalog WHERE video_id=?',(vid,)).fetchone()
            if row:result[vid]=json.loads(row['metadata'])
            else:result[vid]={'id':vid,'provider':vid.split(':')[0],'provider_id':vid.split(':')[-1],'source':vid.split(':')[0].title(),'title':'Referencia pendiente de actualizar','channel':'','thumbnail':'','duration':None,'available':False}
        return result

    def stream(self,c,uid,reference,preferred='mp4',minimum_level=2):
        if not valid_id(reference):raise ProviderError(400,'invalid_id','Referencia inválida.')
        source,raw=reference.split(':',1)
        if source=='piped':
            data=self.request(source,'/streams/'+raw)
            streams=[s for s in data.get('audioStreams',[]) if (s.get('mimeType','').split(';')[0] in ('audio/mp4','audio/webm')) and s.get('url')] if minimum_level<=2 else []
            streams.sort(key=lambda s:(preferred not in s.get('mimeType',''),-int(s.get('bitrate') or 0)))
            level=2
            if not streams:
                streams=[s for s in data.get('videoStreams',[]) if s.get('videoOnly') is False and s.get('mimeType','').split(';')[0]=='video/mp4' and s.get('url')]
                level=3
            if not streams:raise ProviderError(422,'no_audio','No disponible, elige otro.')
            candidate=streams[0];url=candidate['url'];mime=candidate['mimeType']
            if urlparse(url).netloc!=urlparse(self.proxy).netloc or urlparse(url).scheme!=urlparse(self.proxy).scheme:raise ProviderError(502,'invalid_url','Piped devolvió un stream fuera del proxy configurado. Revisa PIPED_PROXY_URL.')
            item=self.normalize(dict(data,id=raw),source)
            if item:self.store(c,[item])
        else:
            data=self.request(source,'/v1/tracks/'+raw,{'app_name':'ONDA'}).get('data',{})
            if not self.normalize(data,source):raise ProviderError(422,'no_audio','La canción de Audius no está disponible para reproducción pública.')
            url=self.audius+'/v1/tracks/'+raw+'/stream?app_name=ONDA';mime='audio/mpeg';level=4
        token=secrets.token_urlsafe(32)
        with self.lock:
            self.tickets={k:v for k,v in self.tickets.items() if v['expires']>time.time()}
            if len(self.tickets)>=1000:self.tickets.pop(next(iter(self.tickets)))
            self.tickets[token]={'uid':uid,'url':url,'source':source,'expires':time.time()+3600,'reference':reference,'level':level,'muxed':level==3}
        return {'url':'/api/online/media/'+token,'mime':mime,'source':source,'expires_in':3600,'level':level,'muxed':level==3}

    def media(self,uid,token,headers=None):
        ticket=self.tickets.get(token)
        if not ticket or ticket['uid']!=uid or ticket['expires']<time.time():raise ProviderError(404,'expired_stream','La sesión del audio ha caducado. Vuelve a pulsar Play.')
        url=ticket['url'];connection=None
        try:
            for _ in range(5):
                local=ticket['source']=='piped' and urlparse(url).netloc==urlparse(self.proxy).netloc
                connection,response=get_response(url,headers,timeout=20,local=local)
                if response.status in (301,302,303,307,308):
                    destination=urljoin(url,response.getheader('Location',''));connection.close();connection=None
                    if ticket['source']=='piped' and urlparse(destination).netloc!=urlparse(self.proxy).netloc:raise ProviderError(502,'invalid_url','El proxy redirigió a un destino no permitido.')
                    url=destination;continue
                if response.status not in (200,206):connection.close();raise ProviderError(502,'stream_unavailable',f'El proveedor rechazó el audio (HTTP {response.status}). Prueba otra canción.')
                mime=response.getheader('Content-Type','').split(';')[0]
                if not (mime.startswith(('audio/','application/octet-stream')) or (ticket.get('muxed') and mime=='video/mp4')):connection.close();raise ProviderError(502,'stream_unavailable','El proveedor no devolvió audio reproducible.')
                return connection,response
            raise ProviderError(502,'stream_unavailable','Demasiadas redirecciones del proveedor.')
        except (OSError,http.client.HTTPException):
            if connection:connection.close()
            raise ProviderError(502,'stream_unavailable','Se perdió la conexión con el audio. Vuelve a pulsar Play.') from None


provider=MusicProvider()
