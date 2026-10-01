from datetime import date

import pytest

from homestats.domain.costs import Pricer, billing_period, forecast, installments_total, monthly
from homestats.domain.estimate import Estimator
from homestats.domain.model import GasFactor, Installment, MediumSettings, Meter, Reading, Tariff
from homestats.domain.plausibility import reading_warnings
from homestats.domain.series import daily_series

D = date
_rid = iter(range(1, 10_000))


def r(meter_id, day, value, nt=None):
    return Reading(next(_rid), meter_id, day, value, nt)


def total(series, start, end):
    return sum(v[0] + v[1] for d, v in series.items() if start <= d < end)


# --- Verbrauchsverteilung --------------------------------------------------

def test_linear_distribution_across_month_boundary():
    m = Meter(1, "wasser", "Wasser", "m³")
    # 15.01. -> 15.02.: 31 Tage, 31 m³ -> 1 m³/Tag
    s = daily_series([m], [r(1, D(2024, 1, 15), 100), r(1, D(2024, 2, 15), 131)])
    assert total(s, D(2024, 1, 1), D(2024, 2, 1)) == pytest.approx(17)  # 15.–31.01.
    assert total(s, D(2024, 2, 1), D(2024, 3, 1)) == pytest.approx(14)  # 01.–14.02.


def test_monthly_stats_mark_partial_months():
    m = Meter(1, "wasser", "Wasser", "m³")
    s = daily_series([m], [r(1, D(2024, 1, 15), 0), r(1, D(2024, 3, 1), 46)])
    stats = monthly(s, Pricer("wasser", "m³", [], []))
    assert not stats[(2024, 1)].complete
    assert stats[(2024, 2)].complete
    assert stats[(2024, 2)].consumption == pytest.approx(29)  # Schaltjahr
    assert sum(ms.consumption for ms in stats.values()) == pytest.approx(46)


def test_days_without_two_readings_are_not_covered():
    m = Meter(1, "strom", "Strom", "kWh")
    s = daily_series([m], [r(1, D(2024, 1, 1), 0)])
    assert s == {}


# --- Zählerwechsel ---------------------------------------------------------

def test_meter_change_is_seamless_and_never_negative():
    old = Meter(1, "gas", "Alt", "m³", removed_on=D(2024, 3, 1))
    new = Meter(2, "gas", "Neu", "m³", installed_on=D(2024, 3, 1), initial=0)
    readings = [
        r(1, D(2024, 1, 1), 5000),
        r(1, D(2024, 3, 1), 5060),  # Endstand alter Zähler
        r(2, D(2024, 4, 1), 31),  # neuer Zähler, Anfangsstand 0 aus Einbaudaten
    ]
    s = daily_series([old, new], readings)
    assert all(v[0] >= 0 for v in s.values())
    assert total(s, D(2024, 1, 1), D(2024, 4, 1)) == pytest.approx(91)
    # lückenlos: jeder Tag von 01.01. bis 31.03. ist abgedeckt
    assert len(s) == 91
    assert total(s, D(2024, 3, 1), D(2024, 4, 1)) == pytest.approx(31)


def test_meter_change_with_nonzero_initial_reading():
    new = Meter(2, "wasser", "Neu", "m³", installed_on=D(2024, 3, 1), initial=12.5)
    s = daily_series([new], [r(2, D(2024, 3, 11), 22.5)])
    assert total(s, D(2024, 3, 1), D(2024, 4, 1)) == pytest.approx(10)


def test_decreasing_reading_counts_as_zero_and_warns():
    m = Meter(1, "strom", "Strom", "kWh")
    readings = [r(1, D(2024, 1, 1), 1000), r(1, D(2024, 2, 1), 900)]
    s = daily_series([m], readings)
    assert total(s, D(2024, 1, 1), D(2024, 2, 1)) == 0
    w = reading_warnings(m, readings, s)
    assert "kleiner als der vorherige" in w[readings[1].id][0]


