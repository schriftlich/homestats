"""Bündelt DB-Daten und Rechenkern zu Auswertungen für die Oberfläche."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import db, fmt
from .domain.costs import Forecast, MonthStat, Pricer, forecast, monthly
from .domain.model import DEFAULT_UNIT, MEDIA, Meter, Reading, add_months
from .domain.plausibility import reading_warnings
from .domain.series import DailySeries, daily_series


@dataclass
class MediumData:
    medium: str
    unit: str
    billed_unit: str
    meters: list[Meter]
    readings: list[Reading]
    series: DailySeries
    pricer: Pricer

    @property
    def has_meters(self) -> bool:
        return bool(self.meters)


def medium_unit(meters: list[Meter], medium: str) -> str:
    active = [m for m in meters if m.removed_on is None] or meters
    return active[-1].unit if active else DEFAULT_UNIT[medium]


def load_medium(conn, medium: str) -> MediumData:
    meters = db.meters(conn, medium)
    readings = db.readings(conn, [m.id for m in meters]) if meters else []
    unit = medium_unit(meters, medium)
    pricer = Pricer(medium, unit, db.tariffs(conn, medium), db.gas_factors(conn) if medium == "gas" else [])
    return MediumData(
        medium, unit, pricer.billed_unit(unit), meters, readings, daily_series(meters, readings), pricer
    )


def warnings_for_meter(data: MediumData, meter: Meter) -> dict[int, list[str]]:
    return reading_warnings(meter, data.readings, data.series, fmt=lambda v: fmt.num(v, 2))


def all_warnings(data: MediumData) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for m in data.meters:
        out.update(warnings_for_meter(data, m))
    return out


@dataclass
class Dashboard:
    data: MediumData
    months: dict[tuple[int, int], MonthStat]
    forecast: Forecast
    last_month: MonthStat | None
    last_month_prev_year: MonthStat | None
    chart: dict
    table: list[tuple[MonthStat, MonthStat | None]]


def dashboard(conn, medium: str, today: date | None = None) -> Dashboard:
    today = today or date.today()
    data = load_medium(conn, medium)
    months = monthly(data.series, data.pricer)
    fc = forecast(data.series, data.pricer, db.installments(conn, medium), db.settings(conn, medium), today)

    complete = [k for k, ms in sorted(months.items()) if ms.complete]
    last_key = complete[-1] if complete else None
    last = months.get(last_key) if last_key else None
    prev = months.get((last_key[0] - 1, last_key[1])) if last_key else None

    # Rollierende 12 Monate bis zum aktuellen Monat, jeweils mit Vorjahresmonat
    first = add_months(date(today.year, today.month, 1), -11)
    keys = [(d.year, d.month) for d in (add_months(first, n) for n in range(12))]

    def val(k, attr):
        ms = months.get(k)
        return round(getattr(ms, attr), 2) if ms else None

    chart = {
        "labels": [f"{fmt.MONTHS[m - 1]} {str(y)[2:]}" for y, m in keys],
        "consumption": [val(k, "consumption") for k in keys],
        "consumption_prev": [val((k[0] - 1, k[1]), "consumption") for k in keys],
        "cost": [val(k, "cost") for k in keys],
        "cost_prev": [val((k[0] - 1, k[1]), "cost") for k in keys],
        "partial": [bool(months.get(k)) and not months[k].complete for k in keys],
        "unit": data.unit,
    }
    table = [(ms, months.get((ms.year - 1, ms.month))) for _, ms in sorted(months.items(), reverse=True)]
    return Dashboard(data, months, fc, last, prev, chart, table)


def overview(conn, today: date | None = None) -> list[Dashboard]:
    return [dashboard(conn, m, today) for m in MEDIA]
