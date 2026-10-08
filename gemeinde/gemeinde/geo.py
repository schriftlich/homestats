"""Koordinaten zu Anschriften über OpenStreetMap Nominatim.

Übertragen werden nur Straße, PLZ und Ort – nie Namen. Nominatim erlaubt
höchstens eine Anfrage pro Sekunde; der Sammellauf hält das ein.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import __version__, db

log = logging.getLogger("gemeinde.geo")

URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"Gemeindeverzeichnis/{__version__} (selbst gehostete Umbrel-App; github.com/schriftlich/homestats)"
MIN_INTERVAL = 1.1

_lock = threading.Lock()
_last_call = 0.0


class GeoError(RuntimeError):
    """Dienst nicht erreichbar – im Gegensatz zu „Adresse nicht gefunden“."""


@dataclass
class Result:
    lat: float | None
    lon: float | None
    status: str  # auto | ungenau | fehlt


def _fetch(params: dict) -> list[dict]:
    """Eine Nominatim-Anfrage (in Tests ersetzt)."""
    global _last_call
    q = urllib.parse.urlencode({**params, "format": "jsonv2", "limit": 1, "countrycodes": "de,at,ch"})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": USER_AGENT, "Accept-Language": "de"})
    with _lock:
        wait = _last_call + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            raise GeoError(f"OpenStreetMap nicht erreichbar: {e}") from e
        finally:
            _last_call = time.monotonic()


def lookup(street: str, zip_: str, city: str) -> Result:
    if not (zip_ or city):
        return Result(None, None, "fehlt")
    if street:
        hits = _fetch({"street": street, "postalcode": zip_, "city": city})
        if hits:
            return Result(float(hits[0]["lat"]), float(hits[0]["lon"]), "auto")
    hits = _fetch({"postalcode": zip_, "city": city})
    if hits:
        return Result(float(hits[0]["lat"]), float(hits[0]["lon"]), "ungenau" if street else "auto")
    return Result(None, None, "fehlt")


def lookup_text(address: str) -> Result:
    if not address.strip():
        return Result(None, None, "fehlt")
    hits = _fetch({"q": address})
    if hits:
        return Result(float(hits[0]["lat"]), float(hits[0]["lon"]), "auto")
    return Result(None, None, "fehlt")


def update_household(conn, h: db.Household) -> Result | None:
    """Ermittelt Koordinaten, wenn sie fehlen oder die Anschrift sich geändert hat.

    Von Hand gesetzte Positionen bleiben, solange die Anschrift gleich ist.
    """
    if not h.address:
        db.set_geo(conn, h.id, None, None, "", "")
        return None
    if not h.geo_stale:
        return None
    res = lookup(h.street, h.zip, h.city)
    db.set_geo(conn, h.id, res.lat, res.lon, res.status, h.address)
    return res


# --- Sammellauf im Hintergrund ------------------------------------------------

class Batch:
    def __init__(self):
        self.running = False
        self.done = 0
        self.total = 0
        self.error = ""
        self._mutex = threading.Lock()

    def start(self, connect=db.connect, force: bool = False) -> bool:
        with self._mutex:
            if self.running:
                return False
            self.running, self.done, self.total, self.error = True, 0, 0, ""
        threading.Thread(target=self._run, args=(connect, force), daemon=True).start()
        return True

    def _run(self, connect, force: bool):
        conn = connect()
        try:
            todo = [h for h in db.households(conn)
                    if h.address and (h.geo_stale or (force and h.geo in ("ungenau", "fehlt")))]
            self.total = len(todo)
            for h in todo:
                if force:
                    h.geo = ""
                update_household(conn, h)
                self.done += 1
        except GeoError as e:
            self.error = str(e)
        except Exception as e:  # pragma: no cover
            log.exception("Geokodierung fehlgeschlagen")
            self.error = f"Unerwarteter Fehler: {e}"
        finally:
            conn.close()
            self.running = False


batch = Batch()
