"""Minimal Turso (libSQL) client speaking Hrana over HTTP, shaped like sqlite3.

Only the slice of the sqlite3 API that server.py uses is implemented. Writes are
buffered and sent together right before the next read or on commit, so a request
that saves twenty catalog rows costs one network round trip instead of twenty.
Outside an explicit BEGIN the buffered writes are applied as one atomic batch;
after BEGIN they run inside a real server-side transaction held by a baton.
"""
import base64
import http.client
import json
import sqlite3
import threading
import time
from urllib.parse import urlparse

WRITES = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'DROP', 'ALTER')
# Turso connections are fresh per stream, so the pragma travels with every request.
PRAGMA = {'sql': 'PRAGMA foreign_keys=ON'}
# One writer at a time inside this process mirrors SQLite's local write lock and
# keeps concurrent requests from failing with SQLITE_BUSY on the remote primary.
write_lock = threading.RLock()

def encode(value):
    if value is None: return {'type': 'null'}
    if isinstance(value, bool) or isinstance(value, int): return {'type': 'integer', 'value': str(int(value))}
    if isinstance(value, float): return {'type': 'float', 'value': value}
    if isinstance(value, (bytes, bytearray, memoryview)): return {'type': 'blob', 'base64': base64.b64encode(bytes(value)).decode()}
    return {'type': 'text', 'value': str(value)}

def decode(value):
    kind = value['type']
    if kind == 'null': return None
    if kind == 'integer': return int(value['value'])
    if kind == 'float': return float(value['value'])
    if kind == 'blob': data = value.get('base64', ''); return base64.b64decode(data + '=' * (-len(data) % 4))  # sent unpadded
    return value['value']

def statement(sql, args=()):
    return {'sql': sql, 'args': [encode(a) for a in args]}

def failure(error):
    message = error.get('message', 'Turso error'); code = error.get('code') or ''
    return (sqlite3.IntegrityError if 'CONSTRAINT' in code or 'constraint failed' in message else sqlite3.OperationalError)(message)

class Row:
    __slots__ = ('_names', '_values')
    def __init__(self, names, values): self._names = names; self._values = values
    def keys(self): return list(self._names)
    def __getitem__(self, key): return self._values[key if isinstance(key, int) else self._names.index(key)]
    def __iter__(self): return iter(self._values)
    def __len__(self): return len(self._values)

class Cursor:
    def __init__(self, result=None):
        names = [col.get('name') for col in result['cols']] if result else []
        self.rows = [Row(names, [decode(v) for v in row]) for row in result['rows']] if result else []
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def fetchall(self): rows, self.rows = self.rows, []; return rows
    def __iter__(self): return iter(self.fetchall())

class Client:
    def __init__(self, url, token):
        parsed = urlparse(url.replace('libsql://', 'https://', 1))
        self.host = parsed.netloc; self.secure = parsed.scheme == 'https'
        self.headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}

        # The HTTP server starts a thread per request, so per-thread connections paid a
        # ~0.5 s TLS handshake on almost every API call. Warm connections are pooled
        # and shared instead; idle ones are dropped before the server can close them.
        self.pool = []; self.pool_guard = threading.Lock()

    IDLE_SECONDS = 20
    POOL_SIZE = 8

    def acquire(self, fresh=False, idle=IDLE_SECONDS):
        if not fresh:
            stamp = time.monotonic()
            with self.pool_guard:
                while self.pool:
                    conn, used = self.pool.pop()
                    if stamp - used < idle: return conn
                    conn.close()
        factory = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        return factory(self.host, timeout=30)

    def release(self, conn):
        with self.pool_guard:
            if len(self.pool) < self.POOL_SIZE: self.pool.append((conn, time.monotonic())); return
        conn.close()

    def warm(self):
        """Open one connection ahead of the first request (TLS handshake off the hot path)."""
        try: self.pipeline([{'type': 'execute', 'stmt': {'sql': 'SELECT 1'}}, {'type': 'close'}])
        except sqlite3.Error: pass

    def pipeline(self, requests, baton=None):
        body = json.dumps({'baton': baton, 'requests': requests})
        for attempt in (0, 1):
            # Inside a transaction a retry is unsafe, so it never rides a possibly stale socket.
            conn = self.acquire(fresh=attempt > 0, idle=5 if baton else self.IDLE_SECONDS)
            try:
                conn.request('POST', '/v2/pipeline', body, self.headers)
                response = conn.getresponse(); raw = response.read()
                self.release(conn)
                break
            except (OSError, http.client.HTTPException):
                conn.close()
                # Retrying is only safe when no server-side transaction is in flight.
                if attempt or baton: raise sqlite3.OperationalError('No se pudo contactar con Turso.')
        if response.status != 200:
            raise sqlite3.OperationalError(f'Turso respondió {response.status}: {raw[:200].decode(errors="replace")}')
        data = json.loads(raw)
        return data.get('baton'), data['results']