def test_dual_register_ht_nt():
    m = Meter(1, "strom", "Strom", "kWh", dual=True)
    s = daily_series([m], [r(1, D(2024, 1, 1), 100, 50), r(1, D(2024, 1, 11), 200, 70)])
    assert sum(v[0] for v in s.values()) == pytest.approx(100)
    assert sum(v[1] for v in s.values()) == pytest.approx(20)


# --- Kosten ----------------------------------------------------------------

def test_cost_full_month():
    m = Meter(1, "strom", "Strom", "kWh")
    s = daily_series([m], [r(1, D(2024, 4, 1), 0), r(1, D(2024, 5, 1), 300)])
    p = Pricer("strom", "kWh", [Tariff(1, "strom", D(2020, 1, 1), 12.0, 0.30)], [])
    ms = monthly(s, p)[(2024, 4)]
    assert ms.base_cost == pytest.approx(12.0)
    assert ms.energy_cost == pytest.approx(90.0)
    assert ms.cost == pytest.approx(102.0)


def test_price_change_mid_interval_is_prorated():
    m = Meter(1, "strom", "Strom", "kWh")
    # 30 Tage à 10 kWh, Preiswechsel am 16.04.: 15 Tage alt, 15 Tage neu
    s = daily_series([m], [r(1, D(2024, 4, 1), 0), r(1, D(2024, 5, 1), 300)])
    tariffs = [
        Tariff(1, "strom", D(2020, 1, 1), 10.0, 0.30),
        Tariff(2, "strom", D(2024, 4, 16), 20.0, 0.40),
    ]
    ms = monthly(s, Pricer("strom", "kWh", tariffs, []))[(2024, 4)]
    assert ms.energy_cost == pytest.approx(150 * 0.30 + 150 * 0.40)
    assert ms.base_cost == pytest.approx(10.0 * 15 / 30 + 20.0 * 15 / 30)


def test_ht_nt_prices():
    m = Meter(1, "strom", "Strom", "kWh", dual=True)
    s = daily_series([m], [r(1, D(2024, 4, 1), 0, 0), r(1, D(2024, 5, 1), 300, 150)])
    p = Pricer("strom", "kWh", [Tariff(1, "strom", D(2020, 1, 1), 0, 0.30, 0.20)], [])
    assert monthly(s, p)[(2024, 4)].energy_cost == pytest.approx(300 * 0.30 + 150 * 0.20)


def test_gas_conversion_with_factor_change():
    m = Meter(1, "gas", "Gas", "m³")
    s = daily_series([m], [r(1, D(2024, 4, 1), 0), r(1, D(2024, 5, 1), 300)])
    factors = [
        GasFactor(1, D(2020, 1, 1), 0.95, 11.0),
        GasFactor(2, D(2024, 4, 16), 0.96, 11.2),
    ]
    p = Pricer("gas", "m³", [Tariff(1, "gas", D(2020, 1, 1), 0, 0.10)], factors)
    ms = monthly(s, p)[(2024, 4)]
    kwh = 150 * 0.95 * 11.0 + 150 * 0.96 * 11.2
    assert ms.billed == pytest.approx(kwh)
    assert ms.consumption == pytest.approx(300)
    assert ms.energy_cost == pytest.approx(kwh * 0.10)


def test_missing_tariff_is_flagged():
    m = Meter(1, "wasser", "Wasser", "m³")
    s = daily_series([m], [r(1, D(2024, 4, 1), 0), r(1, D(2024, 5, 1), 3)])
    ms = monthly(s, Pricer("wasser", "m³", [], []))[(2024, 4)]
    assert ms.cost == 0 and ms.missing


# --- Hochrechnung / Abschläge ---------------------------------------------

def test_billing_period():
    st = MediumSettings("gas", 6, 1)
    assert billing_period(st, D(2024, 5, 31)) == (D(2023, 6, 1), D(2024, 6, 1))
    assert billing_period(st, D(2024, 6, 1)) == (D(2024, 6, 1), D(2025, 6, 1))


