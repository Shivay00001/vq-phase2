#!/usr/bin/env python3
"""
VisionCloud v0 slice — S3-style object storage on local disk.
Stdlib only (no pip dependencies). See docs/ for PRD/TRD/ERD/API contracts.

Run:  python3 vc_server.py [--port 8471] [--data ./data]
Test keys: set VC_ADMIN_SECRET env var for a known admin secret (dev/tests only).
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- config
MAX_OBJECT_BYTES = int(os.environ.get('VC_MAX_OBJECT_BYTES', 64 * 1024 * 1024))  # single-object cap (v0)
MULTIPART_MIN_PART = 5 * 1024 * 1024         # S3-style 5 MB minimum (except last)
MAX_PART_NUMBER = 10000
CLOCK_SKEW_SECONDS = 15 * 60                 # auth timestamp window
BUCKET_RE = re.compile(r'^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$')
SCHEMA = """
CREATE TABLE IF NOT EXISTS access_keys(
  key_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, secret_test TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0,
  quota_bytes INTEGER NOT NULL DEFAULT 1073741824,
  quota_reqs_per_day INTEGER NOT NULL DEFAULT 10000,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS buckets(
  name TEXT PRIMARY KEY, owner_key_id TEXT NOT NULL, created_at TEXT NOT NULL,
  object_count INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS objects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, bucket TEXT NOT NULL, obj_key TEXT NOT NULL,
  blob_hash TEXT NOT NULL, size INTEGER NOT NULL, etag TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(bucket, obj_key));
CREATE TABLE IF NOT EXISTS multipart_uploads(
  upload_id TEXT PRIMARY KEY, bucket TEXT NOT NULL, obj_key TEXT NOT NULL,
  owner_key_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS multipart_parts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, upload_id TEXT NOT NULL,
  part_number INTEGER NOT NULL, blob_hash TEXT NOT NULL,
  size INTEGER NOT NULL, etag TEXT NOT NULL,
  UNIQUE(upload_id, part_number));
CREATE TABLE IF NOT EXISTS key_storage(
  key_id TEXT PRIMARY KEY, bytes_stored INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key_id TEXT NOT NULL, day TEXT NOT NULL,
  req_count INTEGER NOT NULL DEFAULT 0, UNIQUE(key_id, day));
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor_key_id TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_objects_bucket ON objects(bucket);
CREATE INDEX IF NOT EXISTS idx_parts_upload ON multipart_parts(upload_id);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hmac_hex(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- storage backend
class LocalDiskBackend:
    """Dev backend: blobs on local disk, content-addressed. Production would
    swap this for an erasure-coded distributed store (same interface)."""

    def __init__(self, data_dir):
        self.blob_dir = os.path.join(data_dir, 'blobs')
        os.makedirs(self.blob_dir, exist_ok=True)

    def blob_path(self, blob_hash):
        # Hash is hex-only by construction; double-guard anyway.
        if not re.fullmatch(r'[0-9a-f]{64}', blob_hash):
            raise ValueError('bad blob hash')
        return os.path.join(self.blob_dir, blob_hash + '.bin')

    def put(self, data: bytes) -> str:
        h = sha256_hex(data)
        p = self.blob_path(h)
        if not os.path.exists(p):
            tmp = p + '.tmp-' + secrets.token_hex(8)
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, p)
        return h

    def get(self, blob_hash: str) -> bytes:
        with open(self.blob_path(blob_hash), 'rb') as f:
            return f.read()

    def delete_if_orphan(self, blob_hash: str, still_referenced: bool):
        if not still_referenced:
            try:
                os.remove(self.blob_path(blob_hash))
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- server state
class VisionCloud:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, 'visioncloud.db')
        self.backend = LocalDiskBackend(data_dir)
        self.lock = threading.Lock()
        self._init_db()

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        return c

    def _init_db(self):
        with self.lock, self.connect() as c:
            c.executescript(SCHEMA)
            row = c.execute('SELECT key_id FROM access_keys WHERE key_id=?',
                            ('admin',)).fetchone()
            if not row:
                secret = os.environ.get('VC_ADMIN_SECRET') or secrets.token_hex(24)
                c.execute(
                    'INSERT INTO access_keys(key_id,secret_hash,secret_test,is_admin,'
                    'revoked,quota_bytes,quota_reqs_per_day,created_at)'
                    ' VALUES(?,?,?,?,?,?,?,?)',
                    ('admin', sha256_hex(secret.encode()), secret, 1, 0,
                     10 * 1024**3, 1000000, utcnow()))
                c.execute('INSERT INTO key_storage(key_id,bytes_stored,updated_at)'
                          ' VALUES(?,?,?)', ('admin', 0, utcnow()))
                c.commit()
                if not os.environ.get('VC_ADMIN_SECRET'):
                    print('*** FIRST RUN: admin secret (save it, shown once):',
                          secret, flush=True)
                    print('*** (or set VC_ADMIN_SECRET env var for a known secret)',
                          flush=True)

    # ---------------------------------------------------------- key helpers
    def get_key(self, c, key_id):
        return c.execute('SELECT * FROM access_keys WHERE key_id=?',
                         (key_id,)).fetchone()

    def audit(self, c, actor, action, target='', detail=''):
        c.execute('INSERT INTO audit_log(actor_key_id,action,target,detail,created_at)'
                  ' VALUES(?,?,?,?,?)', (actor, action, target, detail, utcnow()))

    # ---------------------------------------------------------- quota
    def check_and_count_request(self, c, key):
        """Increment daily request counter; return (allowed, retry_after_note)."""
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        c.execute('INSERT INTO usage_ledger(key_id,day,req_count) VALUES(?,?,1)'
                  ' ON CONFLICT(key_id,day) DO UPDATE SET req_count=req_count+1',
                  (key['key_id'], day))
        n = c.execute('SELECT req_count FROM usage_ledger WHERE key_id=? AND day=?',
                      (key['key_id'], day)).fetchone()['req_count']
        return n <= key['quota_reqs_per_day'], n

    def storage_used(self, c, key_id):
        r = c.execute('SELECT bytes_stored FROM key_storage WHERE key_id=?',
                      (key_id,)).fetchone()
        return r['bytes_stored'] if r else 0

    def add_storage(self, c, key_id, delta):
        c.execute('INSERT INTO key_storage(key_id,bytes_stored,updated_at)'
                  ' VALUES(?,?,?) ON CONFLICT(key_id) DO UPDATE SET'
                  ' bytes_stored=bytes_stored+excluded.bytes_stored,'
                  ' updated_at=excluded.updated_at',
                  (key_id, delta, utcnow()))

    # ---------------------------------------------------------- validation
    @staticmethod
    def valid_bucket(name):
        return bool(BUCKET_RE.match(name or ''))

    @staticmethod
    def valid_key(key):
        if not key or len(key) > 1024:
            return False
        if key.startswith('/') or '//' in key:
            return False
        if any(seg in ('.', '..') for seg in key.split('/')):
            return False
        if '\x00' in key:
            return False
        return True

    # ---------------------------------------------------------- auth
    def _canonical(self, method, path, query, date, body_hash):
        qs = '&'.join(f'{k}={query[k]}' for k in sorted(query))
        return f'{method}\n{path}\n{qs}\n{date}\n{body_hash}'

    def verify_header_auth(self, c, method, raw_path, query, headers, body):
        """Returns (key_row, error_code). error_code None on success."""
        auth = headers.get('authorization', '')
        m = re.fullmatch(r'VC-HMAC-SHA256 Credential=([A-Za-z0-9_\-]{1,128}), '
                         r'Signature=([0-9a-f]{64})', auth.strip())
        if not m:
            return None, 'Forbidden'
        key_id, sig = m.group(1), m.group(2)
        key = self.get_key(c, key_id)
        # Uniform failure: no oracle on key existence.
        if not key or key['revoked']:
            return None, 'Forbidden'
        date = headers.get('x-vc-date', '')
        try:
            ts = int(date)
        except ValueError:
            return None, 'Forbidden'
        if abs(time.time() - ts) > CLOCK_SKEW_SECONDS:
            return None, 'Forbidden'
        secret = key['secret_test']
        if not secret:
            return None, 'Forbidden'  # production: HSM lookup; v0 keeps test secret
        canon = self._canonical(method, raw_path, query, date, sha256_hex(body))
        expect = hmac_hex(secret, 'VC-HMAC-SHA256\n' + canon)
        if not hmac.compare_digest(expect, sig):
            return None, 'Forbidden'
        return key, None

    def verify_presigned(self, c, method, raw_path, query):
        """Presigned GET/HEAD only. Returns (key_row, bucket, key, error)."""
        try:
            exp = int(query.get('X-VC-Expires', ''))
            key_id = query['X-VC-KeyId']
            sig = query['X-VC-Signature']
        except (KeyError, ValueError):
            return None, None, None, 'Forbidden'
        if exp < time.time():
            return None, None, None, 'Forbidden'
        if not re.fullmatch(r'[0-9a-f]{64}', sig or ''):
            return None, None, None, 'Forbidden'
        key = self.get_key(c, key_id)
        if not key or key['revoked']:
            return None, None, None, 'Forbidden'
        secret = key['secret_test']
        if not secret:
            return None, None, None, 'Forbidden'
        expect = hmac_hex(secret, f'{method}\n{raw_path}\n{exp}')
        if not hmac.compare_digest(expect, sig):
            return None, None, None, 'Forbidden'
        return key, None, None, None


