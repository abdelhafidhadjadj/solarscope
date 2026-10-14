"""Unit tests for sizing.py.

All numbers below are ILLUSTRATIVE test inputs chosen so results are easy to
verify by hand. They are not market data and not real module datasheets.
"""
import math

import pytest
from pydantic import ValidationError

from solarscope.solar.sizing import (
    ModuleSpec,
    SurfaceAssumptions,
    footprint_to_surface_area_m2,
    size_system,
)

MODULE = ModuleSpec(rated_power_wp=500, area_m2=2.0)


def surface(**overrides) -> SurfaceAssumptions:
    """No losses by default, flat roof, flush layout; override per test."""
    base = dict(
        roof_pitch_deg=0,
        obstacles_fraction=0,
        access_paths_fraction=0,
        shading_fraction=0,
        unsuitable_fraction=0,
        ground_coverage_ratio=1,
    )
    base.update(overrides)
    return SurfaceAssumptions(**base)


# ---- footprint -> surface ---------------------------------------------------

def test_flat_roof_surface_equals_footprint():
    assert footprint_to_surface_area_m2(100, 0) == pytest.approx(100)


def test_60_degree_pitch_doubles_the_surface():
    # cos(60 deg) = 0.5, so surface = footprint / 0.5
    assert footprint_to_surface_area_m2(100, 60) == pytest.approx(200)


# ---- panel count and capacity ------------------------------------------------

def test_exact_fit_gives_whole_number_of_panels():
    # 20 m2 / 2 m2 per module = 10 modules, 10 x 500 Wp = 5 kWp
    result = size_system(20, surface(), MODULE)
    assert result.max_panels_fit == 10
    assert result.panel_count == 10
    assert result.installed_capacity_kwp == pytest.approx(5.0)
    assert result.warnings == ()


def test_float_noise_does_not_lose_a_panel():
    odd_module = ModuleSpec(rated_power_wp=500, area_m2=2.2)
    # 13.2 / 2.2 is 5.999999999999999 in floating point; must still give 6
    assert size_system(13.2, surface(), odd_module).max_panels_fit == 6


def test_partial_module_is_not_counted():
    assert size_system(19.9, surface(), MODULE).max_panels_fit == 9


def test_exclusions_combine_multiplicatively():
    result = size_system(100, surface(obstacles_fraction=0.1, shading_fraction=0.2), MODULE)
    assert result.usable_area_m2 == pytest.approx(100 * 0.9 * 0.8)


def test_all_exclusions_at_100_percent_means_zero_panels():
    result = size_system(100, surface(unsuitable_fraction=1), MODULE)
    assert result.panel_count == 0
    assert result.installed_capacity_kwp == 0
    assert any("no panels fit" in w for w in result.warnings)


def test_ground_coverage_ratio_halves_the_count():
    full = size_system(100, surface(), MODULE).max_panels_fit
    half = size_system(100, surface(ground_coverage_ratio=0.5), MODULE).max_panels_fit
    assert full == 50
    assert half == 25


def test_pitched_roof_uses_surface_not_footprint():
    # 50 m2 footprint at 60 deg -> 100 m2 surface -> 50 modules of 2 m2
    assert size_system(50, surface(roof_pitch_deg=60), MODULE).max_panels_fit == 50


def test_area_smaller_than_one_module():
    result = size_system(1.5, surface(), MODULE)
    assert result.max_panels_fit == 0
    assert result.warnings


# ---- target capacity ---------------------------------------------------------

def test_target_below_fit_limits_panel_count():
    result = size_system(100, surface(), MODULE, target_capacity_kwp=5)
    assert result.panel_count == 10  # 5 kWp / 0.5 kWp per module
    assert result.installed_capacity_kwp == pytest.approx(5.0)
    assert result.max_panels_fit == 50


def test_target_rounds_up_to_whole_modules():
    result = size_system(100, surface(), MODULE, target_capacity_kwp=5.1)
    assert result.panel_count == 11  # 10.2 modules -> 11
    assert result.installed_capacity_kwp == pytest.approx(5.5)


def test_target_float_noise_does_not_add_a_panel():
    module = ModuleSpec(rated_power_wp=370, area_m2=1.8)
    # 4.07 kWp / 0.37 kWp = 11.000000000000002 in floats; must stay 11, not 12
    result = size_system(100, surface(), module, target_capacity_kwp=4.07)
    assert result.panel_count == 11


def test_target_above_fit_is_capped_with_warning():
    result = size_system(20, surface(), MODULE, target_capacity_kwp=50)
    assert result.panel_count == 10
    assert any("limited by available area" in w for w in result.warnings)


# ---- validation and extreme inputs ---------------------------------------------

@pytest.mark.parametrize("bad", [0, -5, math.nan, math.inf, -math.inf])
def test_invalid_footprint_is_rejected(bad):
    with pytest.raises(ValueError):
        size_system(bad, surface(), MODULE)


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf])
def test_invalid_target_is_rejected(bad):
    with pytest.raises(ValueError):
        size_system(100, surface(), MODULE, target_capacity_kwp=bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("obstacles_fraction", 1.2),
        ("shading_fraction", -0.1),
        ("roof_pitch_deg", 90),
        ("roof_pitch_deg", -1),
        ("ground_coverage_ratio", 0),
        ("ground_coverage_ratio", 1.5),
    ],
)
def test_out_of_range_assumptions_are_rejected(field, value):
    with pytest.raises(ValidationError):
        surface(**{field: value})


def test_assumptions_have_no_silent_defaults():
    with pytest.raises(ValidationError):
        SurfaceAssumptions()  # every field is required


def test_module_with_impossible_efficiency_is_rejected():
    # 500 W on 0.2 m2 would be 250% efficiency: almost certainly a unit typo
    with pytest.raises(ValidationError):
        ModuleSpec(rated_power_wp=500, area_m2=0.2)


def test_very_large_area_stays_finite_and_exact():
    # 1 km2 footprint, flat, no losses: 500,000 modules of 2 m2 = 250 MWp
    result = size_system(1_000_000, surface(), MODULE)
    assert result.panel_count == 500_000
    assert result.installed_capacity_kwp == pytest.approx(250_000)


def test_results_are_immutable():
    result = size_system(20, surface(), MODULE)
    with pytest.raises(ValidationError):
        result.panel_count = 99
