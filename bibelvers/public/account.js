"use strict";
/* Konto (Netlify Identity) und Synchronisierung mit /api/sync.
   Prinzip: Die App arbeitet immer mit dem lokalen Stand. Beim Sync werden alle Datensätze gehasht und mit dem
   zuletzt synchronisierten Stand verglichen; nur Geänderte gehen an den Server. Der Server liefert alles,
   was seit unserem cursor neu ist. Pro Datensatz gewinnt der jüngere Stand. */

const LAST_USER = "bvl:lastUser";
let USER = null;            // { id, email }
const SY = { running: false, again: false, timer: null, state: "", authFail: false };

// ---------- kleine Helfer ----------
const lsGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* egal */ } };
const lsDel = (k) => { try { localStorage.removeItem(k); } catch (e) { /* egal */ } };

function hash(str) { // cyrb53 – schneller 53-bit-Hash, reicht zum Erkennen von Änderungen
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) { const ch = str.charCodeAt(i); h1 = Math.imul(h1 ^ ch, 2654435761); h2 = Math.imul(h2 ^ ch, 1597334677); }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}
// JSON mit sortierten Schlüsseln: Postgres (JSONB) ändert die Reihenfolge, der Hash darf davon nicht abhängen.
function stable(v) {
  if (Array.isArray(v)) return "[" + v.map(stable).join(",") + "]";
  if (v && typeof v === "object") return "{" + Object.keys(v).sort().filter((k) => v[k] !== undefined).map((k) => JSON.stringify(k) + ":" + stable(v[k])).join(",") + "}";
  return JSON.stringify(v === undefined ? null : v);
}
const hashOf = (data) => hash(stable(data));
const authMsg = (e) => {
  const m = String((e && e.message) || e || "");
  if (/invalid_grant|invalid.*(credentials|password|login)|No user found/i.test(m)) return "E-Mail oder Passwort stimmt nicht.";
  if (/Email not confirmed/i.test(m)) return "Bitte bestätige zuerst deine E-Mail-Adresse.";
  if (/Failed to fetch|NetworkError|network/i.test(m)) return "Keine Verbindung. Bitte später nochmal versuchen.";
  if (/MissingIdentity|Identity is not configured/i.test(m)) return "Die Anmeldung ist auf dem Server noch nicht eingerichtet.";
  if (/expired|invalid.*token|not found/i.test(m)) return "Der Link ist abgelaufen oder ungültig. Fordere einen neuen an.";
  return m || "Unbekannter Fehler.";
};

// ---------- Start ----------
async function boot() {
  let cb = null;
  try { cb = await Auth.handleAuthCallback(); }
  catch (e) { return viewLogin(authMsg(e)); }
  if (cb && cb.type === "invite" && cb.token) return viewSetPassword("invite", cb.token);
  if (cb && cb.type === "recovery") return viewSetPassword("recovery");

  let user = null;
  try { user = await Auth.getUser(); } catch (e) { /* offline o. ä. */ }
  if (!user) {
    // Offline mit abgelaufener Sitzung: mit dem zuletzt angemeldeten Konto lokal weiterlernen.
    const last = lsGet(LAST_USER);
    if (last && !navigator.onLine) { try { return startApp(JSON.parse(last), true); } catch (e) { /* weiter */ } }
    return viewLogin();
  }
  startApp(user);
}

function startApp(user, offline) {
  USER = { id: user.id, email: user.email };
  lsSet(LAST_USER, JSON.stringify(USER));
  KEY = "bvl1:" + USER.id;
  S = load();
  adoptLegacyData();
  document.getElementById("tabs").hidden = false;
  show("heute");
  if (!offline) sync();
  else setSyncState("offline");
}

// Daten aus der Version ohne Konto (Schlüssel "bvl1") einmalig übernehmen.
function adoptLegacyData() {
  const raw = lsGet("bvl1");
  if (!raw) return;
  let old; try { old = JSON.parse(raw); } catch (e) { return lsDel("bvl1"); }
  if (!old || !Array.isArray(old.passages) || !old.passages.length) return lsDel("bvl1");
  if (!confirm(`Auf diesem Gerät liegen ${old.passages.length} Text(e) aus der Version ohne Anmeldung. In dein Konto übernehmen?`)) return;
  const have = new Set(S.passages.map((p) => p.id));
  for (const p of old.passages) if (!have.has(p.id)) S.passages.push(p);
  for (const [id, c] of Object.entries(old.cards || {})) if (!S.cards[id]) S.cards[id] = c;
  for (const [k, l] of Object.entries(old.log || {})) S.log[k] = S.log[k] || l;
  lsDel("bvl1");
  save();
  toast("Übernommen – wird synchronisiert");
}

