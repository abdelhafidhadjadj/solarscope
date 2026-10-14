"""Error types. Callers can tell apart: my request is wrong, the service is
down, or the service answered with something we do not trust."""
from __future__ import annotations


class PvgisError(Exception):
    """Base class for every PVGIS adapter error."""


class PvgisRequestError(PvgisError):
    """PVGIS rejected the request (HTTP 4xx other than 429). Retrying will not help."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"PVGIS rejected the request (HTTP {status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class PvgisUnavailableError(PvgisError):
    """Retries were exhausted (timeouts, rate limit, overload or server errors)."""


class PvgisResponseError(PvgisError):
    """PVGIS answered, but the payload failed validation (shape, units, echo, sums)."""
