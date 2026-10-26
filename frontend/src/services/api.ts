// API service layer — all backend communication goes through here
const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? 'http://localhost:8000' : 'https://gridbricks-wind-portal.onrender.com')
).replace(/\/+$/, '');

export interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
}

export interface CutoffPolicy {
  hour: number;
  minute: number;
  timezone: string;
  day_offset: number;
  description: string;
}

export interface MetadataResponse {
  analysis_start: string;
  analysis_end: string;
  ingested_at: string | null;
  expected_intervals: number;
  actual_intervals: number;
  forecast_records: number;
  demo_mode: boolean;
  cutoff_policy: CutoffPolicy;
  data_sources: Record<string, string>;
  data_quality: Record<string, unknown>;
}

export interface SummaryMetrics {
  mae_mw: number | null;
  rmse_mw: number | null;
  bias_mw: number | null;
  wape_pct: number | null;
  valid_intervals: number;
  total_intervals: number;
  missing_forecast_count: number;
  missing_actual_count: number;
  coverage_pct: number;
  expected_intervals: number;
  note?: string;
}

export interface TimeSeriesPoint {
  start_time: string;
  actual_mw: number | null;
  forecast_mw: number | null;
  error_mw: number | null;
  forecast_publish_time: string | null;
  status: string;
}

export interface TimeSeriesResponse {
  data: TimeSeriesPoint[];
  count: number;
  date_from: string | null;
  date_to: string | null;
}

export interface DailyAccuracyPoint {
  date: string;
  mae_mw: number;
  rmse_mw: number;
  bias_mw: number;
  wape_pct: number | null;
  valid_intervals: number;
}

export interface TimeOfDayPoint {
  hour_utc: number;
  mae_mw: number;
  bias_mw: number;
  count: number;
}

export interface TopErrorRecord {
  start_time: string;
  actual_mw: number;
  forecast_mw: number;
  error_mw: number;
  abs_error_mw: number;
  forecast_publish_time: string | null;
}

export interface ErrorDistributionBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface IntervalRecord {
  start_time: string;
  delivery_date: string;
  actual_mw: number | null;
  forecast_mw: number | null;
  error_mw: number | null;
  abs_error_mw: number | null;
  forecast_publish_time: string | null;
  status: string;
}

export interface PaginatedIntervals {
  data: IntervalRecord[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

async function fetchJSON<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v));
      }
    });
  }
  const resp = await fetch(url.toString());
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  health: () => fetchJSON<HealthResponse>('/api/health'),
  metadata: () => fetchJSON<MetadataResponse>('/api/metadata'),
  summary: () => fetchJSON<SummaryMetrics>('/api/summary'),
  timeseries: (dateFrom?: string, dateTo?: string) =>
    fetchJSON<TimeSeriesResponse>('/api/timeseries', { date_from: dateFrom, date_to: dateTo }),
  dailyAccuracy: () => fetchJSON<DailyAccuracyPoint[]>('/api/accuracy/daily'),
  timeOfDay: () => fetchJSON<TimeOfDayPoint[]>('/api/accuracy/time-of-day'),
  topErrors: (n?: number) => fetchJSON<TopErrorRecord[]>('/api/errors/top', { n }),
  errorDistribution: (bins?: number) => fetchJSON<ErrorDistributionBin[]>('/api/errors/distribution', { bins }),
  intervals: (params: {
    page?: number;
    page_size?: number;
    date_from?: string;
    date_to?: string;
    status?: string;
    sort_by?: string;
    sort_order?: string;
  }) => fetchJSON<PaginatedIntervals>('/api/intervals', params),
};
