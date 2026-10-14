"""Phase 2 - explore PVGIS and NASA POWER responses for one location.

Every raw response is saved untouched next to a small metadata file, so we can
study the real payloads and later reuse a few of them as *recorded* test
fixtures (clearly separated from hand-made mocks).

Run from the backend/ folder:

    python scripts/explore_apis.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Algiers, decimal degrees (south and west are negative).
LAT, LON = 36.75, 3.06

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
HEADERS = {"User-Agent": "SolarScope-exploration/0.1"}

PVGIS_V5_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
# Inferred from the documented OpenAPI location (.../api/v6/openapi.json).
PVGIS_V6_URL = (
    "https://photovoltaic-geographic-information-system.ec.europa.eu"
    "/api/v6/performance/broadband"
)
POWER_MONTHLY_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"

# Same physical system everywhere: 1 kWp, 30 degrees tilt, facing south.
# Note the different conventions: PVGIS 5.x aspect 0 = south, PVGIS 6
# surface_orientation 180 = south, and "loss" (%) vs "system_efficiency" (0-1).
REQUESTS: dict[str, tuple[str, dict]] = {
    "pvgis_v5_3_pvcalc": (
        PVGIS_V5_URL,
        {
            "lat": LAT,
            "lon": LON,
            "peakpower": 1,
            "loss": 14,
            "angle": 30,
            "aspect": 0,
            "raddatabase": "PVGIS-SARAH3",
            "outputformat": "json",
        },
    ),
    "pvgis_v6_broadband": (
        PVGIS_V6_URL,
        {
            "latitude": LAT,
            "longitude": LON,
            "peak-power": 1,  # the hyphen is part of the documented name
            "surface_tilt": 30,
            "surface_orientation": 180,
            "start_time": "2020-01-01T00:00:00",
            "end_time": "2020-12-31T23:59:59",
            "frequency": "Hourly",
            "analysis": "Simple",
            "statistics": "true",
        },
    ),
    "nasa_power_monthly": (
        POWER_MONTHLY_URL,
        {
            "parameters": "ALLSKY_SFC_SW_DWN,T2M",
            "community": "SB",
            "longitude": LON,
            "latitude": LAT,
            "format": "JSON",
            "start": 2015,
            "end": 2024,
        },
    ),
}


def dig(data, *path):
    """Safely walk nested dicts; return None if any key is missing."""
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def summarise(name: str, payload) -> None:
    """Print a few facts. Missing keys are reported, never assumed."""
    if not isinstance(payload, dict):
        print(f"payload is a {type(payload).__name__}, not an object - inspect the file")
        return
    print("top-level keys:", list(payload.keys()))

    if name == "pvgis_v5_3_pvcalc":
        print("annual energy E_y (kWh/year for 1 kWp):",
              dig(payload, "outputs", "totals", "fixed", "E_y"))
        monthly = dig(payload, "outputs", "monthly", "fixed")
        print("monthly rows:", len(monthly) if isinstance(monthly, list) else "not found")

    elif name == "pvgis_v6_broadband":
        stats = payload.get("statistics")
        print("statistics keys:",
              list(stats.keys()) if isinstance(stats, dict) else "not found")

    elif name == "nasa_power_monthly":
        params = dig(payload, "properties", "parameter")
        if isinstance(params, dict):
            print("parameters:", list(params.keys()))
            first = next(iter(params.values()), {})
            print("first 3 entries of the first parameter:", list(first.items())[:3])
        else:
            print("properties.parameter: not found - inspect the file")
        print("header.fill_value:", dig(payload, "header", "fill_value"))


def fetch(name: str, url: str, params: dict) -> bool:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"\n=== {name} ===")

    started = time.perf_counter()
    try:
        response = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        print(f"REQUEST FAILED: {type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - started

    # Keep the body exactly as received.
    try:
        payload = response.json()
        suffix = "json"
    except ValueError:
        payload = None
        suffix = "txt"
    body_path = RAW_DIR / f"{name}_{stamp}.{suffix}"
    body_path.write_text(response.text, encoding="utf-8")

    meta = {
        "source": name,
        "url": str(response.request.url),
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "bytes": len(response.content),
        "elapsed_seconds": round(elapsed, 2),
        "retrieved_at_utc": stamp,
    }
    (RAW_DIR / f"{name}_{stamp}.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    print(f"HTTP {response.status_code} in {elapsed:.2f}s, {len(response.content):,} bytes")
    print(f"saved: {body_path.relative_to(RAW_DIR.parents[1])}")

    if not response.is_success:
        print(f"ERROR BODY (first 400 chars): {response.text[:400]}")
        return False

    summarise(name, payload)
    return True


def main() -> int:
    results = [fetch(name, url, params) for name, (url, params) in REQUESTS.items()]
    print(f"\n{sum(results)}/{len(results)} requests succeeded. Raw files in: {RAW_DIR}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
