import io,struct,time,unittest
from unittest.mock import Mock,patch
from audio_partial import PartialMP4,PartialResponse,RangeSource,relocate_moov,PREFIX
from audio_prepared import PreparedAudioCache
from audio_resolver import AudioUnavailable

def box(kind,data):return struct.pack('>I4s',len(data)+8,kind)+data
FTYP=box(b'ftyp',b'isom0000')
def moov(offset,wide=False):
    table=box(b'co64' if wide else b'stco',b'\0'*4+struct.pack('>I',1)+struct.pack('>Q' if wide else '>I',offset))
    for kind in (b'stbl',b'minf',b'mdia',b'trak',b'moov'):table=box(kind,table)
    return table
ITEM={'url':'https://r1.googlevideo.com/file?mime=audio%2Fmp4','mime':'audio/mp4'}

class PartialTests(unittest.TestCase):
    def setUp(self):
        self.raw=FTYP+box(b'mdat',b'a'*(PREFIX+100000))+moov(len(FTYP)+8)
        self.calls=[];self.etag='stable';self.status=206
        self.patcher=patch('audio_partial.get_response',side_effect=self.get);self.patcher.start();self.addCleanup(self.patcher.stop)
    def get(self,url,headers,timeout=3):
        start,end=map(int,headers['Range'][6:].split('-'));end=min(end,len(self.raw)-1);self.calls.append((start,end))
        response=io.BytesIO(self.raw[start:end+1]);response.status=self.status;response.read1=response.read
        response.getheader=lambda name,default=None:{'Content-Type':'audio/mp4','Content-Range':f'bytes {start}-{end}/{len(self.raw)}','ETag':self.etag}.get(name,default)
        return Mock(),response
    def consume(self,response):
        parts=[]
        try:
            while True:
                data=response.read1(8192)
                if not data:break
                parts.append(data)
        finally:response.close()
        return b''.join(parts)
    def test_tail_moov_is_patched_without_fetching_full_song(self):
        view=PartialMP4(ITEM);before=len(self.calls);response=PartialResponse(view,0,len(self.raw)-1,False)
        first=response.read1(8192);self.assertEqual(len(self.calls),before)
        output=first+self.consume(response)
        self.assertEqual(len(output),len(self.raw));self.assertLess(output.index(b'moov'),output.index(b'mdat'))
        at=output.index(b'stco')+12;offset=struct.unpack_from('>I',output,at)[0]
        self.assertEqual(output[offset:offset+20],b'a'*20)
        self.assertEqual(view.prefix,output[:len(view.prefix)])
        self.assertLess(sum(end-start+1 for start,end in self.calls[:before]),len(self.raw))
    def test_byte_ranges_cross_cached_prefix_and_upstream(self):
        view=PartialMP4(ITEM);full=self.consume(PartialResponse(view,0,view.total-1,False))
        for start,end in [(0,1),(len(view.prefix)-4,len(view.prefix)+9),(view.total-20,view.total-1)]:
            response=PartialResponse(view,start,end,True)
            self.assertEqual(response.getheader('Content-Range'),f'bytes {start}-{end}/{len(full)}')
            self.assertEqual(self.consume(response),full[start:end+1])
    def test_front_metadata_and_fragmented_layout_remain_byte_exact(self):
        self.raw=FTYP+box(b'moov',box(b'mvex',b''))+box(b'moof',b'')+box(b'mdat',b'b'*(PREFIX+10))
        view=PartialMP4(ITEM);self.assertEqual(view.prefix,self.raw[:PREFIX])
        self.assertEqual(self.consume(PartialResponse(view,0,view.total-1,False)),self.raw)
    def test_changed_representation_is_rejected_at_continuation(self):
        view=PartialMP4(ITEM);self.etag='changed';response=PartialResponse(view,len(view.prefix),view.total-1,True)
        with self.assertRaisesRegex(OSError,'partial_continuation_failed'):response.read1()
        response.close()
    def test_ignored_range_is_not_cached(self):
        self.status=200
        with self.assertRaisesRegex(AudioUnavailable,'partial_range_mismatch'):PartialMP4(ITEM)
    def test_co64_offsets_and_overflow(self):
        original=moov(100,True);changed=relocate_moov(original,16,1000,2000)
        self.assertEqual(struct.unpack_from('>Q',changed,changed.index(b'co64')+12)[0],100+len(original))
        with self.assertRaisesRegex(ValueError,'chunk_offset_overflow'):relocate_moov(moov(2**32-10),16,2**32+5,2**32+100)
    def test_cache_partial_mode_never_runs_ffmpeg(self):
        cache=PreparedAudioCache(lambda *args:ITEM,partial=True);self.addCleanup(cache.close)
        with patch('audio_prepared.subprocess.Popen') as spawn:
            cache._prepare('abcdefghijk',{},time.perf_counter())
        spawn.assert_not_called();response=cache.open('abcdefghijk','bytes=0-1')
        self.assertEqual(response.ivox_timings['source'],'prepared-partial');self.assertEqual(response.read1(),FTYP[:2]);response.close()
        self.assertEqual(cache.status('abcdefghijk')['mode'],'partial');self.assertIsNone(cache.temp)

if __name__=='__main__':unittest.main()
