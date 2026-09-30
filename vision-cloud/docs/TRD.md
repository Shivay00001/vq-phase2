# VisionCloud — Technical Requirements Document (v0 Slice)
**Date:** 2026-09-26 | **Status:** implements PRD v0

## 1. Architecture (v0 slice)

```
                ┌─────────────┐
  Browser ─────▶│  Console UI │ (static HTML/JS, served by the same process)
                └──────┬──────┘
                       │ signed fetch (HMAC in JS, test keys only)
                ┌──────▼──────┐      ┌────────────────┐
  SDK/curl ────▶│  vc-server  │─────▶│ SQLite metadata │
  (REST + HMAC) │  (Python 3  │      │ (buckets, keys, │
                │   stdlib)   │      │  objects, parts │
                └──────┬──────┘      │  usage)         │
                       │             └────────────────┘
                       ▼
                ┌──────────────┐
                │ Local disk   │  data/buckets/<bucket>/<sha256(key)>.bin
                │ (dev backend)│  + sidecar .meta JSON (etag, size, ctype)
                └──────────────┘
```

**Production target (not built):** replace local disk with erasure-coded distributed backend; SQLite → Postgres (RLS); add Redis for rate-limit/idempotency; KMS for at-rest encryption. Interfaces (`StorageBackend`, `MetadataStore`) are separated so the swap is mechanical, not a rewrite.

## 2. Tech stack (v0 — zero deps, zero spend)

| Layer | Choice | Reason |
|---|---|---|
| Server | Python 3.12 stdlib `http.server` (threaded) | No pip deps → runs anywhere; honest dev slice. Production would be Workers/FastAPI. |
| Metadata | SQLite (stdlib) | Real ACID transactions for multipart assembly + quota accounting. |
| Blobs | Local filesystem, content-addressed filenames | Path-traversal-proof by construction (keys hashed, never used as paths). |
| Auth | HMAC-SHA256, SigV4-inspired | Documented below; secrets in `keys.json` (test keys only, never real). |
| Console UI | Static HTML/CSS/JS, no build step | Served by `vc-server`; signs requests in-browser with the test key the user pastes. |
| Tests | pytest + requests (dev-machine only) | Server itself has zero runtime deps. |

## 3. Auth design (SigV4-inspired, simplified — differences from real SigV4 stated)

- **Canonical request:** `METHOD\nPATH\nsorted-query\nX-VC-Date\ncontent-sha256\n` — signed with `HMAC(secret, string-to-sign)`, sent as `Authorization: VC-HMAC-SHA256 Credential=<key-id>, Signature=<hex>`.
- **Simplifications vs AWS SigV4:** single region `vc-local-1`, no `x-amz-*` header set, no payload streaming signatures, 15-min clock skew window (vs 15 min SigV4 too), no IAM policies (per-key allow-all-own + quotas).
- **Presigned URLs:** `GET /{bucket}/{key}?X-VC-Expires=<unix>&X-VC-KeyId=<id>&X-VC-Signature=HMAC(secret, "GET\n/path\n<expires>")`. Server rejects expired or tampered URLs. One-time replay is bounded by expiry (documented limitation; production needs single-use tokens + Redis).
- **Key storage:** `keys.json` holds `key_id → {secret_hash (sha256), secret (test only), quotas, revoked}`. Admin endpoints require the `admin` key.
- **RBAC matrix:** `admin` key: all ops incl. key management. `user` keys: own buckets only (ownership recorded per bucket). Support/partner: no console; API returns 403 (deny-by-default) — stubs documented in API_CONTRACTS.md.

## 4. Data model
See `docs/ERD.md`. Buckets (owner key_id), Objects (bucket, key, size, etag, version placeholder), MultipartUploads + Parts, AccessKeys (quotas, revoked), UsageLedger (bytes/op counters per key per day), AuditLog (admin/support actions).

## 5. API contracts
See `docs/API_CONTRACTS.md`. Every endpoint: method, path, auth, rate-limit/quota behavior, request/response shapes, error codes.

## 6. Scaling plan (honest)
- **v0:** single process, single disk. ~hundreds of req/s on localhost; fine for dev.
- **10×:** put behind a reverse proxy, move blobs to network disk; SQLite WAL mode.
- **100×:** requires the production backend (distributed store + Postgres + Redis + KMS) — this is the Phase-2 build, explicitly out of scope. The slice's interfaces are shaped for it.

## 7. Security review (v0 sign-off)
- [x] No IDOR: every object op checks bucket ownership against the signing key (tests: cross-key access → 403).
- [x] Path traversal: keys never touch the filesystem as paths (sha256 filenames); `..`, `/`, absolute keys rejected/neutralized (adversarial tests).
- [x] Replay: `X-VC-Date` ±15 min; presigned URLs expire; (limitation: no nonce store in v0 — documented).
- [x] Quotas enforced server-side on write path; quota check + usage increment in one SQLite transaction (no TOCTOU).
- [x] Secrets: test keys only, `keys.json` git-ignored pattern documented; never logged.
- [x] Error messages: generic 4xx bodies, no stack traces, no "key exists" oracles on auth failures (uniform 403).
- [ ] TLS, at-rest encryption, WAF, malware scan — **production gaps**, listed in README.

## 8. Cost estimate
v0: ₹0 / $0 (local). Production target at 1k users on R2-shaped economics: storage-dominant; framework default stack (R2 + Workers + Supabase) free tiers cover launch — detailed in README production-gaps.
