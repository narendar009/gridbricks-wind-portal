"""
API integration tests for the FastAPI application.

Tests all documented endpoints with the actual data pipeline.
Uses TestClient (synchronous httpx-based transport) for deterministic testing.
"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ==============================================================================
# Health endpoint
# ==============================================================================

class TestHealthEndpoint:
    def test_health_returns_ok(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert data["version"] == "1.0.0"

    def test_health_timestamp_is_iso8601(self):
        resp = client.get("/api/health")
        data = resp.json()
        # Should be parseable as ISO-8601
        datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))


# ==============================================================================
# Metadata endpoint
# ==============================================================================

class TestMetadataEndpoint:
    def test_metadata_returns_200(self):
        resp = client.get("/api/metadata")
        assert resp.status_code == 200

    def test_metadata_contains_required_fields(self):
        resp = client.get("/api/metadata")
        data = resp.json()
        required_fields = [
            "analysis_start", "analysis_end", "expected_intervals",
            "actual_intervals", "forecast_records", "demo_mode",
            "cutoff_policy", "data_sources", "data_quality",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_metadata_cutoff_policy_structure(self):
        resp = client.get("/api/metadata")
        cp = resp.json()["cutoff_policy"]
        assert cp["hour"] == 10
        assert cp["minute"] == 0
        assert cp["timezone"] == "Europe/London"
        assert cp["day_offset"] == 1
        assert "provisional" in cp["description"].lower() or "NOT" in cp["description"]

    def test_metadata_data_sources_list_elexon(self):
        resp = client.get("/api/metadata")
        sources = resp.json()["data_sources"]
        assert "actuals" in sources
        assert "forecasts" in sources
        assert "Elexon" in sources["actuals"] or "BMRS" in sources["actuals"]

    def test_metadata_no_absolute_paths(self):
        """Metadata must not contain machine-specific absolute paths."""
        resp = client.get("/api/metadata")
        raw = resp.text
        # Should not contain Windows drive letters or Unix absolute paths to user dirs
        assert "C:\\" not in raw and "C:/" not in raw, "Found Windows absolute path in metadata"
        assert "/home/" not in raw, "Found Unix absolute path in metadata"
        assert "/Users/" not in raw, "Found macOS absolute path in metadata"


# ==============================================================================
# Summary endpoint
# ==============================================================================

class TestSummaryEndpoint:
    def test_summary_returns_200(self):
        resp = client.get("/api/summary")
        assert resp.status_code == 200

    def test_summary_contains_all_metrics(self):
        resp = client.get("/api/summary")
        data = resp.json()
        metric_fields = [
            "mae_mw", "rmse_mw", "bias_mw", "wape_pct",
            "valid_intervals", "total_intervals",
            "missing_forecast_count", "missing_actual_count",
            "coverage_pct", "expected_intervals",
        ]
        for field in metric_fields:
            assert field in data, f"Missing metric field: {field}"

    def test_summary_expected_intervals_config_derived(self):
        resp = client.get("/api/summary")
        data = resp.json()
        # For default config (Jan 1-31, 2026): 31 * 48 = 1488
        assert data["expected_intervals"] == 1488


# ==============================================================================
# Timeseries endpoint
# ==============================================================================

class TestTimeseriesEndpoint:
    def test_timeseries_returns_200(self):
        resp = client.get("/api/timeseries")
        assert resp.status_code == 200

    def test_timeseries_has_data_and_count(self):
        resp = client.get("/api/timeseries")
        data = resp.json()
        assert "data" in data
        assert "count" in data
        assert data["count"] == len(data["data"])

    def test_timeseries_date_filter(self):
        resp = client.get("/api/timeseries", params={"date_from": "2026-01-10", "date_to": "2026-01-10"})
        assert resp.status_code == 200
        data = resp.json()
        # Should only contain data from Jan 10
        for point in data["data"]:
            assert point["start_time"].startswith("2026-01-10")

    def test_timeseries_invalid_date_returns_400(self):
        resp = client.get("/api/timeseries", params={"date_from": "not-a-date"})
        assert resp.status_code == 400


# ==============================================================================
# Intervals (pagination) endpoint
# ==============================================================================

class TestIntervalsEndpoint:
    def test_intervals_returns_200(self):
        resp = client.get("/api/intervals")
        assert resp.status_code == 200

    def test_intervals_pagination(self):
        resp = client.get("/api/intervals", params={"page": 1, "page_size": 10})
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert len(data["data"]) <= 10
        assert data["total_pages"] >= 1

    def test_intervals_page_2(self):
        resp = client.get("/api/intervals", params={"page": 2, "page_size": 10})
        data = resp.json()
        assert data["page"] == 2

    def test_intervals_status_filter(self):
        resp = client.get("/api/intervals", params={"status": "matched"})
        data = resp.json()
        for record in data["data"]:
            assert record["status"] == "matched"

    def test_intervals_sort_by_error(self):
        resp = client.get("/api/intervals", params={
            "sort_by": "abs_error_mw", "sort_order": "desc", "page_size": 5,
        })
        data = resp.json()
        if len(data["data"]) >= 2:
            errors = [r["abs_error_mw"] for r in data["data"] if r["abs_error_mw"] is not None]
            # Should be sorted descending
            for i in range(len(errors) - 1):
                assert errors[i] >= errors[i + 1]

    def test_intervals_date_filter(self):
        resp = client.get("/api/intervals", params={
            "date_from": "2026-01-15", "date_to": "2026-01-15",
        })
        assert resp.status_code == 200


# ==============================================================================
# Accuracy endpoints
# ==============================================================================

class TestAccuracyEndpoints:
    def test_daily_accuracy_returns_list(self):
        resp = client.get("/api/accuracy/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert "date" in data[0]
            assert "mae_mw" in data[0]

    def test_time_of_day_returns_list(self):
        resp = client.get("/api/accuracy/time-of-day")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert "hour_utc" in data[0]
            assert "mae_mw" in data[0]


# ==============================================================================
# Error endpoints
# ==============================================================================

class TestErrorEndpoints:
    def test_top_errors_returns_list(self):
        resp = client.get("/api/errors/top")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) <= 20  # default n=20

    def test_top_errors_custom_n(self):
        resp = client.get("/api/errors/top", params={"n": 5})
        data = resp.json()
        assert len(data) <= 5

    def test_error_distribution_returns_bins(self):
        resp = client.get("/api/errors/distribution")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert "bin_start" in data[0]
            assert "bin_end" in data[0]
            assert "count" in data[0]

    def test_error_distribution_custom_bins(self):
        resp = client.get("/api/errors/distribution", params={"bins": 10})
        data = resp.json()
        assert len(data) == 10 or len(data) == 0


# ==============================================================================
# Invalid requests
# ==============================================================================

class TestInvalidRequests:
    def test_unknown_endpoint_returns_404(self):
        resp = client.get("/api/nonexistent")
        assert resp.status_code in (404, 405)

    def test_intervals_page_zero_returns_422(self):
        resp = client.get("/api/intervals", params={"page": 0})
        assert resp.status_code == 422

    def test_intervals_negative_page_size_returns_422(self):
        resp = client.get("/api/intervals", params={"page_size": -1})
        assert resp.status_code == 422

    def test_top_errors_n_exceeds_max(self):
        resp = client.get("/api/errors/top", params={"n": 200})
        assert resp.status_code == 422

    def test_distribution_bins_below_min(self):
        resp = client.get("/api/errors/distribution", params={"bins": 2})
        assert resp.status_code == 422

    def test_root_returns_product_info(self):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "product" in data


# ==============================================================================
# CORS and Error Handling
# ==============================================================================

class TestCorsAndErrorHandling:
    def test_cors_headers_on_success(self):
        """Successful responses include CORS headers for production Vercel origin."""
        resp = client.get("/api/health", headers={"Origin": "https://gridbricks-wind-portal.vercel.app"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        assert resp.headers["access-control-allow-origin"] in ("*", "https://gridbricks-wind-portal.vercel.app")

    def test_cors_headers_on_error_response(self):
        """500 error responses handled by global exception handler include CORS headers."""
        from app.main import app
        from starlette.testclient import TestClient
        
        @app.get("/api/test-faulty-endpoint")
        def faulty():
            raise RuntimeError("Simulated crash for testing CORS on 500")

        client_no_raise = TestClient(app, raise_server_exceptions=False)
        resp = client_no_raise.get("/api/test-faulty-endpoint", headers={"Origin": "https://gridbricks-wind-portal.vercel.app"})
        assert resp.status_code == 500
        assert "access-control-allow-origin" in resp.headers
        data = resp.json()
        assert data["detail"] == "Internal Server Error"
        assert "error_type" in data

