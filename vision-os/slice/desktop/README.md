# VisionOS Shell — UX CONCEPT PROTOTYPE

> **This is a web page, not an operating system.** It runs inside your browser
> on top of your real OS. Nothing here touches hardware, processes, or drivers.
> Its purpose is to demonstrate the *target desktop experience* for VisionOS
> while the real platform work (U-PERM, runtime, kernel) proceeds on honest
> multi-year timelines. See `../../docs/RESEARCH.md` for why.

## What it is

A zero-dependency, zero-build desktop shell: open `index.html` in any modern
browser (works from `file://`). Boot screen → desktop with:

- **Window manager** — drag, resize, minimize, maximize, close, z-order focus
- **Taskbar + launcher** — live window buttons, app launcher menu, clock
- **Apps (all real, every button wired):**
  - **Files** — create/rename/delete files & folders, navigate, double-click to
    open text files; backed by **IndexedDB** (survives reload)
  - **Notes** — text editor; New/Save to the IndexedDB filesystem
  - **Terminal** — real mini-shell over the same FS:
    `help ls cd pwd cat echo write mkdir rm date whoami clear`
  - **Settings** — theme (dark/light), wallpaper color, 12/24h clock; persisted
  - **About** — honest disclosure of what this is and isn't

There are **no dead buttons and no "coming soon" placeholders** — anything
visible does something real. Quota errors surface honestly in the UI.

## Smoke checklist (manual)

1. Open `index.html` → boot screen ~1.4s → desktop + About window.
2. Drag/maximize/minimize/restore/close the About window; taskbar button toggles.
3. Double-click Files icon → create folder → create file → double-click file → edits in Notes → Save → reload page → content persists.
4. Terminal: `mkdir demo`, `cd demo`, `write hi.txt hello`, `cat hi.txt`, `ls`, `cd ..`, `pwd`.
5. Settings: switch theme + wallpaper + clock → reload → settings persist.
6. Banner at top always reads "UX CONCEPT PROTOTYPE".

## Files

| File | Role |
|---|---|
| `index.html` | Shell + banner (prototype label) |
| `styles.css` | All styling, dark/light themes |
| `fs.js` | IndexedDB filesystem + settings store (see `docs/ERD.md`) |
| `os.js` | Window manager, launcher, taskbar, all five apps |

## What this prototype deliberately does NOT do

- No process isolation, no real permissions (the browser is the security boundary)
- No networking, no accounts, no cloud sync (Phase 1 scope)
- Per-app capability grants (U-PERM enforcement in the shell) are Phase 1 work —
  the library API is ready (`../uperm/`), the shell wiring is not yet done
