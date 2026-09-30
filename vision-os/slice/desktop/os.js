/* VisionOS shell prototype — window manager, launcher, taskbar, apps.
   Every visible control is wired to something real. Zero dependencies. */
'use strict';

/* ── Settings state ─────────────────────────────── */
const ShellSettings = { theme: 'dark', wallpaper: '#1a2b4a', clockFormat: '24h' };

function applySettings() {
  document.body.classList.toggle('light', ShellSettings.theme === 'light');
  document.getElementById('desktop').style.background = ShellSettings.wallpaper;
}

/* ── Window manager ─────────────────────────────── */
const WM = (() => {
  let zTop = 10, seq = 0;
  const wins = new Map(); // id -> {el, appId, minimized}
  const desk = () => document.getElementById('windows');
  const bar = () => document.getElementById('taskbar-windows');

  function open(appId, opts) {
    opts = opts || {};
    const app = Apps.get(appId);
    if (!app) return null;
    const id = 'w' + (++seq);
    const el = document.createElement('div');
    el.className = 'window';
    el.dataset.winId = id;
    const W = opts.w || 640, H = opts.h || 420;
    const x = 60 + (seq * 36) % 220, y = 50 + (seq * 28) % 140;
    el.style.cssText = `left:${x}px;top:${y}px;width:${W}px;height:${H}px;z-index:${++zTop}`;
    el.innerHTML =
      `<div class="titlebar"><span class="wicon">${app.icon}</span><span class="wtitle">${app.title}</span>` +
      `<span class="wbtns"><button data-act="min" title="Minimize">–</button>` +
      `<button data-act="max" title="Maximize">▢</button>` +
      `<button data-act="close" title="Close">✕</button></span></div>` +
      `<div class="wbody"></div><div class="resize"></div>`;
    desk().appendChild(el);

    const rec = { el, appId, minimized: false, maxed: false, geom: null };
    wins.set(id, rec);

    el.querySelector('.titlebar').addEventListener('pointerdown', (e) => {
      if (e.target.closest('.wbtns')) return;
      focus(id);
      startDrag(e, el, rec);
    });
    el.addEventListener('pointerdown', () => focus(id), true);
    el.querySelector('[data-act="min"]').onclick = () => minimize(id);
    el.querySelector('[data-act="max"]').onclick = () => toggleMax(id);
    el.querySelector('[data-act="close"]').onclick = () => close(id);
    el.querySelector('.resize').addEventListener('pointerdown', (e) => {
      e.preventDefault(); e.stopPropagation(); focus(id); startResize(e, el);
    });

    try { app.render(el.querySelector('.wbody'), id, opts); }
    catch (err) { el.querySelector('.wbody').innerHTML = `<div class="err">App failed to start: ${esc(String(err.message || err))}</div>`; }

    renderTaskbar();
    return id;
  }

  function startDrag(e, el, rec) {
    if (rec.maxed) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    const ox = el.offsetLeft, oy = el.offsetTop;
    const mv = (ev) => {
      el.style.left = Math.max(0, ox + ev.clientX - sx) + 'px';
      el.style.top = Math.max(0, oy + ev.clientY - sy) + 'px';
    };
    const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); };
    addEventListener('pointermove', mv); addEventListener('pointerup', up);
  }

  function startResize(e, el) {
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, ow = el.offsetWidth, oh = el.offsetHeight;
    const mv = (ev) => {
      el.style.width = Math.max(280, ow + ev.clientX - sx) + 'px';
      el.style.height = Math.max(180, oh + ev.clientY - sy) + 'px';
    };
    const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); };
    addEventListener('pointermove', mv); addEventListener('pointerup', up);
  }

  function focus(id) {
    const r = wins.get(id); if (!r) return;
    if (r.minimized) { r.minimized = false; r.el.classList.remove('hidden'); }
    r.el.style.zIndex = ++zTop;
    document.querySelectorAll('.window').forEach((w) => w.classList.toggle('active', w === r.el));
    renderTaskbar();
  }

  function minimize(id) {
    const r = wins.get(id); if (!r) return;
    r.minimized = true; r.el.classList.add('hidden'); renderTaskbar();
  }

  function toggleMax(id) {
    const r = wins.get(id); if (!r) return;
    const el = r.el;
    if (!r.maxed) {
      r.geom = { l: el.style.left, t: el.style.top, w: el.style.width, h: el.style.height };
      el.style.left = '0px'; el.style.top = '0px';
      el.style.width = '100%'; el.style.height = 'calc(100% - 40px)';
      r.maxed = true;
    } else {
      Object.assign(el.style, { left: r.geom.l, top: r.geom.t, width: r.geom.w, height: r.geom.h });
      r.maxed = false;
    }
    focus(id);
  }

  function close(id) {
    const r = wins.get(id); if (!r) return;
    r.el.remove(); wins.delete(id); renderTaskbar();
  }

  function renderTaskbar() {
    const b = bar(); b.innerHTML = '';
    let anyActive = false;
    for (const [id, r] of wins) {
      const app = Apps.get(r.appId);
      const btn = document.createElement('button');
      btn.className = 'taskbtn' + (!r.minimized && r.el.style.zIndex == zTop ? ' active' : '');
      if (!r.minimized && r.el.style.zIndex == zTop) anyActive = true;
      btn.textContent = app.icon + ' ' + app.title;
      btn.title = r.minimized ? 'Restore' : 'Focus';
      btn.onclick = () => (r.minimized ? focus(id) : (r.el.style.zIndex == zTop ? minimize(id) : focus(id)));
      b.appendChild(btn);
    }
    if (!anyActive) document.querySelectorAll('.window').forEach((w) => w.classList.remove('active'));
  }

  return { open, close, focus, minimize };
})();

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── App registry ───────────────────────────────── */
const Apps = (() => {
  const reg = new Map();
  return {
    register: (id, def) => reg.set(id, def),
    get: (id) => reg.get(id),
    list: () => [...reg.entries()].map(([id, d]) => ({ id, ...d })),
  };
})();

