"""SQLite-Zugriff mit einfachen, idempotenten Migrationen."""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

from .domain.model import (
    GasFactor,
    Installment,
    MediumSettings,
    Meter,
    Reading,
    Tariff,
)

# Jede Migration wird genau einmal ausgeführt (Version in PRAGMA user_version).
# Neue Migrationen nur hinten anhängen, nie bestehende ändern.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE meters (
        id INTEGER PRIMARY KEY,
        medium TEXT NOT NULL CHECK (medium IN ('strom','gas','wasser')),
        name TEXT NOT NULL,
        unit TEXT NOT NULL,
        meter_number TEXT NOT NULL DEFAULT '',
        dual INTEGER NOT NULL DEFAULT 0,
        installed_on TEXT,
        initial REAL NOT NULL DEFAULT 0,
        initial_nt REAL NOT NULL DEFAULT 0,
        removed_on TEXT
    );
    CREATE TABLE readings (
        id INTEGER PRIMARY KEY,
        meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        value REAL NOT NULL,
        value_nt REAL,
        note TEXT NOT NULL DEFAULT '',
        UNIQUE (meter_id, day)
    );
    CREATE TABLE tariffs (
        id INTEGER PRIMARY KEY,
        medium TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        base_month REAL NOT NULL,
        price REAL NOT NULL,
        price_nt REAL,
        UNIQUE (medium, valid_from)
    );
    CREATE TABLE gas_factors (
        id INTEGER PRIMARY KEY,
        valid_from TEXT NOT NULL UNIQUE,
        zustandszahl REAL NOT NULL,
        brennwert REAL NOT NULL
    );
    CREATE TABLE installments (
        id INTEGER PRIMARY KEY,
        medium TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        amount REAL NOT NULL,
        UNIQUE (medium, valid_from)
    );
    CREATE TABLE medium_settings (
        medium TEXT PRIMARY KEY,
        billing_start_month INTEGER NOT NULL DEFAULT 1,
        billing_start_day INTEGER NOT NULL DEFAULT 1,
        installments_per_period INTEGER NOT NULL DEFAULT 12
    );
    """,
]

TABLES = ["meters", "readings", "tariffs", "gas_factors", "installments", "medium_settings"]


def db_path() -> Path:
    data_dir = Path(os.environ.get("HOMESTATS_DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "homestats.sqlite3"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # Eine Verbindung pro Request; FastAPI kann Abhängigkeit und Route in
    # unterschiedlichen Threads ausführen.
    conn = sqlite3.connect(path or db_path(), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {n}; COMMIT;")


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


# --- Lesen -----------------------------------------------------------------

def meters(conn, medium: str | None = None) -> list[Meter]:
    sql = "SELECT * FROM meters"
    args: tuple = ()
    if medium:
        sql += " WHERE medium = ?"
        args = (medium,)
    rows = conn.execute(sql + " ORDER BY medium, removed_on IS NOT NULL, installed_on, id", args)
    return [
        Meter(
            r["id"], r["medium"], r["name"], r["unit"], r["meter_number"], bool(r["dual"]),
            _d(r["installed_on"]), r["initial"], r["initial_nt"], _d(r["removed_on"]),
        )
        for r in rows
    ]


def meter(conn, meter_id: int) -> Meter | None:
    return next((m for m in meters(conn) if m.id == meter_id), None)


def readings(conn, meter_ids: list[int] | None = None) -> list[Reading]:
    sql = "SELECT * FROM readings"
    if meter_ids is not None:
        sql += f" WHERE meter_id IN ({','.join('?' * len(meter_ids))})"
    rows = conn.execute(sql + " ORDER BY day DESC, id DESC", meter_ids or ())
    return [
        Reading(r["id"], r["meter_id"], date.fromisoformat(r["day"]), r["value"], r["value_nt"], r["note"])
        for r in rows
    ]


def reading(conn, reading_id: int) -> Reading | None:
    r = conn.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
    if not r:
        return None
    return Reading(r["id"], r["meter_id"], date.fromisoformat(r["day"]), r["value"], r["value_nt"], r["note"])


def tariffs(conn, medium: str) -> list[Tariff]:
    rows = conn.execute("SELECT * FROM tariffs WHERE medium = ? ORDER BY valid_from", (medium,))
    return [
        Tariff(r["id"], r["medium"], date.fromisoformat(r["valid_from"]), r["base_month"], r["price"], r["price_nt"])
        for r in rows
    ]


def gas_factors(conn) -> list[GasFactor]:
    rows = conn.execute("SELECT * FROM gas_factors ORDER BY valid_from")
    return [
        GasFactor(r["id"], date.fromisoformat(r["valid_from"]), r["zustandszahl"], r["brennwert"])
        for r in rows
    ]


def installments(conn, medium: str) -> list[Installment]:
    rows = conn.execute("SELECT * FROM installments WHERE medium = ? ORDER BY valid_from", (medium,))
    return [Installment(r["id"], r["medium"], date.fromisoformat(r["valid_from"]), r["amount"]) for r in rows]


def settings(conn, medium: str) -> MediumSettings:
    r = conn.execute("SELECT * FROM medium_settings WHERE medium = ?", (medium,)).fetchone()
    if not r:
        return MediumSettings(medium)
    return MediumSettings(medium, r["billing_start_month"], r["billing_start_day"], r["installments_per_period"])


def is_empty(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM meters").fetchone()[0] == 0


# --- Schreiben -------------------------------------------------------------

def save_meter(conn, m: Meter) -> int:
    vals = (
        m.medium, m.name, m.unit, m.meter_number, int(m.dual),
        m.installed_on.isoformat() if m.installed_on else None, m.initial, m.initial_nt,
        m.removed_on.isoformat() if m.removed_on else None,
    )
    with conn:
        if m.id:
            conn.execute(
                "UPDATE meters SET medium=?, name=?, unit=?, meter_number=?, dual=?, installed_on=?,"
                " initial=?, initial_nt=?, removed_on=? WHERE id=?",
                (*vals, m.id),
            )
            return m.id
        cur = conn.execute(
            "INSERT INTO meters (medium, name, unit, meter_number, dual, installed_on, initial,"
            " initial_nt, removed_on) VALUES (?,?,?,?,?,?,?,?,?)",
            vals,
        )
        return cur.lastrowid


def save_reading(conn, r: Reading) -> int:
    """Speichert eine Ablesung. Gibt es am selben Tag schon eine für den
    Zähler, wird sie überschrieben."""
    with conn:
        if r.id:
            conn.execute(
                "UPDATE readings SET meter_id=?, day=?, value=?, value_nt=?, note=? WHERE id=?",
                (r.meter_id, r.day.isoformat(), r.value, r.value_nt, r.note, r.id),
            )
            return r.id
        cur = conn.execute(
            "INSERT INTO readings (meter_id, day, value, value_nt, note) VALUES (?,?,?,?,?)"
            " ON CONFLICT (meter_id, day) DO UPDATE SET value=excluded.value,"
            " value_nt=excluded.value_nt, note=excluded.note RETURNING id",
            (r.meter_id, r.day.isoformat(), r.value, r.value_nt, r.note),
        )
        return cur.fetchone()[0]


def save_tariff(conn, t: Tariff) -> None:
    with conn:
        conn.execute(
            "INSERT INTO tariffs (medium, valid_from, base_month, price, price_nt) VALUES (?,?,?,?,?)"
            " ON CONFLICT (medium, valid_from) DO UPDATE SET base_month=excluded.base_month,"
            " price=excluded.price, price_nt=excluded.price_nt",
            (t.medium, t.valid_from.isoformat(), t.base_month, t.price, t.price_nt),
        )


def save_gas_factor(conn, g: GasFactor) -> None:
    with conn:
        conn.execute(
            "INSERT INTO gas_factors (valid_from, zustandszahl, brennwert) VALUES (?,?,?)"
            " ON CONFLICT (valid_from) DO UPDATE SET zustandszahl=excluded.zustandszahl,"
            " brennwert=excluded.brennwert",
            (g.valid_from.isoformat(), g.zustandszahl, g.brennwert),
        )


def save_installment(conn, i: Installment) -> None:
    with conn:
        conn.execute(
            "INSERT INTO installments (medium, valid_from, amount) VALUES (?,?,?)"
            " ON CONFLICT (medium, valid_from) DO UPDATE SET amount=excluded.amount",
            (i.medium, i.valid_from.isoformat(), i.amount),
        )


def save_settings(conn, s: MediumSettings) -> None:
    with conn:
        conn.execute(
            "INSERT INTO medium_settings (medium, billing_start_month, billing_start_day,"
            " installments_per_period) VALUES (?,?,?,?) ON CONFLICT (medium) DO UPDATE SET"
            " billing_start_month=excluded.billing_start_month,"
            " billing_start_day=excluded.billing_start_day,"
            " installments_per_period=excluded.installments_per_period",
            (s.medium, s.billing_start_month, s.billing_start_day, s.installments_per_period),
        )


DELETABLE = {"meters", "readings", "tariffs", "gas_factors", "installments"}


def delete(conn, table: str, row_id: int) -> None:
    if table not in DELETABLE:
        raise ValueError(table)
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))

