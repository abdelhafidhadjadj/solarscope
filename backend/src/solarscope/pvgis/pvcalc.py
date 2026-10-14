"""PVGIS 5.3 `PVcalc` (fixed mounting): request model, response parsing and checks.

Why so many checks? The service is a public API that has changed before (PVGIS
v6 renamed its JSON keys on 23 July 2026), and the real v5.3 payload already
has a quirk: `l_spec` arrives as the STRING "0.79" while its siblings are
numbers. We validate what we depend on and fail loudly instead of passing a
silently wrong number to the cost and finance engine.

Checks performed on every response:
  * the fields we use exist and are numeric (strings like "0.79" are coerced);
  * exactly 12 distinct months;
  * the echoed inputs (peak power, loss, slope, azimuth, location, database)
    equal what we asked for, so PVGIS did not silently substitute anything;
  * the unit labels in `meta` are the ones we assume (kWh/mo, kWh/y, ...);
  * the 12 monthly energies add up to the annual energy within rounding;
  * all energies are finite and non-negative.

Only fixed mounting is supported. Tracking systems need other output paths.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from solarscope.pvgis.errors import PvgisResponseError
from solarscope.solar.energy import scale_energy_kwh, specific_yield_kwh_per_kwp

# PVGIS rounds each monthly value to 2 decimals, so 12 months can differ from
# the annual figure by at most 12 x 0.005 = 0.06. We allow 0.1.
SUM_TOLERANCE = 0.1
# Echoed coordinates / angles must match what we sent (PVGIS echoed them exactly).
ECHO_TOLERANCE = 1e-3


class PvcalcRequest(BaseModel):
    """What we ask PVGIS. Conventions follow PVGIS 5.x, NOT v6:
    azimuth 0 = south, 90 = west, -90 = east; loss is a percentage.
    """

    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    peak_power_kw: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    # Required, no default: system losses are an assumption the user must see.
    system_loss_percent: float = Field(ge=0, lt=100, allow_inf_nan=False)
    tilt_deg: float = Field(ge=0, le=90, allow_inf_nan=False)
    azimuth_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    # None = let PVGIS choose its default database for the location.
    radiation_database: str | None = None

    def query_params(self) -> dict[str, str | float]:
        params: dict[str, str | float] = {
            "lat": self.latitude,
            "lon": self.longitude,
            "peakpower": self.peak_power_kw,
            "loss": self.system_loss_percent,
            "angle": self.tilt_deg,
            "aspect": self.azimuth_deg,
            "outputformat": "json",
        }
        if self.radiation_database:
            params["raddatabase"] = self.radiation_database
        return params


# ---- raw payload models (only the fields we use; extras are ignored) ----------

class _Location(BaseModel):
    model_config = ConfigDict(extra="ignore")
    latitude: float
    longitude: float
    elevation: float


class _MeteoData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    radiation_db: str
    meteo_db: str
    year_min: int
    year_max: int


class _Angle(BaseModel):
    model_config = ConfigDict(extra="ignore")
    value: float


class _Fixed(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slope: _Angle
    azimuth: _Angle


class _MountingSystem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fixed: _Fixed


class _PvModule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    peak_power: float
    system_loss: float


class _Inputs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    location: _Location
    meteo_data: _MeteoData
    mounting_system: _MountingSystem
    pv_module: _PvModule


class _MonthlyRow(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    month: int
    energy_kwh: float = Field(alias="E_m")
    poa_irradiation_kwh_m2: float = Field(alias="H(i)_m")


class _Totals(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    energy_year_kwh: float = Field(alias="E_y")
    poa_irradiation_year_kwh_m2: float = Field(alias="H(i)_y")
    energy_year_sd_kwh: float = Field(alias="SD_y")
    loss_angle_of_incidence_percent: float = Field(alias="l_aoi")
    # Arrives as the string "0.79" in the real payload; pydantic coerces it.
    loss_spectral_percent: float = Field(alias="l_spec")
    loss_temperature_irradiance_percent: float = Field(alias="l_tg")
    loss_total_percent: float = Field(alias="l_total")


class _Monthly(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fixed: list[_MonthlyRow]


class _TotalsWrapper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fixed: _Totals


class _Outputs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    monthly: _Monthly
    totals: _TotalsWrapper


# Unit labels our code relies on, as paths inside the payload.
_EXPECTED_UNITS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("meta", "inputs", "pv_module", "variables", "peak_power", "units"), "kW"),
    (("meta", "inputs", "pv_module", "variables", "system_loss", "units"), "%"),
    (("meta", "inputs", "mounting_system", "fields", "slope", "units"), "degree"),
    (("meta", "inputs", "mounting_system", "fields", "azimuth", "units"), "degree"),
    (("meta", "outputs", "monthly", "variables", "E_m", "units"), "kWh/mo"),
    (("meta", "outputs", "monthly", "variables", "H(i)_m", "units"), "kWh/m2/mo"),
    (("meta", "outputs", "totals", "variables", "E_y", "units"), "kWh/y"),
    (("meta", "outputs", "totals", "variables", "H(i)_y", "units"), "kWh/m2/y"),
    (("meta", "outputs", "totals", "variables", "SD_y", "units"), "kWh"),
)


class PvcalcResult(BaseModel):
    """Validated PVGIS result for the requested peak power."""

    model_config = ConfigDict(frozen=True)

    request: PvcalcRequest
    annual_energy_kwh: float
    monthly_energy_kwh: tuple[float, ...]  # January .. December
    annual_poa_irradiation_kwh_m2: float
    monthly_poa_irradiation_kwh_m2: tuple[float, ...]
    year_to_year_sd_kwh: float
    loss_angle_of_incidence_percent: float
    loss_spectral_percent: float
    loss_temperature_irradiance_percent: float
    loss_total_percent: float
    radiation_database: str
    meteo_database: str
    year_min: int
    year_max: int
    elevation_m: float

    @property
    def specific_yield_kwh_per_kwp(self) -> float:
        """kWh per kWp per year, for the requested system losses and orientation."""
        return specific_yield_kwh_per_kwp(self.annual_energy_kwh, self.request.peak_power_kw)

    def annual_energy_for_capacity_kwh(self, installed_capacity_kwp: float) -> float:
        """Scale to another installed capacity (linear in kWp; see the live test)."""
        return scale_energy_kwh(self.specific_yield_kwh_per_kwp, installed_capacity_kwp)

    def monthly_energy_for_capacity_kwh(self, installed_capacity_kwp: float) -> tuple[float, ...]:
        peak = self.request.peak_power_kw
        return tuple(
            scale_energy_kwh(month / peak, installed_capacity_kwp)
            for month in self.monthly_energy_kwh
        )


def _fail(message: str) -> PvgisResponseError:
    return PvgisResponseError(message)


def _dig(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise _fail(f"missing '{'.'.join(path)}' in the PVGIS response")
        node = node[key]
    return node


def _check_units(payload: dict[str, Any]) -> None:
    for path, expected in _EXPECTED_UNITS:
        found = _dig(payload, path)
        if found != expected:
            raise _fail(
                f"unit changed at '{'.'.join(path)}': expected {expected!r}, got {found!r}"
            )


def _check_echo(inputs: _Inputs, request: PvcalcRequest) -> None:
    pairs = (
        ("latitude", inputs.location.latitude, request.latitude, ECHO_TOLERANCE),
        ("longitude", inputs.location.longitude, request.longitude, ECHO_TOLERANCE),
        ("peak power (kW)", inputs.pv_module.peak_power, request.peak_power_kw, 1e-9),
        ("system loss (%)", inputs.pv_module.system_loss, request.system_loss_percent, 1e-9),
        ("tilt (deg)", inputs.mounting_system.fixed.slope.value, request.tilt_deg, ECHO_TOLERANCE),
        (
            "azimuth (deg)",
            inputs.mounting_system.fixed.azimuth.value,
            request.azimuth_deg,
            ECHO_TOLERANCE,
        ),
    )
    for name, echoed, asked, tolerance in pairs:
        if not math.isclose(echoed, asked, abs_tol=tolerance):
            raise _fail(f"PVGIS echoed {name}={echoed} but we asked for {asked}")
    if request.radiation_database and inputs.meteo_data.radiation_db != request.radiation_database:
        raise _fail(
            f"PVGIS used radiation database {inputs.meteo_data.radiation_db!r} "
            f"but we asked for {request.radiation_database!r}"
        )


def parse_pvcalc_response(payload: Any, request: PvcalcRequest) -> PvcalcResult:
    """Validate a decoded PVcalc JSON payload and convert it to a PvcalcResult."""
    if not isinstance(payload, dict):
        raise _fail(f"expected a JSON object, got {type(payload).__name__}")

    _check_units(payload)

    try:
        inputs = _Inputs.model_validate(payload.get("inputs"))
        outputs = _Outputs.model_validate(payload.get("outputs"))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise _fail(f"unexpected PVGIS payload at '{where}': {first['msg']}") from exc

    _check_echo(inputs, request)

    rows = sorted(outputs.monthly.fixed, key=lambda row: row.month)
    if [row.month for row in rows] != list(range(1, 13)):
        raise _fail(
            "expected exactly the months 1..12, got "
            f"{[row.month for row in outputs.monthly.fixed]}"
        )

    totals = outputs.totals.fixed
    monthly_energy = tuple(row.energy_kwh for row in rows)
    monthly_poa = tuple(row.poa_irradiation_kwh_m2 for row in rows)

    for label, values in (
        ("monthly energy", monthly_energy),
        ("monthly irradiation", monthly_poa),
        ("annual energy", (totals.energy_year_kwh,)),
        ("annual irradiation", (totals.poa_irradiation_year_kwh_m2,)),
    ):
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise _fail(f"{label} contains a negative or non-finite value")

    if abs(sum(monthly_energy) - totals.energy_year_kwh) > SUM_TOLERANCE:
        raise _fail(
            f"monthly energies sum to {sum(monthly_energy):.2f} kWh but the annual "
            f"total is {totals.energy_year_kwh:.2f} kWh"
        )
    if abs(sum(monthly_poa) - totals.poa_irradiation_year_kwh_m2) > SUM_TOLERANCE:
        raise _fail(
            f"monthly irradiation sums to {sum(monthly_poa):.2f} but the annual "
            f"total is {totals.poa_irradiation_year_kwh_m2:.2f} kWh/m2"
        )

    return PvcalcResult(
        request=request,
        annual_energy_kwh=totals.energy_year_kwh,
        monthly_energy_kwh=monthly_energy,
        annual_poa_irradiation_kwh_m2=totals.poa_irradiation_year_kwh_m2,
        monthly_poa_irradiation_kwh_m2=monthly_poa,
        year_to_year_sd_kwh=totals.energy_year_sd_kwh,
        loss_angle_of_incidence_percent=totals.loss_angle_of_incidence_percent,
        loss_spectral_percent=totals.loss_spectral_percent,
        loss_temperature_irradiance_percent=totals.loss_temperature_irradiance_percent,
        loss_total_percent=totals.loss_total_percent,
        radiation_database=inputs.meteo_data.radiation_db,
        meteo_database=inputs.meteo_data.meteo_db,
        year_min=inputs.meteo_data.year_min,
        year_max=inputs.meteo_data.year_max,
        elevation_m=inputs.location.elevation,
    )
