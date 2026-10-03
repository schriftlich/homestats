from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, library as lib
from .guess import HOERBUCH, MUSIK, Group

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tagger")
BASE = Path(__file__).parent
worker: lib.AutoWorker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    if os.environ.get("TAGGER_NO_WORKER") != "1":
        worker = lib.AutoWorker()
        worker.start()
    yield
    if worker:
        worker.stop.set()


app = FastAPI(title="MP3-Tagger", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def dtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def duration(sec: float) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


templates.env.filters.update(dtime=dtime, duration=duration)
templates.env.globals.update(version=__version__, HOERBUCH=HOERBUCH, MUSIK=MUSIK)


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
    return templates.TemplateResponse(request, name, {"msg": request.query_params.get("msg"),
                                                      "err": request.query_params.get("err"), **ctx})


def find_group(box: lib.Inbox, gid: str) -> Group:
    g = next((g for g in box.groups if g.id == gid), None)
    if not g:
        raise HTTPException(404, "Diese Gruppe gibt es nicht mehr – vermutlich schon übernommen.")
    return g


def clean(v) -> str:
    return " ".join(str(v or "").split())


def plan_from_form(g: Group, form, settings: lib.Settings) -> lib.Plan:
    kind = form.get("kind") if form.get("kind") in (MUSIK, HOERBUCH) else g.kind
    items = []
    for t in g.tracks:
        if not form.get(f"on-{t.id}"):
            continue
        raw = clean(form.get(f"track-{t.id}"))
        items.append(lib.Item(
            track_id=t.id,
            title=clean(form.get(f"title-{t.id}")) or g.proposals[t.id].title,
            artist=clean(form.get(f"artist-{t.id}")),
            track=int(raw) if raw.isdigit() and 0 < int(raw) < 1000 else None,
        ))
    return lib.Plan(
        group_id=g.id, kind=kind, album=clean(form.get("album")), albumartist=clean(form.get("albumartist")),
        year=clean(form.get("year"))[:4], genre=clean(form.get("genre")), narrator=clean(form.get("narrator")),
        move=form.get("target", "move") == "move", items=items,
    )


# --- Seiten ------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True, "version": __version__})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    box = lib.load_inbox()
    sure = [g for g in box.groups if g.sure]
    return render(request, "index.html", box=box, sure=sure,
                  n_files=sum(len(g.tracks) for g in box.groups),
                  inbox_ok=lib.inbox().is_dir(), settings=lib.load_settings())


@app.get("/gruppe/{gid}", response_class=HTMLResponse)
def group_page(request: Request, gid: str):
    box = lib.load_inbox()
    g = find_group(box, gid)
    settings = lib.load_settings()
    plan = lib.plan_from_group(g, settings)
    return render(request, "group.html", g=g, plan=plan, box=box, settings=settings,
                  targets=lib.preview_targets(plan, box.files), selected={t.id for t in g.tracks})


@app.post("/gruppe/{gid}", response_class=HTMLResponse)
async def group_post(request: Request, gid: str):
    form = await request.form()
    box = lib.load_inbox()
    g = find_group(box, gid)
    settings = lib.load_settings()
    action = form.get("action", "preview")

    if action == "ignore":
        lib.set_ignored([t.relpath for t in g.tracks], True)
        return redirect("/", msg=f"„{g.album or g.folder}“ wird ab jetzt ausgeblendet.")
    if action in ("as-musik", "as-hoerbuch"):
        lib.set_kind(g.id, MUSIK if action == "as-musik" else HOERBUCH)
        return redirect(f"/gruppe/{gid}")

    plan = plan_from_form(g, form, settings)
    if action == "apply":
        try:
            entry = lib.apply(plan, box.files, settings)
        except lib.ApplyError as e:
            err = str(e)
        else:
            lib.remember(g, plan)
            n = len(entry["items"])
            text = f"{n} Datei(en) übernommen: {entry['label']}"
            if entry["errors"] and not n:
                err = "Nichts übernommen, die Dateien sind unverändert. " + "; ".join(entry["errors"])
            elif entry["errors"]:
                return redirect("/verlauf", err=text + " – Probleme: " + "; ".join(entry["errors"]))
            else:
                return redirect("/", msg=text)
    else:
        err = None
    return render(request, "group.html", g=g, plan=plan, box=box, settings=settings, err=err,
                  targets=lib.preview_targets(plan, box.files), selected={i.track_id for i in plan.items})


@app.post("/alle")
def apply_all(request: Request):
    box = lib.load_inbox()
    settings = lib.load_settings()
    n, errors = 0, []
    for g in box.groups:
        if not g.sure:
            continue
        try:
            entry = lib.apply(lib.plan_from_group(g, settings), box.files, settings)
            n += len(entry["items"])
            errors += entry["errors"]
        except lib.ApplyError as e:
            errors.append(f"{g.album}: {e}")
    if errors:
        target = "/verlauf" if n else "/"
        return redirect(target, err=f"{n} Datei(en) übernommen, Probleme: " + "; ".join(errors[:5]))
    return redirect("/", msg=f"{n} Datei(en) übernommen.")


@app.get("/cover/{tid}")
def cover(tid: str, sq: int = 1):
    box_files = {i.track.id: i for i in lib.scan(min_age=0)}
    info = box_files.get(tid)
    data = lib.cover_bytes(info.path, square=bool(sq)) if info else None
    if not data:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/verlauf", response_class=HTMLResponse)
def history_page(request: Request):
    return render(request, "history.html", entries=lib.history())


@app.post("/verlauf/{entry_id}/rueckgaengig")
def history_undo(entry_id: str):
    try:
        errors = lib.undo(entry_id)
    except lib.ApplyError as e:
        return redirect("/verlauf", err=str(e))
    if errors:
        return redirect("/verlauf", err="Teilweise rückgängig gemacht: " + "; ".join(errors))
    return redirect("/", msg="Rückgängig gemacht – die Dateien liegen wieder im Eingang.")


@app.get("/einstellungen", response_class=HTMLResponse)
def settings_page(request: Request):
    box = lib.load_inbox()
    dl = lib.downloads()
    folders = []
    for label, p in (("Eingang (MeTube)", lib.inbox()), ("Musik (Navidrome)", dl / "music"),
                     ("Hörbücher (Audiobookshelf)", dl / "audiobooks")):
        folders.append({"label": label, "path": p.relative_to(dl).as_posix() if p.is_relative_to(dl) else str(p),
                        "exists": p.is_dir(), "writable": p.is_dir() and os.access(p, os.W_OK)})
    return render(request, "settings.html", settings=lib.load_settings(), folders=folders, box=box,
                  worker=worker)


@app.post("/einstellungen")
async def settings_post(request: Request):
    form = await request.form()
    s = lib.load_settings()
    s.move = form.get("move") == "1"
    s.square_cover = form.get("square_cover") == "1"
    s.strip_description = form.get("strip_description") == "1"
    s.auto = form.get("auto") == "1"
    try:
        s.auto_minutes = max(5, min(1440, int(form.get("auto_minutes") or 15)))
    except ValueError:
        pass
    lib.save_settings(s)
    if worker and s.auto:
        worker.last_run = None   # gleich beim nächsten Durchlauf prüfen
    return redirect("/einstellungen", msg="Gespeichert.")


@app.post("/einstellungen/einblenden")
async def unignore(request: Request):
    form = await request.form()
    lib.set_ignored(form.getlist("path"), False)
    return redirect("/einstellungen", msg="Wieder im Eingang.")


@app.post("/einstellungen/vergessen")
def forget():
    _, ignored = lib.load_memory()
    lib.save_memory(lib.Memory(), ignored)
    return redirect("/einstellungen", msg="Gelernte Zuordnungen gelöscht.")