def test_installments_with_change_and_eleven_payments():
    inst = [Installment(1, "gas", D(2020, 1, 1), 100), Installment(2, "gas", D(2024, 7, 1), 120)]
    period = (D(2024, 1, 1), D(2025, 1, 1))
    assert installments_total(inst, period) == pytest.approx(6 * 100 + 6 * 120)
    assert installments_total(inst, period, 11) == pytest.approx(6 * 100 + 5 * 120)


def test_forecast_flat_profile():
    m = Meter(1, "strom", "Strom", "kWh")
    # 2024 (Schaltjahr) erste 91 Tage mit 10 kWh/Tag
    s = daily_series([m], [r(1, D(2024, 1, 1), 0), r(1, D(2024, 4, 1), 910)])
    p = Pricer("strom", "kWh", [Tariff(1, "strom", D(2020, 1, 1), 10.0, 0.30)], [])
    inst = [Installment(1, "strom", D(2020, 1, 1), 100.0)]
    f = forecast(s, p, inst, MediumSettings("strom"), D(2024, 4, 1))
    assert f.covered_days == 91 and f.total_days == 366
    assert f.consumption == pytest.approx(3660)
    assert f.cost == pytest.approx(3660 * 0.30 + 12 * 10.0)
    assert f.balance == pytest.approx(1200 - f.cost)  # negativ = Nachzahlung


def test_gas_forecast_uses_seasonal_profile():
    m = Meter(1, "gas", "Gas", "kWh")
    # Nur Sommerdaten: linear hochgerechnet wäre das viel zu wenig
    s = daily_series([m], [r(1, D(2024, 6, 1), 0), r(1, D(2024, 9, 1), 920)])
    p = Pricer("gas", "kWh", [Tariff(1, "gas", D(2020, 1, 1), 0, 0.10)], [])
    f = forecast(s, p, [], MediumSettings("gas"), D(2024, 9, 1))
    linear = 920 / 92 * 366
    assert f.consumption > 2.5 * linear


def test_estimator_prefers_previous_year_month():
    m = Meter(1, "wasser", "Wasser", "m³")
    s = daily_series([m], [r(1, D(2023, 7, 1), 0), r(1, D(2023, 8, 1), 62), r(1, D(2023, 9, 1), 93), r(1, D(2023, 10, 1), 123)])
    est = Estimator(s, "wasser")
    assert est.rate(D(2024, 7, 10))[0] == pytest.approx(2.0)
    assert est.rate(D(2024, 9, 10))[0] == pytest.approx(1.0)


def test_long_intervals_do_not_define_month_profile():
    m = Meter(1, "gas", "Gas", "m³")
    # 10 Monate ohne Ablesung: linear verteilt wäre der Sommer viel zu hoch
    s = daily_series([m], [r(1, D(2025, 6, 1), 0), r(1, D(2026, 4, 1), 900)])
    est = Estimator(s, "gas")
    assert not est.month_rate  # kein Monat aus kurzem Intervall
    assert est.rate(D(2026, 7, 15))[0] < est.rate(D(2026, 1, 15))[0] / 4


# --- Plausibilität ---------------------------------------------------------

def test_deviation_warning():
    m = Meter(1, "wasser", "Wasser", "m³")
    readings = [r(1, D(2024, 1, 1), 0), r(1, D(2024, 4, 1), 91), r(1, D(2024, 5, 1), 181)]
    s = daily_series([m], readings)
    w = reading_warnings(m, readings, s)
    assert readings[2].id in w and "weicht stark ab" in w[readings[2].id][0]
    assert readings[1].id not in w  # zu wenig Vergleichshistorie


def test_no_deviation_warning_for_normal_consumption():
    m = Meter(1, "wasser", "Wasser", "m³")
    readings = [r(1, D(2024, 1, 1), 0), r(1, D(2024, 4, 1), 91), r(1, D(2024, 5, 1), 121)]
    assert reading_warnings(m, readings, daily_series([m], readings)) == {}
