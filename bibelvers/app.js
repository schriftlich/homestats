"use strict";
/* Bibelverse lernen – alles läuft im Browser, Daten liegen in localStorage. */

const KEY = "bvl1";
const API = "https://api.getbible.net/v2";
const $view = document.getElementById("view");

// ---------- Speicher ----------
function freshState() {
  return { passages: [], cards: {}, log: {},
    settings: { newPerDay: 5, blockSize: 4, translation: "luther1912", mode: "tippen" } };
}
function load() {
  try {
    const s = JSON.parse(localStorage.getItem(KEY));
    if (s && Array.isArray(s.passages)) { s.settings = Object.assign(freshState().settings, s.settings); s.log = s.log || {}; return s; }
  } catch (e) { /* leer */ }
  return freshState();
}
let S = load();
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(S)); }
  catch (e) { toast("Speichern fehlgeschlagen – Speicher voll?"); }
}

// ---------- Helfer ----------
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const dayKey = (t = Date.now()) => { const d = new Date(SRS.startOfDay(t)); return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate(); };
function todayLog() { const k = dayKey(); return (S.log[k] = S.log[k] || { new: 0, rev: 0 }); }
function toast(msg) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
}
function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Vorlesen wird nicht unterstützt");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "de-DE"; u.rate = 0.9; speechSynthesis.speak(u);
}
const norm = (ch) => ch.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace("ß", "s");
const firstLetter = (w) => { const m = w.match(/[\p{L}\p{N}]/u); return m ? m[0] : null; };
function lettersOnly(text) {
  return text.split(/\s+/).filter(Boolean).map((w) => {
    const i = w.search(/[\p{L}\p{N}]/u);
    if (i < 0) return w;
    const tail = w.slice(i + 1).replace(/[\p{L}\p{N}]/gu, "");
    return w.slice(0, i) + w[i] + tail;
  }).join(" ");
}

// ---------- Stellenangaben ----------
const passage = (id) => S.passages.find((p) => p.id === id);
function vRef(p, v) {
  if (p.book) return BOOKS[p.book - 1][0] + " " + v.c + "," + v.v;
  return p.verses.length > 1 ? p.title + " · V. " + v.v : p.title;
}
function rangeRef(p, i, j) {
  const a = p.verses[i], b = p.verses[j];
  if (i === j) return vRef(p, a);
  if (!p.book) return p.title + " · V. " + a.v + "–" + b.v;
  const bn = BOOKS[p.book - 1][0];
  if (a.c === b.c) return bn + " " + a.c + "," + a.v + "–" + b.v;
  return bn + " " + a.c + "," + a.v + "–" + b.c + "," + b.v;
}

// ---------- Karten ----------
// kind: v = einzelner Vers, b = Abschnitt (mehrere Verse), c = ganzes Kapitel / ganzer Text
function buildCards(p) {
  const bs = Math.max(2, S.settings.blockSize | 0);
  const add = (kind, from, to) => {
    const id = p.id + ":" + kind + ":" + from + "-" + to;
    if (!S.cards[id]) S.cards[id] = { id, pid: p.id, kind, from, to, state: "new", due: 0 };
  };
  p.verses.forEach((_, i) => add("v", i, i));
  // nach Kapitel gruppieren
  const groups = [];
  p.verses.forEach((v, i) => {
    const g = groups[groups.length - 1];
    if (g && g.c === v.c) g.to = i; else groups.push({ c: v.c, from: i, to: i });
  });
  for (const g of groups) {
    const n = g.to - g.from + 1;
    if (n <= 1) continue;
    if (n > bs) for (let s = g.from; s <= g.to; s += bs) { const e = Math.min(g.to, s + bs - 1); if (e > s) add("b", s, e); }
    add("c", g.from, g.to);
  }
}
const cardsOf = (pid) => Object.values(S.cards).filter((c) => c.pid === pid);
const verseCard = (pid, i) => S.cards[pid + ":v:" + i + "-" + i];
function unlocked(c) {
  if (c.kind === "v") return true;
  for (let i = c.from; i <= c.to; i++) { const vc = verseCard(c.pid, i); if (!vc || vc.state !== "review") return false; }
  if (c.kind === "c") {
    return cardsOf(c.pid).every((b) => b.kind !== "b" || b.to < c.from || b.from > c.to || b.state !== "new");
  }
  return true;
}
const kindLabel = { v: "Vers", b: "Abschnitt", c: "Ganzes Kapitel" };

// ---------- Warteschlange ----------
function queueInfo(opts = {}) {
  const now = Date.now();
  const active = new Set(S.passages.filter((p) => !p.paused && (!opts.pid || p.id === opts.pid)).map((p) => p.id));
  const all = Object.values(S.cards).filter((c) => active.has(c.pid));
  const due = all.filter((c) => c.state !== "new" && c.due <= now).sort((a, b) => a.due - b.due);
  const cons = all.filter((c) => c.state === "new" && c.kind !== "v" && unlocked(c)).sort((a, b) => a.from - b.from);
  let left = opts.extraNew != null ? opts.extraNew : Math.max(0, S.settings.newPerDay - todayLog().new);
  const news = [];
  for (const p of S.passages) {
    if (!active.has(p.id)) continue;
    for (let i = 0; i < p.verses.length && left > 0; i++) {
      const c = verseCard(p.id, i);
      if (c && c.state === "new") { news.push(c); left--; }
    }
  }
  return { due, cons, news };
}

