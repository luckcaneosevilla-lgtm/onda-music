import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from youtube_provider import ProviderError, load_environment
from music_provider import provider, valid_id, get_response
from audio_resolver import resolver, AudioUnavailable
from streaming import metric
import turso

ROOT = Path(__file__).resolve().parent
load_environment(ROOT / '.env')
DATA = Path(os.environ.get('ONDA_DATA', ROOT / 'data')).resolve()
MB = 1024**2
# Sessions slide: each day of use pushes expiry 180 days ahead, so a phone that
# opens IVOX now and then stays signed in until the user logs out.
SESSION_SECONDS = 180*86400
# Audio relays re-check the session for every chunk; with a cloud database each
# check is a network round trip, so verified sessions are reused briefly.
# IVOX_TRACE=1 prints how long each API request takes (time to audio headers too).
TRACE = os.environ.get('IVOX_TRACE')=='1'
SESSION_CACHE = {}; SESSION_CACHE_SECONDS = 20; session_guard = threading.Lock()
def forget_sessions(token=None):
    with session_guard:
        if token: SESSION_CACHE.pop(token, None)
        else: SESSION_CACHE.clear()
def session_cookie(token):
    secure='; Secure' if os.environ.get('ONDA_SECURE_COOKIE')=='1' else ''
    return f'onda={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}'
LIGHT_WRITES = re.compile(r'/api/(audio/prefetch|online/items/|online/recent$|online/playlists/[a-f0-9]{32}/items$)')
TURSO = turso.Client(os.environ['TURSO_DATABASE_URL'], os.environ['TURSO_AUTH_TOKEN']) if os.environ.get('TURSO_DATABASE_URL') and os.environ.get('TURSO_AUTH_TOKEN') else None

def now(): return int(time.time())

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

