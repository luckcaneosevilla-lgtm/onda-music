import io
import json
import unittest
import time
from unittest.mock import Mock, patch

from audio_router import AudioRouter
from audio_resolver import OPTIONS

META={'title':'Example (Official Audio)','channel':'Artist','duration':180}
TRACK={'id':'AbC12','title':'Example','user':{'name':'Artist'},'duration':180,'is_streamable':True}


def response(data,status=200,mime='application/json',location=None):
    result=io.BytesIO(json.dumps(data).encode());result.status=status
    result.getheader=lambda key,default=None: {'Content-Type':mime,'Location':location}.get(key,default)
    return result


class AudioRouterTests(unittest.TestCase):
    def setUp(self):
        inner=Mock();inner.resolve.return_value=None;inner.open.return_value=None
        patcher=patch('audio_router.InnerTubeResolver',return_value=inner);patcher.start();self.addCleanup(patcher.stop)
    def test_matching_requires_same_recording_and_public_stream(self):
        self.assertTrue(AudioRouter.matches(META,TRACK))
        self.assertTrue(AudioRouter.matches(dict(META,title='Example (feat. Guest)'),dict(TRACK,title='Example (ft. Guest)')))
        for changes in ({'title':'Example remix'},{'duration':30},{'user':{'name':'Other'}},
                        {'is_stream_gated':True},{'is_streamable':False},{'id':'../bad'}):
            self.assertFalse(AudioRouter.matches(META,dict(TRACK,**changes)))

    def test_audius_selected_cached_and_range_forwarded(self):
        fallback=Mock();router=AudioRouter(fallback);connection=Mock()
        stream=response({},206,'audio/mpeg')
        with patch('audio_router.get_response',side_effect=[(connection,response({'data':[TRACK]})),(connection,stream)]) as get:
            router.find('abcdefghijk',META)
            self.assertEqual(router.resolve('abcdefghijk',META)['source'],'audius')
            self.assertEqual(router.open('abcdefghijk','bytes=0-100',META)[2],'audio/mpeg')
            self.assertEqual(get.call_args.args[1],{'Range':'bytes=0-100'})
            self.assertEqual(stream.ivox_timings['source'],'audius')
            fallback.resolve.assert_not_called();fallback.open.assert_not_called()

    def test_miss_and_timeout_use_fallback_and_negative_cache(self):
        for effect in ([(Mock(),response({'data':[]}))],TimeoutError()):
            fallback=Mock();fallback.resolve.return_value={'url':'internal','mime':'audio/mp4'}
            router=AudioRouter(fallback)
            with patch('audio_router.get_response',side_effect=effect) as get:
                router.find('abcdefghijk',META)
                for _ in range(2):self.assertEqual(router.resolve('abcdefghijk',META)['source'],'yt-dlp')
                self.assertEqual(get.call_count,1)

    def test_broken_audius_stream_uses_fallback(self):
        fallback=Mock();fallback.open.return_value=(Mock(),response({},206,'audio/mp4'),'audio/mp4')
        router=AudioRouter(fallback);connection=Mock()
        with patch('audio_router.get_response',side_effect=[(connection,response({'data':[TRACK]})),(connection,response({},403))]):
            router.find('abcdefghijk',META)
            self.assertEqual(router.open('abcdefghijk','bytes=0-',META)[2],'audio/mp4')
            self.assertIsNone(router.find('abcdefghijk',META))
            fallback.open.assert_called_once_with('abcdefghijk','bytes=0-')
            connection.close.assert_called()

    def test_download_flags(self):
        self.assertEqual(OPTIONS['http_chunk_size'],8*1024*1024)
        self.assertEqual(OPTIONS['concurrent_fragment_downloads'],8)

    def test_search_prefetch_deduplicates_and_prepares_prefix(self):
        fallback=Mock();router=AudioRouter(fallback)
        metadata=dict(META,provider='piped',provider_id='abcdefghijk')
        with patch('audio_router.threading.Thread') as thread:
            router.prefetch_many([metadata,metadata])
            router.prefetch_many([metadata])
            self.assertEqual(len(router.pending),1)
            thread.return_value.start.assert_called_once()
        with patch.object(router,'find',return_value={'track_id':'AbC12'}) as find,patch.object(router,'prepare_prefix') as prefix:
            router.prepare()
            find.assert_called_once_with('abcdefghijk',metadata,thorough=True)
            prefix.assert_called_once();fallback.prefetch.assert_not_called()
        self.assertFalse(router.running);self.assertFalse(router.preparing)

    def test_prefetch_warms_innertube_without_spawning_fallback(self):
        fallback=Mock();router=AudioRouter(fallback)
        metadata=dict(META,provider='piped',provider_id='abcdefghijk')
        router.inner.resolve.return_value={'url':'https://r1.googlevideo.com/audio'}
        with patch('audio_router.threading.Thread'):
            router.prefetch_many([metadata])
        with patch.object(router,'find') as find:
            router.prepare()
        router.inner.resolve.assert_called_once_with('abcdefghijk')
        find.assert_not_called();fallback.prefetch.assert_not_called()
        self.assertFalse(router.running);self.assertFalse(router.preparing)

    def test_prefetch_does_not_block_play_and_cache_lasts_30_minutes(self):
        fallback=Mock();fallback.resolve.return_value={'mime':'audio/mp4'}
        router=AudioRouter(fallback);key=router.key('abcdefghijk',META)
        router.preparing.add(key)
        with patch('audio_router.get_response') as get:
            self.assertEqual(router.resolve('abcdefghijk',META)['source'],'yt-dlp')
            get.assert_not_called()
        router.preparing.clear()
        started=time.monotonic()
        with patch('audio_router.get_response',return_value=(Mock(),response({'data':[TRACK]}))):
            router.find('abcdefghijk',META)
        self.assertAlmostEqual(router.cache[key][0]-started,1800,delta=1)


if __name__=='__main__':unittest.main()