// ---------- Ansichten ----------
let tab = "heute";
document.getElementById("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]"); if (!b) return;
  show(b.dataset.tab);
});
function show(t, arg) {
  tab = t;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  window.scrollTo(0, 0);
  ({ heute: viewHome, bibliothek: viewLibrary, neu: viewAdd, einstellungen: viewSettings, detail: viewDetail })[t](arg);
}

function streak() {
  let n = 0; const t = Date.now();
  for (let d = 0; d < 3650; d++) {
    const k = dayKey(t - d * SRS.DAY);
    if (S.log[k] && S.log[k].rev > 0) n++;
    else if (d > 0) break;
  }
  return n;
}

function viewHome() {
  const q = queueInfo();
  const total = q.due.length + q.cons.length + q.news.length;
  const verses = Object.values(S.cards).filter((c) => c.kind === "v");
  const learned = verses.filter((c) => c.state === "review").length;
  let html = `<h2>Heute</h2>`;
  if (!S.passages.length) {
    html += `<div class="card"><p>Willkommen! Füge deinen ersten Vers, ein Kapitel oder ein ganzes Buch hinzu.</p>
      <button class="primary big" id="goAdd">Text hinzufügen</button>
      <p class="small muted">Oder starte direkt mit <button class="link" id="quick">Johannes 3,16</button>.</p></div>`;
  } else {
    html += `<div class="stats">
      <div class="stat"><b>${q.due.length + q.cons.length}</b><span>fällig</span></div>
      <div class="stat"><b>${q.news.length}</b><span>neu heute</span></div>
      <div class="stat"><b>${streak()}</b><span>Tage am Stück</span></div></div>`;
    if (total) html += `<div class="card"><button class="primary big" id="start">Lernen starten (${total})</button></div>`;
    else {
      const next = Object.values(S.cards).filter((c) => c.state !== "new").sort((a, b) => a.due - b.due)[0];
      html += `<div class="card"><p><b>Alles erledigt für heute.</b></p>
        <p class="muted small">${next ? "Nächste Wiederholung in " + SRS.fmtIvl(next.due - Date.now()) + "." : ""}
        Neue Verse: ${todayLog().new}/${S.settings.newPerDay} heute.</p>
        <button id="more">3 weitere neue Verse lernen</button></div>`;
    }
    html += `<div class="card"><div class="row"><div class="grow"><b>${learned}</b> von ${verses.length} Versen im Langzeitgedächtnis</div></div>
      <div class="bar"><i style="width:${verses.length ? (100 * learned / verses.length).toFixed(1) : 0}%"></i></div>
      <p class="small muted" style="margin:10px 0 0">${forecast()}</p></div>`;
  }
  $view.innerHTML = html;
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("goAdd", () => show("neu"));
  on("quick", () => quickAdd(43, 3, 16, 16));
  on("start", () => startSession({}));
  on("more", () => startSession({ extraNew: 3 }));
}

function forecast() {
  const now = Date.now(), base = SRS.startOfDay(now); const days = [0, 0, 0, 0, 0, 0, 0];
  for (const c of Object.values(S.cards)) {
    if (c.state === "new") continue;
    const d = Math.max(0, Math.floor((c.due - base) / SRS.DAY));
    if (d < 7) days[d]++;
  }
  const names = ["Heute", "Morgen"];
  return "Vorschau: " + days.map((n, i) => {
    const lbl = names[i] || new Date(base + i * SRS.DAY).toLocaleDateString("de-DE", { weekday: "short" });
    return lbl + " " + n;
  }).join(" · ");
}

function progress(p) {
  const vs = cardsOf(p.id).filter((c) => c.kind === "v");
  return { total: vs.length, learned: vs.filter((c) => c.state === "review").length, started: vs.filter((c) => c.state !== "new").length,
    due: cardsOf(p.id).filter((c) => c.state !== "new" && c.due <= Date.now()).length };
}

function viewLibrary() {
  let html = `<h2>Bibliothek</h2>`;
  if (!S.passages.length) html += `<div class="card muted">Noch nichts hier. Über „Hinzufügen“ Verse, Kapitel oder Bücher laden.</div>`;
  else {
    html += `<div class="card plist">`;
    for (const p of S.passages) {
      const g = progress(p);
      html += `<div class="item" data-id="${p.id}"><div class="grow">
        <div><b>${esc(p.title)}</b> <span class="badge">${esc(p.kindLabel)}</span>
        ${p.paused ? '<span class="badge paused">pausiert</span>' : ""}</div>
        <div class="small muted">${g.learned}/${g.total} gelernt · ${g.started - g.learned} in Arbeit · ${g.due} fällig · ${esc(p.translationName || "")}</div>
        <div class="bar"><i style="width:${(100 * g.learned / g.total).toFixed(1)}%"></i></div></div><div class="muted">›</div></div>`;
    }
    html += `</div>`;
  }
  $view.innerHTML = html;
  $view.querySelectorAll(".item").forEach((el) => (el.onclick = () => show("detail", el.dataset.id)));
}

