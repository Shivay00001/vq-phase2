# VisionOS — Technical Requirements Document (TRD)

**Date:** 2026-09-26. Companion to PRD.md, ERD.md, API_CONTRACTS.md, RESEARCH.md.

## 1. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 0 (this slice)                                        │
│  ┌──────────────┐   ┌────────────────────────────────────┐  │
│  │ Web Shell    │   │ U-PERM capability library (JS)     │  │
│  │ PROTOTYPE    │   │ REAL, tested                       │  │
│  │ HTML/CSS/JS, │   │ mint/derive/check/revoke,          │  │
│  │ IndexedDB    │   │ delegation tree, revocation        │  │
│  └──────────────┘   │ propagation                        │  │
│         │           └────────────────────────────────────┘  │
│         │  (Phase 1: shell calls U-PERM for per-app grants) │
└─────────────────────────────────────────────────────────────┘

Phase 2 (future): U-PERM policy daemon + Linux namespaces/seccomp sandbox.
Phase 3 (future): microkernel; U-PERM semantics move into kernel space.
```

**Design decision:** the capability library is written in dependency-free
JavaScript so the *same code* runs in Node (tests), the browser shell
(Phase 1 grants), and later a JS policy daemon. No rewrite at each phase.

## 2. Tech stack (Phase 0)

| Layer | Choice | Why |
|---|---|---|
| Shell prototype | Static HTML/CSS/JS, zero dependencies | Opens from `file://`; no build; no server; zero cost |
| Persistence | IndexedDB (files, settings) | Real client-side DB; survives reload; no backend |
| U-PERM | Vanilla JS (ES2020), Node ≥ 18 for tests | Same module in browser + Node; `node:test` built-in, zero test deps |

**Deviations from SOFTWARE_BUILD_FRAMEWORK.md stack (written reason):** no
Cloudflare/Supabase — there is deliberately *no backend* in Phase 0 (nothing
deployed, zero spend). Backend choices get made at Phase 1.

## 3. U-PERM design

- **Capability = unguessable bearer token.** `mint(objectId, rights)` returns a
  256-bit random token string. The token is the *only* reference to the grant;
  the authority table lives in a closure (never exposed).
- **Rights model:** per-object operation sets, e.g. `file:read`, `file:write`.
  Rights are opaque strings; the library never interprets them beyond
  subset checks — applications define their vocabulary.
- **Derivation tree:** `derive(parentToken, rightsSubset, opts)` mints a child
  whose rights must be ⊆ parent's rights (else `RangeError`). Children record
  `parentId`; revocation walks the tree.
- **Checks:** `check(token, objectId, operation)` returns boolean. False unless:
  token exists, not expired, not revoked (nor any ancestor revoked), object
  matches, rights include the operation. Constant-structure, no exceptions on
  the deny path (deny-by-default).
- **Revocation:** `revoke(token)` marks the token and *all descendants* revoked
  (propagation through the derivation tree, verified by tests).
- **Expiry:** optional `expiresAt`/`ttlMs` at mint/derive; expired tokens deny.
- **Anti-tamper:** `inspect()` returns frozen copies; mutating a returned object
  cannot change stored rights (adversarial test covers this).
- **Confused-deputy safety:** the library provides no ambient lookup — a deputy
  service must be *handed* a caller capability to act; tests demonstrate a deputy
  that refuses to spend its own authority on a caller's request.

## 4. Shell prototype design

- **Window manager:** absolutely-positioned windows; drag (pointer events),
  resize (SE handle), minimize/maximize/close, z-order focus, taskbar with live
  window buttons, launcher menu, desktop icons, clock.
- **Apps (all real, no dead buttons):** Files (CRUD on IndexedDB FS, folders,
  rename, delete, text editing), Notes (text editor, per-note files), Terminal
  (real mini-shell: `help ls cd cat echo write rm mkdir date whoami clear`),
  Settings (theme, wallpaper, clock format — persisted), About (honest
  prototype disclosure).
- **FS model:** single IndexedDB object store `nodes` (see ERD.md); paths
  resolved in JS. Quota errors surface honestly to the user.

## 5. Security considerations (Phase 0)

- U-PERM tokens are bearer tokens: whoever holds the string holds the right.
  Documented; transport protection is the caller's job (Phase 1: per-app
  isolated storage).
- Shell prototype runs with full browser authority — it is a *UX mock*, not a
  security boundary; the README says so.
- No credentials, no network calls, no eval of user input in Terminal
  (commands are a fixed dispatch table — tested implicitly by code review).

## 6. Scaling & cost

- Phase 0: static files, zero marginal cost at any user count.
- Phase 1 (planned): Cloudflare Pages + Workers + R2 or Supabase free tiers
  per the framework; per-user U-PERM checks are O(depth) in-memory — negligible.
- No cost incurred or committed in this slice.

## 7. Test strategy

- U-PERM: `node --test` suite — functional tests (grant/delegate/check/revoke/
  expiry) + adversarial tests (forgery, rights escalation, frozen-copy tamper,
  revoked reuse, ancestor revocation propagation, confused deputy, cross-object
  use, expiry bypass). Target: all green, counts reported in README.
- Shell: manual smoke checklist in slice/desktop/README.md (boot, windows,
  files persist after reload, settings persist). No fake automation claims.

## 8. Open risks

- Bearer-token theft model needs a Phase-1 story (per-app token vaults).
- Derivation trees can grow unboundedly — Phase 1 needs pruning/GC policy.
