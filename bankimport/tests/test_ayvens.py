"""Ayvens-Bank-Export (Tagesgeld): Format ohne eigene IBAN, Unterkonten werden übersprungen."""
import io
import json
from decimal import Decimal

import pytest

from bankimport import banks, firefly, sure

DKB = "11111111-1111-1111-1111-111111111111"
AYV = "33333333-3333-3333-3333-333333333333"
DKB_IBAN = "DE00111100000000000001"

CSV = f'''﻿Datum,Referenzkonto,Beschreibung,Sort,Betrag
06-10-2026,{DKB_IBAN},Sparen,dazu,"380,00"
01-10-2026,,Zinsen,dazu,"0,55"
10-09-2026,{DKB_IBAN},Keine Beschreibung verfugbar,Ab,"-673,00"
19-07-2026,8912773291,Keine Beschreibung verfugbar,dazu,"1400,00"
02-07-2026,8912773321,Huettenwanderung,Ab,"-375,00"
24-06-2026,DE99999900000000000099,,dazu,"1,00"
'''.encode("utf-8")


def test_parse_ayvens():
    st = banks.parse(CSV)
    assert st.bank == "Ayvens Bank" and st.account_iban == ""
    sparen, zinsen, raus, topf_rein, topf_raus, fremd = st.bookings
    assert sparen.amount == Decimal("380.00") and sparen.iban == DKB_IBAN and sparen.description == "Sparen"
    assert zinsen.counterparty == "Ayvens Bank" and zinsen.description == "Zinsen"
    assert raus.amount == Decimal("-673.00") and raus.description == ""   # Platzhaltertext entfernt
    assert topf_rein.skip and topf_raus.skip and not sparen.skip
    assert topf_raus.date.isoformat() == "2026-07-02"


def test_dkb_still_detected():
    from test_bankimport import CSV as DKB_CSV
    assert banks.parse(DKB_CSV).bank == "DKB"


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.ok = 200 <= status < 300
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeSure:
    def __init__(self):
        self.created = []
        self.cats = [{"id": "c1", "name": "Einkommen"}]
        self.history = [  # Zinsen aus dem Vormonat, schon kategorisiert
            {"date": "2026-09-01", "amount_cents": 102, "classification": "income", "name": "Ayvens Bank",
             "notes": "Zinsen", "category": {"name": "Einkommen"}, "external_id": None, "source": None},
        ]

    def handle(self, method, url, params=None, json=None):
        path = url.split("/api/v1")[1]
        page = {"pagination": {"page": 1, "total_pages": 1}}
        if method == "GET" and path == "/accounts":
            return FakeResp(200, {"accounts": [{"id": DKB, "name": "DKB Giro", "account_type": "depository"},
                                               {"id": AYV, "name": "Ayvens Tagesgeld", "account_type": "depository"}],
                                  **page})
        if method == "GET" and path == "/transactions":
            assert params["account_id"] == AYV
            mine = [dict(t, date=t["date"], amount_cents=int(float(t["amount"]) * 100),
                         classification="expense" if t["nature"] == "expense" else "income",
                         category=None) for t in self.created]
            return FakeResp(200, {"transactions": self.history + mine, **page})
        if method == "GET" and path == "/categories":
            return FakeResp(200, {"categories": self.cats, **page})
        if method == "POST" and path == "/transactions":
            t = json["transaction"]
            if any(c["external_id"] == t["external_id"] for c in self.created):
                return FakeResp(200, {"id": "existing"})
            self.created.append(t)
            return FakeResp(201, {"id": str(len(self.created))})
        raise AssertionError(f"unerwartet: {method} {path}")


@pytest.fixture
def server(monkeypatch):
    srv = FakeSure()

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            return srv.handle("GET", url, params=params)

        def post(self, url, json=None, timeout=None):
            return srv.handle("POST", url, json=json)

    monkeypatch.setattr(sure.requests, "Session", FakeSession)
    return srv


def test_plan_and_import(server):
    client = sure.Sure("http://sure", "key", {DKB_IBAN: DKB})
    plan = firefly.build_plan(client, banks.parse(CSV), account=(AYV, "Ayvens Tagesgeld"))
    assert [r.status for r in plan.rows] == ["neu", "neu", "neu", "übersprungen", "übersprungen", "neu"]
    assert [r.kind for r in plan.rows][:3] == ["transfer", "deposit", "transfer"]
    assert plan.rows[0].other == "DKB Giro"
    assert plan.rows[1].category == "Einkommen"          # Zinsen gelernt

    results = firefly.run_import(client, plan)
    assert all(ok for _, ok, _ in results) and len(server.created) == 4
    sparen, zinsen, raus, fremd = server.created
    assert sparen["account_id"] == AYV and sparen["nature"] == "income" and sparen["name"] == "DKB Giro"
    assert zinsen["category_id"] == "c1"
    assert raus["nature"] == "expense" and raus["amount"] == "673.00" and raus["name"] == "DKB Giro"
    assert fremd["nature"] == "income" and fremd["name"] == "(unbekannt)"

    plan2 = firefly.build_plan(client, banks.parse(CSV), account=(AYV, "Ayvens Tagesgeld"))
    assert "neu" not in [r.status for r in plan2.rows]  # erneuter Import: nichts doppelt


def test_without_account_needs_choice(server):
    client = sure.Sure("http://sure", "key", {})
    with pytest.raises(firefly.FireflyError, match="Zielkonto"):
        firefly.build_plan(client, banks.parse(CSV))


def test_web_account_choice_remembered(server, tmp_path, monkeypatch):
    monkeypatch.setenv("BANKIMPORT_DATA_DIR", str(tmp_path))
    from bankimport import app as appmod, settings
    settings.save(target="sure", sure_key="key", sure_accounts={DKB_IBAN: DKB})
    c = appmod.app.test_client()

    html = c.post("/vorschau", data={"file": (io.BytesIO(CSV), "ayvens.csv")},
                  content_type="multipart/form-data").get_data(as_text=True)
    assert "Zielkonto für Ayvens Bank" in html and "Ayvens Tagesgeld" in html
    pid = html.split("/konto/")[1].split('"')[0]

    html = c.post(f"/konto/{pid}", data={"account": AYV}).get_data(as_text=True)
    assert "Vorschau – Ayvens Tagesgeld" in html and "2 Unterkonto-Bewegungen" in html
    assert settings.load()["bank_accounts"] == {"Ayvens Bank": AYV}

    # zweiter Upload: Konto ist gemerkt, direkt zur Vorschau
    html = c.post("/vorschau", data={"file": (io.BytesIO(CSV), "ayvens.csv")},
                  content_type="multipart/form-data").get_data(as_text=True)
    assert "Vorschau – Ayvens Tagesgeld" in html and "anderes Konto wählen" in html
    pid = html.split("/import/")[1].split('"')[0]
    html = c.post(f"/import/{pid}", data={"cat_1": "Einkommen"}).get_data(as_text=True)
    assert "4 importiert" in html
