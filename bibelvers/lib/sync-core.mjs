// Synchronisierung: Geräte schicken geänderte Datensätze und holen alles, was seit ihrem Stand (cursor) neu ist.
// Konflikte: der Datensatz mit dem jüngeren updated_at gewinnt (pro Datensatz).

export class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export const KINDS = new Set(["passage", "card", "log", "settings"]);
export const MAX_CHANGES = 2000;
export const PAGE = 5000;
const MAX_ITEM_BYTES = 2_000_000;

export function validateChange(c) {
  if (!c || typeof c !== "object") throw new HttpError(400, "Ungültiger Datensatz");
  if (!KINDS.has(c.kind)) throw new HttpError(400, "Unbekannte Art: " + c.kind);
  if (typeof c.id !== "string" || !c.id || c.id.length > 200) throw new HttpError(400, "Ungültige ID");
  if (!Number.isSafeInteger(c.updated_at) || c.updated_at <= 0) throw new HttpError(400, "Ungültiger Zeitstempel");
  const deleted = c.deleted === true;
  if (!deleted && (c.data === null || typeof c.data !== "object")) throw new HttpError(400, "Daten fehlen");
  const json = deleted ? null : JSON.stringify(c.data);
  if (json && json.length > MAX_ITEM_BYTES) throw new HttpError(413, "Datensatz zu groß");
  return { kind: c.kind, id: c.id, json, updated_at: c.updated_at, deleted };
}

// client: verbundener pg-Client (nicht der Pool), damit die Transaktion auf einer Verbindung läuft.
export async function applySync(client, userId, body) {
  if (!userId) throw new HttpError(401, "Nicht angemeldet");
  const cursor = Number.isSafeInteger(body?.cursor) && body.cursor > 0 ? body.cursor : 0;
  const raw = Array.isArray(body?.changes) ? body.changes : [];
  if (raw.length > MAX_CHANGES) throw new HttpError(413, "Zu viele Änderungen auf einmal");
  const changes = raw.map(validateChange);

  await client.query("BEGIN");
  try {
    // Pro Nutzer serialisieren: so werden seq-Werte eines Nutzers in Reihenfolge sichtbar und kein Gerät verpasst etwas.
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [userId]);
    for (const c of changes) {
      await client.query(
        `INSERT INTO items (user_id, kind, id, data, updated_at, deleted)
         VALUES ($1, $2, $3, $4::jsonb, $5, $6)
         ON CONFLICT (user_id, kind, id) DO UPDATE
           SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at, deleted = EXCLUDED.deleted, seq = nextval('items_seq')
           WHERE items.updated_at < EXCLUDED.updated_at`,
        [userId, c.kind, c.id, c.json, c.updated_at, c.deleted],
      );
    }
    const { rows } = await client.query(
      `SELECT kind, id, data, updated_at, deleted, seq FROM items
       WHERE user_id = $1 AND seq > $2 ORDER BY seq LIMIT ${PAGE}`,
      [userId, cursor],
    );
    await client.query("COMMIT");
    const out = rows.map((r) => ({ kind: r.kind, id: r.id, data: r.data, updated_at: Number(r.updated_at), deleted: r.deleted }));
    return { changes: out, cursor: rows.length ? Number(rows[rows.length - 1].seq) : cursor, more: rows.length === PAGE };
  } catch (e) {
    await client.query("ROLLBACK").catch(() => {});
    throw e;
  }
}
