from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMESTATS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HOMESTATS_DEMO", raising=False)
    from homestats.main import app

    with TestClient(app) as c:
        yield c


def test_full_flow(client):
    r = client.post("/zaehler", data={"medium": "strom", "name": "Haus", "unit": "kWh"})
    assert r.status_code == 200 and "Haus" in r.text

    r = client.post("/ablesung", data={"meter_id": 1, "day": "2024-01-01", "value": "1.000,5"})
    assert "Gespeichert" in r.text and "1.000,500" in r.text
    client.post("/ablesung", data={"meter_id": 1, "day": "2024-02-01", "value": "1310,5"})
    # Stand kleiner als vorher -> Warnung, aber gespeichert
    r = client.post("/ablesung", data={"meter_id": 1, "day": "2024-03-01", "value": "900"})
    assert "Gespeichert" in r.text and "kleiner als der vorherige" in r.text

    client.post("/tarife/strom/tarif", data={"valid_from": "2023-01-01", "base_month": "12", "price": "0,30"})
    client.post("/tarife/strom/abschlag", data={"valid_from": "2023-01-01", "amount": "80"})
    r = client.get("/?m=strom")
    assert r.status_code == 200 and "Hochrechnung" in r.text
    assert "105,00 €" in r.text  # Jan 2024: 310 kWh × 0,30 + 12 € Grundpreis


def test_invalid_number_shows_error(client):
    client.post("/zaehler", data={"medium": "wasser"})
    r = client.post("/ablesung", data={"meter_id": 1, "day": date.today().isoformat(), "value": "abc"})
    assert "ungültige Zahl" in r.text


def test_cross_site_post_rejected(client):
    r = client.post("/zaehler", data={"medium": "gas"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_backup_roundtrip(client, tmp_path, monkeypatch):
    from homestats import db, demo

    conn = db.connect()
    demo.seed(conn)
    before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in db.TABLES}
    conn.close()
    dash_before = client.get("/?m=gas").text

    zipped = client.get("/daten/export").content
    r = client.post("/daten/import", files={"file": ("b.zip", zipped)}, data={"confirm": "1"})
    assert "Import erfolgreich" in r.text

    conn = db.connect()
    after = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in db.TABLES}
    for t in db.TABLES:
        assert [tuple(x) for x in after[t]] == [tuple(x) for x in before[t]], t
    assert client.get("/?m=gas").text == dash_before


def test_import_rejects_garbage(client):
    r = client.post("/daten/import", files={"file": ("x.zip", b"nope")}, data={"confirm": "1"})
    assert "kein gültiges ZIP" in r.text
