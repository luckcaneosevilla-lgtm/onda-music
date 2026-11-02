import io
from pathlib import Path
import struct
import time
import unittest
from unittest.mock import Mock,patch
from audio_prepared import PreparedAudioCache,validate_faststart
from audio_resolver import AudioUnavailable

VID='abcdefghijk';URL='https://r1.googlevideo.com/audio?mime=audio%2Fmp4'
def box(kind,data):return struct.pack('>I4s',8+len(data),kind)+data
FILE=box(b'ftyp',b'isom')+box(b'moov',b'metadata')+box(b'mdat',b'audio'*10)
class PreparedTests(unittest.TestCase):
    def cache(self,**kwargs):
        cache=PreparedAudioCache(lambda *a:{'url':URL,'mime':'audio/mp4'},binary='ffmpeg',**kwargs)
        self.addCleanup(cache.close);return cache
    def prepare(self,cache,payload=b'original',declared=None,result=FILE,hook=None):
        response=io.BytesIO(payload);response.status=200;response.read1=response.read
        response.getheader=lambda name,default=None:str(len(payload) if declared is None else declared) if name=='Content-Length' else default
        def spawn(command,**kwargs):
            self.assertIn('aac',command);self.assertIn('128k',command);self.assertIn('+faststart',command);self.assertNotIn(URL,command)
            Path(command[-1]).write_bytes(result)
            process=Mock();process.returncode=0;process.poll.return_value=None;self.last_process=process
            def wait(**kw):
                if process.poll.return_value is None and hook:hook()
                process.poll.return_value=0;return 0
            process.wait.side_effect=wait
            process.kill.side_effect=lambda:setattr(process.poll,'return_value',0)
            return process
        with patch('audio_prepared.get_response',return_value=(Mock(),response)),patch('audio_prepared.subprocess.Popen',side_effect=spawn):
            cache._prepare(VID,{},time.perf_counter())
    def test_atomic_publish_and_byte_ranges(self):
        cache=self.cache();self.prepare(cache,hook=lambda:self.assertIsNone(cache.open(VID)))
        for header,start,end in [(None,0,len(FILE)-1),('bytes=0-1',0,1),('bytes=10-',10,len(FILE)-1),('bytes=-4',len(FILE)-4,len(FILE)-1)]:
            response=cache.open(VID,header)
            try:
                self.assertEqual(response.status,206 if header else 200)
                self.assertEqual(response.getheader('Content-Type'),'audio/mp4')
                self.assertEqual(response.read1(10000),FILE[start:end+1]);self.assertEqual(response.read1(),b'')
            finally:response.close();response.close()
        self.assertEqual(cache.entries[VID]['leases'],0)
    def test_unsatisfied_ranges(self):
        cache=self.cache();self.prepare(cache)
        for header in ('bytes=-0','bytes=9999-','bytes=3-1','bytes=0-1,3-4','bytes=-'):
            response=cache.open(VID,header);self.assertEqual(response.status,416);self.assertEqual(response.getheader('Content-Range'),f'bytes */{len(FILE)}');response.close()
    def test_expired_file_is_kept_until_active_reader_closes(self):
        cache=self.cache();self.prepare(cache);path=cache.entries[VID]['path'];response=cache.open(VID)
        cache.invalidate(VID);self.assertIsNone(cache.open(VID));self.assertTrue(path.exists());self.assertEqual(response.read1(2),FILE[:2])
        response.close();self.assertFalse(path.exists());self.assertNotIn(VID,cache.entries)
    def test_download_size_and_truncation_never_publish(self):
        for settings,declared in [({'max_file':2},None),({},99)]:
            cache=self.cache(**settings)
            with self.assertRaises(AudioUnavailable):self.prepare(cache,declared=declared)
            self.assertFalse(cache.entries);self.assertEqual(list(Path(cache.temp.name).iterdir()),[])
    def test_bad_atom_layout_never_publishes(self):
        cache=self.cache()
        with self.assertRaises(ValueError):self.prepare(cache,result=box(b'ftyp',b'isom')+box(b'mdat',b'audio')+box(b'moov',b'metadata'))
        self.assertFalse(cache.entries);self.assertEqual(list(Path(cache.temp.name).iterdir()),[])
    def test_lru_disk_budget_and_missing_file_recovery(self):
        cache=self.cache(max_entries=1);self.prepare(cache);old=cache.entries[VID]['path']
        with cache.lock:cache._prune(needed=len(FILE))
        self.assertFalse(old.exists());self.assertIsNone(cache.open(VID))
        self.prepare(cache);cache.entries[VID]['path'].unlink();self.assertIsNone(cache.open(VID));self.assertNotIn(VID,cache.entries)
    def test_queue_is_bounded_and_deduplicated(self):
        cache=self.cache();items=[{'provider':'piped','provider_id':f'{i:011d}'} for i in range(10)]
        with patch('audio_prepared.threading.Thread') as thread:
            cache.prefetch_many([items[0],items[0],*items[1:]])
            self.assertEqual(len(cache.pending),3);thread.return_value.start.assert_called_once()
            cache.prefetch_many(items[7:]);self.assertEqual([v['provider_id'] for v in cache.pending],[v['provider_id'] for v in items[7:]])
    def test_timeout_kills_encoder_and_removes_partial_files(self):
        import subprocess
        cache=self.cache()
        with self.assertRaises(subprocess.TimeoutExpired):self.prepare(cache,hook=lambda:(_ for _ in ()).throw(subprocess.TimeoutExpired('ffmpeg',1)))
        self.last_process.kill.assert_called_once();self.assertIsNone(cache.process)
        self.assertFalse(cache.entries);self.assertEqual(list(Path(cache.temp.name).iterdir()),[])

if __name__=='__main__':unittest.main()
