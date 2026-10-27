"""
Accuracy metrics calculations.

All metrics are computed over "valid matched" intervals only:
those where both actual_mw and forecast_mw are present.

Metrics:
- MAE  = mean(|forecast - actual|)
- RMSE = sqrt(mean((forecast - actual)^2))
- Bias = mean(forecast - actual)  [positive = overprediction]
- WAPE = sum(|forecast - actual|) / sum(|actual|) x 100
         [returns None if denominator is zero]

Sign convention: error = forecast - actual
  positive -> forecast overpredicted
  negative -> forecast underpredicted
"""
import logging
import math
from typing import Any

import numpy as np
import pandas as pd

from app.core.config import compute_expected_intervals

logger = logging.getLogger(__name__)


def _valid_matched(df: pd.DataFrame) -> pd.DataFrame:
    """Return rows where both actual_mw and forecast_mw are non-null (matched)."""
    return df[df["status"] == "matched"].copy()


def compute_summary_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """
    Compute monthly summary metrics over all valid matched intervals.

    The expected_intervals denominator is derived from the configured
    ANALYSIS_START_DATE / ANALYSIS_END_DATE rather than hardcoded.
    """
    total = len(df)
    matched = _valid_matched(df)
    n = len(matched)
    missing_forecast = (
        int(df["status"].isin(["missing_forecast", "missing_both"]).sum())
        if not df.empty and "status" in df.columns
        else 0
    )
    missing_actual = (
        int(df["status"].isin(["missing_actual", "missing_both"]).sum())
        if not df.empty and "status" in df.columns
        else 0
    )

    expected = compute_expected_intervals()

    if n == 0:
        return {
            "mae_mw": None,
            "rmse_mw": None,
            "bias_mw": None,
            "wape_pct": None,
            "valid_intervals": 0,
            "total_intervals": total,
            "missing_forecast_count": missing_forecast,
            "missing_actual_count": missing_actual,
            "coverage_pct": 0.0,
            "expected_intervals": expected,
            "note": "No valid matched intervals available for metric computation.",
        }

    errors = matched["error_mw"].values.astype(float)
    actuals = matched["actual_mw"].values.astype(float)

    mae = float(np.mean(np.abs(errors)))
    rmse = float(math.sqrt(np.mean(errors**2)))
    bias = float(np.mean(errors))

    denom = float(np.sum(np.abs(actuals)))
    wape = float(np.sum(np.abs(errors)) / denom * 100) if denom > 0 else None

    coverage_pct = round(n / expected * 100, 2) if expected > 0 else 0.0

    return {
        "mae_mw": round(mae, 2),
        "rmse_mw": round(rmse, 2),
        "bias_mw": round(bias, 2),
        "wape_pct": round(wape, 4) if wape is not None else None,
        "valid_intervals": n,
        "total_intervals": total,
        "missing_forecast_count": missing_forecast,
        "missing_actual_count": missing_actual,
        "coverage_pct": coverage_pct,
        "expected_intervals": expected,
    }


def compute_daily_metrics(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Compute MAE, RMSE, bias per delivery date.
    Denominators are the underlying valid matched intervals per day.
    """
    matched = _valid_matched(df)
    if matched.empty:
        return []

    matched = matched.copy()
    matched["date"] = pd.to_datetime(matched["start_time"]).dt.date

    rows = []
    for day, grp in matched.groupby("date"):
        errors = grp["error_mw"].values.astype(float)
        actuals = grp["actual_mw"].values.astype(float)
        n = len(grp)

        mae = float(np.mean(np.abs(errors)))
        rmse = float(math.sqrt(np.mean(errors**2)))
        bias = float(np.mean(errors))
        denom = float(np.sum(np.abs(actuals)))
        wape = float(np.sum(np.abs(errors)) / denom * 100) if denom > 0 else None

        rows.append(
            {
                "date": day.isoformat(),
                "mae_mw": round(mae, 2),
                "rmse_mw": round(rmse, 2),
                "bias_mw": round(bias, 2),
                "wape_pct": round(wape, 4) if wape is not None else None,
                "valid_intervals": n,
            }
        )
    return rows


def compute_time_of_day_metrics(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Compute average absolute error by hour-of-day (UTC) across all valid intervals.
    """
    matched = _valid_matched(df)
    if matched.empty:
        return []

    matched = matched.copy()
    matched["hour_utc"] = pd.to_datetime(matched["start_time"]).dt.hour

    rows = []
    for hour, grp in matched.groupby("hour_utc"):
        errors = grp["error_mw"].values.astype(float)
        rows.append(
            {
                "hour_utc": int(hour),
                "mae_mw": round(float(np.mean(np.abs(errors))), 2),
                "bias_mw": round(float(np.mean(errors)), 2),
                "count": len(grp),
            }
        )
    return rows


def compute_top_errors(df: pd.DataFrame, n: int = 20) -> list[dict[str, Any]]:
    """Return the n intervals with the highest absolute error."""
    matched = _valid_matched(df)
    if matched.empty:
        return []

    top = matched.nlargest(n, "abs_error_mw")
    records = []
    for _, row in top.iterrows():
        records.append(
            {
                "start_time": row["start_time"].isoformat(),
                "actual_mw": round(float(row["actual_mw"]), 1),
                "forecast_mw": round(float(row["forecast_mw"]), 1),
                "error_mw": round(float(row["error_mw"]), 1),
                "abs_error_mw": round(float(row["abs_error_mw"]), 1),
                "forecast_publish_time": (
                    row["forecast_publish_time"].isoformat()
                    if pd.notna(row["forecast_publish_time"])
                    else None
                ),
            }
        )
    return records


def compute_error_distribution(
    df: pd.DataFrame, bins: int = 20
) -> list[dict[str, Any]]:
    """
    Compute error distribution histogram over valid matched intervals.
    """
    matched = _valid_matched(df)
    if matched.empty:
        return []

    errors = matched["error_mw"].values.astype(float)
    counts, edges = np.histogram(errors, bins=bins)

    result = []
    for i, count in enumerate(counts):
        result.append(
            {
                "bin_start": round(float(edges[i]), 1),
                "bin_end": round(float(edges[i + 1]), 1),
                "count": int(count),
            }
        )
    return result
