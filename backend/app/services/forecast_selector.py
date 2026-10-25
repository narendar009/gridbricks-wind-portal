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
) -> pd.DataFrame:
    """
    For each actual delivery interval, select the latest eligible forecast.

    Parameters
    ----------
    actuals_df : DataFrame with columns:
        - start_time (datetime, UTC-aware)
        - actual_mw (float)
    forecasts_df : DataFrame with columns:
        - start_time (datetime, UTC-aware) — forecast target interval
        - publish_time (datetime, UTC-aware)
        - forecast_mw (float)

    Returns
    -------
    DataFrame with one row per delivery interval containing:
        start_time, actual_mw, forecast_mw, forecast_publish_time,
        delivery_date, cutoff_utc, status
    """
    if actuals_df.empty:
        logger.warning("Actuals DataFrame is empty; returning empty result.")
        return _empty_result()

    # Ensure timestamps are UTC-aware
    actuals_df = actuals_df.copy()
    forecasts_df = forecasts_df.copy()

    actuals_df["start_time"] = pd.to_datetime(actuals_df["start_time"], utc=True)
    if not forecasts_df.empty:
        forecasts_df["start_time"] = pd.to_datetime(
            forecasts_df["start_time"], utc=True
        )
        forecasts_df["publish_time"] = pd.to_datetime(
            forecasts_df["publish_time"], utc=True
        )

    rows = []
    for _, actual_row in actuals_df.iterrows():
        interval_start: pd.Timestamp = actual_row["start_time"]
        actual_mw: float = actual_row["actual_mw"]

        # Delivery date in Europe/London (for cutoff computation)
        delivery_date_london = interval_start.tz_convert(_tz_london).date()
        cutoff_utc = compute_cutoff_utc(delivery_date_london)

        if forecasts_df.empty:
            rows.append(
                _missing_row(interval_start, actual_mw, delivery_date_london, cutoff_utc)
            )
            continue

        # ── Temporal resolution bridging ─────────────────────────────────────
        # FUELHH is half-hourly; WINDFOR is hourly.
        # Floor the actual interval start to the nearest hour to find the
        # matching forecast record. E.g., 06:30 → 06:00, 07:00 → 07:00.
        forecast_hour = interval_start.floor("h")

        # Filter forecasts targeting this forecast hour
        candidate_mask = forecasts_df["start_time"] == forecast_hour
        candidates = forecasts_df[candidate_mask].copy()


        if candidates.empty:
            rows.append(
                _missing_row(interval_start, actual_mw, delivery_date_london, cutoff_utc)
            )
            continue

        # Filter to forecasts published AT OR BEFORE the cutoff
        eligible = candidates[candidates["publish_time"] <= pd.Timestamp(cutoff_utc)]

        if eligible.empty:
            rows.append(
                _missing_row(interval_start, actual_mw, delivery_date_london, cutoff_utc)
            )
            continue

        # Resolve duplicates deterministically: sort by (publish_time desc, forecast_mw asc)
        eligible = eligible.sort_values(
            by=["publish_time", "forecast_mw"], ascending=[False, True]
        )
        selected = eligible.iloc[0]

        error_mw = float(selected["forecast_mw"]) - float(actual_mw)
        rows.append(
            {
                "start_time": interval_start,
                "delivery_date": delivery_date_london,
                "cutoff_utc": cutoff_utc,
                "actual_mw": float(actual_mw),
                "forecast_mw": float(selected["forecast_mw"]),
                "forecast_publish_time": selected["publish_time"],
                "error_mw": error_mw,
                "abs_error_mw": abs(error_mw),
                "status": "matched",
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
        "Vintage selection complete: %d total, %d matched, %d missing_forecast",
        len(result),
        (result["status"] == "matched").sum(),
        (result["status"] == "missing_forecast").sum(),
    )
    return result


def _missing_row(
    interval_start: pd.Timestamp,
    actual_mw: float,
    delivery_date: date,
    cutoff_utc: datetime,
) -> dict:
    return {
        "start_time": interval_start,
        "delivery_date": delivery_date,
        "cutoff_utc": cutoff_utc,
        "actual_mw": float(actual_mw),
        "forecast_mw": None,
        "forecast_publish_time": None,
        "error_mw": None,
        "abs_error_mw": None,
        "status": "missing_forecast",
    }


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
