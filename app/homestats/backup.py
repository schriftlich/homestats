"""CSV-Backup: Export aller Tabellen als ZIP mit je einer CSV-Datei, Import
stellt den kompletten Datenbestand daraus wieder her.

CSV-Format: Trennzeichen ';', Dezimalkomma, Datum ISO (JJJJ-MM-TT), UTF-8
mit BOM – lässt sich so direkt in einer deutschen Tabellenkalkulation öffnen.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import zipfile

from .db import TABLES
from .fmt import parse_num, plain


class ImportError_(ValueError):
    pass


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, bool]]:
    """(name, typ, notnull) je Spalte."""
    return [(r[1], r[2].upper(), bool(r[3]) or bool(r[5])) for r in conn.execute(f"PRAGMA table_info({table})")]


def export_zip(conn: sqlite3.Connection) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in TABLES:
            cols = _columns(conn, table)
            out = io.StringIO()
            w = csv.writer(out, delimiter=";", lineterminator="\n")
            w.writerow([c[0] for c in cols])
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
                w.writerow([
                    "" if v is None else plain(v) if isinstance(v, float) else v
                    for v in row
                ])
            zf.writestr(f"{table}.csv", "﻿" + out.getvalue())
    return buf.getvalue()


def _parse_rows(conn, table: str, text: str) -> tuple[list[str], list[list]]:
    cols = {name: (typ, notnull) for name, typ, notnull in _columns(conn, table)}
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    header = next(reader, None)
    if not header:
        return [], []
    header = [h.strip() for h in header]
    unknown = [h for h in header if h not in cols]
    if unknown:
        raise ImportError_(f"{table}.csv: unbekannte Spalte(n) {', '.join(unknown)}")
    rows = []
    for line_no, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        vals = []
        for name, cell in zip(header, raw):
            typ, notnull = cols[name]
            cell = cell.strip()
            try:
                if cell == "":
                    v = "" if typ == "TEXT" and notnull else None
                elif typ == "REAL":
                    v = parse_num(cell)
                elif typ == "INTEGER":
                    v = int(cell)
                else:
                    v = cell
            except ValueError:
                raise ImportError_(f"{table}.csv Zeile {line_no}: ungültiger Wert '{cell}' in Spalte {name}")
            vals.append(v)
        rows.append(vals)
    return header, rows


def import_zip(conn: sqlite3.Connection, data: bytes) -> dict[str, int]:
    """Ersetzt ALLE Daten durch den Inhalt des Backups. Alles oder nichts."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("Die Datei ist kein gültiges ZIP-Backup.")
    names = set(zf.namelist())
    if "meters.csv" not in names:
        raise ImportError_("Im Backup fehlt meters.csv.")
    parsed = {}
    for table in TABLES:
        if f"{table}.csv" in names:
            parsed[table] = _parse_rows(conn, table, zf.read(f"{table}.csv").decode("utf-8"))
    counts: dict[str, int] = {}
    try:
        conn.execute("BEGIN")
        for table in reversed(TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table in TABLES:
            if table not in parsed:
                continue
            header, rows = parsed[table]
            if rows:
                conn.executemany(
                    f"INSERT INTO {table} ({','.join(header)}) VALUES ({','.join('?' * len(header))})",
                    rows,
                )
            counts[table] = len(rows)
        conn.execute("COMMIT")
    except sqlite3.Error as e:
        conn.execute("ROLLBACK")
        raise ImportError_(f"Import fehlgeschlagen, nichts wurde geändert: {e}")
    return counts
