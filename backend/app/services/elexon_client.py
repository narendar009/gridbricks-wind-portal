"""
Elexon BMRS API client — corrected to use the non-stream historical endpoints.

Documented schema findings (from direct API inspection, October 2026):
- FUELHH endpoint: GET /datasets/FUELHH
  params: settlementDateFrom (YYYY-MM-DD), settlementDateTo (YYYY-MM-DD),
          fuelType (WIND), format (json)
  response: {"data": [{dataset, publishTime, startTime, settlementDate,
                        settlementPeriod, fuelType, generation}, ...]}
  interval: half-hourly (48 per settlement day)

- WINDFOR endpoint: GET /datasets/WINDFOR
  params: publishDateTimeFrom, publishDateTimeTo (ISO UTC),
          startDateTimeFrom, startDateTimeTo (ISO UTC), format (json)
  response: {"data": [{dataset, publishTime, startTime, generation}, ...]}
  interval: hourly (each forecast publication covers ~48-hour ahead window)

The /stream variants of these endpoints return only live/current data,
ignoring date parameters, and are therefore not suitable for historical analysis.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import (
    ELEXON_BASE_URL,
    ELEXON_MAX_RETRIES,
    ELEXON_RETRY_BACKOFF,
    ELEXON_TIMEOUT_CONNECT,
    ELEXON_TIMEOUT_READ,
)

logger = logging.getLogger(__name__)

_FUELHH_ENDPOINT = "/datasets/FUELHH"
_WINDFOR_ENDPOINT = "/datasets/WINDFOR"

# How many days per FUELHH request chunk (API appears to support multi-day ranges)
_FUELHH_CHUNK_DAYS = 7

# How many days per WINDFOR publish-window chunk
_WINDFOR_CHUNK_DAYS = 7


def _make_client() -> httpx.Client:
    return httpx.Client(
        base_url=ELEXON_BASE_URL,
        timeout=httpx.Timeout(
            connect=ELEXON_TIMEOUT_CONNECT,
            read=ELEXON_TIMEOUT_READ,
            write=10,
            pool=10,
        ),
        headers={"Accept": "application/json"},
    )


def _get_with_retry(
    client: httpx.Client, endpoint: str, params: dict[str, Any]
) -> list[dict]:
    """
    Perform GET with bounded retries and exponential back-off.
    Handles both list responses (stream) and {"data": [...]} object responses.
    """
    last_exc: Exception | None = None
    for attempt in range(1, ELEXON_MAX_RETRIES + 1):
        try:
            resp = client.get(endpoint, params=params)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                logger.warning(
                    "Rate-limited; sleeping %ds (attempt %d/%d)",
                    retry_after, attempt, ELEXON_MAX_RETRIES,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            raw = resp.json()
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict) and "data" in raw:
                data = raw["data"]
                if isinstance(data, list):
                    return data
                raise ValueError(f"Unexpected 'data' type: {type(data).__name__}")
            raise ValueError(
                f"Unexpected response type from {endpoint}: {type(raw).__name__}"
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            wait = ELEXON_RETRY_BACKOFF ** attempt
            logger.warning(
                "Transient error attempt %d/%d for %s: %s; retrying in %.1fs",
                attempt, ELEXON_MAX_RETRIES, endpoint, exc, wait,
            )
            time.sleep(wait)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP %d from %s: %s",
                exc.response.status_code, endpoint, exc.response.text[:300],
            )
            raise
    raise RuntimeError(
        f"All {ELEXON_MAX_RETRIES} attempts failed for {endpoint}"
    ) from last_exc


def fetch_fuelhh_wind(start_date: date, end_date: date) -> list[dict]:
    """
    Fetch actual half-hourly wind generation (FUELHH, fuelType=WIND).

    Queries the non-stream historical endpoint in weekly chunks.
    Returns a flat list of raw API records (each has startTime, generation, etc.).
    """
    records: list[dict] = []
    current = start_date
    with _make_client() as client:
        while current <= end_date:
            chunk_end = min(
                current + timedelta(days=_FUELHH_CHUNK_DAYS - 1), end_date
            )
            logger.info(
                "Fetching FUELHH WIND settlementDateFrom=%s settlementDateTo=%s",
                current, chunk_end,
            )
            chunk = _get_with_retry(
                client,
                _FUELHH_ENDPOINT,
                {
                    "settlementDateFrom": current.isoformat(),
                    "settlementDateTo": chunk_end.isoformat(),
                    "fuelType": "WIND",
                    "format": "json",
                },
            )
            logger.debug("  → %d records (%s to %s)", len(chunk), current, chunk_end)
            records.extend(chunk)
            current = chunk_end + timedelta(days=1)
    return records


def fetch_windfor(from_dt: datetime, to_dt: datetime) -> list[dict]:
    """
    Fetch all WINDFOR forecast publications whose publishTime falls in
    [from_dt, to_dt] (UTC datetimes).

    The strategy: chunk the publish-time window into manageable periods,
    fetching all forecast publications. The startDateTimeFrom/To parameters
    are set broadly to capture all delivery intervals we care about.
    """
    records: list[dict] = []
    from_dt = from_dt.replace(tzinfo=timezone.utc) if from_dt.tzinfo is None else from_dt
    to_dt = to_dt.replace(tzinfo=timezone.utc) if to_dt.tzinfo is None else to_dt

    # Delivery interval range we care about: analysis period
    start_dt_from = from_dt  # forecasts target delivery from ~start of analysis
    start_dt_to = to_dt + timedelta(days=2)  # with a small buffer

    with _make_client() as client:
        current = from_dt
        while current < to_dt:
            chunk_end = min(current + timedelta(days=_WINDFOR_CHUNK_DAYS), to_dt)
            logger.info(
                "Fetching WINDFOR publishDateTimeFrom=%s publishDateTimeTo=%s",
                current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            chunk = _get_with_retry(
                client,
                _WINDFOR_ENDPOINT,
                {
                    "publishDateTimeFrom": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "publishDateTimeTo": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "startDateTimeFrom": start_dt_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "startDateTimeTo": start_dt_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "format": "json",
                },
            )
            logger.debug("  → %d records", len(chunk))
            records.extend(chunk)
            current = chunk_end
    return records
