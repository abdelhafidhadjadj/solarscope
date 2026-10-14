"""HTTP client for PVGIS 5.3 PVcalc.

Documented service behaviour we handle (PVGIS non-interactive API page):
  * 429 = more than 30 calls/second from one IP -> wait and retry;
  * 529 = server overloaded, "repeat the request after a while" -> retry;
  * GET only, and browsers (AJAX) are not allowed -> this runs server-side only.

Retries use exponential backoff (1 s, 2 s, 4 s by default) without jitter, which
is fine for a single-user app; a `Retry-After` header is honoured up to 60 s.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from solarscope.pvgis.errors import (
    PvgisRequestError,
    PvgisResponseError,
    PvgisUnavailableError,
)
from solarscope.pvgis.pvcalc import PvcalcRequest, PvcalcResult, parse_pvcalc_response

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER_SECONDS = 60.0


@dataclass(frozen=True)
class PvcalcFetch:
    """A validated result plus everything needed to record where it came from."""

    result: PvcalcResult
    raw: dict[str, Any]
    url: str
    retrieved_at_utc: datetime
    elapsed_seconds: float
    attempts: int


class PvgisClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._base_url = base_url
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=15.0),
            headers={"User-Agent": "SolarScope/0.1"},
        )
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "PvgisClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _wait_before_retry(self, attempt: int, response: httpx.Response | None) -> None:
        delay = self._backoff_seconds * (2 ** (attempt - 1))
        if response is not None:
            header = response.headers.get("Retry-After", "")
            if header.isdigit():
                delay = max(delay, min(float(header), MAX_RETRY_AFTER_SECONDS))
        self._sleep(delay)

    def fetch_pvcalc(self, request: PvcalcRequest) -> PvcalcFetch:
        started = time.perf_counter()
        last_problem = "no attempt made"

        for attempt in range(1, self._max_attempts + 1):
            response: httpx.Response | None = None
            try:
                response = self._http.get(self._base_url, params=request.query_params())
            except httpx.TransportError as exc:  # timeouts, DNS, connection resets
                last_problem = f"{type(exc).__name__}: {exc}"
                logger.warning("PVGIS attempt %d/%d failed: %s", attempt, self._max_attempts, last_problem)
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last_problem = f"HTTP {response.status_code}"
                    logger.warning(
                        "PVGIS attempt %d/%d got HTTP %d", attempt, self._max_attempts, response.status_code
                    )
                elif response.is_success:
                    return self._build_fetch(response, request, started, attempt)
                else:
                    # Other 4xx: our request is wrong, retrying cannot fix it.
                    raise PvgisRequestError(response.status_code, response.text[:500])

            if attempt < self._max_attempts:
                self._wait_before_retry(attempt, response)

        raise PvgisUnavailableError(
            f"PVGIS did not answer successfully after {self._max_attempts} attempts "
            f"(last problem: {last_problem})"
        )

    def _build_fetch(
        self,
        response: httpx.Response,
        request: PvcalcRequest,
        started: float,
        attempts: int,
    ) -> PvcalcFetch:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PvgisResponseError(
                f"response is not valid JSON (first 200 chars: {response.text[:200]!r})"
            ) from exc

        result = parse_pvcalc_response(payload, request)
        elapsed = time.perf_counter() - started
        logger.info(
            "PVGIS ok in %.2fs after %d attempt(s): %.2f kWh/year for %s kWp",
            elapsed, attempts, result.annual_energy_kwh, request.peak_power_kw,
        )
        return PvcalcFetch(
            result=result,
            raw=payload,
            url=str(response.request.url),
            retrieved_at_utc=datetime.now(timezone.utc),
            elapsed_seconds=elapsed,
            attempts=attempts,
        )
