-- Ein generischer Speicher pro Nutzer: Texte (passage), Lernkarten (card), Tagesstatistik (log), Einstellungen (settings).
-- seq ist ein serverseitiger, streng steigender Zähler: Geräte holen alles mit seq > ihrem letzten Stand.
CREATE SEQUENCE items_seq;

CREATE TABLE items (
  user_id    TEXT    NOT NULL,
  kind       TEXT    NOT NULL,
  id         TEXT    NOT NULL,
  data       JSONB,
  updated_at BIGINT  NOT NULL,
  deleted    BOOLEAN NOT NULL DEFAULT FALSE,
  seq        BIGINT  NOT NULL DEFAULT nextval('items_seq'),
  PRIMARY KEY (user_id, kind, id)
);

CREATE INDEX items_user_seq ON items (user_id, seq);
