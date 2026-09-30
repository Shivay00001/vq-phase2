# VisionOS — Phase 2 Foundation Slice

The operating-system layer of the VisionQuantech U-STACK vision. This is a
**multi-year platform effort** — this slice is Phase 0 (foundation), built per
`../SOFTWARE_BUILD_FRAMEWORK.md`: research first, then PRD/TRD/ERD/API contracts,
then an honest working slice. Nothing is faked.

## What's real vs. what's prototype

| Item | Status | Evidence |
|---|---|---|
| **U-PERM capability library** (`slice/uperm/`) | ✅ **REAL, production-grade core** | `node --test uperm.test.js` → **29/29 pass** (14 functional + 15 adversarial). Zero deps. Same module runs in Node, browsers, and the future policy daemon. |
| **Web shell** (`slice/desktop/`) | ⚠️ **UX CONCEPT PROTOTYPE** | Opens from `file://`, zero build. Real window manager, real IndexedDB file manager, real mini-shell terminal, real persisted settings. It is a web page demonstrating target UX — **not an OS**. Labeled as such in-app (top banner + About). |
| **Bare-metal kernel / drivers** | ❌ Not started — 5–10 year effort | See `docs/RESEARCH.md`: Fuchsia (100+ engineers, 10 yrs, still not mainstream), Redox (11 yrs, still alpha). We do not pretend otherwise. |
| **Competitor research** | ✅ Done | `docs/RESEARCH.md` — Fuchsia, Redox, SerenityOS, seL4, Haiku, Qubes, Puter/daedalOS/OS.js |
| **PRD / TRD / ERD / API contracts** | ✅ Done | `docs/` — includes honest phased roadmap (Shell → Runtime → Kernel) |

## Layout

```
vision-os/
├── README.md            ← you are here
├── docs/
│   ├── RESEARCH.md      ← competitor notes: what real OS projects cost, why multi-year
│   ├── PRD.md           ← vision, phased scope, non-goals, metrics
│   ├── TRD.md           ← architecture, U-PERM design, stack, test strategy
│   ├── ERD.md           ← IndexedDB schema (shell) + U-PERM authority model
│   └── API_CONTRACTS.md ← U-PERM API (real) · shell APIs (prototype) · IPC sketch (design-only)
└── slice/
    ├── uperm/           ← REAL: uperm.js + uperm.test.js (29 tests) + README.md
    └── desktop/         ← PROTOTYPE: index.html + styles.css + fs.js + os.js + README.md
```

## Try it (zero spend, nothing deployed)

```bash
# 1. Real subsystem — capability security core with adversarial tests
cd slice/uperm && node --test uperm.test.js

# 2. UX prototype — open in a browser (file:// works, no server needed)
#    slice/desktop/index.html
```

## Constraints honored

- **Zero spend.** No deployments, no paid services, no credentials anywhere.
- **Anti-vibe-coding.** Every visible control does something real or is labeled
  non-functional; `grep -ri "coming soon"` across the slice returns nothing.
- **Tests are real.** 15 adversarial cases: token forgery, rights escalation,
  snapshot tampering, revoked-reuse, revocation propagation to depth 5,
  confused-deputy (×2), cross-object use, expiry bypass, double-revoke,
  10k-mint collision check.
- **Nothing outside** `~/workspace/products/vq-phase2/vision-os/` was touched.

## Next steps (needs parent/user decisions — not started)

1. Phase 1 shell product: wire U-PERM per-app grants into the desktop prototype
   (each app holds only granted capabilities on the FS).
2. Phase 1 backend choice (Cloudflare/Supabase free tiers per framework) + sync.
3. Phase 2 runtime: Linux sandbox (namespaces/seccomp) + U-PERM policy daemon.
