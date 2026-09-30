/* VisionERP slice — shared client. XSS-safe: never uses innerHTML with user data. */
"use strict";
const API = "/api/v1";

function tok() { return localStorage.getItem("vq_token") || ""; }
function me() { try { return JSON.parse(localStorage.getItem("vq_user") || "null"); } catch { return null; } }

async function api(method, path, body) {
  const opts = { method, headers: { "Authorization": "Bearer " + tok() } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(API + path, opts);
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) {
    const msg = (data && data.message) || ("HTTP " + r.status);
    const e = new Error(msg); e.status = r.status; e.code = data && data.error; throw e;
  }
  return data;
}

/* ---- DOM builders (textContent only — user data is never injected as HTML) ---- */
function el(tag, text, attrs) {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = String(text);
  if (attrs) for (const k of Object.keys(attrs)) n.setAttribute(k, attrs[k]);
  return n;
}
function pill(status) { return el("span", status, { "class": "pill " + String(status).replace(/[^a-z_]/g, "") }); }
function money(n) { return "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
function fmtDate(s) { return s ? String(s).slice(0, 10) : "—"; }

function table(headers, rows, renderRow) {
  const t = el("table");
  const thead = el("thead"), tr = el("tr");
  headers.forEach(h => tr.appendChild(el("th", h)));
  thead.appendChild(tr); t.appendChild(thead);
  const tb = el("tbody");
  if (!rows.length) {
    const r = el("tr"), c = el("td", "No records.");
    c.colSpan = headers.length; c.className = "mut"; r.appendChild(c); tb.appendChild(r);
  } else rows.forEach(row => tb.appendChild(renderRow(row)));
  t.appendChild(tb);
  return t;
}
function tdRow(cells) {
  const tr = el("tr");
  cells.forEach(c => {
    const td = el("td");
    if (c instanceof Node) td.appendChild(c); else td.textContent = c === null || c === undefined ? "" : String(c);
    tr.appendChild(td);
  });
  return tr;
}
function btn(label, cls, fn) {
  const b = el("button", label, { "class": "act sm " + (cls || "") });
  b.addEventListener("click", async (ev) => {
    ev.preventDefault();
    try { await fn(); } catch (e) { showErr(e.message); }
  });
  return b;
}
function showErr(msg) {
  const d = document.getElementById("flash");
  if (d) { d.className = "err"; d.textContent = "Error: " + msg; d.scrollIntoView(); }
  else alert("Error: " + msg);
}
function showOk(msg) {
  const d = document.getElementById("flash");
  if (d) { d.className = "okmsg"; d.textContent = msg; }
}
function clearFlash() { const d = document.getElementById("flash"); if (d) d.textContent = ""; }

function guard(roles) {
  const u = me();
  if (!u || !tok()) { location.href = "/index.html"; return null; }
  if (roles && !roles.includes(u.role)) { location.href = "/index.html"; return null; }
  return u;
}
function topbar(user, title) {
  const h = document.querySelector("header.top");
  h.appendChild(el("h1", title));
  const w = el("span", user.full_name + " · " + user.role, { "class": "who" });
  h.appendChild(w);
  const b = el("button", "Logout");
  b.addEventListener("click", async () => {
    try { await api("POST", "/auth/logout"); } catch { /* ignore */ }
    localStorage.removeItem("vq_token"); localStorage.removeItem("vq_user");
    location.href = "/index.html";
  });
  h.appendChild(b);
}
function tabs(names, onSwitch) {
  // names: [{id, label}]
  const nav = document.querySelector("nav.tabs");
  names.forEach((n, i) => {
    const b = el("button", n.label);
    if (i === 0) b.classList.add("on");
    b.addEventListener("click", () => {
      nav.querySelectorAll("button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      document.querySelectorAll("section.panel").forEach(p => p.classList.remove("on"));
      const p = document.getElementById("tab-" + n.id);
      if (p) p.classList.add("on");
      clearFlash();
      if (onSwitch) onSwitch(n.id);
    });
    nav.appendChild(b);
  });
}
function mount(id, node) {
  const p = document.getElementById("tab-" + id);
  p.replaceChildren(); p.appendChild(node);
}
function field(labelText, inputEl) {
  const w = el("div"); w.appendChild(el("label", labelText)); w.appendChild(inputEl); return w;
}
function input(type, name, val, attrs) {
  const i = el("input", null, Object.assign({ type, name, value: val || "" }, attrs || {}));
  return i;
}
function select(name, options, val) {
  const s = el("select", null, { name });
  options.forEach(o => {
    const v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o;
    const op = el("option", t, { value: v });
    if (String(v) === String(val)) op.selected = true;
    s.appendChild(op);
  });
  return s;
}
async function formDialog(title, fields, onSubmit) {
  // fields: [{label, name, type, options, value, required}]
  const wrap = el("div");
  wrap.appendChild(el("h3", title));
  const f = el("form", null, { "class": "grid" });
  const refs = {};
  fields.forEach(fd => {
    let inp;
    if (fd.type === "select") inp = select(fd.name, fd.options, fd.value);
    else if (fd.type === "textarea") { inp = el("textarea", fd.value || "", { name: fd.name }); }
    else inp = input(fd.type || "text", fd.name, fd.value, fd.required ? { required: "1" } : null);
    refs[fd.name] = inp;
    f.appendChild(field(fd.label + (fd.required ? " *" : ""), inp));
  });
  const sb = el("button", "Submit", { "class": "act", type: "submit" });
  f.appendChild(el("div", null)).appendChild(sb);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    fields.forEach(fd => {
      let v = refs[fd.name].value;
      if (fd.num) { v = v === "" ? undefined : Number(v); }
      if (v !== undefined && v !== "") body[fd.name] = v;
    });
    try { await onSubmit(body); showOk("Saved."); } catch (e) { showErr(e.message); }
  });
  wrap.appendChild(f);
  return wrap;
}
