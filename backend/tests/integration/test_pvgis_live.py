"""LIVE tests against the real PVGIS service. Skipped unless RUN_LIVE_TESTS=1.

    RUN_LIVE_TESTS=1 python -m pytest tests/integration -v        (Git Bash)

They check two things the offline tests cannot:
  1. PVGIS output really is linear in peak power (the assumption that lets us
     ask for 1 kWp once and scale it to any installed capacity);
  2. the live service still returns the numbers we recorded on 2026-10-14
     (a drift detector: a failure means PVGIS changed its data or model).
"""
import os

import pytest

from solarscope.pvgis import PvcalcRequest, PvgisClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_TESTS") != "1",
        reason="live PVGIS tests are opt-in: set RUN_LIVE_TESTS=1",
    ),
]


def algiers(peak_power_kw: float) -> PvcalcRequest:
    return PvcalcRequest(
        latitude=36.75, longitude=3.06, peak_power_kw=peak_power_kw,
        system_loss_percent=14.0, tilt_deg=30.0, azimuth_deg=0.0,
        radiation_database="PVGIS-SARAH3",
    )


def test_energy_is_linear_in_peak_power():
    with PvgisClient() as client:
        one = client.fetch_pvcalc(algiers(1.0)).result
        five = client.fetch_pvcalc(algiers(5.0)).result
    print(f"\n1 kWp: {one.annual_energy_kwh}  5 kWp: {five.annual_energy_kwh}  "
          f"ratio: {five.annual_energy_kwh / one.annual_energy_kwh:.6f}")
    assert five.annual_energy_kwh == pytest.approx(5 * one.annual_energy_kwh, rel=1e-3)
    assert five.specific_yield_kwh_per_kwp == pytest.approx(
        one.specific_yield_kwh_per_kwp, rel=1e-3
    )


def test_live_service_still_matches_the_recording():
    with PvgisClient() as client:
        live = client.fetch_pvcalc(algiers(1.0)).result
    print(f"\nlive E_y: {live.annual_energy_kwh}  recorded E_y: 1550.98")
    assert live.annual_energy_kwh == pytest.approx(1550.98, rel=5e-3)
