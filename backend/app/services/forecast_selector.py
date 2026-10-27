"""
Forecast vintage selector — the most critical business logic.

For each delivery interval:
  1. Compute the day-ahead procurement decision cutoff for that delivery date.
  2. Floor the actual half-hourly startTime to the nearest hour to match WINDFOR's
     hourly resolution (e.g., 06:30 UTC → 06:00 UTC forecast hour).
  3. Filter forecasts for that forecast hour to those published <= cutoff.
  4. Select the record with the latest publishTime among eligible ones.
  5. If no eligible forecast exists, mark as missing.
  6. If multiple records share the same publishTime (duplicates), keep the first
     one by insertion order after deterministic sort (publishTime, generation).

Temporal resolution note:
  FUELHH actuals are half-hourly; WINDFOR forecasts are hourly.
  The join key is the forecast hour containing the actual interval.
  This is documented in the methodology and disclosed in the UI.

The cutoff is computed PER delivery date; a single month never uses the same
cutoff timestamp for every interval.

Timezone policy: all datetimes are compared in UTC after normalizing.
The cutoff is expressed in Europe/London local time and converted to UTC.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.config import (
    CUTOFF_DAY_OFFSET,
    CUTOFF_HOUR,
    CUTOFF_MINUTE,
    CUTOFF_TIMEZONE,
)

logger = logging.getLogger(__name__)

_tz_london = ZoneInfo(CUTOFF_TIMEZONE)
_tz_utc = ZoneInfo("UTC")


def compute_cutoff_utc(delivery_date: date) -> datetime:
    """
    Compute the procurement decision cutoff (UTC) for a given delivery date.

    Default policy (configurable):
        Cutoff = CUTOFF_HOUR:CUTOFF_MINUTE Europe/London on
                 (delivery_date − CUTOFF_DAY_OFFSET)

    This is a PROVISIONAL default. It is NOT an official UK market rule.
    It is configurable via CUTOFF_HOUR, CUTOFF_MINUTE, CUTOFF_TIMEZONE,
    CUTOFF_DAY_OFFSET environment variables.
    """
    decision_date = delivery_date - timedelta(days=CUTOFF_DAY_OFFSET)
    local_cutoff = datetime(
        decision_date.year,
        decision_date.month,
        decision_date.day,
        CUTOFF_HOUR,
        CUTOFF_MINUTE,
        0,
        tzinfo=_tz_london,
    )
    # Convert to UTC (handles BST/GMT transitions automatically via zoneinfo)
    return local_cutoff.astimezone(_tz_utc)


def select_forecast_vintage(
    actuals_df: pd.DataFrame,
    forecasts_df: pd.DataFrame,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
) -> pd.DataFrame:
    """
    Select the latest eligible forecast vintage published on or before the
    provisional day-ahead procurement decision cutoff for each delivery interval.

    Supports generating the complete expected half-hourly delivery timeline when
    start_date_str and end_date_str are supplied (or derived from configured analysis scope).
    Actual intervals are left-joined against that timeline so missing actual intervals
    are explicitly reported in the output rather than omitted.

    Parameters
    ----------
    actuals_df : DataFrame with columns:
        - start_time (datetime, UTC-aware)
        - actual_mw (float)
    forecasts_df : DataFrame with columns:
        - start_time (datetime, UTC-aware) — forecast target interval
        - publish_time (datetime, UTC-aware)
        - forecast_mw (float)
    start_date_str : Optional[str] — YYYY-MM-DD start of expected delivery timeline
    end_date_str : Optional[str] — YYYY-MM-DD end of expected delivery timeline

    Returns
    -------
    DataFrame with one row per delivery interval containing:
        start_time, delivery_date, cutoff_utc, actual_mw, forecast_mw,
        forecast_publish_time, error_mw, abs_error_mw, status
    """
    actuals = (
        actuals_df.copy()
        if not actuals_df.empty
        else pd.DataFrame(columns=["start_time", "actual_mw"])
    )
    forecasts = (
        forecasts_df.copy()
        if not forecasts_df.empty
        else pd.DataFrame(columns=["start_time", "publish_time", "forecast_mw"])
    )

    if not actuals.empty:
        actuals["start_time"] = pd.to_datetime(actuals["start_time"], utc=True)
    if not forecasts.empty:
        forecasts["start_time"] = pd.to_datetime(forecasts["start_time"], utc=True)
        forecasts["publish_time"] = pd.to_datetime(forecasts["publish_time"], utc=True)

    # Determine delivery intervals timeline
    if start_date_str is not None and end_date_str is not None:
        start_ts = pd.Timestamp(f"{start_date_str}T00:00:00Z")
        end_ts = pd.Timestamp(f"{end_date_str}T23:30:00Z")
        timeline = pd.date_range(start=start_ts, end=end_ts, freq="30min", tz="UTC")
    elif not actuals.empty:
        timeline = pd.DatetimeIndex(actuals["start_time"].drop_duplicates().sort_values())
    else:
        return _empty_result()

    if len(timeline) == 0:
        return _empty_result()

    # Map actuals by start_time for O(1) lookup
    actuals_map: dict[pd.Timestamp, float] = {}
    if not actuals.empty:
        for _, row in actuals.iterrows():
            ts = row["start_time"]
            if ts not in actuals_map and pd.notna(row["actual_mw"]):
                actuals_map[ts] = float(row["actual_mw"])

    rows = []
    for interval_start in timeline:
        has_actual = interval_start in actuals_map
        actual_mw = actuals_map[interval_start] if has_actual else None

        # Delivery date in Europe/London (for cutoff computation)
        delivery_date_london = interval_start.tz_convert(_tz_london).date()
        cutoff_utc = compute_cutoff_utc(delivery_date_london)

        # ── Temporal resolution bridging ─────────────────────────────────────
        # FUELHH is half-hourly; WINDFOR is hourly.
        # Floor the actual interval start to the nearest hour to find the
        # matching forecast record. E.g., 06:30 → 06:00, 07:00 → 07:00.
        forecast_hour = interval_start.floor("h")

        has_forecast = False
        forecast_mw = None
        forecast_publish_time = None

        if not forecasts.empty:
            candidate_mask = (forecasts["start_time"] == forecast_hour) & (
                forecasts["publish_time"] <= pd.Timestamp(cutoff_utc)
            )
            eligible = forecasts[candidate_mask]
            if not eligible.empty:
                # Deterministic tie-breaking: latest publishTime, then lowest forecast_mw
                eligible = eligible.sort_values(
                    by=["publish_time", "forecast_mw"], ascending=[False, True]
                )
                selected = eligible.iloc[0]
                has_forecast = True
                forecast_mw = float(selected["forecast_mw"])
                forecast_publish_time = selected["publish_time"]

        # Status and errors
        if has_actual and has_forecast:
            status = "matched"
            error_mw = forecast_mw - actual_mw
            abs_error_mw = abs(error_mw)
        elif has_actual and not has_forecast:
            status = "missing_forecast"
            error_mw = None
            abs_error_mw = None
        elif not has_actual and has_forecast:
            status = "missing_actual"
            error_mw = None
            abs_error_mw = None
        else:
            status = "missing_both"
            error_mw = None
            abs_error_mw = None

        rows.append(
            {
                "start_time": interval_start,
                "delivery_date": delivery_date_london,
                "cutoff_utc": cutoff_utc,
                "actual_mw": actual_mw,
                "forecast_mw": forecast_mw,
                "forecast_publish_time": forecast_publish_time,
                "error_mw": error_mw,
                "abs_error_mw": abs_error_mw,
                "status": status,
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return _empty_result()

    # Coerce types
    result["start_time"] = pd.to_datetime(result["start_time"], utc=True)
    result["cutoff_utc"] = pd.to_datetime(result["cutoff_utc"], utc=True)
    if "forecast_publish_time" in result.columns:
        result["forecast_publish_time"] = pd.to_datetime(
            result["forecast_publish_time"], utc=True
        )

    logger.info(
        "Vintage selection complete: %d total, %d matched, %d missing_forecast, %d missing_actual",
        len(result),
        (result["status"] == "matched").sum(),
        (result["status"] == "missing_forecast").sum(),
        (result["status"] == "missing_actual").sum(),
    )
    return result


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "start_time",
            "delivery_date",
            "cutoff_utc",
            "actual_mw",
            "forecast_mw",
            "forecast_publish_time",
            "error_mw",
            "abs_error_mw",
            "status",
        ]
    )
