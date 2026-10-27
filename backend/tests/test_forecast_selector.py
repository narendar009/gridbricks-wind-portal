"""
Comprehensive test suite for forecast vintage selection and metrics.

Tests cover:
- Forecast published before cutoff is eligible
- Forecast published exactly at cutoff is eligible
- Forecast published after cutoff is excluded
- Latest eligible publication wins when multiple forecasts exist
- Newer ineligible forecast never replaces older eligible one
- Different delivery dates receive the correct cutoff
- Timezone conversion does not cause incorrect selection
- MAE, RMSE, bias, WAPE calculations
- Zero denominators
- Missing records
- Duplicate records
- Partial coverage
"""
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.services.forecast_selector import compute_cutoff_utc, select_forecast_vintage
from app.services.metrics import (
    compute_daily_metrics,
    compute_summary_metrics,
    compute_time_of_day_metrics,
    compute_top_errors,
)

_tz_utc = ZoneInfo("UTC")
_tz_london = ZoneInfo("Europe/London")

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ts(dt_str: str) -> pd.Timestamp:
    """Parse an ISO string as UTC timestamp."""
    return pd.Timestamp(dt_str, tz="UTC")


def _make_actuals(intervals: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"start_time": _ts(ts), "actual_mw": mw} for ts, mw in intervals]
    )


def _make_forecasts(records: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "start_time": _ts(st),
                "publish_time": _ts(pt),
                "forecast_mw": mw,
            }
            for st, pt, mw in records
        ]
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: compute_cutoff_utc
# ══════════════════════════════════════════════════════════════════════════════


class TestComputeCutoffUtc:
    """Test that the cutoff is computed per delivery date, with correct TZ handling."""

    def test_cutoff_for_jan_02_delivery(self):
        """
        Delivery 2026-01-02: cutoff should be 2026-01-01 10:00 Europe/London = 10:00 UTC
        (London is UTC+0 in January).
        """
        cutoff = compute_cutoff_utc(date(2026, 1, 2))
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=_tz_utc)
        assert cutoff == expected, f"Expected {expected}, got {cutoff}"

    def test_cutoff_for_different_delivery_dates_differ(self):
        """Each delivery date produces a different cutoff (one day apart)."""
        cutoff_jan02 = compute_cutoff_utc(date(2026, 1, 2))
        cutoff_jan03 = compute_cutoff_utc(date(2026, 1, 3))
        delta = cutoff_jan03 - cutoff_jan02
        assert delta == timedelta(days=1), f"Expected 1-day gap, got {delta}"

    def test_cutoff_in_bst_period(self):
        """
        Delivery 2026-07-02 (summer, BST = UTC+1):
        Cutoff = 2026-07-01 10:00 BST = 2026-07-01 09:00 UTC.
        """
        cutoff = compute_cutoff_utc(date(2026, 7, 2))
        expected = datetime(2026, 7, 1, 9, 0, 0, tzinfo=_tz_utc)
        assert cutoff == expected, f"Expected {expected}, got {cutoff}"

    def test_january_is_gmt_not_bst(self):
        """January is GMT (UTC+0), so London 10:00 == UTC 10:00."""
        cutoff = compute_cutoff_utc(date(2026, 1, 15))
        assert cutoff.hour == 10
        assert cutoff.tzinfo == _tz_utc


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Forecast vintage selection — boundary conditions
# ══════════════════════════════════════════════════════════════════════════════


