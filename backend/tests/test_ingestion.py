"""
Ingestion pipeline tests using mocked Elexon API responses.

Tests cover:
- Normalisation of FUELHH records
- Normalisation of WINDFOR records
- Forecast publish window calculation
- Expected intervals computation
- Data quality report generation
- Missing-value handling in normalisation
"""
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from app.services.ingestion import (
    _normalise_actuals,
    _normalise_forecasts,
    _forecast_publish_window,
)
from app.services.data_quality import (
    assess_data_quality,
    generate_expected_timeline,
)
from app.core.config import compute_expected_intervals


# ==============================================================================
# FUELHH normalisation
# ==============================================================================

class TestNormaliseActuals:
    def test_normalise_basic(self):
        """Basic FUELHH records are normalised correctly."""
        records = [
            {"startTime": "2026-01-01T00:00:00Z", "generation": 5000, "fuelType": "WIND"},
            {"startTime": "2026-01-01T00:30:00Z", "generation": 5200, "fuelType": "WIND"},
        ]
        df = _normalise_actuals(records)
        assert len(df) == 2
        assert list(df.columns) == ["start_time", "actual_mw"]
        assert df.iloc[0]["actual_mw"] == 5000

    def test_normalise_filters_non_wind(self):
        """Non-WIND fuel types are excluded."""
        records = [
            {"startTime": "2026-01-01T00:00:00Z", "generation": 5000, "fuelType": "WIND"},
            {"startTime": "2026-01-01T00:00:00Z", "generation": 3000, "fuelType": "CCGT"},
        ]
        df = _normalise_actuals(records)
        assert len(df) == 1
        assert df.iloc[0]["actual_mw"] == 5000

    def test_normalise_deduplicates(self):
        """Duplicate startTime keeps the last occurrence."""
        records = [
            {"startTime": "2026-01-01T00:00:00Z", "generation": 5000, "fuelType": "WIND"},
            {"startTime": "2026-01-01T00:00:00Z", "generation": 5500, "fuelType": "WIND"},
        ]
        df = _normalise_actuals(records)
        assert len(df) == 1
        # keep="last" means the second (5500) is kept
        assert df.iloc[0]["actual_mw"] == 5500

    def test_normalise_empty(self):
        """Empty records produce empty DataFrame with correct schema."""
        df = _normalise_actuals([])
        assert df.empty
        assert list(df.columns) == ["start_time", "actual_mw"]

    def test_normalise_coerces_generation(self):
        """Non-numeric generation values are coerced to NaN."""
        records = [
            {"startTime": "2026-01-01T00:00:00Z", "generation": "invalid", "fuelType": "WIND"},
            {"startTime": "2026-01-01T00:30:00Z", "generation": 5000, "fuelType": "WIND"},
        ]
        df = _normalise_actuals(records)
        # The invalid one has NaN actual_mw but is still present (only start_time NaN is dropped)
        assert len(df) == 2


# ==============================================================================
# WINDFOR normalisation
# ==============================================================================

class TestNormaliseForecasts:
    def test_normalise_basic(self):
        """Basic WINDFOR records are normalised correctly."""
        records = [
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T09:00:00Z", "generation": 5200},
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T10:00:00Z", "generation": 5100},
        ]
        df = _normalise_forecasts(records)
        assert len(df) == 2
        assert list(df.columns) == ["start_time", "publish_time", "forecast_mw"]

    def test_normalise_deduplicates_same_start_publish(self):
        """(start_time, publish_time) duplicates are removed deterministically."""
        records = [
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T09:00:00Z", "generation": 5200},
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T09:00:00Z", "generation": 5300},
        ]
        df = _normalise_forecasts(records)
        assert len(df) == 1

    def test_normalise_empty(self):
        """Empty records produce empty DataFrame with correct schema."""
        df = _normalise_forecasts([])
        assert df.empty
        assert list(df.columns) == ["start_time", "publish_time", "forecast_mw"]

    def test_normalise_preserves_all_publications(self):
        """All unique (start_time, publish_time) pairs are preserved for vintage selection."""
        records = [
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T08:00:00Z", "generation": 5000},
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T09:00:00Z", "generation": 5100},
            {"startTime": "2026-01-01T06:00:00Z", "publishTime": "2025-12-31T10:00:00Z", "generation": 5200},
        ]
        df = _normalise_forecasts(records)
        assert len(df) == 3


