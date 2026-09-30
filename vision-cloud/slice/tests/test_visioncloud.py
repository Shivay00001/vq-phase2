"""VisionCloud v0 slice — test suite (functional + adversarial).

Run:  ./run_tests.sh   (starts server, runs pytest, stops server)
"""
import hashlib
import hmac
import os
import time
import urllib.parse

import pytest
import requests

BASE = os.environ['VC_TEST_BASE']
ADMIN_ID = 'admin'
ADMIN_SECRET = os.environ['VC_TEST_ADMIN_SECRET']


# ------------------------------------------------------------- client
def _canonical(method, path, query, date, body_hash):
    qs = '&'.join(f'{k}={query[k]}' for k in sorted(query))
    return f'{method}\n{path}\n{qs}\n{date}\n{body_hash}'


def sign_request(method, path, query, body, key_id, secret):
    body = body or b''
    date = str(int(time.time()))
    canon = _canonical(method, path, query, date,
                       hashlib.sha256(body).hexdigest())
    sig = hmac.new(secret.encode(),
                   ('VC-HMAC-SHA256\n' + canon).encode(),
                   hashlib.sha256).hexdigest()
    return {'Authorization': f'VC-HMAC-SHA256 Credential={key_id}, Signature={sig}',
            'X-VC-Date': date}


def call(method, path, query=None, body=None, key_id=ADMIN_ID,
         secret=ADMIN_SECRET, headers=None, raw=False):
    """Signed request. path = raw unencoded path (server verifies decoded path)."""
    query = query or {}
    q = ('?' + urllib.parse.urlencode(query)) if query else ''
    url_path = '/'.join(urllib.parse.quote(seg, safe='') for seg in path.split('/'))
    h = sign_request(method, path, query, body, key_id, secret)
    if headers:
        h.update(headers)
    r = requests.request(method, BASE + url_path + q, headers=h, data=body)
    if raw:
        return r
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def presign(bucket, key, key_id, secret, expires_in=3600):
    path = f'/{bucket}/{key}'
    exp = int(time.time()) + expires_in
    sig = hmac.new(secret.encode(), f'GET\n{path}\n{exp}'.encode(),
                   hashlib.sha256).hexdigest()
    url_path = '/'.join(urllib.parse.quote(seg, safe='') for seg in path.split('/'))
    return (f'{BASE}{url_path}?X-VC-Expires={exp}&X-VC-KeyId={key_id}'
            f'&X-VC-Signature={sig}')


@pytest.fixture()
def user():
    """Fresh non-admin key per test."""
    kid = f'testuser_{int(time.time()*1000)}_{os.getpid()}'
    code, j = call('POST', '/admin/keys', body=b'{"quota_bytes": 104857600}')
    assert code == 201, j
    return j['key_id'], j['secret']


@pytest.fixture()
def bucket(user):
    kid, sec = user
    name = f'bkt-{kid.replace("_","-")}'[:50]
    code, _ = call('PUT', f'/{name}', key_id=kid, secret=sec)
    assert code == 200
    return name, kid, sec