class TestForecastVintageSelection:
    """
    Delivery interval: 2026-01-02T06:00:00Z
    Cutoff: 2026-01-01T10:00:00Z (10:00 UTC = 10:00 London in January)
    """

    DELIVERY = "2026-01-02T06:00:00Z"
    CUTOFF = "2026-01-01T10:00:00Z"  # UTC

    def _run(self, forecast_records):
        actuals = _make_actuals([(self.DELIVERY, 5000.0)])
        forecasts = _make_forecasts(forecast_records)
        return select_forecast_vintage(actuals, forecasts)

    def test_forecast_before_cutoff_is_eligible(self):
        """A forecast published 1h before cutoff should be selected."""
        records = [(self.DELIVERY, "2026-01-01T09:00:00Z", 5200.0)]
        result = self._run(records)
        assert len(result) == 1
        assert result.iloc[0]["status"] == "matched"
        assert result.iloc[0]["forecast_mw"] == 5200.0

    def test_forecast_exactly_at_cutoff_is_eligible(self):
        """A forecast published exactly at the cutoff (inclusive) must be selected."""
        records = [(self.DELIVERY, self.CUTOFF, 5100.0)]
        result = self._run(records)
        assert result.iloc[0]["status"] == "matched"
        assert result.iloc[0]["forecast_mw"] == 5100.0

    def test_forecast_after_cutoff_is_excluded(self):
        """A forecast published 1 second after cutoff must NOT be selected."""
        records = [(self.DELIVERY, "2026-01-01T10:00:01Z", 5300.0)]
        result = self._run(records)
        assert result.iloc[0]["status"] == "missing_forecast"
        assert result.iloc[0]["forecast_mw"] is None

    def test_latest_eligible_publication_wins(self):
        """When two eligible forecasts exist, the one with later publishTime wins."""
        records = [
            (self.DELIVERY, "2026-01-01T08:00:00Z", 4800.0),
            (self.DELIVERY, "2026-01-01T09:30:00Z", 5100.0),  # ← later, should win
        ]
        result = self._run(records)
        assert result.iloc[0]["status"] == "matched"
        assert result.iloc[0]["forecast_mw"] == 5100.0

    def test_newer_ineligible_forecast_never_replaces_eligible(self):
        """
        Even if a newer forecast exists, if it's after the cutoff it must NOT
        replace an older eligible one.
        """
        records = [
            (self.DELIVERY, "2026-01-01T09:00:00Z", 5000.0),  # eligible
            (self.DELIVERY, "2026-01-01T11:00:00Z", 6000.0),  # ineligible
        ]
        result = self._run(records)
        assert result.iloc[0]["status"] == "matched"
        assert result.iloc[0]["forecast_mw"] == 5000.0

    def test_no_forecast_returns_missing(self):
        """If no forecast exists for the interval, status should be missing_forecast."""
        result = self._run([])
        assert result.iloc[0]["status"] == "missing_forecast"

    def test_all_forecasts_after_cutoff_returns_missing(self):
        """If all forecasts are after the cutoff, status must be missing_forecast."""
        records = [
            (self.DELIVERY, "2026-01-01T11:00:00Z", 5000.0),
            (self.DELIVERY, "2026-01-01T12:00:00Z", 5100.0),
        ]
        result = self._run(records)
        assert result.iloc[0]["status"] == "missing_forecast"

    def test_different_delivery_dates_correct_cutoffs(self):
        """
        Jan 02 delivery uses Jan 01 cutoff; Jan 03 delivery uses Jan 02 cutoff.
        A forecast published at 2026-01-01T10:00Z is eligible for Jan-02 delivery
        but should not be used for Jan-03 delivery (cutoff is Jan-02 10:00Z).
        """
        actuals = _make_actuals([
            ("2026-01-02T06:00:00Z", 5000.0),
            ("2026-01-03T06:00:00Z", 6000.0),
        ])
        forecasts = _make_forecasts([
            # Eligible for Jan-02 only
            ("2026-01-02T06:00:00Z", "2026-01-01T10:00:00Z", 5100.0),
            # Eligible for both (published before both cutoffs)
            ("2026-01-03T06:00:00Z", "2026-01-01T10:00:00Z", 6100.0),
        ])
        result = select_forecast_vintage(actuals, forecasts)
        result = result.sort_values("start_time").reset_index(drop=True)

        # Jan-02 delivery should be matched
        assert result.iloc[0]["status"] == "matched"
        assert result.iloc[0]["forecast_mw"] == 5100.0

        # Jan-03 delivery should also be matched (forecast published Jan-01 is before Jan-02 cutoff)
        assert result.iloc[1]["status"] == "matched"
        assert result.iloc[1]["forecast_mw"] == 6100.0

    def test_duplicate_publish_times_deterministic(self):
        """Two records with identical publishTime produce deterministic output."""
        records = [
            (self.DELIVERY, "2026-01-01T09:00:00Z", 5000.0),
            (self.DELIVERY, "2026-01-01T09:00:00Z", 5200.0),  # same publishTime
        ]
        result1 = self._run(records)
        result2 = self._run(records)
        # Must be deterministic (same answer both times)
        assert result1.iloc[0]["forecast_mw"] == result2.iloc[0]["forecast_mw"]

    def test_empty_actuals_returns_empty(self):
        """Empty actuals DataFrame should return empty result."""
        actuals = pd.DataFrame(columns=["start_time", "actual_mw"])
        forecasts = _make_forecasts([(self.DELIVERY, "2026-01-01T09:00:00Z", 5000.0)])
        result = select_forecast_vintage(actuals, forecasts)
        assert result.empty


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Metrics calculations
# ══════════════════════════════════════════════════════════════════════════════


