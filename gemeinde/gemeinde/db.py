"""SQLite-Zugriff: Haushalte, Personen, Einstellungen."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Jede Migration wird genau einmal ausgeführt (Version in PRAGMA user_version).
# Neue Migrationen nur hinten anhängen, nie bestehende ändern.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE households (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        street TEXT NOT NULL DEFAULT '',
        zip TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE persons (
        id INTEGER PRIMARY KEY,
        household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL DEFAULT '',
        child INTEGER NOT NULL DEFAULT 0,
        birthday TEXT,
        mobile TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        member INTEGER NOT NULL DEFAULT 0,
        member_since TEXT,
        consent INTEGER NOT NULL DEFAULT 0,
        consent_on TEXT,
        left_on TEXT,
        note TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX persons_household ON persons(household_id);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 2: Koordinaten je Haushalt für die Karte.
    # geo: '' = noch nicht ermittelt, 'auto' = Straße gefunden, 'ungenau' = nur PLZ/Ort,
    #      'fehlt' = nicht gefunden, 'hand' = von Hand gesetzt.
    # geo_addr: Anschrift, für die die Koordinaten gelten (erkennt Umzüge).
    """
    ALTER TABLE households ADD COLUMN lat REAL;
    ALTER TABLE households ADD COLUMN lon REAL;
    ALTER TABLE households ADD COLUMN geo TEXT NOT NULL DEFAULT '';
    ALTER TABLE households ADD COLUMN geo_addr TEXT NOT NULL DEFAULT '';
    """,
]

TABLES = ["households", "persons", "settings"]

DEFAULT_SETTINGS = {
    "church_name": "",
    "pdf_note": "Vertraulich – nur für den Gebrauch innerhalb der Gemeinde. Bitte nicht weitergeben.",
    "church_address": "",
    "church_lat": "",
    "church_lon": "",
}


@dataclass
class Person:
    id: int | None
    household_id: int
    first_name: str
    last_name: str = ""
    child: bool = False
    birthday: date | None = None
    mobile: str = ""
    email: str = ""
    member: bool = False
    member_since: date | None = None
    consent: bool = False
    consent_on: date | None = None
    left_on: date | None = None
    note: str = ""

    @property
    def active(self) -> bool:
        return self.left_on is None


@dataclass
class Household:
    id: int | None
    name: str
    street: str = ""
    zip: str = ""
    city: str = ""
    phone: str = ""
    note: str = ""
    persons: list[Person] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    geo: str = ""
    geo_addr: str = ""

    @property
    def place(self) -> str:
        return " ".join(p for p in (self.zip, self.city) if p)

    @property
    def address(self) -> str:
        """Anschrift für die Geokodierung (ohne Namen)."""
        return ", ".join(x for x in (self.street, self.place) if x)

    @property
    def has_pos(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def geo_stale(self) -> bool:
        """Koordinaten fehlen oder gehören zu einer alten Anschrift."""
        return bool(self.address) and (self.geo == "" or self.geo_addr != self.address)

    @property
    def active_persons(self) -> list[Person]:
        return [p for p in self.persons if p.active]

    def full_name(self, p: Person) -> str:
        return f"{p.first_name} {p.last_name or self.name}".strip()


def db_path() -> Path:
    data_dir = Path(os.environ.get("GEMEINDE_DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "gemeinde.sqlite3"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
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


def _iso(v: date | None) -> str | None:
    return v.isoformat() if v else None


def sort_key(p: Person):
    """Erwachsene vor Kindern, innerhalb nach Alter (Ältere zuerst)."""
    return (p.child, p.birthday or date.max, p.first_name.lower())


# --- Lesen -----------------------------------------------------------------

def _person(r) -> Person:
    return Person(
        r["id"], r["household_id"], r["first_name"], r["last_name"], bool(r["child"]),
        _d(r["birthday"]), r["mobile"], r["email"], bool(r["member"]), _d(r["member_since"]),
        bool(r["consent"]), _d(r["consent_on"]), _d(r["left_on"]), r["note"],
    )


def households(conn) -> list[Household]:
    hs = {
        r["id"]: Household(r["id"], r["name"], r["street"], r["zip"], r["city"], r["phone"], r["note"],
                           lat=r["lat"], lon=r["lon"], geo=r["geo"], geo_addr=r["geo_addr"])
        for r in conn.execute("SELECT * FROM households")
    }
    for r in conn.execute("SELECT * FROM persons"):
        hs[r["household_id"]].persons.append(_person(r))
    for h in hs.values():
        h.persons.sort(key=sort_key)
    return sorted(hs.values(), key=lambda h: (h.name.lower(), h.city.lower(), h.id))


def household(conn, hid: int) -> Household | None:
    return next((h for h in households(conn) if h.id == hid), None)


def person(conn, pid: int) -> Person | None:
    r = conn.execute("SELECT * FROM persons WHERE id = ?", (pid,)).fetchone()
    return _person(r) if r else None


def settings(conn) -> dict[str, str]:
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
    return s


def is_empty(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM households").fetchone()[0] == 0


# --- Schreiben -------------------------------------------------------------

def save_household(conn, h: Household) -> int:
    vals = (h.name, h.street, h.zip, h.city, h.phone, h.note)
    with conn:
        if h.id:
            conn.execute(
                "UPDATE households SET name=?, street=?, zip=?, city=?, phone=?, note=? WHERE id=?",
                (*vals, h.id),
            )
            return h.id
        return conn.execute(
            "INSERT INTO households (name, street, zip, city, phone, note) VALUES (?,?,?,?,?,?)", vals
        ).lastrowid


def save_person(conn, p: Person) -> int:
    vals = (
        p.household_id, p.first_name, p.last_name, int(p.child), _iso(p.birthday), p.mobile,
        p.email, int(p.member), _iso(p.member_since), int(p.consent), _iso(p.consent_on),
        _iso(p.left_on), p.note,
    )
    cols = ("household_id, first_name, last_name, child, birthday, mobile, email, member,"
            " member_since, consent, consent_on, left_on, note")
    with conn:
        if p.id:
            sets = ", ".join(f"{c.strip()}=?" for c in cols.split(","))
            conn.execute(f"UPDATE persons SET {sets} WHERE id=?", (*vals, p.id))
            return p.id
        return conn.execute(
            f"INSERT INTO persons ({cols}) VALUES ({','.join('?' * len(vals))})", vals
        ).lastrowid


def set_geo(conn, hid: int, lat: float | None, lon: float | None, geo: str, geo_addr: str) -> None:
    with conn:
        conn.execute("UPDATE households SET lat=?, lon=?, geo=?, geo_addr=? WHERE id=?",
                     (lat, lon, geo, geo_addr, hid))


def consent_all(conn, hid: int, day: date) -> int:
    with conn:
        return conn.execute(
            "UPDATE persons SET consent=1, consent_on=COALESCE(consent_on, ?)"
            " WHERE household_id=? AND consent=0 AND left_on IS NULL",
            (day.isoformat(), hid),
        ).rowcount


def save_settings(conn, values: dict[str, str]) -> None:
    with conn:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?)"
                    " ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                    (k, v),
                )


def delete_household(conn, hid: int) -> None:
    with conn:
        conn.execute("DELETE FROM households WHERE id = ?", (hid,))


def delete_person(conn, pid: int) -> None:
    with conn:
        conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
