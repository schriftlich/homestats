"""Dateien lesen, Tags schreiben, verschieben, rückgängig machen.

Ordner (im Container):
  /downloads/metube      Eingang (MeTube)
  /downloads/music       Navidrome
  /downloads/audiobooks  Audiobookshelf
  /data                  Einstellungen, Gedächtnis, Verlauf, Sicherungen für "Rückgängig"
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mutagen.id3 import (
    APIC, ID3, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, ID3NoHeaderError,
)
from mutagen.mp3 import MP3

from . import __version__
from .guess import HOERBUCH, MUSIK, Group, Memory, Track, build_groups, norm, safe_name, target_relpath

log = logging.getLogger("tagger")

MARKER = "HOMESTATS_TAGGER"
REMOVE_FRAMES = ("TXXX:description", "TXXX:synopsis", "TXXX:DESCRIPTION", "TXXX:SYNOPSIS", "TSSE")
LOCK = threading.RLock()


def data_dir() -> Path:
    p = Path(os.environ.get("TAGGER_DATA_DIR", "/data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def downloads() -> Path:
    return Path(os.environ.get("TAGGER_DOWNLOADS", "/downloads"))


def inbox() -> Path:
    return downloads() / os.environ.get("TAGGER_INBOX", "metube")


# --- Einstellungen & Gedächtnis -------------------------------------------------

@dataclass
class Settings:
    move: bool = True              # in music/ bzw. audiobooks/ einsortieren
    square_cover: bool = True      # YouTube-Vorschaubild quadratisch zuschneiden
    strip_description: bool = True # Videobeschreibung (Links, Lyrics) entfernen
    auto: bool = False             # sicher erkannte Gruppen automatisch übernehmen
    auto_minutes: int = 15


def _read_json(name: str, default):
    try:
        return json.loads((data_dir() / name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(name: str, value) -> None:
    path = data_dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(path)


def load_settings() -> Settings:
    raw = _read_json("settings.json", {})
    s = Settings()
    for k, v in raw.items():
        if hasattr(s, k):
            setattr(s, k, v)
    s.auto_minutes = max(5, int(s.auto_minutes or 15))
    return s


def save_settings(s: Settings) -> None:
    _write_json("settings.json", asdict(s))


def load_memory() -> tuple[Memory, set[str]]:
    raw = _read_json("memory.json", {})
    mem = Memory(artists=raw.get("artists", {}), authors=raw.get("authors", {}), kinds=raw.get("kinds", {}))
    return mem, set(raw.get("ignored", []))


def save_memory(mem: Memory, ignored: set[str]) -> None:
    _write_json("memory.json", {"artists": mem.artists, "authors": mem.authors, "kinds": mem.kinds,
                                "ignored": sorted(ignored)})


# --- Einlesen ------------------------------------------------------------------

@dataclass
class FileInfo:
    track: Track
    path: Path
    mtime: float
    size: int
    has_cover: bool
    done: bool


_cache: dict[str, tuple[float, int, FileInfo]] = {}


def _first(tags: ID3 | None, key: str) -> str:
    if not tags or key not in tags:
        return ""
    return str(tags[key].text[0]) if getattr(tags[key], "text", None) else ""


def track_id(relpath: str) -> str:
    return hashlib.sha1(relpath.encode()).hexdigest()[:12]


def read_file(path: Path, relpath: str) -> FileInfo:
    st = path.stat()
    hit = _cache.get(relpath)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    duration, tags = 0.0, None
    try:
        audio = MP3(path)
        duration = audio.info.length
        tags = audio.tags
    except Exception as e:  # kaputte/unvollständige Datei – trotzdem anzeigen
        log.warning("Kann %s nicht lesen: %s", relpath, e)
    t = Track(
        id=track_id(relpath), relpath=relpath,
        title=_first(tags, "TIT2"), artist=_first(tags, "TPE1"), genre=_first(tags, "TCON"),
        date=_first(tags, "TDRC") or _first(tags, "TYER"), duration=duration,
    )
    info = FileInfo(track=t, path=path, mtime=st.st_mtime, size=st.st_size,
                    has_cover=bool(tags and tags.getall("APIC")),
                    done=bool(tags and f"TXXX:{MARKER}" in tags))
    _cache[relpath] = (st.st_mtime, st.st_size, info)
    return info


def scan(min_age: float = 30) -> list[FileInfo]:
    """Alle fertigen MP3s im Eingang (keine halb geladenen, keine versteckten Ordner)."""
    root = inbox()
    out = []
    if not root.is_dir():
        return out
    now = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            low = name.lower()
            if name.startswith(".") or not low.endswith(".mp3") or ".part" in low or ".temp." in low:
                continue
            path = Path(dirpath) / name
            try:
                if now - path.stat().st_mtime < min_age:
                    continue
                rel = path.relative_to(root).as_posix()
                out.append(read_file(path, rel))
            except FileNotFoundError:
                continue
    return out


@dataclass
class Inbox:
    groups: list[Group]
    files: dict[str, FileInfo]
    done: list[FileInfo]
    ignored: list[FileInfo]


def load_inbox(min_age: float = 30) -> Inbox:
    mem, ignored = load_memory()
    infos = scan(min_age)
    kept = kept_in_inbox()
    for i in infos:
        i.done = i.track.relpath in kept
    files = {i.track.id: i for i in infos}
    todo = [i for i in infos if not i.done and i.track.relpath not in ignored]
    groups = build_groups([i.track for i in todo], mem)
    return Inbox(groups=groups, files=files,
                 done=[i for i in infos if i.done and i.track.relpath not in ignored],
                 ignored=[i for i in infos if i.track.relpath in ignored])


# --- Cover ---------------------------------------------------------------------

def square_jpeg(data: bytes, max_size: int = 1000) -> bytes | None:
    """Mittiges Quadrat aus dem (meist 16:9) YouTube-Vorschaubild."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    w, h = im.size
    if abs(w - h) <= 2 and max(w, h) <= max_size:
        return None  # schon quadratisch
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    im = im.crop((left, top, left + side, top + side)).convert("RGB")
    if side > max_size:
        im = im.resize((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def cover_bytes(path: Path, square: bool, thumb: int | None = 240) -> bytes | None:
    from PIL import Image

    try:
        tags = ID3(path)
    except Exception:
        return None
    pics = tags.getall("APIC")
    if not pics:
        return None
    data = pics[0].data
    if square:
        data = square_jpeg(data) or data
    if thumb:
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((thumb, thumb))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            data = buf.getvalue()
        except Exception:
            return None
    return data


# --- Übernehmen ----------------------------------------------------------------

@dataclass
class Item:
    """Was mit einer Datei passieren soll."""
    track_id: str
    title: str
    artist: str
    track: int | None = None


@dataclass
class Plan:
    group_id: str
    kind: str
    album: str
    albumartist: str
    year: str = ""
    genre: str = ""
    narrator: str = ""
    move: bool = True
    items: list[Item] = field(default_factory=list)


class ApplyError(Exception):
    pass


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for n in range(2, 1000):
        q = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not q.exists():
            return q
    raise ApplyError(f"Kein freier Dateiname für {p}")


def write_tags(path: Path, plan: Plan, item: Item, total: int, settings: Settings) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    def put(frame_cls, key: str, value: str):
        tags.delall(key)
        if value:
            tags.add(frame_cls(encoding=3, text=[value]))

    put(TIT2, "TIT2", item.title)
    put(TPE1, "TPE1", item.artist or plan.albumartist)
    put(TPE2, "TPE2", plan.albumartist)
    put(TALB, "TALB", plan.album)
    put(TCON, "TCON", plan.genre)
    put(TDRC, "TDRC", plan.year)
    tags.delall("TYER")
    put(TRCK, "TRCK", f"{item.track}/{total}" if item.track and total > 1 else (str(item.track) if item.track else ""))
    tags.delall("TPOS")
    if plan.kind == HOERBUCH:
        put(TCOM, "TCOM", plan.narrator)   # Audiobookshelf: Sprecher
    if settings.strip_description:
        for key in REMOVE_FRAMES:
            tags.delall(key)
    if settings.square_cover:
        pics = tags.getall("APIC")
        if pics:
            sq = square_jpeg(pics[0].data)
            if sq:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=sq))
    tags.delall(f"TXXX:{MARKER}")
    tags.add(TXXX(encoding=3, desc=MARKER, text=[__version__]))
    tags.save(path, v2_version=3)