def _make_matched_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal matched DataFrame for metrics testing."""
    defaults = {
        "start_time": pd.Timestamp("2026-01-01T00:00:00Z"),
        "delivery_date": date(2026, 1, 1),
        "cutoff_utc": pd.Timestamp("2025-12-31T10:00:00Z"),
        "status": "matched",
    }
    records = []
    for row in rows:
        r = {**defaults, **row}
        if "error_mw" not in r and "forecast_mw" in r and "actual_mw" in r:
            r["error_mw"] = r["forecast_mw"] - r["actual_mw"]
            r["abs_error_mw"] = abs(r["error_mw"])
        records.append(r)
    return pd.DataFrame(records)


class TestMetricsCalculations:

    def test_mae_simple(self):
        """MAE = mean(|errors|) = (200 + 100) / 2 = 150."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1200.0},  # error = +200
            {"actual_mw": 2000.0, "forecast_mw": 1900.0},  # error = −100
        ])
        m = compute_summary_metrics(df)
        assert abs(m["mae_mw"] - 150.0) < 0.01

    def test_rmse_simple(self):
        """RMSE = sqrt((200² + 100²) / 2) = sqrt(25000) ≈ 158.11."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1200.0},
            {"actual_mw": 2000.0, "forecast_mw": 1900.0},
        ])
        m = compute_summary_metrics(df)
        expected = math.sqrt((200**2 + 100**2) / 2)
        assert abs(m["rmse_mw"] - expected) < 0.01

    def test_bias_positive(self):
        """Positive bias means overprediction."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1200.0},  # +200
            {"actual_mw": 1000.0, "forecast_mw": 1100.0},  # +100
        ])
        m = compute_summary_metrics(df)
        assert m["bias_mw"] == pytest.approx(150.0, abs=0.01)

    def test_bias_negative(self):
        """Negative bias means underprediction."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 800.0},  # −200
            {"actual_mw": 1000.0, "forecast_mw": 900.0},  # −100
        ])
        m = compute_summary_metrics(df)
        assert m["bias_mw"] == pytest.approx(-150.0, abs=0.01)

    def test_wape_calculation(self):
        """WAPE = sum(|errors|) / sum(|actuals|) × 100."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1200.0},  # abs error 200
            {"actual_mw": 2000.0, "forecast_mw": 1900.0},  # abs error 100
        ])
        m = compute_summary_metrics(df)
        expected_wape = (200 + 100) / (1000 + 2000) * 100
        assert m["wape_pct"] == pytest.approx(expected_wape, abs=0.01)

    def test_wape_zero_denominator_returns_none(self):
        """WAPE must return None when sum(|actual|) == 0, not divide by zero."""
        df = _make_matched_df([
            {"actual_mw": 0.0, "forecast_mw": 100.0, "error_mw": 100.0, "abs_error_mw": 100.0},
        ])
        m = compute_summary_metrics(df)
        assert m["wape_pct"] is None

    def test_empty_df_returns_none_metrics(self):
        """Empty DataFrame should return None for all metrics, not crash."""
        df = pd.DataFrame(columns=["start_time", "actual_mw", "forecast_mw", "error_mw", "abs_error_mw", "status"])
        m = compute_summary_metrics(df)
        assert m["mae_mw"] is None
        assert m["rmse_mw"] is None
        assert m["valid_intervals"] == 0

    def test_missing_forecast_intervals_excluded_from_metrics(self):
        """Intervals with status=missing_forecast must not affect metric calculations."""
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1200.0},  # matched, error=200
        ])
        # Add a missing_forecast row
        missing_row = pd.DataFrame([{
            "start_time": pd.Timestamp("2026-01-01T01:00:00Z"),
            "delivery_date": date(2026, 1, 1),
            "cutoff_utc": pd.Timestamp("2025-12-31T10:00:00Z"),
            "actual_mw": 1000.0,
            "forecast_mw": None,
            "error_mw": None,
            "abs_error_mw": None,
            "status": "missing_forecast",
        }])
        combined = pd.concat([df, missing_row], ignore_index=True)
        m = compute_summary_metrics(combined)
        # MAE should only consider the matched row
        assert m["mae_mw"] == pytest.approx(200.0, abs=0.01)
        assert m["valid_intervals"] == 1
        assert m["missing_forecast_count"] == 1

    def test_all_missing_forecast_returns_none(self):
        """If all intervals are missing_forecast, all metrics must be None."""
        df = pd.DataFrame([{
            "start_time": pd.Timestamp("2026-01-01T00:00:00Z"),
            "delivery_date": date(2026, 1, 1),
            "cutoff_utc": pd.Timestamp("2025-12-31T10:00:00Z"),
            "actual_mw": 1000.0,
            "forecast_mw": None,
            "error_mw": None,
            "abs_error_mw": None,
            "status": "missing_forecast",
        }])
        m = compute_summary_metrics(df)
        assert m["mae_mw"] is None

    def test_partial_coverage_correct_interval_count(self):
        """Coverage pct = valid_intervals / 1488 × 100."""
        n = 100
        df = _make_matched_df([
            {"actual_mw": 1000.0, "forecast_mw": 1100.0} for _ in range(n)
        ])
        m = compute_summary_metrics(df)
        assert m["valid_intervals"] == n
        assert m["coverage_pct"] == pytest.approx(n / 1488 * 100, abs=0.01)


