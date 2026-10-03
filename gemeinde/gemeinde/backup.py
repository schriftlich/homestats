"""CSV-Backup: ZIP mit einer CSV je Tabelle; Import ersetzt den kompletten Bestand.

CSV-Format: Trennzeichen ';', Datum ISO (JJJJ-MM-TT), UTF-8 mit BOM – lässt
sich direkt in einer deutschen Tabellenkalkulation öffnen.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import zipfile

from .db import TABLES

BOM = "﻿"


class BackupError(ValueError):
    pass


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool]]:
    """name -> (typ, notnull)"""
    return {r[1]: (r[2].upper(), bool(r[3]) or bool(r[5])) for r in conn.execute(f"PRAGMA table_info({table})")}


def export_zip(conn: sqlite3.Connection) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in TABLES:
            out = io.StringIO()
            w = csv.writer(out, delimiter=";", lineterminator="\n")
            w.writerow(list(_columns(conn, table)))
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
                w.writerow(["" if v is None else v for v in row])
            zf.writestr(f"{table}.csv", BOM + out.getvalue())
    return buf.getvalue()


def _parse(conn, table: str, text: str) -> tuple[list[str], list[list]]:
    cols = _columns(conn, table)
    reader = csv.reader(io.StringIO(text.lstrip(BOM)), delimiter=";")
    header = [h.strip() for h in next(reader, [])]
    unknown = [h for h in header if h not in cols]
    if unknown:
        raise BackupError(f"{table}.csv: unbekannte Spalte(n) {', '.join(unknown)}")
    rows = []
    for line_no, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        vals = []
        for name, cell in zip(header, raw):
            typ, notnull = cols[name]
            cell = cell.strip()
            if cell == "":
                vals.append("" if typ == "TEXT" and notnull else None)
            elif typ == "INTEGER":
                try:
                    vals.append(int(cell))
                except ValueError:
                    raise BackupError(f"{table}.csv Zeile {line_no}: ungültige Zahl '{cell}' in Spalte {name}")
            else:
                vals.append(cell)
        rows.append(vals)
    return header, rows


def import_zip(conn: sqlite3.Connection, data: bytes) -> dict[str, int]:
    """Ersetzt ALLE Daten durch den Inhalt des Backups. Alles oder nichts."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("Die Datei ist kein gültiges ZIP-Backup.")
    names = set(zf.namelist())
    for required in ("households.csv", "persons.csv"):
        if required not in names:
            raise BackupError(f"Im Backup fehlt {required}.")
    parsed = {
        t: _parse(conn, t, zf.read(f"{t}.csv").decode("utf-8"))
        for t in TABLES if f"{t}.csv" in names
    }
    counts: dict[str, int] = {}
    try:
        conn.execute("BEGIN")
        for table in reversed(TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table, (header, rows) in parsed.items():
            if rows:
                conn.executemany(
                    f"INSERT INTO {table} ({','.join(header)}) VALUES ({','.join('?' * len(header))})",
                    rows,
                )
            counts[table] = len(rows)
        conn.execute("COMMIT")
    except sqlite3.Error as e:
        conn.execute("ROLLBACK")
        raise BackupError(f"Import fehlgeschlagen, nichts wurde geändert: {e}")
    return counts
