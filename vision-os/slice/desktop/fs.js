/* VisionOS shell prototype — IndexedDB file system. Zero dependencies. */
'use strict';
const FS = (() => {
  const DB_NAME = 'visionos', DB_VER = 1;
  let db = null;

  function open() {
    return new Promise((resolve, reject) => {
      const rq = indexedDB.open(DB_NAME, DB_VER);
      rq.onupgradeneeded = (e) => {
        const d = e.target.result;
        const ns = d.createObjectStore('nodes', { keyPath: 'id', autoIncrement: true });
        ns.createIndex('by_parent', 'parentId', { unique: false });
        ns.createIndex('by_parent_name', ['parentId', 'name'], { unique: true });
        d.createObjectStore('settings', { keyPath: 'key' });
      };
      rq.onsuccess = (e) => { db = e.target.result; resolve(); };
      rq.onerror = () => reject(rq.error);
    });
  }

  function req(promiseLike) {
    return new Promise((resolve, reject) => {
      promiseLike.onsuccess = (e) => resolve(e.target.result);
      promiseLike.onerror = () => reject(promiseLike.error);
    });
  }

  async function seedIfEmpty() {
    const count = await req(db.transaction('nodes').objectStore('nodes').count());
    if (count > 0) return;
    const t = db.transaction('nodes', 'readwrite');
    const s = t.objectStore('nodes');
    const now = Date.now();
    const put = (n) => new Promise((res, rej) => {
      const r = s.add(n); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
    const docs = await put({ parentId: null, name: 'Documents', type: 'folder', content: '', mime: '', createdAt: now, updatedAt: now });
    await put({ parentId: null, name: 'Pictures', type: 'folder', content: '', mime: '', createdAt: now, updatedAt: now });
    await put({ parentId: docs, name: 'notes.txt', type: 'file', content: 'Welcome to VisionOS shell prototype.\n\nEverything here runs in your browser and is stored in IndexedDB.\nTry the Terminal app: type "help".\n', mime: 'text/plain', createdAt: now, updatedAt: now });
    await put({ parentId: null, name: 'welcome.txt', type: 'file', content: 'VisionOS — UX CONCEPT PROTOTYPE.\n\nThis is a web page demonstrating the target desktop experience.\nIt is NOT an operating system. The real subsystem in this slice is\nthe U-PERM capability library (slice/uperm/) with 29 passing tests.\n', mime: 'text/plain', createdAt: now, updatedAt: now });
    await new Promise((res, rej) => { t.oncomplete = res; t.onerror = () => rej(t.error); });
  }

  async function list(parentId) {
    const s = db.transaction('nodes').objectStore('nodes');
    const idx = s.index('by_parent');
    const items = await req(idx.getAll(parentId === undefined ? null : parentId));
    return items.sort((a, b) =>
      (a.type === b.type) ? a.name.localeCompare(b.name) : (a.type === 'folder' ? -1 : 1));
  }

  async function get(id) {
    const r = await req(db.transaction('nodes').objectStore('nodes').get(id));
    return r || null;
  }

  async function nameExists(parentId, name) {
    const s = db.transaction('nodes').objectStore('nodes');
    const r = await req(s.index('by_parent_name').get([parentId, name]));
    return !!r;
  }

  async function mkdir(parentId, name) {
    name = (name || '').trim();
    if (!name) throw new Error('name-required');
    if (await nameExists(parentId, name)) throw new Error('name-exists');
    const now = Date.now();
    const id = await req(db.transaction('nodes', 'readwrite').objectStore('nodes')
      .add({ parentId, name, type: 'folder', content: '', mime: '', createdAt: now, updatedAt: now }));
    return get(id);
  }

  async function writeFile(parentId, name, content, mime) {
    name = (name || '').trim();
    if (!name) throw new Error('name-required');
    if (await nameExists(parentId, name)) throw new Error('name-exists');
    const now = Date.now();
    try {
      const id = await req(db.transaction('nodes', 'readwrite').objectStore('nodes')
        .add({ parentId, name, type: 'file', content: content || '', mime: mime || 'text/plain', createdAt: now, updatedAt: now }));
      return get(id);
    } catch (e) {
      if (e && e.name === 'QuotaExceededError') throw new Error('quota');
      throw e;
    }
  }

  async function updateFile(id, content) {
    const node = await get(id);
    if (!node || node.type !== 'file') throw new Error('not-found');
    node.content = content;
    node.updatedAt = Date.now();
    try {
      await req(db.transaction('nodes', 'readwrite').objectStore('nodes').put(node));
    } catch (e) {
      if (e && e.name === 'QuotaExceededError') throw new Error('quota');
      throw e;
    }
    return node;
  }

  async function rename(id, newName) {
    newName = (newName || '').trim();
    if (!newName) throw new Error('name-required');
    const node = await get(id);
    if (!node) throw new Error('not-found');
    if (node.name !== newName && await nameExists(node.parentId, newName)) throw new Error('name-exists');
    node.name = newName;
    node.updatedAt = Date.now();
    await req(db.transaction('nodes', 'readwrite').objectStore('nodes').put(node));
    return node;
  }

  async function remove(id) {
    const node = await get(id);
    if (!node) throw new Error('not-found');
    // recursive delete for folders
    const stack = [id];
    const t = db.transaction('nodes', 'readwrite');
    const s = t.objectStore('nodes');
    const idx = s.index('by_parent');
    while (stack.length) {
      const cur = stack.pop();
      const kids = await req(idx.getAll(cur));
      for (const k of kids) stack.push(k.id);
      await req(s.delete(cur));
    }
    await new Promise((res, rej) => { t.oncomplete = res; t.onerror = () => rej(t.error); });
  }

  async function pathOf(id) {
    const parts = [];
    let cur = id == null ? null : await get(id);
    while (cur) {
      parts.unshift(cur.name);
      cur = cur.parentId == null ? null : await get(cur.parentId);
    }
    return '/' + parts.join('/');
  }

  async function resolvePath(path) {
    // Supports absolute paths like /Documents/notes.txt
    const segs = (path || '').split('/').filter(Boolean);
    let parentId = null, node = null;
    for (const seg of segs) {
      if (seg === '.') continue;
      if (seg === '..') {
        node = parentId == null ? null : await get(parentId);
        parentId = node ? node.parentId : null;
        continue;
      }
      const kids = await list(parentId);
      node = kids.find((k) => k.name === seg) || null;
      if (!node) return null;
      parentId = node.id;
    }
    return node;
  }

  const Settings = {
    async get(key, fallback) {
      const r = await req(db.transaction('settings').objectStore('settings').get(key));
      return r ? r.value : fallback;
    },
    async set(key, value) {
      await req(db.transaction('settings', 'readwrite').objectStore('settings').put({ key, value }));
    },
    async all() {
      const all = await req(db.transaction('settings').objectStore('settings').getAll());
      const o = {};
      for (const r of all) o[r.key] = r.value;
      return o;
    },
  };

  return {
    init: async () => { await open(); await seedIfEmpty(); },
    list, get, mkdir, writeFile, updateFile, rename, remove, pathOf, resolvePath,
    Settings,
  };
})();
