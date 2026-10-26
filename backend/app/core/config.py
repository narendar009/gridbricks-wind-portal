"""
Application configuration loaded from environment variables.
All defaults are documented clearly; none are official UK market rules.
"""
import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Data directories ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ── Elexon API ────────────────────────────────────────────────────────────────
ELEXON_BASE_URL = os.getenv(
    "ELEXON_BASE_URL", "https://data.elexon.co.uk/bmrs/api/v1"
)
ELEXON_TIMEOUT_CONNECT = float(os.getenv("ELEXON_TIMEOUT_CONNECT", "10"))
ELEXON_TIMEOUT_READ = float(os.getenv("ELEXON_TIMEOUT_READ", "60"))
ELEXON_MAX_RETRIES = int(os.getenv("ELEXON_MAX_RETRIES", "3"))
ELEXON_RETRY_BACKOFF = float(os.getenv("ELEXON_RETRY_BACKOFF", "2.0"))

# ── Analysis date scope ───────────────────────────────────────────────────────
ANALYSIS_START_DATE = os.getenv("ANALYSIS_START_DATE", "2026-01-01")
ANALYSIS_END_DATE = os.getenv("ANALYSIS_END_DATE", "2026-01-31")

# ── Procurement decision cutoff policy ───────────────────────────────────────
# PROVISIONAL DEFAULT: 10:00 Europe/London on the day BEFORE delivery.
# This is NOT an official UK electricity market rule.
# It is a configurable placeholder chosen to represent a plausible
# day-ahead decision cutoff. Adjust via environment variables.
CUTOFF_HOUR = int(os.getenv("CUTOFF_HOUR", "10"))
CUTOFF_MINUTE = int(os.getenv("CUTOFF_MINUTE", "0"))
CUTOFF_TIMEZONE = os.getenv("CUTOFF_TIMEZONE", "Europe/London")
# DAY_OFFSET: number of days before delivery date to apply cutoff.
# Default 1 means "the day before delivery at CUTOFF_HOUR:CUTOFF_MINUTE".
CUTOFF_DAY_OFFSET = int(os.getenv("CUTOFF_DAY_OFFSET", "1"))

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
).split(",")

# ── Caching ───────────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))

# ── Demo mode ─────────────────────────────────────────────────────────────────
# Set DEMO_MODE=true to serve synthetic fixture data when real data is absent.
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

# ── Derived: half-hourly intervals per day ────────────────────────────────────
INTERVALS_PER_DAY = 48  # UK settlement periods per day (30-minute intervals)


def compute_expected_intervals(start_date_str: str | None = None,
                               end_date_str: str | None = None) -> int:
    """
    Compute the expected number of half-hourly intervals for the analysis period.

    Derives from configuration rather than hardcoding, so any date range change
    automatically adjusts the denominator used for coverage calculations.
    """
    start_str = start_date_str or ANALYSIS_START_DATE
    end_str = end_date_str or ANALYSIS_END_DATE
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()
    num_days = (end - start).days + 1  # inclusive of both start and end
    return num_days * INTERVALS_PER_DAY
