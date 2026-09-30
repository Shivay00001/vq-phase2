# VisionOS — Product Requirements Document (PRD)

**Status:** Phase 2 foundation — research + prototype slice. **Not a product yet.**
**Date:** 2026-09-26. **Owner:** VisionQuantech (U-STACK platform track).

## 1. Vision

VisionOS is the long-term operating-system layer of the U-STACK vision: a
capability-first computing platform where **no software holds ambient authority** —
every app, agent, and service acts only through explicitly granted, delegable,
revocable capabilities (U-PERM). In the far term this is a real kernel; in the near
term it is a capability-enforced shell and runtime on top of existing systems.

## 2. Problem

- Modern OSes grant apps ambient authority (filesystem, network, camera) via
  coarse install-time prompts users don't understand. Malware, supply-chain
  attacks, and over-privileged AI agents all exploit this.
- Existing capability systems (seL4, Zircon) prove the model works but never
  became developer-friendly platforms; web "OSes" (Puter, daedalOS) have zero
  permission model at all.
- VisionQuantech's AI-agent business needs a trustworthy execution substrate for
  agents that act on user data — U-PERM is that substrate's security core.

## 3. Users

1. **End users** — want apps that can't silently exfiltrate data; want to see and
   revoke exactly what each app may do.
2. **Developers** — want a simple `check(cap, object, op)` API instead of
   platform-specific permission mazes.
3. **VisionQuantech (platform owner)** — wants the agent runtime to be provably
   least-privilege (auditable for enterprise clients).
4. **(Future) Hardware partners** — only relevant at kernel phase; explicitly out
   of scope now.

## 4. Phased scope (honest)

| Phase | Name | What ships | Timeline class |
|---|---|---|---|
| 0 | **Foundation slice** (this task) | U-PERM capability library (real, tested) + web shell UX prototype | done 2026-09-26 |
| 1 | VisionOS Shell (product) | Polished browser/desktop shell; per-app U-PERM grants; cloud sync; app SDK | 12–18 months |
| 2 | VisionOS Runtime | Capability sandbox on Linux (namespaces/seccomp + U-PERM policy daemon); runs real binaries least-privilege | 2–3 years |
| 3 | VisionOS Kernel | Bare-metal microkernel (scheduler, VM, drivers for a reference board) | 5–10 years |

**Non-goals (all phases of this slice):** bare-metal kernel, hardware drivers,
running unmodified Linux/Windows binaries, app-store payments, mobile OS.

## 5. User stories (Phase 0 slice)

- As a user, I can boot the VisionOS web shell, open draggable/resizable windows,
  launch apps from a launcher, and manage files that persist across reloads
  (IndexedDB), so I can *feel* the target UX.
- As a user, I can open Settings and change theme/wallpaper/clock, persisted
  locally, so the shell feels like mine.
- As a developer, I can `mint → derive → check → revoke` capabilities with the
  U-PERM library, delegate diminished rights, and rely on revocation propagating
  to all descendants — verified by unit + adversarial tests.
- As a security reviewer, I can read the adversarial test list (forgery,
  escalation, confused deputy, revoked reuse, expiry) and see each attack fail.

## 6. Success metrics (Phase 0)

- U-PERM: 100% of unit + adversarial tests pass; zero ambient-authority paths in
  the API (every check requires presenting a capability token).
- Shell prototype: boots in a browser from `file://` with zero build step; all
  visible controls do something real or are honestly labeled non-functional.
- Docs: PRD/TRD/ERD/API contracts/research exist and mark prototype-vs-real
  boundaries explicitly.

## 7. Monetization (future phases only)

Phase 1: free shell + Pro (sync, more storage). Phase 2: enterprise runtime
licensing (audited agent sandboxes). Phase 3: none planned — research asset.
Nothing is monetized or deployed in Phase 0.

## 8. Risks

- **Scope fantasy:** calling a web page an OS destroys credibility — mitigated by
  prominent prototype labeling (see slice/desktop/README.md).
- **Capability UX:** users don't understand capabilities — Phase 1 needs a
  grants UI that reads like plain language ("Notes may read: Documents").
- **Effort:** kernel phase needs a dedicated team; do not staff it until the
  runtime phase has paying users.
