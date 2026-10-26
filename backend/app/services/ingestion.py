"""
Data ingestion pipeline.

Downloads FUELHH (actuals, half-hourly) and WINDFOR (forecasts, hourly) from Elexon,
normalizes schema, handles the temporal resolution mismatch, and saves to data/.

IMPORTANT: FUELHH is half-hourly (48 records/day); WINDFOR is hourly (24/day).
For matching:
  - The actual interval startTime (half-hourly) is matched to the forecast
    for the containing hour: forecast_hour = floor(actual_startTime to nearest hour).
  - This means each forecast covers 2 half-hourly actual intervals.
  - This is documented in the methodology and disclosed in the UI.

Run independently:
    python -m app.services.ingestion
    python -m app.services.ingestion --force   # re-download even if cached
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.core.config import (
    ANALYSIS_END_DATE,
    ANALYSIS_START_DATE,
    CUTOFF_DAY_OFFSET,
    CUTOFF_HOUR,
    CUTOFF_MINUTE,
    CUTOFF_TIMEZONE,
    DEMO_MODE,
    PROCESSED_DIR,
    RAW_DIR,
    compute_expected_intervals,
)
from app.services.elexon_client import fetch_fuelhh_wind, fetch_windfor

logger = logging.getLogger(__name__)

_ACTUALS_RAW = RAW_DIR / "fuelhh_wind_raw.json"
_FORECASTS_RAW = RAW_DIR / "windfor_raw.json"
_ACTUALS_PARQUET = PROCESSED_DIR / "actuals.parquet"
_FORECASTS_PARQUET = PROCESSED_DIR / "forecasts.parquet"
_META_FILE = PROCESSED_DIR / "metadata.json"


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalise_actuals(records: list[dict]) -> pd.DataFrame:
    """
    Normalise FUELHH records.

    Output schema:
        start_time (UTC-aware datetime64): interval start
        actual_mw  (float): generation in MW
    """
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["start_time", "actual_mw"])

    # Defensive: keep only WIND
    if "fuelType" in df.columns:
        df = df[df["fuelType"] == "WIND"]

    df = df.rename(columns={"startTime": "start_time", "generation": "actual_mw"})
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["actual_mw"] = pd.to_numeric(df["actual_mw"], errors="coerce")

    df = df[["start_time", "actual_mw"]].dropna(subset=["start_time"])

    # Deduplicate: same startTime → keep last (most recently published actual)
    df = df.sort_values("start_time").drop_duplicates(subset=["start_time"], keep="last")

    return df.reset_index(drop=True)


def _normalise_forecasts(records: list[dict]) -> pd.DataFrame:
    """
    Normalise WINDFOR records.

    WINDFOR is hourly. Multiple forecast publications exist per delivery hour.
    We keep ALL publications to enable vintage selection.

    Output schema:
        start_time   (UTC-aware datetime64): forecast target hour
        publish_time (UTC-aware datetime64): when this forecast was published
        forecast_mw  (float): forecast generation in MW
    """
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["start_time", "publish_time", "forecast_mw"])

    df = df.rename(
        columns={
            "startTime": "start_time",
            "publishTime": "publish_time",
            "generation": "forecast_mw",
        }
    )
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["publish_time"] = pd.to_datetime(df["publish_time"], utc=True)
    df["forecast_mw"] = pd.to_numeric(df["forecast_mw"], errors="coerce")

    df = df[["start_time", "publish_time", "forecast_mw"]].dropna(
        subset=["start_time", "publish_time"]
    )

    # Deduplicate (startTime, publishTime) deterministically
    df = df.sort_values(["start_time", "publish_time", "forecast_mw"])
    df = df.drop_duplicates(subset=["start_time", "publish_time"], keep="first")

    return df.reset_index(drop=True)


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _forecast_publish_window(
    analysis_start: date, analysis_end: date
) -> tuple[datetime, datetime]:
    """
    Determine publish-time window for WINDFOR fetching.

    We need forecasts published from midnight UTC on (analysis_start - 1 day)
    through the end of the last analysis day. This captures all forecast
    publications that could have been eligible under the day-ahead cutoff policy.
    """
    # Earliest possible eligible publish time: start of the day before analysis_start
    from_dt = datetime(
        analysis_start.year,
        analysis_start.month,
        analysis_start.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    ) - timedelta(days=2)  # extra day buffer for timezone edge cases

    # Latest publish time: end of last analysis day
    to_dt = datetime(
        analysis_end.year,
        analysis_end.month,
        analysis_end.day,
        23, 59, 59,
        tzinfo=timezone.utc,
    )
    return from_dt, to_dt


# ── Main ingestion ────────────────────────────────────────────────────────────

def run_ingestion(
    start_date_str: str | None = None,
    end_date_str: str | None = None,
    force: bool = False,
) -> dict:
    """
    Download, normalize, and persist actuals and forecasts.

    Returns metadata dict.
    """
    start_str = start_date_str or ANALYSIS_START_DATE
    end_str = end_date_str or ANALYSIS_END_DATE
    start = _parse_date(start_str)
    end = _parse_date(end_str)

    if DEMO_MODE:
        logger.warning("DEMO_MODE active; skipping real API calls.")
        return _load_metadata()

    ingested_at = datetime.now(timezone.utc).isoformat()

    # ── Actuals (FUELHH, half-hourly) ─────────────────────────────────────────
    if force or not _ACTUALS_PARQUET.exists():
        logger.info("Fetching FUELHH WIND actuals %s → %s", start, end)
        raw_actuals = fetch_fuelhh_wind(start, end)
        _ACTUALS_RAW.write_text(json.dumps(raw_actuals, default=str))
        logger.info("Saved %d raw actual records.", len(raw_actuals))

        actuals_df = _normalise_actuals(raw_actuals)
        actuals_df.to_parquet(_ACTUALS_PARQUET, index=False)
        logger.info("Normalised actuals: %d rows.", len(actuals_df))
    else:
        actuals_df = pd.read_parquet(_ACTUALS_PARQUET)
        logger.info("Actuals cached: %d rows.", len(actuals_df))

    # ── Forecasts (WINDFOR, hourly) ───────────────────────────────────────────
    if force or not _FORECASTS_PARQUET.exists():
        from_dt, to_dt = _forecast_publish_window(start, end)
        logger.info("Fetching WINDFOR publishTime %s → %s", from_dt, to_dt)
        raw_forecasts = fetch_windfor(from_dt, to_dt)
        _FORECASTS_RAW.write_text(json.dumps(raw_forecasts, default=str))
        logger.info("Saved %d raw forecast records.", len(raw_forecasts))

        forecasts_df = _normalise_forecasts(raw_forecasts)
        forecasts_df.to_parquet(_FORECASTS_PARQUET, index=False)
        logger.info("Normalised forecasts: %d rows.", len(forecasts_df))
    else:
        forecasts_df = pd.read_parquet(_FORECASTS_PARQUET)
        logger.info("Forecasts cached: %d rows.", len(forecasts_df))

    # ── Coverage stats ────────────────────────────────────────────────────────
    expected_intervals = compute_expected_intervals(start_str, end_str)
    actual_intervals = len(actuals_df)

    meta = {
        "ingested_at": ingested_at,
        "analysis_start": start_str,
        "analysis_end": end_str,
        "expected_half_hourly_intervals": expected_intervals,
        "actual_intervals_fetched": actual_intervals,
        "forecast_records_fetched": len(forecasts_df),
        "actuals_parquet": _ACTUALS_PARQUET.name,
        "forecasts_parquet": _FORECASTS_PARQUET.name,
        "demo_mode": False,
        "note_temporal_resolution": (
            "FUELHH actuals are half-hourly; WINDFOR forecasts are hourly. "
            "Each actual interval is matched to the forecast for the containing hour "
            "(actual startTime floored to nearest hour)."
        ),
        "cutoff_policy": {
            "hour": CUTOFF_HOUR,
            "minute": CUTOFF_MINUTE,
            "timezone": CUTOFF_TIMEZONE,
            "day_offset": CUTOFF_DAY_OFFSET,
            "description": (
                f"Provisional default: {CUTOFF_HOUR:02d}:{CUTOFF_MINUTE:02d} "
                f"{CUTOFF_TIMEZONE} on the day {CUTOFF_DAY_OFFSET} day(s) before delivery. "
                "NOT an official UK electricity market rule."
            ),
        },
        "sources": {
            "actuals": "Elexon BMRS FUELHH (fuelType=WIND)",
            "forecasts": "Elexon BMRS WINDFOR",
        },
    }
    _META_FILE.write_text(json.dumps(meta, indent=2))
    logger.info("Metadata written to %s", _META_FILE)
    return meta


def _load_metadata() -> dict:
    if _META_FILE.exists():
        return json.loads(_META_FILE.read_text())
    return {}


def load_actuals() -> pd.DataFrame:
    if _ACTUALS_PARQUET.exists():
        return pd.read_parquet(_ACTUALS_PARQUET)
    return pd.DataFrame(columns=["start_time", "actual_mw"])


def load_forecasts() -> pd.DataFrame:
    if _FORECASTS_PARQUET.exists():
        return pd.read_parquet(_FORECASTS_PARQUET)
    return pd.DataFrame(columns=["start_time", "publish_time", "forecast_mw"])


if __name__ == "__main__":
    import sys
    from app.core.logging_config import configure_logging

    configure_logging("INFO")
    force_flag = "--force" in sys.argv
    meta = run_ingestion(force=force_flag)
    print(json.dumps(meta, indent=2, default=str))
