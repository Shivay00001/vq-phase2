# VisionCloud — v0 Slice (object storage)

Part of **VQ Phase-2 / U-STACK**. Built per `~/workspace/products/SOFTWARE_BUILD_FRAMEWORK.md`:
research first, then PRD → TRD → ERD → API contracts → working slice → adversarial tests.

## What this is

A **real, working, S3-style object storage service** running on local disk:
buckets, objects, multipart upload, byte-range downloads, presigned URLs,
HMAC request signing (SigV4-inspired), per-key storage + request quotas, an
admin API (key issuance/revocation, usage, audit log), and a browser console.

## Quick start (zero spend, zero dependencies for the server)

```bash
cd slice
python3 vc_server.py                 # → http://127.0.0.1:8471
# first run prints a one-time admin secret (or set VC_ADMIN_SECRET)
# console: http://127.0.0.1:8471/console/   health: /health
```

Run the test suite (needs the venv's pytest+requests — dev machine only):

```bash
cd slice && ./run_tests.sh           # 46 tests, ~7s
```

## What's REAL vs what's PROTOTYPE

| Area | Status | Notes |
|---|---|---|
| S3-style REST (buckets/objects/multipart/presigned/range/HEAD) | ✅ Real | Implemented + tested; 46/46 green incl. adversarial |
| HMAC request signing, 15-min skew, uniform 403s | ✅ Real | SigV4-inspired; simplifications documented in TRD §3 |
| Path-traversal safety (content-addressed blobs) | ✅ Real | Keys hashed, never used as paths; adversarial-tested incl. raw-socket `..`, `%2e%2e` |
| Per-key quotas (bytes + req/day), transactional | ✅ Real | Check+increment in one SQLite txn; quota-exhaustion tested |
| Multipart (ETag validation, 5 MB rule, abort) | ✅ Real | Tested incl. tampered-ETag and small-part rejection |
| Usage metering + audit log | ✅ Real | Seed of billing; no invoicing |
| Console UI | ✅ Real | Static page; signs requests in-browser (test keys only) |
| Replication / erasure coding / 11 nines | ❌ Prototype gap | Single disk. A disk failure loses data. |
| Real billing/invoicing | ❌ Prototype gap | Counters only; Razorpay when real |
| TLS, at-rest encryption (KMS), WAF, malware scan | ❌ Prototype gap | Localhost slice |
| Multi-region, CDN/edge, lifecycle tiers | ❌ Prototype gap | Phase-2 production build |
| Compliance (SOC 2 / ISO / DPDP) | ❌ Prototype gap | Required before any customer data |
| Support/Partner panels | ❌ Stub | API returns 403 deny-by-default; RBAC matrix defined in TRD |
| `secret_test` column in DB | ⚠️ Dev-only | Lets tests/console sign requests; **must not exist in production** (hash-only + issue-once) |
| Presigned-URL replay within expiry | ⚠️ Known limit | Bounded by expiry; production needs single-use tokens + Redis |

## Production gaps (the honest list — nothing here is faked)

1. **Distributed storage**: erasure coding or 3× replication across failure domains,
   background repair, checksums/bit-rot detection. The `LocalDiskBackend` class is
   the seam — production swaps it, not the API layer.
2. **Control plane**: real KMS-backed key management + rotation, IAM-style policies,
   Postgres + RLS (framework default), Redis for per-second rate limits/idempotency.
3. **Billing**: metering → invoicing (Razorpay test mode), free-tier accounting,
   overage/dunning. Egress-led pricing per the research (egress is the bill).
4. **Security**: TLS everywhere, encryption at rest, secret rotation, abuse/fraud
   detection, pen-test before launch.
5. **Compliance**: SOC 2 / ISO 27001 track, India DPDP data-residency (region pinning).
6. **Infra economics**: wholesale disk + bandwidth contracts — egress bought wholesale
   is the entire margin (see `docs/RESEARCH.md`: R2's whole position is $0 egress).

## Docs

- `docs/RESEARCH.md` — S3/GCS/R2/B2/MinIO pricing + auth models, what production needs
- `docs/PRD.md` — problem, users, stories, non-goals, metrics
- `docs/TRD.md` — architecture, auth design, RBAC matrix, security review sign-off
- `docs/ERD.md` — SQLite schema diagram
- `docs/API_CONTRACTS.md` — every endpoint, auth, quotas, errors

## Bugs the tests caught (kept honest)

- Quota double-counting on object overwrite (storage decremented twice) — fixed; the
  20-thread concurrent-overwrite test now asserts byte-exact accounting.
- Multipart-complete double-counted staged bytes against quota — fixed with
  `used − staged + total` accounting.
- List `marker` was inclusive instead of exclusive — fixed to S3 semantics.
