import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

temporary=tempfile.TemporaryDirectory()
os.environ['ONDA_DATA']=temporary.name
os.environ['TURSO_DATABASE_URL']=''  # tests never touch the cloud database
spec=importlib.util.spec_from_file_location('onda',Path(__file__).resolve().parents[1]/'server.py')
onda=importlib.util.module_from_spec(spec); spec.loader.exec_module(onda)

class Client:
    def __init__(self,port): self.port=port; self.cookie=''; self.csrf=''
    def request(self,path,method='GET',data=None,extra=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port)
        headers={'Cookie':self.cookie,'X-CSRF-Token':self.csrf}
        headers.update(extra or {})
        raw=data if isinstance(data,bytes) else json.dumps(data).encode() if data is not None else None
        conn.request(method,path,raw,headers); r=conn.getresponse(); body=r.read()
        if r.getheader('Set-Cookie'): self.cookie=r.getheader('Set-Cookie').split(';')[0]
        try: result=json.loads(body)
        except (ValueError,UnicodeDecodeError): result=body
        if isinstance(result,dict) and 'csrf' in result:self.csrf=result['csrf']
        status=r.status; conn.close(); return status,result

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        onda.init(); cls.server=onda.ThreadingHTTPServer(('127.0.0.1',0),onda.Handler)
        cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();temporary.cleanup()
    def setUp(self):
        with onda.db() as c:
            for table in ['admin_recovery','online_playlist_tracks','online_saves','recent_searches','online_catalog','playlist_tracks','playlists','tracks','audit','sessions','users','login_attempts']:c.execute('DELETE FROM '+table)
            c.execute("UPDATE plans SET storage=1048576,playlists=1,file_size=524288,features='{}' WHERE name='free'")
        self.admin=self.register('admin');self.user=self.register('user');self.other=self.register('other')
        with onda.db() as c:c.execute("UPDATE users SET role='admin' WHERE email='admin@example.com'")
        self.uid=self.user.request('/api/me')[1]['user']['id'];self.aid=self.admin.request('/api/me')[1]['user']['id']
    def register(self,name):
        client=Client(self.port); status,b=client.request('/api/register','POST',{'name':name,'email':name+'@example.com','password':'long-private-password'})
        self.assertEqual(status,200,b); return client
    def change(self,action,**kw):return self.admin.request('/api/admin/users/'+self.uid,'PATCH',dict(action=action,**kw))
    def upload(self,client=None,size=128):return (client or self.user).request('/api/tracks?name=test.wav','POST',b'RIFF'+b'\x00'*4+b'WAVE'+b'\x00'*(size-12))
    def test_access_and_csrf(self):
        self.assertEqual(Client(self.port).request('/admin')[0],303)
        self.assertEqual(Client(self.port).request('/api/library')[0],401)
        self.assertEqual(self.user.request('/admin')[0],403)
        self.assertEqual(self.user.request('/api/admin/users')[0],403)
        self.assertEqual(self.user.request('/api/admin/users/'+self.uid,'PATCH',{'action':'grant'})[0],403)
        self.assertEqual(self.admin.request('/api/admin/users/'+self.aid,'PATCH',{'action':'revoke','confirm':True})[0],403)
        status,me=self.admin.request('/api/admin/users/'+self.aid,'PATCH',{'action':'grant','expires':None})
        self.assertEqual((status,me['plan'],me['role']),(200,'premium','admin'))
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'X'},{'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'X'},{'Origin':'https://evil.example'})[0],403)
    def test_premium_lifecycle_open_sessions(self):
        self.assertEqual(self.change('grant')[0],200)
        self.assertEqual(self.user.request('/api/me')[1]['user']['plan'],'premium')
        self.assertEqual(self.user.request('/api/admin/users')[0],403)
        expiry=int(time.time())+86400
        self.assertEqual(self.change('grant',expires=expiry)[0],200)
        self.assertEqual(self.change('extend',days=2)[1]['expires'],expiry+172800)
        self.assertEqual(self.change('trial',days=7)[0],200)
        with onda.db() as c:c.execute('UPDATE users SET expires=? WHERE id=?',(int(time.time())-1,self.uid))
        self.assertEqual(self.user.request('/api/me')[1]['user']['plan'],'free')
        self.assertEqual(self.change('grant',expires=int(time.time())-1)[0],400)
        self.assertEqual(self.change('grant')[0],200)
        self.assertEqual(self.change('revoke')[0],400)
        self.assertEqual(self.change('revoke',confirm=True,reason='Fin de prueba')[0],200)
        self.assertEqual(self.user.request('/api/me')[1]['user']['plan'],'free')
        audit=self.admin.request('/api/admin/audit')[1]
        self.assertEqual(audit[0]['reason'],'Fin de prueba');self.assertEqual(audit[0]['actor'],self.aid)
    def test_suspension_and_last_admin(self):
        self.assertEqual(self.change('suspend')[0],400)
        self.assertEqual(self.change('suspend',confirm=True)[0],200)
        self.assertEqual(self.user.request('/api/me')[0],403)
        self.assertEqual(self.change('activate')[0],200)
        self.assertEqual(self.user.request('/api/me')[0],200)
        self.assertEqual(self.admin.request('/api/admin/users/'+self.aid,'PATCH',{'action':'suspend','confirm':True})[0],403)
        self.assertEqual(self.change('role',role='admin')[0],200)
        self.assertEqual(self.user.request('/api/admin/users')[0],200)
        self.assertEqual(self.user.request('/api/admin/users/'+self.aid,'PATCH',{'action':'role','role':'user'})[0],200)
        self.assertEqual(self.admin.request('/api/admin/users')[0],403)
        self.assertEqual(self.user.request('/api/admin/users/'+self.uid,'PATCH',{'action':'role','role':'user'})[0],403)
    def test_isolation_streaming_favorites_playlists(self):
        code,b=self.upload();self.assertEqual(code,201,b);tid=b['id']
        self.assertEqual(self.other.request('/api/library')[1]['tracks'],[])
        self.assertEqual(self.other.request('/api/tracks/'+tid+'/audio')[0],404)
        self.assertEqual(self.other.request('/api/tracks/'+tid,'DELETE')[0],404)
        self.assertEqual(self.user.request('/api/tracks/'+tid,'PATCH',{'favorite':True})[0],200)
        _,p=self.user.request('/api/playlists','POST',{'name':'Private'})
        self.assertEqual(self.other.request('/api/playlists/'+p['id'],'PATCH',{'track_id':tid})[0],404)
        _,p2=self.other.request('/api/playlists','POST',{'name':'Other'})
        self.assertEqual(self.other.request('/api/playlists/'+p2['id'],'PATCH',{'track_id':tid})[0],404)
        self.assertEqual(self.user.request('/api/playlists/'+p['id'],'PATCH',{'track_id':tid})[0],200)
        status,raw=self.user.request('/api/tracks/'+tid+'/audio',extra={'Range':'bytes=0-3'})
        self.assertEqual(status,206);self.assertEqual(raw,b'RIFF')
        self.assertEqual(self.user.request('/api/tracks/'+tid+'/audio',extra={'Range':'bytes=999-1000'})[0],416)
        self.assertEqual(Client(self.port).request('/api/tracks/'+tid+'/audio')[0],401)
        self.assertEqual(self.change('suspend',confirm=True)[0],200)
        self.assertEqual(self.user.request('/api/tracks/'+tid+'/audio')[0],403)
    def test_limits_downgrade_preserves_content(self):
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'One'})[0],201)
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'Two'})[0],409)
        self.assertEqual(self.upload(size=524289)[0],413)
        self.change('grant')
        self.assertEqual(self.upload(size=1048577)[0],201)
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'Two'})[0],201)
        self.change('revoke',confirm=True)
        self.assertEqual(len(self.user.request('/api/library')[1]['tracks']),1)
        self.assertEqual(len(self.user.request('/api/library')[1]['playlists']),2)
        self.assertEqual(self.upload()[0],409)
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'Three'})[0],409)
        b={'name':'free','storage':10*1048576,'file_size':2*1048576,'playlists':3,'features':{'future':False},'reason':'Más espacio'}
        self.assertEqual(self.admin.request('/api/admin/plans','PATCH',b)[0],200)
        self.assertEqual(self.upload()[0],201)
        self.assertEqual(self.user.request('/api/playlists','POST',{'name':'Three'})[0],201)
        self.assertEqual(self.user.request('/api/me')[1]['user']['limits']['features'],{'future':False})
    def test_persistence_and_real_summary(self):
        self.upload(size=256);self.change('grant')
        summary=self.admin.request('/api/admin/users')[1]['summary']
        self.assertEqual(summary,{'users':3,'premium':1,'storage':256})
        search=self.admin.request('/api/admin/users?q=user%40')[1]['users'];self.assertEqual(len(search),1)
        second=Client(self.port)
        self.assertEqual(second.request('/api/login','POST',{'email':'user@example.com','password':'long-private-password'})[0],200)
        self.assertEqual(len(second.request('/api/library')[1]['tracks']),1)
        self.assertEqual(second.request('/api/me')[1]['user']['plan'],'premium')

if __name__=='__main__':unittest.main()