# ---------------------------------------------------------------- HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = 'VisionCloud/0.1'
    vc: VisionCloud = None  # set at startup
    console_dir: str = ''

    # -- helpers -------------------------------------------------
    def log_message(self, *a):
        pass  # quiet; audit_log covers admin actions

    def _send(self, code, obj=None, ctype='application/json', extra=None):
        body = b'' if obj is None else (
            obj if isinstance(obj, bytes) else json.dumps(obj).encode())
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _err(self, code, err_code, message):
        self._send(code, {'error': {'code': err_code, 'message': message}})

    def _parse(self):
        u = urllib.parse.urlsplit(self.path)
        # raw_path: percent-decoded EXCEPT we keep it safe; keys with %2F stay slashes
        raw_path = urllib.parse.unquote(u.path)
        query = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        return raw_path, query

    def _read_body(self, limit=MAX_OBJECT_BYTES + 1024 * 1024):
        n = int(self.headers.get('Content-Length') or 0)
        if n > limit:
            return None  # too large
        return self.rfile.read(n) if n else b''

    # -- auth gate -----------------------------------------------
    def _auth(self, c, method, raw_path, query, body):
        """Returns (key_row, via_presigned: bool, error)."""
        if method in ('GET', 'HEAD') and 'X-VC-Signature' in query:
            key, _, _, err = self.vc.verify_presigned(c, method, raw_path, query)
            return key, True, err
        headers = {k.lower(): v for k, v in self.headers.items()}
        key, err = self.vc.verify_header_auth(c, method, raw_path, query,
                                              headers, body)
        return key, False, err

    # -- main dispatch --------------------------------------------
    def _route(self, method):
        raw_path, query = self._parse()
        body = self._read_body()
        if body is None:
            self._err(413, 'EntityTooLarge',
                      f'body exceeds {MAX_OBJECT_BYTES} bytes cap')
            return
        # Console UI + health need no auth
        if raw_path == '/console' or raw_path == '/console/':
            return self._serve_console('index.html')
        if raw_path.startswith('/console/'):
            return self._serve_console(raw_path[len('/console/'):])
        if raw_path == '/health':
            return self._send(200, {'status': 'ok', 'version': '0.1-slice'})

        vc = self.vc
        c = vc.connect()
        try:
            with vc.lock:
                key, presigned, err = self._auth(c, method, raw_path, query, body)
                if err:
                    self._err(403, err, 'request not authorized')
                    return
                allowed, n = vc.check_and_count_request(c, key)
                if not allowed:
                    c.commit()
                    self._err(429, 'RateLimited',
                              f'daily request quota exceeded ({n})')
                    return
                try:
                    self._dispatch(c, key, presigned, method, raw_path, query, body)
                    c.commit()
                except _HTTPError as e:
                    c.rollback()
                    self._err(e.code, e.err_code, e.message)
                except Exception:
                    c.rollback()
                    self._err(500, 'InternalError', 'unexpected server error')
        finally:
            c.close()

    def _dispatch(self, c, key, presigned, method, raw_path, query, body):
        vc = self.vc
        parts = [p for p in raw_path.split('/') if p != '']
        # ---- /admin/* ----
        if parts and parts[0] == 'admin':
            if presigned:
                raise _HTTPError(403, 'Forbidden', 'request not authorized')
            if not key['is_admin']:
                raise _HTTPError(403, 'Forbidden', 'request not authorized')
            return self._admin(c, key, method, parts[1:], query, body)
        # ---- /support/* /partner/* : deny-by-default stubs ----
        if parts and parts[0] in ('support', 'partner'):
            raise _HTTPError(403, 'NotImplemented',
                             'role not enabled in v0 slice')
        # ---- bucket / object routes ----
        if not parts:
            if method == 'GET':
                return self._list_buckets(c, key, query)
            raise _HTTPError(405, 'MethodNotAllowed', 'method not allowed')
        bucket = parts[0]
        obj_key = '/'.join(parts[1:]) if len(parts) > 1 else None

        if presigned and (obj_key is None or method not in ('GET', 'HEAD')):
            raise _HTTPError(403, 'Forbidden', 'request not authorized')
        if not vc.valid_bucket(bucket):
            raise _HTTPError(400, 'InvalidBucketName', 'invalid bucket name')

        if obj_key is None:
            if method == 'PUT':
                return self._create_bucket(c, key, bucket)
            if method == 'GET' and 'list' in query:
                return self._list_objects(c, key, bucket, query)
            if method == 'DELETE':
                return self._delete_bucket(c, key, bucket, query)
            if method == 'GET':
                # S3-ism: GET on bucket without ?list -> treat as list for console ease
                return self._list_objects(c, key, bucket, query)
            raise _HTTPError(405, 'MethodNotAllowed', 'method not allowed')

        if not vc.valid_key(obj_key):
            raise _HTTPError(400, 'InvalidKey', 'invalid object key')
        if 'uploads' in query and method == 'POST':
            return self._mp_init(c, key, bucket, obj_key)
        if 'uploadId' in query:
            uid = query['uploadId']
            if method == 'PUT' and 'partNumber' in query:
                return self._mp_put_part(c, key, bucket, obj_key, uid, query, body)
            if method == 'POST':
                return self._mp_complete(c, key, bucket, obj_key, uid, body)
            if method == 'DELETE':
                return self._mp_abort(c, key, bucket, obj_key, uid)
            raise _HTTPError(405, 'MethodNotAllowed', 'method not allowed')
        if method == 'PUT':
            return self._put_object(c, key, bucket, obj_key, body,
                                    self.headers.get('Content-Type'))
        if method in ('GET', 'HEAD'):
            return self._get_object(c, key, bucket, obj_key, presigned)
        if method == 'DELETE':
            return self._delete_object(c, key, bucket, obj_key)
        raise _HTTPError(405, 'MethodNotAllowed', 'method not allowed')

    # -- ownership ------------------------------------------------
    def _bucket_row(self, c, bucket):
        return c.execute('SELECT * FROM buckets WHERE name=?', (bucket,)).fetchone()

    def _require_owner(self, c, key, bucket):
        b = self._bucket_row(c, bucket)
        if not b:
            raise _HTTPError(404, 'NoSuchBucket', 'bucket does not exist')
        if not key['is_admin'] and b['owner_key_id'] != key['key_id']:
            raise _HTTPError(403, 'Forbidden', 'request not authorized')
        return b

    # -- buckets ---------------------------------------------------
    def _create_bucket(self, c, key, bucket):
        if self._bucket_row(c, bucket):
            raise _HTTPError(409, 'BucketAlreadyExists', 'bucket already exists')
        now = utcnow()
        c.execute('INSERT INTO buckets(name,owner_key_id,created_at) VALUES(?,?,?)',
                  (bucket, key['key_id'], now))
        self._send(200, {'name': bucket, 'created_at': now})

    def _list_buckets(self, c, key, query):
        if key['is_admin'] and query.get('all') == '1':
            rows = c.execute('SELECT * FROM buckets ORDER BY name').fetchall()
        else:
            rows = c.execute('SELECT * FROM buckets WHERE owner_key_id=? ORDER BY name',
                             (key['key_id'],)).fetchall()
        self._send(200, {'buckets': [
            {'name': r['name'], 'objects': r['object_count'],
             'bytes': r['bytes_total'], 'created_at': r['created_at']}
            for r in rows]})

    def _delete_bucket(self, c, key, bucket, query):
        b = self._require_owner(c, key, bucket)
        if b['object_count'] > 0 and not (key['is_admin'] and query.get('force') == '1'):
            raise _HTTPError(409, 'BucketNotEmpty', 'bucket not empty')
        if b['object_count'] > 0:  # admin force: wipe objects
            for r in c.execute('SELECT blob_hash,size FROM objects WHERE bucket=?',
                               (bucket,)):
                self.vc.add_storage(c, b['owner_key_id'], -r['size'])
                self._release_blob(c, r['blob_hash'])
            c.execute('DELETE FROM objects WHERE bucket=?', (bucket,))
        c.execute('DELETE FROM buckets WHERE name=?', (bucket,))
        self.vc.audit(c, key['key_id'], 'delete_bucket', bucket)
        self._send(204)

    # -- objects ---------------------------------------------------
    def _release_blob(self, c, blob_hash):
        """Delete the blob file only when no object/part row references it.
        Pure garbage collection — storage accounting is done explicitly by
        callers via add_storage(), so there is exactly one accounting site
        per mutation (no double counting)."""
        n = c.execute('SELECT COUNT(*) n FROM objects WHERE blob_hash=?',
                      (blob_hash,)).fetchone()['n']
        n += c.execute('SELECT COUNT(*) n FROM multipart_parts WHERE blob_hash=?',
                       (blob_hash,)).fetchone()['n']
        # NOTE: bytes are counted once per object row even if the blob file is
        # shared; the file is removed only when no row references it.
        self.vc.backend.delete_if_orphan(blob_hash, n > 0)

    def _put_object(self, c, key, bucket, obj_key, body, ctype):
        b = self._require_owner(c, key, bucket)
        if len(body) > MAX_OBJECT_BYTES:
            raise _HTTPError(413, 'EntityTooLarge',
                             f'object exceeds {MAX_OBJECT_BYTES} bytes cap')
        vc = self.vc
        old = c.execute('SELECT size,blob_hash FROM objects WHERE bucket=? AND obj_key=?',
                        (bucket, obj_key)).fetchone()
        delta = len(body) - (old['size'] if old else 0)
        if vc.storage_used(c, key['key_id']) + delta > key['quota_bytes']:
            raise _HTTPError(403, 'QuotaExceeded', 'storage quota exceeded')
        blob = vc.backend.put(body)
        etag = sha256_hex(body)
        now = utcnow()
        if old:
            c.execute('UPDATE objects SET blob_hash=?,size=?,etag=?,content_type=?,'
                      'updated_at=? WHERE bucket=? AND obj_key=?',
                      (blob, len(body), etag,
                       ctype or 'application/octet-stream', now, bucket, obj_key))
            self._release_blob(c, old['blob_hash'])
            c.execute('UPDATE buckets SET bytes_total=bytes_total+? WHERE name=?',
                      (delta, bucket))
        else:
            c.execute('INSERT INTO objects(bucket,obj_key,blob_hash,size,etag,'
                      'content_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                      (bucket, obj_key, blob, len(body), etag,
                       ctype or 'application/octet-stream', now, now))
            c.execute('UPDATE buckets SET object_count=object_count+1,'
                      ' bytes_total=bytes_total+? WHERE name=?', (len(body), bucket))
        vc.add_storage(c, key['key_id'], delta)
        self._send(200, {'key': obj_key, 'size': len(body), 'etag': etag},
                   extra={'ETag': etag})

    def _get_object(self, c, key, bucket, obj_key, presigned):
        b = self._require_owner(c, key, bucket)
        r = c.execute('SELECT * FROM objects WHERE bucket=? AND obj_key=?',
                      (bucket, obj_key)).fetchone()
        if not r:
            raise _HTTPError(404, 'NoSuchKey', 'object does not exist')
        data = self.vc.backend.get(r['blob_hash'])
        rng = self.headers.get('Range')
        start, end = 0, len(data) - 1
        code = 200
        if rng:
            m = re.fullmatch(r'bytes=(\d*)-(\d*)', rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                raise _HTTPError(416, 'InvalidRange', 'invalid range')
            if m.group(1) and m.group(2):
                start, end = int(m.group(1)), int(m.group(2))
            elif m.group(1):
                start, end = int(m.group(1)), len(data) - 1
            else:
                start, end = len(data) - int(m.group(2)), len(data) - 1
            if start > end or start >= len(data):
                raise _HTTPError(416, 'InvalidRange', 'range not satisfiable')
            end = min(end, len(data) - 1)
            data = data[start:end + 1]
            code = 206
        extra = {'ETag': r['etag'], 'Accept-Ranges': 'bytes'}
        if code == 206:
            extra['Content-Range'] = f'bytes {start}-{end}/{r["size"]}'
        self._send(code, data, ctype=r['content_type'], extra=extra)

    def _delete_object(self, c, key, bucket, obj_key):
        b = self._require_owner(c, key, bucket)
        r = c.execute('SELECT size,blob_hash FROM objects WHERE bucket=? AND obj_key=?',
                      (bucket, obj_key)).fetchone()
        if r:
            c.execute('DELETE FROM objects WHERE bucket=? AND obj_key=?',
                      (bucket, obj_key))
            self.vc.add_storage(c, b['owner_key_id'], -r['size'])
            self._release_blob(c, r['blob_hash'])
            c.execute('UPDATE buckets SET object_count=object_count-1,'
                      ' bytes_total=bytes_total-? WHERE name=?', (r['size'], bucket))
        self._send(204)  # idempotent, S3-style

    def _list_objects(self, c, key, bucket, query):
        self._require_owner(c, key, bucket)
        prefix = query.get('prefix', '')
        delimiter = query.get('delimiter', '')
        marker = query.get('marker', '')
        try:
            max_keys = min(int(query.get('max-keys', '100')), 1000)
        except ValueError:
            raise _HTTPError(400, 'InvalidArgument', 'bad max-keys')
        rows = c.execute(
            'SELECT obj_key,size,etag,content_type,updated_at FROM objects'
            ' WHERE bucket=? AND obj_key>? ORDER BY obj_key',
            (bucket, marker)).fetchall()
        objs, prefixes, seen = [], [], set()
        for r in rows:
            k = r['obj_key']
            if prefix and not k.startswith(prefix):
                continue
            disp = k
            if delimiter:
                rest = k[len(prefix):]
                if delimiter in rest:
                    cp = prefix + rest.split(delimiter)[0] + delimiter
                    if cp not in seen:
                        seen.add(cp)
                        prefixes.append(cp)
                    continue
            objs.append({'key': k, 'size': r['size'], 'etag': r['etag'],
                         'content_type': r['content_type'],
                         'updated_at': r['updated_at']})
            if len(objs) >= max_keys + 1:
                break
        truncated = len(objs) > max_keys
        objs = objs[:max_keys]
        next_marker = objs[-1]['key'] if truncated else ''
        self._send(200, {'objects': objs, 'common_prefixes': sorted(prefixes),
                         'truncated': truncated, 'next_marker': next_marker})

    # -- multipart -------------------------------------------------
    def _mp_get(self, c, key, bucket, obj_key, uid):
        self._require_owner(c, key, bucket)
        u = c.execute('SELECT * FROM multipart_uploads WHERE upload_id=?',
                      (uid,)).fetchone()
        if not u or u['bucket'] != bucket or u['obj_key'] != obj_key:
            raise _HTTPError(404, 'NoSuchUpload', 'upload not found')
        return u

    def _mp_init(self, c, key, bucket, obj_key):
        self._require_owner(c, key, bucket)
        uid = secrets.token_hex(16)
        c.execute('INSERT INTO multipart_uploads(upload_id,bucket,obj_key,'
                  'owner_key_id,created_at) VALUES(?,?,?,?,?)',
                  (uid, bucket, obj_key, key['key_id'], utcnow()))
        self._send(200, {'upload_id': uid})

    def _mp_put_part(self, c, key, bucket, obj_key, uid, query, body):
        u = self._mp_get(c, key, bucket, obj_key, uid)
        try:
            n = int(query.get('partNumber', '0'))
        except ValueError:
            n = 0
        if not 1 <= n <= MAX_PART_NUMBER:
            raise _HTTPError(400, 'InvalidArgument', 'bad partNumber')
        if len(body) > MAX_OBJECT_BYTES:
            raise _HTTPError(413, 'EntityTooLarge', 'part too large')
        blob = self.vc.backend.put(body)
        etag = sha256_hex(body)
        old = c.execute('SELECT size,blob_hash FROM multipart_parts WHERE upload_id=?'
                        ' AND part_number=?', (uid, n)).fetchone()
        if old:  # re-upload of same part: release old blob
            self._release_blob(c, old['blob_hash'])
            c.execute('UPDATE multipart_parts SET blob_hash=?,size=?,etag=?'
                      ' WHERE upload_id=? AND part_number=?',
                      (blob, len(body), etag, uid, n))
        else:
            c.execute('INSERT INTO multipart_parts(upload_id,part_number,blob_hash,'
                      'size,etag) VALUES(?,?,?,?,?)', (uid, n, blob, len(body), etag))
        self.vc.add_storage(c, u['owner_key_id'],
                            len(body) - (old['size'] if old else 0))
        self._send(200, {'etag': etag}, extra={'ETag': etag})

    def _mp_complete(self, c, key, bucket, obj_key, uid, body):
        u = self._mp_get(c, key, bucket, obj_key, uid)
        try:
            spec = json.loads(body.decode() or '{}')
            want = [(int(p['part_number']), p['etag']) for p in spec.get('parts', [])]
        except (ValueError, KeyError, TypeError):
            raise _HTTPError(400, 'InvalidArgument', 'bad parts list')
        if not want:
            raise _HTTPError(400, 'InvalidArgument', 'no parts given')
        stored = {r['part_number']: (r['etag'], r['blob_hash'], r['size'])
                  for r in c.execute('SELECT * FROM multipart_parts WHERE upload_id=?',
                                     (uid,))}
        total, chunks = 0, []
        for i, (n, etag) in enumerate(want):
            if n not in stored or stored[n][0] != etag:
                raise _HTTPError(400, 'InvalidPart', f'part {n} etag mismatch')
            size = stored[n][2]
            if i < len(want) - 1 and size < MULTIPART_MIN_PART:
                raise _HTTPError(400, 'EntityTooSmall',
                                 f'part {n} smaller than 5 MB minimum')
            chunks.append(self.vc.backend.get(stored[n][1]))
            total += size
        if total > MAX_OBJECT_BYTES:
            raise _HTTPError(413, 'EntityTooLarge', 'assembled object too large')
        vc = self.vc
        b = self._bucket_row(c, bucket)
        old = c.execute('SELECT size,blob_hash FROM objects WHERE bucket=? AND obj_key=?',
                        (bucket, obj_key)).fetchone()
        staged_total = sum(sz for (_, _, sz) in stored.values())
        # Staged part bytes are already counted in storage_used; completing
        # swaps them for the final object: new_usage = used - staged + total.
        if vc.storage_used(c, key['key_id']) - staged_total + total > key['quota_bytes']:
            raise _HTTPError(403, 'QuotaExceeded', 'storage quota exceeded')
        blob = vc.backend.put(b''.join(chunks))
        etag = sha256_hex(b''.join(chunks))
        now = utcnow()
        if old:
            c.execute('UPDATE objects SET blob_hash=?,size=?,etag=?,updated_at=?'
                      ' WHERE bucket=? AND obj_key=?',
                      (blob, total, etag, now, bucket, obj_key))
            self._release_blob(c, old['blob_hash'])
            c.execute('UPDATE buckets SET bytes_total=bytes_total+? WHERE name=?',
                      (total - old['size'], bucket))
        else:
            c.execute('INSERT INTO objects(bucket,obj_key,blob_hash,size,etag,'
                      'content_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                      (bucket, obj_key, blob, total, etag,
                       'application/octet-stream', now, now))
            c.execute('UPDATE buckets SET object_count=object_count+1,'
                      ' bytes_total=bytes_total+? WHERE name=?', (total, bucket))
        # Delete part rows first so orphan detection sees zero references,
        # then GC the files. Unlisted staged parts are discarded on complete
        # (documented) so they can't leak as unbilled orphans.
        c.execute('DELETE FROM multipart_parts WHERE upload_id=?', (uid,))
        for _, (_, bh, _) in stored.items():
            self._release_blob(c, bh)
        c.execute('DELETE FROM multipart_uploads WHERE upload_id=?', (uid,))
        vc.add_storage(c, u['owner_key_id'],
                       -staged_total + (total - (old['size'] if old else 0)))
        self._send(200, {'key': obj_key, 'size': total, 'etag': etag})

    def _mp_abort(self, c, key, bucket, obj_key, uid):
        u = self._mp_get(c, key, bucket, obj_key, uid)
        staged = list(c.execute('SELECT blob_hash,size FROM multipart_parts'
                                ' WHERE upload_id=?', (uid,)))
        c.execute('DELETE FROM multipart_parts WHERE upload_id=?', (uid,))
        for r in staged:
            self.vc.add_storage(c, u['owner_key_id'], -r['size'])
            self._release_blob(c, r['blob_hash'])
        c.execute('DELETE FROM multipart_uploads WHERE upload_id=?', (uid,))
        self._send(204)

    # -- admin ------------------------------------------------------
    def _admin(self, c, key, method, rest, query, body):
        vc = self.vc
        if rest[:1] == ['keys'] and len(rest) == 1:
            if method == 'POST':
                try:
                    spec = json.loads(body.decode() or '{}')
                except ValueError:
                    raise _HTTPError(400, 'InvalidArgument', 'bad JSON')
                key_id = spec.get('key_id') or ('vk_' + secrets.token_hex(8))
                if not re.fullmatch(r'[A-Za-z0-9_\-]{1,128}', key_id):
                    raise _HTTPError(400, 'InvalidArgument', 'bad key_id')
                if vc.get_key(c, key_id):
                    raise _HTTPError(409, 'KeyAlreadyExists', 'key already exists')
                secret = 'vcs_' + secrets.token_hex(24)
                c.execute('INSERT INTO access_keys(key_id,secret_hash,secret_test,'
                          'is_admin,revoked,quota_bytes,quota_reqs_per_day,created_at)'
                          ' VALUES(?,?,?,?,?,?,?,?)',
                          (key_id, sha256_hex(secret.encode()), secret, 0, 0,
                           int(spec.get('quota_bytes', 1073741824)),
                           int(spec.get('quota_reqs_per_day', 10000)), utcnow()))
                c.execute('INSERT INTO key_storage(key_id,bytes_stored,updated_at)'
                          ' VALUES(?,?,?)', (key_id, 0, utcnow()))
                vc.audit(c, key['key_id'], 'create_key', key_id)
                self._send(201, {'key_id': key_id, 'secret': secret,
                                 'note': 'secret shown once; v0 dev convenience'})
                return
            if method == 'GET':
                rows = c.execute('SELECT key_id,is_admin,revoked,quota_bytes,'
                                 'quota_reqs_per_day,created_at FROM access_keys'
                                 ' ORDER BY created_at').fetchall()
                self._send(200, {'keys': [dict(r) for r in rows]})
                return
        if rest[:2] == ['keys', rest[1] if len(rest) > 1 else ''] and len(rest) == 3 \
                and rest[2] == 'revoke' and method == 'POST':
            target = vc.get_key(c, rest[1])
            if not target:
                raise _HTTPError(404, 'NoSuchKey', 'key does not exist')
            if target['key_id'] == 'admin':
                raise _HTTPError(400, 'InvalidArgument', 'cannot revoke admin')
            c.execute('UPDATE access_keys SET revoked=1 WHERE key_id=?',
                      (target['key_id'],))
            vc.audit(c, key['key_id'], 'revoke_key', target['key_id'])
            self._send(200, {'revoked': target['key_id']})
            return
        if rest[:1] == ['usage'] and method == 'GET':
            q = 'SELECT * FROM usage_ledger WHERE 1=1'
            args = []
            if query.get('key_id'):
                q += ' AND key_id=?'
                args.append(query['key_id'])
            if query.get('day'):
                q += ' AND day=?'
                args.append(query['day'])
            rows = c.execute(q + ' ORDER BY day DESC, key_id', args).fetchall()
            stor = {r['key_id']: r['bytes_stored'] for r in
                    c.execute('SELECT * FROM key_storage').fetchall()}
            self._send(200, {'usage': [dict(r) for r in rows],
                             'bytes_stored': stor})
            return
        if rest[:1] == ['audit'] and method == 'GET':
            try:
                lim = min(int(query.get('limit', '50')), 500)
            except ValueError:
                lim = 50
            rows = c.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?',
                             (lim,)).fetchall()
            self._send(200, {'audit': [dict(r) for r in rows]})
            return
        raise _HTTPError(404, 'NotFound', 'no such admin endpoint')

    # -- console -----------------------------------------------------
    def _serve_console(self, name):
        if '/' in name or '\\' in name or name.startswith('.'):
            self._err(400, 'InvalidArgument', 'bad console path')
            return
        p = os.path.join(self.console_dir, name or 'index.html')
        if not os.path.isfile(p):
            self._err(404, 'NotFound', 'console file not found')
            return
        ctype = 'text/html' if name.endswith('.html') else \
            'application/javascript' if name.endswith('.js') else \
            'text/css' if name.endswith('.css') else 'application/octet-stream'
        with open(p, 'rb') as f:
            self._send(200, f.read(), ctype=ctype)

    # -- verbs --------------------------------------------------------
    def do_GET(self):
        self._route('GET')

    def do_HEAD(self):
        self._route('HEAD')

    def do_PUT(self):
        self._route('PUT')

    def do_POST(self):
        self._route('POST')

    def do_DELETE(self):
        self._route('DELETE')


class _HTTPError(Exception):
    def __init__(self, code, err_code, message):
        super().__init__(message)
        self.code, self.err_code, self.message = code, err_code, message


def main():
    ap = argparse.ArgumentParser(description='VisionCloud v0 slice')
    ap.add_argument('--port', type=int, default=8471)
    ap.add_argument('--data', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'data'))
    ap.add_argument('--console', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'console'))
    args = ap.parse_args()
    vc = VisionCloud(args.data)
    Handler.vc = vc
    Handler.console_dir = args.console
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'VisionCloud v0 slice on http://127.0.0.1:{args.port} '
          f'(console: /console/, health: /health)', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
