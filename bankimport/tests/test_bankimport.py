from decimal import Decimal

import pytest

from bankimport import banks, firefly

LONG = "Abrechnung Zinsen für geduldete Überziehung " * 40

CSV = f'''"Girokonto";"DE00111100000000000001"

"Kontostand vom 03.10.2026:";"1.721,97 €"
""
"Buchungsdatum";"Wertstellung";"Status";"Zahlungspflichtige*r";"Zahlungsempfänger*in";"Verwendungszweck";"Umsatztyp";"IBAN";"Betrag (€)";"Gläubiger-ID";"Mandatsreferenz";"Kundenreferenz"
"05.10.26";"05.10.26";"Gebucht";"Max Muster";"Max Muster";"Sparen";"Ausgang";"NL00BANK0000000002";"-380";"";"";""
"02.10.26";"02.10.26";"Gebucht";"FIRMA GBR";"MUSTER MAX";"LOHN / GEHALT         09/26";"Eingang";"DE70602500100015093552";"3.283,3";"";"";"REF1"
"02.10.26";"02.10.26";"Gebucht";"MUSTER, MAX";"ENERGIE GMBH";"ABSCHLAG 10/26";"Ausgang";"DE63600501010002011963";"-116";"DE20ZZZ00000193512";"M-1";"R2"
"30.09.26";"30.09.26";"Gebucht";"MUSTER, MAX";"";"{LONG}";"Ausgang";"BADIBAN";"-1,53";"";"";""
"06.10.26";"06.10.26";"Vorgemerkt";"MUSTER, MAX";"Shop";"Kauf";"Ausgang";"";"-9,99";"";"";""
'''.encode()


class FakeFirefly:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created = []

    def asset_accounts(self):
        return {"DE00111100000000000001": ("1", "DKB Girokonto"),
                "NL00BANK0000000002": ("2", "Sparkonto")}

    def account_transactions(self, account_id, start, end):
        return self.existing

    def create(self, payload):
        split = payload["transactions"][0]
        if split.get("destination_iban") == "BADIBAN":
            return False, "422: transactions.0.destination_iban: IBAN ungültig"
        self.created.append(split)
        return True, str(len(self.created))


def test_parse_dkb():
    st = banks.parse(CSV)
    assert st.account_iban == "DE00111100000000000001"
    assert len(st.bookings) == 5
    salary = st.bookings[1]
    assert salary.amount == Decimal("3283.3")
    assert salary.counterparty == "FIRMA GBR"          # Eingang -> Zahler
    assert st.bookings[2].counterparty == "ENERGIE GMBH"  # Ausgang -> Empfänger
    assert salary.description == "LOHN / GEHALT 09/26"
    assert st.bookings[4].booked is False


def test_unknown_format():
    with pytest.raises(banks.ParseError):
        banks.parse(b"a;b;c\n1;2;3\n")


def test_plan_and_import():
    ff = FakeFirefly(existing=[{"date": "2026-10-02T00:00:00+02:00", "amount": "3283.300000",
                                "description": "LOHN / GEHALT 09/26", "external_id": None}])
    plan = firefly.build_plan(ff, banks.parse(CSV))
    status = [r.status for r in plan.rows]
    assert status == ["neu", "duplikat", "neu", "neu", "vorgemerkt"]
    assert plan.rows[0].kind == "transfer"
    assert plan.rows[0].payload["transactions"][0]["destination_id"] == "2"

    results = firefly.run_import(ff, plan)
    assert all(ok for _, ok, _ in results) and len(ff.created) == 3
    fee = ff.created[2]
    assert len(fee["description"]) <= firefly.MAX_DESC + 2
    assert fee["notes"].startswith("Abrechnung")
    assert "destination_iban" not in fee  # nach IBAN-Fehler ohne IBAN wiederholt
    assert fee["destination_name"] == "(unbekannt)"


def test_unknown_account():
    class NoAcc(FakeFirefly):
        def asset_accounts(self):
            return {}
    with pytest.raises(firefly.FireflyError, match="kein Bestandskonto"):
        firefly.build_plan(NoAcc(), banks.parse(CSV))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BANKIMPORT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    from bankimport import app as appmod
    fake = FakeFirefly()
    monkeypatch.setattr(appmod, "Firefly", lambda url, token: fake)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        c.fake = fake
        yield c


def test_web_flow(client):
    assert "Einrichtung" in client.get("/").get_data(as_text=True)
    r = client.post("/einstellungen", data={"firefly_token": "abc", "firefly_url": "", "action": "save"})
    html = r.get_data(as_text=True)
    assert "Verbindung zu Firefly funktioniert" in html and "Sparkonto" in html

    import io
    r = client.post("/vorschau", data={"file": (io.BytesIO(CSV), "dkb.csv")},
                    content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert "4 neu" in html and "1 vorgemerkt" in html
    pid = html.split("/import/")[1].split('"')[0]
    html = client.post(f"/import/{pid}").get_data(as_text=True)
    assert "4 importiert" in html
    assert client.get("/icon.svg").status_code == 200
    assert client.get("/healthz").get_json() == {"ok": True}


def test_cross_site_post_rejected(client):
    r = client.post("/einstellungen", data={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
