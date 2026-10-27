"""
FastAPI route handlers for all endpoints.
"""
import logging
import math
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.core.cache import get_cache
from app.core.config import (
    ANALYSIS_END_DATE,
    ANALYSIS_START_DATE,
    CUTOFF_DAY_OFFSET,
    CUTOFF_HOUR,
    CUTOFF_MINUTE,
    CUTOFF_TIMEZONE,
    DEMO_MODE,
    compute_expected_intervals,
)
from app.schemas.responses import (
    DailyAccuracyPoint,
    ErrorDistributionBin,
    HealthResponse,
    IntervalRecord,
    MetadataResponse,
    PaginatedIntervals,
    SummaryMetrics,
    TimeOfDayPoint,
    TimeSeriesPoint,
    TimeSeriesResponse,
    TopErrorRecord,
    CutoffPolicy,
)
from app.services.metrics import (
    compute_daily_metrics,
    compute_error_distribution,
    compute_summary_metrics,
    compute_time_of_day_metrics,
    compute_top_errors,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["System"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/metadata", response_model=MetadataResponse, tags=["System"])
def metadata() -> MetadataResponse:
    cache = get_cache()
    meta = cache.get("metadata", {})
    quality = cache.get("quality", {})

    cutoff_policy = CutoffPolicy(
        hour=CUTOFF_HOUR,
        minute=CUTOFF_MINUTE,
        timezone=CUTOFF_TIMEZONE,
        day_offset=CUTOFF_DAY_OFFSET,
        description=(
            f"Provisional default: {CUTOFF_HOUR:02d}:{CUTOFF_MINUTE:02d} "
            f"{CUTOFF_TIMEZONE} on the day {CUTOFF_DAY_OFFSET} day(s) before delivery. "
            "NOT an official UK electricity market rule."
        ),
    )

    return MetadataResponse(
        analysis_start=meta.get("analysis_start", ANALYSIS_START_DATE),
        analysis_end=meta.get("analysis_end", ANALYSIS_END_DATE),
        ingested_at=meta.get("ingested_at"),
        expected_intervals=meta.get(
            "expected_half_hourly_intervals", compute_expected_intervals()
        ),
        actual_intervals=meta.get("actual_intervals_fetched", 0),
        forecast_records=meta.get("forecast_records_fetched", 0),
        demo_mode=cache.get("demo_mode", DEMO_MODE),
        cutoff_policy=cutoff_policy,
        data_sources={
            "actuals": "Elexon BMRS FUELHH (fuelType=WIND)",
            "forecasts": "Elexon BMRS WINDFOR",
        },
        data_quality=quality,
    )


@router.get("/summary", response_model=SummaryMetrics, tags=["Analytics"])
def summary() -> SummaryMetrics:
    cache = get_cache()
    matched = cache.get("matched_df")
    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return SummaryMetrics(
            mae_mw=None,
            rmse_mw=None,
            bias_mw=None,
            wape_pct=None,
            valid_intervals=0,
            total_intervals=0,
            missing_forecast_count=0,
            missing_actual_count=0,
            coverage_pct=0.0,
            expected_intervals=compute_expected_intervals(),
            note="No data available. Run ingestion or enable DEMO_MODE.",
        )
    metrics = compute_summary_metrics(matched)
    return SummaryMetrics(**metrics)


def _parse_iso_date(date_str: str, param_name: str) -> date:
    """
    Parse a date string in strict YYYY-MM-DD ISO format.
    Raises HTTPException(400) if malformed or impossible calendar date.
    """
    if not isinstance(date_str, str) or len(date_str) != 10:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name} format: '{date_str}'. Expected YYYY-MM-DD.",
        )
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name} date: '{date_str}'. Must be a valid calendar date in YYYY-MM-DD format.",
        ) from exc


def _validate_date_range(
    date_from: Optional[str], date_to: Optional[str]
) -> tuple[Optional[date], Optional[date]]:
    """Validate date_from and date_to, ensuring ISO validity and date_from <= date_to."""
    d_from = _parse_iso_date(date_from, "date_from") if date_from else None
    d_to = _parse_iso_date(date_to, "date_to") if date_to else None
    if d_from and d_to and d_from > d_to:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date range: date_from ('{date_from}') must be less than or equal to date_to ('{date_to}').",
        )
    return d_from, d_to


