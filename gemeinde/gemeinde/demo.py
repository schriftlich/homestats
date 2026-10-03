"""Erfundene Beispieldaten für die lokale Entwicklung (GEMEINDE_DEMO=1)."""
from __future__ import annotations

from datetime import date

from . import db
from .db import Household, Person

DATA = [
    ("Beispiel", "Lindenweg 4", "73430", "Aalen", "07361 000111", [
        ("Thomas", False, date(1979, 3, 14), "0151 0000001", "thomas@example.org", True),
        ("Ruth", False, date(1982, 11, 2), "0151 0000002", "ruth@example.org", True),
        ("Jonas", True, date(2008, 6, 21), "0151 0000003", "", True),
        ("Lea", True, date(2013, 1, 9), "", "", False),
        ("Ben", True, date(2017, 10, 5), "", "", False),
    ]),
    ("Muster", "Am Kirchberg 12", "73431", "Aalen", "", [
        ("Elisabeth", False, date(1948, 2, 29), "0170 0000004", "", True),
    ]),
    ("Probe", "Gartenstraße 1", "73433", "Aalen", "07361 000222", [
        ("Daniel", False, date(1990, 7, 30), "0160 0000005", "daniel@example.org", True),
        ("Miriam", False, date(1991, 4, 18), "0160 0000006", "miriam@example.org", False),
        ("Noah", True, date(2021, 12, 24), "", "", False),
    ]),
]


def seed(conn) -> None:
    for name, street, zip_, city, phone, persons in DATA:
        hid = db.save_household(conn, Household(None, name, street, zip_, city, phone))
        for first, child, bday, mobile, email, member in persons:
            db.save_person(conn, Person(
                None, hid, first, "", child, bday, mobile, email, member,
                consent=True, consent_on=date(2026, 1, 1),
            ))
