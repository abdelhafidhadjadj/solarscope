"""Sizing: from a drawn footprint to a panel count and installed kWp.

Naming convention (units are part of every name):
    *_m2   area in square metres
    *_wp   module rated power in watts-peak
    *_kwp  installed capacity in kilowatts-peak (rating under reference
           conditions, NOT instantaneous power and NOT energy)
    *_deg  angle in degrees

Nothing here estimates energy. Energy comes from PVGIS (step 3.2) and is
combined with the capacity computed here in `energy.py`.

Every assumption is a REQUIRED field with no default, so no optimistic value
is applied silently. The UI will later pre-fill them from visible settings.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Tolerance so 22.8 / 2.28 (= 10.000000000000002 in floats) still gives 10.
_EPS = 1e-9


def _require_positive_finite(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")


class ModuleSpec(BaseModel):
    """One PV module, as given on its datasheet."""

    model_config = ConfigDict(frozen=True)

    rated_power_wp: float = Field(gt=0, allow_inf_nan=False)
    area_m2: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _efficiency_must_be_physical(self) -> "ModuleSpec":
        # Efficiency at the 1000 W/m2 reference irradiance can never reach 100%.
        # This only catches order-of-magnitude typos (e.g. 0.228 instead of 2.28).
        efficiency = self.rated_power_wp / (self.area_m2 * 1000.0)
        if efficiency >= 1.0:
            raise ValueError(
                f"rated_power_wp / area_m2 implies {efficiency:.0%} efficiency; "
                "check the module power and area units"
            )
        return self


class SurfaceAssumptions(BaseModel):
    """Everything that turns a drawn polygon into area that can hold modules."""

    model_config = ConfigDict(frozen=True)

    # 0 = flat. For a pitched roof the drawn polygon is the horizontal
    # footprint, so the real roof surface is larger by 1 / cos(pitch).
    roof_pitch_deg: float = Field(ge=0, le=70, allow_inf_nan=False)

    # Fractions of the roof surface lost to each cause, each in [0, 1].
    # They combine multiplicatively, so the total can never exceed 100%.
    obstacles_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    access_paths_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    shading_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    unsuitable_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)

    # Module area / ground area. Use 1.0 for modules flush on a pitched roof.
    # For tilted rows on a flat roof or land, row spacing makes this < 1.
    ground_coverage_ratio: float = Field(gt=0, le=1, allow_inf_nan=False)


class SizingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    footprint_area_m2: float
    roof_surface_area_m2: float
    usable_area_m2: float
    module_area_available_m2: float
    max_panels_fit: int
    panel_count: int
    module_area_used_m2: float
    installed_capacity_kwp: float
    warnings: tuple[str, ...]


def footprint_to_surface_area_m2(footprint_area_m2: float, roof_pitch_deg: float) -> float:
    """Real roof surface from its horizontal footprint: footprint / cos(pitch)."""
    _require_positive_finite("footprint_area_m2", footprint_area_m2)
    return footprint_area_m2 / math.cos(math.radians(roof_pitch_deg))


def size_system(
    footprint_area_m2: float,
    surface: SurfaceAssumptions,
    module: ModuleSpec,
    target_capacity_kwp: float | None = None,
) -> SizingResult:
    """Estimate how many modules fit and the resulting installed capacity.

    If `target_capacity_kwp` is given, the panel count is the smaller of what
    fits and what the target needs (rounded UP to whole modules, so the result
    can exceed the target by less than one module).
    """
    roof_surface = footprint_to_surface_area_m2(footprint_area_m2, surface.roof_pitch_deg)

    keep = (
        (1 - surface.obstacles_fraction)
        * (1 - surface.access_paths_fraction)
        * (1 - surface.shading_fraction)
        * (1 - surface.unsuitable_fraction)
    )
    usable = roof_surface * keep
    module_area_available = usable * surface.ground_coverage_ratio
    max_fit = math.floor(module_area_available / module.area_m2 + _EPS)

    warnings: list[str] = []
    count = max_fit

    if target_capacity_kwp is not None:
        _require_positive_finite("target_capacity_kwp", target_capacity_kwp)
        wanted = math.ceil(target_capacity_kwp * 1000.0 / module.rated_power_wp - _EPS)
        if wanted > max_fit:
            warnings.append(
                f"Target of {target_capacity_kwp} kWp needs {wanted} modules but only "
                f"{max_fit} fit; capacity is limited by available area."
            )
        count = min(max_fit, wanted)

    if max_fit == 0:
        warnings.append("Usable area is smaller than one module; no panels fit.")

    return SizingResult(
        footprint_area_m2=footprint_area_m2,
        roof_surface_area_m2=roof_surface,
        usable_area_m2=usable,
        module_area_available_m2=module_area_available,
        max_panels_fit=max_fit,
        panel_count=count,
        module_area_used_m2=count * module.area_m2,
        installed_capacity_kwp=count * module.rated_power_wp / 1000.0,
        warnings=tuple(warnings),
    )
