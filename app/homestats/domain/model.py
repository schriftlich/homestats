"""Fachliche Datenklassen – unabhängig von Datenbank und Web."""
from __future__ import annotations

import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Protocol, Sequence, TypeVar

MEDIA = ("strom", "gas", "wasser")
MEDIUM_LABEL = {"strom": "Strom", "gas": "Gas", "wasser": "Wasser"}
DEFAULT_UNIT = {"strom": "kWh", "gas": "m³", "wasser": "m³"}


@dataclass(frozen=True)
class Meter:
    id: int
    medium: str
    name: str
    unit: str
    meter_number: str = ""
    dual: bool = False  # Strom mit zwei Zählwerken (HT/NT)
    installed_on: date | None = None
    initial: float = 0.0  # Anfangsstand beim Einbau
    initial_nt: float = 0.0
    removed_on: date | None = None


@dataclass(frozen=True)
class Reading:
    id: int
    meter_id: int
    day: date
    value: float
    value_nt: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Tariff:
    id: int
    medium: str
    valid_from: date
    base_month: float  # Grundpreis €/Monat
    price: float  # Arbeitspreis €/Einheit (Gas: €/kWh), bei HT/NT der HT-Preis
    price_nt: float | None = None


@dataclass(frozen=True)
class GasFactor:
    id: int
    valid_from: date
    zustandszahl: float
    brennwert: float  # kWh/m³

    @property
    def factor(self) -> float:
        return self.zustandszahl * self.brennwert


@dataclass(frozen=True)
class Installment:
    id: int
    medium: str
    valid_from: date
    amount: float  # Abschlag €/Monat


@dataclass(frozen=True)
class MediumSettings:
    medium: str
    billing_start_month: int = 1
    billing_start_day: int = 1
    installments_per_period: int = 12


class _HasValidFrom(Protocol):
    valid_from: date


T = TypeVar("T", bound=_HasValidFrom)


def valid_at(items: Sequence[T], day: date) -> T | None:
    """Liefert den Eintrag, der am Tag `day` gilt (letztes valid_from <= day).

    `items` muss nach valid_from aufsteigend sortiert sein.
    """
    idx = bisect_right([i.valid_from for i in items], day)
    return items[idx - 1] if idx else None


def days_in_month(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def daterange(start: date, end: date) -> Iterator[date]:
    """Tage von start (inklusive) bis end (exklusive)."""
    for n in range((end - start).days):
        yield start + timedelta(days=n)


def add_months(day: date, months: int) -> date:
    m = day.month - 1 + months
    year, month = day.year + m // 12, m % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def converts_to_kwh(medium: str, unit: str) -> bool:
    return medium == "gas" and unit.strip().lower() in ("m³", "m3", "cbm")
