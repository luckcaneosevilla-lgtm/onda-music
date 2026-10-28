"""Eventual shared URL cache. All Turso I/O stays off playback threads.

URLs can be tied to an egress IP. Only replicas with an explicitly shared scope
and the same public egress may reuse entries. Prefix bytes remain node-local.
"""
import queue
import threading
import time
from streaming import metric

class SharedAudioCache:
    def __init__(self,connect,scope,merge):
        self.connect=connect;self.scope=scope;self.merge=merge
        self.pending=queue.Queue(maxsize=600);self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def enqueue(self,video_id,item):
        try:self.pending.put_nowait((video_id,item))
        except queue.Full:metric('audio_cache_queue_full')
    def sync(self,c):
        c.execute('DELETE FROM audio_resolved_urls WHERE expires<=?',(time.time(),))
        rows=c.execute('SELECT video_id,url,mime,expires FROM audio_resolved_urls WHERE scope=? AND expires>? ORDER BY expires DESC LIMIT 300',(self.scope,time.time()+120)).fetchall()
        self.merge(rows)
    def run(self):
        retry=1
        while not self.stopped.is_set():
            inflight=None
            try:
                with self.connect() as c:
                    c.execute('CREATE TABLE IF NOT EXISTS audio_resolved_urls(scope TEXT NOT NULL,video_id TEXT NOT NULL,url TEXT NOT NULL,mime TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(scope,video_id))')
                    self.sync(c)
                next_sync=time.monotonic()+30;retry=1
                while not self.stopped.is_set():
                    try:video_id,item=self.pending.get(timeout=1)
                    except queue.Empty:video_id=None
                    if video_id is not None:
                        inflight=(video_id,item)
                        with self.connect() as c:
                            if item:c.execute('INSERT OR REPLACE INTO audio_resolved_urls VALUES(?,?,?,?,?)',(self.scope,video_id,item['url'],item['mime'],item['expires']))
                            else:c.execute('DELETE FROM audio_resolved_urls WHERE scope=? AND video_id=?',(self.scope,video_id))
                        inflight=None
                    if time.monotonic()>=next_sync:
                        with self.connect() as c:self.sync(c)
                        next_sync=time.monotonic()+30
            except Exception as error:
                if inflight:self.enqueue(*inflight)
                metric('audio_cache_error',error=type(error).__name__)
                self.stopped.wait(retry);retry=min(60,retry*2)
    def close(self):self.stopped.set()
