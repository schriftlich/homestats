"""Anbindung an Firefly III über die offizielle REST-Schnittstelle."""
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import requests

from .banks import Booking, Statement

MAX_DESC = 500  # Firefly erlaubt 1000 Zeichen, wir bleiben deutlich darunter
HISTORY_DAYS = 400  # so weit zurück wird für Duplikate und Kategorie-Vorschläge gelesen


class FireflyError(Exception):
    pass


class Firefly:
    def __init__(self, url: str, token: str, timeout: int = 30):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/json",
        })
        self.timeout = timeout

    def _get(self, path, **params):
        r = self.s.get(f"{self.url}/api/v1{path}", params=params, timeout=self.timeout)
        if r.status_code == 401:
            raise FireflyError("Firefly lehnt den Zugangsschlüssel ab (401). Ist FIREFLY_TOKEN korrekt?")
        if not r.ok:
            raise FireflyError(f"Firefly-Fehler {r.status_code} bei {path}: {r.text[:300]}")
        return r.json()

    def _get_all(self, path, **params):
        page, out = 1, []
        while True:
            data = self._get(path, page=page, limit=200, **params)
            out.extend(data.get("data", []))
            pag = data.get("meta", {}).get("pagination", {})
            if page >= int(pag.get("total_pages", 1) or 1):
                return out
            page += 1

    def asset_accounts(self):
        """Gibt {IBAN: (id, name)} aller Bestandskonten zurück."""
        res = {}
        for acc in self._get_all("/accounts", type="asset"):
            a = acc["attributes"]
            iban = (a.get("iban") or "").replace(" ", "").upper()
            if iban:
                res[iban] = (acc["id"], a.get("name", ""))
        return res

    def account_transactions(self, account_id, start, end):
        out = []
        for grp in self._get_all(f"/accounts/{account_id}/transactions",
                                 start=start.isoformat(), end=end.isoformat()):
            out.extend(grp["attributes"]["transactions"])
        return out

    def categories(self):
        """Namen aller Kategorien, alphabetisch."""
        return sorted((c["attributes"]["name"] for c in self._get_all("/categories")), key=str.lower)

    def create_category(self, name):
        r = self.s.post(f"{self.url}/api/v1/categories", json={"name": name}, timeout=self.timeout)
        if not r.ok:
            raise FireflyError(f"Kategorie „{name}“ konnte nicht angelegt werden: {r.status_code} {r.text[:200]}")

    def create(self, payload):
        # x_-Felder sind nur für andere Ziele (Sure) gedacht
        payload = dict(payload, transactions=[{k: v for k, v in t.items() if not k.startswith("x_")}
                                              for t in payload["transactions"]])
        r = self.s.post(f"{self.url}/api/v1/transactions", json=payload, timeout=self.timeout)
        if r.ok:
            return True, r.json()["data"]["id"]
        try:
            msg = r.json().get("message") or r.text
            errs = r.json().get("errors")
            if errs:
                msg = "; ".join(f"{k}: {', '.join(v)}" for k, v in errs.items())
        except ValueError:
            msg = r.text
        return False, f"{r.status_code}: {msg[:300]}"


# --- Planung -----------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()[:40]


def _key(d, amount, desc):
    return (d.isoformat()[:10], f"{abs(Decimal(str(amount))):.2f}", _norm(desc))


def _short(desc: str) -> str:
    if len(desc) <= MAX_DESC:
        return desc
    cut = desc[:MAX_DESC].rsplit(" ", 1)[0]
    return cut + " …"


