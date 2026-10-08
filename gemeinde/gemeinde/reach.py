"""Erreichbarkeit der Gemeinde: Fahrzeit-Zonen (Isochronen) für 15 und 30 Minuten.

Berechnet über den freien Valhalla-Routingdienst der FOSSGIS (OpenStreetMap-
Daten). Übertragen wird nur der Standort der Gemeinde – keine Mitgliederdaten.
Ob ein Haushalt in einer Zone liegt, wird lokal ausgerechnet. Ergebnisse werden
im Datenordner zwischengespeichert, bis sich Standort oder Verkehrsmittel ändern.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import db
from .geo import USER_AGENT, GeoError

URL = "https://valhalla1.openstreetmap.de/isochrone"
MINUTES = (15, 30)
MODES = {
    "auto": ("auto", "Auto"),
    "rad": ("bicycle", "Fahrrad"),
    "fuss": ("pedestrian", "zu Fuß"),
}


def _cache_path():
    return db.db_path().parent / "isochronen.json"


def build_query(body: dict) -> str:
    # Kompaktes JSON und %20 statt "+": Valhalla dekodiert "+" nicht als Leerzeichen.
    return urllib.parse.urlencode({"json": json.dumps(body, separators=(",", ":"))},
                                  quote_via=urllib.parse.quote)


def _fetch(lat: float, lon: float, costing: str) -> dict:
    """Valhalla-Anfrage (in Tests ersetzt). Liefert GeoJSON-FeatureCollection."""
    body = {
        "locations": [{"lat": lat, "lon": lon}], "costing": costing,
        "contours": [{"time": m} for m in MINUTES], "polygons": True,
        "denoise": 0.5, "generalize": 100,
    }
    q = build_query(body)
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (OSError, ValueError) as e:
        raise GeoError(f"Routingdienst nicht erreichbar: {e}") from e


def zones(lat: float, lon: float, mode: str, refresh: bool = False) -> dict[int, dict]:
    """{Minuten: GeoJSON-Geometrie} – aus dem Zwischenspeicher oder frisch berechnet."""
    costing = MODES[mode][0]
    key = f"{lat:.5f},{lon:.5f},{costing}"
    path = _cache_path()
    try:
        cache = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        cache = {}
    if refresh or key not in cache:
        data = _fetch(lat, lon, costing)
        found = {}
        for f in data.get("features", []):
            m = int(round(f.get("properties", {}).get("contour", 0)))
            if m in MINUTES and f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon"):
                found[str(m)] = f["geometry"]
        if not found:
            raise GeoError("Der Routingdienst hat keine Zonen geliefert.")
        cache[key] = found
        # nur aktuelle Gemeinde-Standorte behalten
        cache = {k: v for k, v in cache.items() if k.startswith(f"{lat:.5f},{lon:.5f},")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(path)
    return {int(m): g for m, g in cache[key].items()}


# --- Punkt-in-Polygon ---------------------------------------------------------

def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(x: float, y: float, rings: list) -> bool:
    return bool(rings) and _in_ring(x, y, rings[0]) and not any(_in_ring(x, y, h) for h in rings[1:])


def contains(geometry: dict, lat: float, lon: float) -> bool:
    if geometry["type"] == "Polygon":
        return _in_polygon(lon, lat, geometry["coordinates"])
    return any(_in_polygon(lon, lat, p) for p in geometry["coordinates"])


def band(zs: dict[int, dict], lat: float, lon: float) -> int | None:
    """Kleinste Zone (15/30), in der der Punkt liegt; None = weiter weg."""
    for m in sorted(zs):
        if contains(zs[m], lat, lon):
            return m
    return None