/* ── App: Files ─────────────────────────────────── */
Apps.register('files', {
  title: 'Files', icon: '🗂',
  render(el, winId, opts) {
    let cwd = opts.dirId !== undefined ? opts.dirId : null;
    let selected = null;
    el.innerHTML =
      `<div class="ftoolbar">
         <button data-a="up">⬆ Up</button>
         <button data-a="mkdir">+ Folder</button>
         <button data-a="mkfile">+ File</button>
         <button data-a="rename">Rename</button>
         <button data-a="del">Delete</button>
         <span class="fpath"></span>
       </div><div class="fgrid"></div><div class="fstatus"></div>`;
    const grid = el.querySelector('.fgrid'), pathEl = el.querySelector('.fpath'), status = el.querySelector('.fstatus');
    const say = (m, bad) => { status.textContent = m; status.classList.toggle('bad', !!bad); };

    async function refresh() {
      const items = await FS.list(cwd);
      pathEl.textContent = await FS.pathOf(cwd);
      grid.innerHTML = '';
      selected = null;
      if (!items.length) grid.innerHTML = '<div class="fempty">Empty folder</div>';
      for (const n of items) {
        const d = document.createElement('div');
        d.className = 'fitem'; d.dataset.id = n.id;
        d.innerHTML = `<div class="fic">${n.type === 'folder' ? '📁' : '📄'}</div><div class="fname">${esc(n.name)}</div>`;
        d.onclick = () => { grid.querySelectorAll('.fitem').forEach((x) => x.classList.remove('sel')); d.classList.add('sel'); selected = n; };
        d.ondblclick = async () => {
          if (n.type === 'folder') { cwd = n.id; refresh(); }
          else WM.open('notes', { fileId: n.id });
        };
        grid.appendChild(d);
      }
      say(items.length + ' item(s)');
    }

    el.querySelector('[data-a="up"]').onclick = async () => {
      if (cwd == null) return;
      const n = await FS.get(cwd);
      cwd = n ? n.parentId : null; refresh();
    };
    el.querySelector('[data-a="mkdir"]').onclick = async () => {
      const name = prompt('Folder name:'); if (name == null) return;
      try { await FS.mkdir(cwd, name); refresh(); } catch (e) { say(friendlyErr(e), true); }
    };
    el.querySelector('[data-a="mkfile"]').onclick = async () => {
      const name = prompt('File name (e.g. todo.txt):'); if (name == null) return;
      try { await FS.writeFile(cwd, name, ''); refresh(); } catch (e) { say(friendlyErr(e), true); }
    };
    el.querySelector('[data-a="rename"]').onclick = async () => {
      if (!selected) return say('Select an item first.', true);
      const name = prompt('New name:', selected.name); if (name == null) return;
      try { await FS.rename(selected.id, name); refresh(); } catch (e) { say(friendlyErr(e), true); }
    };
    el.querySelector('[data-a="del"]').onclick = async () => {
      if (!selected) return say('Select an item first.', true);
      if (!confirm(`Delete "${selected.name}"?`)) return;
      try { await FS.remove(selected.id); refresh(); } catch (e) { say(friendlyErr(e), true); }
    };
    refresh();
  },
});