def target_for(plan: Plan, item: Item, info: FileInfo) -> Path:
    multi = len(plan.items) > 1
    if plan.move:
        rel = target_relpath(plan.kind, plan.album, plan.albumartist, item.title, item.track,
                             plan.narrator, plan.genre, multi=multi)
        return downloads() / rel
    # bleibt im Eingang, bekommt nur einen sauberen Namen
    prefix = f"{item.track:02d} - " if item.track and multi and plan.kind == MUSIK else ""
    return info.path.with_name(prefix + safe_name(item.title) + ".mp3")


def preview_targets(plan: Plan, files: dict[str, FileInfo]) -> dict[str, str]:
    out = {}
    for it in plan.items:
        p = target_for(plan, it, files[it.track_id])
        try:
            out[it.track_id] = p.relative_to(downloads()).as_posix()
        except ValueError:
            out[it.track_id] = p.as_posix()
    return out


def apply(plan: Plan, files: dict[str, FileInfo], settings: Settings, auto: bool = False) -> dict:
    """Tags schreiben und Dateien umbenennen/verschieben. Gibt den Verlaufseintrag zurück."""
    if not plan.items:
        raise ApplyError("Keine Datei ausgewählt.")
    if plan.kind == HOERBUCH and not plan.albumartist:
        raise ApplyError("Bitte den Autor eintragen.")
    if not plan.album:
        raise ApplyError("Album bzw. Buchtitel fehlt.")
    with LOCK:
        entry_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        undo_dir = data_dir() / "undo" / entry_id
        undo_dir.mkdir(parents=True, exist_ok=True)
        done, errors = [], []
        sources = set()
        for n, item in enumerate(plan.items):
            info = files.get(item.track_id)
            if not info or not info.path.exists():
                errors.append(f"Datei nicht mehr vorhanden ({item.title})")
                continue
            src = info.path
            dst = target_for(plan, item, info)
            # Erst prüfen, dann ändern: entweder alles klappt oder die Datei bleibt unverändert
            problem = _check_writable(src, dst)
            if problem:
                errors.append(problem)
                continue
            backup = undo_dir / f"{n}.id3"
            try:
                ID3(src).save(_touch(backup), v2_version=4)
            except ID3NoHeaderError:
                backup = None
            except OSError as e:
                errors.append(f"{_rel(src)}: Sicherung fehlgeschlagen ({e.strerror or e})")
                continue
            tagged = False
            st = src.stat()
            try:
                write_tags(src, plan, item, len(plan.items), settings)
                tagged = True
                if dst != src:
                    make_dirs(dst.parent)
                    dst = unique_path(dst)
                    _move(src, dst)
                    fix_owner(dst)
            except OSError as e:
                where = e.filename if getattr(e, "filename", None) else src
                errors.append(f"{_rel(Path(where))}: {_os_error(e)}")
                if tagged and src.exists():
                    _restore_tags(src, backup)   # nicht halb fertig liegen lassen
                    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
                continue
            sources.add(src.parent)
            done.append({"old": _rel(src), "new": _rel(dst), "backup": backup.name if backup else None,
                         "title": item.title})
            _cache.pop(info.track.relpath, None)
        for d in sorted(sources, key=lambda p: len(p.parts), reverse=True):
            _remove_empty_dirs(d)
        entry = {
            "id": entry_id, "time": time.time(), "auto": auto, "kind": plan.kind,
            "label": f"{plan.albumartist} – {plan.album}" if plan.albumartist else plan.album,
            "move": plan.move, "items": done, "errors": errors, "undone": False,
        }
        if done:
            _append_log(entry)
        else:
            shutil.rmtree(undo_dir, ignore_errors=True)
        return entry


