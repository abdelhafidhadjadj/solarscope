"""Unit tests for energy.py (kWp vs kWh bookkeeping).

Numbers are illustrative test inputs, not real PVGIS results.
"""
import math

import pytest

from solarscope.solar.energy import scale_energy_kwh, specific_yield_kwh_per_kwp


def test_specific_yield_is_energy_per_kwp():
    # 7,500 kWh from a 5 kWp system -> 1,500 kWh/kWp/year
    assert specific_yield_kwh_per_kwp(7500, 5) == pytest.approx(1500)


def test_scaling_is_linear_in_capacity():
    per_kwp = 1234.5
    assert scale_energy_kwh(per_kwp, 5) == pytest.approx(5 * per_kwp)
    assert scale_energy_kwh(per_kwp, 10) == pytest.approx(2 * scale_energy_kwh(per_kwp, 5))


def test_round_trip_yield_and_scaling():
    energy = scale_energy_kwh(1400, 3.3)
    assert specific_yield_kwh_per_kwp(energy, 3.3) == pytest.approx(1400)


def test_zero_capacity_gives_zero_energy():
    assert scale_energy_kwh(1500, 0) == 0


def test_yield_with_zero_capacity_is_rejected():
    with pytest.raises(ValueError):
        specific_yield_kwh_per_kwp(1000, 0)


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf])
def test_invalid_energy_is_rejected(bad):
    with pytest.raises(ValueError):
        scale_energy_kwh(bad, 5)
    with pytest.raises(ValueError):
        specific_yield_kwh_per_kwp(bad, 5)


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf])
def test_invalid_capacity_is_rejected(bad):
    with pytest.raises(ValueError):
        scale_energy_kwh(1500, bad)


# ---- seasonal summary ---------------------------------------------------------

from solarscope.solar.energy import seasonal_summary  # noqa: E402

# Recorded PVGIS 5.3 monthly energies for 1 kWp in Algiers (see fixtures/).
ALGIERS_MONTHLY = [95.07, 103.85, 132.73, 139.87, 151.51, 156.49,
                   165.41, 160.41, 137.79, 127.45, 91.5, 88.91]


def test_seasonal_summary_on_recorded_algiers_values():
    summary = seasonal_summary(ALGIERS_MONTHLY)
    assert (summary.best_month, summary.best_kwh) == (7, 165.41)
    assert (summary.worst_month, summary.worst_kwh) == (12, 88.91)
    assert summary.best_to_worst_ratio == pytest.approx(165.41 / 88.91)


def test_seasonal_ratio_is_none_when_worst_month_is_zero():
    values = [0.0] + [10.0] * 11
    summary = seasonal_summary(values)
    assert summary.worst_month == 1
    assert summary.best_to_worst_ratio is None


def test_seasonal_summary_needs_exactly_12_values():
    with pytest.raises(ValueError):
        seasonal_summary([1.0] * 11)


def test_seasonal_summary_rejects_negative_values():
    with pytest.raises(ValueError):
        seasonal_summary([1.0] * 11 + [-1.0])