@router.get("/timeseries", response_model=TimeSeriesResponse, tags=["Analytics"])
def timeseries(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD filter start"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD filter end"),
) -> TimeSeriesResponse:
    d_from, d_to = _validate_date_range(date_from, date_to)

    cache = get_cache()
    matched = cache.get("matched_df")

    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return TimeSeriesResponse(data=[], count=0, date_from=date_from, date_to=date_to)

    df = matched.copy()
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)

    if d_from:
        dt_from = pd.Timestamp(datetime.combine(d_from, datetime.min.time()), tz="UTC")
        df = df[df["start_time"] >= dt_from]

    if d_to:
        dt_to = pd.Timestamp(datetime.combine(d_to, datetime.min.time()), tz="UTC") + pd.Timedelta(days=1)
        df = df[df["start_time"] < dt_to]

    df = df.sort_values("start_time")

    def _safe_float(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return round(float(v), 1)

    points = []
    for _, row in df.iterrows():
        pts = TimeSeriesPoint(
            start_time=row["start_time"].isoformat(),
            actual_mw=_safe_float(row.get("actual_mw")),
            forecast_mw=_safe_float(row.get("forecast_mw")),
            error_mw=_safe_float(row.get("error_mw")),
            forecast_publish_time=(
                row["forecast_publish_time"].isoformat()
                if pd.notna(row.get("forecast_publish_time"))
                else None
            ),
            status=str(row.get("status", "unknown")),
        )
        points.append(pts)

    return TimeSeriesResponse(
        data=points,
        count=len(points),
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/intervals", response_model=PaginatedIntervals, tags=["Analytics"])
def intervals(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Records per page"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD filter start"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD filter end"),
    status: Optional[str] = Query(None, description="Filter by status: matched | missing_forecast | missing_actual | missing_both"),
    sort_by: Optional[str] = Query("start_time", description="Column to sort by"),
    sort_order: Optional[str] = Query("asc", description="asc | desc"),
) -> PaginatedIntervals:
    d_from, d_to = _validate_date_range(date_from, date_to)

    cache = get_cache()
    matched = cache.get("matched_df")

    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return PaginatedIntervals(data=[], total=0, page=page, page_size=page_size, total_pages=0)

    df = matched.copy()
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)

    if d_from:
        dt_from = pd.Timestamp(datetime.combine(d_from, datetime.min.time()), tz="UTC")
        df = df[df["start_time"] >= dt_from]

    if d_to:
        dt_to = pd.Timestamp(datetime.combine(d_to, datetime.min.time()), tz="UTC") + pd.Timedelta(days=1)
        df = df[df["start_time"] < dt_to]

    if status:
        df = df[df["status"] == status]

    valid_sort_cols = {"start_time", "actual_mw", "forecast_mw", "error_mw", "abs_error_mw"}
    sort_col = sort_by if sort_by in valid_sort_cols else "start_time"
    ascending = sort_order.lower() != "desc"

    try:
        df = df.sort_values(sort_col, ascending=ascending)
    except Exception:
        df = df.sort_values("start_time")

    total = len(df)
    total_pages = max(1, math.ceil(total / page_size))
    start_idx = (page - 1) * page_size
    page_df = df.iloc[start_idx: start_idx + page_size]

    def _safe_float(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return round(float(v), 1)

    records = []
    for _, row in page_df.iterrows():
        records.append(
            IntervalRecord(
                start_time=row["start_time"].isoformat(),
                delivery_date=str(row.get("delivery_date", "")),
                actual_mw=_safe_float(row.get("actual_mw")),
                forecast_mw=_safe_float(row.get("forecast_mw")),
                error_mw=_safe_float(row.get("error_mw")),
                abs_error_mw=_safe_float(row.get("abs_error_mw")),
                forecast_publish_time=(
                    row["forecast_publish_time"].isoformat()
                    if pd.notna(row.get("forecast_publish_time"))
                    else None
                ),
                status=str(row.get("status", "unknown")),
            )
        )

    return PaginatedIntervals(
        data=records,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/accuracy/daily", response_model=list[DailyAccuracyPoint], tags=["Analytics"])
def accuracy_daily() -> list[DailyAccuracyPoint]:
    cache = get_cache()
    matched = cache.get("matched_df")
    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return []
    return [DailyAccuracyPoint(**r) for r in compute_daily_metrics(matched)]


@router.get("/accuracy/time-of-day", response_model=list[TimeOfDayPoint], tags=["Analytics"])
def accuracy_time_of_day() -> list[TimeOfDayPoint]:
    cache = get_cache()
    matched = cache.get("matched_df")
    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return []
    return [TimeOfDayPoint(**r) for r in compute_time_of_day_metrics(matched)]


@router.get("/errors/top", response_model=list[TopErrorRecord], tags=["Analytics"])
def top_errors(n: int = Query(20, ge=1, le=100)) -> list[TopErrorRecord]:
    cache = get_cache()
    matched = cache.get("matched_df")
    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return []
    return [TopErrorRecord(**r) for r in compute_top_errors(matched, n)]


@router.get("/errors/distribution", response_model=list[ErrorDistributionBin], tags=["Analytics"])
def error_distribution(
    bins: int = Query(20, ge=5, le=50)
) -> list[ErrorDistributionBin]:
    cache = get_cache()
    matched = cache.get("matched_df")
    if matched is None or (isinstance(matched, pd.DataFrame) and matched.empty):
        return []
    return [ErrorDistributionBin(**r) for r in compute_error_distribution(matched, bins)]
