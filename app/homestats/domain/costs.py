"""Kostenberechnung, Monatsauswertung und Hochrechnung gegen Abschläge.

Gerechnet wird tagesgenau: Jeder Tag bekommt den an diesem Tag gültigen
Tarif und (bei Gas) die gültigen Umrechnungsfaktoren. Preiswechsel mitten
im Monat oder zwischen zwei Ablesungen werden dadurch automatisch
zeitanteilig korrekt berücksichtigt.

Kosten je Tag = Grundpreis/Tage des Monats + Verbrauch × Arbeitspreis
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .estimate import Estimator
from .model import (
    GasFactor,
    Installment,
    MediumSettings,
    Tariff,
    add_months,
    converts_to_kwh,
    daterange,
    days_in_month,
    valid_at,
)
from .series import DailySeries

MISSING_TARIFF = "Tarif fehlt"
MISSING_FACTOR = "Brennwert/Zustandszahl fehlt"


@dataclass
class DayCost:
    consumption: float  # in Zählereinheit, HT+NT
    billed: float  # abgerechnete Menge (Gas: kWh, sonst = consumption)
    energy_cost: float
    base_cost: float
    missing: set[str] = field(default_factory=set)

    @property
    def cost(self) -> float:
        return self.energy_cost + self.base_cost


class Pricer:
    def __init__(self, medium: str, unit: str, tariffs: list[Tariff], factors: list[GasFactor]):
        self.medium = medium
        self.tariffs = sorted(tariffs, key=lambda t: t.valid_from)
        self.factors = sorted(factors, key=lambda f: f.valid_from)
        self.convert = converts_to_kwh(medium, unit)

    def billed_unit(self, unit: str) -> str:
        return "kWh" if self.convert else unit

    def day(self, day: date, main: float, nt: float) -> DayCost:
        missing: set[str] = set()
        factor = 1.0
        if self.convert:
            gf = valid_at(self.factors, day)
            if gf is None:
                missing.add(MISSING_FACTOR)
                factor = 0.0
            else:
                factor = gf.factor
        billed_main, billed_nt = main * factor, nt * factor
        t = valid_at(self.tariffs, day)
        if t is None:
            missing.add(MISSING_TARIFF)
            energy = base = 0.0
        else:
            price_nt = t.price_nt if t.price_nt is not None else t.price
            energy = billed_main * t.price + billed_nt * price_nt
            base = t.base_month / days_in_month(day)
        return DayCost(main + nt, billed_main + billed_nt, energy, base, missing)


@dataclass
class MonthStat:
    year: int
    month: int
    days: int
    covered_days: int = 0
    consumption: float = 0.0
    consumption_nt: float = 0.0
    billed: float = 0.0
    energy_cost: float = 0.0
    base_cost: float = 0.0
    missing: set[str] = field(default_factory=set)

    @property
    def cost(self) -> float:
        return self.energy_cost + self.base_cost

    @property
    def complete(self) -> bool:
        return self.covered_days == self.days


def monthly(series: DailySeries, pricer: Pricer) -> dict[tuple[int, int], MonthStat]:
    """Verbrauch und Kosten je Kalendermonat (nur Tage mit Ablesungsdaten)."""
    out: dict[tuple[int, int], MonthStat] = {}
    for day in sorted(series):
        main, nt = series[day]
        key = (day.year, day.month)
        ms = out.get(key)
        if ms is None:
            ms = out[key] = MonthStat(day.year, day.month, days_in_month(day))
        dc = pricer.day(day, main, nt)
        ms.covered_days += 1
        ms.consumption += dc.consumption
        ms.consumption_nt += nt
        ms.billed += dc.billed
        ms.energy_cost += dc.energy_cost
        ms.base_cost += dc.base_cost
        ms.missing |= dc.missing
    return out


def billing_period(settings: MediumSettings, today: date) -> tuple[date, date]:
    """Abrechnungszeitraum [start, ende) der `today` enthält."""
    start = date(today.year, settings.billing_start_month, settings.billing_start_day)
    if start > today:
        start = date(today.year - 1, settings.billing_start_month, settings.billing_start_day)
    return start, add_months(start, 12)


def installments_total(
    installments: list[Installment], period: tuple[date, date], count: int = 12
) -> float:
    """Summe der Abschläge im Zeitraum. Fällig ist je Monat ab Periodenbeginn;
    bei z. B. 11 Abschlägen entfällt der letzte Monat."""
    items = sorted(installments, key=lambda i: i.valid_from)
    total = 0.0
    for n in range(min(count, 12)):
        inst = valid_at(items, add_months(period[0], n))
        if inst:
            total += inst.amount
    return total


@dataclass
class Forecast:
    start: date
    end: date
    total_days: int
    covered_days: int
    actual_consumption: float
    estimated_consumption: float
    actual_cost: float
    estimated_cost: float
    installments: float
    has_data: bool
    missing: set[str]

    @property
    def last_day(self) -> date:
        return self.end - timedelta(days=1)

    @property
    def consumption(self) -> float:
        return self.actual_consumption + self.estimated_consumption

    @property
    def cost(self) -> float:
        return self.actual_cost + self.estimated_cost

    @property
    def balance(self) -> float:
        """> 0: Guthaben, < 0: Nachzahlung."""
        return self.installments - self.cost


def forecast(
    series: DailySeries,
    pricer: Pricer,
    installments: list[Installment],
    settings: MediumSettings,
    today: date,
) -> Forecast:
    """Hochrechnung des aktuellen Abrechnungszeitraums.

    Tage mit Ablesungsdaten zählen mit ihrem echten Verbrauch, alle anderen
    Tage des Zeitraums (Zukunft und Lücken) werden geschätzt.
    """
    start, end = billing_period(settings, today)
    est = Estimator(series, pricer.medium)
    covered = 0
    act_c = est_c = act_cost = est_cost = 0.0
    missing: set[str] = set()
    for day in daterange(start, end):
        if day in series:
            main, nt = series[day]
            dc = pricer.day(day, main, nt)
            covered += 1
            act_c += dc.consumption
            act_cost += dc.cost
        else:
            main, nt = est.rate(day)
            dc = pricer.day(day, main, nt)
            est_c += dc.consumption
            est_cost += dc.cost
        missing |= dc.missing
    return Forecast(
        start=start,
        end=end,
        total_days=(end - start).days,
        covered_days=covered,
        actual_consumption=act_c,
        estimated_consumption=est_c,
        actual_cost=act_cost,
        estimated_cost=est_cost,
        installments=installments_total(installments, (start, end), settings.installments_per_period),
        has_data=est.has_data,
        missing=missing,
    )
