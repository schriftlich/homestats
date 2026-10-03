"""Einlesen von Kontoauszügen (CSV) verschiedener Banken.

Jeder Parser liefert ein Statement mit der IBAN des eigenen Kontos und einer
Liste von Buchungen in einem einheitlichen Format.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")


@dataclass
class Booking:
    line: int                 # Zeilennummer in der Datei (für Meldungen)
    date: date
    amount: Decimal           # negativ = Ausgang, positiv = Eingang
    counterparty: str         # Name der Gegenpartei
    iban: str                 # IBAN der Gegenpartei
    description: str          # Verwendungszweck (vollständig)
    booked: bool = True       # False = nur vorgemerkt
    creditor_id: str = ""
    mandate: str = ""
    reference: str = ""


@dataclass
class Statement:
    bank: str
    account_iban: str
    bookings: list = field(default_factory=list)


class ParseError(Exception):
    pass


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("Die Datei hat eine unbekannte Zeichenkodierung.")


def parse_german_amount(text: str) -> Decimal:
    t = text.replace("€", "").replace("\xa0", "").replace(" ", "").strip()
    t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ParseError(f"Betrag nicht lesbar: {text!r}")


def parse_german_date(text: str) -> date:
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$", text.strip())
    if not m:
        raise ParseError(f"Datum nicht lesbar: {text!r}")
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    return date(y, mo, d)


def parse_dkb(raw: bytes) -> Statement:
    """DKB-Export (neues Banking, ab 2023): Info-Zeilen, dann Tabelle mit Semikolon."""
    rows = list(csv.reader(io.StringIO(_decode(raw)), delimiter=";"))

    header_idx = None
    account_iban = ""
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if cells and cells[0] == "Buchungsdatum":
            header_idx = i
            break
        for c in cells:
            c2 = c.replace(" ", "")
            if not account_iban and IBAN_RE.match(c2):
                account_iban = c2
    if header_idx is None:
        raise ParseError("Keine DKB-Umsatztabelle gefunden (Spalte „Buchungsdatum“ fehlt).")

    header = [h.strip() for h in rows[header_idx]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    c = {
        "date": col("Buchungsdatum"),
        "status": col("Status"),
        "payer": col("Zahlungspflichtige*r", "Auftraggeber"),
        "payee": col("Zahlungsempfänger*in", "Empfänger"),
        "purpose": col("Verwendungszweck"),
        "iban": col("IBAN"),
        "amount": col("Betrag (€)", "Betrag (EUR)", "Betrag"),
        "creditor": col("Gläubiger-ID"),
        "mandate": col("Mandatsreferenz"),
        "ref": col("Kundenreferenz"),
    }
    for key in ("date", "amount"):
        if c[key] is None:
            raise ParseError(f"Pflichtspalte fehlt in der Datei: {key}")

    def get(row, key):
        idx = c[key]
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    st = Statement(bank="DKB", account_iban=account_iban)
    for n, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(cell.strip() for cell in row):
            continue
        amount = parse_german_amount(get(row, "amount"))
        cp = get(row, "payee") if amount < 0 else get(row, "payer")
        status = get(row, "status")
        st.bookings.append(Booking(
            line=n,
            date=parse_german_date(get(row, "date")),
            amount=amount,
            counterparty=re.sub(r"\s+", " ", cp),
            iban=get(row, "iban").replace(" ", "").upper(),
            description=re.sub(r"\s+", " ", get(row, "purpose")),
            booked=(status == "" or status.lower() == "gebucht"),
            creditor_id=get(row, "creditor"),
            mandate=get(row, "mandate"),
            reference=get(row, "ref"),
        ))
    return st


PARSERS = {"dkb": parse_dkb}


def parse(raw: bytes) -> Statement:
    """Erkennt die Bank anhand des Inhalts. Bisher nur DKB."""
    text = _decode(raw)
    if '"Buchungsdatum";"Wertstellung"' in text or "Buchungsdatum;Wertstellung" in text:
        return parse_dkb(raw)
    raise ParseError("Dieses Dateiformat kenne ich noch nicht. Bisher wird der DKB-CSV-Export unterstützt.")