// ---------- Anmelde-Ansichten ----------
function authShell(inner) {
  document.getElementById("tabs").hidden = true;
  setSyncState("");
  $view.innerHTML = `<div class="auth card">${inner}</div>`;
}

function viewLogin(err) {
  authShell(`<h2>Anmelden</h2>
    <form id="lf">
      <label>E-Mail</label><input id="em" type="email" autocomplete="username" required value="${esc((JSON.parse(lsGet(LAST_USER) || "{}").email) || "")}">
      <label>Passwort</label><input id="pw" type="password" autocomplete="current-password" required>
      <button class="primary big" style="margin-top:14px">Anmelden</button>
      <div class="err-msg" id="er">${err ? esc(err) : ""}</div>
    </form>
    <p class="small muted"><button class="link" id="fg">Passwort vergessen?</button></p>
    <p class="small muted">Zugang nur per Einladung. Du hast keine? Frag den, der die App betreut.</p>`);
  const er = document.getElementById("er");
  document.getElementById("lf").onsubmit = async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true; er.textContent = "";
    try {
      const u = await Auth.login(document.getElementById("em").value.trim(), document.getElementById("pw").value);
      startApp(u);
    } catch (x) { er.textContent = authMsg(x); btn.disabled = false; }
  };
  document.getElementById("fg").onclick = async () => {
    const em = document.getElementById("em").value.trim();
    if (!em) { er.textContent = "Bitte zuerst deine E-Mail eintragen."; return; }
    try { await Auth.requestPasswordRecovery(em); er.textContent = ""; toast("Falls es das Konto gibt, ist eine Mail unterwegs."); }
    catch (x) { er.textContent = authMsg(x); }
  };
}

function viewSetPassword(kind, token) {
  const invite = kind === "invite";
  authShell(`<h2>${invite ? "Willkommen!" : "Neues Passwort"}</h2>
    <p class="muted small">${invite ? "Leg ein Passwort für dein Konto fest." : "Leg ein neues Passwort fest."}</p>
    <form id="sf">
      <label>Passwort (mind. 8 Zeichen)</label><input id="p1" type="password" autocomplete="new-password" minlength="8" required>
      <label>Wiederholen</label><input id="p2" type="password" autocomplete="new-password" minlength="8" required>
      <button class="primary big" style="margin-top:14px">Speichern</button>
      <div class="err-msg" id="er"></div>
    </form>`);
  document.getElementById("sf").onsubmit = async (e) => {
    e.preventDefault();
    const p1 = document.getElementById("p1").value, p2 = document.getElementById("p2").value, er = document.getElementById("er");
    if (p1.length < 8) { er.textContent = "Mindestens 8 Zeichen."; return; }
    if (p1 !== p2) { er.textContent = "Die Passwörter stimmen nicht überein."; return; }
    const btn = e.target.querySelector("button"); btn.disabled = true;
    try {
      const u = invite ? await Auth.acceptInvite(token, p1) : await Auth.updateUser({ password: p1 });
      history.replaceState(null, "", location.pathname);
      toast("Passwort gespeichert");
      startApp(u || await Auth.getUser());
    } catch (x) { er.textContent = authMsg(x); btn.disabled = false; }
  };
}

