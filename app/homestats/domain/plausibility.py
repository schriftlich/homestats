"""Plausibilitätsprüfung von Ablesungen (nur Warnungen, nie blockierend)."""
from __future__ import annotations

from .estimate import Estimator
from .model import Meter, Reading, daterange
from .series import DailySeries, meter_points

MAX_DEVIATION = 0.5  # ±50 % gegenüber dem erwarteten Verbrauch
MIN_HISTORY_DAYS = 60


def reading_warnings(
    meter: Meter,
    readings: list[Reading],
    medium_series: DailySeries,
    fmt=lambda v: f"{v:.2f}",
) -> dict[int, list[str]]:
    """Warnungen je Ablesung-ID eines Zählers.

    `medium_series` ist die Tagesreihe des ganzen Mediums; für den Vergleich
    wird der zu prüfende Zeitraum selbst herausgerechnet.
    """
    out: dict[int, list[str]] = {}
    pts = meter_points(meter, readings)
    for a, b in zip(pts, pts[1:]):
        if b.reading_id is None:
            continue
        msgs: list[str] = []
        if b.value < a.value or (meter.dual and b.value_nt < a.value_nt):
            msgs.append(
                f"Stand ist kleiner als der vorherige ({fmt(a.value)} am {a.day:%d.%m.%Y})."
            )
        days = (b.day - a.day).days
        if days > 0 and not msgs:
            actual = (b.value - a.value) + ((b.value_nt - a.value_nt) if meter.dual else 0.0)
            est = Estimator(medium_series, meter.medium, exclude=(a.day, b.day))
            if est.days >= MIN_HISTORY_DAYS:
                expected = sum(sum(est.rate(d)) for d in daterange(a.day, b.day))
                if expected > 0:
                    dev = actual / expected - 1
                    if abs(dev) > MAX_DEVIATION:
                        msgs.append(
                            f"Verbrauch weicht stark ab: {fmt(actual)} {meter.unit} statt "
                            f"erwartet ca. {fmt(expected)} {meter.unit} ({dev * 100:+.0f} %)."
                        )
        if msgs:
            out[b.reading_id] = msgs
    return out
