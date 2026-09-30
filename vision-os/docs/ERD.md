# VisionOS — Entity-Relationship Diagram (ERD)

**Scope:** Phase 0. Two bounded contexts: (A) the shell prototype's IndexedDB
model, (B) the U-PERM in-memory authority model. Phase 0 has no server database.

## A. Shell prototype (IndexedDB, database `visionos`, version 1)

```
┌─────────────────────────────┐
│ nodes  (object store)       │
│ keyPath: "id" (auto-incr)   │
├─────────────────────────────┤
│ id: number (PK)             │
│ parentId: number|null (FK → nodes.id, null = root) │
│ name: string                │
│ type: "file" | "folder"     │
│ content: string (files)     │
│ mime: string                │
│ createdAt: number (epoch ms)│
│ updatedAt: number (epoch ms)│
└─────────────────────────────┘
        │ 1
        │ has many (self-referencing tree)
        ▼
┌─────────────────────────────┐
│ Indexes:                    │
│  - by_parent: parentId      │
│  - by_parent_name: [parentId, name] (unique: no dup names) │
└─────────────────────────────┘

┌─────────────────────────────┐
│ settings (object store)     │
│ keyPath: "key"              │
├─────────────────────────────┤
│ key: string (PK)            │
│   "theme" | "wallpaper" |   │
│   "clockFormat"             │
│ value: string               │
└─────────────────────────────┘

Relationships:
- nodes.parentId → nodes.id (self-referencing tree; folders contain files/folders)
- settings is a flat key-value store; no relations.
- Seed data on first boot: /Documents, /Pictures, /welcome.txt
```

## B. U-PERM authority model (in-memory; Phase 1 persists per-app grants)

```
┌──────────────────────────────────────┐
│ Authority (closure-private table)    │
├──────────────────────────────────────┤
│ token: string (256-bit random, PK)   │
│ objectId: string                      │
│ rights: Set<string> (frozen on read)  │
│ parentToken: string|null (FK → Authority.token) │
│ children: Set<token> (derivation tree)│
│ revoked: boolean                       │
│ expiresAt: number|null                 │
│ label: string (debug only)             │
└──────────────────────────────────────┘
        │ 1
        │ derives many (rights_child ⊆ rights_parent, enforced)
        ▼
  (same table, self-referencing derivation tree)

Invariants (all enforced + tested):
1. rights(child) ⊆ rights(parent) — else mint/derive throws RangeError.
2. revoke(t) ⇒ revoked = true for t and every transitive child.
3. check(t, obj, op) is true ⟺ t exists ∧ ¬revoked(t) ∧ ¬revoked(any ancestor)
   ∧ ¬expired(t) ∧ objectId(t) = obj ∧ op ∈ rights(t).
4. Tokens are never enumerable from outside the closure (no list-all API).
```

## C. Future (Phase 1+, not built)

Server model would add: `users`, `devices`, `apps`, `grants`
(grants = persisted U-PERM capabilities per app: app_id, object_id, rights,
parent_grant_id, revoked_at, expires_at — mirroring table B in Postgres with
RLS per the framework). Design-only; no schema shipped in Phase 0.
