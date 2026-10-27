import subprocess
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from audio_resolver import YtDlpResolver,AudioUnavailable

class ResolverTests(unittest.TestCase):
    def test_subprocess_arguments_timeout_and_cache(self):
        r=YtDlpResolver();result=SimpleNamespace(returncode=0,stdout='https://r1.googlevideo.com/videoplayback?mime=audio%2Fwebm\n')
        with patch('audio_resolver.subprocess.run',return_value=result) as run:
            r.resolve('dQw4w9WgXcQ');r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,1)
            args=run.call_args.args[0];self.assertIn('bestaudio',args);self.assertIn('--ignore-config',args);self.assertIn('-g',args);self.assertEqual(run.call_args.kwargs['timeout'],15);self.assertNotIn('shell',run.call_args.kwargs)
            self.assertGreater(r.cache['dQw4w9WgXcQ']['expires'],time.time()+1790)
            r.cache['dQw4w9WgXcQ']['expires']=0;r.resolve('dQw4w9WgXcQ');self.assertEqual(run.call_count,2)
    def test_timeout_negative_cache_and_invalid_destinations(self):
        r=YtDlpResolver()
        with patch('audio_resolver.subprocess.run',side_effect=subprocess.TimeoutExpired('yt-dlp',15)) as run:
            for _ in range(2):
                with self.assertRaises(AudioUnavailable):r.resolve('dQw4w9WgXcQ')
            self.assertEqual(run.call_count,1)
        for url in ('http://r1.googlevideo.com/file','https://127.0.0.1/file','https://googlevideo.com.evil.test/file','https://user:pass@r1.googlevideo.com/file'):
            self.assertFalse(r.allowed(url))
        with patch('audio_resolver.subprocess.run') as run:
            with self.assertRaises(AudioUnavailable):r.resolve('--exec bad')
            run.assert_not_called()
