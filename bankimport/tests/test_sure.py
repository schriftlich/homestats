"""Bank-Import gegen ein nachgebautes Sure (HTTP-Ebene simuliert)."""
import io
import json

import pytest

from bankimport import banks, firefly, sure
from test_bankimport import CSV

ACC = "11111111-1111-1111-1111-111111111111"
SAV = "22222222-2222-2222-2222-222222222222"


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.ok = 200 <= status < 300
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeSureServer:
    def __init__(self):
        self.created = []
        self.cats = [{"id": "c1", "name": "Energie"}, {"id": "c2", "name": "Einkommen"}]
        self.history = [
            {"date": "2026-09-02", "amount_cents": 11600, "classification": "expense",
             "name": "ENERGIE GMBH", "notes": "ABSCHLAG 09/26", "category": {"name": "Energie"},
             "external_id": None, "source": None},
            {"date": "2026-10-02", "amount_cents": 328330, "classification": "income",
             "name": "FIRMA GBR", "notes": "LOHN / GEHALT 09/26", "category": {"name": "Einkommen"},
             "external_id": None, "source": None},
        ]

    def handle(self, method, url, params=None, json=None, headers=None):
        assert headers["X-Api-Key"] == "key"
        path = url.split("/api/v1")[1]
        page = {"pagination": {"page": 1, "total_pages": 1}}
        if method == "GET" and path == "/accounts":
            return FakeResp(200, {"accounts": [{"id": ACC, "name": "DKB Giro", "account_type": "depository"},
                                               {"id": SAV, "name": "Tagesgeld", "account_type": "depository"}], **page})
        if method == "GET" and path == "/transactions":
            assert params["account_id"] == ACC and params["start_date"] <= "2026-09-02"
            return FakeResp(200, {"transactions": self.history, **page})
        if method == "GET" and path == "/categories":
            return FakeResp(200, {"categories": self.cats, **page})
        if method == "POST" and path == "/categories":
            c = json["category"]
            assert c["color"].startswith("#") and c["icon"]
            self.cats.append({"id": f"c{len(self.cats) + 1}", "name": c["name"]})
            return FakeResp(201, {"id": "x"})
        if method == "POST" and path == "/transactions":
            t = json["transaction"]
            if any(c["external_id"] == t["external_id"] for c in self.created):
                return FakeResp(200, {"id": "existing"})
            self.created.append(t)
            return FakeResp(201, {"id": str(len(self.created))})
        raise AssertionError(f"unerwartet: {method} {path}")


@pytest.fixture
def server(monkeypatch):
    srv = FakeSureServer()

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            return srv.handle("GET", url, params=params, headers=self.headers)

        def post(self, url, json=None, timeout=None):
            return srv.handle("POST", url, json=json, headers=self.headers)

    monkeypatch.setattr(sure.requests, "Session", FakeSession)
    return srv


def test_sure_plan_and_import(server):
    client = sure.Sure("http://sure", "key", {"DE00 1111 0000 0000 0000 01": ACC, "NL00BANK0000000002": SAV})
    plan = firefly.build_plan(client, banks.parse(CSV))
    assert plan.account_name == "DKB Giro"
    status = [r.status for r in plan.rows]
    assert status == ["neu", "duplikat", "neu", "neu", "vorgemerkt"]   # Gehalt schon in Sure
    assert plan.rows[0].kind == "transfer"
    assert plan.rows[2].category == "Energie"                           # gelernt

    results = firefly.run_import(client, plan)
    assert all(ok for _, ok, _ in results) and len(server.created) == 3
    sparen, energie, fee = server.created
    assert sparen["account_id"] == ACC and sparen["nature"] == "expense" and sparen["amount"] == "380.00"
    assert energie["name"] == "ENERGIE GMBH" and energie["notes"] == "ABSCHLAG 10/26"
    assert energie["category_id"] == "c1" and energie["source"] == "bankimport"
    assert fee["name"] == "(unbekannt)" or fee["name"].startswith("Abrechnung")
    assert len(fee["notes"]) > 1000                                     # voller Text in der Notiz

    # erneuter Import derselben Datei: Sure erkennt externe IDs (idempotent)
    plan2 = firefly.build_plan(client, banks.parse(CSV))
    results2 = firefly.run_import(client, plan2)
    assert all(ok for _, ok, _ in results2) and len(server.created) == 3


def test_sure_unmapped_iban(server):
    client = sure.Sure("http://sure", "key", {})
    with pytest.raises(firefly.FireflyError, match="keinem Sure-Konto"):
        firefly.build_plan(client, banks.parse(CSV))


def test_sure_web(server, tmp_path, monkeypatch):
    monkeypatch.setenv("BANKIMPORT_DATA_DIR", str(tmp_path))
    from bankimport import app as appmod
    c = appmod.app.test_client()
    html = c.post("/einstellungen", data={"target": "sure", "sure_key": "key", "action": "save"}).get_data(as_text=True)
    assert "DKB Giro" in html and f'name="iban_{ACC}"' in html
    html = c.post("/einstellungen", data={"target": "sure", f"iban_{ACC}": "DE00111100000000000001",
                                          f"iban_{SAV}": "", "action": "test"}).get_data(as_text=True)
    assert "Verbindung zu Sure funktioniert" in html and "DE00111100000000000001" in html

    html = c.post("/einstellungen/kategorien").get_data(as_text=True)
    assert "12 Kategorien angelegt" in html

    html = c.post("/vorschau", data={"file": (io.BytesIO(CSV), "dkb.csv")},
                  content_type="multipart/form-data").get_data(as_text=True)
    assert "DKB Giro" in html and "gelernt" in html
    pid = html.split("/import/")[1].split('"')[0]
    html = c.post(f"/import/{pid}", data={"cat_2": "Wohnen"}).get_data(as_text=True)
    assert "3 importiert" in html and "Sure öffnen" in html
    assert server.created[1]["category_id"] == next(x["id"] for x in server.cats if x["name"] == "Wohnen")
