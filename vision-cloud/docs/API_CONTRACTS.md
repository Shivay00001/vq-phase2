# VisionCloud — API Contracts (v0)
Base: `http://localhost:8471` (configurable). Auth: `VC-HMAC-SHA256` (see TRD §3) unless noted.
All timestamps UTC ISO-8601. Errors: JSON `{"error": {"code": "...", "message": "..."}}` — generic messages, no internals.

## Conventions
- Bucket names: `^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`. Object keys: 1–1024 chars, must not contain `..` segments or be absolute; stored verbatim, persisted to disk by sha256.
- Pagination: `max-keys` (default 100, max 1000), `marker`, `prefix`, `delimiter=/`.
- Quota errors: `403 QuotaExceeded` (storage), `429 RateLimited` (requests/day). Auth failures: uniform `403 Forbidden` (no key-exists oracle).

## Buckets
| Method & Path | Auth | Description |
|---|---|---|
| `PUT /{bucket}` | user | Create bucket (caller becomes owner). 200 `{name, created_at}`; 409 `BucketAlreadyExists`. |
| `GET /` | user | List own buckets (admin: `?all=1`). 200 `{buckets: [{name, objects, bytes, created_at}]}`. |
| `DELETE /{bucket}` | owner/admin | Delete empty bucket. 409 `BucketNotEmpty` unless `?force=1` (admin only). 204. |
| `GET /{bucket}?list&prefix=&delimiter=&marker=&max-keys=` | owner/admin | List objects. 200 `{objects: [{key, size, etag, content_type, updated_at}], truncated, next_marker}`. |

## Objects
| Method & Path | Auth | Description |
|---|---|---|
| `PUT /{bucket}/{key}` | owner | Upload (single). Headers: `Content-Type`, optional `Content-MD5`. Enforces key quota + 64 MB cap inside one transaction. 200 `{key, size, etag}` where etag = sha256 hex. |
| `GET /{bucket}/{key}` | owner or valid presigned URL | Download. Supports `Range: bytes=a-b` (206). `HEAD` returns metadata only. 404 `NoSuchKey`. |
| `DELETE /{bucket}/{key}` | owner | Delete. 204 (idempotent — 204 even if absent, S3-style). |

## Multipart upload
| Method & Path | Auth | Description |
|---|---|---|
| `POST /{bucket}/{key}?uploads` | owner | Init. 200 `{upload_id}` (uuid4 hex). |
| `PUT /{bucket}/{key}?partNumber=N&uploadId=` | owner | Upload part (N: 1–10000, ≥5 MB except last — enforced at complete). 200 `{etag}`. |
| `POST /{bucket}/{key}?uploadId=` | owner | Complete. Body JSON `{parts: [{part_number, etag}]}` — ETags must match stored parts in order. Assembles blob, enforces quota. 200 `{key, size, etag}`. |
| `DELETE /{bucket}/{key}?uploadId=` | owner | Abort. Deletes staged parts. 204. |

## Presigned URLs
`GET /{bucket}/{key}?X-VC-Expires=<unix>&X-VC-KeyId=<id>&X-VC-Signature=<hex>` — **no Authorization header needed**; signature = `HMAC(secret, "GET\n/<bucket>/<key>\n<expires>")`. Expired/tampered → 403. Console has "copy presigned link" (default 1h).

## Admin (requires `is_admin` key)
| Method & Path | Description |
|---|---|
| `POST /admin/keys` | Body `{key_id?, quota_bytes?, quota_reqs_per_day?}` → 201 `{key_id, secret (shown once)}`. Audit-logged. |
| `GET /admin/keys` | List keys (secrets never returned). |
| `POST /admin/keys/{id}/revoke` | Revoke; all subsequent requests 403. Audit-logged. |
| `GET /admin/usage?key_id=&day=` | Usage ledger rows. |
| `GET /admin/audit?limit=` | Audit log tail. |

## Support / Partner (v0: deny-by-default stubs)
`GET /support/...`, `POST /partner/...` → `403` with `{"error":{"code":"NotImplemented","message":"role not enabled in v0 slice"}}`. Roles exist in the RBAC matrix (TRD §3) so the surface is shaped, but no privileged support/partner actions ship in the slice.

## Console UI
`GET /console/` — static app (no auth to load the page; the user pastes a Key ID + Secret, and the JS signs every API call — secrets stay in the browser, test keys only).

## Rate limits (v0)
Per-key `quota_reqs_per_day` (default 10,000; admin unlimited-ish 1,000,000). No per-second limiter in v0 — documented production gap (Redis token bucket).