function friendlyErr(e) {
  const m = { 'name-exists': 'A file/folder with that name already exists.', 'name-required': 'Name cannot be empty.', 'not-found': 'Item not found.', 'quota': 'Storage quota exceeded — free some space.' };
  return m[e.message] || ('Error: ' + e.message);
}

/* ── App: Notes (text editor) ───────────────────── */
Apps.register('notes', {
  title: 'Notes', icon: '📝',
  render(el, winId, opts) {
    el.innerHTML =
      `<div class="ntoolbar"><button data-a="new">New</button><button data-a="save">Save</button><span class="fname-label"></span></div>
       <textarea class="nedit" placeholder="Type here…"></textarea><div class="fstatus"></div>`;
    const ta = el.querySelector('.nedit'), label = el.querySelector('.fname-label'), status = el.querySelector('.fstatus');
    let fileId = opts.fileId || null, dirty = false;
    ta.oninput = () => { dirty = true; };
    async function load(id) {
      const n = await FS.get(id);
      if (!n || n.type !== 'file') { status.textContent = 'File not found.'; return; }
      fileId = id; ta.value = n.content || ''; dirty = false;
      label.textContent = n.name + '  (' + await FS.pathOf(n.parentId) + ')';
      status.textContent = 'Loaded.';
    }
    el.querySelector('[data-a="new"]').onclick = async () => {
      const name = prompt('New note name:', 'untitled.txt'); if (name == null) return;
      try {
        const docs = await FS.resolvePath('/Documents');
        const n = await FS.writeFile(docs ? docs.id : null, name, '');
        load(n.id);
      } catch (e) { status.textContent = friendlyErr(e); }
    };
    el.querySelector('[data-a="save"]').onclick = async () => {
      if (!fileId) { status.textContent = 'Nothing open — click New first.'; status.classList.add('bad'); return; }
      try { await FS.updateFile(fileId, ta.value); dirty = false; status.textContent = 'Saved.'; status.classList.remove('bad'); }
      catch (e) { status.textContent = friendlyErr(e); status.classList.add('bad'); }
    };
    if (fileId) load(fileId);
    else { label.textContent = '(no file open)'; }
  },
});

