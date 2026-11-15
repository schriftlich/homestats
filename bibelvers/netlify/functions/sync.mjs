import { getUser, verifyRequestOrigin } from "@netlify/identity";
import { getDatabase } from "@netlify/database";
import { applySync, HttpError } from "../../lib/sync-core.mjs";

export default async (req) => {
  if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });
  try { verifyRequestOrigin(req); } catch { return Response.json({ error: "Ungültige Herkunft" }, { status: 403 }); }

  const user = await getUser();
  if (!user) return Response.json({ error: "Nicht angemeldet" }, { status: 401 });

  let body;
  try { body = await req.json(); } catch { return Response.json({ error: "Ungültiges JSON" }, { status: 400 }); }

  const client = await getDatabase().pool.connect();
  try {
    return Response.json(await applySync(client, user.id, body));
  } catch (e) {
    if (e instanceof HttpError) return Response.json({ error: e.message }, { status: e.status });
    console.error("sync failed", e);
    return Response.json({ error: "Serverfehler" }, { status: 500 });
  } finally {
    client.release();
  }
};

export const config = { path: "/api/sync" };