def _os_error(e: OSError) -> str:
    if isinstance(e, PermissionError):
        return "keine Schreibrechte"
    return e.strerror or str(e)


def _check_writable(src: Path, dst: Path) -> str | None:
    if not os.access(src, os.W_OK):
        return f"{_rel(src)}: Datei ist schreibgeschützt (keine Schreibrechte)"
    if dst.parent != src.parent and not os.access(src.parent, os.W_OK):
        return f"{_rel(src.parent)}: Ordner ist schreibgeschützt, Datei kann nicht verschoben werden"
    # nächsten existierenden Ordner auf dem Weg zum Ziel prüfen
    d = dst.parent
    while not d.exists() and d != d.parent:
        d = d.parent
    if not os.access(d, os.W_OK):
        return f"{_rel(d)}: Ordner ist schreibgeschützt, Ziel kann nicht angelegt werden"
    return None


def _owner() -> tuple[int, int] | None:
    """Besitzer des Download-Ordners – neue Ordner/Dateien bekommen denselben (falls wir root sind)."""
    if os.geteuid() != 0:
        return None
    try:
        st = downloads().stat()
        return st.st_uid, st.st_gid
    except OSError:
        return None


def fix_owner(p: Path) -> None:
    own = _owner()
    if own:
        try:
            os.chown(p, *own)
        except OSError:
            pass


