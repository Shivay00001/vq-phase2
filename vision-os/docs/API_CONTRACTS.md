# VisionOS — API Contracts

## 1. U-PERM capability library (REAL — implemented in `slice/uperm/uperm.js`)

All functions synchronous. Errors: `TypeError` (bad argument shape),
`RangeError` (rights escalation attempt), `Error("unknown capability")`
only from `revoke`/`inspect` on bogus tokens — `check` never throws, it
returns `false` (deny-by-default).

### `createAuthority() → Authority`
Creates an isolated capability table. Multiple authorities never share tokens.

### `authority.mint(objectId: string, rights: string[], opts?: { label?, ttlMs?, expiresAt? }) → string`
Mints a root capability token for `objectId` with exactly `rights`.
- `objectId`: non-empty string naming the protected object.
- `rights`: non-empty array of non-empty strings, e.g. `["file:read","file:write"]`.
- Returns: 256-bit random hex token string (bearer token — guard it).

### `authority.derive(parentToken: string, rights: string[], opts?: { label?, ttlMs? }) → string`
Mints a child capability with diminished-or-equal rights.
- Throws `RangeError` if any requested right is not in the parent's rights
  (escalation attempt) or if the parent is revoked/expired/unknown.
- Child records `parentToken`; revoking any ancestor revokes the child.

### `authority.check(token: string, objectId: string, operation: string) → boolean`
`true` iff: token known ∧ not expired ∧ neither it nor any ancestor revoked ∧
`objectId` matches ∧ `operation` ∈ rights. Never throws on the deny path.

### `authority.revoke(token: string) → number`
Marks token and **all transitive descendants** revoked. Returns count revoked.
Throws `Error` on unknown token.

### `authority.inspect(token: string) → { objectId, rights: string[], label, expiresAt, revoked, depth } | null`
Debug/introspection helper. Returns a **frozen deep copy** — mutating it
cannot affect stored rights (adversarial-tested). Returns `null` for unknown
tokens. There is intentionally **no list-all-tokens API**.

### Rights vocabulary (convention, not enforced)
`"<resource>:<verb>"`, e.g. `file:read`, `file:write`, `file:delete`,
`net:connect`, `camera:capture`. Applications define their own verbs; the
library only enforces subset relations.

### Worked example
```js
const { createAuthority } = require('./uperm.js'); // or import in browser
const auth = createAuthority();
const root  = auth.mint('doc:report', ['file:read', 'file:write'], { label: 'owner' });
const ro    = auth.derive(root, ['file:read'], { label: 'viewer' });
auth.check(ro, 'doc:report', 'file:read');   // true
auth.check(ro, 'doc:report', 'file:write');  // false — not granted
auth.check(ro, 'doc:other',  'file:read');   // false — wrong object
auth.revoke(root);                            // revokes root AND ro
auth.check(ro, 'doc:report', 'file:read');   // false — ancestor revoked
```

---

## 2. Shell prototype internal APIs (PROTOTYPE — `slice/desktop/`)

These are in-page JS modules, not network APIs. Documented for Phase-1 reuse.

### FS API (`fs.js`)
- `FS.init() → Promise<void>` — opens IndexedDB, seeds `/Documents`, `/Pictures`, `/welcome.txt` on first run.
- `FS.list(parentId) → Promise<Node[]>` — children ordered folders-first.
- `FS.mkdir(parentId, name)`, `FS.writeFile(parentId, name, content, mime?)`,
  `FS.readFile(id) → Promise<string>`, `FS.rename(id, newName)`,
  `FS.remove(id)` (recursive for folders), `FS.resolve(path) → Promise<Node|null>`.
- Errors: `Error("name-exists")`, `Error("not-found")`, `Error("quota"`) —
  surfaced honestly in UI.

### Window manager API (`os.js`)
- `WM.open(appId, opts?) → winId`, `WM.close(winId)`, `WM.focus(winId)`,
  `WM.minimize/maximize/restore(winId)`.
- Apps register via `Apps.register(id, { title, icon, render(el, win) })`.
- Every control in the chrome (drag bar, resize handle, taskbar buttons,
  launcher) is wired — no dead buttons (verified in smoke checklist).

### Settings API
- `Settings.get(key) / Settings.set(key, value)` → persisted to IndexedDB
  `settings` store. Keys: `theme` (`dark|light`), `wallpaper` (css color),
  `clockFormat` (`12h|24h`).

---

## 3. Future kernel IPC sketch (DESIGN ONLY — not implemented)

Phase-3 IPC would mirror U-PERM over message passing: endpoints named by
capabilities, `send(capToken, message)` checked like `check()`. This section
is a placeholder so Phase-0 naming stays compatible; **no code exists**.