def external_id(st: Statement, b: Booking) -> str:
    raw = "|".join([st.account_iban, b.date.isoformat(), f"{b.amount:.2f}",
                    b.iban, b.description, b.reference])
    return "bankimport-" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _name(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


class CategoryLearner:
    """Merkt sich, welche Kategorie eine Gegenpartei bisher bekommen hat.

    Vorgeschlagen wird nur, wenn die Historie eindeutig ist: Alle bisherigen
    Buchungen dieser Gegenpartei haben dieselbe Kategorie. Sammel-Gegenparteien
    wie PayPal oder die Kreditkarten-IBAN bekommen so keinen falschen Vorschlag.
    """

    def __init__(self, history):
        self.by_name = {}
        self.by_iban = {}
        iban_names = {}
        for t in history:
            if t.get("type") not in ("withdrawal", "deposit"):
                continue
            if t["type"] == "withdrawal":
                name, iban = t.get("destination_name"), t.get("destination_iban")
            else:
                name, iban = t.get("source_name"), t.get("source_iban")
            cat = t.get("category_name")
            n = _name(name)
            iban = (iban or "").replace(" ", "").upper()
            if iban:
                iban_names.setdefault(iban, set()).add(n)
            if not cat:
                continue
            if n:
                self.by_name.setdefault(n, set()).add(cat)
            if iban:
                self.by_iban.setdefault(iban, set()).add(cat)
        # IBANs, hinter denen verschiedene Gegenparteien stehen, sind nicht aussagekräftig
        for iban, names in iban_names.items():
            if len(names) > 1:
                self.by_iban.pop(iban, None)

    def suggest(self, name: str, iban: str):
        for cats in (self.by_name.get(_name(name)), self.by_iban.get(iban or "")):
            if cats and len(cats) == 1:
                return next(iter(cats))
        return None


@dataclass
class PlannedRow:
    booking: Booking
    kind: str            # withdrawal | deposit | transfer
    status: str          # neu | duplikat | vorgemerkt
    other: str           # Anzeige: Gegenpartei bzw. eigenes Konto
    payload: dict = field(default_factory=dict)
    category: str = ""   # Vorschlag aus der Historie


@dataclass
class Plan:
    account_id: str
    account_name: str
    statement: Statement
    rows: list

    @property
    def new_rows(self):
        return [r for r in self.rows if r.status == "neu"]


def build_plan(ff: Firefly, st: Statement, tag: str = "Bank-Import") -> Plan:
    if not st.bookings:
        raise FireflyError("Die Datei enthält keine Buchungen.")
    own = ff.asset_accounts()
    if not st.account_iban:
        raise FireflyError("In der Datei steht keine IBAN des eigenen Kontos.")
    if st.account_iban not in own:
        raise FireflyError(getattr(ff, "MISSING_ACCOUNT",
            "Zur IBAN {iban} gibt es in Firefly kein Bestandskonto. "
            "Lege das Konto in Firefly an und trage dort die IBAN ein.").format(iban=st.account_iban))
    acc_id, acc_name = own[st.account_iban]

    start = min(b.date for b in st.bookings) - timedelta(days=1)
    end = max(b.date for b in st.bookings) + timedelta(days=1)
    history = ff.account_transactions(acc_id, end - timedelta(days=HISTORY_DAYS), end)
    learner = CategoryLearner(history)
    existing = Counter()
    existing_ext = set()
    for t in history:
        if not (start.isoformat() <= t["date"][:10] <= end.isoformat()):
            continue
        existing[_key_from_ff(t)] += 1
        if t.get("external_id"):
            existing_ext.add(t["external_id"])

    rows = []
    for b in st.bookings:
        ext = external_id(st, b)
        key = _key(b.date, b.amount, b.description or b.counterparty)
        out = b.amount < 0
        other_acc = own.get(b.iban) if b.iban and b.iban != st.account_iban else None

        if other_acc:
            kind = "transfer"
            other = other_acc[1]
        else:
            kind = "withdrawal" if out else "deposit"
            other = b.counterparty or "(unbekannt)"

        if not b.booked:
            status = "vorgemerkt"
        elif ext in existing_ext or existing[key] > 0:
            status = "duplikat"
            if existing[key] > 0:
                existing[key] -= 1
        else:
            status = "neu"

        cat = ""
        if kind != "transfer" and status == "neu":
            cat = learner.suggest(b.counterparty, b.iban) or ""
        rows.append(PlannedRow(b, kind, status, other,
                               _payload(b, kind, acc_id, other_acc, ext, tag), cat))
    return Plan(acc_id, acc_name, st, rows)


def _key_from_ff(t):
    from datetime import date
    d = date.fromisoformat(t["date"][:10])
    return _key(d, t["amount"], t.get("description") or "")


def _payload(b: Booking, kind, acc_id, other_acc, ext, tag):
    desc = _short(b.description) or b.counterparty or "(ohne Verwendungszweck)"
    split = {
        "type": kind,
        "date": b.date.isoformat(),
        "amount": f"{abs(b.amount):.2f}",
        "description": desc,
        "external_id": ext,
        "tags": [tag],
        # Zusatzinfos für Ziele ohne Firefly-Kontenmodell (Sure); Firefly bekommt sie nicht
        "x_account_id": acc_id,
        "x_direction": "out" if b.amount < 0 else "in",
        "x_counterparty": b.counterparty or "",
    }
    if desc != b.description and b.description:
        split["notes"] = b.description
    for k, v in (("sepa_ci", b.creditor_id), ("sepa_db", b.mandate), ("sepa_ct_id", b.reference)):
        if v:
            split[k] = v

    name = b.counterparty or "(unbekannt)"
    if kind == "transfer":
        if b.amount < 0:
            split["source_id"], split["destination_id"] = acc_id, other_acc[0]
        else:
            split["source_id"], split["destination_id"] = other_acc[0], acc_id
    elif kind == "withdrawal":
        split["source_id"] = acc_id
        split["destination_name"] = name
        if b.iban:
            split["destination_iban"] = b.iban
    else:
        split["destination_id"] = acc_id
        split["source_name"] = name
        if b.iban:
            split["source_iban"] = b.iban

    return {
        "error_if_duplicate_hash": False,
        "apply_rules": True,
        "fire_webhooks": True,
        "transactions": [split],
    }


def run_import(ff: Firefly, plan: Plan, categories=None):
    """categories: {Zeilenindex in plan.rows: Kategoriename oder ""}; None = Vorschläge übernehmen."""
    results = []
    for i, r in enumerate(plan.rows):
        if r.status != "neu":
            continue
        cat = r.category if categories is None else categories.get(i, "")
        split = r.payload["transactions"][0]
        if cat and r.kind != "transfer":
            split["category_name"] = cat
        else:
            split.pop("category_name", None)
        ok, info = ff.create(r.payload)
        if not ok and "iban" in str(info).lower():
            # Firefly lehnt manche Gegen-IBANs ab (Format, Kontotyp) – dann ohne IBAN anlegen
            split = r.payload["transactions"][0]
            split.pop("destination_iban", None)
            split.pop("source_iban", None)
            ok, info = ff.create(r.payload)
        results.append((r, ok, info))
    return results
