"""Tests for parsing and validating PVcalc responses.

RECORDED data: tests/fixtures/pvgis_v5_3_pvcalc_algiers_recorded.json is a real
PVGIS 5.3 response (retrieved 2026-10-14, Algiers 36.75 N / 3.06 E, 1 kWp,
14% losses, 30 deg tilt, south). Its values are asserted as-is.

MUTATED data: the failure tests below copy that recording and break ONE thing,
to prove the validator notices. Those mutated payloads are not real PVGIS output.
"""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from solarscope.pvgis import PvcalcRequest, PvgisResponseError, parse_pvcalc_response

FIXTURE = Path(__file__).parent / "fixtures" / "pvgis_v5_3_pvcalc_algiers_recorded.json"


def recorded() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def algiers_request(**overrides) -> PvcalcRequest:
    base = dict(
        latitude=36.75, longitude=3.06, peak_power_kw=1.0,
        system_loss_percent=14.0, tilt_deg=30.0, azimuth_deg=0.0,
        radiation_database="PVGIS-SARAH3",
    )
    base.update(overrides)
    return PvcalcRequest(**base)


# ---- the recorded response parses to the recorded numbers ---------------------

def test_recorded_response_values():
    result = parse_pvcalc_response(recorded(), algiers_request())
    assert result.annual_energy_kwh == 1550.98
    assert result.annual_poa_irradiation_kwh_m2 == 2012.27
    assert result.year_to_year_sd_kwh == 36.01
    assert len(result.monthly_energy_kwh) == 12
    assert result.monthly_energy_kwh[0] == 95.07      # January
    assert result.monthly_energy_kwh[6] == 165.41     # July
    assert result.radiation_database == "PVGIS-SARAH3"
    assert result.meteo_database == "ERA5"
    assert (result.year_min, result.year_max) == (2005, 2023)
    assert result.elevation_m == 123.0


def test_string_loss_value_is_coerced_to_a_number():
    # The real payload has "l_spec": "0.79" (a string); siblings are numbers.
    assert isinstance(recorded()["outputs"]["totals"]["fixed"]["l_spec"], str)
    result = parse_pvcalc_response(recorded(), algiers_request())
    assert result.loss_spectral_percent == 0.79
    assert result.loss_angle_of_incidence_percent == -2.51
    assert result.loss_temperature_irradiance_percent == -8.78
    assert result.loss_total_percent == -22.92


def test_specific_yield_for_one_kwp_equals_annual_energy():
    result = parse_pvcalc_response(recorded(), algiers_request())
    assert result.specific_yield_kwh_per_kwp == pytest.approx(1550.98)


def test_scaling_to_another_capacity():
    result = parse_pvcalc_response(recorded(), algiers_request())
    assert result.annual_energy_for_capacity_kwh(5) == pytest.approx(5 * 1550.98)
    monthly = result.monthly_energy_for_capacity_kwh(5)
    assert monthly[6] == pytest.approx(5 * 165.41)
    assert sum(monthly) == pytest.approx(5 * 1550.98, abs=0.5)  # monthly rounding only


def test_request_without_database_accepts_whatever_pvgis_used():
    result = parse_pvcalc_response(recorded(), algiers_request(radiation_database=None))
    assert result.radiation_database == "PVGIS-SARAH3"


def test_query_params_use_pvgis_5x_names_and_conventions():
    params = algiers_request().query_params()
    assert params == {
        "lat": 36.75, "lon": 3.06, "peakpower": 1.0, "loss": 14.0,
        "angle": 30.0, "aspect": 0.0, "outputformat": "json",
        "raddatabase": "PVGIS-SARAH3",
    }
    assert "raddatabase" not in algiers_request(radiation_database=None).query_params()


