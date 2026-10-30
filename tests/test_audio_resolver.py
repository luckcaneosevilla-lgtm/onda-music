import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from audio_resolver import YtDlpResolver,AudioUnavailable,OPTIONS,WORKER

URL='https://r1.googlevideo.com/videoplayback?mime=audio%2Fmp4'

class Process:
    def __init__(self,url=URL,hang=False,delay=0):
        self.url=url;self.hang=hang;self.delay=delay;self.returncode=None
        self.stdin=io.StringIO();self.stdout=io.StringIO();self.inputs=[];self.reaped=False
    def communicate(self,data,timeout=None):
        self.inputs.append(data.strip());time.sleep(self.delay)
        if self.hang:raise subprocess.TimeoutExpired('yt-dlp',timeout)
        self.returncode=0
        return json.dumps({'id':data.split()[0],'url':self.url,'engine':'yt-dlp-direct'}),None
    def wait(self,timeout=None):self.reaped=True;self.returncode=0

class ResolverTests(unittest.TestCase):
    def setUp(self):
        cleanup=patch('audio_resolver.kill_process_tree',side_effect=lambda p:p.wait())
        cleanup.start();self.addCleanup(cleanup.stop)

    def test_one_shot_child_exits_after_one_request(self):
        fake='''class YoutubeDL:
    def __init__(self,options):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def extract_info(self,url,download=False):return {'url':'https://r1.googlevideo.com/audio?mime=audio%2Fmp4'}
'''
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'yt_dlp.py').write_text(fake,encoding='utf-8')
            env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'};env['PYTHONPATH']=directory
            result=subprocess.run([sys.executable,'-c',WORKER,json.dumps(OPTIONS)],input='dQw4w9WgXcQ\nkJQP7kiw5Fk\n',
                                  capture_output=True,text=True,env=env,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(result.returncode,0,result.stderr)
            lines=result.stdout.splitlines();self.assertEqual(len(lines),1)
            self.assertEqual(json.loads(lines[0])['engine'],'yt-dlp-direct')

    def test_no_process_on_construction_or_prefetch(self):
        with patch('audio_resolver.subprocess.Popen') as spawn:
            resolver=YtDlpResolver();resolver.prefetch('dQw4w9WgXcQ');resolver.prefetch_many(['kJQP7kiw5Fk'])
            spawn.assert_not_called()
            self.assertFalse(hasattr(resolver,'idle'));self.assertFalse(hasattr(resolver,'start'))

    def test_one_child_per_uncached_fallback_and_no_resident_process(self):
        resolver=YtDlpResolver();children=[Process(),Process()]
        with patch('audio_resolver.subprocess.Popen',side_effect=children) as spawn:
            resolver.resolve('dQw4w9WgXcQ');resolver.resolve('dQw4w9WgXcQ');resolver.resolve('kJQP7kiw5Fk')
            self.assertEqual(spawn.call_count,2)
            for child in children:
                self.assertTrue(child.reaped);self.assertTrue(child.stdin.closed);self.assertTrue(child.stdout.closed)
            args=spawn.call_args.args[0];self.assertEqual(json.loads(args[-1])['http_chunk_size'],8*1024*1024)
            self.assertNotIn('shell',spawn.call_args.kwargs)

    def test_timeout_reaps_child_and_releases_slot(self):
        resolver=YtDlpResolver();hung=Process(hang=True);fresh=Process()
        with patch('audio_resolver.subprocess.Popen',side_effect=[hung,fresh]) as spawn:
            with self.assertRaisesRegex(AudioUnavailable,'extraction_timeout'):resolver.resolve('dQw4w9WgXcQ')
            self.assertTrue(hung.reaped);self.assertTrue(hung.stdout.closed)
            with self.assertRaises(AudioUnavailable):resolver.resolve('dQw4w9WgXcQ')
            self.assertEqual(resolver.resolve('kJQP7kiw5Fk')['mime'],'audio/mp4');self.assertEqual(spawn.call_count,2)

    def test_concurrent_fallbacks_are_serialized(self):
        resolver=YtDlpResolver();active=0;peak=0;guard=threading.Lock();errors=[]
        class Child(Process):
            def communicate(self,*args,**kwargs):
                nonlocal active,peak
                with guard:active+=1;peak=max(peak,active)
                try:return super().communicate(*args,**kwargs)
                finally:
                    with guard:active-=1
        def play(vid):
            try:resolver.resolve(vid)
            except Exception as error:errors.append(str(error))
        with patch('audio_resolver.subprocess.Popen',side_effect=lambda *a,**kw:Child(delay=.05)):
            threads=[threading.Thread(target=play,args=(vid,)) for vid in ('dQw4w9WgXcQ','kJQP7kiw5Fk')]
            for thread in threads:thread.start()
            for thread in threads:thread.join(3)
        self.assertFalse(errors);self.assertEqual(peak,1)

    def test_busy_slot_is_not_cached_as_unplayable(self):
        resolver=YtDlpResolver()
        with patch.object(resolver,'run',side_effect=[AudioUnavailable('worker_unavailable'),URL]):
            with self.assertRaises(AudioUnavailable):resolver.resolve('dQw4w9WgXcQ')
            self.assertNotIn('dQw4w9WgXcQ',resolver.failures)
            self.assertEqual(resolver.resolve('dQw4w9WgXcQ')['url'],URL)

    def test_explicit_refresh_clears_failure_once(self):
        resolver=YtDlpResolver();vid='dQw4w9WgXcQ'
        with patch.object(resolver,'run',side_effect=[AudioUnavailable('format_unavailable'),URL]) as run:
            with self.assertRaises(AudioUnavailable):resolver.resolve(vid)
            self.assertTrue(resolver.refresh(vid));self.assertEqual(resolver.resolve(vid)['url'],URL)
            self.assertFalse(resolver.refresh(vid));self.assertEqual(run.call_count,2)

    def test_persistent_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audio.sqlite';resolver=YtDlpResolver();resolver.persist(path)
            with patch.object(resolver,'run',return_value=URL):resolver.resolve('dQw4w9WgXcQ')
            restarted=YtDlpResolver();restarted.persist(path)
            with patch.object(restarted,'run') as run:
                self.assertEqual(restarted.resolve('dQw4w9WgXcQ')['mime'],'audio/mp4');run.assert_not_called()
            restarted.invalidate('dQw4w9WgXcQ');again=YtDlpResolver();again.persist(path)
            self.assertNotIn('dQw4w9WgXcQ',again.cache)

    def test_invalid_id_and_oversized_or_untrusted_url(self):
        resolver=YtDlpResolver()
        for url in ('http://r1.googlevideo.com/file','https://127.0.0.1/file','https://googlevideo.com.evil.test/file',
                    'https://user:pass@r1.googlevideo.com/file',URL+'x'*8192):self.assertFalse(resolver.allowed(url))
        with patch.object(resolver,'run',return_value='https://evil.test/audio') as run:
            with self.assertRaises(AudioUnavailable):resolver.resolve('--exec bad')
            run.assert_not_called()
            with self.assertRaisesRegex(AudioUnavailable,'invalid_stream_url'):resolver.resolve('dQw4w9WgXcQ')

if __name__=='__main__':unittest.main()
