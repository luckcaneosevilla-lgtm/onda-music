import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock,patch
from audio_prepared import PreparedAudioCache
from audio_playback import NativePlayback
from audio_resolver import AudioUnavailable
import tests.test_audio_partial as fixtures
ITEM=fixtures.ITEM


class NativePlaybackTests(unittest.TestCase):
    setUp=fixtures.PartialTests.setUp
    get=fixtures.PartialTests.get
    consume=fixtures.PartialTests.consume
    def cache(self):
        cache=PreparedAudioCache(lambda *args:ITEM,partial=True,prefix_bytes=65536)
        self.addCleanup(cache.close)
        return cache

    def test_probe_and_seek_keep_original_choice_after_background_publication(self):
        cache=self.cache();player=NativePlayback(cache)
        with patch.object(cache,'ensure',return_value=False):
            self.assertIsNone(player.open(('user','attempt'),'abcdefghijk','bytes=0-1'))
        cache._prepare('abcdefghijk',{},time.perf_counter())
        self.assertIsNone(player.open(('user','attempt'),'abcdefghijk','bytes=20-40'))
        response=player.open(('user','new-attempt'),'abcdefghijk','bytes=0-1')
        self.assertEqual(response.read1(),self.raw[:2]);response.close()

    def test_prepared_choice_survives_cache_invalidation(self):
        cache=self.cache();player=NativePlayback(cache)
        response=player.open(('user','attempt'),'abcdefghijk',None)
        expected=self.consume(response)
        cache.invalidate('abcdefghijk')
        self.assertIsNone(cache.open('abcdefghijk'))
        self.assertEqual(self.consume(player.open(('user','attempt'),'abcdefghijk','bytes=10-100')),expected[10:101])

    def test_demand_bypasses_unrelated_speculation_and_joins_same_track(self):
        cache=self.cache();started=threading.Event();release=threading.Event();calls=[]
        def prepare(vid,metadata,at):
            calls.append(vid);started.set();release.wait(2)
            with cache.lock:cache.entries[vid]={'expires':time.monotonic()+60,'leases':0,'size':0,'kind':'partial'}
        cache.pending.append({'provider_id':'other______'})
        with patch.object(cache,'_prepare',side_effect=prepare),ThreadPoolExecutor(2) as pool:
            first=pool.submit(cache.ensure,'abcdefghijk',{},1)
            self.assertTrue(started.wait(1))
            second=pool.submit(cache.ensure,'abcdefghijk',{},1)
            release.set()
            self.assertTrue(first.result());self.assertTrue(second.result())
        self.assertEqual(calls,['abcdefghijk'])
        self.assertEqual(cache.pending[0]['provider_id'],'other______')

    def test_cold_wait_is_bounded(self):
        cache=self.cache();release=threading.Event()
        with patch.object(cache,'_prepare',side_effect=lambda *a:release.wait(1)):
            began=time.monotonic()
            try:self.assertFalse(cache.ensure('abcdefghijk',timeout=.03))
            finally:release.set()
            self.assertLess(time.monotonic()-began,.3)

    def test_refresh_runs_once_for_all_ranges_in_attempt(self):
        cache=self.cache();refresh=Mock();player=NativePlayback(cache,refresh=refresh)
        for header in ('bytes=0-1','bytes=30-80','bytes=-20'):
            response=player.open(('user','attempt'),'abcdefghijk',header,refresh=True)
            self.consume(response)
        refresh.assert_called_once_with('abcdefghijk')

    def test_expired_attempt_fails_instead_of_switching_offsets(self):
        cache=self.cache();player=NativePlayback(cache)
        response=player.open(('user','attempt'),'abcdefghijk','bytes=0-1');response.close()
        player.choices[('user','attempt')]['expires']=time.monotonic()-.01
        with self.assertRaises(AudioUnavailable):player.open(('user','attempt'),'abcdefghijk','bytes=30-80')

    def test_attempt_outlives_short_cache_entry_window(self):
        # A URL resolved 25 min earlier leaves the cache entry a short window;
        # the playing attempt must keep its view while the signed URL is valid.
        cache=self.cache();player=NativePlayback(cache)
        response=player.open(('user','attempt'),'abcdefghijk','bytes=0-1');response.close()
        with cache.lock:cache.entries['abcdefghijk']['expires']=time.monotonic()-1
        choice=player.choices[('user','attempt')]
        self.assertGreater(choice['expires'],time.monotonic()+60)
        self.consume(player.open(('user','attempt'),'abcdefghijk','bytes=30-80'))

    def test_live_attempt_slides_but_not_past_signed_url(self):
        cache=self.cache();player=NativePlayback(cache,ttl=100)
        response=player.open(('user','attempt'),'abcdefghijk','bytes=0-1');response.close()
        choice=player.choices[('user','attempt')]
        choice['expires']=time.monotonic()+5;choice['hard']=time.monotonic()+50
        self.consume(player.open(('user','attempt'),'abcdefghijk','bytes=30-80'))
        self.assertAlmostEqual(choice['expires'],choice['hard'],delta=.5)

    def test_prepared_view_uses_signed_url_expiry(self):
        signed=dict(ITEM,url=ITEM['url']+'&expire=%d'%(time.time()+6*3600),expires=time.time()+120)
        cache=PreparedAudioCache(lambda *args:signed,partial=True,prefix_bytes=65536);self.addCleanup(cache.close)
        cache._prepare('abcdefghijk',{},time.perf_counter())
        with cache.lock:self.assertGreater(cache.entries['abcdefghijk']['expires'],time.monotonic()+1700)


if __name__=='__main__':unittest.main()