def db():
    # With Turso configured the data lives in the cloud and survives container
    # restarts; without it (local dev, tests) the classic SQLite file is used.
    if TURSO: return turso.Connection(TURSO)
    c = sqlite3.connect(DATA / 'onda.sqlite', timeout=30, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    (DATA / 'audio').mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'active', plan TEXT NOT NULL DEFAULT 'free', expires INTEGER, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS plans(name TEXT PRIMARY KEY, storage INTEGER NOT NULL, playlists INTEGER NOT NULL, file_size INTEGER NOT NULL, features TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),title TEXT NOT NULL,size INTEGER NOT NULL,mime TEXT NOT NULL,favorite INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS playlists(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS playlist_tracks(playlist_id TEXT REFERENCES playlists(id) ON DELETE CASCADE,track_id TEXT REFERENCES tracks(id) ON DELETE CASCADE,PRIMARY KEY(playlist_id,track_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT REFERENCES users(id),target TEXT REFERENCES users(id),action TEXT NOT NULL,reason TEXT,before_state TEXT,after_state TEXT,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS online_catalog(video_id TEXT PRIMARY KEY,metadata TEXT NOT NULL,refreshed INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS online_saves(user_id TEXT REFERENCES users(id),video_id TEXT NOT NULL,favorite INTEGER NOT NULL DEFAULT 0,later INTEGER NOT NULL DEFAULT 0,last_played INTEGER,PRIMARY KEY(user_id,video_id));
        CREATE TABLE IF NOT EXISTS online_playlist_tracks(playlist_id TEXT REFERENCES playlists(id) ON DELETE CASCADE,video_id TEXT NOT NULL,added INTEGER NOT NULL,PRIMARY KEY(playlist_id,video_id));
        CREATE TABLE IF NOT EXISTS recent_searches(user_id TEXT REFERENCES users(id),query TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(user_id,query));
        CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS admin_recovery(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),expires INTEGER NOT NULL);
        ''')
        if not c.execute("SELECT 1 FROM migrations WHERE name='hybrid-reference-ids-v1'").fetchone():
            for row in c.execute('SELECT * FROM online_catalog').fetchall():
                if ':' in row['video_id']:continue
                item=json.loads(row['metadata']);item.update(id='piped:'+row['video_id'],provider_id=row['video_id'],provider='piped',source='Piped')
                c.execute('INSERT OR IGNORE INTO online_catalog VALUES(?,?,?)',(item['id'],json.dumps(item),row['refreshed']))
                c.execute('DELETE FROM online_catalog WHERE video_id=?',(row['video_id'],))
            for table in ('online_saves','online_playlist_tracks'):
                c.execute("UPDATE OR IGNORE "+table+" SET video_id='piped:'||video_id WHERE instr(video_id,':')=0")
            c.execute("INSERT INTO migrations VALUES('hybrid-reference-ids-v1')")
        c.execute('DELETE FROM online_catalog WHERE refreshed<?',(now()-30*86400,))
        c.execute('INSERT OR IGNORE INTO plans VALUES(?,?,?,?,?)', ('free',1024*MB,10,50*MB,'{}'))
        c.execute('INSERT OR IGNORE INTO plans VALUES(?,?,?,?,?)', ('premium',20*1024*MB,200,250*MB,'{}'))
    resolver.persist(DATA / 'onda.sqlite',cloud_factory=(lambda:turso.Connection(TURSO)) if TURSO else None,scope=os.environ.get('IVOX_AUDIO_CACHE_SCOPE'))

def password_hash(password):
    salt = secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()

def password_ok(password, saved):
    salt, value = saved.split(':')
    return hmac.compare_digest(hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex(),value)

class Problem(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

def require(condition, message, status=400):
    if not condition: raise Problem(status,message)

def effective(u): return 'premium' if u['plan']=='premium' and (u['expires'] is None or u['expires'] > now()) else 'free'

def user_view(c,u):
    result = {k:u[k] for k in ('id','name','email','role','status','created','expires')}
    result['plan'] = effective(u)
    result['assigned_plan'] = u['plan']
    # One round trip for limits and usage (each query is a network call with Turso).
    row = c.execute('SELECT p.*,(SELECT COALESCE(SUM(size),0) FROM tracks WHERE user_id=?) AS used_storage,(SELECT COUNT(*) FROM playlists WHERE user_id=?) AS used_playlists FROM plans p WHERE p.name=?',(u['id'],u['id'],result['plan'])).fetchone()
    result['limits'] = {k:row[k] for k in row.keys() if not k.startswith('used_')}
    result['limits']['features'] = json.loads(result['limits']['features'])
    result['usage'] = {'storage':row['used_storage'], 'playlists':row['used_playlists']}
    return result

def audit(c,actor,target,action,reason,before,after):
    c.execute('INSERT INTO audit(actor,target,action,reason,before_state,after_state,created) VALUES(?,?,?,?,?,?,?)',(actor,target,action,reason,json.dumps(before),json.dumps(after),now()))

def create_admin_recovery(email,port=8765):
    token=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u=c.execute('SELECT * FROM users WHERE email=?',(email.strip().lower(),)).fetchone()
        require(u is not None,'La cuenta no está registrada.',404)
        c.execute('DELETE FROM admin_recovery WHERE user_id=? OR expires<=?',(u['id'],now()))
        c.execute('INSERT INTO admin_recovery VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],now()+600))
        audit(c,u['id'],u['id'],'admin_recovery_requested','Solicitud desde el equipo con acceso local a la base de datos',{'role':u['role']},{'expires_in_seconds':600})
    return f'http://127.0.0.1:{port}/recover#{token}'

class OndaHTTPServer(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True

class Handler(BaseHTTPRequestHandler):
    server_version = 'IVOX'
    def log_message(self, fmt, *args): pass
    def send(self,status,data,cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.headers_common('application/json; charset=utf-8')
        cookie=cookie or getattr(self,'renewed_cookie',None)
        if cookie: self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def headers_common(self,mime):
        self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; media-src 'self' blob:; img-src 'self' data: https:; frame-src 'none'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def body(self,limit=65536):
        size=int(self.headers.get('Content-Length','0'))
        require(0<size<=limit,'Tamaño de solicitud no permitido.',413)
        raw=self.rfile.read(size)
        self.unread_body=max(0,self.unread_body-len(raw))
        require(len(raw)==size,'La carga está incompleta.')
        return raw

    def session(self,c,write=False):
        cookie=SimpleCookie(); cookie.load(self.headers.get('Cookie',''))
        token=cookie.get('onda')
        hashed=hashlib.sha256((token.value if token else '').encode()).hexdigest()
        with session_guard: hit=SESSION_CACHE.get(hashed)
        if hit and hit[2]>time.monotonic() and hit[1]['expires']>now(): u,s=hit[:2]
        else:
            s=c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(hashed,now())).fetchone()
            require(s is not None,'Inicia sesión.',401)
            u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            s=dict(s)
            if s['expires']<now()+SESSION_SECONDS-86400:
                s['expires']=now()+SESSION_SECONDS
                c.execute('UPDATE sessions SET expires=? WHERE token=?',(s['expires'],hashed)); self.renewed_cookie=session_cookie(token.value)
            with session_guard: SESSION_CACHE[hashed]=(u,s,time.monotonic()+SESSION_CACHE_SECONDS)
        require(u['status']=='active','Esta cuenta está suspendida.',403)
        if write: require(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']),'Sesión inválida. Recarga la página.',403)
        return u,s

    def do_GET(self): self.dispatch()
    def do_POST(self): self.dispatch()
    def do_PATCH(self): self.dispatch()
    def do_DELETE(self): self.dispatch()

    def dispatch(self):
        self.unread_body=0;self.started=time.perf_counter();self.audio_video_id=None;self.audio_request_id=secrets.token_hex(8)
        try:
            self.connection.settimeout(20)
            self.unread_body=int(self.headers.get('Content-Length','0'))
            if self.command!='GET':
                origin=self.headers.get('Origin')
                if origin: require(urlparse(origin).netloc==self.headers.get('Host'),'Origen no permitido.',403)
            path=urlparse(self.path).path
            if not path.startswith('/api/'):
                if path=='/admin':
                    with db() as c:
                        try:u,_=self.session(c)
                        except Problem as e:
                            if e.status!=401:raise
                            self.send_response(303);self.headers_common('text/plain; charset=utf-8');self.send_header('Location','/?next=/admin');self.send_header('Content-Length','0');self.end_headers();return
                        require(u['role']=='admin','Acceso reservado a administradores.',403)
                require(self.command=='GET','Método no permitido.',405)
                if path=='/landing' or path.startswith('/landing/'):
                    asset=path.removeprefix('/landing').lstrip('/') or 'index.html'
                    landing_types={'index.html':'text/html; charset=utf-8','style.css':'text/css; charset=utf-8','script.js':'text/javascript','logo.svg':'image/svg+xml','loader.html':'text/html; charset=utf-8','loader.css':'text/css; charset=utf-8','loader.js':'text/javascript','taglines.json':'application/json','fonts/cinzel.woff2':'font/woff2','fonts/inter.woff2':'font/woff2','fonts/OFL.txt':'text/plain; charset=utf-8'}
                    require(asset in landing_types,'Archivo no encontrado.',404)
                    if path=='/landing':
                        self.send_response(302);self.headers_common('text/plain');self.send_header('Location','/landing/');self.send_header('Content-Length','0');self.end_headers();return
                    raw=(ROOT/'landing'/asset).read_bytes()
                    self.send_response(200);self.headers_common(landing_types[asset]);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                if path=='/.well-known/assetlinks.json':
                    # Digital Asset Links for the Android app (Bubblewrap/TWA): see docs/IVOX_EMPAQUETADO_NATIVO.md.
                    raw=os.environ.get('ANDROID_ASSETLINKS','').encode()
                    if not raw and (DATA/'assetlinks.json').exists(): raw=(DATA/'assetlinks.json').read_bytes()
                    require(raw,'No configurado.',404); json.loads(raw)
                    self.send_response(200);self.headers_common('application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                if path=='/youtube-setup':
                    self.send(410,{'error':'La reproducción actual usa Piped y Audius. Consulta README_SECCION_REPRODUCCION.md.'});return
                static_types={'player-controls.js':'text/javascript','app.js':'text/javascript','online.js':'text/javascript','sheet.js':'text/javascript','home.js':'text/javascript','offline.js':'text/javascript','crossfade.js':'text/javascript','sheet.css':'text/css','pwa.js':'text/javascript','recover.js':'text/javascript','recover.html':'text/html; charset=utf-8','service-worker.js':'text/javascript','style.css':'text/css','online.css':'text/css','player.css':'text/css','manifest.json':'application/manifest+json','icon-192x192.png':'image/png','icon-512x512.png':'image/png','silence.wav':'audio/wav','fonts/cinzel.woff2':'font/woff2','fonts/inter.woff2':'font/woff2','fonts/OFL.txt':'text/plain; charset=utf-8','index.html':'text/html; charset=utf-8'}
                filename='recover.html' if path=='/recover' else path[1:] if path[1:] in static_types else 'index.html'
                raw=(ROOT/'web'/filename).read_bytes()
                self.send_response(200); self.headers_common(static_types[filename])
                if filename=='service-worker.js': self.send_header('Service-Worker-Allowed','/')
                self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            with db() as c:
                # Frequent, self-contained writes skip the global write transaction (with Turso
                # that is several serialized round trips); their writes still commit as one batch.
                if self.command!='GET' and not LIGHT_WRITES.match(path): c.execute('BEGIN IMMEDIATE')
                self.api(c,path)
        except (Problem,ProviderError) as e:
            # Drain bounded rejected uploads so Windows does not reset the
            # socket before the client receives the actionable limit error.
            try:
                self.connection.settimeout(2)
                remaining=min(self.unread_body,512*MB)
                while remaining>0:
                    chunk=self.rfile.read(min(65536,remaining))
                    if not chunk: break
                    remaining-=len(chunk)
            except (OSError,TimeoutError): pass
            self.send(e.status,{'error':e.message,**({'code':e.code,'retry_after':e.retry_after} if isinstance(e,ProviderError) else {})})
        except (ValueError,KeyError,TypeError,json.JSONDecodeError): self.send(400,{'error':'Datos inválidos.'})
        except sqlite3.IntegrityError: self.send(409,{'error':'El registro ya existe o contiene referencias inválidas.'})
        except ConnectionError: pass
        except Exception:
            import traceback; traceback.print_exc(); self.send(500,{'error':'No se pudo completar la operación.'})
        finally:
            if TRACE and urlparse(self.path).path.startswith('/api/'): print(f'[trace] {self.command} {urlparse(self.path).path} {time.perf_counter()-self.started:.3f}s',flush=True)

    def api(self,c,path):
        method=self.command
        if path=='/api/recover-admin' and method=='POST':
            b=json.loads(self.body());token=b.get('token','');password=b.get('password','')
            require(isinstance(token,str) and re.fullmatch('[A-Za-z0-9_-]{43}',token),'El enlace no es válido o ha caducado.',403)
            recovery=c.execute('SELECT * FROM admin_recovery WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),now())).fetchone()
            require(recovery is not None,'El enlace no es válido, ya se utilizó o ha caducado.',403)
            require(isinstance(password,str) and 12<=len(password)<=256,'Usa una contraseña de 12 a 256 caracteres.')
            u=c.execute('SELECT * FROM users WHERE id=?',(recovery['user_id'],)).fetchone();uid=u['id']
            c.execute("UPDATE users SET password=?,role='admin',status='active' WHERE id=?",(password_hash(password),uid))
            c.execute('DELETE FROM admin_recovery WHERE user_id=?',(uid,))
            c.execute('DELETE FROM sessions WHERE user_id=?',(uid,)); forget_sessions()
            c.execute('DELETE FROM login_attempts WHERE key=?',(self.client_address[0],))
            audit(c,uid,uid,'admin_recovered','Recuperación con enlace local temporal de un solo uso',{'role':u['role'],'status':u['status']},{'role':'admin','status':'active','password_changed':True,'previous_sessions_revoked':True})
            session_token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(session_token.encode()).hexdigest(),uid,csrf,now()+SESSION_SECONDS));c.commit()
            self.send(200,{'redirect':'/admin'},session_cookie(session_token));return
        if path in ('/api/login','/api/register') and method=='POST':
            b=json.loads(self.body()); email=str(b.get('email','')).strip().lower(); password=b.get('password','')
            require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) and len(email)<=254,'Email inválido.')
            key=self.client_address[0]
            attempt=c.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
            require(not attempt or attempt['until']<now() or attempt['count']<10,'Demasiados intentos. Espera 15 minutos.',429)
            if path.endswith('register'):
                require(12<=len(password)<=256,'Usa una contraseña de 12 a 256 caracteres.')
                name=str(b.get('name','')).strip(); require(0<len(name)<=100,'Indica tu nombre (máximo 100 caracteres).')
                uid=secrets.token_hex(16)
                c.execute('INSERT INTO users(id,name,email,password,created) VALUES(?,?,?,?,?)',(uid,name,email,password_hash(password),now()))
                u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            else:
                u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                valid=password_ok(password,u['password'] if u else '0'*32+':'+'0'*128)
                if not u or not valid:
                    c.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN until<? THEN 1 ELSE count+1 END,until=?',(key,now()+900,now(),now()+900)); c.commit()
                    raise Problem(401,'Email o contraseña incorrectos.')
            require(u['status']=='active','Esta cuenta está suspendida.',403)
            c.execute('DELETE FROM login_attempts WHERE key=?',(key,))
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],csrf,now()+SESSION_SECONDS))
            c.commit()
            self.send(200,{'user':user_view(c,u),'csrf':csrf},session_cookie(token)); return
        u,s=self.session(c,method!='GET')
        uid=u['id']
        if path=='/api/me' and method=='GET': self.send(200,{'user':user_view(c,u),'csrf':s['csrf'],'plans': [dict(r) for r in c.execute('SELECT * FROM plans')]}); return
        if path=='/api/logout' and method=='POST':
            c.execute('DELETE FROM sessions WHERE token=?',(s['token'],)); c.commit(); forget_sessions(s['token']); self.send(200,{},'onda=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); return
        if path.startswith('/api/admin'):
            require(u['role']=='admin','Acceso reservado a administradores.',403)
            return self.admin(c,u,path,method)
        if path=='/api/audio/prefetch' and method=='POST':
            ids=json.loads(self.body()).get('video_ids')
            require(isinstance(ids,list) and len(ids)<=5 and all(isinstance(v,str) for v in ids),'Datos inválidos.')
            require(all(re.fullmatch('[A-Za-z0-9_-]{11}',v) for v in ids),'Referencia inválida.')
            # Only references this account can already discover; no arbitrary warming API.
            if ids:
                marks=','.join('?' for _ in ids)
                known={row[0] for row in c.execute('SELECT video_id FROM online_catalog WHERE video_id IN ('+marks+')',tuple('piped:'+v for v in ids))}
                ids=[v for v in ids if 'piped:'+v in known]
            resolver.prefetch_many(ids)
            self.send(202,{});return
        m=re.fullmatch(r'/api/audio/([A-Za-z0-9_-]{11})',path)
        if m and method=='GET':
            video_id=m[1]
            self.audio_video_id=video_id
            try:
                if parse_qs(urlparse(self.path).query).get('resolve')==['1']:
                    item=resolver.resolve(video_id)
                    self.send(200,{'url':'/api/audio/'+video_id,'source':'yt-dlp','level':1,'mime':item['mime']});return
                range_header=self.headers.get('Range')
                require(not range_header or re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)',range_header),'Rango inválido.',416)
                connection,response,mime=resolver.open(video_id,range_header)
            except AudioUnavailable as error:
                # The reason (e.g. upstream_403: YouTube refused this server's IP) is safe to show.
                reason=re.sub(r'[^A-Za-z0-9_]','',str(error))[:40]
                metric('audio_request_failed',request_id=self.audio_request_id,video_id=video_id,reason=reason,total_ms=round((time.perf_counter()-self.started)*1000,2))
                self.send(502,{'error':'audio_unavailable',**({'reason':reason} if reason else {})});return
            return self.relay(c,connection,response,mime,1,self.download_name())
        if path.startswith('/api/online/'):
            return self.online(c,u,path,method)
        if path=='/api/library' and method=='GET':
            tracks=[dict(r) for r in c.execute('SELECT id,title,size,favorite FROM tracks WHERE user_id=?',(uid,))]
            playlists={r['id']:dict(r,tracks=[]) for r in c.execute('SELECT * FROM playlists WHERE user_id=?',(uid,))}
            for r in c.execute('SELECT pt.playlist_id,pt.track_id FROM playlist_tracks pt JOIN playlists p ON p.id=pt.playlist_id WHERE p.user_id=?',(uid,)): playlists[r[0]]['tracks'].append(r[1])
            self.send(200,{'tracks':tracks,'playlists':list(playlists.values())}); return
        if path=='/api/tracks' and method=='POST':
            v=user_view(c,u); limit=v['limits']; size=int(self.headers.get('Content-Length','0'))
            require(0<size<=limit['file_size'],'El archivo supera el límite de tu plan. Usa un archivo más pequeño.',413)
            require(v['usage']['storage']+size<=limit['storage'],'Has alcanzado el almacenamiento de tu plan. Tu música se conserva; elimina archivos para importar otros.',409)
            raw=self.body(limit['file_size'])
            if raw.startswith(b'RIFF') and raw[8:12]==b'WAVE': mime='audio/wav'
            elif raw.startswith(b'fLaC'): mime='audio/flac'
            elif raw.startswith(b'OggS'): mime='audio/ogg'
            elif raw.startswith(b'ID3') or (len(raw)>2 and raw[0]==255 and raw[1]&224==224): mime='audio/mpeg'
            elif len(raw)>12 and raw[4:8]==b'ftyp': mime='audio/mp4'
            else: raise Problem(400,'Formato no reconocido. Usa MP3, WAV, FLAC, Ogg o M4A.')
            title=parse_qs(urlparse(self.path).query).get('name',['Sin título'])[0][:200]
            tid=secrets.token_hex(16); target=DATA/'audio'/tid
            try:
                target.write_bytes(raw)
                c.execute('INSERT INTO tracks(id,user_id,title,size,mime) VALUES(?,?,?,?,?)',(tid,uid,title,len(raw),mime)); c.commit()
            except Exception:
                target.unlink(missing_ok=True); raise
            self.send(201,{'id':tid}); return
        m=re.fullmatch(r'/api/tracks/([a-f0-9]{32})(/audio)?',path)
        if m:
            t=c.execute('SELECT * FROM tracks WHERE id=? AND user_id=?',(m[1],uid)).fetchone(); require(t is not None,'Canción no encontrada.',404)
            if m[2] and method=='GET': return self.stream(t)
            if method=='PATCH':
                b=json.loads(self.body()); require(type(b.get('favorite')) is bool,'Favorito inválido.')
                c.execute('UPDATE tracks SET favorite=? WHERE id=?',(int(b['favorite']),t['id']))
            elif method=='DELETE':
                c.execute('DELETE FROM tracks WHERE id=?',(t['id'],)); c.commit(); (DATA/'audio'/t['id']).unlink(missing_ok=True)
            else: raise Problem(405,'Método no permitido.')
            c.commit(); self.send(200,{}); return
        if path=='/api/playlists' and method=='POST':
            v=user_view(c,u); require(v['usage']['playlists']<v['limits']['playlists'],'Has alcanzado el máximo de playlists. Las existentes se conservan; elimina una para crear otra.',409)
            b=json.loads(self.body()); name=str(b.get('name','')).strip(); require(0<len(name)<=100,'Nombre de playlist inválido.')
            pid=secrets.token_hex(16); c.execute('INSERT INTO playlists VALUES(?,?,?)',(pid,uid,name)); c.commit(); self.send(201,{'id':pid}); return
        m=re.fullmatch(r'/api/playlists/([a-f0-9]{32})',path)
        if m:
            p=c.execute('SELECT * FROM playlists WHERE id=? AND user_id=?',(m[1],uid)).fetchone(); require(p is not None,'Playlist no encontrada.',404)
            if method=='DELETE': c.execute('DELETE FROM playlists WHERE id=?',(m[1],))
            elif method=='PATCH':
                b=json.loads(self.body())
                if 'name' in b:
                    name=str(b['name']).strip(); require(0<len(name)<=100,'Nombre inválido.'); c.execute('UPDATE playlists SET name=? WHERE id=?',(name,m[1]))
                if 'track_id' in b:
                    require(c.execute('SELECT 1 FROM tracks WHERE id=? AND user_id=?',(b['track_id'],uid)).fetchone(),'Canción no encontrada.',404)
                    if b.get('remove'): c.execute('DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?',(m[1],b['track_id']))
                    else: c.execute('INSERT OR IGNORE INTO playlist_tracks VALUES(?,?)',(m[1],b['track_id']))
            else: raise Problem(405,'Método no permitido.')
            c.commit(); self.send(200,{}); return
        raise Problem(404,'Ruta no encontrada.')

    def online(self,c,u,path,method):
        uid=u['id']
        if path=='/api/online/status' and method=='GET':
            self.send(200,{'configured':True,'source':'piped','piped':provider.piped_available(),'allow_youtube_external':os.environ.get('ALLOW_YOUTUBE_EXTERNAL')=='1'});return
        if path=='/api/online/search' and method=='GET':
            params=parse_qs(urlparse(self.path).query);q=' '.join(params.get('q',[''])[0].split());page=params.get('page',[''])[0]
            require(2<=len(q)<=120,'Escribe entre 2 y 120 caracteres para buscar.')
            require(len(page)<=24000 and (not page or re.fullmatch('[A-Za-z0-9_=-]+',page)),'Página inválida.')
            result=provider.search(c,uid,q,page,params.get('source',['auto'])[0],params.get('filter',['songs'])[0])
            resolver.prefetch_many([v['provider_id'] for v in result.get('items',[])[:5] if v.get('provider')=='piped'])
            c.commit();self.send(200,result);return
        if path=='/api/online/charts' and method=='GET':
            result=provider.charts(c);c.commit();self.send(200,result);return
        if path=='/api/online/suggest' and method=='GET':
            q=' '.join(parse_qs(urlparse(self.path).query).get('q',[''])[0].split());require(1<=len(q)<=80,'Búsqueda inválida.')
            result=provider.suggest(c,uid,q,parse_qs(urlparse(self.path).query).get('deep')==['1']);c.commit();self.send(200,result);return
        if path=='/api/online/artist' and method=='GET':
            q=' '.join(parse_qs(urlparse(self.path).query).get('q',[''])[0].split());require(2<=len(q)<=120,'Búsqueda inválida.')
            artist=provider.artist_lookup(c,uid,q);c.commit();self.send(200,{'artist':artist});return
        m=re.fullmatch(r'/api/online/artist/(UC[A-Za-z0-9_-]{22})',path)
        if m and method=='GET':
            artist=provider.artist_page(c,uid,m[1]);c.commit();self.send(200,{'artist':artist});return
        m=re.fullmatch(r'/api/online/(radio|lyrics)/((?:piped|audius):[A-Za-z0-9_-]+)',path)
        if m and method=='GET':
            require(valid_id(m[2]),'Referencia inválida.')
            if m[1]=='lyrics':self.send(200,provider.lyrics(uid,m[2]));return
            items=provider.radio(c,uid,m[2]);c.commit();self.send(200,{'items':items});return
        m=re.fullmatch(r'/api/online/play/((?:piped|audius):[A-Za-z0-9_-]+)',path)
        if m and method=='GET':
            require(valid_id(m[1]),'Referencia inválida.')
            require(c.execute('SELECT 1 FROM online_catalog WHERE video_id=?',(m[1],)).fetchone(),'Busca de nuevo la canción para actualizarla.',409)
            preferred=parse_qs(urlparse(self.path).query).get('format',['mp4'])[0]
            minimum=3 if parse_qs(urlparse(self.path).query).get('minimum_level')==['3'] else 2
            result=provider.stream(c,uid,m[1],preferred,minimum);c.commit();self.send(200,result);return
        m=re.fullmatch(r'/api/online/media/([A-Za-z0-9_-]{40,60})',path)
        if m and method=='GET':
            range_header=self.headers.get('Range')
            require(not range_header or re.fullmatch(r'bytes=\d*-\d*',range_header),'Rango inválido.',416)
            connection,response=provider.media(uid,m[1],{'Range':range_header} if range_header else {})
            return self.relay(c,connection,response,response.getheader('Content-Type','audio/mpeg'),provider.tickets[m[1]].get('level',4),self.download_name())
        m=re.fullmatch(r'/api/online/art/(piped:[A-Za-z0-9_-]{11})',path)
        if m and method=='GET':
            row=c.execute('SELECT metadata FROM online_catalog WHERE video_id=?',(m[1],)).fetchone();require(row,'Portada no encontrada.',404)
            target=json.loads(row['metadata']).get('art_url','')
            require(target.startswith(provider.proxy+'/'),'Portada no disponible.',404)
            connection,response=get_response(target,timeout=10,local=True)
            try:
                require(response.status==200 and response.getheader('Content-Type','').startswith('image/'),'Portada no disponible.',404)
                raw=response.read(2*MB);self.send_response(200);self.headers_common(response.getheader('Content-Type'));self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            finally:connection.close()
            return
        if path=='/api/online/recent' and method=='POST':
            b=json.loads(self.body());q=' '.join(str(b.get('query','')).split());require(2<=len(q)<=120,'Búsqueda inválida.')
            c.execute('DELETE FROM recent_searches WHERE user_id=? AND lower(query)=lower(?)',(uid,q))
            c.execute('INSERT INTO recent_searches VALUES(?,?,?)',(uid,q,now()))
            c.execute('DELETE FROM recent_searches WHERE user_id=? AND query NOT IN (SELECT query FROM recent_searches WHERE user_id=? ORDER BY created DESC,rowid DESC LIMIT 8)',(uid,uid));c.commit();self.send(200,{});return
        if path=='/api/online/recent' and method=='DELETE':
            c.execute('DELETE FROM recent_searches WHERE user_id=?',(uid,));c.commit();self.send(200,{});return
        if path=='/api/online/library' and method=='GET':
            # Stale catalogue rows are pruned at startup; this read stays a handful of round trips.
            saves=[dict(r) for r in c.execute('SELECT * FROM online_saves WHERE user_id=? ORDER BY COALESCE(last_played,0) DESC',(uid,))]
            playlists={row['id']:dict(row,tracks=[]) for row in c.execute('SELECT id,name FROM playlists WHERE user_id=? ORDER BY rowid DESC',(uid,))}
            for row in c.execute('SELECT t.playlist_id,t.video_id FROM online_playlist_tracks t JOIN playlists p ON p.id=t.playlist_id WHERE p.user_id=? ORDER BY t.added,t.rowid',(uid,)): playlists[row[0]]['tracks'].append(row[1])
            playlists=list(playlists.values());ids=[s['video_id'] for s in saves]+[v for p in playlists for v in p['tracks']]
            metadata=provider.metadata(c,ids)
            self.send(200,{'saves':saves,'playlists':playlists,'items':metadata,'recent':[r[0] for r in c.execute('SELECT query FROM recent_searches WHERE user_id=? ORDER BY created DESC,rowid DESC',(uid,))]});return
        if path=='/api/online/library' and method=='DELETE':
            c.execute('DELETE FROM online_playlist_tracks WHERE playlist_id IN (SELECT id FROM playlists WHERE user_id=?)',(uid,))
            c.execute('DELETE FROM online_saves WHERE user_id=?',(uid,));c.execute('DELETE FROM recent_searches WHERE user_id=?',(uid,));c.commit();self.send(200,{});return
        if path=='/api/online/history' and method=='DELETE':
            c.execute('UPDATE online_saves SET last_played=NULL WHERE user_id=?',(uid,))
            c.execute('DELETE FROM online_saves WHERE user_id=? AND favorite=0 AND later=0',(uid,));c.commit();self.send(200,{});return
        m=re.fullmatch(r'/api/online/items/((?:piped|audius):[A-Za-z0-9_-]+)',path)
        if m and method=='PATCH':
            vid=m[1];require(valid_id(vid),'Referencia inválida.');b=json.loads(self.body());require(set(b)<= {'favorite','later','played'} and bool(b),'Acción inválida.')
            for value in b.values(): require(type(value) is bool,'Valor inválido.')
            # Only ids obtained from the official provider can be added. Metadata
            # submitted by the browser is never trusted or stored.
            adding=any(b.values())
            if adding: require(c.execute('SELECT 1 FROM online_catalog WHERE video_id=? AND refreshed>?',(vid,now()-30*86400)).fetchone(),'Busca de nuevo el vídeo para actualizar su disponibilidad.',409)
            c.execute('INSERT OR IGNORE INTO online_saves(user_id,video_id) VALUES(?,?)',(uid,vid))
            for key in ('favorite','later'):
                if key in b:c.execute('UPDATE online_saves SET '+key+'=? WHERE user_id=? AND video_id=?',(int(b[key]),uid,vid))
            if b.get('played'): c.execute('UPDATE online_saves SET last_played=? WHERE user_id=? AND video_id=?',(now(),uid,vid))
            c.execute('DELETE FROM online_saves WHERE user_id=? AND favorite=0 AND later=0 AND last_played IS NULL',(uid,))
            # The real length lets the player stop at the song's end when it had no catalogue length.
            c.commit();self.send(200,{'duration':resolver.duration(vid.split(':',1)[1])} if b.get('played') and vid.startswith('piped:') else {});return
        m=re.fullmatch(r'/api/online/playlists/([a-f0-9]{32})/items',path)
        if m and method in ('POST','DELETE'):
            require(c.execute('SELECT 1 FROM playlists WHERE id=? AND user_id=?',(m[1],uid)).fetchone(),'Playlist no encontrada.',404)
            b=json.loads(self.body());vid=b.get('video_id','');require(valid_id(vid),'Referencia inválida.')
            if method=='POST':
                require(c.execute('SELECT 1 FROM online_catalog WHERE video_id=? AND refreshed>?',(vid,now()-30*86400)).fetchone(),'Busca de nuevo el vídeo para actualizarlo.',409)
                c.execute('INSERT OR IGNORE INTO online_playlist_tracks VALUES(?,?,?)',(m[1],vid,now()))
            else:c.execute('DELETE FROM online_playlist_tracks WHERE playlist_id=? AND video_id=?',(m[1],vid))
            c.commit();self.send(200,{});return
        raise Problem(404,'Ruta no encontrada.')

    def download_name(self):
        # ?download=<title> turns a stream into a file save; the name is only a label.
        query=parse_qs(urlparse(self.path).query)
        if 'download' not in query:return None
        name=re.sub(r'[\x00-\x1f\\/:*?"<>|]+',' ',query['download'][0]).strip()[:120]
        return name or 'IVOX'

    def relay(self,c,connection,response,mime,level,download=None):
        sent=0;first_byte_ms=None;outcome='complete';phase='headers'
        timings=getattr(response,'ivox_timings',{})
        try:
            self.send_response(response.status);self.headers_common(mime)
            self.send_header('X-IVOX-Audio-Level',str(level))
            self.send_header('Cache-Control','private, no-store')
            self.send_header('X-Accel-Buffering','no')
            self.send_header('X-IVOX-Audio-Cache','prefix' if timings.get('prefix_hit') else 'passthrough')
            self.send_header('Server-Timing',', '.join(f'{key};dur={value}' for key,value in timings.items() if key.endswith('_ms')) or 'relay;dur=0')
            if download:
                extension={'audio/mp4':'m4a','audio/webm':'webm','audio/mpeg':'mp3','audio/ogg':'ogg'}.get(mime.split(';')[0].strip(),'audio')
                self.send_header('Content-Disposition',f"attachment; filename=\"IVOX.{extension}\"; filename*=UTF-8''{quote(download+'.'+extension)}")
            for header in ('Content-Length','Content-Range'):
                value=response.getheader(header)
                if value:self.send_header(header,value)
            # Safari only seeks (and trusts the length) when byte ranges are advertised.
            accepts=response.getheader('Accept-Ranges')
            if accepts:self.send_header('Accept-Ranges',accepts)
            elif response.status==206:self.send_header('Accept-Ranges','bytes')
            self.end_headers()
            if TRACE: print(f'[trace] audio headers {self.headers.get("Range")} {time.perf_counter()-self.started:.3f}s',flush=True)
            while True:
                try:self.session(c)
                except Problem:outcome='session_revoked';self.close_connection=True;break
                # HTTPResponse.read(n) waits for n bytes. read1 returns available
                # bytes immediately, so slow upstreams cannot delay first sound
                # merely to fill a 64 KiB server buffer.
                reader=getattr(response,'read1',response.read)
                phase='upstream_read'
                chunk=reader(16384 if sent==0 else 65536)
                if not chunk:
                    expected=response.getheader('Content-Length')
                    if expected and expected.isdigit() and sent<int(expected):raise http.client.IncompleteRead(b'')
                    break
                phase='client_write'
                self.wfile.write(chunk)
                if sent==0:
                    self.wfile.flush();first_byte_ms=round((time.perf_counter()-self.started)*1000,2)
                    metric('audio_first_byte',request_id=self.audio_request_id,video_id=getattr(self,'audio_video_id',None),first_byte_ms=first_byte_ms,**timings)
                sent+=len(chunk)
        except (OSError,http.client.HTTPException):
            self.close_connection=True;outcome=phase+'_error'
            # A failed prefix continuation must not be served again on the
            # next recovery request. Client skips/disconnects keep valid cache.
            if phase=='upstream_read' and level==1 and self.audio_video_id:
                resolver.invalidate(self.audio_video_id)
        finally:
            connection.close()
            metric('audio_complete',request_id=self.audio_request_id,video_id=getattr(self,'audio_video_id',None),first_byte_ms=first_byte_ms,total_ms=round((time.perf_counter()-self.started)*1000,2),bytes=sent,outcome=outcome,**timings)

    def admin(self,c,u,path,method):
        if path=='/api/admin/users' and method=='GET':
            q=parse_qs(urlparse(self.path).query).get('q',[''])[0].lower()
            users=[user_view(c,r) for r in c.execute('SELECT * FROM users ORDER BY created DESC') if q in r['name'].lower() or q in r['email'].lower()]
            self.send(200,{'users':users,'summary':{'users':c.execute('SELECT COUNT(*) FROM users').fetchone()[0],'premium':c.execute("SELECT COUNT(*) FROM users WHERE plan='premium' AND (expires IS NULL OR expires>?)",(now(),)).fetchone()[0],'storage':c.execute('SELECT COALESCE(SUM(size),0) FROM tracks').fetchone()[0]}}); return
        if path=='/api/admin/plans':
            if method=='GET': self.send(200,[dict(r) for r in c.execute('SELECT * FROM plans')]); return
            require(method=='PATCH','Método no permitido.',405); b=json.loads(self.body()); require(b['name'] in ('free','premium'),'Plan inválido.')
            for key in ('storage','playlists','file_size'): require(type(b[key]) is int and 0<b[key]<=10**13,'Límite inválido.')
            require(b['file_size']<=b['storage'],'El límite por archivo no puede superar el almacenamiento.')
            features=b.get('features',{}); require(isinstance(features,dict) and len(features)<=50 and all(isinstance(k,str) and len(k)<=80 and type(v) is bool for k,v in features.items()),'Funciones inválidas.')
            before=dict(c.execute('SELECT * FROM plans WHERE name=?',(b['name'],)).fetchone())
            c.execute('UPDATE plans SET storage=?,playlists=?,file_size=?,features=? WHERE name=?',(b['storage'],b['playlists'],b['file_size'],json.dumps(features),b['name']))
            audit(c,u['id'],None,'limits',str(b.get('reason',''))[:500],before,b); c.commit(); self.send(200,{}); return
        if path=='/api/admin/audit' and method=='GET':
            rows=c.execute('SELECT a.*,u.email actor_email,t.email target_email FROM audit a LEFT JOIN users u ON u.id=a.actor LEFT JOIN users t ON t.id=a.target ORDER BY a.id DESC LIMIT 200')
            self.send(200,[dict(r) for r in rows]); return
        m=re.fullmatch(r'/api/admin/users/([a-f0-9]{32})',path)
        require(m is not None,'Ruta no encontrada.',404)
        t=c.execute('SELECT * FROM users WHERE id=?',(m[1],)).fetchone(); require(t is not None,'Usuario no encontrado.',404)
        if method=='GET': self.send(200,user_view(c,t)); return
        require(method=='PATCH','Método no permitido.',405)
        b=json.loads(self.body()); action=b['action']; reason=str(b.get('reason',''))[:500]
        # Admins may upgrade their own plan, but never demote, suspend or re-role
        # themselves: those could lock the last administrator out.
        require(t['id']!=u['id'] or action in ('grant','trial','extend'),'No puedes quitarte Premium ni cambiar tu propio rol o estado.',403)
        before={k:t[k] for k in ('role','status','plan','expires')}; after=before.copy()
        if action in ('grant','trial','extend'):
            after['plan']='premium'
            if action=='grant':
                expiry=b.get('expires'); require(expiry is None or (type(expiry) is int and now()<expiry<=253402300799),'El vencimiento debe ser una fecha futura.'); after['expires']=expiry
            else:
                days=b.get('days'); require(type(days) is int and 1<=days<=3650,'Indica entre 1 y 3650 días.')
                require(action!='extend' or effective(t)=='premium','La cuenta no tiene Premium activo.')
                require(action!='extend' or t['expires'] is not None,'Premium indefinido no necesita ampliación.')
                after['expires']=(max(now(),t['expires'] or now()) if action=='extend' else now())+days*86400
        elif action=='revoke': after.update(plan='free',expires=None)
        elif action=='suspend': after['status']='suspended'
        elif action=='activate': after['status']='active'
        elif action=='role':
            require(b.get('role') in ('admin','user'),'Rol inválido.'); after['role']=b['role']
        else: raise Problem(400,'Acción inválida.')
        if action in ('revoke','suspend'): require(b.get('confirm') is True,'Confirma esta acción.')
        if t['role']=='admin' and t['status']=='active' and (after['role']!='admin' or after['status']!='active'):
            require(c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0]>1,'Debe quedar un administrador activo.',409)
        c.execute('UPDATE users SET role=?,status=?,plan=?,expires=? WHERE id=?',(after['role'],after['status'],after['plan'],after['expires'],t['id'])); forget_sessions()
        audit(c,u['id'],t['id'],action,reason,before,after); c.commit(); self.send(200,user_view(c,c.execute('SELECT * FROM users WHERE id=?',(t['id'],)).fetchone()))

    def stream(self,t):
        size=t['size']; start=0; end=size-1; status=200
        range_header=self.headers.get('Range')
        if range_header:
            m=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            if not m or not any(m.groups()): self.send(416,{'error':'Rango inválido.'}); return
            if m[1]: start=int(m[1]); end=min(int(m[2]),size-1) if m[2] else size-1
            else: start=max(0,size-int(m[2]))
            if start>end or start>=size: self.send(416,{'error':'Rango fuera del archivo.'}); return
            status=206
        self.send_response(status); self.headers_common(t['mime']); self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1))
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        with (DATA/'audio'/t['id']).open('rb') as f:
            f.seek(start); remaining=end-start+1
            while remaining:
                chunk=f.read(min(65536,remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining-=len(chunk)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['serve','bootstrap-admin','recover-admin'],nargs='?',default='serve'); parser.add_argument('--email'); parser.add_argument('--port',type=int,default=8765); args=parser.parse_args(); init()
    if args.command=='recover-admin':
        require(args.email is not None,'Indica --email.')
        print(create_admin_recovery(args.email,args.port))
        print('Enlace privado de un solo uso. Caduca en 10 minutos. Ábrelo en este ordenador y elige una contraseña nueva.')
    elif args.command=='bootstrap-admin':
        require(args.email is not None,'Indica --email.')
        # Never hold a write lock while waiting for interactive input.
        with db() as c:
            u=c.execute('SELECT * FROM users WHERE email=?',(args.email.lower(),)).fetchone(); require(u is not None,'Primero registra la cuenta en IVOX.')
        verified_password=getpass.getpass('Contraseña de la cuenta: ')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            require(c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0]==0,'Ya existe un administrador activo. Usa el panel.')
            u=c.execute('SELECT * FROM users WHERE email=?',(args.email.lower(),)).fetchone(); require(u is not None,'Primero registra la cuenta en IVOX.')
            require(password_ok(verified_password,u['password']),'Contraseña incorrecta.',403)
            c.execute("UPDATE users SET role='admin',status='active' WHERE id=?",(u['id'],)); audit(c,u['id'],u['id'],'bootstrap','Verificación local de contraseña',{'role':u['role']},{'role':'admin'}); c.commit()
        print('Administrador configurado. Recarga IVOX.')
    else:
        # Warm pools: yt-dlp workers resolve one known video so the player JS is cached before the first play.
        resolver.start(warm='jNQXAC9IVRw'); provider.music.start()
        if TURSO:
            # A cheap ping keeps one pooled TLS connection to Turso alive, so a request
            # after a quiet minute (the first Play, opening the library) never pays a handshake.
            def keep_warm():
                while True: TURSO.warm(); time.sleep(12)
            threading.Thread(target=keep_warm,daemon=True).start()
        print(f'IVOX: escuchando en 0.0.0.0:{args.port} · acceso local http://127.0.0.1:{args.port}'); OndaHTTPServer(('0.0.0.0',args.port),Handler).serve_forever()

if __name__=='__main__': main()