// Konto-Bereich in den Einstellungen
function renderAccount(el) {
  if (!el || !USER) return;
  const pending = pendingChanges().length;
  el.innerHTML = `<div class="card"><h3 style="margin-top:0">Konto</h3>
    <p>Angemeldet als <b>${esc(USER.email || "")}</b></p>
    <p class="small muted">Synchronisierung: ${esc(SY.state || "–")}${pending ? ` · ${pending} Änderung(en) noch nicht hochgeladen` : ""}</p>
    <div class="row"><button id="syn">Jetzt synchronisieren</button><button id="cpw">Passwort ändern</button><button id="out">Abmelden</button></div>
    <div id="cpwbox"></div></div>`;
  el.querySelector("#syn").onclick = async () => { await sync(); renderAccount(el); };
  el.querySelector("#out").onclick = async () => {
    if (pendingChanges().length && !confirm("Es gibt Änderungen, die noch nicht hochgeladen sind. Trotzdem abmelden? (Sie bleiben auf diesem Gerät gespeichert.)")) return;
    try { await Auth.logout(); } catch (e) { /* Cookies werden trotzdem gelöscht */ }
    USER = null; lsDel(LAST_USER); S = freshState(); KEY = "bvl1";
    viewLogin();
  };
  el.querySelector("#cpw").onclick = () => {
    const box = el.querySelector("#cpwbox");
    box.innerHTML = `<label>Neues Passwort (mind. 8 Zeichen)</label><input id="np" type="password" autocomplete="new-password" minlength="8">
      <div class="row" style="margin-top:8px"><button class="primary" id="nps">Speichern</button></div><div class="err-msg" id="nper"></div>`;
    box.querySelector("#nps").onclick = async () => {
      const pw = box.querySelector("#np").value;
      if (pw.length < 8) { box.querySelector("#nper").textContent = "Mindestens 8 Zeichen."; return; }
      try { await Auth.updateUser({ password: pw }); box.innerHTML = ""; toast("Passwort geändert"); }
      catch (x) { box.querySelector("#nper").textContent = authMsg(x); }
    };
  };
}

// ---------- Synchronisierung ----------
function setSyncState(s, isErr) {
  SY.state = s;
  const el = document.getElementById("syncst");
  if (!el) return;
  const label = { ok: "☁ synchron", busy: "⟳ synchronisiere …", offline: "offline – speichert lokal", auth: "Bitte neu anmelden", error: "Sync-Fehler" }[s] || "";
  el.textContent = label; el.className = "syncst" + (isErr || s === "auth" || s === "error" ? " err" : "");
  if (s === "auth") { el.style.cursor = "pointer"; el.onclick = () => viewLogin("Deine Sitzung ist abgelaufen."); } else { el.onclick = null; el.style.cursor = ""; }
}

function localRecords() {
  const m = new Map();
  const put = (kind, id, data) => m.set(kind + ":" + id, { kind, id, data });
  put("settings", "settings", S.settings);
  for (const p of S.passages) put("passage", p.id, p);
  for (const [id, c] of Object.entries(S.cards)) put("card", id, c);
  for (const [id, l] of Object.entries(S.log)) put("log", id, l);
  return m;
}

function pendingChanges() {
  const recs = localRecords(), out = [];
  for (const [k, r] of recs) { const h = hashOf(r.data); if (S.sync.h[k] !== h) out.push({ k, h, kind: r.kind, id: r.id, data: r.data }); }
  for (const k of Object.keys(S.sync.h)) if (!recs.has(k)) {
    const i = k.indexOf(":"); out.push({ k, h: null, kind: k.slice(0, i), id: k.slice(i + 1), deleted: true });
  }
  return out;
}

function localGet(kind, id) {
  if (kind === "settings") return S.settings;
  if (kind === "passage") return S.passages.find((p) => p.id === id);
  if (kind === "card") return S.cards[id];
  if (kind === "log") return S.log[id];
}

function applyRemote(rows) {
  let changed = false;
  for (const r of rows) {
    const k = r.kind + ":" + r.id;
    const local = localGet(r.kind, r.id);
    // Lokal inzwischen geändert (noch nicht hochgeladen)? Dann gewinnt lokal und geht beim nächsten Sync hoch.
    if (local !== undefined && S.sync.h[k] !== undefined && hashOf(local) !== S.sync.h[k]) continue;
    if (r.deleted) {
      if (r.kind === "passage") S.passages = S.passages.filter((p) => p.id !== r.id);
      else if (r.kind === "card") delete S.cards[r.id];
      else if (r.kind === "log") delete S.log[r.id];
      delete S.sync.h[k];
      changed = true;
      continue;
    }
    let data = r.data;
    if (r.kind === "log" && local) data = { new: Math.max(local.new || 0, data.new || 0), rev: Math.max(local.rev || 0, data.rev || 0) };
    if (r.kind === "settings") S.settings = Object.assign(freshState().settings, data);
    else if (r.kind === "passage") {
      const i = S.passages.findIndex((p) => p.id === r.id);
      if (i >= 0) S.passages[i] = data; else S.passages.push(data);
      S.passages.sort((a, b) => (a.created || 0) - (b.created || 0));
    } else if (r.kind === "card") S.cards[r.id] = data;
    else if (r.kind === "log") S.log[r.id] = data;
    S.sync.h[k] = hashOf(r.data);
    changed = true;
  }
  return changed;
}