function dotClass(c) {
  if (!c || c.state === "new") return "";
  if (c.state === "learning") return "learning";
  return c.interval >= 21 ? "strong" : "review";
}

function viewDetail(pid) {
  const p = passage(pid); if (!p) return show("bibliothek");
  const g = progress(p);
  const learnedIdx = p.verses.map((_, i) => i).filter((i) => verseCard(pid, i).state !== "new");
  const defTo = learnedIdx.length ? learnedIdx[learnedIdx.length - 1] : 0;
  const opts = (sel) => p.verses.map((v, i) => `<option value="${i}" ${i === sel ? "selected" : ""}>${esc(vRef(p, v))}</option>`).join("");
  let html = `<button class="link" id="back">‹ Bibliothek</button>
    <h2>${esc(p.title)}</h2>
    <p class="muted small">${esc(p.kindLabel)} · ${esc(p.translationName || "")} · ${g.learned}/${g.total} Verse gelernt</p>
    <div class="card"><div class="row">
      <button class="primary" id="learn">Jetzt lernen</button>
      <button id="more">+3 neue Verse</button>
    </div></div>`;
  if (p.verses.length > 1) {
    html += `<div class="card"><h3 style="margin-top:0">Am Stück aufsagen</h3>
      <p class="small muted">Freies Üben ohne Einfluss auf die Wiederholungsplanung – ideal, um Kapitel als Ganzes zu festigen.</p>
      <div class="grid2"><div><label>Von</label><select id="pf">${opts(0)}</select></div>
      <div><label>Bis</label><select id="pt">${opts(defTo)}</select></div></div>
      <div class="row" style="margin-top:10px"><button id="practice">Üben</button></div></div>`;
  }
  html += `<div class="card"><div class="legend"><span><i class="dot"></i>neu</span><span><i class="dot learning"></i>in Arbeit</span>
    <span><i class="dot review"></i>gelernt</span><span><i class="dot strong"></i>sitzt (≥ 3 Wochen)</span></div><ul class="vlist">`;
  let lastC = null;
  p.verses.forEach((v, i) => {
    if (p.book && p.kind === "buch" && v.c !== lastC) { html += `<li><b>Kapitel ${v.c}</b></li>`; lastC = v.c; }
    const c = verseCard(pid, i);
    const info = c.state === "new" ? "" : c.state === "learning" ? "lernt" : SRS.fmtIvl(c.interval * SRS.DAY);
    html += `<li><i class="dot ${dotClass(c)}"></i><div class="grow"><sup>${v.v}</sup> ${esc(v.t)}</div><span class="small muted">${info}</span></li>`;
  });
  html += `</ul></div>
    <div class="card row">
      <button id="pause">${p.paused ? "Fortsetzen" : "Pausieren"}</button>
      <button id="reset">Fortschritt zurücksetzen</button>
      <button id="del" style="color:var(--bad)">Löschen</button></div>`;
  $view.innerHTML = html;
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("back", () => show("bibliothek"));
  on("learn", () => startSession({ pid }));
  on("more", () => startSession({ pid, extraNew: 3 }));
  on("practice", () => {
    let a = +document.getElementById("pf").value, b = +document.getElementById("pt").value;
    if (b < a) [a, b] = [b, a];
    startPractice(p, a, b);
  });
  on("pause", () => { p.paused = !p.paused; save(); viewDetail(pid); });
  on("reset", () => {
    if (!confirm("Fortschritt für diesen Text zurücksetzen?")) return;
    cardsOf(pid).forEach((c) => (S.cards[c.id] = { id: c.id, pid, kind: c.kind, from: c.from, to: c.to, state: "new", due: 0 }));
    save(); viewDetail(pid);
  });
  on("del", () => {
    if (!confirm(`„${p.title}“ mit allem Fortschritt löschen?`)) return;
    cardsOf(pid).forEach((c) => delete S.cards[c.id]);
    S.passages = S.passages.filter((x) => x.id !== pid);
    save(); show("bibliothek");
  });
}

// ---------- Hinzufügen ----------
let translations = TRANSLATIONS.slice();
let addKind = "vers";
let preview = null;

async function loadTranslations() {
  try {
    const r = await fetch(API + "/translations.json");
    const d = await r.json();
    const list = Object.values(d).filter((t) => /german|deutsch/i.test(t.language || "") || t.lang === "de")
      .map((t) => ({ id: t.abbreviation, name: t.translation || t.abbreviation }));
    if (list.length) translations = list;
  } catch (e) { /* Fallback-Liste bleibt */ }
}

