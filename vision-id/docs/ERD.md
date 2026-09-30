# VisionID — Entity-Relationship Diagram

SQLite (slice). All timestamps UTC ISO-8601. `TEXT PRIMARY KEY` ids are `usr_`, `dev_`, `fam_`, `cli_` prefixed randoms unless noted.

```
users 1───* refresh_tokens      users 1───* devices
  │              │                    │
  │              │ family_id ─┐       │  users 1───* auth_codes
  │              │            │       │  (user who consented)
  │              └────────────┴── theft: reuse of superseded token
  │                               revokes whole family
  │
  └───* audit_log (actor; actor may be NULL for pre-auth events)
  └───* rate_limit_counters (keyed by scope+key, windowed)

clients 1───* auth_codes (client the code was issued to)
```

## Tables

### users
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `usr_<32 hex>` |
| email | TEXT UNIQUE NOT NULL | lowercased, max 254 |
| password_hash | TEXT NOT NULL | `$argon2id$...` (or `$2b$` legacy → rehash on login) |
| status | TEXT NOT NULL | `active` \| `locked` \| `disabled` (default `active`) |
| roles | TEXT NOT NULL | JSON array, e.g. `["user"]`; `admin`,`support` grant scopes |
| created_at / updated_at | TEXT NOT NULL | UTC |

### refresh_tokens
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `rt_<32 hex>` (opaque handle id, not the secret) |
| user_id | TEXT NOT NULL → users.id | indexed |
| family_id | TEXT NOT NULL | `fam_<hex>`; all rotations of one session share it |
| token_hash | TEXT UNIQUE NOT NULL | SHA-256 hex of the 256-bit secret (secret never stored) |
| device_id | TEXT NULL → devices.id | device the session is bound to |
| scopes | TEXT NOT NULL | space-delimited, frozen at issue |
| issued_at / expires_at | TEXT NOT NULL | TTL 30 days, sliding on rotation |
| status | TEXT NOT NULL | `active` \| `superseded` \| `revoked` |
| replaced_by | TEXT NULL | id of the rotation child (audit chain) |

**Rotation invariant:** refresh with an `active` token → it becomes `superseded`, child `active`.
Refresh with `superseded`/`revoked` token → **whole family → `revoked`** + audit `token_reuse_detected`.

### auth_codes
| Column | Type | Notes |
|---|---|---|
| code_hash | TEXT PK | SHA-256 of the code (code never stored) |
| client_id | TEXT NOT NULL → clients.client_id | |
| user_id | TEXT NOT NULL → users.id | |
| redirect_uri | TEXT NOT NULL | must exactly match client registration |
| scopes | TEXT NOT NULL | consented scopes |
| code_challenge / challenge_method | TEXT NULL | PKCE (`S256`); required for public clients |
| auth_time | TEXT NOT NULL | when the user authenticated |
| expires_at | TEXT NOT NULL | 10 minutes |
| used | INTEGER NOT NULL | 0/1; second use → theft path |

### devices
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `dev_<32 hex>` |
| user_id | TEXT NOT NULL → users.id | indexed |
| device_name / platform | TEXT NOT NULL | user-supplied label + `android`/`ios`/`web`/`desktop` |
| public_key_jwk | TEXT NOT NULL | Ed25519 public JWK of the device keypair |
| challenge / challenge_expires | TEXT NULL | enrollment proof-of-possession challenge |
| status | TEXT NOT NULL | `pending` → `active` \| `revoked` |
| enrolled_at / last_seen_at | TEXT NOT NULL/NULL | |

### clients (OIDC relying parties)
| Column | Type | Notes |
|---|---|---|
| client_id | TEXT PK | `cli_<hex>` |
| client_secret_hash | TEXT NULL | NULL = public client (PKCE mandatory) |
| name | TEXT NOT NULL | |
| redirect_uris | TEXT NOT NULL | JSON array; exact match enforced |
| allowed_scopes | TEXT NOT NULL | space-delimited ceiling for this client |
| confidential | INTEGER NOT NULL | 0/1 |

### audit_log (append-only; no UPDATE/DELETE code path)
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| ts | TEXT NOT NULL | UTC, indexed |
| actor_user_id | TEXT NULL | NULL for anonymous/pre-auth events |
| event | TEXT NOT NULL | e.g. `login_success`, `token_reuse_detected` (see list) |
| ip | TEXT NULL | client IP as seen by server |
| details | TEXT NULL | JSON; never contains secrets/passwords |

Event catalog: `register`, `register_rejected_weak_password`, `login_success`, `login_failure`,
`account_locked`, `token_refreshed`, `token_reuse_detected`, `logout`, `auth_code_issued`,
`auth_code_exchanged`, `auth_code_reuse_detected`, `device_enroll_started`,
`device_enroll_completed`, `device_revoked`, `scope_denied`, `rate_limited`.

### rate_limit_counters
| Column | Type | Notes |
|---|---|---|
| key | TEXT PK | e.g. `login:ip:1.2.3.4`, `login:email:a@b.c` |
| window_start | TEXT NOT NULL | |
| count | INTEGER NOT NULL | |