# ==============================================================================
# Publish window calculation
# ==============================================================================

class TestForecastPublishWindow:
    def test_window_includes_day_before(self):
        """Window starts 2 days before analysis_start for timezone edge cases."""
        start = date(2026, 1, 1)
        end = date(2026, 1, 31)
        from_dt, to_dt = _forecast_publish_window(start, end)
        assert from_dt.date() == date(2025, 12, 30)
        assert to_dt.date() == date(2026, 1, 31)

    def test_window_covers_full_range(self):
        """Window end should be end of last analysis day."""
        start = date(2026, 1, 1)
        end = date(2026, 1, 7)
        from_dt, to_dt = _forecast_publish_window(start, end)
        assert to_dt.hour == 23
        assert to_dt.minute == 59


# ==============================================================================
# Expected intervals configuration
# ==============================================================================

class TestExpectedIntervals:
    def test_january_2026(self):
        """January 1-31, 2026 = 31 days x 48 = 1488."""
        result = compute_expected_intervals("2026-01-01", "2026-01-31")
        assert result == 1488

    def test_single_day(self):
        """Single day = 48 intervals."""
        result = compute_expected_intervals("2026-01-01", "2026-01-01")
        assert result == 48

    def test_one_week(self):
        """7 days = 336 intervals."""
        result = compute_expected_intervals("2026-01-01", "2026-01-07")
        assert result == 336

    def test_february_non_leap_year(self):
        """February 2026 (non-leap) = 28 days x 48 = 1344."""
        result = compute_expected_intervals("2026-02-01", "2026-02-28")
        assert result == 1344


# ==============================================================================
# Data quality report
# ==============================================================================

class TestDataQuality:
    def test_expected_timeline_length(self):
        """Generated timeline for January 2026 has exactly 1488 entries."""
        timeline = generate_expected_timeline("2026-01-01", "2026-01-31")
        assert len(timeline) == 1488

    def test_quality_report_with_full_coverage(self):
        """Full coverage produces 100% actual_coverage_pct."""
        # Create minimal DataFrames
        times = pd.date_range("2026-01-01", periods=1488, freq="30min", tz="UTC")
        actuals = pd.DataFrame({"start_time": times, "actual_mw": [5000.0] * 1488})
        forecasts = pd.DataFrame({
            "start_time": times[:10],
            "publish_time": [pd.Timestamp("2025-12-31T09:00:00Z")] * 10,
            "forecast_mw": [5100.0] * 10,
        })
        matched = pd.DataFrame({
            "start_time": times,
            "status": ["matched"] * 1488,
        })
        report = assess_data_quality(actuals, forecasts, matched)
        assert report["actual_coverage_pct"] == 100.0
        assert report["missing_actual_intervals"] == 0

    def test_quality_report_with_missing_actuals(self):
        """Missing actuals are detected and reported."""
        times = pd.date_range("2026-01-01", periods=1400, freq="30min", tz="UTC")
        actuals = pd.DataFrame({"start_time": times, "actual_mw": [5000.0] * 1400})
        forecasts = pd.DataFrame(columns=["start_time", "publish_time", "forecast_mw"])
        matched = pd.DataFrame(columns=["start_time", "status"])
        report = assess_data_quality(actuals, forecasts, matched)
        assert report["actual_intervals_present"] == 1400
        assert report["missing_actual_intervals"] == 88  # 1488 - 1400

    def test_quality_report_with_empty_data(self):
        """Empty datasets produce valid report with zeros."""
        actuals = pd.DataFrame(columns=["start_time", "actual_mw"])
        forecasts = pd.DataFrame(columns=["start_time", "publish_time", "forecast_mw"])
        matched = pd.DataFrame(columns=["start_time", "status"])
        report = assess_data_quality(actuals, forecasts, matched)
        assert report["actual_intervals_present"] == 0
        assert report["actual_coverage_pct"] == 0.0
        assert report["missing_actual_intervals"] == 1488