/* ── App: Terminal (real mini-shell over FS) ─────── */
Apps.register('terminal', {
  title: 'Terminal', icon: '⌨',
  render(el) {
    el.innerHTML = `<div class="term"></div>`;
    const term = el.querySelector('.term');
    let cwd = null;
    const print = (html) => { const d = document.createElement('div'); d.innerHTML = html; term.appendChild(d); term.scrollTop = term.scrollHeight; };
    const inputLine = () => {
      const d = document.createElement('div');
      d.className = 'tline';
      d.innerHTML = `<span class="prompt">visionos:<span class="tpath"></span>$ </span><input class="tin" spellcheck="false">`;
      term.appendChild(d);
      const inp = d.querySelector('.tin');
      FS.pathOf(cwd).then((p) => { d.querySelector('.tpath').textContent = p; });
      inp.focus();
      inp.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        const cmd = inp.value; inp.disabled = true;
        await run(cmd);
        inputLine();
      });
      term.scrollTop = term.scrollHeight;
    };
    term.addEventListener('click', () => { const i = term.querySelector('.tin:not([disabled])'); if (i) i.focus(); });

    async function run(raw) {
      const parts = raw.trim().match(/(?:[^\s"]+|"[^"]*")+/g) || [];
      const cmd = (parts[0] || '').toLowerCase();
      const args = parts.slice(1).map((a) => a.replace(/^"|"$/g, ''));
      const out = (t) => print(esc(t));
      try {
        switch (cmd) {
          case '': break;
          case 'help':
            out('commands: help, ls, cd <dir|..|/>, pwd, cat <file>, echo <text>, write <file> <text>, mkdir <name>, rm <name>, date, whoami, clear');
            break;
          case 'ls': {
            const items = await FS.list(cwd);
            out(items.length ? items.map((n) => (n.type === 'folder' ? n.name + '/' : n.name)).join('   ') : '(empty)');
            break;
          }
          case 'pwd': out(await FS.pathOf(cwd)); break;
          case 'cd': {
            const dest = args[0] || '/';
            if (dest === '/') { cwd = null; break; }
            if (dest === '..') { const n = cwd == null ? null : await FS.get(cwd); cwd = n ? n.parentId : null; break; }
            const kids = await FS.list(cwd);
            const hit = kids.find((k) => k.name === dest && k.type === 'folder');
            if (!hit) out(`cd: no such directory: ${dest}`); else cwd = hit.id;
            break;
          }
          case 'cat': {
            if (!args[0]) { out('usage: cat <file>'); break; }
            const kids = await FS.list(cwd);
            const hit = kids.find((k) => k.name === args[0] && k.type === 'file');
            if (!hit) out(`cat: no such file: ${args[0]}`);
            else { const n = await FS.get(hit.id); (n.content || '').split('\n').forEach((l) => out(l)); }
            break;
          }
          case 'echo': out(args.join(' ')); break;
          case 'write': {
            if (args.length < 2) { out('usage: write <file> <text>'); break; }
            const kids = await FS.list(cwd);
            const hit = kids.find((k) => k.name === args[0]);
            if (hit && hit.type === 'file') { await FS.updateFile(hit.id, args.slice(1).join(' ')); out('updated.'); }
            else if (!hit) { await FS.writeFile(cwd, args[0], args.slice(1).join(' ')); out('created.'); }
            else out('write: is a directory');
            break;
          }
          case 'mkdir': {
            if (!args[0]) { out('usage: mkdir <name>'); break; }
            try { await FS.mkdir(cwd, args[0]); out('created.'); } catch (e) { out(friendlyErr(e)); }
            break;
          }
          case 'rm': {
            if (!args[0]) { out('usage: rm <name>'); break; }
            const kids = await FS.list(cwd);
            const hit = kids.find((k) => k.name === args[0]);
            if (!hit) out(`rm: no such file: ${args[0]}`);
            else { await FS.remove(hit.id); out('deleted.'); }
            break;
          }
          case 'date': out(new Date().toString()); break;
          case 'whoami': out('guest (prototype — no real users yet)'); break;
          case 'clear': term.innerHTML = ''; break;
          default: out(`unknown command: ${cmd} — type "help"`);
        }
      } catch (e) { out(friendlyErr(e)); }
    }

    print('VisionOS shell prototype — mini terminal. Type <b>help</b>.');
    inputLine();
  },
});

