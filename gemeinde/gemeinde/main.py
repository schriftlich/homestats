from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json

from . import __version__, backup, db, demo, fmt, geo, logo, pdf
from .db import Household, Person

log = logging.getLogger("gemeinde")
BASE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.migrate(conn)
    if os.environ.get("GEMEINDE_DEMO") == "1" and db.is_empty(conn):
        log.info("Leere Datenbank – lege Beispieldaten an (GEMEINDE_DEMO=1)")
        demo.seed(conn)
    conn.close()
    yield


app = FastAPI(title="Gemeindeverzeichnis", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters.update(ddate=fmt.ddate)
templates.env.globals.update(version=__version__, MONTHS_LONG=fmt.MONTHS_LONG, WEEKDAYS=fmt.WEEKDAYS)


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Einfacher CSRF-Schutz: schreibende Requests nur von der eigenen Seite."""
    if request.method == "POST":
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            return Response("Ungültige Herkunft der Anfrage.", status_code=403)
    return await call_next(request)


def redirect(path: str, **params) -> RedirectResponse:
    q = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return RedirectResponse(f"{path}?{q}" if q else path, status_code=303)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"today": date.today(), **ctx})


class FormError(ValueError):
    pass


def opt_date(v: str | None, label: str) -> date | None:
    try:
        d = fmt.parse_date(v)
    except ValueError:
        raise FormError(f"{label}: ungültiges Datum (TT.MM.JJJJ)")
    if d and d > date.today() + timedelta(days=1) and label == "Geburtstag":
        raise FormError("Geburtstag liegt in der Zukunft")
    return d


def clean(v: str | None) -> str:
    return " ".join((v or "").split())


# --- Auswertungen ------------------------------------------------------------

def upcoming(households: list[Household], today: date, days: int) -> list[dict]:
    out = []
    for h in households:
        for p in h.active_persons:
            if not p.birthday:
                continue
            nxt = fmt.next_birthday(p.birthday, today)
            if (nxt - today).days < days:
                out.append({
                    "date": nxt, "in_days": (nxt - today).days, "name": h.full_name(p),
                    "age": fmt.age_on(p.birthday, nxt), "household": h, "person": p,
                })
    return sorted(out, key=lambda e: (e["date"], e["name"]))


def stats(households: list[Household]) -> dict:
    active = [p for h in households for p in h.active_persons]
    return {
        "households": sum(1 for h in households if h.active_persons),
        "persons": len(active),
        "members": sum(p.member for p in active),
        "children": sum(p.child for p in active),
        "no_consent": sum(not p.consent for p in active),
    }


def matches(h: Household, q: str) -> bool:
    q = q.lower()
    hay = [h.name, h.city, h.street] + [h.full_name(p) for p in h.persons]
    return any(q in x.lower() for x in hay)


# --- Seiten ------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True, "version": __version__})


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, q: str = "", alle: str = "", conn=Depends(get_conn)):
    hs = db.households(conn)
    shown = [h for h in hs if (alle or h.active_persons or not h.persons)]
    if q.strip():
        shown = [h for h in shown if matches(h, q.strip())]
    return render(
        request, "overview.html", households=shown, stats=stats(hs), q=q, alle=alle,
        hidden=len(hs) - len([h for h in hs if h.active_persons or not h.persons]),
        birthdays=upcoming(hs, date.today(), 14),
    )


@app.get("/haushalt/neu", response_class=HTMLResponse)
def household_new(request: Request):
    return render(request, "household_form.html", h=Household(None, ""), error=None)


@app.get("/haushalt/{hid}", response_class=HTMLResponse)
def household_view(request: Request, hid: int, ok: str = "", conn=Depends(get_conn)):
    h = db.household(conn, hid)
    if not h:
        raise HTTPException(404)
    s = db.settings(conn)
    return render(request, "household.html", h=h, ok=ok, church_json=_js(_church(s)),
                  pos_json=_js({"lat": h.lat, "lon": h.lon} if h.has_pos else None))


@app.get("/haushalt/{hid}/bearbeiten", response_class=HTMLResponse)
def household_edit(request: Request, hid: int, conn=Depends(get_conn)):
    h = db.household(conn, hid)
    if not h:
        raise HTTPException(404)
    return render(request, "household_form.html", h=h, error=None)


@app.post("/haushalt", response_class=HTMLResponse)
def household_save(
    request: Request,
    id: int | None = Form(None), name: str = Form(""), street: str = Form(""),
    zip: str = Form(""), city: str = Form(""), phone: str = Form(""), note: str = Form(""),
    conn=Depends(get_conn),
):
    h = Household(id or None, clean(name), clean(street), clean(zip), clean(city), clean(phone), note.strip())
    if not h.name:
        return render(request, "household_form.html", h=h, error="Familienname fehlt")
    if h.id and not db.household(conn, h.id):
        raise HTTPException(404)
    new = not h.id
    hid = db.save_household(conn, h)
    msg = "Haushalt gespeichert"
    try:
        res = geo.update_household(conn, db.household(conn, hid))
        if res and res.status == "fehlt":
            msg += " – Anschrift auf der Karte nicht gefunden, bitte Nadel von Hand setzen"
        elif res and res.status == "ungenau":
            msg += " – Straße nicht gefunden, Nadel steht nur im Ort"
    except geo.GeoError:
        msg += " – Karte: OpenStreetMap gerade nicht erreichbar, später unter Karte nachholen"
    if new:
        return redirect("/person/neu", haushalt=hid, hinweis=msg.partition(" – ")[2])
    return redirect(f"/haushalt/{hid}", ok=msg)


def _coord(v: str, lo: float, hi: float) -> float:
    try:
        x = float(v.replace(",", "."))
    except ValueError:
        raise HTTPException(400, "Ungültige Koordinate")
    if not lo <= x <= hi:
        raise HTTPException(400, "Ungültige Koordinate")
    return x


@app.post("/haushalt/{hid}/position")
def household_position(hid: int, lat: str = Form(...), lon: str = Form(...), conn=Depends(get_conn)):
    h = db.household(conn, hid)
    if not h:
        raise HTTPException(404)
    db.set_geo(conn, hid, _coord(lat, -90, 90), _coord(lon, -180, 180), "hand", h.address)
    return redirect(f"/haushalt/{hid}", ok="Position gespeichert")


@app.post("/haushalt/{hid}/position/auto")
def household_position_auto(hid: int, conn=Depends(get_conn)):
    h = db.household(conn, hid)
    if not h:
        raise HTTPException(404)
    h.geo = ""
    try:
        res = geo.update_household(conn, h)
    except geo.GeoError:
        return redirect(f"/haushalt/{hid}", ok="OpenStreetMap gerade nicht erreichbar – bitte später erneut versuchen")
    text = {"auto": "Position automatisch ermittelt", "ungenau": "Nur der Ort wurde gefunden",
            "fehlt": "Anschrift nicht gefunden"}.get(res.status if res else "", "Keine Anschrift hinterlegt")
    return redirect(f"/haushalt/{hid}", ok=text)


def _js(data) -> str:
    """JSON sicher in <script> einbetten."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _church(s: dict) -> dict | None:
    try:
        return {"lat": float(s["church_lat"]), "lon": float(s["church_lon"]),
                "name": s["church_name"] or "Gemeinde", "address": s["church_address"]}
    except ValueError:
        return None


@app.get("/karte", response_class=HTMLResponse)
def map_page(request: Request, conn=Depends(get_conn)):
    hs = [h for h in db.households(conn) if h.active_persons]
    pins = [{
        "id": h.id, "name": h.name, "address": h.address, "lat": h.lat, "lon": h.lon, "geo": h.geo,
        "persons": len(h.active_persons), "members": sum(p.member for p in h.active_persons),
    } for h in hs if h.has_pos]
    missing = [h for h in hs if not h.has_pos or h.geo in ("ungenau", "fehlt")]
    stale = sum(1 for h in hs if h.geo_stale)
    return render(
        request, "map.html", pins_json=_js(pins),
        church_json=_js(_church(db.settings(conn))),
        missing=missing, stale=stale, batch=geo.batch, n_households=len(hs),
    )


@app.post("/karte/ermitteln")
def map_geocode(nochmal: str = Form("")):
    geo.batch.start(force=bool(nochmal))
    return redirect("/karte")


@app.post("/haushalt/{hid}/loeschen")
def household_delete(hid: int, conn=Depends(get_conn)):
    db.delete_household(conn, hid)
    return redirect("/")


@app.post("/haushalt/{hid}/einwilligung")
def household_consent(hid: int, conn=Depends(get_conn)):
    n = db.consent_all(conn, hid, date.today())
    return redirect(f"/haushalt/{hid}", ok=f"Einwilligung für {n} Person(en) eingetragen")


@app.get("/person/neu", response_class=HTMLResponse)
def person_new(request: Request, haushalt: int, hinweis: str = "", conn=Depends(get_conn)):
    h = db.household(conn, haushalt)
    if not h:
        raise HTTPException(404)
    # Ab dem dritten Eintrag ist es meist ein Kind
    p = Person(None, h.id, "", child=len(h.persons) >= 2)
    return render(request, "person_form.html", p=p, h=h, households=db.households(conn), error=None,
                  hinweis=hinweis)


@app.get("/person/{pid}", response_class=HTMLResponse)
def person_edit(request: Request, pid: int, conn=Depends(get_conn)):
    p = db.person(conn, pid)
    if not p:
        raise HTTPException(404)
    h = db.household(conn, p.household_id)
    return render(request, "person_form.html", p=p, h=h, households=db.households(conn), error=None)


@app.post("/person", response_class=HTMLResponse)
def person_save(
    request: Request,
    id: int | None = Form(None), household_id: int = Form(...),
    first_name: str = Form(""), last_name: str = Form(""), child: str = Form(""),
    birthday: str = Form(""), mobile: str = Form(""), email: str = Form(""),
    member: str = Form(""), member_since: str = Form(""),
    consent: str = Form(""), consent_on: str = Form(""), left_on: str = Form(""),
    note: str = Form(""), weiter: str = Form(""),
    conn=Depends(get_conn),
):
    h = db.household(conn, household_id)
    if not h:
        raise HTTPException(404)
    p = Person(
        id or None, household_id, clean(first_name), clean(last_name), bool(child),
        mobile=clean(mobile), email=clean(email), member=bool(member), consent=bool(consent),
        note=note.strip(),
    )
    try:
        if not p.first_name:
            raise FormError("Vorname fehlt")
        p.birthday = opt_date(birthday, "Geburtstag")
        p.member_since = opt_date(member_since, "Mitglied seit")
        p.consent_on = opt_date(consent_on, "Einwilligung am")
        p.left_on = opt_date(left_on, "Ausgetreten am")
        if p.email and ("@" not in p.email or " " in p.email):
            raise FormError("E-Mail-Adresse sieht ungültig aus")
    except FormError as e:
        return render(request, "person_form.html", p=p, h=h, households=db.households(conn), error=str(e))
    if p.last_name == h.name:
        p.last_name = ""
    if p.consent and not p.consent_on:
        p.consent_on = date.today()
    if not p.consent:
        p.consent_on = None
    if not p.member:
        p.member_since = None
    if p.id and not db.person(conn, p.id):
        raise HTTPException(404)
    db.save_person(conn, p)
    if weiter:
        return redirect("/person/neu", haushalt=household_id)
    return redirect(f"/haushalt/{household_id}", ok=f"{p.first_name} gespeichert")


@app.post("/person/{pid}/loeschen")
def person_delete(pid: int, conn=Depends(get_conn)):
    p = db.person(conn, pid)
    if not p:
        raise HTTPException(404)
    db.delete_person(conn, pid)
    return redirect(f"/haushalt/{p.household_id}", ok=f"{p.first_name} gelöscht")


@app.get("/geburtstage", response_class=HTMLResponse)
def birthdays(request: Request, conn=Depends(get_conn)):
    today = date.today()
    items = upcoming(db.households(conn), today, 366)
    months: list[tuple[str, list]] = []
    for e in items:
        label = f"{fmt.MONTHS_LONG[e['date'].month - 1]} {e['date'].year}"
        if not months or months[-1][0] != label:
            months.append((label, []))
        months[-1][1].append(e)
    return render(request, "birthdays.html", months=months)


@app.get("/liste", response_class=HTMLResponse)
def list_page(request: Request, conn=Depends(get_conn)):
    hs = db.households(conn)
    missing = [(h, p) for h in hs for p in h.active_persons if not p.consent]
    listed = pdf.listed_households(hs)
    return render(
        request, "list.html", missing=missing, settings=db.settings(conn),
        n_households=len(listed), n_persons=sum(len(pdf.listed_persons(h)) for h in listed),
    )


@app.get("/liste.pdf")
def list_pdf(email: str = "", geburtstage: str = "", conn=Depends(get_conn)):
    s = db.settings(conn)
    data = pdf.render(db.households(conn), pdf.Options(
        church_name=s["church_name"], note=s["pdf_note"], email=bool(email), birthdays=bool(geburtstage),
        logo=logo.load(),
    ))
    name = f"Mitgliederliste_{date.today().isoformat()}.pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/einstellungen", response_class=HTMLResponse)
