import io
import json
import time
import unittest
from unittest.mock import Mock,patch
from innertube_resolver import InnerTubeResolver,CLIENT
from audio_router import AudioRouter

VID='dQw4w9WgXcQ'
URL='https://r1.googlevideo.com/audio?mime=audio%2Fmp4'
def payload(**changes):
    result={'playabilityStatus':{'status':'OK'},'videoDetails':{'videoId':VID},
            'streamingData':{'adaptiveFormats':[{'url':URL,'mimeType':'audio/mp4; codecs="mp4a.40.2"','itag':140}]}}
    result.update(changes);return result

class InnerTubeTests(unittest.TestCase):
    def test_post_context_and_cache_without_subprocess(self):
        response=Mock(status=200);response.read.return_value=json.dumps(payload()).encode()
        connection=Mock();connection.getresponse.return_value=response
        resolver=InnerTubeResolver(preferred='ANDROID_VR')
        resolver.visitor='test-visitor';resolver.visitor_expires=time.monotonic()+1800
        with patch('innertube_resolver.http.client.HTTPSConnection',return_value=connection):
            self.assertEqual(resolver.resolve(VID)['url'],URL)
            self.assertEqual(resolver.resolve(VID)['url'],URL)
        self.assertEqual(connection.request.call_count,1)
        args=connection.request.call_args.args
        self.assertEqual(args[0],'POST');self.assertEqual(json.loads(args[2])['context']['client'],dict(CLIENT,visitorData='test-visitor'))
        self.assertEqual(args[3]['X-Goog-Visitor-Id'],'test-visitor')
        self.assertAlmostEqual(resolver.cache[VID]['expires']-time.time(),1800,delta=2)

    def test_invalid_restricted_ciphered_and_wrong_destination(self):
        for data in [payload(playabilityStatus={'status':'LOGIN_REQUIRED'}),payload(videoDetails={'videoId':'wrong'}),
                     payload(streamingData={'adaptiveFormats':[{'mimeType':'audio/mp4','signatureCipher':'secret'}]}),
                     payload(streamingData={'adaptiveFormats':[{'mimeType':'audio/mp4','url':'https://127.0.0.1/secret'}]})]:
            self.assertIsNone(InnerTubeResolver.select(data,VID))
        self.assertIsNone(InnerTubeResolver().resolve('--exec'))

    def test_rejected_media_falls_back_and_invalidates_url(self):
        fallback=Mock();fallback_response=Mock();fallback_response.ivox_timings={}
        fallback.open.return_value=(Mock(),fallback_response,'audio/mp4')
        router=AudioRouter(fallback);router.inner.cache[VID]=dict(InnerTubeResolver.select(payload(),VID))
        connection=Mock();response=Mock(status=403);response.getheader.return_value='text/plain'
        with patch('innertube_resolver.get_response',return_value=(connection,response)):
            self.assertEqual(router.open(VID,'bytes=0-')[2],'audio/mp4')
        fallback.open.assert_called_once_with(VID,'bytes=0-');connection.close.assert_called_once()
        self.assertNotIn(VID,router.inner.cache)

    def test_timeout_returns_none_and_does_not_repeat_request(self):
        resolver=InnerTubeResolver();connection=Mock();connection.request.side_effect=TimeoutError()
        with patch('innertube_resolver.http.client.HTTPSConnection',return_value=connection):
            self.assertIsNone(resolver.resolve(VID));self.assertIsNone(resolver.resolve(VID))
        connection.request.assert_called_once();connection.close.assert_called_once()

    def test_direct_audio_keeps_range_and_never_calls_ytdlp(self):
        fallback=Mock();router=AudioRouter(fallback)
        router.inner.cache[VID]=InnerTubeResolver.select(payload(),VID)
        response=Mock(status=206);response.getheader.return_value='audio/mp4'
        with patch('innertube_resolver.get_response',return_value=(Mock(),response)) as get:
            self.assertEqual(router.open(VID,'bytes=100-200')[2],'audio/mp4')
            self.assertEqual(get.call_args.args[1]['Range'],'bytes=100-200')
        fallback.open.assert_not_called()
        self.assertEqual(response.ivox_timings['source'],'innertube')

    def test_visitor_post_and_html_backup_are_cached(self):
        resolver=InnerTubeResolver()
        with patch.object(resolver,'request',side_effect=[(405,b''),(200,b'<script>{"VISITOR_DATA":"from-html"}</script>')]) as request:
            self.assertEqual(resolver.visitor_data(VID),'from-html')
            self.assertEqual(resolver.visitor_data(VID),'from-html')
            self.assertEqual(request.call_count,2)
        resolver.close()

    def test_login_refreshes_once_and_attaches_new_token(self):
        resolver=InnerTubeResolver(preferred='ANDROID_VR');resolver.visitor='old';resolver.visitor_expires=time.monotonic()+1800
        responses=[(200,json.dumps(payload(playabilityStatus={'status':'LOGIN_REQUIRED'})).encode()),
                   (200,b'{"responseContext":{"visitorData":"new"}}'),(200,json.dumps(payload()).encode())]
        with patch.object(resolver,'request',side_effect=responses) as request:
            self.assertTrue(resolver.resolve(VID))
            self.assertEqual(request.call_count,3)
            self.assertEqual(json.loads(request.call_args.args[2])['context']['client']['visitorData'],'new')
        self.assertEqual(resolver.visitor,'new')

    def test_alternative_client_when_android_stays_blocked(self):
        resolver=InnerTubeResolver(preferred='ANDROID_VR');resolver.visitor='old';resolver.visitor_expires=time.monotonic()+1800
        blocked=(200,json.dumps(payload(playabilityStatus={'status':'LOGIN_REQUIRED'})).encode())
        with patch.object(resolver,'request',side_effect=[blocked,(200,b'{"visitorData":"new"}'),blocked,
                                                         (200,json.dumps(payload()).encode())]):
            self.assertEqual(resolver.resolve(VID)['client'],'VISIONOS')

    def test_visitor_expiry_and_rejected_token_single_refresh(self):
        resolver=InnerTubeResolver();resolver.visitor='expired';resolver.visitor_expires=0
        with patch.object(resolver,'request',return_value=(200,b'{"visitorData":"fresh"}')) as request:
            self.assertEqual(resolver.visitor_data(VID),'fresh')
            self.assertEqual(resolver.visitor_data(VID,rejected='expired'),'fresh')
            request.assert_called_once()

if __name__=='__main__':unittest.main()