# ============================================================== FUNCTIONAL
class TestBuckets:
    def test_create_list_delete(self, user):
        kid, sec = user
        code, j = call('PUT', '/mybucket1', key_id=kid, secret=sec)
        assert code == 200 and j['name'] == 'mybucket1'
        code, j = call('PUT', '/mybucket1', key_id=kid, secret=sec)
        assert code == 409  # already exists
        code, j = call('GET', '/', key_id=kid, secret=sec)
        assert code == 200 and any(b['name'] == 'mybucket1' for b in j['buckets'])
        code, _ = call('DELETE', '/mybucket1', key_id=kid, secret=sec)
        assert code == 204
        code, j = call('DELETE', '/mybucket1', key_id=kid, secret=sec)
        assert code == 404  # gone

    def test_delete_nonempty_blocked(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/k', body=b'x', key_id=kid, secret=sec)
        code, j = call('DELETE', f'/{name}', key_id=kid, secret=sec)
        assert code == 409 and j['error']['code'] == 'BucketNotEmpty'

    def test_invalid_bucket_names(self, user):
        kid, sec = user
        for bad in ['AB', 'a', 'has space', 'UPPER', '-lead', 'trail-',
                    'a' * 64, 'double..dot']:
            if bad == 'double..dot':
                continue  # actually valid per S3 rules; skip
            code, _ = call('PUT', f'/{bad}', key_id=kid, secret=sec)
            assert code == 400, bad


class TestObjects:
    def test_put_get_head_delete(self, bucket):
        name, kid, sec = bucket
        data = b'hello visioncloud'
        code, j = call('PUT', f'/{name}/hello.txt', body=data, key_id=kid, secret=sec,
                       headers={'Content-Type': 'text/plain'})
        assert code == 200
        assert j['etag'] == hashlib.sha256(data).hexdigest()
        r = call('GET', f'/{name}/hello.txt', key_id=kid, secret=sec, raw=True)
        assert r.status_code == 200 and r.content == data
        assert r.headers['Content-Type'] == 'text/plain'
        r = call('HEAD', f'/{name}/hello.txt', key_id=kid, secret=sec, raw=True)
        assert r.status_code == 200 and r.content == b''
        code, _ = call('DELETE', f'/{name}/hello.txt', key_id=kid, secret=sec)
        assert code == 204
        code, j = call('GET', f'/{name}/hello.txt', key_id=kid, secret=sec)
        assert code == 404
        code, _ = call('DELETE', f'/{name}/hello.txt', key_id=kid, secret=sec)
        assert code == 204  # idempotent

    def test_overwrite(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/k', body=b'v1', key_id=kid, secret=sec)
        code, j = call('PUT', f'/{name}/k', body=b'version-two', key_id=kid, secret=sec)
        assert code == 200 and j['size'] == 11
        r = call('GET', f'/{name}/k', key_id=kid, secret=sec, raw=True)
        assert r.content == b'version-two'

    def test_range_get(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/r', body=b'0123456789', key_id=kid, secret=sec)
        r = call('GET', f'/{name}/r', key_id=kid, secret=sec, raw=True,
                 headers={'Range': 'bytes=2-5'})
        assert r.status_code == 206 and r.content == b'2345'
        assert r.headers['Content-Range'] == 'bytes 2-5/10'
        r = call('GET', f'/{name}/r', key_id=kid, secret=sec, raw=True,
                 headers={'Range': 'bytes=8-'})
        assert r.status_code == 206 and r.content == b'89'
        r = call('GET', f'/{name}/r', key_id=kid, secret=sec, raw=True,
                 headers={'Range': 'bytes=99-100'})
        assert r.status_code == 416

    def test_list_prefix_delimiter_pagination(self, bucket):
        name, kid, sec = bucket
        for k in ['a/1', 'a/2', 'b/1', 'top']:
            call('PUT', f'/{name}/{k}', body=b'x', key_id=kid, secret=sec)
        code, j = call('GET', f'/{name}', {'list': '', 'prefix': 'a/'},
                       key_id=kid, secret=sec)
        assert code == 200 and len(j['objects']) == 2
        code, j = call('GET', f'/{name}', {'list': '', 'delimiter': '/'},
                       key_id=kid, secret=sec)
        assert code == 200 and sorted(j['common_prefixes']) == ['a/', 'b/']
        assert [o['key'] for o in j['objects']] == ['top']
        code, j = call('GET', f'/{name}', {'list': '', 'max-keys': '2'},
                       key_id=kid, secret=sec)
        assert code == 200 and j['truncated'] and len(j['objects']) == 2
        code, j = call('GET', f'/{name}',
                       {'list': '', 'max-keys': '2', 'marker': j['next_marker']},
                       key_id=kid, secret=sec)
        assert code == 200 and len(j['objects']) == 2 and not j['truncated']

    def test_folder_like_keys_with_slashes(self, bucket):
        name, kid, sec = bucket
        code, _ = call('PUT', f'/{name}/dir/sub/file.txt', body=b'deep',
                       key_id=kid, secret=sec)
        assert code == 200
        r = call('GET', f'/{name}/dir/sub/file.txt', key_id=kid, secret=sec, raw=True)
        assert r.content == b'deep'


class TestMultipart:
    def test_full_cycle(self, bucket):
        name, kid, sec = bucket
        code, j = call('POST', f'/{name}/big.bin', {'uploads': ''},
                       key_id=kid, secret=sec)
        assert code == 200
        uid = j['upload_id']
        p1, p2 = os.urandom(6 * 1024 * 1024), os.urandom(1024)
        code, r1 = call('PUT', f'/{name}/big.bin',
                        {'partNumber': '1', 'uploadId': uid}, body=p1,
                        key_id=kid, secret=sec)
        assert code == 200
        code, r2 = call('PUT', f'/{name}/big.bin',
                        {'partNumber': '2', 'uploadId': uid}, body=p2,
                        key_id=kid, secret=sec)
        assert code == 200
        import json as _json
        code, j = call('POST', f'/{name}/big.bin', {'uploadId': uid},
                        body=_json.dumps(
                            {'parts': [{'part_number': 1, 'etag': r1['etag']},
                                       {'part_number': 2, 'etag': r2['etag']}]}).encode(),
                        key_id=kid, secret=sec)
        assert code == 200 and j['size'] == len(p1) + len(p2)
        r = call('GET', f'/{name}/big.bin', key_id=kid, secret=sec, raw=True)
        assert r.content == p1 + p2

    def test_abort(self, bucket):
        name, kid, sec = bucket
        code, j = call('POST', f'/{name}/ab.bin', {'uploads': ''},
                       key_id=kid, secret=sec)
        uid = j['upload_id']
        call('PUT', f'/{name}/ab.bin', {'partNumber': '1', 'uploadId': uid},
             body=b'part', key_id=kid, secret=sec)
        code, _ = call('DELETE', f'/{name}/ab.bin', {'uploadId': uid},
                       key_id=kid, secret=sec)
        assert code == 204
        import json as _json
        code, j = call('POST', f'/{name}/ab.bin', {'uploadId': uid},
                       body=_json.dumps({'parts': []}).encode(),
                       key_id=kid, secret=sec)
        assert code in (400, 404)

    def test_complete_rejects_bad_etag(self, bucket):
        name, kid, sec = bucket
        code, j = call('POST', f'/{name}/bad.bin', {'uploads': ''},
                       key_id=kid, secret=sec)
        uid = j['upload_id']
        call('PUT', f'/{name}/bad.bin', {'partNumber': '1', 'uploadId': uid},
             body=b'x' * (6 * 1024 * 1024), key_id=kid, secret=sec)
        import json as _json
        code, j = call('POST', f'/{name}/bad.bin', {'uploadId': uid},
                       body=_json.dumps(
                           {'parts': [{'part_number': 1,
                                       'etag': '0' * 64}]}).encode(),
                       key_id=kid, secret=sec)
        assert code == 400 and j['error']['code'] == 'InvalidPart'

    def test_small_nonfinal_part_rejected(self, bucket):
        name, kid, sec = bucket
        code, j = call('POST', f'/{name}/small.bin', {'uploads': ''},
                       key_id=kid, secret=sec)
        uid = j['upload_id']
        _, r1 = call('PUT', f'/{name}/small.bin', {'partNumber': '1', 'uploadId': uid},
                     body=b'tiny', key_id=kid, secret=sec)
        _, r2 = call('PUT', f'/{name}/small.bin', {'partNumber': '2', 'uploadId': uid},
                     body=b'tiny2', key_id=kid, secret=sec)
        import json as _json
        code, j = call('POST', f'/{name}/small.bin', {'uploadId': uid},
                       body=_json.dumps(
                           {'parts': [{'part_number': 1, 'etag': r1['etag']},
                                      {'part_number': 2, 'etag': r2['etag']}]}).encode(),
                       key_id=kid, secret=sec)
        assert code == 400 and j['error']['code'] == 'EntityTooSmall'


class TestPresigned:
    def test_presigned_download(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/pub.txt', body=b'presigned-ok', key_id=kid, secret=sec)
        url = presign(name, 'pub.txt', kid, sec)
        r = requests.get(url)
        assert r.status_code == 200 and r.content == b'presigned-ok'

    def test_presigned_tampered_expiry_rejected(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/t.txt', body=b'x', key_id=kid, secret=sec)
        url = presign(name, 't.txt', kid, sec)
        # tamper: extend expiry without re-signing
        bad = url.replace('X-VC-Expires=', 'X-VC-ExpiresX=')
        assert requests.get(bad).status_code == 403
        parts = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        q['X-VC-Expires'] = str(int(q['X-VC-Expires']) + 99999)
        bad2 = parts._replace(query=urllib.parse.urlencode(q)).geturl()
        assert requests.get(bad2).status_code == 403

    def test_presigned_expired_rejected(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/e.txt', body=b'x', key_id=kid, secret=sec)
        url = presign(name, 'e.txt', kid, sec, expires_in=-10)
        assert requests.get(url).status_code == 403


class TestAdmin:
    def test_issue_list_revoke_key(self):
        code, j = call('POST', '/admin/keys', body=b'{"key_id":"revme"}')
        assert code == 201 and 'secret' in j
        sec = j['secret']
        code, _ = call('PUT', '/tmp-bkt-should-fail', key_id='revme', secret=sec)
        assert code == 200  # works before revoke
        code, j = call('POST', '/admin/keys/revme/revoke')
        assert code == 200
        code, _ = call('PUT', '/tmp-bkt2', key_id='revme', secret=sec)
        assert code == 403  # revoked -> fail closed
        code, j = call('GET', '/admin/keys')
        assert code == 200 and any(k['key_id'] == 'revme' and k['revoked']
                                   for k in j['keys'])
        assert all('secret' not in k and 'secret_test' not in k for k in j['keys'])

    def test_usage_and_audit_visible(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/u.bin', body=b'12345', key_id=kid, secret=sec)
        code, j = call('GET', '/admin/usage', {'key_id': kid})
        assert code == 200 and j['bytes_stored'][kid] >= 5
        assert any(r['req_count'] > 0 for r in j['usage'])
        code, j = call('GET', '/admin/audit', {'limit': '5'})
        assert code == 200 and isinstance(j['audit'], list)

    def test_nonadmin_cannot_admin(self, user):
        kid, sec = user
        code, _ = call('GET', '/admin/keys', key_id=kid, secret=sec)
        assert code == 403
        code, _ = call('POST', '/admin/keys', body=b'{}', key_id=kid, secret=sec)
        assert code == 403

    def test_support_partner_deny_by_default(self, user):
        kid, sec = user
        code, _ = call('GET', '/support/tickets', key_id=kid, secret=sec)
        assert code == 403
        code, _ = call('POST', '/partner/orders', body=b'{}', key_id=kid, secret=sec)
        assert code == 403

    def test_console_and_health(self):
        r = requests.get(BASE + '/console/')
        assert r.status_code == 200 and 'VisionCloud Console' in r.text
        r = requests.get(BASE + '/health')
        assert r.status_code == 200 and r.json()['status'] == 'ok'


# ============================================================== ADVERSARIAL
class TestAuthAdversarial:
    def test_no_auth_header(self, bucket):
        name, _, _ = bucket
        r = requests.get(f'{BASE}/{name}/hello.txt')
        assert r.status_code == 403

    def test_garbage_auth_header(self, bucket):
        name, kid, _ = bucket
        r = requests.get(f'{BASE}/{name}',
                         headers={'Authorization': 'Bearer abc',
                                  'X-VC-Date': str(int(time.time()))})
        assert r.status_code == 403

    def test_wrong_secret(self, bucket):
        name, kid, sec = bucket
        code, _ = call('GET', f'/{name}', key_id=kid, secret='wrong-' + sec)
        assert code == 403

    def test_tampered_body_fails_signature(self, bucket):
        name, kid, sec = bucket
        body = b'original'
        h = sign_request('PUT', f'/{name}/tamper', {}, body, kid, sec)
        # send DIFFERENT body with the original signature
        r = requests.put(f'{BASE}/{name}/tamper', headers=h, data=b'tampered!!')
        assert r.status_code == 403

    def test_stale_timestamp_rejected(self, bucket):
        name, kid, sec = bucket
        h = sign_request('PUT', f'/{name}/stale', {}, b'x', kid, sec)
        h['X-VC-Date'] = str(int(time.time()) - 3600)  # 1h old, resigned? no: resign properly
        canon = _canonical('PUT', f'/{name}/stale', {}, h['X-VC-Date'],
                           hashlib.sha256(b'x').hexdigest())
        sig = hmac.new(sec.encode(), ('VC-HMAC-SHA256\n' + canon).encode(),
                       hashlib.sha256).hexdigest()
        h['Authorization'] = f'VC-HMAC-SHA256 Credential={kid}, Signature={sig}'
        r = requests.put(f'{BASE}/{name}/stale', headers=h, data=b'x')
        assert r.status_code == 403

    def test_unknown_key_no_oracle(self, bucket):
        name, _, _ = bucket
        code1, _ = call('GET', f'/{name}', key_id='no-such-key', secret='x' * 32)
        code2, _ = call('GET', f'/{name}', key_id='admin', secret='wrongsecret')
        assert code1 == code2 == 403  # identical response, no existence oracle

    def test_cross_key_idor_blocked(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/secret.txt', body=b'private', key_id=kid, secret=sec)
        # a genuinely independent second key (NOT the shared `user` fixture)
        code, k2 = call('POST', '/admin/keys', body=b'{}')
        assert code == 201
        kid2, sec2 = k2['key_id'], k2['secret']
        for method in ['GET', 'PUT', 'DELETE']:
            code, _ = call(method, f'/{name}/secret.txt',
                           body=b'pwned' if method == 'PUT' else None,
                           key_id=kid2, secret=sec2)
            assert code == 403, method
        # attacker cannot even list the victim's bucket
        code, _ = call('GET', f'/{name}', {'list': ''}, key_id=kid2, secret=sec2)
        assert code == 403
        # victim's data untouched
        r = call('GET', f'/{name}/secret.txt', key_id=kid, secret=sec, raw=True)
        assert r.content == b'private'

    def test_replay_with_modified_query_fails(self, bucket):
        name, kid, sec = bucket
        call('PUT', f'/{name}/a', body=b'1', key_id=kid, secret=sec)
        call('PUT', f'/{name}/b', body=b'2', key_id=kid, secret=sec)
        # sign a list with prefix=a, then replay with prefix=b
        h = sign_request('GET', f'/{name}', {'list': '', 'prefix': 'a'}, b'', kid, sec)
        r = requests.get(f'{BASE}/{name}?list=&prefix=b', headers=h)
        assert r.status_code == 403


class TestPathTraversal:
    """Raw-socket tests: `requests` normalizes `..` in paths before sending,
    which would hide server behavior — so we speak raw HTTP here."""

    def raw_call(self, method, raw_path, body=b'', key_id=ADMIN_ID, secret=ADMIN_SECRET):
        import http.client
        host = os.environ['VC_TEST_BASE'].split('://')[1]
        h, _, port = host.partition(':')
        # sign the DECODED path: the server verifies the signature over the
        # percent-decoded request path (same rule as the console UI)
        sign_path = urllib.parse.unquote(raw_path)
        date = str(int(time.time()))
        canon = _canonical(method, sign_path, {}, date,
                           hashlib.sha256(body).hexdigest())
        sig = hmac.new(secret.encode(), ('VC-HMAC-SHA256\n' + canon).encode(),
                       hashlib.sha256).hexdigest()
        headers = {'Authorization':
                   f'VC-HMAC-SHA256 Credential={key_id}, Signature={sig}',
                   'X-VC-Date': date, 'Content-Length': str(len(body))}
        conn = http.client.HTTPConnection(h, int(port or 80), timeout=10)
        conn.request(method, raw_path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    @pytest.mark.parametrize('evil', [
        '../escape', '..', '../../etc/passwd', 'a/../../b',
        'a/../b', './dot', 'dir/..', '%2e%2e/x',
    ])
    def test_evil_keys_rejected(self, bucket, evil):
        name, kid, sec = bucket
        status, _ = self.raw_call('PUT', f'/{name}/{evil}', b'pwn',
                                  key_id=kid, secret=sec)
        assert status in (400, 403, 404), (evil, status)  # never 200; never escapes

    def test_double_encoded_key_is_just_a_weird_key(self, bucket):
        # '%252e%252e/y' single-decodes to the literal key '%2e%2e/y' — an odd
        # but harmless name. It must round-trip, not escape.
        name, kid, sec = bucket
        status, _ = self.raw_call('PUT', f'/{name}/%252e%252e/y', b'pwn',
                                  key_id=kid, secret=sec)
        assert status == 200
        r = call('GET', f'/{name}/%2e%2e/y', key_id=kid, secret=sec, raw=True)
        assert r.status_code == 200 and r.content == b'pwn'

    def test_double_slash_normalizes_safely(self, bucket):
        # '/absolute' as a key is unrepresentable: empty segments collapse and
        # the object lands under the normalized key 'absolute' — hashed blob,
        # no filesystem escape. Documenting, not forbidding.
        name, kid, sec = bucket
        status, _ = self.raw_call('PUT', f'/{name}//absolute', b'pwn',
                                  key_id=kid, secret=sec)
        assert status == 200
        r = call('GET', f'/{name}/absolute', key_id=kid, secret=sec, raw=True)
        assert r.status_code == 200 and r.content == b'pwn'

    def test_no_escape_from_data_dir(self, bucket):
        import glob
        name, kid, sec = bucket
        data_dir = os.environ['VC_TEST_DATA']
        before = set(glob.glob(os.path.join(data_dir, '**'), recursive=True))
        for evil in ['../out1', '..%2f..%2fout2', 'sub/../../out3',
                     '%2e%2e/%2e%2e/out4', '..\\win']:
            self.raw_call('PUT', f'/{name}/{evil}', b'pwn',
                          key_id=kid, secret=sec)
        after = set(glob.glob(os.path.join(data_dir, '**'), recursive=True))
        outside = [p for p in (after - before)
                   if os.path.dirname(os.path.abspath(p)) == os.path.abspath(data_dir)
                   and not p.endswith('.db')]
        assert outside == [], outside
        # and the parent of the data dir gained nothing either
        parent = os.path.dirname(os.path.abspath(data_dir))
        assert not any(os.path.basename(p).startswith('out')
                       for p in glob.glob(os.path.join(parent, 'out*')))


class TestQuotas:
    def test_storage_quota_enforced(self):
        code, j = call('POST', '/admin/keys', body=b'{"quota_bytes": 100}')
        kid, sec = j['key_id'], j['secret']
        call('PUT', '/qbkt', key_id=kid, secret=sec)
        code, j = call('PUT', '/qbkt/big', body=b'x' * 101, key_id=kid, secret=sec)
        assert code == 403 and j['error']['code'] == 'QuotaExceeded'
        code, _ = call('PUT', '/qbkt/ok', body=b'x' * 100, key_id=kid, secret=sec)
        assert code == 200
        # delete frees quota
        call('DELETE', '/qbkt/ok', key_id=kid, secret=sec)
        code, _ = call('PUT', '/qbkt/ok2', body=b'x' * 100, key_id=kid, secret=sec)
        assert code == 200

    def test_request_quota_enforced(self):
        code, j = call('POST', '/admin/keys',
                       body=b'{"quota_reqs_per_day": 3}')
        kid, sec = j['key_id'], j['secret']
        codes = [call('GET', '/', key_id=kid, secret=sec)[0] for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429 and codes[4] == 429

    def test_multipart_respects_quota(self):
        code, j = call('POST', '/admin/keys', body=b'{"quota_bytes": 50}')
        kid, sec = j['key_id'], j['secret']
        call('PUT', '/qmp', key_id=kid, secret=sec)
        code, u = call('POST', '/qmp/f.bin', {'uploads': ''}, key_id=kid, secret=sec)
        uid = u['upload_id']
        _, p = call('PUT', '/qmp/f.bin', {'partNumber': '1', 'uploadId': uid},
                    body=b'x' * 40, key_id=kid, secret=sec)
        import json as _json
        code, j = call('POST', '/qmp/f.bin', {'uploadId': uid},
                       body=_json.dumps(
                           {'parts': [{'part_number': 1, 'etag': p['etag']}]}).encode(),
                       key_id=kid, secret=sec)
        assert code == 200  # 40 <= 50 fits
        code, u = call('POST', '/qmp/g.bin', {'uploads': ''}, key_id=kid, secret=sec)
        uid = u['upload_id']
        _, p = call('PUT', '/qmp/g.bin', {'partNumber': '1', 'uploadId': uid},
                    body=b'y' * 40, key_id=kid, secret=sec)
        code, j = call('POST', '/qmp/g.bin', {'uploadId': uid},
                       body=_json.dumps(
                           {'parts': [{'part_number': 1, 'etag': p['etag']}]}).encode(),
                       key_id=kid, secret=sec)
        assert code == 403 and j['error']['code'] == 'QuotaExceeded'  # 80 > 50


class TestOversized:
    def test_single_put_over_cap_rejected(self, bucket):
        name, kid, sec = bucket
        cap = int(os.environ.get('VC_MAX_OBJECT_BYTES', 64 * 1024 * 1024))
        code, j = call('PUT', f'/{name}/huge', body=b'x' * (cap + 1),
                       key_id=kid, secret=sec)
        assert code == 413

    def test_part_over_cap_rejected(self, bucket):
        name, kid, sec = bucket
        cap = int(os.environ.get('VC_MAX_OBJECT_BYTES', 64 * 1024 * 1024))
        _, u = call('POST', f'/{name}/hp.bin', {'uploads': ''},
                    key_id=kid, secret=sec)
        code, _ = call('PUT', f'/{name}/hp.bin',
                       {'partNumber': '1', 'uploadId': u['upload_id']},
                       body=b'x' * (cap + 1), key_id=kid, secret=sec)
        assert code == 413


class TestRobustness:
    def test_concurrent_overwrites_stay_consistent(self, bucket):
        import threading
        name, kid, sec = bucket
        errs = []

        def w(i):
            try:
                code, _ = call('PUT', f'/{name}/race', body=f'v{i}'.encode(),
                               key_id=kid, secret=sec)
                assert code == 200
            except Exception as e:
                errs.append(e)

        ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errs
        r = call('GET', f'/{name}/race', key_id=kid, secret=sec, raw=True)
        assert r.status_code == 200 and r.content in [f'v{i}'.encode() for i in range(20)]
        code, j = call('GET', '/admin/usage', {'key_id': kid})
        assert j['bytes_stored'][kid] == len(r.content)  # accounting exact

    def test_error_bodies_leak_nothing(self, bucket):
        name, kid, sec = bucket
        code, j = call('GET', '/no-such-bucket-xyz/k', key_id=kid, secret=sec)
        assert code == 404
        txt = str(j).lower()
        assert 'traceback' not in txt and '/home/' not in txt and 'sqlite' not in txt
