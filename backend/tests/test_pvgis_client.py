"""Tests for PvgisClient retry and error behaviour.

MOCK data: every test here uses httpx.MockTransport, so no network is touched.
Successful bodies are the RECORDED real response from tests/fixtures/; the
errors (429, 529, timeouts, bad JSON) are simulated by the mock handlers.
"""
import json
from pathlib import Path

import httpx
import pytest

from solarscope.pvgis import (
    PvcalcRequest,
    PvgisClient,
    PvgisRequestError,
    PvgisResponseError,
    PvgisUnavailableError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pvgis_v5_3_pvcalc_algiers_recorded.json"
RECORDED = json.loads(FIXTURE.read_text(encoding="utf-8"))

REQUEST = PvcalcRequest(
    latitude=36.75, longitude=3.06, peak_power_kw=1.0,
    system_loss_percent=14.0, tilt_deg=30.0, azimuth_deg=0.0,
    radiation_database="PVGIS-SARAH3",
)


def make_client(handler, **kwargs):
    """Client wired to a mock handler; `sleeps` records requested waits."""
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = PvgisClient(http_client=http, sleep=sleeps.append, **kwargs)
    return client, sleeps


def test_success_returns_validated_result_and_provenance():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=RECORDED)

    client, sleeps = make_client(handler)
    fetch = client.fetch_pvcalc(REQUEST)

    assert fetch.result.annual_energy_kwh == 1550.98
    assert fetch.attempts == 1
    assert sleeps == []
    assert fetch.raw == RECORDED                       # raw payload kept for storage
    assert fetch.retrieved_at_utc.tzinfo is not None   # timezone-aware timestamp
    assert "v5_3/PVcalc" in fetch.url

    params = dict(seen[0].url.params)
    assert params["lat"] == "36.75" and params["lon"] == "3.06"
    assert params["peakpower"] == "1.0" and params["loss"] == "14.0"
    assert params["angle"] == "30.0" and params["aspect"] == "0.0"
    assert params["outputformat"] == "json" and params["raddatabase"] == "PVGIS-SARAH3"


def test_rate_limit_429_is_retried_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json=RECORDED)

    client, sleeps = make_client(handler)
    fetch = client.fetch_pvcalc(REQUEST)
    assert fetch.attempts == 2
    assert sleeps == [1.0]


def test_overload_529_exhausts_retries_with_exponential_backoff():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(529)

    client, sleeps = make_client(handler, max_attempts=4)
    with pytest.raises(PvgisUnavailableError, match="HTTP 529"):
        client.fetch_pvcalc(REQUEST)
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]   # no sleep after the final attempt


def test_timeout_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, json=RECORDED)

    client, sleeps = make_client(handler)
    assert client.fetch_pvcalc(REQUEST).attempts == 2
    assert sleeps == [1.0]


def test_retry_after_header_is_honoured_and_capped():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json=RECORDED)

    client, sleeps = make_client(handler)
    client.fetch_pvcalc(REQUEST)
    assert sleeps == [7.0, 60.0]   # 7 s as asked; 3600 s capped at 60 s


def test_client_error_400_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"message": "lat: out of range", "status": 400})

    client, sleeps = make_client(handler)
    with pytest.raises(PvgisRequestError) as excinfo:
        client.fetch_pvcalc(REQUEST)
    assert excinfo.value.status_code == 400
    assert "out of range" in str(excinfo.value)
    assert len(calls) == 1 and sleeps == []


def test_invalid_json_body_is_a_response_error():
    def handler(request):
        return httpx.Response(200, text="<html>maintenance</html>")

    client, _ = make_client(handler)
    with pytest.raises(PvgisResponseError, match="not valid JSON"):
        client.fetch_pvcalc(REQUEST)


def test_payload_that_fails_validation_is_a_response_error_not_a_retry():
    broken = json.loads(json.dumps(RECORDED))
    broken["inputs"]["pv_module"]["peak_power"] = 9.0   # MUTATED: service "substituted" a value
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=broken)

    client, sleeps = make_client(handler)
    with pytest.raises(PvgisResponseError, match="peak power"):
        client.fetch_pvcalc(REQUEST)
    assert len(calls) == 1 and sleeps == []


def test_max_attempts_must_be_positive():
    with pytest.raises(ValueError):
        PvgisClient(max_attempts=0)
