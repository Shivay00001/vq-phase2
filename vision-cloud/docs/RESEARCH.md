# VisionCloud — Competitor Research Notes
**Date:** 2026-09-26 | **Track:** VQ Phase-2 (U-STACK / VisionCloud) — research pass before build.

## 1. Systems studied

| System | Model | Key facts (verified via public docs/pricing, Sep 2026) |
|---|---|---|
| **AWS S3** | The reference object store. 99.999999999% (11 nines) durability, multiple storage classes (Standard, Intelligent-Tiering, Standard-IA, Glacier family, Deep Archive). SigV4 request signing; IAM policies + bucket policies + ACLs. | Storage: $0.023/GB-mo Standard (first 50 TB, US); **egress $0.09/GB** (first 10 TB); PUT $5.00/M, GET $0.40/M. Free tier: 5 GB for 12 months only. For a 2 TB stored / 6 TB downloaded workload ≈ **$577/mo** — ~94% of it egress. |
| **Google Cloud Storage** | Four tiers (Standard → Rapid → Archive), BigQuery/Dataflow integration, strict minimum storage durations (365 days on Archive). | Storage $0.020/GB-mo; **egress $0.12/GB** — highest of the hyperscalers. Always-free 5 GB/mo. 2 TB/6 TB workload ≈ **$736/mo**. |
| **Cloudflare R2** | S3 API-compatible (the migration target most indie tooling aims at), single Standard tier + Infrequent Access ($0.01/GB), **zero egress fees**, no minimum durations, no early-deletion penalties. | $0.015/GB-mo; PUT $4.50/M; GET $0.36/M; egress $0. Free tier 10 GB/mo. Same workload ≈ **$30/mo**. Edge-served by default. Weakness: no cold/archive tier, smaller ecosystem, less mature compliance for regulated industries. |
| **Backblaze B2 / Wasabi** | Budget S3-compatible alternates. | B2: $0.006/GB, egress $0.01/GB after 3×-storage allowance. Wasabi: $0.00699/GB, egress free within 1:1 monthly egress ratio policy. |
| **MinIO** | Open-source, S3-compatible, self-hosted. The closest thing to "run S3 on your own disks." | Erasure coding instead of replication, runs on commodity hardware; production-grade for those who operate it. Confirms: an honest self-hosted object store is *doable* but the hard parts are distribution, repair, and operations — not the API surface. |

**Sources:** futuretweets.com/s3-vs-azure-blob-vs-gcs-vs-cloudflare-r2-pricing-2026, shattered.io/cloudflare-r2-vs-aws-s3-pricing-2026, writeupcafe.com hidden-costs guide, hbmartin/comparison-s3-compatible-object-storage-alternatives (GitHub), profullstack/c0mpute storage-pricing-scenarios.

## 2. What makes them win (patterns to copy)

1. **S3-compatible API surface** — the industry standardized on S3's REST model (buckets, objects, multipart, presigned URLs, SigV4). Compatibility = existing SDKs/tools work unmodified. VisionCloud's slice copies this deliberately.
2. **Egress is the real product** — for anything that *serves* data, egress dwarfs storage (94% of the example bill). R2's entire market position is "zero egress." Any VisionCloud pricing must be egress-led, not storage-led.
3. **Tiering + lifecycle policies** — S3/GCS win on cold tiers; teams store archives cheaply. A cloud without tiers is a toy for backup workloads.
4. **Auth model maturity** — SigV4 (HMAC-signed canonical requests), IAM least-privilege policies, bucket policies, presigned URLs for delegated access. This is table stakes; the slice implements a simplified SigV4-style scheme.
5. **Durability math** — 11 nines comes from geographic replication + erasure coding + checksums + repair. Nobody trusts a store without a durability story.
6. **Boring operations** — versioning, replication rules, server access logging, event notifications, CDN fronting. The feature checklist is decades deep.

## 3. What a REAL production VisionCloud needs (not faked)

- **Distributed storage layer**: erasure coding or 3× replication across failure domains (racks/zones/regions), background repair, bit-rot checksums. Local disk ≠ cloud.
- **Control plane**: identity (real key management + rotation + IAM-style policies), quota/billing metering (per-GB-mo, per-op, per-GB-egress), audit logging, multi-tenancy isolation.
- **Data plane**: consistent reads, conditional writes, multipart semantics, resumable uploads, byte-range GET, versioning, lifecycle engine, CDN/edge caching for egress economics.
- **Security & compliance**: TLS everywhere, encryption at rest (KMS), SOC 2/ISO posture for enterprise, region pinning (India DPDP Act data-residency), abuse/fraud detection.
- **Billing**: usage metering → invoicing (Razorpay in India), free-tier accounting, overage handling, dunning. Without billing there is no business model.
- **Infra**: actual datacenters or colocation/wholesale disk+network contracts; bandwidth commits (egress is bought wholesale at a fraction of the S3 retail price — that's the entire margin).

## 4. Honest positioning for the slice (v0)

We build the **S3-compatible API + auth + quota + console** surface honestly on local disk — a real, working, tested object store for dev/small workloads — and explicitly label replication, billing, compliance, and distributed durability as **not implemented** (production gaps listed in README). We do not claim 11 nines, we do not claim a cloud.

## 5. Design decisions taken from research

- Adopt S3 REST shape (path-style `/{bucket}/{key}`, `PUT/GET/DELETE/HEAD`, `?uploads`, `?partNumber`, `?uploadId`, multipart init/complete) so standard mental models transfer.
- Auth = SigV4-inspired HMAC-SHA256 signing (canonical request + string-to-sign). Simplified (single-region, header-based), but the *mechanism* (secret never on the wire, replay windows, scope) is real.
- Presigned URLs for delegated downloads (expiry-bounded, HMAC token).
- Per-access-key quotas (storage bytes + request counts) — the seed of billing metering.
- Multipart upload with per-part ETags and server-side assembly, because large-object upload is the first thing that breaks on naive stores.
