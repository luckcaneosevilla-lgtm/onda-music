import http.client
import threading
import time
import unittest
from unittest.mock import Mock,patch
from audio_resolver import YtDlpResolver,AudioUnavailable
from streaming import requires_audio_proxy,audio_route
from server import Handler,OndaHTTPServer

SAFARI='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'
CHROME='Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36'
ITEM={'source':'innertube','url':'https://r1.googlevideo.com/videoplayback?secret=not-returned-on-ios','mime':'audio/mp4','expires':9999999999}

class IOSProxyTests(unittest.TestCase):
    def test_native_attempt_uses_account_scoped_pinned_view(self):
        handler=Handler.__new__(Handler);handler.command='GET'
        handler.path='/api/audio/abcdefghijk?native=1&playback=0123456789abcdef&refresh=1'
        handler.headers={'User-Agent':SAFARI,'Range':'bytes=0-1'}
        handler.session=Mock(return_value=({'id':'fixture'},{}));handler.relay=Mock();handler.download_name=Mock(return_value=None)
        response=Mock()
        with patch('server.provider.metadata',return_value={}),patch('server.native_playback.open',return_value=response) as pinned,patch('server.prepared_audio.invalidate') as invalidate,patch('server.audio_router.open') as upstream:
            handler.api(Mock(),'/api/audio/abcdefghijk')
        pinned.assert_called_once_with(('fixture','abcdefghijk','0123456789abcdef'),'abcdefghijk','bytes=0-1',None,refresh=True)
        invalidate.assert_not_called();upstream.assert_not_called()

    def test_apple_clients_receive_no_google_url_without_resolving(self):
        for ua in (SAFARI,'Mozilla/5.0 (iPhone) Version/18.0 Mobile Safari/604.1','Mozilla/5.0 (iPad) CriOS/140 Mobile Safari/604.1','Mozilla/5.0 (iPhone) FxiOS/140 Mobile'):
            with self.subTest(ua=ua):
                self.assertTrue(requires_audio_proxy(ua))
                route=audio_route('abcdefghijk',None,ua)
                self.assertTrue(route['proxy_only']);self.assertEqual(route['url'],'/api/audio/abcdefghijk')
                self.assertNotIn('googlevideo',str(audio_route('abcdefghijk',ITEM,ua)))
        self.assertFalse(requires_audio_proxy(CHROME))
        self.assertEqual(audio_route('abcdefghijk',ITEM,CHROME)['url'],ITEM['url'])

    def test_resolve_endpoint_on_safari_skips_extractor(self):
        handler=Handler.__new__(Handler)
        handler.command='GET';handler.path='/api/audio/abcdefghijk?resolve=1'
        handler.headers={'User-Agent':SAFARI};handler.session=Mock(return_value=({'id':'fixture'},{}));handler.send=Mock()
        with patch('server.provider.metadata',return_value={}),patch('server.audio_router.resolve') as resolve:
            handler.api(Mock(),'/api/audio/abcdefghijk')
        resolve.assert_not_called()
        self.assertEqual(handler.send.call_args.args[1]['url'],'/api/audio/abcdefghijk')

    def test_prepared_file_is_served_without_resolving_or_streaming_upstream(self):
        handler=Handler.__new__(Handler);handler.command='GET';handler.path='/api/audio/abcdefghijk?native=1'
        handler.headers={'User-Agent':SAFARI,'Range':'bytes=0-1'}
        handler.session=Mock(return_value=({'id':'fixture'},{}));handler.relay=Mock();handler.download_name=Mock(return_value=None)
        response=Mock()
        with patch('server.provider.metadata',return_value={}) as metadata,patch('server.prepared_audio.open',return_value=response) as prepared,patch('server.audio_router.open') as upstream,patch('server.audio_router.resolve') as resolve:
            handler.api(Mock(),'/api/audio/abcdefghijk')
        prepared.assert_called_once_with('abcdefghijk','bytes=0-1');metadata.assert_not_called();upstream.assert_not_called();resolve.assert_not_called()
        self.assertEqual(handler.relay.call_args.args[1:4],(response,response,'audio/mp4'))

    def test_extraction_does_not_queue_for_twelve_seconds(self):
        resolver=YtDlpResolver();resolver.slot=Mock();resolver.slot.acquire.return_value=False
        started=time.perf_counter()
        with self.assertRaisesRegex(AudioUnavailable,'worker_unavailable'):resolver.run('abcdefghijk')
        resolver.slot.acquire.assert_called_once_with(timeout=4)
        self.assertLess(time.perf_counter()-started,.1)
        # Slot wait + extraction stay inside the client's 30 s startup watchdog.
        self.assertLessEqual(4+resolver.timeout,16)

    def test_speculative_preparation_never_runs_extraction(self):
        from audio_resolver import speculative
        resolver=YtDlpResolver();resolver.slot=Mock()
        speculative.on=True
        try:
            with self.assertRaisesRegex(AudioUnavailable,'background_deferred'):resolver.run('abcdefghijk')
        finally:speculative.on=False
        resolver.slot.acquire.assert_not_called()

    def test_play_joins_extraction_already_running_for_same_song(self):
        resolver=YtDlpResolver();release=threading.Event();calls=[]
        def run(video_id,background=False):
            calls.append(video_id);release.wait(2)
            return 'https://r1.googlevideo.com/videoplayback?mime=audio%2Fmp4&itag=140&expire='+str(int(time.time()+3600))
        resolver.run=run
        first=threading.Thread(target=resolver.resolve,args=('abcdefghijk',));first.start()
        time.sleep(.3);threading.Timer(.5,release.set).start()
        self.assertTrue(resolver.resolve('abcdefghijk')['url'].startswith('https://'))
        first.join(3);self.assertEqual(calls,['abcdefghijk'])

    def test_proxy_flushes_first_8k_before_upstream_finishes(self):
        gate=threading.Event();chunks=[]
        class Upstream:
            status=200;ivox_timings={'source':'fixture','resolve_ms':0}
            def getheader(self,key,default=None):return {'Content-Length':'16384','Accept-Ranges':'bytes'}.get(key,default)
            def read(self,*args):raise AssertionError('blocking read used')
            def read1(self,size):
                chunks.append(size)
                if len(chunks)==1:return b'a'*8192
                if len(chunks)==2:
                    if not gate.wait(3):raise OSError('client did not receive prefix')
                    return b'b'*8192
                return b''
            def close(self):pass
        upstream=Upstream()
        class FixtureHandler(Handler):
            def session(self,*args):return None
            def do_GET(self):
                self.started=time.perf_counter();self.audio_request_id='fixture';self.audio_video_id=None
                self.relay(None,upstream,upstream,'audio/mp4',1)
        server=OndaHTTPServer(('127.0.0.1',0),FixtureHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:
            started=time.perf_counter();client.request('GET','/audio');response=client.getresponse();first=response.read1(8192)
            elapsed=time.perf_counter()-started
            self.assertGreater(len(first),0);self.assertEqual(first,b'a'*len(first));self.assertFalse(gate.is_set());self.assertLess(elapsed,.1)
            self.assertEqual(response.getheader('Content-Type'),'audio/mp4')
            gate.set();self.assertEqual(response.read(),b'a'*(8192-len(first))+b'b'*8192)
            print('Local proxy first 8 KiB: %.2f ms (mock upstream already resolved)'%(elapsed*1000),flush=True)
        finally:
            gate.set();client.close();server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
