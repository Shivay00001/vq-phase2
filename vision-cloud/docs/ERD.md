# VisionCloud — Entity Relationship Diagram (v0)
SQLite metadata store (`slice/data/visioncloud.db`). Blobs live on disk, referenced by hash.

```
┌──────────────────┐        ┌──────────────────┐
│   access_keys    │        │     buckets      │
├──────────────────┤        ├──────────────────┤
│ key_id      PK   │──┐  ┌─▶│ name        PK   │
│ secret_hash      │  │  │  │ owner_key_id FK  │
│ secret_test *    │  │  │  │ created_at       │
│ is_admin    BOOL │  │  │  │ object_count     │
│ revoked     BOOL │  │  │  │ bytes_total      │
│ quota_bytes INT  │  │  │  └────────┬─────────┘
│ quota_reqs  INT  │  │  │           │ 1 ── N
│ created_at       │  │  │  ┌────────▼─────────┐
└──────────────────┘  │  │  │     objects      │
        │ 1 ── N      │  │  ├──────────────────┤
        │ (owns)      │  │  │ id          PK   │
┌───────▼──────────┐  │  │  │ bucket      FK   │
│  usage_ledger    │  │  │  │ obj_key     TEXT │
├──────────────────┤  │  │  │ blob_hash   TEXT │──▶ disk: data/blobs/<sha256>.bin
│ id          PK   │  │  │  │ size        INT  │    (+ sidecar .meta JSON)
│ key_id      FK   │  │  │  │ etag        TEXT │
│ day         DATE │  │  │  │ content_type TEXT│
│ req_count   INT  │  │  │  │ updated_at       │
└──────────────────┘  │  │  └──────────────────┘
                      │  │
        ┌─────────────▼──▼──────────────┐
        │        audit_log              │
        ├───────────────────────────────┤
        │ id PK, actor_key_id FK,       │
        │ action TEXT, target TEXT,     │
        │ detail TEXT, created_at       │
        └───────────────────────────────┘

┌──────────────────────┐     ┌──────────────────────┐
│ multipart_uploads    │     │   multipart_parts    │
├──────────────────────┤     ├──────────────────────┤
│ upload_id       PK   │──┐  │ id              PK   │
│ bucket          FK   │  │  │ upload_id       FK   │
│ obj_key         TEXT │  │  │ part_number     INT  │
│ owner_key_id    FK   │  │  │ blob_hash       TEXT │
│ created_at           │  │  │ size            INT  │
└──────────────────────┘  │  │ etag            TEXT │
                          │  └──────────────────────┘
                          │  1 ── N (UNIQUE upload_id+part_number)

┌──────────────────────┐
│     key_storage      │  ← live per-key byte totals (NOT in usage_ledger;
├──────────────────────┤    single accounting site per mutation — see TRD §8)
│ key_id          PK   │
│ bytes_stored    INT  │
│ updated_at           │
└──────────────────────┘
```

\* `secret_test` stores the raw test secret so the bundled console + tests can sign requests. **This column must not exist in production** — production stores only `secret_hash` and issues the secret once at creation. Documented as a v0-dev convenience; the server never returns it over the API.

## Indexes
- `objects(bucket, obj_key)` UNIQUE — key lookup + overwrite semantics.
- `usage_ledger(key_id, day)` UNIQUE — quota accounting.
- `audit_log(created_at)` — admin tail queries.
- `multipart_parts(upload_id, part_number)` UNIQUE.

## Quota accounting rule
`key_storage.bytes_stored` = live per-key byte total, updated transactionally on PUT/DELETE/complete/abort/part-put (exactly one accounting site per mutation). `usage_ledger.req_count` = signed requests per key per UTC day. Both checked **inside the same SQLite transaction** as the write they gate — no check-then-act race.
