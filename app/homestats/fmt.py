"""Deutsche Zahlen- und Datumsformate (ohne Locale-Abhängigkeit im Container)."""
from __future__ import annotations

from datetime import date

MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
MONTHS_LONG = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "–"
    s = f"{value:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def euro(value: float | None, digits: int = 2) -> str:
    return "–" if value is None else f"{num(value, digits)} €"


def plain(value: float | None) -> str:
    """Zahl für Eingabefelder: Dezimalkomma, keine Tausenderpunkte, ohne unnötige Nullen."""
    if value is None:
        return ""
    s = f"{value:.6f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def ddate(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "–"


def month_label(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]} {year}"


def parse_num(text: str | None) -> float | None:
    """Akzeptiert '1234,5', '1.234,5', '1234.5'. Leer -> None."""
    if text is None:
        return None
    t = text.strip().replace(" ", "").replace(" ", "").replace("€", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def parse_date(text: str | None) -> date | None:
    """Akzeptiert ISO (aus <input type=date>) und TT.MM.JJJJ."""
    if not text or not text.strip():
        return None
    t = text.strip()
    if "." in t:
        d, m, y = t.split(".")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(t)