def responses(items):
    for item in items:
        if item['type'] == 'error': raise failure(item['error'])
    return [item['response'] for item in items]

class Connection:
    def __init__(self, client):
        self.client = client; self.pending = []; self.baton = None; self.locked = False

    def __enter__(self): return self
    def __exit__(self, kind, *args):
        try:
            if kind is None: self.commit()
            else: self.rollback()
        finally: self.close()

    def execute(self, sql, args=()):
        word = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ''
        if word == 'BEGIN':
            self.flush()
            write_lock.acquire(); self.locked = True
            try:
                self.baton, items = self.client.pipeline([{'type': 'execute', 'stmt': PRAGMA}, {'type': 'execute', 'stmt': statement(sql)}])
                responses(items)
            except Exception: self.rollback() if self.baton else self.release(); raise
            return Cursor()
        if word in WRITES:
            self.pending.append(statement(sql, args)); return Cursor()
        return Cursor(self.send(self.pending + [statement(sql, args)])[-1]['result'])

    def executescript(self, script):
        self.pending.extend(statement(s) for s in script.split(';') if s.strip())
        self.flush()

    def send(self, stmts):
        """Run stmts in order and return their responses, honouring the open transaction."""
        self.pending = []
        if self.baton:
            # Keep the new baton even on error so rollback reaches the live stream.
            self.baton, items = self.client.pipeline([{'type': 'execute', 'stmt': s} for s in stmts], self.baton)
            return responses(items)
        writes = [s for s in stmts if s['sql'].lstrip().split(None, 1)[0].upper() in WRITES]
        requests = [{'type': 'batch', 'batch': self.atomic(writes)}] if writes else []
        reads = stmts[len(writes):]
        requests += [{'type': 'execute', 'stmt': s} for s in reads]
        if writes: write_lock.acquire()
        try: results = responses(self.client.pipeline(requests + [{'type': 'close'}])[1])
        finally:
            if writes: write_lock.release()
        if writes: self.check(results[0]['result'])
        return results[1 if writes else 0:-1]

    @staticmethod
    def atomic(stmts):
        # BEGIN, every statement only if the previous one succeeded, then COMMIT,
        # falling back to ROLLBACK so a failed write leaves no partial changes.
        steps = [{'stmt': PRAGMA}, {'stmt': {'sql': 'BEGIN'}}]
        for s in stmts: steps.append({'stmt': s, 'condition': {'type': 'ok', 'step': len(steps) - 1}})
        steps.append({'stmt': {'sql': 'COMMIT'}, 'condition': {'type': 'ok', 'step': len(steps) - 1}})
        steps.append({'stmt': {'sql': 'ROLLBACK'}, 'condition': {'type': 'not', 'cond': {'type': 'ok', 'step': len(steps) - 1}}})
        return {'steps': steps}

    @staticmethod
    def check(batch):
        for error in batch['step_errors']:
            if error: raise failure(error)

    def flush(self):
        if self.pending: self.send(list(self.pending))

    def commit(self):
        if not self.baton: return self.flush()
        try: self.send(self.pending + [{'sql': 'COMMIT'}])
        finally: self.end()

    def rollback(self):
        self.pending = []
        if not self.baton: return
        try: self.client.pipeline([{'type': 'execute', 'stmt': {'sql': 'ROLLBACK'}}], self.baton)
        except sqlite3.Error: pass
        finally: self.end()

    def end(self):
        baton, self.baton = self.baton, None
        try:
            if baton: self.client.pipeline([{'type': 'close'}], baton)
        except sqlite3.Error: pass
        finally: self.release()

    def release(self):
        if self.locked: self.locked = False; write_lock.release()

    def close(self):
        if self.baton: self.rollback()
        self.pending = []