async function postSync(body, retried) {
  const res = await fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(body) });
  if (res.status === 401 && !retried) {
    // Token evtl. abgelaufen: getUser() erneuert die Sitzung, dann ein zweiter Versuch.
    const u = await Auth.getUser().catch(() => null);
    if (u) return postSync(body, true);
  }
  if (res.status === 401) { const e = new Error("auth"); e.auth = true; throw e; }
  if (!res.ok) { let m = res.status; try { m = (await res.json()).error || m; } catch (e) { /* egal */ } throw new Error("Server: " + m); }
  return res.json();
}

// Änderungen in Pakete aufteilen (Anzahl und Größe begrenzen).
function chunks(list) {
  const out = []; let cur = [], size = 0;
  for (const c of list) {
    const s = c.data ? JSON.stringify(c.data).length : 50;
    if (cur.length && (cur.length >= 500 || size + s > 3_000_000)) { out.push(cur); cur = []; size = 0; }
    cur.push(c); size += s;
  }
  if (cur.length) out.push(cur);
  return out;
}

// Gibt immer ein Promise zurück, das erst nach Abschluss aller angestoßenen Durchläufe erfüllt ist.
function sync() {
  if (!USER) return Promise.resolve();
  if (SY.running) { SY.again = true; return SY.promise; }
  SY.running = true;
  SY.promise = (async () => {
    try { do { SY.again = false; await syncOnce(); } while (SY.again && USER); }
    finally { SY.running = false; }
  })();
  return SY.promise;
}

async function syncOnce() {
  clearTimeout(SY.timer);
  setSyncState("busy");
  let remoteChanged = false;
  try {
    // Neues Gerät: erst den Serverstand holen, damit lokale Standardwerte nichts überschreiben.
    if (!S.sync.cursor) {
      let more = true;
      while (more) {
        const res = await postSync({ cursor: S.sync.cursor, changes: [] });
        if (applyRemote(res.changes)) remoteChanged = true;
        S.sync.cursor = res.cursor; more = res.more && res.changes.length > 0;
      }
    }
    const now = Date.now();
    const pending = pendingChanges();
    const batches = chunks(pending);
    let cursor = S.sync.cursor, more = false, b = 0;
    do {
      const batch = batches[b++] || [];
      const res = await postSync({ cursor, changes: batch.map((c) => c.deleted
        ? { kind: c.kind, id: c.id, deleted: true, updated_at: now }
        : { kind: c.kind, id: c.id, data: c.data, updated_at: now }) });
      for (const c of batch) { if (c.h === null) delete S.sync.h[c.k]; else S.sync.h[c.k] = c.h; }
      if (applyRemote(res.changes)) remoteChanged = true;
      cursor = res.cursor; more = res.more;
      S.sync.cursor = cursor;
      try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* save() meldet das schon */ }
    } while (b < batches.length || more);
    SY.authFail = false;
    setSyncState("ok");
  } catch (e) {
    if (e.auth) { SY.authFail = true; setSyncState("auth"); }
    else if (!navigator.onLine || /Failed to fetch|NetworkError|Load failed/i.test(e.message)) setSyncState("offline");
    else { console.error(e); setSyncState("error", true); }
  }
  if (remoteChanged && !sess && USER && ["heute", "bibliothek", "detail"].includes(tab)) refreshView();
}

function refreshView() {
  // Nur ruhende Übersichten neu zeichnen – nie mitten in einer Übung oder einem Formular.
  if (tab === "heute" && !document.getElementById("ex")) show("heute");
  else if (tab === "bibliothek") show("bibliothek");
  else if (tab === "detail" && document.getElementById("learn")) show(passage(viewArg) ? "detail" : "bibliothek", viewArg);
}

// Nach jedem Speichern gebündelt synchronisieren.
function scheduleSync() {
  if (!USER || SY.authFail) return;
  clearTimeout(SY.timer);
  SY.timer = setTimeout(sync, 4000);
}

document.addEventListener("visibilitychange", () => { if (USER && !SY.authFail) sync(); });
window.addEventListener("online", () => { if (USER) sync(); });

boot();
