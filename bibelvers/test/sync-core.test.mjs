import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { NetlifyDB } from "@netlify/database-dev";
import pg from "pg";
import { applySync, HttpError } from "../lib/sync-core.mjs";

let db, client;
before(async () => {
  db = new NetlifyDB();
  const cs = await db.start();
  await db.applyMigrations("./netlify/database/migrations");
  client = new pg.Client({ connectionString: cs });
  await client.connect();
});
after(async () => { await client?.end(); await db?.stop(); });

const card = (id, ivl, t) => ({ kind: "card", id, data: { id, interval: ivl }, updated_at: t });

test("Erstsync liefert eigene Daten zurück, Cursor steigt", async () => {
  const r = await applySync(client, "anna", { cursor: 0, changes: [card("p1:v:0-0", 1, 100), { kind: "settings", id: "settings", data: { newPerDay: 3 }, updated_at: 100 }] });
  assert.equal(r.changes.length, 2);
  assert.ok(r.cursor > 0);
  const again = await applySync(client, "anna", { cursor: r.cursor, changes: [] });
  assert.equal(again.changes.length, 0);
  assert.equal(again.cursor, r.cursor);
});

test("Zweites Gerät bekommt alles, Nutzer sind getrennt", async () => {
  const ben = await applySync(client, "ben", { cursor: 0, changes: [] });
  assert.equal(ben.changes.length, 0);
  const anna2 = await applySync(client, "anna", { cursor: 0, changes: [] });
  assert.equal(anna2.changes.length, 2);
});

test("Neuerer Stand gewinnt, älterer wird ignoriert", async () => {
  const a = await applySync(client, "anna", { cursor: 0, changes: [] });
  const r1 = await applySync(client, "anna", { cursor: a.cursor, changes: [card("p1:v:0-0", 8, 300)] });
  assert.deepEqual(r1.changes.map((c) => c.data.interval), [8]);
  const r2 = await applySync(client, "anna", { cursor: r1.cursor, changes: [card("p1:v:0-0", 3, 200)] });
  assert.equal(r2.changes.length, 0, "älterer Stand darf nichts ändern");
  const all = await applySync(client, "anna", { cursor: 0, changes: [] });
  assert.equal(all.changes.find((c) => c.id === "p1:v:0-0").data.interval, 8);
});

test("Löschen wird synchronisiert", async () => {
  const a = await applySync(client, "anna", { cursor: 0, changes: [] });
  const r = await applySync(client, "anna", { cursor: a.cursor, changes: [{ kind: "card", id: "p1:v:0-0", updated_at: 400, deleted: true }] });
  assert.equal(r.changes[0].deleted, true);
  assert.equal(r.changes[0].data, null);
});

test("Ungültige Eingaben werden abgelehnt, ohne Teilschreibvorgang", async () => {
  await assert.rejects(applySync(client, "anna", { changes: [{ kind: "hack", id: "x", data: {}, updated_at: 1 }] }), HttpError);
  await assert.rejects(applySync(client, "anna", { changes: [card("ok", 1, 500), { kind: "card", id: "", data: {}, updated_at: 1 }] }), HttpError);
  const all = await applySync(client, "anna", { cursor: 0, changes: [] });
  assert.ok(!all.changes.some((c) => c.id === "ok"));
  await assert.rejects(applySync(client, "", { changes: [] }), HttpError);
});
