# U-PERM — VisionOS capability security core

**Status: REAL subsystem code. Tested. Not a mock.**

Object-capability permission library: every privileged action requires
presenting an unguessable bearer token that names one object and the exact
operations allowed on it. No ambient authority — the confused-deputy class of
bugs is structurally impossible (proven by test ADV-09/ADV-10).

## Run the tests

```bash
node --test uperm.test.js
```

Zero dependencies. Node ≥ 18. Expected: **29/29 pass**
(14 functional, 15 adversarial).

## API (summary)

| Call | Effect |
|---|---|
| `createAuthority()` | Isolated capability table (closures; tokens never enumerable) |
| `mint(objectId, rights, opts?)` | Root capability → 256-bit token |
| `derive(parent, rightsSubset, opts?)` | Child with diminished rights; `RangeError` on escalation |
| `check(token, objectId, op)` | `boolean`; deny-by-default, never throws on deny path |
| `revoke(token)` | Revokes token **and all transitive descendants**; returns count |
| `inspect(token)` | Frozen debug snapshot (mutating it changes nothing) |

Full contracts: `../../docs/API_CONTRACTS.md`.

## Adversarial coverage (all passing)

- ADV-01/02 forged & malformed tokens rejected
- ADV-03/04 rights-escalation via `derive` (incl. case/superset tricks) → `RangeError`
- ADV-05 `inspect()` snapshot tampering cannot alter stored grants
- ADV-06 revoked token reuse denied
- ADV-07 revocation propagates through 5-deep delegation chains
- ADV-08 deriving from revoked/expired parent refused
- ADV-09/10 confused deputy: deputy must spend the *caller's* capability; no ambient lookup API exists
- ADV-11 cross-object token use denied
- ADV-12 expiry poisons the whole lineage; no re-derive bypass
- ADV-13 double-revoke safe · ADV-14 10k mints, zero collisions · ADV-15 non-string args deny, never throw

## Known limits (honest)

- Bearer-token model: holding the string = holding the right. Guard tokens in
  transit/storage; per-app vaults are a Phase-1 item.
- In-memory only; no persistence yet (Phase 1 persists grants in Postgres).
- No derivation-tree GC; unbounded delegation chains are the caller's problem
  for now.
