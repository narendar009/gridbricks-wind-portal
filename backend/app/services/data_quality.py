"""
Data quality reporting.

Generates explicit half-hourly timeline for the analysis period and joins
actual records against it so missing intervals are reported explicitly.
"""
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.config import (
    ANALYSIS_END_DATE,
    ANALYSIS_START_DATE,
    INTERVALS_PER_DAY,
    compute_expected_intervals,
)

logger = logging.getLogger(__name__)


def generate_expected_timeline(
    start_date_str: str | None = None,
    end_date_str: str | None = None,
) -> pd.DatetimeIndex:
    """
    Generate a complete half-hourly timeline for the analysis period.

    Returns a DatetimeIndex with one entry per expected settlement interval.
    For January 2026: 31 days x 48 intervals = 1,488 timestamps.
    """
    start_str = start_date_str or ANALYSIS_START_DATE
    end_str = end_date_str or ANALYSIS_END_DATE
    start = pd.Timestamp(f"{start_str}T00:00:00Z")
    end = pd.Timestamp(f"{end_str}T23:30:00Z")
    return pd.date_range(start=start, end=end, freq="30min", tz="UTC")


def assess_data_quality(
    actuals_df: pd.DataFrame,
    forecasts_df: pd.DataFrame,
    matched_df: pd.DataFrame,
) -> dict[str, Any]:
    """
    Generate a data quality report comparing expected vs actual coverage.

    Uses configuration-derived expected intervals (not hardcoded).
    Explicitly reports missing half-hourly intervals by joining against the
    generated timeline.
    """
    expected_intervals = compute_expected_intervals()
    expected_timeline = generate_expected_timeline()

    actual_count = len(actuals_df) if not actuals_df.empty else 0
    forecast_raw_count = len(forecasts_df) if not forecasts_df.empty else 0

    matched_count = int((matched_df["status"] == "matched").sum()) if not matched_df.empty else 0
    missing_forecast_count = int(
        (matched_df["status"] == "missing_forecast").sum()
    ) if not matched_df.empty else 0

    # Identify missing actual intervals by comparing to expected timeline.
    # Use tz_convert(None).to_numpy() to avoid Pandas 4.x deprecation warning
    # about .values dropping timezone information on DatetimeTZDtype.
    missing_actual_times: list[str] = []
    if not actuals_df.empty:
        actual_arr = pd.to_datetime(actuals_df["start_time"], utc=True).dt.tz_convert(None).to_numpy()
        actual_times = set(actual_arr)
        expected_arr = expected_timeline.tz_convert(None).to_numpy()
        expected_set = set(expected_arr)
        missing_set = sorted(expected_set - actual_times)
        missing_actual_times = [
            pd.Timestamp(t, tz="UTC").isoformat() for t in missing_set
        ]
    else:
        missing_actual_times = [t.isoformat() for t in expected_timeline]

    report = {
        "expected_half_hourly_intervals": expected_intervals,
        "actual_intervals_present": actual_count,
        "actual_coverage_pct": round(
            actual_count / expected_intervals * 100, 2
        ) if expected_intervals > 0 else 0.0,
        "matched_intervals": matched_count,
        "missing_forecast_intervals": missing_forecast_count,
        "missing_actual_intervals": len(missing_actual_times),
        "raw_forecast_records": forecast_raw_count,
        "forecast_vintage_unique_publish_times": (
            int(forecasts_df["publish_time"].nunique())
            if not forecasts_df.empty
            else 0
        ),
    }

    # Only include explicit missing times if there are gaps (keep response compact)
    if missing_actual_times:
        report["missing_actual_interval_times"] = missing_actual_times[:50]  # cap for API
        if len(missing_actual_times) > 50:
            report["missing_actual_interval_note"] = (
                f"Showing first 50 of {len(missing_actual_times)} missing intervals."
            )

    return report
