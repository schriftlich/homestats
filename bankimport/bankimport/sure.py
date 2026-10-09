"""Anbindung an Sure (Fork von Maybe Finance) über dessen REST-Schnittstelle /api/v1.

Die Klasse bietet dieselben Methoden wie `Firefly`, damit Planung, Duplikat-
erkennung und Kategorie-Lernen unverändert für beide Ziele funktionieren.
Sure kennt keine IBAN an Konten – die Zuordnung IBAN -> Sure-Konto wird in den
Einstellungen von Bank-Import gepflegt.
"""
import requests

from .firefly import FireflyError

SOURCE = "bankimport"

CATEGORY_STYLE = {  # Name -> (Farbe, Lucide-Icon aus Sures erlaubter Liste)
    "Wohnen": ("#6471eb", "house"),
    "Energie": ("#f5a524", "zap"),
    "Lebensmittel": ("#2fb171", "shopping-cart"),
    "Haushalt": ("#8b6f47", "shopping-basket"),
    "Mobilität": ("#3b82f6", "car"),
    "Kommunikation": ("#06b6d4", "smartphone"),
    "Versicherungen": ("#64748b", "shield"),
    "Kinder": ("#ec4899", "baby"),
    "Gesundheit": ("#ef4444", "heart-pulse"),
    "Gemeinde & Spenden": ("#a855f7", "hand-heart"),
    "Freizeit & Urlaub": ("#14b8a6", "tree-palm"),
    "Fitness": ("#84cc16", "dumbbell"),
    "Business & Projekte": ("#475569", "briefcase"),
    "Einkommen": ("#16a34a", "wallet"),
}


class Sure:
    MISSING_ACCOUNT = ("Die IBAN {iban} ist noch keinem Sure-Konto zugeordnet. "
                       "Trage sie unter Einstellungen beim passenden Konto ein.")
    def __init__(self, url: str, api_key: str, account_map: dict, timeout: int = 30):
        self.url = url.rstrip("/")
        self.account_map = {k.replace(" ", "").upper(): v for k, v in (account_map or {}).items() if v}
        self.s = requests.Session()
        self.s.headers.update({"X-Api-Key": api_key, "Accept": "application/json",
                               "Content-Type": "application/json"})
        self.timeout = timeout
        self._cats = None

    # --- Grundlagen -------------------------------------------------------------
    def _get(self, path, **params):
        r = self.s.get(f"{self.url}/api/v1{path}", params=params, timeout=self.timeout)
        if r.status_code == 401:
            raise FireflyError("Sure lehnt den API-Schlüssel ab (401). Ist er korrekt und hat er Lese- und Schreibrechte?")
        if not r.ok:
            raise FireflyError(f"Sure-Fehler {r.status_code} bei {path}: {r.text[:300]}")
        return r.json()

    def _get_all(self, path, key, **params):
        page, out = 1, []
        while True:
            data = self._get(path, page=page, per_page=100, **params)
            out.extend(data.get(key, []))
            pages = int(data.get("pagination", {}).get("total_pages", 1) or 1)
            if page >= pages:
                return out
            page += 1

    def accounts(self):
        """Alle Sure-Konten als Liste von (id, name, typ)."""
        return [(a["id"], a["name"], a.get("account_type") or "")
                for a in self._get_all("/accounts", "accounts")]

    # --- gleiche Schnittstelle wie Firefly --------------------------------------
    def all_accounts(self):
        return [(i, n) for i, n, _ in self.accounts()]

    def asset_accounts(self):
        names = {i: n for i, n, _ in self.accounts()}
        return {iban: (aid, names.get(aid, "?")) for iban, aid in self.account_map.items() if aid in names}

    def account_transactions(self, account_id, start, end):
        out = []
        for t in self._get_all("/transactions", "transactions", account_id=account_id,
                               start_date=start.isoformat(), end_date=end.isoformat()):
            income = t.get("classification") == "income"
            name = t.get("name") or ""
            out.append({
                "type": "deposit" if income else "withdrawal",
                "date": str(t["date"]),
                "amount": f"{int(t.get('amount_cents') or 0) / 100:.2f}",
                "description": t.get("notes") or name,
                "external_id": t.get("external_id") if t.get("source") == SOURCE else None,
                "source_name" if income else "destination_name": name,
                "category_name": (t.get("category") or {}).get("name"),
            })
        return out

    def _category_ids(self):
        if self._cats is None:
            self._cats = {c["name"]: c["id"] for c in self._get_all("/categories", "categories")}
        return self._cats

    def categories(self):
        return sorted(self._category_ids(), key=str.lower)

    def create_category(self, name):
        color, icon = CATEGORY_STYLE.get(name, ("#6b7280", "tag"))
        r = self.s.post(f"{self.url}/api/v1/categories",
                        json={"category": {"name": name, "color": color, "icon": icon}},
                        timeout=self.timeout)
        if not r.ok:
            raise FireflyError(f"Kategorie „{name}“ konnte nicht angelegt werden: {r.status_code} {r.text[:200]}")
        self._cats = None

    def create(self, payload):
        split = payload["transactions"][0]
        body = {
            "account_id": split["x_account_id"],
            "date": split["date"],
            "amount": split["amount"],
            "name": split.get("x_counterparty") or split["description"],
            "notes": split.get("notes") or split["description"],
            "currency": "EUR",
            "nature": "expense" if split["x_direction"] == "out" else "income",
            "external_id": split["external_id"],
            "source": SOURCE,
        }
        cat = split.get("category_name")
        if cat:
            cid = self._category_ids().get(cat)
            if cid:
                body["category_id"] = cid
        r = self.s.post(f"{self.url}/api/v1/transactions", json={"transaction": body}, timeout=self.timeout)
        if r.status_code == 201:
            return True, r.json().get("id", "")
        if r.status_code == 200:
            return True, "schon vorhanden"
        try:
            js = r.json()
            msg = "; ".join(js.get("errors") or []) or js.get("message") or r.text
        except ValueError:
            msg = r.text
        return False, f"{r.status_code}: {msg[:300]}"
