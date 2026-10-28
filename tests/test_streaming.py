import io
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from streaming import PrefixCache,prefix_response
from audio_resolver import YtDlpResolver
from audio_cache import SharedAudioCache

URL='https://r1.googlevideo.com/videoplayback?mime=audio%2Fmp4'

class Response:
    def __init__(self,body=b'',status=206,headers=None):
        self.status=status;self.body=io.BytesIO(body);self.headers={k.lower():v for k,v in (headers or {}).items()}
    def getheader(self,name,default=None):return self.headers.get(name.lower(),default)
    def read(self,size=-1):return self.body.read(size)
    read1=read

class StreamingTests(unittest.TestCase):
    def item(self):return {'url':URL,'mime':'audio/mp4','expires':time.time()+1000,'data':b'abcd','total':10,'ETag':'fixed'}
    def test_prefix_is_immediate_then_exact_continuation(self):
        conn=Mock();open_=Mock(return_value=(conn,Response(b'efghij',headers={'Content-Range':'bytes 4-9/10','Content-Type':'audio/mp4','ETag':'fixed'})))
        response=prefix_response(self.item(),'bytes=0-',open_)
        self.assertEqual(response.read1(65536),b'abcd');open_.assert_not_called()
        self.assertEqual(response.read(20),b'efghij')
        self.assertEqual(response.getheader('Content-Length'),'10')
        self.assertEqual(response.getheader('Content-Range'),'bytes 0-9/10')
        self.assertEqual(open_.call_args.args[1],{'Range':'bytes=4-9'})
        response.close();conn.close.assert_called_once()
    def test_seek_inside_prefix_and_outside_and_suffix(self):
        open_=Mock()
        response=prefix_response(self.item(),'bytes=1-2',open_)
        self.assertEqual(response.read(),b'bc');open_.assert_not_called()
        self.assertEqual(response.getheader('Content-Range'),'bytes 1-2/10')
        self.assertIsNone(prefix_response(self.item(),'bytes=5-',open_))
        self.assertIsNone(prefix_response(self.item(),'bytes=-2',open_))
        self.assertIsNone(prefix_response(self.item(),'bytes=8-2',open_))
        self.assertIsNone(prefix_response(self.item(),'bytes=-',open_))
        self.assertEqual(prefix_response(self.item(),None,open_).status,200)
    def test_refuses_mismatched_representation_and_ignored_range(self):
        for response in [Response(b'bad',status=200),Response(b'efghij',headers={'Content-Range':'bytes 4-9/10','Content-Type':'audio/mp4','ETag':'changed'}),Response(b'bad',headers={'Content-Range':'bytes 5-9/10','Content-Type':'audio/mp4','ETag':'fixed'})]:
            conn=Mock();reader=prefix_response(self.item(),None,Mock(return_value=(conn,response)))
            self.assertEqual(reader.read1(),b'abcd')
            with self.assertRaises(OSError):reader.read1()
            conn.close.assert_called_once()
    def test_prefix_cache_is_bounded_and_expires_and_checks_url(self):
        cache=PrefixCache(limit=8,fragment=4)
        for name in ('a','b','c'):cache.put(name,self.item())
        self.assertEqual(cache.size,8);self.assertIsNone(cache.get('a',URL))
        self.assertIsNone(cache.get('b',URL+'&different=1'));self.assertEqual(cache.size,4)
        expired=self.item();expired['expires']=time.time()+30;cache.put('soon',expired)
        self.assertIsNone(cache.get('soon',URL))
    def test_prefetch_reads_only_fragment_and_ignores_200(self):
        resolver=YtDlpResolver();resolver.prefixes=PrefixCache(fragment=4)
        item=self.item();conn=Mock();response=Response(b'abcdefghij',headers={'Content-Range':'bytes 0-3/10','Content-Type':'audio/mp4'})
        with patch.object(resolver,'resolve',return_value=item),patch('audio_resolver.get_response',return_value=(conn,response)):
            resolver.prefetch_fragment('dQw4w9WgXcQ')
        self.assertEqual(resolver.prefixes.get('dQw4w9WgXcQ',URL)['data'],b'abcd')
        self.assertEqual(response.body.tell(),4);conn.close.assert_called_once()
        resolver.prefixes.remove('dQw4w9WgXcQ');response=Response(b'abcdefghij',status=200)
        with patch.object(resolver,'resolve',return_value=item),patch('audio_resolver.get_response',return_value=(Mock(),response)):
            resolver.prefetch_fragment('dQw4w9WgXcQ')
        self.assertEqual(response.body.tell(),0);self.assertIsNone(resolver.prefixes.get('dQw4w9WgXcQ',URL))
    def test_expiry_honours_provider_even_when_ttl_is_two_hours(self):
        resolver=YtDlpResolver();expiry=int(time.time()+900)
        with patch.object(resolver,'run',return_value=URL+'&expire='+str(expiry)):
            item=resolver.resolve('dQw4w9WgXcQ')
        self.assertEqual(item['expires'],expiry-120)
        self.assertEqual(resolver.cache_ttl,7200)
    def test_cache_hit_does_not_wait_for_background_refresh(self):
        resolver=YtDlpResolver();resolver.cache['dQw4w9WgXcQ']=self.item()
        lock=threading.Lock();lock.acquire();resolver.locks['dQw4w9WgXcQ']=[lock,1]
        started=time.monotonic()
        try:self.assertEqual(resolver.resolve('dQw4w9WgXcQ')['url'],URL)
        finally:lock.release()
        self.assertLess(time.monotonic()-started,.1)
    def test_shared_cache_scope_hydration_and_invalidation(self):
        # Same connection contract as Turso; no account or external service touched.
        class Connection(sqlite3.Connection):
            def __exit__(self,*args):
                try:return super().__exit__(*args)
                finally:self.close()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'shared.sqlite';connect=lambda:sqlite3.connect(path,factory=Connection)
            merged=[];cache=SharedAudioCache(connect,'egress-a',lambda rows:merged.extend(rows))
            try:
                cache.enqueue('dQw4w9WgXcQ',self.item())
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    try:
                        with connect() as c:
                            rows=c.execute('SELECT * FROM audio_resolved_urls').fetchall()
                        if rows:break
                    except sqlite3.Error:pass
                    time.sleep(.02)
                self.assertEqual(rows[0][0],'egress-a')
                with connect() as c:cache.sync(c)
                self.assertEqual(merged[-1][0],'dQw4w9WgXcQ')
                other=[];separate=SharedAudioCache(connect,'egress-b',lambda rows:other.extend(rows))
                separate.close();separate.thread.join(2);self.assertEqual(other,[])
                cache.enqueue('dQw4w9WgXcQ',None)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    with connect() as c:rows=c.execute('SELECT * FROM audio_resolved_urls').fetchall()
                    if not rows:break
                    time.sleep(.02)
                self.assertEqual(rows,[])
            finally:cache.close();cache.thread.join(2)

if __name__=='__main__':unittest.main()
