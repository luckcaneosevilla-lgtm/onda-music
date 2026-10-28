import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_security import onda,Client
from music_provider import MusicProvider
from youtube_provider import ProviderError

VID='piped:testVideo01'
PIPED={'items':[{'type':'stream','url':'/watch?v=testVideo01','title':'Fixture de prueba','uploaderName':'Canal de prueba','duration':201,'thumbnail':'http://127.0.0.1:8081/fixture.jpg?host=i.ytimg.com'}],'nextpage':'{"page":"second"}'}
AUDIUS={'data':[{'id':'AbCd1','title':'Fixture independiente','duration':180,'is_streamable':True,'is_available':True,'user':{'name':'Artista de prueba'},'artwork':{}}]}

class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.original=onda.DATA;onda.DATA=Path(self.temp.name);onda.TURSO=None;onda.init()
        self.server=onda.ThreadingHTTPServer(('127.0.0.1',0),onda.Handler);self.port=self.server.server_port
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.client=self.register('one');self.other=self.register('two');self.old_provider=onda.provider;onda.provider=MusicProvider()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();onda.provider=self.old_provider;onda.DATA=self.original;self.temp.cleanup()
    def register(self,name):
        c=Client(self.port);status,r=c.request('/api/register','POST',{'name':name,'email':name+'@test.com','password':'private-test-password'});self.assertEqual(status,200,r);return c
    def search(self):
        with patch.object(onda.provider,'request',return_value=PIPED) as request:
            result=self.client.request('/api/online/search?q=Feid&source=piped');count=request.call_count
        return result,count
    def test_piped_search_cache_and_signed_pagination(self):
        (status,r),count=self.search();self.assertEqual(status,200,r);self.assertEqual(count,1);self.assertEqual(r['items'][0]['duration'],201);self.assertEqual(r['items'][0]['id'],VID)
        (_,cached),count=self.search();self.assertTrue(cached['cached']);self.assertEqual(count,0)
        page=r['next_page']
        with patch.object(onda.provider,'request',return_value={'items':[]}) as request:
            self.assertEqual(self.client.request('/api/online/search?q=Feid&source=piped&page='+page)[0],200)
            self.assertEqual(request.call_args.args[1],'/nextpage/search');self.assertEqual(request.call_args.args[2]['nextpage'],PIPED['nextpage'])
        self.assertEqual(self.client.request('/api/online/search?q=Other&source=piped&page='+page)[0],400)
        self.assertEqual(Client(self.port).request('/api/online/search?q=Feid')[0],401)
    def test_explicit_audius_and_automatic_fallback(self):
        def response(source,*args):
            if source=='piped':raise ProviderError(502,'provider_unavailable','Piped no disponible')
            return AUDIUS
        with patch.object(onda.provider,'music_search',side_effect=ProviderError(502,'ytmusic_unavailable','Fixture')),patch.object(onda.provider,'youtube_search',side_effect=ProviderError(502,'provider_unavailable','Fixture')),patch.object(onda.provider,'request',side_effect=response):
            code,r=self.client.request('/api/online/search?q=Feid');self.assertEqual(code,200,r);self.assertEqual(r['source'],'audius');self.assertTrue(r['fallback']);self.assertIn('Audius',r['notice'])
        self.assertEqual(r['items'][0]['id'],'audius:AbCd1')
        with patch.object(onda.provider,'music_search',side_effect=ProviderError(502,'ytmusic_unavailable','Fixture')),patch.object(onda.provider,'youtube_search',side_effect=ProviderError(502,'provider_unavailable','Fixture')),patch.object(onda.provider,'request',side_effect=ProviderError(502,'provider_unavailable','Ambas fuentes fallan')):
            code,r=self.client.request('/api/online/search?q=Another');self.assertEqual(code,502);self.assertNotIn('items',r)
    def test_private_references_sync_history_and_delete(self):
        self.search();self.assertEqual(self.client.request('/api/online/items/'+VID,'PATCH',{'favorite':True,'later':True,'played':True})[0],200)
        _,p=self.client.request('/api/playlists','POST',{'name':'Online'});path='/api/online/playlists/'+p['id']+'/items'
        self.assertEqual(self.client.request(path,'POST',{'video_id':VID})[0],200);self.assertEqual(self.other.request(path,'POST',{'video_id':VID})[0],404);self.assertEqual(self.other.request(path,'DELETE',{'video_id':VID})[0],404)
        self.assertEqual(self.other.request('/api/online/library')[1]['saves'],[])
        second=Client(self.port);second.request('/api/login','POST',{'email':'one@test.com','password':'private-test-password'})
        lib=second.request('/api/online/library')[1];self.assertEqual(lib['playlists'][0]['tracks'],[VID]);self.assertEqual(lib['saves'][0]['favorite'],1);self.assertEqual(lib['saves'][0]['later'],1);self.assertIsNotNone(lib['saves'][0]['last_played'])
        self.assertEqual(second.request('/api/me')[1]['user']['plan'],'free')
        second.request('/api/online/history','DELETE');self.assertIsNone(second.request('/api/online/library')[1]['saves'][0]['last_played'])
        second.request('/api/online/library','DELETE');self.assertEqual(second.request('/api/online/library')[1]['saves'],[]);self.assertEqual(second.request('/api/online/library')[1]['playlists'][0]['tracks'],[])
        self.assertEqual(self.client.request('/api/online/items/piped:unknown0000','PATCH',{'favorite':True})[0],409)
    def test_stream_selection_no_audio_and_protected_tickets(self):
        self.search()
        data={'id':'testVideo01','title':'Fixture','uploader':'Canal','audioStreams':[{'mimeType':'audio/webm','bitrate':250000,'url':'http://127.0.0.1:8081/webm'},{'mimeType':'audio/mp4','bitrate':128000,'url':'http://127.0.0.1:8081/mp4'},{'mimeType':'audio/mp4','bitrate':192000,'url':'http://127.0.0.1:8081/mp4-high'}]}
        with patch.object(onda.provider,'request',return_value=data):
            code,r=self.client.request('/api/online/play/'+VID);self.assertEqual(code,200,r)
        token=r['url'].rsplit('/',1)[-1];self.assertTrue(onda.provider.tickets[token]['url'].endswith('/mp4-high'))
        self.assertEqual(self.other.request(r['url'])[0],404);self.assertEqual(Client(self.port).request(r['url'])[0],401)
        onda.provider.tickets[token]['expires']=0;self.assertEqual(self.client.request(r['url'])[0],404)
        with patch.object(onda.provider,'request',return_value={'audioStreams':[]}):self.assertEqual(self.client.request('/api/online/play/'+VID)[0],422)
        data['audioStreams']=[{'mimeType':'audio/mp4','url':'http://169.254.169.254/metadata'}]
        with patch.object(onda.provider,'request',return_value=data):self.assertEqual(self.client.request('/api/online/play/'+VID)[0],502)
    def test_migration_preserves_legacy_youtube_references(self):
        with onda.db() as c:
            uid=c.execute('SELECT id FROM users LIMIT 1').fetchone()[0]
            c.execute("DELETE FROM migrations WHERE name='hybrid-reference-ids-v1'")
            c.execute('INSERT INTO online_catalog VALUES(?,?,?)',('oldVideo001',json.dumps({'id':'oldVideo001','title':'Anterior','provider':'youtube'}),int(time.time())))
            c.execute('INSERT INTO online_saves(user_id,video_id,favorite) VALUES(?,?,1)',(uid,'oldVideo001'))
        onda.init()
        with onda.db() as c:
            self.assertIsNotNone(c.execute('SELECT * FROM online_saves WHERE video_id=?',('piped:oldVideo001',)).fetchone())
            self.assertEqual(json.loads(c.execute('SELECT metadata FROM online_catalog WHERE video_id=?',('piped:oldVideo001',)).fetchone()[0])['source'],'Piped')

    def test_muxed_level3_excludes_video_only_and_pure_audio_wins(self):
        self.search()
        muxed={'mimeType':'video/mp4','videoOnly':False,'itag':18,'url':'http://127.0.0.1:8081/muxed'}
        video_only=dict(muxed,videoOnly=True,url='http://127.0.0.1:8081/video-only')
        data={'id':'testVideo01','title':'Fixture','audioStreams':[],'videoStreams':[video_only,muxed]}
        with patch.object(onda.provider,'request',return_value=data):
            status,r=self.client.request('/api/online/play/'+VID);self.assertEqual(status,200,r);self.assertEqual(r['level'],3);self.assertTrue(r['muxed'])
            self.assertTrue(onda.provider.tickets[r['url'].rsplit('/',1)[-1]]['url'].endswith('/muxed'))
            data['audioStreams']=[{'mimeType':'audio/mp4','bitrate':128000,'url':'http://127.0.0.1:8081/pure'}]
            self.assertEqual(self.client.request('/api/online/play/'+VID)[1]['level'],2)
            self.assertEqual(self.client.request('/api/online/play/'+VID+'?minimum_level=3')[1]['level'],3)
            data['audioStreams']=[];data['videoStreams']=[video_only]
            self.assertEqual(self.client.request('/api/online/play/'+VID)[0],422)

    def test_ytdlp_endpoint_auth_failure_and_private_resolve_metadata(self):
        from audio_resolver import AudioUnavailable
        self.assertEqual(Client(self.port).request('/api/audio/testVideo01')[0],401)
        with patch.object(onda.resolver,'resolve',side_effect=AudioUnavailable):
            status,r=self.client.request('/api/audio/testVideo01?resolve=1');self.assertEqual(status,502);self.assertEqual(r,{'error':'audio_unavailable'})
        with patch.object(onda.resolver,'resolve',return_value={'url':'https://private.googlevideo.com/secret','mime':'audio/webm'}):
            status,r=self.client.request('/api/audio/testVideo01?resolve=1');self.assertEqual(status,200);self.assertEqual(r['url'],'/api/audio/testVideo01');self.assertNotIn('googlevideo',json.dumps(r))
        from io import BytesIO
        class Response(BytesIO):
            status=206
            def getheader(self,key,default=None):return {'Content-Type':'audio/webm','Content-Length':'4','Content-Range':'bytes 0-3/100','Accept-Ranges':'bytes'}.get(key,default)
        from unittest.mock import Mock
        with patch.object(onda.resolver,'open',return_value=(Mock(),Response(b'1234'),'audio/webm')) as request:
            status,r=self.client.request('/api/audio/testVideo01',extra={'Range':'bytes=0-3'});self.assertEqual(status,206);self.assertEqual(request.call_args.args[1],'bytes=0-3')
        self.assertEqual(self.client.request('/api/audio/testVideo01',extra={'Range':'bytes=-'})[0],416)
        with patch.object(onda.resolver,'prefetch') as prefetch:
            self.assertEqual(self.client.request('/api/audio/prefetch','POST',{'video_ids':['testVideo01','testVideo02','testVideo03']})[0],202)
            self.assertEqual([c.args[0] for c in prefetch.call_args_list],['testVideo01','testVideo02','testVideo03'])
            self.assertEqual(self.client.request('/api/audio/prefetch','POST',{'video_ids':['a','b','c','d']})[0],400)
            self.assertEqual(Client(self.port).request('/api/audio/prefetch','POST',{'video_ids':['testVideo01']})[0],401)
            self.assertEqual(prefetch.call_count,3)

    def test_local_admin_recovery_expiry_single_use_and_session_revocation(self):
        link=onda.create_admin_recovery('one@test.com',self.port);token=link.split('#')[1]
        with onda.db() as c:
            row=c.execute('SELECT * FROM admin_recovery').fetchone();self.assertNotEqual(row['token'],token)
            self.assertLessEqual(row['expires'],int(time.time())+600)
        anonymous=Client(self.port)
        self.assertEqual(anonymous.request('/api/recover-admin','POST',{'token':token,'password':'short'})[0],400)
        self.assertEqual(self.client.request('/api/me')[1]['user']['role'],'user')
        body={'token':token,'password':'my-new-private-password'}
        code,r=anonymous.request('/api/recover-admin','POST',body);self.assertEqual(code,200,r)
        me=anonymous.request('/api/me')[1]['user'];self.assertEqual(me['role'],'admin');self.assertEqual(me['plan'],'free')
        self.assertEqual(anonymous.request('/api/admin/users')[0],200)
        self.assertEqual(self.client.request('/api/me')[0],401)
        self.assertEqual(self.other.request('/api/me')[0],200)
        self.assertEqual(Client(self.port).request('/api/recover-admin','POST',body)[0],403)
        self.assertEqual(Client(self.port).request('/api/login','POST',{'email':'one@test.com','password':'private-test-password'})[0],401)
        another=onda.create_admin_recovery('one@test.com',self.port).split('#')[1]
        with onda.db() as c:c.execute('UPDATE admin_recovery SET expires=0')
        self.assertEqual(Client(self.port).request('/api/recover-admin','POST',{'token':another,'password':'another-private-password'})[0],403)
        self.assertEqual(Client(self.port).request('/api/admin/users')[0],401)

    def test_music_catalog_songs_artists_cache_and_pagination(self):
        entries=[{'videoId':f'testSong{i:03d}','title':f'Fixture canción {i}','artists':[{'name':'Artista fixture'}],'duration':'3:17','thumbnails':[]} for i in range(40)]
        with patch.object(onda.provider.music,'search',return_value=entries) as search:
            code,r=self.client.request('/api/online/search?q=Feid');self.assertEqual(code,200,r);self.assertEqual(r['source'],'ytmusic');self.assertEqual(len(r['items']),20);self.assertEqual(r['items'][0]['duration'],197);self.assertEqual(r['items'][0]['channel'],'Artista fixture')
            search.assert_called_once_with('Feid','songs',20)
            self.assertTrue(self.client.request('/api/online/search?q=Feid')[1]['cached']);self.assertEqual(search.call_count,1)
            page=r['next_page'];code,r=self.client.request('/api/online/search?q=Feid&source=ytmusic&page='+page);self.assertEqual(code,200,r);self.assertEqual(len(r['items']),20);self.assertEqual(r['items'][0]['title'],'Fixture canción 20');self.assertEqual(search.call_args.args[2],40)
        with patch.object(onda.provider.music,'search',return_value=[{'browseId':'UCfixture','artist':'Artista fixture','thumbnails':[]}]) as search:
            code,r=self.client.request('/api/online/search?q=Feid&filter=artists');self.assertEqual(code,200,r);self.assertEqual(r['artists'][0]['name'],'Artista fixture');self.assertEqual(r['items'],[]);search.assert_called_once_with('Feid','artists',20)
        self.assertEqual(self.client.request('/api/online/search?q=Feid&filter=invalid')[0],400)

    def test_ytmusic_failure_uses_youtube_data_api_without_exposing_key(self):
        def api(resource,params):
            if resource=='search':return {'items':[{'id':{'videoId':'testVideo01'}}]}
            return {'items':[{'id':'testVideo01','snippet':{'title':'Vídeo de prueba','channelTitle':'Canal fixture','thumbnails':{}},'status':{'embeddable':True,'privacyStatus':'public'},'contentDetails':{'duration':'PT3M17S'}}]}
        with patch.object(onda.provider.music,'search',side_effect=ProviderError(502,'ytmusic_unavailable','Fixture')),patch.object(onda.provider.youtube,'request',side_effect=api) as fallback:
            code,r=self.client.request('/api/online/search?q=Feid');self.assertEqual(code,200,r);self.assertTrue(r['fallback']);self.assertEqual(r['source'],'youtube');self.assertEqual(r['items'][0]['provider_id'],'testVideo01');self.assertEqual(r['items'][0]['duration'],197);self.assertEqual(fallback.call_count,2)
            self.assertNotIn('key=',json.dumps(r))

if __name__=='__main__':unittest.main()
