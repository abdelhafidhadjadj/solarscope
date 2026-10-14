"""Energy bookkeeping: keeps kWp (capacity) and kWh (energy) apart.

PVGIS reports energy for a chosen peak power, and its model is linear in peak
power. So we can ask PVGIS once for 1 kWp, cache that, and scale it by the
installed capacity. Step 3.2 verifies this linearity against real PVGIS calls
instead of just trusting it.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def _require_finite_non_negative(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")


def specific_yield_kwh_per_kwp(annual_energy_kwh: float, installed_capacity_kwp: float) -> float:
    """Specific yield in kWh/kWp/year = annual energy / installed capacity."""
    _require_finite_non_negative("annual_energy_kwh", annual_energy_kwh)
    if not math.isfinite(installed_capacity_kwp) or installed_capacity_kwp <= 0:
        raise ValueError(
            f"installed_capacity_kwp must be a finite number > 0, got {installed_capacity_kwp!r}"
        )
    return annual_energy_kwh / installed_capacity_kwp


def scale_energy_kwh(energy_per_kwp_kwh: float, installed_capacity_kwp: float) -> float:
    """Energy (kWh) for a system, from the energy of 1 kWp over the same period."""
    _require_finite_non_negative("energy_per_kwp_kwh", energy_per_kwp_kwh)
    _require_finite_non_negative("installed_capacity_kwp", installed_capacity_kwp)
    return energy_per_kwp_kwh * installed_capacity_kwp


@dataclass(frozen=True)
class SeasonalSummary:
    """How much production varies across the year (months are 1 = January)."""

    best_month: int
    best_kwh: float
    worst_month: int
    worst_kwh: float
    # None when the worst month produces nothing (a ratio would be infinite).
    best_to_worst_ratio: float | None


def seasonal_summary(monthly_energy_kwh: Sequence[float]) -> SeasonalSummary:
    """Best and worst month and their ratio, from 12 monthly energies in kWh."""
    if len(monthly_energy_kwh) != 12:
        raise ValueError(f"expected 12 monthly values, got {len(monthly_energy_kwh)}")
    for value in monthly_energy_kwh:
        _require_finite_non_negative("monthly energy", value)

    best_index = max(range(12), key=lambda i: monthly_energy_kwh[i])
    worst_index = min(range(12), key=lambda i: monthly_energy_kwh[i])
    best, worst = monthly_energy_kwh[best_index], monthly_energy_kwh[worst_index]
    return SeasonalSummary(
        best_month=best_index + 1,
        best_kwh=best,
        worst_month=worst_index + 1,
        worst_kwh=worst,
        best_to_worst_ratio=None if worst == 0 else best / worst,
    )