def make_dirs(d: Path) -> None:
    missing = []
    while not d.exists():
        missing.append(d)
        d = d.parent
    for m in reversed(missing):
        m.mkdir(exist_ok=True)
        fix_owner(m)


def _restore_tags(path: Path, backup: Path | None) -> None:
    try:
        ID3(path).delete(path)
        if backup and backup.exists() and backup.stat().st_size:
            ID3(backup).save(path, v2_version=4)
        _cache.pop(_rel_inbox(path), None)
    except Exception:
        log.exception("Konnte Tags von %s nicht zurücksetzen", path)


def _rel_inbox(p: Path) -> str:
    try:
        return p.relative_to(inbox()).as_posix()
    except ValueError:
        return p.as_posix()


def _touch(p: Path) -> Path:
    p.write_bytes(b"")
    return p


def _rel(p: Path) -> str:
    try:
        return p.relative_to(downloads()).as_posix()
    except ValueError:
        return p.as_posix()


def _move(src: Path, dst: Path) -> None:
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno != 18:  # EXDEV: anderes Dateisystem
            raise
        shutil.move(src, dst)


def _remove_empty_dirs(d: Path) -> None:
    """Leere Ordner im Eingang aufräumen (nie den Eingang selbst)."""
    root = inbox().resolve()
    d = d.resolve()
    while d != root and root in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


# --- Verlauf & Rückgängig -------------------------------------------------------