function viewAdd() {
  preview = null;
  const k = addKind;
  const tOpts = translations.map((t) => `<option value="${esc(t.id)}" ${t.id === S.settings.translation ? "selected" : ""}>${esc(t.name)}</option>`).join("");
  const bOpts = BOOKS.map((b, i) => `<option value="${i + 1}" ${i === 42 ? "selected" : ""}>${esc(b[0])}</option>`).join("");
  let html = `<h2>Hinzufügen</h2>
    <div class="seg" id="kind">
      <button data-k="vers" class="${k === "vers" ? "on" : ""}">Vers(e)</button>
      <button data-k="kapitel" class="${k === "kapitel" ? "on" : ""}">Kapitel</button>
      <button data-k="buch" class="${k === "buch" ? "on" : ""}">Buch</button>
      <button data-k="eigen" class="${k === "eigen" ? "on" : ""}">Eigener Text</button></div>
    <div class="card">`;
  if (k === "eigen") {
    html += `<label>Stellenangabe / Titel</label><input id="title" placeholder="z. B. Römer 8,28 (Neue Genfer)">
      <label>Text</label><textarea id="raw" placeholder="Text einfügen. Versnummern am Anfang (z. B. „28 Wir wissen …“) werden erkannt – dann wird jeder Vers einzeln gelernt."></textarea>
      <p class="small muted">So kannst du jede Übersetzung nutzen (Lutherbibel 2017, Elberfelder 2006 …), wenn du den Text z. B. aus deiner Bibel-App kopierst.</p>
      <button class="primary" id="load">Vorschau</button>`;
  } else {
    html += `<label>Übersetzung</label><select id="tr">${tOpts}</select>
      <label>Buch</label><select id="book">${bOpts}</select>`;
    if (k !== "buch") html += `<label>Kapitel</label><input id="ch" type="number" min="1" value="3" inputmode="numeric">`;
    if (k === "vers") html += `<div class="grid2"><div><label>Vers von</label><input id="vf" type="number" min="1" value="16" inputmode="numeric"></div>
      <div><label>bis (optional)</label><input id="vt" type="number" min="1" inputmode="numeric"></div></div>`;
    if (k === "buch") html += `<p class="small muted">Ein ganzes Buch wird Vers für Vers in Kapitel-Reihenfolge eingeführt. Kleiner anfangen (z. B. Philipper, Jakobus) lohnt sich.</p>`;
    html += `<div class="row" style="margin-top:12px"><button class="primary" id="load">Text laden</button></div>
      <p class="small muted">Gemeinfreie Übersetzungen über getbible.net. Für neuere Übersetzungen „Eigener Text“ nutzen.</p>`;
  }
  html += `</div><div id="prev"></div>`;
  $view.innerHTML = html;
  document.getElementById("kind").onclick = (e) => { const b = e.target.closest("button"); if (b) { addKind = b.dataset.k; viewAdd(); } };
  document.getElementById("load").onclick = () => doLoad().catch((err) => {
    document.getElementById("prev").innerHTML = `<div class="card" style="color:var(--bad)">${esc(err.message)}</div>`;
  });
}