def settings_page(request: Request, ok: str = "", conn=Depends(get_conn)):
    return render(request, "settings.html", settings=db.settings(conn), ok=ok, result=None, error=None,
                  has_logo=logo.path().exists())


@app.get("/logo.png")
def logo_png():
    data = logo.load()
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/einstellungen/logo", response_class=HTMLResponse)
async def logo_upload(request: Request, file: UploadFile = File(...), conn=Depends(get_conn)):
    try:
        logo.save(await file.read())
    except logo.LogoError as e:
        return render(request, "settings.html", settings=db.settings(conn), ok="", result=None,
                      error=str(e), has_logo=logo.path().exists())
    return redirect("/einstellungen", ok="Logo gespeichert")


@app.post("/einstellungen/logo/loeschen")
def logo_delete():
    logo.delete()
    return redirect("/einstellungen", ok="Logo entfernt")


@app.post("/einstellungen")
def settings_save(church_name: str = Form(""), pdf_note: str = Form(""), church_address: str = Form(""),
                  conn=Depends(get_conn)):
    values = {"church_name": clean(church_name), "pdf_note": clean(pdf_note),
              "church_address": clean(church_address)}
    msg = "Gespeichert"
    old = db.settings(conn)
    if values["church_address"] != old["church_address"] or (values["church_address"] and not old["church_lat"]):
        values["church_lat"] = values["church_lon"] = ""
        if values["church_address"]:
            try:
                res = geo.lookup_text(values["church_address"])
                if res.lat is not None:
                    values["church_lat"], values["church_lon"] = str(res.lat), str(res.lon)
                else:
                    msg = "Gespeichert – Anschrift der Gemeinde wurde auf der Karte nicht gefunden"
            except geo.GeoError:
                msg = "Gespeichert – OpenStreetMap nicht erreichbar, Gemeinde fehlt vorerst auf der Karte"
    db.save_settings(conn, values)
    return redirect("/einstellungen", ok=msg)


@app.get("/daten/export")
def data_export(conn=Depends(get_conn)):
    name = f"gemeinde-backup-{date.today().isoformat()}.zip"
    return Response(backup.export_zip(conn), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/daten/import", response_class=HTMLResponse)
async def data_import(request: Request, file: UploadFile = File(...), confirm: str = Form(""),
                      conn=Depends(get_conn)):
    ctx = {"settings": db.settings(conn), "ok": "", "result": None, "error": None}
    if not confirm:
        ctx["error"] = "Bitte bestätigen, dass alle Daten ersetzt werden."
    else:
        try:
            counts = backup.import_zip(conn, await file.read())
            ctx["result"] = (f"Backup eingespielt: {counts.get('households', 0)} Haushalte,"
                             f" {counts.get('persons', 0)} Personen.")
            ctx["settings"] = db.settings(conn)
        except backup.BackupError as e:
            ctx["error"] = str(e)
    return render(request, "settings.html", has_logo=logo.path().exists(), **ctx)
