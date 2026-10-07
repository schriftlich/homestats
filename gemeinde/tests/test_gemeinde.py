import io
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from gemeinde.fmt import age_on, next_birthday, parse_date


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMEINDE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GEMEINDE_DEMO", raising=False)
    from gemeinde.main import app

    with TestClient(app) as c:
        yield c


def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def add_family(client):
    r = client.post("/haushalt", data={"name": "Müller", "street": "Hauptstr. 5", "zip": "73430",
                                       "city": "Aalen", "phone": "07361 1234"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/person/neu?haushalt=1"
    client.post("/person", data={"household_id": 1, "first_name": "Peter", "birthday": "1980-05-01",
                                 "mobile": "0151 111", "member": "1", "consent": "1"})
    client.post("/person", data={"household_id": 1, "first_name": "Anna", "birthday": "03.02.1982",
                                 "email": "anna@example.org", "member": "1", "consent": "1"})
    client.post("/person", data={"household_id": 1, "first_name": "Lena", "child": "1",
                                 "birthday": "2015-07-09", "consent": "1"})
    client.post("/person", data={"household_id": 1, "first_name": "Tim", "child": "1"})  # keine Einwilligung


def test_flow_and_pdf(client):
    add_family(client)
    r = client.get("/haushalt/1")
    assert "Peter Müller" in r.text and "keine Einwilligung" in r.text and "Kind" in r.text

    r = client.get("/liste")
    assert "1 Haushalte" in r.text and "3 Personen" in r.text and "Tim Müller" in r.text

    r = client.get("/liste.pdf?email=1&geburtstage=1")
    assert r.headers["content-type"] == "application/pdf"
    text = pdf_text(r.content)
    assert "Müller" in text and "Hauptstr. 5, 73430 Aalen" in text and "Tel. 07361 1234" in text
    assert "01.05.1980" in text and "anna@example.org" in text and "Lena Müller" in text
    assert "Tim" not in text  # ohne Einwilligung
    assert "Geburtstage" in text and "Februar" in text

    text = pdf_text(client.get("/liste.pdf").content)
    assert "anna@example.org" not in text and "Februar" not in text


def test_consent_all_and_left(client):
    add_family(client)
    client.post("/haushalt/1/einwilligung")
    assert "Tim Müller" in pdf_text(client.get("/liste.pdf").content)
    # Austritt: bleibt gespeichert, fehlt aber in Liste
    client.post("/person", data={"id": 1, "household_id": 1, "first_name": "Peter",
                                 "consent": "1", "left_on": "2026-01-01"})
    text = pdf_text(client.get("/liste.pdf").content)
    assert "Peter" not in text and "Anna" in text


def test_validation(client):
    r = client.post("/haushalt", data={"name": "  "})
    assert "Familienname fehlt" in r.text
    client.post("/haushalt", data={"name": "X"})
    r = client.post("/person", data={"household_id": 1, "first_name": "A", "birthday": "31.02.1990"})
    assert "ungültiges Datum" in r.text
    r = client.post("/person", data={"household_id": 1, "first_name": "A", "email": "kaputt"})
    assert "E-Mail" in r.text


def test_backup_roundtrip(client):
    add_family(client)
    client.post("/einstellungen", data={"church_name": "Gemeinde Musterstadt", "pdf_note": "Vertraulich"})
    data = client.get("/daten/export").content
    assert "persons.csv" in zipfile.ZipFile(io.BytesIO(data)).namelist()
    client.post("/haushalt/1/loeschen")
    assert "Müller" not in client.get("/").text
    r = client.post("/daten/import", files={"file": ("b.zip", data)}, data={"confirm": "1"})
    assert "1 Haushalte, 4 Personen" in r.text
    assert "Gemeinde Musterstadt" in pdf_text(client.get("/liste.pdf").content)


def test_search_and_overview(client):
    add_family(client)
    client.post("/haushalt", data={"name": "Schmidt", "city": "Ulm"})
    r = client.get("/?q=lena")
    assert "Müller" in r.text and "Schmidt" not in r.text
    r = client.get("/geburtstage")
    assert "wird" in r.text


def test_cross_site_post_blocked(client):
    r = client.post("/haushalt", data={"name": "X"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_dates():
    assert parse_date("1.2.85") == date(1985, 2, 1)
    assert next_birthday(date(2000, 2, 29), date(2027, 2, 1)) == date(2027, 2, 28)
    assert next_birthday(date(2000, 5, 1), date(2026, 5, 2)) == date(2027, 5, 1)
    assert age_on(date(2000, 5, 1), date(2026, 5, 1)) == 26


def _png(w=300, h=100) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (180, 140, 100, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_logo_upload_pdf_and_backup(client):
    add_family(client)
    r = client.post("/einstellungen/logo", files={"file": ("x.txt", b"kein Bild")})
    assert "kein lesbares Bild" in r.text
    r = client.post("/einstellungen/logo", files={"file": ("logo.png", _png())})
    assert "Logo gespeichert" in r.text
    assert client.get("/logo.png").headers["content-type"] == "image/png"
    reader = PdfReader(io.BytesIO(client.get("/liste.pdf?geburtstage=1").content))
    assert len(reader.pages[0].images) == 1
    assert all(len(p.images) == 0 for p in reader.pages[1:])

    data = client.get("/daten/export").content
    assert "logo.png" in zipfile.ZipFile(io.BytesIO(data)).namelist()
    client.post("/einstellungen/logo/loeschen")
    assert client.get("/logo.png").status_code == 404
    client.post("/daten/import", files={"file": ("b.zip", data)}, data={"confirm": "1"})
    assert client.get("/logo.png").status_code == 200
