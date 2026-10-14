# Phase 2 findings: external data sources

Retrieved 2026-10-14 from the live services. Test system: Algiers (36.75 N, 3.06 E),
1 kWp, 30 deg tilt, facing south, 14% system losses. Raw responses were saved to
`backend/data/raw/` (git-ignored); one PVGIS 5.3 response is kept as a test fixture in
`backend/tests/fixtures/`.

## Decision

**PVGIS 5.3 `PVcalc` is the production source for the MVP.** PVGIS v6 is deferred.

Reasons:
- The v5.3 API returned exactly the numbers the PVGIS web tool shows for the same inputs
  (yearly energy 1550.98 kWh, in-plane irradiation 2012.27 kWh/m2, year-to-year variability
  36.01 kWh, losses -2.51 / +0.79 / -8.78 / -22.92 %). This confirms the API matches the
  published tool. It does not prove the model matches reality.
- It is a documented, versioned service with stated limits.
- It gives a 2005-2023 multi-year average plus year-to-year variability; v6 returned a single
  requested year with zero variability.
- v6 describes itself as "actively under development" and changed its JSON keys on
  23 July 2026 with no transition period.
- Hourly series for the later self-consumption model are documented for v5.3 as well
  (`seriescalc` with `pvcalculation=1`). Not exercised yet.

## PVGIS 5.3 (`https://re.jrc.ec.europa.eu/api/v5_3/PVcalc`)

- GET only. Browser (AJAX) calls are not allowed, so every call goes through our backend.
- Limits: 30 calls/second per IP (HTTP 429); overload returns HTTP 529 ("repeat after a while").
- Conventions: azimuth 0 = south, 90 = west, -90 = east; losses are a percentage.
- Data used in the recorded response: radiation `PVGIS-SARAH3`, meteorology `ERA5`,
  years 2005-2023, elevation 123 m (DEM-calculated).
- Quirk: `l_spec` is the string `"0.79"` while sibling fields are numbers. The adapter coerces it.
- Monthly values are rounded to 2 decimals; they sum to within 0.01 of the annual value.
- Output is linear in peak power by design; the live test `test_energy_is_linear_in_peak_power`
  checks it against the real service (run it with `RUN_LIVE_TESTS=1`).

## PVGIS v6 (`.../api/v6/performance/broadband`), for later

- The URL path inferred from the OpenAPI file worked (HTTP 200, 1.2 s).
- Angles and coordinates come back in **radians** by default (`angle_output_units`).
- Nested sections label units inconsistently (`unit` vs `units`; kWh vs Wh vs Wh/kWp).
- Documented coverage: 2014-2024, 75 N to 60 S.
- The 2020 request returned 8,782 hourly values, but a leap year has 8,784. Cause not investigated.
- Elevation 93 m, versus 123 m in v5.3 (different terrain data).
- Annual energy 1625.66 kWh (year 2020) versus 1550.98 kWh (v5.3, 2005-2023 mean): +4.8%.
  In-plane irradiation differs by only +0.5%. The loss breakdown differs:
  temperature/irradiance -5.66% (v6) versus -8.78% (v5.3), reflectivity -1.68% versus
  angle-of-incidence -2.51%. One year versus a 19-year mean (v5.3 year-to-year SD is 36 kWh)
  explains part of the gap; the module model is a likely second factor. Not isolated yet.

## NASA POWER monthly (`/api/temporal/monthly/point`)

- At most 20 parameters per request. API version reported: v2.10.0.
- Units from the response header: `ALLSKY_SFC_SW_DWN` = **W m-2** (a mean irradiance, NOT
  kWh/m2/day); `T2M` = degrees C.
- Missing values are `-999.0` and must never enter an average.
- Keys are `YYYYMM`. **`YYYY13` is the annual value, not a month**; it must be excluded from
  monthly series. For 2015 it equals the day-weighted mean of the 12 months (207.4 W/m2).
- Time standard LST. Sources: MERRA2 and SYN1DEG.
- This is horizontal-plane irradiance. Convert to energy: W/m2 x 24 h x days / 1000 = kWh/m2.
  2015-2024 mean is 205.1 W/m2, about 1797 kWh/m2/year. PVGIS reports 2012 kWh/m2 on the
  30 deg plane, a ratio of 1.12, which is a plausible tilt gain (a consistency check only).
- Temperature cross-check for 2020: PVGIS v6 19.47 C versus NASA 19.62 C annual; monthly
  values differ by -1.4 to +1.3 C (different reanalyses and grids). The Climate Explorer
  methodology page must state this.
- Docs warn that repeatedly requesting the same grid cell can get an IP blocked: cache by
  location.

## Rules adopted for the engine

1. Never trust a unit from memory: read it from the response metadata, and fail if it changes.
2. Validate that the response echoes the inputs we sent.
3. Treat sentinel values (-999.0) and annual pseudo-months (`YYYY13`) explicitly.
4. Keep raw payloads, URL and retrieval time for every call.
5. Keep v6 behind its own adapter, if and when it is added.

## Not yet verified

- Data licence and attribution wording for PVGIS and NASA POWER (read before publication).
- `seriescalc` (hourly) behaviour and size.
- Why v6 returned 8,782 hourly values for 2020.
- The cause of the 4.8% v5.3 versus v6 difference.