# ---- request validation -----------------------------------------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("latitude", 91), ("latitude", -90.5), ("longitude", 181),
        ("peak_power_kw", 0), ("peak_power_kw", -1),
        ("system_loss_percent", -1), ("system_loss_percent", 100),
        ("tilt_deg", -1), ("tilt_deg", 91),
        ("azimuth_deg", 181), ("latitude", float("nan")),
    ],
)
def test_invalid_request_is_rejected(field, value):
    with pytest.raises(ValidationError):
        algiers_request(**{field: value})


def test_system_loss_has_no_default():
    with pytest.raises(ValidationError):
        PvcalcRequest(latitude=36.75, longitude=3.06, tilt_deg=30, azimuth_deg=0)


# ---- the validator notices each kind of damage (MUTATED copies) ------------------

def _set(path, value):
    def mutate(payload):
        node = payload
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _drop_march(payload):
    rows = payload["outputs"]["monthly"]["fixed"]
    payload["outputs"]["monthly"]["fixed"] = [r for r in rows if r["month"] != 3]


def _duplicate_january(payload):
    rows = payload["outputs"]["monthly"]["fixed"]
    rows[1] = copy.deepcopy(rows[0])


def _relabel_february_as_january(payload):
    # Values untouched, so every sum still matches: only the month check can catch this.
    payload["outputs"]["monthly"]["fixed"][1]["month"] = 1


def _relabel_december_as_13(payload):
    payload["outputs"]["monthly"]["fixed"][11]["month"] = 13


def _negative_month_with_sum_preserved(payload):
    # January -10, February +10 more than before: the monthly sum is unchanged,
    # so only the negative-value check can catch this.
    rows = payload["outputs"]["monthly"]["fixed"]
    rows[1]["E_m"] = round(rows[1]["E_m"] + rows[0]["E_m"] + 10, 2)
    rows[0]["E_m"] = -10.0


def _nan_month(payload):
    # abs(nan - total) > tolerance is False, so a NaN would sail through the sum
    # check; only the finite-value check catches it.
    payload["outputs"]["monthly"]["fixed"][0]["E_m"] = float("nan")


def _delete_outputs(payload):
    del payload["outputs"]


MUTATIONS = {
    "echoed peak power differs": _set(("inputs", "pv_module", "peak_power"), 2.0),
    "echoed loss differs": _set(("inputs", "pv_module", "system_loss"), 10.0),
    "echoed tilt differs": _set(("inputs", "mounting_system", "fixed", "slope", "value"), 35),
    "echoed azimuth differs": _set(("inputs", "mounting_system", "fixed", "azimuth", "value"), 90),
    "echoed latitude differs": _set(("inputs", "location", "latitude"), 40.0),
    "radiation database differs": _set(("inputs", "meteo_data", "radiation_db"), "PVGIS-ERA5"),
    "monthly energy unit changed": _set(("meta", "outputs", "monthly", "variables", "E_m", "units"), "Wh/mo"),
    "annual energy unit changed": _set(("meta", "outputs", "totals", "variables", "E_y", "units"), "MWh/y"),
    "annual energy disagrees with months": _set(("outputs", "totals", "fixed", "E_y"), 1650.98),
    "annual irradiation disagrees with months": _set(("outputs", "totals", "fixed", "H(i)_y"), 2500.0),
    "negative annual energy": _set(("outputs", "totals", "fixed", "E_y"), -1550.98),
    "non-numeric energy": _set(("outputs", "monthly", "fixed", 0, "E_m"), "n/a"),
    "a month is missing": _drop_march,
    "a month is duplicated": _duplicate_january,
    "two rows share a month number": _relabel_february_as_january,
    "month number out of range": _relabel_december_as_13,
    "negative month, sum preserved": _negative_month_with_sum_preserved,
    "NaN in a month": _nan_month,
    "outputs section missing": _delete_outputs,
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_validator_rejects_damaged_payload(name):
    payload = recorded()
    MUTATIONS[name](payload)
    with pytest.raises(PvgisResponseError):
        parse_pvcalc_response(payload, algiers_request())


def test_non_object_payload_is_rejected():
    with pytest.raises(PvgisResponseError):
        parse_pvcalc_response([1, 2, 3], algiers_request())
