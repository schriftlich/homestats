"""Verbrauchsreihe je Medium.

Der Verbrauch zwischen zwei Ablesungen wird linear auf die Tage dazwischen
verteilt. Daraus entsteht eine tagesgenaue Reihe, die sich beliebig auf
Kalendermonate oder Abrechnungszeiträume summieren lässt.

Zählerwechsel: Der Verbrauch wird immer innerhalb eines Zählers aus
Stand-Differenzen berechnet und erst danach je Medium zusammengeführt.
Der Sprung vom Endstand des alten auf den Anfangsstand des neuen Zählers
taucht deshalb nie als (negativer) Verbrauch auf.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .model import Meter, Reading

# Tag -> [Verbrauch Hauptzählwerk, Verbrauch NT-Zählwerk]
DailySeries = dict[date, list[float]]


@dataclass(frozen=True)
class Point:
    day: date
    value: float
    value_nt: float
    reading_id: int | None  # None = Anfangsstand aus den Zählerdaten


def meter_points(meter: Meter, readings: Iterable[Reading]) -> list[Point]:
    """Alle Stützstellen eines Zählers, chronologisch, inkl. Anfangsstand."""
    pts = [
        Point(r.day, r.value, r.value_nt or 0.0, r.id)
        for r in sorted(readings, key=lambda r: r.day)
        if r.meter_id == meter.id
    ]
    if meter.installed_on and (not pts or meter.installed_on < pts[0].day):
        pts.insert(0, Point(meter.installed_on, meter.initial, meter.initial_nt, None))
    return pts


def add_meter_to_series(series: DailySeries, meter: Meter, readings: Iterable[Reading]) -> None:
    pts = meter_points(meter, readings)
    for a, b in zip(pts, pts[1:]):
        days = (b.day - a.day).days
        if days <= 0:
            continue
        # Negative Differenzen (Tippfehler, Zählerüberlauf) werden nicht als
        # Verbrauch gezählt – die Plausibilitätsprüfung weist darauf hin.
        rate = max(b.value - a.value, 0.0) / days
        rate_nt = max(b.value_nt - a.value_nt, 0.0) / days if meter.dual else 0.0
        for n in range(days):
            slot = series.setdefault(a.day + timedelta(days=n), [0.0, 0.0])
            slot[0] += rate
            slot[1] += rate_nt


def daily_series(meters: Iterable[Meter], readings: Iterable[Reading]) -> DailySeries:
    """Tagesreihe aller übergebenen Zähler (ein Medium) zusammengeführt.

    Ein Tag ist genau dann in der Reihe enthalten, wenn mindestens ein Zähler
    ihn durch zwei Stützstellen abdeckt.
    """
    readings = list(readings)
    series: DailySeries = {}
    for m in meters:
        add_meter_to_series(series, m, readings)
    return series
