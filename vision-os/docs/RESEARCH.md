# VisionOS — Competitor & Precedent Research

Researched 2026-09-26. Sources: Wikipedia (SerenityOS, seL4), Redox OS FAQ, press coverage of Fuchsia, Puter/daedalOS community write-ups, seL4 whitepaper (seL4.systems). Full URLs in the source column; verify before citing externally.

## 1. What real OS projects actually cost (time, people, money)

| Project | Started | Team | Status (2026) | Key fact |
|---|---|---|---|---|
| **Google Fuchsia** | 2016 (Zircon kernel) | 100+ engineers at peak; 16% laid off Jan 2023 | Shipped only on Nest Hub / Nest Hub Max (headless-ish targets); never a mainstream phone/desktop OS after ~10 years | ~1.5M LOC (excl. third-party); 6 CPU archs; 150+ public design docs. Even Google-scale money + talent could not ship a general-purpose OS in under a decade. |
| **Redox OS** | 2015 | Open-source community | Still **alpha** after 11 years; "not ready for daily usage" per its own FAQ | Rust microkernel. Lesson: memory-safe language doesn't shortcut the driver/hardware long tail. |
| **SerenityOS** | 2018 | 1 founder → 1000+ contributors (hobbyist) | Still explicitly "does not cater to non-technical users"; founder stepped down 2024 to work on the Ladybird browser | Lesson: a from-scratch desktop OS is a *community-decade* project. The browser (Ladybird) became the shippable product, not the OS. |
| **seL4** | 2009 (verification complete) | Research lab (UNSW/NICTA, now Trustworthy Systems) | World's first formally verified OS kernel; used in DARPA HACMS drones, medical, automotive | Lesson: the *kernel* can be small and provable (~9k LOC) — capability-based security is the proven path. But seL4 is a kernel, not an OS: everything above it (drivers, FS, GUI) is still years of work. |
| **Haiku** | 2001 | Community | Still beta after 25 years | Lesson: binary/driver compatibility with an existing ecosystem (BeOS) helps but doesn't make it fast. |
| **Qubes OS** | 2012 | Small commercial team (Invisible Things Lab) | Niche but real product | Lesson: a *security story* (compartmentalization) can be the whole product differentiator — directly relevant to U-PERM. |

**Takeaway for VisionOS:** a bare-metal, general-purpose OS is a 5–10 year effort even with serious funding. The honest paths that have actually shipped are (a) a verified microkernel with a narrow target (seL4 → defense/medical), or (b) a *web/cloud shell* that reuses the existing OS underneath (Puter).

## 2. The web-desktop layer: what "OS in a browser" really is

| Project | What it is | Scale proof |
|---|---|---|
| **Puter** ("Internet OS") | Open-source browser desktop: window manager, file manager, app store, OnlyOffice, self-hostable | Launched Mar 2024 as a personal project → 36,000 GitHub stars, 305 contributors. Press (XDA, MakeUseOf) treats it as a real product category. |
| **daedalOS** | Fully client-side web desktop by Dustin Brett (personal site as demo) | One developer; shows a single dev can ship a credible desktop UX in a browser. |
| **OS.js** | Older web-desktop framework | Shows the category is ~10 years old — the tech is commodity. |

**Takeaway:** a browser shell is a *UX product*, not an OS. It can be built by a tiny team in months, needs zero hardware drivers, and is the only VisionOS slice that can honestly exist in 2026. It must never be marketed as an operating system — Puter itself is careful to say it "still depends on an existing OS underneath."

## 3. Capability security: the one subsystem worth building first

- **seL4 model:** every kernel object (memory, thread, IPC endpoint) is named only by an *unforgeable capability token* encoding allowed operations. No ambient authority. Capabilities can be minted with diminished rights, delegated, and revoked (revocation propagates through the derivation tree). IPC fast path ~300 cycles on ARM.
- **Why it matters for VisionOS:** capability discipline solves the *confused deputy problem* that plagues ACL systems (Linux "capabilities" are ACLs, not object capabilities — seL4 docs explicitly call this out). A capability core (U-PERM) is implementable, testable, and formally specifiable *today*, and it ports upward: same API in the web shell (per-app permissions), in a future Linux-based runtime, and eventually in a real kernel.
- **Precedents for the pattern:** E language, Cap'n Proto (capability RPC), Tahoe-LAFS (capability file storage), Fuchsia's Zircon (capability-based handles).

## 4. What to copy / what to beat / what to avoid

- **Copy:** seL4's capability semantics (mint/derive/revoke, derivation tree); Puter's "it just opens in a tab" distribution; Qubes' security-as-the-product positioning.
- **Beat:** Puter/daedalOS have no real permission model — apps run with ambient browser authority. A web shell where every app holds *only the capabilities it was granted* (U-PERM enforced) is a genuine differentiator.
- **Avoid:** promising a bare-metal kernel on any near-term timeline; "coming soon" buttons; calling a web page an OS.

## 5. Honest effort sizing for VisionOS

| Layer | Realistic scope | Effort class |
|---|---|---|
| U-PERM capability library | Done in this slice (JS, tested) | weeks |
| Web shell prototype (this slice) | UX concept, real local apps | weeks |
| VisionOS Shell product (polished, multi-user, sync) | 12–18 months, small team | product |
| VisionOS Runtime (capability sandbox on Linux: namespaces/seccomp + U-PERM policy) | 2–3 years | platform |
| VisionOS Kernel (bare metal: scheduler, MM, drivers) | 5–10 years, dedicated team | moonshot |

**Bottom line:** the OS is a multi-year platform bet. The correct 2026 move is the capability security core (real, tested) + the shell UX (prototype) — which is exactly what this slice delivers.
