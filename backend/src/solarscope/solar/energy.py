"""Energy bookkeeping: keeps kWp (capacity) and kWh (energy) apart.

PVGIS reports energy for a chosen peak power, and its model is linear in peak
power. So we can ask PVGIS once for 1 kWp, cache that, and scale it by the
installed capacity. Step 3.2 verifies this linearity against real PVGIS calls
instead of just trusting it.
"""
from __future__ import annotations

import math


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
