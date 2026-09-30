# VisionCloud — Product Requirements Document (v0 Slice)
**Date:** 2026-09-26 | **Status:** approved for slice build | **Author:** Nerc (TRACK 2)

## 1. Problem
VisionQuantech's U-STACK needs a storage primitive (VisionCloud) alongside compute and database. Building the *real* distributed cloud requires datacenters, billing, and compliance — none of which exist yet. The v0 slice proves the **API surface, auth model, and operational semantics** honestly on local disk, so Phase-2 design decisions are validated against working code instead of slideware.

## 2. Non-goals (explicit — we will NOT claim these)
- Any durability beyond one disk (no replication, no erasure coding, no 11 nines).
- Real billing/invoicing; metering hooks only (bytes + request counters per key).
- Multi-region, CDN/edge, or zero-egress economics.
- Compliance certifications (SOC 2 / ISO / DPDP posture), encryption at rest via KMS, malware scanning.
- Database and compute components of VisionCloud — object storage only.

## 3. Users (panels per framework; tool slices are exempt from full 4-panel builds, but RBAC roles are defined)
- **Admin (platform owner):** create/revoke access keys, set per-key quotas, inspect usage, delete any bucket (with audit log).
- **User (developer):** own buckets/objects only; presigned-URL delegation; multipart uploads.
- **Support / Partner:** defined in RBAC matrix (docs/TRD.md) but UI ships User + Admin only in v0; support/partner actions are API-gated stubs with deny-by-default.

## 4. User stories
1. As a developer, I can create a bucket, upload objects (single + multipart), download them (direct + presigned URL), and delete them — using HMAC-signed requests like S3, so my mental model transfers.
2. As a developer, I get byte-range downloads and object metadata (ETag, size, content-type) so media/large-file use cases work.
3. As an admin, I can issue an access key with a storage quota and a request quota, and watch usage climb — so metering (the seed of billing) is real from day one.
4. As an admin, I can revoke a key and have every request with it fail closed.
5. As a console user, I can browse buckets/objects and upload/download in a browser without curl.

## 5. Functional requirements (v0)
- S3-style REST: `PUT /{bucket}`, `GET /` (list buckets), `DELETE /{bucket}`, `PUT /{bucket}/{key}`, `GET /{bucket}/{key}` (+`?partNumber`, byte `Range`), `HEAD /{bucket}/{key}`, `DELETE /{bucket}/{key}`, `GET /{bucket}?list` (prefix, delimiter, marker, max-keys, pagination).
- Multipart: `POST /{bucket}/{key}?uploads` → uploadId; `PUT ...?partNumber=N&uploadId=` ; `POST ...?uploadId=` (complete with part ETags); `DELETE ...?uploadId=` (abort).
- Auth: HMAC-SHA256 request signing (SigV4-inspired, documented simplifications), `X-VC-Date` within ±15 min, per-key secret never on the wire, presigned URL downloads (`?X-VC-Expires`, `&X-VC-Signature=`).
- Quotas: per-key `max_bytes` (storage) and `max_requests` (per 24h sliding window); writes fail with 403/429 when exceeded; reads unaffected by storage quota.
- Safety: object keys sanitized — no `..`, no absolute paths, no escaping the data dir (adversarial-tested); single-object cap 64 MB (configurable); bucket names `[a-z0-9.-]{3,63}`.

## 6. Success metrics (v0)
- 100% of listed API contracts implemented + tested (unit + adversarial).
- Zero auth-bypass / path-traversal / quota-bypass findings in adversarial suite.
- Console demo: bucket create → upload → presigned download → delete, end to end, in a browser.
- Docs: PRD/TRD/ERD/API contracts + honest prototype-vs-production README.

## 7. Monetization (future, documented not built)
Free tier (10 GB, like R2's free tier) → Pro by GB-mo + request ops, **egress-led pricing** (research shows egress is the bill). Razorpay when real. No billing code in v0 beyond metering counters.
