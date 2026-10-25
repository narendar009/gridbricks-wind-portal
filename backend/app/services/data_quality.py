"""
Data quality reporting.
"""
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def assess_data_quality(
    actuals_df: pd.DataFrame,
    forecasts_df: pd.DataFrame,
    matched_df: pd.DataFrame,
) -> dict[str, Any]:
    """
    Generate a data quality report comparing expected vs actual coverage.
    """
    expected_intervals = 1488  # 31 days × 48 half-hourly

    actual_count = len(actuals_df) if not actuals_df.empty else 0
    forecast_raw_count = len(forecasts_df) if not forecasts_df.empty else 0

    matched_count = int((matched_df["status"] == "matched").sum()) if not matched_df.empty else 0
    missing_forecast_count = int(
        (matched_df["status"] == "missing_forecast").sum()
    ) if not matched_df.empty else 0

    return {
        "expected_half_hourly_intervals": expected_intervals,
        "actual_intervals_present": actual_count,
        "actual_coverage_pct": round(actual_count / expected_intervals * 100, 2),
        "matched_intervals": matched_count,
        "missing_forecast_intervals": missing_forecast_count,
        "raw_forecast_records": forecast_raw_count,
        "forecast_vintage_unique_publish_times": (
            int(forecasts_df["publish_time"].nunique())
            if not forecasts_df.empty
            else 0
        ),
    }