def _append_log(entry: dict) -> None:
    with open(data_dir() / "log.jsonl", "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def kept_in_inbox() -> set[str]:
    """Dateien, die bewusst getaggt im Eingang gelassen wurden (relativ zum Eingang)."""
    prefix = inbox().relative_to(downloads()).as_posix() + "/" if inbox().is_relative_to(downloads()) else ""
    out = set()
    for e in history(100_000):
        if e.get("undone") or e.get("move", True):
            continue
        for it in e["items"]:
            if it["new"].startswith(prefix):
                out.add(it["new"][len(prefix):])
    return out


def history(limit: int = 200) -> list[dict]:
    try:
        lines = (data_dir() / "log.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    entries = {}
    for ln in lines:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        entries[e["id"]] = e   # spätere Zeilen (z. B. "undone") überschreiben frühere
    return sorted(entries.values(), key=lambda e: e["time"], reverse=True)[:limit]


def undo(entry_id: str) -> list[str]:
    with LOCK:
        entry = next((e for e in history(10_000) if e["id"] == entry_id), None)
        if not entry or entry.get("undone"):
            raise ApplyError("Eintrag nicht gefunden oder schon rückgängig gemacht.")
        undo_dir = data_dir() / "undo" / entry_id
        errors = []
        for it in reversed(entry["items"]):
            cur, old = downloads() / it["new"], downloads() / it["old"]
            if not cur.exists():
                errors.append(f"Nicht mehr da: {it['new']}")
                continue
            try:
                if it.get("backup") and (undo_dir / it["backup"]).exists():
                    saved = ID3(undo_dir / it["backup"])
                    ID3(cur).delete(cur)
                    saved.save(cur, v2_version=4)
                make_dirs(old.parent)
                dst = unique_path(old) if cur != old else old
                if cur != dst:
                    _move(cur, dst)
                _remove_empty_dirs_lib(cur.parent)
            except OSError as e:
                errors.append(f"{it['new']}: {e.strerror or e}")
        entry["undone"] = True
        entry["undo_errors"] = errors
        _append_log(entry)
        _cache.clear()
        return errors


def _remove_empty_dirs_lib(d: Path) -> None:
    """Nach dem Zurückschieben leere Album-/Autorordner in music/ bzw. audiobooks/ entfernen."""
    stop = {downloads().resolve(), (downloads() / "music").resolve(), (downloads() / "audiobooks").resolve(),
            inbox().resolve()}
    d = d.resolve()
    while d not in stop and downloads().resolve() in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


# --- Lernen aus Korrekturen ------------------------------------------------------

def remember(group: Group, plan: Plan) -> None:
    mem, ignored = load_memory()
    if plan.kind != group.kind:
        mem.kinds[group.id] = plan.kind
    if plan.kind == HOERBUCH:
        if plan.albumartist and plan.album:
            mem.authors[norm(plan.album)] = plan.albumartist
    elif plan.albumartist and norm(plan.albumartist) != norm(group.albumartist):
        from .guess import clean_channel
        for t in group.tracks:
            ch = clean_channel(t.artist)
            if ch and norm(ch) != norm(plan.albumartist):
                mem.artists[norm(ch)] = plan.albumartist
    save_memory(mem, ignored)


def set_kind(group_id: str, kind: str) -> None:
    mem, ignored = load_memory()
    mem.kinds[group_id] = kind
    save_memory(mem, ignored)


def set_ignored(relpaths: list[str], on: bool) -> None:
    mem, ignored = load_memory()
    ignored = (ignored | set(relpaths)) if on else (ignored - set(relpaths))
    save_memory(mem, ignored)


def plan_from_group(g: Group, settings: Settings) -> Plan:
    return Plan(group_id=g.id, kind=g.kind, album=g.album, albumartist=g.albumartist, year=g.year,
                genre=g.genre, narrator=g.narrator, move=settings.move,
                items=[Item(t.id, g.proposals[t.id].title, g.proposals[t.id].artist, g.proposals[t.id].track)
                       for t in g.tracks])


# --- Automatik -----------------------------------------------------------------

def auto_run(min_age_minutes: int = 10) -> list[dict]:
    """Sicher erkannte Gruppen übernehmen, deren Dateien seit ein paar Minuten unverändert sind."""
    settings = load_settings()
    box = load_inbox(min_age=min_age_minutes * 60)
    results = []
    old_enough = {i.track.relpath for i in box.files.values()}
    young = [i.track.relpath for i in scan(min_age=0) if i.track.relpath not in old_enough]
    for g in box.groups:
        if not g.sure:
            continue
        # Ordner wird evtl. noch befüllt (Playlist lädt noch): warten, bis alle Dateien alt genug sind
        if g.folder and any(p.startswith(g.folder + "/") for p in young):
            continue
        try:
            results.append(apply(plan_from_group(g, settings), box.files, settings, auto=True))
        except ApplyError as e:
            log.warning("Automatik %s: %s", g.album, e)
    return results


class AutoWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="tagger-auto")
        self.stop = threading.Event()
        self.last_run: float | None = None
        self.last_result: str = ""

    def run(self):
        while not self.stop.wait(30):
            s = load_settings()
            if not s.auto:
                continue
            if self.last_run and time.time() - self.last_run < s.auto_minutes * 60:
                continue
            self.last_run = time.time()
            try:
                res = auto_run()
                n = sum(len(r["items"]) for r in res)
                self.last_result = f"{n} Datei(en) übernommen" if n else "nichts Neues"
            except Exception as e:  # nie den Thread sterben lassen
                log.exception("Automatik fehlgeschlagen")
                self.last_result = f"Fehler: {e}"
