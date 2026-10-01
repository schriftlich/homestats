from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, backup, db, demo, fmt, service
from .domain.model import (
    DEFAULT_UNIT,
    MEDIA,
    MEDIUM_LABEL,
    GasFactor,
    Installment,
    MediumSettings,
    Meter,
    Reading,
    Tariff,
)

log = logging.getLogger("homestats")
BASE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.migrate(conn)
    if os.environ.get("HOMESTATS_DEMO") == "1" and db.is_empty(conn):
        log.info("Leere Datenbank – lege Beispieldaten an (HOMESTATS_DEMO=1)")
        demo.seed(conn)
    conn.close()
    yield


app = FastAPI(title="Zählerstände", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters.update(
    num=fmt.num, euro=fmt.euro, ddate=fmt.ddate, plain=fmt.plain,
)
templates.env.globals.update(
    MEDIA=MEDIA, LABEL=MEDIUM_LABEL, DEFAULT_UNIT=DEFAULT_UNIT,
    month_label=fmt.month_label, MONTHS_LONG=fmt.MONTHS_LONG, version=__version__,
)


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Einfacher CSRF-Schutz: schreibende Requests nur von der eigenen Seite.

    Nutzt den Browser-Header Sec-Fetch-Site statt Origin/Host-Vergleich, weil
    der Host-Header hinter dem Umbrel-Proxy nicht verlässlich ist.
    """
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


def check_medium(m: str | None) -> str:
    if m not in MEDIA:
        raise HTTPException(404)
    return m


class FormError(ValueError):
    pass


def req_num(v: str | None, label: str) -> float:
    try:
        n = fmt.parse_num(v)
    except ValueError:
        raise FormError(f"{label}: ungültige Zahl")
    if n is None:
        raise FormError(f"{label} fehlt")
    return n


def opt_num(v: str | None, label: str) -> float | None:
    try:
        return fmt.parse_num(v)
    except ValueError:
        raise FormError(f"{label}: ungültige Zahl")


def req_date(v: str | None, label: str) -> date:
    try:
        d = fmt.parse_date(v)
    except ValueError:
        raise FormError(f"{label}: ungültiges Datum")
    if d is None:
        raise FormError(f"{label} fehlt")
    return d


def opt_date(v: str | None, label: str) -> date | None:
    try:
        return fmt.parse_date(v)
    except ValueError:
        raise FormError(f"{label}: ungültiges Datum")


# --- Dashboard ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, m: str = "strom", conn=Depends(get_conn)):
    m = check_medium(m)
    boards = service.overview(conn)
    board = next(b for b in boards if b.data.medium == m)
    return render(request, "dashboard.html", boards=boards, board=board, medium=m)


@app.get("/healthz")
def healthz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return JSONResponse({"status": "ok", "version": __version__})


# --- Ablesung erfassen -------------------------------------------------------

def active_meters(conn) -> list[Meter]:
    return [m for m in db.meters(conn) if m.removed_on is None]


def last_reading(conn, meter_id: int) -> Reading | None:
    rs = db.readings(conn, [meter_id])
    return rs[0] if rs else None


@app.get("/ablesung", response_class=HTMLResponse)
def reading_form(request: Request, meter_id: int | None = None, saved: int | None = None, conn=Depends(get_conn)):
    meters = active_meters(conn)
    saved_reading = db.reading(conn, saved) if saved else None
    warnings: list[str] = []
    if saved_reading:
        sm = db.meter(conn, saved_reading.meter_id)
        data = service.load_medium(conn, sm.medium)
        warnings = service.warnings_for_meter(data, sm).get(saved_reading.id, [])
        meter_id = meter_id or sm.id
    selected = next((m for m in meters if m.id == meter_id), meters[0] if meters else None)
    return render(
        request, "reading_form.html", meters=meters, selected=selected,
        last=last_reading(conn, selected.id) if selected else None,
        saved=saved_reading, saved_meter=db.meter(conn, saved_reading.meter_id) if saved_reading else None,
        warnings=warnings,
    )


@app.get("/ablesung/felder", response_class=HTMLResponse)
def reading_fields(request: Request, meter_id: int, conn=Depends(get_conn)):
    meter = db.meter(conn, meter_id)
    if not meter:
        raise HTTPException(404)
    return render(request, "_reading_fields.html", selected=meter, last=last_reading(conn, meter_id))


@app.post("/ablesung")
def reading_save(
    request: Request,
    meter_id: int = Form(...),
    day: str = Form(""),
    value: str = Form(""),
    value_nt: str = Form(""),
    note: str = Form(""),
    reading_id: int = Form(0),
    next_url: str = Form(""),
    conn=Depends(get_conn),
):
    meter = db.meter(conn, meter_id)
    if not meter:
        raise HTTPException(404)
    try:
        r = Reading(
            reading_id, meter_id, req_date(day, "Datum"), req_num(value, "Zählerstand"),
            req_num(value_nt, "Stand NT") if meter.dual else None, note.strip(),
        )
    except FormError as e:
        return render(
            request, "reading_form.html", meters=active_meters(conn), selected=meter,
            last=last_reading(conn, meter_id), error=str(e), form=dict(day=day, value=value, value_nt=value_nt, note=note),
            saved=None, warnings=[],
        )
    try:
        rid = db.save_reading(conn, r)
    except sqlite3.IntegrityError:
        return render(
            request, "reading_edit.html", r=r, meter=meter,
            error="Für diesen Zähler gibt es an dem Tag schon eine Ablesung.",
        )
    if next_url.startswith("/"):
        return RedirectResponse(next_url, status_code=303)
    return redirect("/ablesung", saved=rid)


@app.get("/ablesungen", response_class=HTMLResponse)
def reading_list(request: Request, m: str = "strom", conn=Depends(get_conn)):
    m = check_medium(m)
    data = service.load_medium(conn, m)
    warnings = service.all_warnings(data)
    meters = {x.id: x for x in data.meters}
    # Verbrauch seit vorheriger Ablesung je Zähler
    deltas: dict[int, float] = {}
    for meter in data.meters:
        rs = sorted((r for r in data.readings if r.meter_id == meter.id), key=lambda r: r.day)
        prev_v = meter.initial + (meter.initial_nt if meter.dual else 0) if meter.installed_on else None
        for r in rs:
            v = r.value + ((r.value_nt or 0) if meter.dual else 0)
            if prev_v is not None:
                deltas[r.id] = v - prev_v
            prev_v = v
    return render(
        request, "readings.html", medium=m, data=data, meters=meters, warnings=warnings, deltas=deltas,
    )


@app.get("/ablesungen/{reading_id}", response_class=HTMLResponse)
def reading_edit(request: Request, reading_id: int, conn=Depends(get_conn)):
    r = db.reading(conn, reading_id)
    if not r:
        raise HTTPException(404)
    meter = db.meter(conn, r.meter_id)
    return render(request, "reading_edit.html", r=r, meter=meter)


# --- Zähler ------------------------------------------------------------------

@app.get("/zaehler", response_class=HTMLResponse)
def meter_list(request: Request, edit: int | None = None, neu: str | None = None, conn=Depends(get_conn)):
    meters = db.meters(conn)
    current = next((x for x in meters if x.id == edit), None)
    if neu in MEDIA:
        current = Meter(0, neu, "", DEFAULT_UNIT[neu])
    counts = {r[0]: r[1] for r in conn.execute("SELECT meter_id, COUNT(*) FROM readings GROUP BY meter_id")}
    return render(request, "meters.html", meters=meters, current=current, counts=counts)


@app.post("/zaehler")
def meter_save(
    request: Request,
    id: int = Form(0),
    medium: str = Form(...),
    name: str = Form(""),
    unit: str = Form(""),
    meter_number: str = Form(""),
    dual: str = Form(""),
    installed_on: str = Form(""),
    initial: str = Form(""),
    initial_nt: str = Form(""),
    removed_on: str = Form(""),
    conn=Depends(get_conn),
):
    medium = check_medium(medium)
    try:
        m = Meter(
            id, medium, name.strip() or MEDIUM_LABEL[medium], unit.strip() or DEFAULT_UNIT[medium],
            meter_number.strip(), medium == "strom" and dual == "1",
            opt_date(installed_on, "Einbaudatum"), opt_num(initial, "Anfangsstand") or 0.0,
            opt_num(initial_nt, "Anfangsstand NT") or 0.0, opt_date(removed_on, "Ausbaudatum"),
        )
        if m.installed_on and m.removed_on and m.removed_on < m.installed_on:
            raise FormError("Ausbaudatum liegt vor dem Einbaudatum")
    except FormError as e:
        return render(
            request, "meters.html", meters=db.meters(conn), counts={}, error=str(e),
            current=Meter(id, medium, name, unit, meter_number),
        )
    db.save_meter(conn, m)
    return redirect("/zaehler")


# --- Tarife, Abschläge, Gasfaktoren -----------------------------------------

@app.get("/tarife", response_class=HTMLResponse)
def tariff_page(request: Request, m: str = "strom", error: str = "", conn=Depends(get_conn)):
    m = check_medium(m)
    meters = db.meters(conn, m)
    return render(
        request, "tariffs.html", medium=m, tariffs=db.tariffs(conn, m),
        installments=db.installments(conn, m), settings=db.settings(conn, m),
        factors=db.gas_factors(conn), dual=any(x.dual for x in meters),
        unit=service.medium_unit(meters, m), error=error,
    )


@app.post("/tarife/{medium}/tarif")
def tariff_save(
    medium: str, valid_from: str = Form(""), base_month: str = Form(""), price: str = Form(""),
    price_nt: str = Form(""), conn=Depends(get_conn),
):
    medium = check_medium(medium)
    try:
        db.save_tariff(conn, Tariff(
            0, medium, req_date(valid_from, "Gültig ab"), req_num(base_month, "Grundpreis"),
            req_num(price, "Arbeitspreis"), opt_num(price_nt, "Arbeitspreis NT"),
        ))
    except FormError as e:
        return redirect("/tarife", m=medium, error=str(e))
    return redirect("/tarife", m=medium)


@app.post("/tarife/{medium}/abschlag")
def installment_save(medium: str, valid_from: str = Form(""), amount: str = Form(""), conn=Depends(get_conn)):
    medium = check_medium(medium)
    try:
        db.save_installment(conn, Installment(0, medium, req_date(valid_from, "Gültig ab"), req_num(amount, "Abschlag")))
    except FormError as e:
        return redirect("/tarife", m=medium, error=str(e))
    return redirect("/tarife", m=medium)


@app.post("/tarife/{medium}/zeitraum")
def settings_save(
    medium: str, start_month: int = Form(1), start_day: int = Form(1), count: int = Form(12), conn=Depends(get_conn),
):
    medium = check_medium(medium)
    if not (1 <= start_month <= 12 and 1 <= start_day <= 28 and 1 <= count <= 12):
        return redirect("/tarife", m=medium, error="Ungültiger Abrechnungszeitraum (Tag 1–28, 1–12 Abschläge)")
    db.save_settings(conn, MediumSettings(medium, start_month, start_day, count))
    return redirect("/tarife", m=medium)


@app.post("/tarife/gas/faktor")
def factor_save(valid_from: str = Form(""), zustandszahl: str = Form(""), brennwert: str = Form(""), conn=Depends(get_conn)):
    try:
        db.save_gas_factor(conn, GasFactor(
            0, req_date(valid_from, "Gültig ab"), req_num(zustandszahl, "Zustandszahl"), req_num(brennwert, "Brennwert"),
        ))
    except FormError as e:
        return redirect("/tarife", m="gas", error=str(e))
    return redirect("/tarife", m="gas")


@app.post("/loeschen/{table}/{row_id}")
def delete_row(table: str, row_id: int, next_url: str = Form("/"), conn=Depends(get_conn)):
    if table not in db.DELETABLE:
        raise HTTPException(404)
    db.delete(conn, table, row_id)
    return RedirectResponse(next_url if next_url.startswith("/") else "/", status_code=303)


# --- Daten: Export / Import --------------------------------------------------

@app.get("/daten", response_class=HTMLResponse)
def data_page(request: Request, result: str = "", error: str = ""):
    return render(request, "data.html", result=result, error=error)


@app.get("/daten/export")
def data_export(conn=Depends(get_conn)):
    name = f"zaehlerstaende-backup-{date.today().isoformat()}.zip"
    return Response(
        backup.export_zip(conn), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/daten/import")
async def data_import(file: UploadFile = File(...), confirm: str = Form(""), conn=Depends(get_conn)):
    if confirm != "1":
        return redirect("/daten", error="Bitte bestätigen, dass alle vorhandenen Daten ersetzt werden.")
    try:
        counts = backup.import_zip(conn, await file.read())
    except backup.ImportError_ as e:
        return redirect("/daten", error=str(e))
    summary = ", ".join(f"{n} {t}" for t, n in counts.items())
    return redirect("/daten", result=f"Import erfolgreich: {summary}")
