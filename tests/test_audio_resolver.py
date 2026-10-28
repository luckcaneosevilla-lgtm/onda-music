import subprocess
import tempfile
import threading
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from audio_resolver import YtDlpResolver,AudioUnavailable

URL='https://r1.googlevideo.com/videoplayback?mime=audio%2Fmp4\n'
class Process:
    def __init__(self,stdout=URL,error=None):self.stdout=stdout;self.error=error;self.returncode=0;self.killed=False;self.inputs=[]
    def poll(self):return None
    def communicate(self,data=None,timeout=None):
        self.inputs.append((data,timeout))
        if self.error and not self.killed:raise self.error
        return self.stdout,None
    def kill(self):self.killed=True

class ResolverTests(unittest.TestCase):
    def test_subprocess_arguments_timeout_and_cache(self):
        r=YtDlpResolver();process=Process()
        with patch('audio_resolver.subprocess.Popen',return_value=process) as run:
            r.resolve('dQw4w9WgXcQ');r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,1)
            args=run.call_args.args[0];self.assertIn('bestaudio[ext=m4a]/bestaudio',args);self.assertIn('youtube:skip=hls,dash',args);self.assertIn('--ignore-config',args);self.assertIn('-g',args);self.assertNotIn('shell',run.call_args.kwargs)
            self.assertEqual(process.inputs,[('dQw4w9WgXcQ\n',15)]);self.assertNotIn('dQw4w9WgXcQ',' '.join(args))
            self.assertGreater(r.cache['dQw4w9WgXcQ']['expires'],time.time()+1790)
            r.cache['dQw4w9WgXcQ']['expires']=0;r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,2)
    def test_persistent_cache_survives_restart_and_expires(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'onda.sqlite';r=YtDlpResolver();r.persist(path)
            with patch('audio_resolver.subprocess.Popen',return_value=Process()) as run:
                r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,1)
            restarted=YtDlpResolver();restarted.persist(path)
            with patch('audio_resolver.subprocess.Popen',return_value=Process()) as run:
                self.assertEqual(restarted.resolve('dQw4w9WgXcQ')['mime'],'audio/mp4');run.assert_not_called()
            restarted.invalidate('dQw4w9WgXcQ');again=YtDlpResolver();again.persist(path);self.assertNotIn('dQw4w9WgXcQ',again.cache)
            r.save('INSERT OR REPLACE INTO audio_url_cache VALUES(?,?,?,?)',('aaaaaaaaaaa','https://r1.googlevideo.com/x','audio/webm',time.time()-1))
            expired=YtDlpResolver();expired.persist(path);self.assertNotIn('aaaaaaaaaaa',expired.cache)
    def test_timeout_negative_cache_and_invalid_destinations(self):
        r=YtDlpResolver()
        process=Process(error=subprocess.TimeoutExpired('yt-dlp',15))
        with patch('audio_resolver.subprocess.Popen',return_value=process) as run:
            for _ in range(2):
                with self.assertRaises(AudioUnavailable):r.resolve('dQw4w9WgXcQ')
            self.assertEqual(run.call_count,1);self.assertTrue(process.killed)
        for url in ('http://r1.googlevideo.com/file','https://127.0.0.1/file','https://googlevideo.com.evil.test/file','https://user:pass@r1.googlevideo.com/file'):
            self.assertFalse(r.allowed(url))
        with patch('audio_resolver.subprocess.Popen') as run:
            with self.assertRaises(AudioUnavailable):r.resolve('--exec bad')
            r.prefetch('--exec bad');run.assert_not_called()
    def test_warm_spare_is_used_and_replaced(self):
        r=YtDlpResolver(spares=1);spares=[Process(),Process()]
        with patch('audio_resolver.subprocess.Popen',side_effect=spares) as run:
            r.warm();self.assertEqual(r.spares,[spares[0]])
            r.resolve('dQw4w9WgXcQ');self.assertEqual(spares[0].inputs,[('dQw4w9WgXcQ\n',15)])
            for _ in range(50):
                if r.spares==[spares[1]]:break
                time.sleep(.01)
            self.assertEqual(r.spares,[spares[1]]);self.assertEqual(run.call_count,2)
    def test_prefetch_fills_cache_in_background_and_is_bounded(self):
        r=YtDlpResolver();gate=threading.Event()
        def slow(*a,**k):gate.wait(2);return Process()
        with patch('audio_resolver.subprocess.Popen',side_effect=slow) as run:
            for video_id in ('dQw4w9WgXcQ','kJQP7kiw5Fk','9bZkp7q19f0','OPf0YbXqDm0'):r.prefetch(video_id)
            gate.set()
            for _ in range(200):
                if len(r.cache)==3:break
                time.sleep(.01)
            time.sleep(.05);self.assertEqual(run.call_count,3);self.assertEqual(set(r.cache),{'dQw4w9WgXcQ','kJQP7kiw5Fk','9bZkp7q19f0'})
            r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,3)