class TestDailyMetrics:
    def test_daily_mae_correct(self):
        """Daily MAE should be per-day, not overall."""
        rows = []
        for i in range(48):
            ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=30 * i)
            rows.append({
                "start_time": ts,
                "delivery_date": date(2026, 1, 1),
                "cutoff_utc": pd.Timestamp("2025-12-31T10:00:00Z"),
                "actual_mw": 1000.0,
                "forecast_mw": 1200.0,
                "error_mw": 200.0,
                "abs_error_mw": 200.0,
                "status": "matched",
            })
        df = pd.DataFrame(rows)
        daily = compute_daily_metrics(df)
        assert len(daily) == 1
        assert daily[0]["date"] == "2026-01-01"
        assert abs(daily[0]["mae_mw"] - 200.0) < 0.01


class TestTopErrors:
    def test_top_errors_sorted_by_abs_error(self):
        matched_rows = []
        for i in range(5):
            matched_rows.append({
                "actual_mw": 1000.0,
                "forecast_mw": 1000.0 + (i + 1) * 100,
                "error_mw": (i + 1) * 100.0,
                "abs_error_mw": (i + 1) * 100.0,
                "status": "matched",
                "start_time": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=i),
                "delivery_date": date(2026, 1, 1),
                "cutoff_utc": pd.Timestamp("2025-12-31T10:00:00Z"),
                "forecast_publish_time": pd.Timestamp("2025-12-31T09:00:00Z"),
            })
        df = pd.DataFrame(matched_rows)
        top = compute_top_errors(df, n=3)
        assert len(top) == 3
        # Should be sorted descending by abs_error_mw
        assert top[0]["abs_error_mw"] >= top[1]["abs_error_mw"]
        assert top[1]["abs_error_mw"] >= top[2]["abs_error_mw"]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Complete expected timeline and missing actuals
