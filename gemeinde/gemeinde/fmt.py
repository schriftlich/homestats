"""Deutsche Datumsformate und Geburtstagsrechnung (ohne Locale im Container)."""
from __future__ import annotations

from datetime import date

MONTHS_LONG = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def ddate(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def parse_date(text: str | None) -> date | None:
    """Akzeptiert ISO (aus <input type=date>) und TT.MM.JJJJ. Leer -> None."""
    if not text or not text.strip():
        return None
    t = text.strip()
    if "." in t:
        d, m, y = (x.strip() for x in t.split("."))
        y = int(y)
        if y < 100:  # zweistellige Jahreszahl
            y += 2000 if y <= date.today().year % 100 else 1900
        return date(y, int(m), int(d))
    return date.fromisoformat(t)


def next_birthday(birthday: date, today: date) -> date:
    """Nächster Geburtstag ab heute (heute zählt mit). 29.2. -> 28.2. in Nicht-Schaltjahren."""
    for year in (today.year, today.year + 1):
        try:
            d = birthday.replace(year=year)
        except ValueError:
            d = date(year, 2, 28)
        if d >= today:
            return d
    raise AssertionError("unreachable")


def age_on(birthday: date, day: date) -> int:
    return day.year - birthday.year - ((day.month, day.day) < (birthday.month, birthday.day))
