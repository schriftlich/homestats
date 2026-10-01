"""Beispieldaten zum Ausprobieren (nur wenn HOMESTATS_DEMO=1 und DB leer)."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from . import db
from .domain.estimate import month_weight
from .domain.model import GasFactor, Installment, MediumSettings, Meter, Reading, Tariff, add_months


def _reading_days(today: date) -> list[date]:
    rnd = random.Random(42)
    start = date(today.year - 2, 1, 1)
    days = [start]
    d = start
    while True:
        d = add_months(d, 1)
        jittered = d + timedelta(days=rnd.randint(-3, 3))
        if jittered > today:
            break
        days.append(jittered)
    return days


def _cumulate(days: list[date], rate) -> list[float]:
    vals, acc = [0.0], 0.0
    for a, b in zip(days, days[1:]):
        acc += sum(rate(a + timedelta(n)) for n in range((b - a).days))
        vals.append(acc)
    return vals


def seed(conn) -> None:
    today = date.today()
    y0 = today.year - 2
    days = _reading_days(today)
    rnd = random.Random(7)

    def noisy(base):
        return lambda d: base(d) * rnd.uniform(0.8, 1.2)

    # Strom: ~2.600 kWh/Jahr, im Winter etwas mehr
    strom = Meter(0, "strom", "Hausstrom", "kWh", "1EBZ0100123456")
    sid = db.save_meter(conn, strom)
    vals = _cumulate(days, noisy(lambda d: 7.1 * (1 + 0.2 * math.cos((d.month - 1) / 12 * 2 * math.pi))))
    for d, v in zip(days, vals):
        db.save_reading(conn, Reading(0, sid, d, round(18234.0 + v, 1)))

    # Gas: ~1.250 m³/Jahr mit Heizprofil
    gas = Meter(0, "gas", "Gaszähler Keller", "m³", "7GMT0012345678")
    gid = db.save_meter(conn, gas)
    vals = _cumulate(days, noisy(lambda d: 3.4 * month_weight("gas", d.month)))
    for d, v in zip(days, vals):
        db.save_reading(conn, Reading(0, gid, d, round(4321.0 + v, 3)))

    # Wasser mit Zählerwechsel (Eichfrist) im Sommer des Vorjahres
    change = date(today.year - 1, 7, 15)
    old = Meter(0, "wasser", "Wasser (alt)", "m³", "W-2017-4711", removed_on=change)
    new = Meter(0, "wasser", "Wasser", "m³", "W-2025-0815", installed_on=change, initial=0.0)
    oid, nid = db.save_meter(conn, old), db.save_meter(conn, new)
    wdays = sorted(set(days) | {change})
    vals = _cumulate(wdays, noisy(lambda d: 0.28))
    at_change = vals[wdays.index(change)]
    for d, v in zip(wdays, vals):
        if d <= change:
            db.save_reading(conn, Reading(0, oid, d, round(812.0 + v, 3), note="Ausbau" if d == change else ""))
        if d > change:
            db.save_reading(conn, Reading(0, nid, d, round(v - at_change, 3)))

    # Tarife mit Preiswechsel
    db.save_tariff(conn, Tariff(0, "strom", date(y0, 1, 1), 13.50, 0.362))
    db.save_tariff(conn, Tariff(0, "strom", date(y0 + 1, 3, 1), 14.90, 0.318))
    db.save_tariff(conn, Tariff(0, "gas", date(y0, 1, 1), 15.00, 0.121))
    db.save_tariff(conn, Tariff(0, "gas", date(y0 + 1, 1, 1), 16.20, 0.105))
    db.save_tariff(conn, Tariff(0, "wasser", date(y0, 1, 1), 9.00, 4.85))
    db.save_gas_factor(conn, GasFactor(0, date(y0, 1, 1), 0.9512, 11.312))
    db.save_gas_factor(conn, GasFactor(0, date(y0 + 1, 1, 1), 0.9498, 11.245))

    for medium, amount in (("strom", 72.0), ("gas", 135.0), ("wasser", 45.0)):
        db.save_installment(conn, Installment(0, medium, date(y0, 1, 1), amount))
    db.save_installment(conn, Installment(0, "gas", date(y0 + 1, 6, 1), 120.0))
    db.save_settings(conn, MediumSettings("gas", 6, 1, 12))