/* ── App: Settings ──────────────────────────────── */
Apps.register('settings', {
  title: 'Settings', icon: '⚙',
  render(el) {
    el.innerHTML =
      `<div class="setrow"><label>Theme</label>
         <button data-k="theme" data-v="dark">Dark</button><button data-k="theme" data-v="light">Light</button></div>
       <div class="setrow"><label>Wallpaper</label>
         <button data-k="wallpaper" data-v="#1a2b4a" style="background:#1a2b4a"> </button>
         <button data-k="wallpaper" data-v="#3a1a4a" style="background:#3a1a4a"> </button>
         <button data-k="wallpaper" data-v="#0f3d2e" style="background:#0f3d2e"> </button>
         <button data-k="wallpaper" data-v="#4a2a10" style="background:#4a2a10"> </button></div>
       <div class="setrow"><label>Clock</label>
         <button data-k="clockFormat" data-v="12h">12-hour</button><button data-k="clockFormat" data-v="24h">24-hour</button></div>
       <div class="fstatus">Settings persist in IndexedDB across reloads.</div>`;
    el.querySelectorAll('button[data-k]').forEach((b) => {
      b.onclick = async () => {
        ShellSettings[b.dataset.k] = b.dataset.v;
        await FS.Settings.set(b.dataset.k, b.dataset.v);
        applySettings(); tickClock();
      };
    });
  },
});

/* ── App: About ─────────────────────────────────── */
Apps.register('about', {
  title: 'About', icon: '◈',
  render(el) {
    el.innerHTML = `<div class="about">
      <div class="about-logo">◈</div><h2>VisionOS</h2>
      <p><b>UX CONCEPT PROTOTYPE</b> — Phase 0 of the VisionQuantech U-STACK platform track.</p>
      <p>This is a web page demonstrating the target desktop experience. It is <b>not</b> an operating system:
      it runs inside your browser on top of your real OS, with files in IndexedDB.</p>
      <p><b>What is real in this slice:</b> the <b>U-PERM</b> capability security library
      (<code>slice/uperm/</code>) — grant/delegate/revoke permission tokens with
      revocation propagation, verified by 29 unit + adversarial tests
      (<code>node --test uperm.test.js</code>).</p>
      <p>Docs: <code>docs/PRD.md · TRD.md · ERD.md · API_CONTRACTS.md · RESEARCH.md</code></p>
      <p class="dim">Zero spend · nothing deployed · no credentials · no network calls.</p></div>`;
  },
});

/* ── Desktop chrome: icons, launcher, clock ─────── */
function buildDesktop() {
  const iconsEl = document.getElementById('icons');
  for (const a of Apps.list()) {
    const d = document.createElement('div');
    d.className = 'dicon';
    d.innerHTML = `<div class="dic">${a.icon}</div><div class="dlabel">${a.title}</div>`;
    d.ondblclick = () => WM.open(a.id);
    d.onclick = () => { iconsEl.querySelectorAll('.dicon').forEach((x) => x.classList.remove('sel')); d.classList.add('sel'); };
    iconsEl.appendChild(d);
  }
  document.getElementById('launcher-btn').onclick = () => {
    const l = document.getElementById('launcher');
    l.classList.toggle('hidden');
    if (!l.classList.contains('hidden') && !l.children.length) {
      for (const a of Apps.list()) {
        const b = document.createElement('button');
        b.className = 'litem';
        b.innerHTML = `<span>${a.icon}</span> ${a.title}`;
        b.onclick = () => { WM.open(a.id); l.classList.add('hidden'); };
        l.appendChild(b);
      }
    }
  };
  document.getElementById('desktop').addEventListener('pointerdown', (e) => {
    if (!e.target.closest('#launcher') && !e.target.closest('#launcher-btn'))
      document.getElementById('launcher').classList.add('hidden');
  });
}

function tickClock() {
  const el = document.getElementById('clock');
  const d = new Date();
  el.textContent = ShellSettings.clockFormat === '12h'
    ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : d.toTimeString().slice(0, 5);
}

/* ── Boot ───────────────────────────────────────── */
(async function boot() {
  try {
    await FS.init();
    const saved = await FS.Settings.all();
    Object.assign(ShellSettings, saved);
  } catch (e) {
    document.querySelector('.boot-sub').textContent = 'Storage unavailable: ' + e.message;
    return;
  }
  applySettings();
  buildDesktop();
  tickClock();
  setInterval(tickClock, 10000);
  setTimeout(() => {
    document.getElementById('boot').classList.add('hidden');
    document.getElementById('desktop').classList.remove('hidden');
    WM.open('about', { w: 560, h: 440 });
  }, 1400);
})();
