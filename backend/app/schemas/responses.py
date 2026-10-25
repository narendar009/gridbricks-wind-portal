"""
Pydantic response schemas for all API endpoints.
"""
from typing import Any, Optional
import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str = "1.0.0"


class CutoffPolicy(BaseModel):
    hour: int
    minute: int
    timezone: str
    day_offset: int
    description: str


class MetadataResponse(BaseModel):
    analysis_start: str
    analysis_end: str
    ingested_at: Optional[str] = None
    expected_intervals: int
    actual_intervals: int
    forecast_records: int
    demo_mode: bool
    cutoff_policy: CutoffPolicy
    data_sources: dict[str, str]
    data_quality: dict[str, Any]


class SummaryMetrics(BaseModel):
    mae_mw: Optional[float] = None
    rmse_mw: Optional[float] = None
    bias_mw: Optional[float] = None
    wape_pct: Optional[float] = None
    valid_intervals: int
    total_intervals: int
    missing_forecast_count: int
    missing_actual_count: int
    coverage_pct: float
    expected_intervals: int
    note: Optional[str] = None


class TimeSeriesPoint(BaseModel):
    start_time: str
    actual_mw: Optional[float] = None
    forecast_mw: Optional[float] = None
    error_mw: Optional[float] = None
    forecast_publish_time: Optional[str] = None
    status: str


class TimeSeriesResponse(BaseModel):
    data: list[TimeSeriesPoint]
    count: int
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class DailyAccuracyPoint(BaseModel):
    date: str
    mae_mw: float
    rmse_mw: float
    bias_mw: float
    wape_pct: Optional[float] = None
    valid_intervals: int


class TimeOfDayPoint(BaseModel):
    hour_utc: int
    mae_mw: float
    bias_mw: float
    count: int


class TopErrorRecord(BaseModel):
    start_time: str
    actual_mw: float
    forecast_mw: float
    error_mw: float
    abs_error_mw: float
    forecast_publish_time: Optional[str] = None


class ErrorDistributionBin(BaseModel):
    bin_start: float
    bin_end: float
    count: int


class IntervalRecord(BaseModel):
    start_time: str
    delivery_date: str
    actual_mw: Optional[float] = None
    forecast_mw: Optional[float] = None
    error_mw: Optional[float] = None
    abs_error_mw: Optional[float] = None
    forecast_publish_time: Optional[str] = None
    status: str


class PaginatedIntervals(BaseModel):
    data: list[IntervalRecord]
    total: int
    page: int
    page_size: int
    total_pages: int