function cleanText(t) { return String(t || "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim(); }
function parseVerses(arr) {
  const list = Array.isArray(arr) ? arr : Object.values(arr || {});
  return list.map((v) => ({ c: +v.chapter, v: +v.verse, t: cleanText(v.text) })).filter((v) => v.t);
}
async function fetchJSON(url) {
  let r;
  try { r = await fetch(url); } catch (e) { throw new Error("Keine Verbindung zu getbible.net. Prüfe dein Internet oder nutze „Eigener Text“."); }
  if (!r.ok) throw new Error("Text nicht gefunden (" + r.status + "). Gibt es dieses Kapitel in der Übersetzung?");
  return r.json();
}
async function fetchChapter(tr, book, ch) {
  const d = await fetchJSON(`${API}/${tr}/${book}/${ch}.json`);
  return parseVerses(d.verses).map((v) => Object.assign(v, { c: v.c || ch }));
}

async function doLoad() {
  const prev = document.getElementById("prev");
  prev.innerHTML = `<div class="card muted">Lade …</div>`;
  const k = addKind;
  if (k === "eigen") {
    const title = document.getElementById("title").value.trim() || "Eigener Text";
    const verses = parseOwn(document.getElementById("raw").value);
    if (!verses.length) throw new Error("Bitte Text einfügen.");
    preview = { title, kind: "eigen", kindLabel: verses.length > 1 ? "Abschnitt" : "Vers", book: null, translationName: "eigener Text", verses };
  } else {
    const tr = document.getElementById("tr").value;
    const trName = (translations.find((t) => t.id === tr) || { name: tr }).name;
    S.settings.translation = tr; save();
    const book = +document.getElementById("book").value;
    const bn = BOOKS[book - 1][0];
    if (k === "buch") {
      let verses = [];
      try {
        const d = await fetchJSON(`${API}/${tr}/${book}.json`);
        const chs = Array.isArray(d.chapters) ? d.chapters : Object.values(d.chapters || {});
        for (const c of chs) verses.push(...parseVerses(c.verses).map((v) => Object.assign(v, { c: v.c || +c.chapter })));
      } catch (e) { verses = []; }
      if (!verses.length) {
        for (let c = 1; c <= BOOKS[book - 1][2]; c++) {
          prev.innerHTML = `<div class="card muted">Lade Kapitel ${c} von ${BOOKS[book - 1][2]} …</div>`;
          verses.push(...await fetchChapter(tr, book, c));
        }
      }
      if (!verses.length) throw new Error("Buch konnte nicht geladen werden.");
      preview = { title: bn, kind: "buch", kindLabel: "Buch", book, translation: tr, translationName: trName, verses };
    } else {
      const ch = +document.getElementById("ch").value;
      if (!(ch >= 1 && ch <= BOOKS[book - 1][2])) throw new Error(`${bn} hat ${BOOKS[book - 1][2]} Kapitel.`);
      let verses = await fetchChapter(tr, book, ch);
      if (!verses.length) throw new Error("Kapitel ist leer.");
      if (k === "kapitel") {
        preview = { title: `${bn} ${ch}`, kind: "kapitel", kindLabel: "Kapitel", book, translation: tr, translationName: trName, verses };
      } else {
        const vf = +document.getElementById("vf").value || 1;
        const vt = +document.getElementById("vt").value || vf;
        const lo = Math.min(vf, vt), hi = Math.max(vf, vt);
        verses = verses.filter((v) => v.v >= lo && v.v <= hi);
        if (!verses.length) throw new Error("Diese Verse gibt es in dem Kapitel nicht.");
        preview = { title: `${bn} ${ch},${lo}${hi > lo ? "–" + hi : ""}`, kind: "vers", kindLabel: hi > lo ? "Verse" : "Vers", book, translation: tr, translationName: trName, verses };
      }
    }
  }
  renderPreview();
}

function parseOwn(raw) {
  raw = raw.replace(/\r/g, "").trim();
  if (!raw) return [];
  // Versnummern erkennen: Zahl am Zeilenanfang oder mitten im Text vor einem Großbuchstaben/Wort
  const parts = raw.split(/(?:^|\s)(\d{1,3})\s+(?=[^\d\s])/);
  if (parts.length >= 3) {
    const out = [];
    if (cleanText(parts[0])) out.push({ c: null, v: 0, t: cleanText(parts[0]) });
    for (let i = 1; i < parts.length; i += 2) { const t = cleanText(parts[i + 1]); if (t) out.push({ c: null, v: +parts[i], t }); }
    if (out.length && out[0].v === 0) { if (out.length > 1) { out[1].t = out[0].t + " " + out[1].t; out.shift(); } else out[0].v = 1; }
    return out;
  }
  return [{ c: null, v: 1, t: cleanText(raw) }];
}

function renderPreview() {
  const p = preview;
  const words = p.verses.reduce((n, v) => n + v.t.split(" ").length, 0);
  const days = Math.ceil(p.verses.length / Math.max(1, S.settings.newPerDay));
  const shown = p.verses.slice(0, 40);
  document.getElementById("prev").innerHTML = `<div class="card">
    <b>${esc(p.title)}</b> <span class="small muted">· ${esc(p.translationName)}</span>
    <p class="small muted">${p.verses.length} Vers${p.verses.length > 1 ? "e" : ""}, ${words} Wörter${p.verses.length > 1 ? ` · bei ${S.settings.newPerDay} neuen Versen pro Tag ca. ${days} Tage zum Einführen` : ""}</p>
    <div class="text" style="font-size:1.05rem">${shown.map((v) => `<sup>${p.book && p.kind === "buch" ? v.c + "," : ""}${v.v || ""}</sup>${esc(v.t)} `).join("")}${p.verses.length > 40 ? " …" : ""}</div>
    <button class="primary big" id="addit" style="margin-top:12px">Zur Bibliothek hinzufügen</button></div>`;
  document.getElementById("addit").onclick = () => {
    const p2 = Object.assign({ id: uid(), created: Date.now(), paused: false }, preview);
    S.passages.push(p2); buildCards(p2); save();
    toast("Hinzugefügt: " + p2.title);
    show("detail", p2.id);
  };
}

async function quickAdd(book, ch, vf, vt) {
  try {
    const tr = S.settings.translation;
    const verses = (await fetchChapter(tr, book, ch)).filter((v) => v.v >= vf && v.v <= vt);
    if (!verses.length) throw new Error("Vers nicht gefunden.");
    const p = { id: uid(), created: Date.now(), paused: false, title: `${BOOKS[book - 1][0]} ${ch},${vf}`, kind: "vers", kindLabel: "Vers",
      book, translation: tr, translationName: (translations.find((t) => t.id === tr) || { name: tr }).name, verses };
    S.passages.push(p); buildCards(p); save(); show("heute");
  } catch (e) { toast(e.message); }
}

// ---------- Einstellungen ----------
function viewSettings() {
  const st = S.settings;
  $view.innerHTML = `<h2>Einstellungen</h2>
    <div class="card">
      <label>Neue Verse pro Tag</label><input id="npd" type="number" min="0" max="50" value="${st.newPerDay}" inputmode="numeric">
      <p class="small muted">Für ganze Kapitel sind 2–5 neue Verse pro Tag realistisch. Wiederholungen haben immer Vorrang.</p>
      <label>Abschnittsgröße (Verse pro Abschnitt)</label><input id="bs" type="number" min="2" max="12" value="${st.blockSize}" inputmode="numeric">
      <p class="small muted">Kapitel werden in Abschnitte zerlegt, die – sobald die Einzelverse sitzen – zusammen abgefragt werden. Danach kommt das ganze Kapitel. Gilt für neu hinzugefügte Texte.</p>
      <label>Abfrage-Modus</label>
      <div class="seg" id="mode"><button data-m="tippen" class="${st.mode === "tippen" ? "on" : ""}">Anfangsbuchstaben tippen</button>
        <button data-m="aufdecken" class="${st.mode === "aufdecken" ? "on" : ""}">Aufsagen &amp; aufdecken</button></div>
      <p class="small muted">Tippen: Du tippst zu jedem Wort den ersten Buchstaben – die App prüft und schlägt die Bewertung vor. Aufdecken: Du sagst den Text laut auf und bewertest dich selbst.</p>
    </div>
    <div class="card"><h3 style="margin-top:0">Daten</h3>
      <p class="small muted">Alles wird nur in diesem Browser gespeichert. Mach ab und zu ein Backup – damit kannst du auch auf ein anderes Gerät umziehen.</p>
      <div class="row"><button id="exp">Backup herunterladen</button>
      <label class="btn" style="margin:0;color:var(--ink);font-size:1rem">Backup einspielen<input id="imp" type="file" accept="application/json,.json" hidden></label></div>
      <div class="row" style="margin-top:10px"><button id="wipe" style="color:var(--bad)">Alle Daten löschen</button></div>
    </div>
    <p class="small muted">Gemeinfreie Bibeltexte: getbible.net. So funktioniert die Wiederholung: Verse kommen nach dem Lernen nach 1 min, 10 min, 1 Tag wieder; danach wachsen die Abstände je nach Bewertung (SM-2). Vergessene Verse kommen sofort zurück in die Lernphase.</p>`;
  const num = (id, key, min, max) => (document.getElementById(id).onchange = (e) => {
    st[key] = Math.min(max, Math.max(min, parseInt(e.target.value, 10) || min)); save(); toast("Gespeichert");
  });
  num("npd", "newPerDay", 0, 50); num("bs", "blockSize", 2, 12);
  document.getElementById("mode").onclick = (e) => { const b = e.target.closest("button"); if (b) { st.mode = b.dataset.m; save(); viewSettings(); } };
  document.getElementById("exp").onclick = () => {
    const blob = new Blob([JSON.stringify(S)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = "bibelverse-backup-" + new Date().toISOString().slice(0, 10) + ".json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  document.getElementById("imp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const d = JSON.parse(await f.text());
      if (!d || !Array.isArray(d.passages) || typeof d.cards !== "object") throw new Error();
      if (!confirm("Backup einspielen? Die aktuellen Daten werden ersetzt.")) return;
      localStorage.setItem(KEY, JSON.stringify(d)); S = load(); toast("Backup eingespielt"); show("heute");
    } catch (err) { toast("Ungültige Backup-Datei"); }
  };
  document.getElementById("wipe").onclick = () => {
    if (!confirm("Wirklich alle Texte und den gesamten Lernfortschritt löschen?")) return;
    S = freshState(); save(); show("heute");
  };
}

// ---------- Lern-Sitzung ----------
let sess = null;

function startSession(opts) {
  const q = queueInfo(opts);
  const ids = [...q.due, ...q.cons, ...q.news].map((c) => c.id);
  if (!ids.length) { toast("Gerade nichts zu lernen"); return; }
  sess = { opts, queue: ids, learning: [], done: 0, mode: S.settings.mode };
  nextCard();
}

function nextCard() {
  const now = Date.now();
  sess.learning.sort((a, b) => S.cards[a].due - S.cards[b].due);
  let id = null;
  if (sess.learning.length && S.cards[sess.learning[0]].due <= now) id = sess.learning.shift();
  else if (sess.queue.length) id = sess.queue.shift();
  else if (sess.learning.length) id = sess.learning.shift();   // vorziehen statt warten
  if (!id) return finishSession();
  const card = S.cards[id];
  if (!card) return nextCard();
  sess.card = id;
  const isNewVerse = card.kind === "v" && card.state === "new";
  sess.stage = isNewVerse ? "read" : "test";
  renderCard();
}

function finishSession() {
  const n = sess.done; sess = null;
  $view.innerHTML = `<h2>Geschafft!</h2><div class="card"><p>${n} Karte${n === 1 ? "" : "n"} wiederholt.</p>
    <p class="muted small">${forecast()}</p><button class="primary big" id="home">Zur Übersicht</button></div>`;
  document.getElementById("home").onclick = () => show("heute");
}

function cardContext(card) {
  const p = passage(card.pid);
  const verses = p.verses.slice(card.from, card.to + 1);
  const prevV = card.from > 0 && p.verses[card.from - 1].c === p.verses[card.from].c ? p.verses[card.from - 1] : null;
  return { p, verses, prevV, ref: rangeRef(p, card.from, card.to) };
}

function sessHeader(card, ctx) {
  const left = sess.queue.length + sess.learning.length;
  return `<div class="sess-head"><div><div class="ref">${esc(ctx.ref)}</div>
    <div class="small muted">${kindLabel[card.kind]}${card.state === "new" ? " · neu" : card.state === "learning" ? " · in Arbeit" : ""} · ${esc(ctx.p.translationName || "")}</div></div>
    <div class="progress">${sess.done} ✓ · ${left} offen<br><button class="link" id="quit">Beenden</button></div></div>`;
}
const cueHtml = (ctx) => ctx.prevV ? `<div class="cue">… ${esc(ctx.prevV.t.split(" ").slice(-14).join(" "))}</div>`
  : (ctx.p.verses.length > 1 && ctx.verses[0] === ctx.p.verses.find((v) => v.c === ctx.verses[0].c) ? `<div class="cue">Anfang${ctx.p.book ? " von Kapitel " + ctx.verses[0].c : ""}</div>` : "");
const versesHtml = (verses, many) => verses.map((v) => `${many ? `<sup>${v.v}</sup>` : ""}${esc(v.t)}`).join(" ");

function renderCard() {
  const card = S.cards[sess.card];
  const ctx = cardContext(card);
  const many = ctx.verses.length > 1;
  const full = ctx.verses.map((v) => v.t).join(" ");
  const st = sess.stage;
  let html = `<div class="card">${sessHeader(card, ctx)}`;
  if (st === "read" || st === "letters") {
    html += `<div class="steps"><i class="on"></i><i class="${st === "letters" ? "on" : ""}"></i><i></i></div>${cueHtml(ctx)}`;
    if (st === "read") {
      html += `<p class="small muted">Schritt 1: Lies den Vers 2–3× laut und präg dir die Stelle mit ein.</p>
        <div class="text">${versesHtml(ctx.verses, many)}</div>
        <div class="row" style="margin-top:14px"><button id="tts">🔊 Vorlesen</button><div class="grow"></div><button class="primary" id="go">Weiter</button></div>`;
    } else {
      html += `<p class="small muted">Schritt 2: Sag den Vers nur mit Hilfe der Anfangsbuchstaben auf.</p>
        <div class="text" id="lt">${esc(lettersOnly(full))}</div>
        <div class="row" style="margin-top:14px"><button id="peek">Text zeigen</button><div class="grow"></div><button class="primary" id="go">Weiter</button></div>`;
    }
    html += `</div>`;
    $view.innerHTML = html;
    bindQuit();
    const tts = document.getElementById("tts"); if (tts) tts.onclick = () => speak(full);
    const peek = document.getElementById("peek");
    if (peek) peek.onclick = () => { const lt = document.getElementById("lt"); const on = peek.dataset.on === "1";
      lt.innerHTML = on ? esc(lettersOnly(full)) : versesHtml(ctx.verses, many); peek.dataset.on = on ? "0" : "1"; peek.textContent = on ? "Text zeigen" : "Buchstaben zeigen"; };
    document.getElementById("go").onclick = () => { sess.stage = st === "read" ? "letters" : "test"; renderCard(); };
    return;
  }
  // Abfrage
  const wasNew = card.kind === "v" && card.state === "new";
  html += (wasNew ? `<div class="steps"><i class="on"></i><i class="on"></i><i class="on"></i></div>` : "") + cueHtml(ctx);
  html += `<div id="ex"></div></div>`;
  $view.innerHTML = html;
  bindQuit();
  const ex = document.getElementById("ex");
  const onDone = (res) => showRating(ex, card, res);
  if (sess.mode === "tippen") typingExercise(ex, ctx.verses, many, onDone);
  else revealExercise(ex, ctx.verses, many, full, onDone);
}

function bindQuit() {
  const q = document.getElementById("quit");
  if (q) q.onclick = () => { sess = null; show("heute"); };
}

function revealExercise(ex, verses, many, full, onDone) {
  ex.innerHTML = `<p class="small muted">Sag den Text laut auf. Dann aufdecken und ehrlich bewerten.</p>
    <div class="row"><button id="hint">Anfangsbuchstaben</button><button class="primary grow" id="rev">Aufdecken</button></div><div id="hintbox"></div>`;
  let hints = false;
  ex.querySelector("#hint").onclick = () => { hints = true; ex.querySelector("#hintbox").innerHTML = `<div class="text muted" style="margin-top:10px">${esc(lettersOnly(full))}</div>`; };
  ex.querySelector("#rev").onclick = () => {
    ex.innerHTML = `<div class="text">${versesHtml(verses, many)}</div><div class="row" style="margin-top:8px"><button id="tts">🔊</button></div>`;
    ex.querySelector("#tts").onclick = () => speak(full);
    onDone({ errors: null, words: 0, hints });
  };
}

// Anfangsbuchstaben-Tippen: jedes Wort wird aufgedeckt, wenn der erste Buchstabe stimmt.
function typingExercise(ex, verses, many, onDone) {
  const toks = [];
  verses.forEach((v) => {
    if (many) toks.push({ sup: v.v });
    v.t.split(" ").filter(Boolean).forEach((w) => { const l = firstLetter(w); toks.push({ w, l: l ? norm(l) : null, s: l ? "hid" : "okw" }); });
  });
  const words = toks.filter((t) => t.l);
  let pos = 0, errors = 0, hints = false, showHints = false, finished = false;
  ex.innerHTML = `<div class="text" id="tx"></div>
    <div class="typebox"><input id="ti" autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false" placeholder="tippen …" enterkeyhint="next"></div>
    <div class="row" style="margin-top:8px"><button id="hb">Buchstaben zeigen</button><button id="wb">Wort zeigen</button><div class="grow"></div><button id="ab">Alles aufdecken</button></div>
    <div id="res"></div>`;
  const tx = ex.querySelector("#tx"), ti = ex.querySelector("#ti");
  function draw() {
    tx.innerHTML = toks.map((t) => {
      if (t.sup != null) return `<sup>${t.sup}</sup>`;
      let cls = t.s;
      let txt = esc(t.w);
      if (t.s === "hid") {
        if (showHints) { cls = "hint"; txt = esc(lettersOnly(t.w)) + "&nbsp;".repeat(Math.max(1, Math.min(6, t.w.length - 1))); }
        if (t === words[pos]) cls += " cur";
      }
      return `<span class="w ${cls}">${txt}</span>`;
    }).join(" ");
  }
  function advance() { while (pos < words.length && words[pos].s !== "hid") pos++; if (pos >= words.length) finish(); }
  function finish() {
    if (finished) return; finished = true;
    ti.disabled = true; ti.placeholder = "Fertig";
    ["hb", "wb", "ab"].forEach((id) => (ex.querySelector("#" + id).disabled = true));
    draw();
    onDone({ errors, words: words.length, hints });
  }
  function typed(ch) {
    if (finished || !/[\p{L}\p{N}]/u.test(ch)) return;
    const w = words[pos]; if (!w) return;
    if (norm(ch) === w.l) w.s = "okw"; else { w.s = "err"; errors++; if (navigator.vibrate) navigator.vibrate(30); }
    pos++; advance(); draw();
  }
  ti.addEventListener("input", () => { const v = ti.value; ti.value = ""; for (const ch of v) typed(ch); });
  ex.querySelector("#hb").onclick = () => { showHints = !showHints; if (showHints) hints = true; draw(); ti.focus(); };
  ex.querySelector("#wb").onclick = () => { const w = words[pos]; if (w) { w.s = "err"; errors++; pos++; advance(); draw(); } ti.focus(); };
  ex.querySelector("#ab").onclick = () => { for (const w of words) if (w.s === "hid") { w.s = "err"; errors++; } pos = words.length; finish(); };
  tx.onclick = () => ti.focus();
  draw(); advance();
  setTimeout(() => ti.focus(), 50);
}

function showRating(ex, card, res) {
  const now = Date.now();
  let base = card;
  if (card.state === "new" && card.kind !== "v") base = Object.assign({}, card, { state: "review", interval: 1, due: now });
  const sug = res.errors == null ? (res.hints ? 2 : 3) : SRS.suggest(res.errors, res.words, res.hints);
  const labels = ["Nochmal", "Schwer", "Gut", "Leicht"];
  let msg = "";
  if (res.errors != null) {
    const pct = res.words ? Math.round(100 * (res.words - res.errors) / res.words) : 100;
    const cls = pct >= 92 ? "good" : pct >= 75 ? "mid" : "bad";
    msg = `<div class="result ${cls}">${pct} % richtig · ${res.errors} Fehler bei ${res.words} Wörtern${res.hints ? " · mit Hinweisen" : ""}</div>`;
  }
  const btns = [1, 2, 3, 4].map((r) => {
    const n = SRS.schedule(base, r, now, { noFuzz: true });
    return `<button class="r${r} ${r === sug ? "sug" : ""}" data-r="${r}">${labels[r - 1]}<small>${SRS.fmtIvl(n.due - now)}</small></button>`;
  }).join("");
  const box = document.createElement("div");
  box.innerHTML = `${msg}<div class="rate">${btns}</div>`;
  ex.appendChild(box);
  box.querySelector(".rate").onclick = (e) => {
    const b = e.target.closest("button[data-r]"); if (!b) return;
    const r = +b.dataset.r;
    const wasNew = card.state === "new";
    const upd = SRS.schedule(base, r, Date.now());
    S.cards[card.id] = upd;
    const lg = todayLog(); lg.rev++; if (wasNew && card.kind === "v") lg.new++;
    save();
    sess.done++;
    if (upd.state === "learning") sess.learning.push(card.id);
    nextCard();
  };
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------- Freies Üben (am Stück) ----------
function startPractice(p, a, b) {
  const verses = p.verses.slice(a, b + 1);
  const many = verses.length > 1;
  const prevV = a > 0 ? p.verses[a - 1] : null;
  $view.innerHTML = `<div class="card"><div class="sess-head"><div><div class="ref">${esc(rangeRef(p, a, b))}</div>
    <div class="small muted">Freies Üben · ${verses.length} Verse</div></div><button class="link" id="quit">Beenden</button></div>
    ${prevV ? `<div class="cue">… ${esc(prevV.t.split(" ").slice(-14).join(" "))}</div>` : ""}
    <div id="ex"></div></div>`;
  document.getElementById("quit").onclick = () => show("detail", p.id);
  const ex = document.getElementById("ex");
  const done = (res) => {
    const box = document.createElement("div");
    let msg = "Fertig.";
    if (res.errors != null) {
      const pct = res.words ? Math.round(100 * (res.words - res.errors) / res.words) : 100;
      msg = `${pct} % richtig · ${res.errors} Fehler bei ${res.words} Wörtern`;
    }
    box.innerHTML = `<div class="result">${msg}</div><div class="row" style="margin-top:10px">
      <button id="again">Nochmal</button><button class="primary" id="back">Zurück</button></div>`;
    ex.appendChild(box);
    box.querySelector("#again").onclick = () => startPractice(p, a, b);
    box.querySelector("#back").onclick = () => show("detail", p.id);
  };
  if (S.settings.mode === "tippen") typingExercise(ex, verses, many, done);
  else revealExercise(ex, verses, many, verses.map((v) => v.t).join(" "), done);
}

// ---------- Start ----------
loadTranslations();
show("heute");
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