# ══════════════════════════════════════════════════════════════════════════════


class TestExpectedTimelineAndMissingActuals:
    """Verify that expected timeline generation left-joins actuals and reports missing actuals."""

    def test_timeline_generation_includes_missing_actuals(self):
        """When actuals have gaps, the expected timeline retains all intervals with status='missing_actual'."""
        # 1 day = 48 intervals, omit 12:00 and 12:30
        actual_intervals = []
        for i in range(48):
            if i in (24, 25):  # 12:00, 12:30 omitted
                continue
            ts = pd.Timestamp("2026-01-02T00:00:00Z") + pd.Timedelta(minutes=30 * i)
            actual_intervals.append((ts.isoformat(), 3000.0 + i * 10))

        actuals = _make_actuals(actual_intervals)
        assert len(actuals) == 46

        # Forecasts exist for all hours including 12:00
        forecast_records = []
        for h in range(24):
            ts = pd.Timestamp("2026-01-02T00:00:00Z") + pd.Timedelta(hours=h)
            # Published at 09:00 on D-1 (before 10:00 cutoff)
            forecast_records.append((ts.isoformat(), "2026-01-01T09:00:00Z", 3200.0 + h * 20))

        forecasts = _make_forecasts(forecast_records)

        result = select_forecast_vintage(
            actuals,
            forecasts,
            start_date_str="2026-01-02",
            end_date_str="2026-01-02",
        )

        # Full 48 intervals must be present
        assert len(result) == 48
        assert (result["status"] == "matched").sum() == 46
        assert (result["status"] == "missing_actual").sum() == 2

        # Verify missing actual rows have forecast populated and null errors
        missing_rows = result[result["status"] == "missing_actual"]
        assert len(missing_rows) == 2
        for _, m_row in missing_rows.iterrows():
            assert pd.isna(m_row["actual_mw"]) or m_row["actual_mw"] is None
            assert m_row["forecast_mw"] is not None
            assert pd.isna(m_row["error_mw"]) or m_row["error_mw"] is None
            assert pd.isna(m_row["abs_error_mw"]) or m_row["abs_error_mw"] is None
            assert m_row["forecast_publish_time"] is not None

    def test_missing_actuals_excluded_from_metrics_and_counted_in_summary(self):
        """Metrics are computed only over valid matched rows, and missing_actuals are counted."""
        actual_intervals = []
        for i in range(48):
            if i in (10, 11, 12):  # 3 intervals missing
                continue
            ts = pd.Timestamp("2026-01-02T00:00:00Z") + pd.Timedelta(minutes=30 * i)
            actual_intervals.append((ts.isoformat(), 4000.0))

        actuals = _make_actuals(actual_intervals)
        forecast_records = [
            (
                (pd.Timestamp("2026-01-02T00:00:00Z") + pd.Timedelta(hours=h)).isoformat(),
                "2026-01-01T09:00:00Z",
                4200.0,
            )
            for h in range(24)
        ]
        forecasts = _make_forecasts(forecast_records)

        result = select_forecast_vintage(
            actuals,
            forecasts,
            start_date_str="2026-01-02",
            end_date_str="2026-01-02",
        )

        summary = compute_summary_metrics(result)
        assert summary["valid_intervals"] == 45
        assert summary["total_intervals"] == 48
        assert summary["missing_actual_count"] == 3
        assert summary["missing_forecast_count"] == 0
        # MAE should be exactly 200.0 (4200 - 4000) over valid intervals
        assert abs(summary["mae_mw"] - 200.0) < 0.01

