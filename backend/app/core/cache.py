"""
Application-level data cache to avoid recomputing the matched dataset on every request.
Thread-safe via a reentrant lock; the cache is refreshed after CACHE_TTL_SECONDS.

Concurrency fix: all data loading happens outside the lock to avoid blocking
readers during a potentially slow reload. A separate _loading flag prevents
multiple threads from triggering simultaneous reloads.
"""
import logging
import threading
import time
from typing import Any

import pandas as pd

from app.core.config import CACHE_TTL_SECONDS, DEMO_MODE
from app.services.data_quality import assess_data_quality
from app.services.forecast_selector import select_forecast_vintage
from app.services.ingestion import load_actuals, load_forecasts, _load_metadata

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loading = False
_cache: dict[str, Any] = {
    "matched_df": None,
    "actuals_df": None,
    "forecasts_df": None,
    "metadata": {},
    "quality": {},
    "loaded_at": 0.0,
    "demo_mode": False,
}


def _is_stale() -> bool:
    return time.monotonic() - _cache["loaded_at"] > CACHE_TTL_SECONDS


def _load() -> None:
    """
    Load data into the cache.

    Data loading (IO, computation) is performed WITHOUT holding the lock.
    Only the final cache assignment is locked, so readers are never blocked
    during the slow Parquet read and vintage selection.
    """
    global _loading

    # Prevent duplicate concurrent loads
    with _lock:
        if _loading:
            return
        _loading = True

    try:
        logger.info("Loading data into application cache ...")
        actuals = load_actuals()
        forecasts = load_forecasts()
        meta = _load_metadata()

        if actuals.empty and not DEMO_MODE:
            logger.warning(
                "No actuals data found. Dashboard will show empty state. "
                "Run ingestion first: python -m app.services.ingestion"
            )

        matched = (
            select_forecast_vintage(actuals, forecasts)
            if not actuals.empty
            else pd.DataFrame()
        )
        quality = assess_data_quality(actuals, forecasts, matched)

        # Atomically swap the cache contents
        with _lock:
            _cache["actuals_df"] = actuals
            _cache["forecasts_df"] = forecasts
            _cache["matched_df"] = matched
            _cache["metadata"] = meta
            _cache["quality"] = quality
            _cache["loaded_at"] = time.monotonic()
            _cache["demo_mode"] = meta.get("demo_mode", DEMO_MODE)
        logger.info("Cache loaded: %d matched intervals.", len(matched))
    finally:
        with _lock:
            _loading = False


def get_cache() -> dict[str, Any]:
    """
    Return the current cache, refreshing if stale.

    Thread-safe: multiple concurrent readers will all see the same (possibly
    stale-but-consistent) snapshot while a single writer refreshes in the
    background.
    """
    with _lock:
        stale = _is_stale()
    if stale:
        _load()
    return _cache
