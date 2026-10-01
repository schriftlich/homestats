"""Schätzung des Tagesverbrauchs für Tage ohne Ablesung.

Reihenfolge:
1. Für Kalendermonate mit genug Historie (alle Vorjahre gemittelt) wird der
   tatsächliche Tagesschnitt dieses Monats genutzt ("Vorjahresprofil").
2. Sonst wird der Gesamtschnitt mit einem saisonalen Profil gewichtet.
   Für Gas ist das ein typisches Heizprofil, für Strom und Wasser flach.
"""
from __future__ import annotations

from datetime import date

from .series import DailySeries

# Relativer Monatsanteil am Jahres-Gasverbrauch eines Haushalts mit Heizung.
GAS_PROFILE = {1: 17, 2: 15, 3: 13, 4: 9, 5: 5, 6: 3, 7: 2.5, 8: 2.5, 9: 4, 10: 8, 11: 12, 12: 16}
MIN_DAYS_PER_MONTH = 10
# Tage aus längeren Ableseintervallen taugen nicht für ein Monatsprofil: Bei
# linearer Verteilung über z. B. 10 Monate landet Winterverbrauch im Sommer.
MAX_PROFILE_SPAN = 62


def month_weight(medium: str, month: int) -> float:
    """Gewicht je Tag im Monat, normiert auf Jahresmittel 1."""
    if medium != "gas":
        return 1.0
    mean = sum(GAS_PROFILE.values()) / 12
    return GAS_PROFILE[month] / mean


class Estimator:
    def __init__(
        self,
        series: DailySeries,
        medium: str,
        exclude: tuple[date, date] | None = None,
    ) -> None:
        self.medium = medium
        sums: dict[int, list[float]] = {}
        counts: dict[int, int] = {}
        total = [0.0, 0.0]
        weight_sum = 0.0
        self.days = 0
        for day, (main, nt, span) in series.items():
            if exclude and exclude[0] <= day < exclude[1]:
                continue
            if span <= MAX_PROFILE_SPAN:
                s = sums.setdefault(day.month, [0.0, 0.0])
                s[0] += main
                s[1] += nt
                counts[day.month] = counts.get(day.month, 0) + 1
            total[0] += main
            total[1] += nt
            weight_sum += month_weight(medium, day.month)
            self.days += 1
        self.month_rate = {
            m: [sums[m][0] / c, sums[m][1] / c] for m, c in counts.items() if c >= MIN_DAYS_PER_MONTH
        }
        self.k = [total[0] / weight_sum, total[1] / weight_sum] if weight_sum else [0.0, 0.0]

    @property
    def has_data(self) -> bool:
        return self.days > 0

    def rate(self, day: date) -> list[float]:
        if day.month in self.month_rate:
            return list(self.month_rate[day.month])
        w = month_weight(self.medium, day.month)
        return [self.k[0] * w, self.k[1] * w]
