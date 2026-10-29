import json
import queue
import tempfile
import threading
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from audio_resolver import YtDlpResolver,AudioUnavailable,OPTIONS

URL='https://r1.googlevideo.com/videoplayback?mime=audio%2Fmp4'
class Process:
    """A fake warm worker: prints 'ready', then answers one JSON line per video id."""
    def __init__(self,url=URL,hang=False,delay=0,log=None):
        self.url=url;self.hang=hang;self.delay=delay;self.inputs=[] if log is None else log;self.out=queue.Queue();self.killed=False;self.out.put('ready\n')
        process=self
        class Stdin:
            def write(self,data):
                process.inputs.append(data.strip())
                if not process.hang:
                    def answer(video_id=data.strip()):
                        time.sleep(process.delay);process.out.put(json.dumps({'id':video_id,'url':process.url})+'\n')
                    threading.Thread(target=answer,daemon=True).start()
            def flush(self):pass
        self.stdin=Stdin()
    @property
    def stdout(self):
        while True:
            line=self.out.get()
            if line is None:return
            yield line
    def poll(self):return 0 if self.killed else None
    def kill(self):self.killed=True;self.out.put(None)

class ResolverTests(unittest.TestCase):
    def test_busy_pool_is_not_remembered_as_an_unplayable_song(self):
        r=YtDlpResolver()
        with patch.object(r,'run',side_effect=[AudioUnavailable('worker_unavailable'),URL]) as run:
            with self.assertRaisesRegex(AudioUnavailable,'worker_unavailable'):r.resolve('dQw4w9WgXcQ')
            self.assertNotIn('dQw4w9WgXcQ',r.failures)
            self.assertEqual(r.resolve('dQw4w9WgXcQ')['url'],URL)
            self.assertEqual(r.playing,0)
            self.assertEqual(run.call_count,2)

    def test_small_pool_reserves_extraction_for_actual_play(self):
        r=YtDlpResolver(size=1)
        with patch.object(r,'spawn') as spawn:
            r.prefetch_many(['dQw4w9WgXcQ'])
            spawn.assert_not_called()
            self.assertEqual(list(r.pending),[])

    def test_explicit_refresh_clears_negative_cache_and_is_bounded(self):
        r=YtDlpResolver();vid='dQw4w9WgXcQ'
        with patch.object(r,'run',side_effect=[AudioUnavailable('format_unavailable'),URL]) as run:
            with self.assertRaisesRegex(AudioUnavailable,'format_unavailable'):r.resolve(vid)
            with self.assertRaisesRegex(AudioUnavailable,'format_unavailable'):r.resolve(vid)
            self.assertEqual(run.call_count,1)
            self.assertTrue(r.refresh(vid))
            self.assertEqual(r.resolve(vid)['url'],URL)
            self.assertFalse(r.refresh(vid))
            self.assertIn(vid,r.cache)
            self.assertEqual(run.call_count,2)
            self.assertGreater(r.compatible[vid],time.time())

    def test_worker_arguments_reuse_and_cache(self):
        r=YtDlpResolver(size=1);process=Process()
        with patch('audio_resolver.subprocess.Popen',return_value=process) as run:
            r.resolve('dQw4w9WgXcQ');r.resolve('dQw4w9WgXcQ');r.resolve('kJQP7kiw5Fk')
            # One warm process serves every song; the cache answers repeats without asking it.
            self.assertEqual(run.call_count,1);self.assertEqual(process.inputs,['dQw4w9WgXcQ','kJQP7kiw5Fk'])
            args=run.call_args.args[0];options=json.loads(args[-1])
            self.assertEqual(options['format'],'bestaudio[acodec^=mp4a.40.2]/bestaudio[ext=m4a]/bestaudio');self.assertEqual(options['extractor_args'],OPTIONS['extractor_args']);self.assertTrue(options['noplaylist'])
            self.assertNotIn('shell',run.call_args.kwargs);self.assertNotIn('dQw4w9WgXcQ',' '.join(args))
            self.assertGreater(r.cache['dQw4w9WgXcQ']['expires'],time.time()+1790)
            r.cache['dQw4w9WgXcQ']['expires']=0;r.resolve('dQw4w9WgXcQ');self.assertEqual(process.inputs[-1],'dQw4w9WgXcQ')
    def test_persistent_cache_survives_restart_and_expires(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'onda.sqlite';r=YtDlpResolver(size=1);r.persist(path)
            with patch('audio_resolver.subprocess.Popen',return_value=Process()) as run:
                r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,1)
            restarted=YtDlpResolver();restarted.persist(path)
            with patch('audio_resolver.subprocess.Popen',return_value=Process()) as run:
                self.assertEqual(restarted.resolve('dQw4w9WgXcQ')['mime'],'audio/mp4');run.assert_not_called()
            restarted.invalidate('dQw4w9WgXcQ');again=YtDlpResolver();again.persist(path);self.assertNotIn('dQw4w9WgXcQ',again.cache)
            r.save('INSERT OR REPLACE INTO audio_url_cache VALUES(?,?,?,?)',('aaaaaaaaaaa','https://r1.googlevideo.com/x','audio/webm',time.time()-1))
            expired=YtDlpResolver();expired.persist(path);self.assertNotIn('aaaaaaaaaaa',expired.cache)
    def test_hung_worker_is_killed_replaced_and_failure_remembered(self):
        r=YtDlpResolver(size=1);r.timeout=.2;hung=Process(hang=True);fresh=Process()
        with patch('audio_resolver.subprocess.Popen',side_effect=[hung,fresh]) as run:
            with self.assertRaises(AudioUnavailable):r.resolve('dQw4w9WgXcQ')
            self.assertTrue(hung.killed);self.assertEqual(r.count,0)
            # The same song is not retried for 30 s; another song gets a fresh worker.
            with self.assertRaises(AudioUnavailable):r.resolve('dQw4w9WgXcQ')
            self.assertEqual(run.call_count,1)
            self.assertEqual(r.resolve('kJQP7kiw5Fk')['mime'],'audio/mp4');self.assertEqual(run.call_count,2)
    def test_invalid_ids_and_destinations(self):
        r=YtDlpResolver()
        for url in ('http://r1.googlevideo.com/file','https://127.0.0.1/file','https://googlevideo.com.evil.test/file','https://user:pass@r1.googlevideo.com/file'):
            self.assertFalse(r.allowed(url))
        with patch('audio_resolver.subprocess.Popen',return_value=Process(url='https://evil.test/x')) as run:
            with self.assertRaises(AudioUnavailable):r.resolve('--exec bad')
            r.prefetch('--exec bad');run.assert_not_called()
            with self.assertRaises(AudioUnavailable):r.resolve('dQw4w9WgXcQ')
    def test_start_boots_whole_pool_with_warmup(self):
        r=YtDlpResolver(size=3);processes=[Process(),Process(),Process()]
        with patch('audio_resolver.subprocess.Popen',side_effect=processes):
            r.start(warm='jNQXAC9IVRw')
            for _ in range(200):
                if len(r.idle)==3:break
                time.sleep(.01)
            self.assertEqual(len(r.idle),3);self.assertTrue(all(p.inputs==['jNQXAC9IVRw'] for p in processes))
    def test_prefetch_keeps_a_worker_free_and_newest_first(self):
        r=YtDlpResolver(size=3);made=[];asked=[]
        def spawn(*a,**k):
            p=Process(delay=.15,log=asked);made.append(p);return p
        with patch('audio_resolver.subprocess.Popen',side_effect=spawn):
            ids=['dQw4w9WgXcQ','kJQP7kiw5Fk','9bZkp7q19f0','OPf0YbXqDm0','jNQXAC9IVRw']
            r.prefetch_many(ids)
            time.sleep(.05);self.assertLessEqual(len(made),2)
            # A play while prefetching still gets the third worker immediately.
            started=time.time();r.resolve('fJ9rUzIMcZQ');self.assertLess(time.time()-started,.5)
            for _ in range(300):
                if all(v in r.cache for v in ids):break
                time.sleep(.01)
            self.assertTrue(all(v in r.cache for v in ids));self.assertEqual(len(made),3)
            self.assertLess(asked.index('dQw4w9WgXcQ'),asked.index('jNQXAC9IVRw'))

if __name__=='__main__':unittest.main()
