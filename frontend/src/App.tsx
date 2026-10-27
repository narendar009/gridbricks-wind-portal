import { useState, useEffect, useCallback } from 'react'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { Wind, Activity, TrendingUp, AlertTriangle, Database, Clock, RefreshCw, ChevronDown, ChevronUp, Info } from 'lucide-react'
import {
  api,
  type SummaryMetrics,
  type TimeSeriesPoint,
  type DailyAccuracyPoint,
  type TimeOfDayPoint,
  type TopErrorRecord,
  type ErrorDistributionBin,
  type MetadataResponse,
  type PaginatedIntervals,
  type IntervalRecord,
} from './services/api'
import './index.css'

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function fmt(v: number | null | undefined, dp = 1): string {
  if (v == null) return '\u2014'
  return v.toFixed(dp)
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// ─────────────────────────────────────────────
// Custom tooltip
// ─────────────────────────────────────────────
interface TooltipPayloadEntry {
  name: string
  value: number | string | null
  color: string
}

function CustomTooltip({ active, payload, label }: {
  active?: boolean
  payload?: TooltipPayloadEntry[]
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="custom-tooltip">
      <div className="custom-tooltip-label">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="custom-tooltip-row">
          <span className="custom-tooltip-dot" style={{ background: p.color }} />
          <span>{p.name}: <strong>{p.value ?? '\u2014'}</strong></span>
        </div>
      ))}
    </div>
  )
}

// ─────────────────────────────────────────────
// Loading / Error / Empty states
// ─────────────────────────────────────────────
function LoadingState({ label }: { label?: string }) {
  return (
    <div className="state-container">
      <div className="state-icon state-icon-loading">
        <div className="spinner" />
      </div>
      <div className="state-title">{label ?? 'Loading\u2026'}</div>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="state-container">
      <div className="state-icon state-icon-error">
        <AlertTriangle size={24} />
      </div>
      <div className="state-title">Failed to load</div>
      <div className="state-body">{message}</div>
    </div>
  )
}

function EmptyState({ label }: { label?: string }) {
  return (
    <div className="state-container">
      <div className="state-icon state-icon-empty">
        <Database size={24} />
      </div>
      <div className="state-title">No data available</div>
      <div className="state-body">{label ?? 'Run ingestion or enable DEMO_MODE to populate data.'}</div>
    </div>
  )
}

// ─────────────────────────────────────────────
// KPI Card
// ─────────────────────────────────────────────
function KpiCard({
  label, value, unit, sublabel, accent = 'teal', icon,
}: {
  label: string
  value: string | number | null
  unit?: string
  sublabel?: string
  accent?: 'teal' | 'amber' | 'red' | 'violet' | 'green'
  icon?: React.ReactNode
}) {
  return (
    <div className={`kpi-card kpi-accent-${accent}`}>
      <div className="kpi-label">
        {icon && <span>{icon}</span>}
        {label}
      </div>
      {value == null ? (
        <div className="kpi-null">\u2014</div>
      ) : (
        <div className="kpi-value">
          {value}
          {unit && <span className="kpi-unit">{unit}</span>}
        </div>
      )}
      {sublabel && <div className="kpi-sublabel">{sublabel}</div>}
    </div>
  )
}

// ─────────────────────────────────────────────
// Header
// ─────────────────────────────────────────────
function Header({ healthy, lastRefresh }: { healthy: boolean; lastRefresh: Date | null }) {
  return (
    <header className="header">
      <div className="header-inner">
        <div className="header-brand">
          <div className="header-logo">
            <Wind size={22} color="white" />
          </div>
          <div>
            <div className="header-title">Wind Forecast Intelligence</div>
            <div className="header-subtitle">UK Grid \u00b7 Elexon BMRS \u00b7 Day-ahead accuracy</div>
          </div>
        </div>
        <div className="header-meta">
          {lastRefresh && (
            <div className="header-badge">
              <Clock size={11} />
              Updated {lastRefresh.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
            </div>
          )}
          <div className="header-badge">
            <span className="dot" style={{ background: healthy ? 'var(--green-400)' : 'var(--red-400)' }} />
            {healthy ? 'API Online' : 'API Offline'}
          </div>
        </div>
      </div>
    </header>
  )
}

// ─────────────────────────────────────────────
// Methodology accordion
// ─────────────────────────────────────────────
function MethodologyAccordion({ meta }: { meta: MetadataResponse | null }) {
  const [open, setOpen] = useState(false)
  if (!meta) return null
  const { cutoff_policy: cp } = meta
  return (
    <div className="methodology-section">
      <button
        className="methodology-toggle"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        id="methodology-toggle"
      >
        <span className="flex items-center gap-2">
          <Info size={15} />
          Methodology &amp; Data Quality
        </span>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open && (
        <div className="methodology-body">
          <div className="methodology-grid">
            <div className="methodology-section-item">
              <h4>Data Sources</h4>
              <ul>
                {Object.entries(meta.data_sources).map(([k, v]) => (
                  <li key={k}><strong>{k}:</strong> {v}</li>
                ))}
              </ul>
            </div>
            <div className="methodology-section-item">
              <h4>Cutoff Policy</h4>
              <p>{cp.description}</p>
              <p>Timezone: <strong>{cp.timezone}</strong> \u00b7 Day offset: <strong>{cp.day_offset}</strong></p>
            </div>
            <div className="methodology-section-item">
              <h4>Coverage</h4>
              <p>Analysis: <strong>{meta.analysis_start}</strong> \u2192 <strong>{meta.analysis_end}</strong></p>
              <p>Expected intervals: <strong>{meta.expected_intervals}</strong></p>
              <p>Actual fetched: <strong>{meta.actual_intervals}</strong></p>
              <p>Forecast records: <strong>{meta.forecast_records}</strong></p>
            </div>
            <div className="methodology-section-item">
              <h4>Data Quality</h4>
              {Object.entries(meta.data_quality).length === 0 ? (
                <p>No quality flags.</p>
              ) : (
                <ul>
                  {Object.entries(meta.data_quality).map(([k, v]) => (
                    <li key={k}><strong>{k}:</strong> {String(v)}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────
// Time-series chart
// ─────────────────────────────────────────────
function TimeSeriesPanel() {
  const [data, setData] = useState<TimeSeriesPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [applied, setApplied] = useState<{ from: string; to: string }>({ from: '', to: '' })

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await api.timeseries(applied.from || undefined, applied.to || undefined)
      setData(res.data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [applied])

  useEffect(() => { load() }, [load])

  const chartData = data.map(d => ({
    time: fmtDate(d.start_time),
    Actual: d.actual_mw,
    Forecast: d.forecast_mw,
  }))

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">
          <span className="icon"><Activity size={15} /></span>
          Time-series: Actual vs Forecast
        </div>
        <div className="controls-bar">
          <input
            id="ts-date-from"
            type="date"
            className="date-input"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            title="From date"
          />
          <input
            id="ts-date-to"
            type="date"
            className="date-input"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            title="To date"
          />
          <button id="ts-apply-btn" className="btn btn-primary" onClick={() => setApplied({ from: dateFrom, to: dateTo })}>
            Apply
          </button>
          <button id="ts-reset-btn" className="btn btn-ghost" onClick={() => { setDateFrom(''); setDateTo(''); setApplied({ from: '', to: '' }) }}>
            Reset
          </button>
        </div>
      </div>
      <div className="card-body">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : data.length === 0 ? <EmptyState /> : (
          <div className="chart-container">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} unit=" MW" width={72} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
                <Line type="monotone" dataKey="Actual" stroke="var(--teal-500)" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="Forecast" stroke="var(--violet-400)" dot={false} strokeWidth={2} strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Forecast Error bar chart
// ─────────────────────────────────────────────
function ErrorSeriesPanel() {
  const [data, setData] = useState<TimeSeriesPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.timeseries()
      .then(r => setData(r.data))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const chartData = data
    .filter(d => d.error_mw != null)
    .map(d => ({ time: fmtDate(d.start_time), Error: d.error_mw }))

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><TrendingUp size={15} /></span> Forecast Error (MW)</div>
      </div>
      <div className="card-body">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : chartData.length === 0 ? <EmptyState /> : (
          <div className="chart-container-sm">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis dataKey="time" tick={{ fontSize: 10 }} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} unit=" MW" width={72} />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={0} stroke="var(--slate-400)" />
                <Bar dataKey="Error" fill="var(--teal-400)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Daily accuracy
// ─────────────────────────────────────────────
function DailyAccuracyPanel() {
  const [data, setData] = useState<DailyAccuracyPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.dailyAccuracy()
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const chartData = data.map(d => ({
    date: fmtDate(d.date),
    MAE: d.mae_mw,
    RMSE: d.rmse_mw,
    Bias: d.bias_mw,
  }))

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><Activity size={15} /></span> Daily Accuracy (MAE / RMSE / Bias)</div>
      </div>
      <div className="card-body">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : data.length === 0 ? <EmptyState /> : (
          <div className="chart-container">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} unit=" MW" width={72} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
                <Line type="monotone" dataKey="MAE" stroke="var(--teal-500)" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="RMSE" stroke="var(--amber-400)" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="Bias" stroke="var(--red-400)" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Time-of-day accuracy
// ─────────────────────────────────────────────
function TimeOfDayPanel() {
  const [data, setData] = useState<TimeOfDayPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.timeOfDay()
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const chartData = data.map(d => ({
    hour: `${String(d.hour_utc).padStart(2, '0')}:00`,
    MAE: d.mae_mw,
    Bias: d.bias_mw,
  }))

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><Clock size={15} /></span> Time-of-Day Accuracy (UTC)</div>
      </div>
      <div className="card-body">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : data.length === 0 ? <EmptyState /> : (
          <div className="chart-container-sm">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis dataKey="hour" tick={{ fontSize: 10 }} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} unit=" MW" width={72} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
                <ReferenceLine y={0} stroke="var(--slate-400)" />
                <Bar dataKey="MAE" fill="var(--teal-400)" radius={[2, 2, 0, 0]} />
                <Bar dataKey="Bias" fill="var(--rose-400)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Error distribution histogram
// ─────────────────────────────────────────────
function ErrorDistributionPanel() {
  const [data, setData] = useState<ErrorDistributionBin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.errorDistribution(20)
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const chartData = data.map(d => ({
    bin: `${d.bin_start.toFixed(0)}`,
    Count: d.count,
  }))

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><TrendingUp size={15} /></span> Error Distribution</div>
      </div>
      <div className="card-body">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : data.length === 0 ? <EmptyState /> : (
          <div className="chart-container-sm">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis
                  dataKey="bin"
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  label={{ value: 'Error (MW)', position: 'insideBottom', offset: -8, fontSize: 11 }}
                />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={48} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="Count" fill="var(--violet-400)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Top errors table
// ─────────────────────────────────────────────
function TopErrorsPanel() {
  const [data, setData] = useState<TopErrorRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.topErrors(15)
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><AlertTriangle size={15} /></span> Top Forecast Errors</div>
      </div>
      <div className="card-body" style={{ padding: '12px 0 0' }}>
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : data.length === 0 ? <EmptyState /> : (
          <div className="table-wrapper" style={{ border: 'none', borderRadius: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Time (UTC)</th>
                  <th>Actual MW</th>
                  <th>Forecast MW</th>
                  <th>Error MW</th>
                  <th>|Error| MW</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r, i) => (
                  <tr key={i}>
                    <td className="td-mono">{fmtDateTime(r.start_time)}</td>
                    <td>{fmt(r.actual_mw)}</td>
                    <td>{fmt(r.forecast_mw)}</td>
                    <td style={{ color: r.error_mw > 0 ? 'var(--green-500)' : 'var(--red-500)' }}>
                      {fmt(r.error_mw)}
                    </td>
                    <td className="font-bold">{fmt(r.abs_error_mw)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Intervals table (paginated + sortable)
// ─────────────────────────────────────────────
type SortKey = 'start_time' | 'actual_mw' | 'forecast_mw' | 'error_mw' | 'abs_error_mw'

// Defined outside IntervalsPanel to avoid react/static-components lint warning
function SortArrow({ col, sortBy, sortOrder }: { col: SortKey; sortBy: SortKey; sortOrder: 'asc' | 'desc' }) {
  return col !== sortBy
    ? <span style={{ opacity: 0.3 }}>&#x21c5;</span>
    : <span>{sortOrder === 'asc' ? '\u2191' : '\u2193'}</span>
}

function IntervalsPanel() {
  const [data, setData] = useState<PaginatedIntervals | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('start_time')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await api.intervals({
        page,
        page_size: 50,
        status: status || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      })
      setData(res)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [page, status, sortBy, sortOrder])

  useEffect(() => { load() }, [load])

  const handleSort = (col: SortKey) => {
    if (col === sortBy) setSortOrder(o => o === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortOrder('asc') }
    setPage(1)
  }

  const statusBadge = (s: string) => {
    const cls = s === 'matched'
      ? 'badge-matched'
      : s.includes('missing') ? 'badge-missing' : 'badge-error'
    return <span className={`badge ${cls}`}>{s}</span>
  }

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title"><span className="icon"><Database size={15} /></span> Interval Records</div>
        <div className="controls-bar">
          <select
            id="intervals-status-filter"
            className="select"
            value={status}
            onChange={e => { setStatus(e.target.value); setPage(1) }}
          >
            <option value="">All statuses</option>
            <option value="matched">Matched</option>
            <option value="missing_forecast">Missing forecast</option>
            <option value="missing_actual">Missing actual</option>
          </select>
          <button id="intervals-refresh-btn" className="btn btn-ghost" onClick={load} title="Refresh">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>
      <div className="card-body" style={{ padding: '12px 0 0' }}>
        {error ? <ErrorState message={error} /> : (
          <>
            {data && (
              <div className="table-wrapper" style={{ border: 'none', borderRadius: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th onClick={() => handleSort('start_time')}>Time <SortArrow col="start_time" sortBy={sortBy} sortOrder={sortOrder} /></th>
                      <th>Date</th>
                      <th onClick={() => handleSort('actual_mw')}>Actual MW <SortArrow col="actual_mw" sortBy={sortBy} sortOrder={sortOrder} /></th>
                      <th onClick={() => handleSort('forecast_mw')}>Forecast MW <SortArrow col="forecast_mw" sortBy={sortBy} sortOrder={sortOrder} /></th>
                      <th onClick={() => handleSort('error_mw')}>Error MW <SortArrow col="error_mw" sortBy={sortBy} sortOrder={sortOrder} /></th>
                      <th onClick={() => handleSort('abs_error_mw')}>|Error| MW <SortArrow col="abs_error_mw" sortBy={sortBy} sortOrder={sortOrder} /></th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      <tr>
                        <td colSpan={7} style={{ textAlign: 'center', padding: '40px' }}>
                          <div className="spinner" style={{ margin: '0 auto' }} />
                        </td>
                      </tr>
                    ) : data.data.length === 0 ? (
                      <tr>
                        <td colSpan={7} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                          No records
                        </td>
                      </tr>
                    ) : data.data.map((r: IntervalRecord, i: number) => (
                      <tr key={i}>
                        <td className="td-mono">{fmtDateTime(r.start_time)}</td>
                        <td className="td-mono">{r.delivery_date}</td>
                        <td>{fmt(r.actual_mw)}</td>
                        <td>{fmt(r.forecast_mw)}</td>
                        <td style={{
                          color: r.error_mw != null
                            ? (r.error_mw > 0 ? 'var(--green-500)' : 'var(--red-500)')
                            : undefined,
                        }}>
                          {fmt(r.error_mw)}
                        </td>
                        <td className="font-bold">{fmt(r.abs_error_mw)}</td>
                        <td>{statusBadge(r.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {data && (
              <div className="pagination" style={{ padding: '12px 24px' }}>
                <div className="pagination-info">
                  {data.total} records \u00b7 page {data.page} of {data.total_pages}
                </div>
                <button
                  id="intervals-prev-btn"
                  className="btn btn-ghost"
                  disabled={page <= 1}
                  onClick={() => setPage(p => p - 1)}
                >
                  \u2190 Prev
                </button>
                <button
                  id="intervals-next-btn"
                  className="btn btn-ghost"
                  disabled={page >= (data.total_pages ?? 1)}
                  onClick={() => setPage(p => p + 1)}
                >
                  Next \u2192
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Root App
// ─────────────────────────────────────────────
export default function App() {
  const [healthy, setHealthy] = useState(false)
  const [meta, setMeta] = useState<MetadataResponse | null>(null)
  const [summary, setSummary] = useState<SummaryMetrics | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  const loadMeta = useCallback(async () => {
    try {
      const [h, m, s] = await Promise.all([api.health(), api.metadata(), api.summary()])
      setHealthy(h.status === 'ok')
      setMeta(m)
      setSummary(s)
    } catch {
      setHealthy(false)
    } finally {
      setSummaryLoading(false)
      setLastRefresh(new Date())
    }
  }, [])

  useEffect(() => { loadMeta() }, [loadMeta])

  return (
    <div className="app-wrapper">
      <Header healthy={healthy} lastRefresh={lastRefresh} />

      <main className="page-content">

        {/* Demo mode banner */}
        {meta?.demo_mode && (
          <div className="policy-banner" style={{ marginBottom: 24 }}>
            <span className="icon"><Info size={14} /></span>
            <div>
              <strong>Demo Mode Active</strong> \u2014 Displaying synthetic data.
              Set <code>DEMO_MODE=false</code> and run ingestion for live Elexon BMRS data.
            </div>
          </div>
        )}

        {/* Cutoff policy note */}
        {meta && (
          <div className="policy-banner">
            <span className="icon"><AlertTriangle size={14} /></span>
            <div>
              <strong>Provisional cutoff policy:</strong> {meta.cutoff_policy.description}
            </div>
          </div>
        )}

        {/* Section heading */}
        <div className="section-header" style={{ marginTop: 28 }}>
          <div>
            <div className="section-title"><Activity size={16} /> Overall Accuracy Metrics</div>
            <div className="section-subtitle">
              {meta ? `${meta.analysis_start} \u2192 ${meta.analysis_end}` : 'Loading period\u2026'}
            </div>
          </div>
        </div>

        {/* KPI grid */}
        {summaryLoading ? (
          <div className="kpi-grid">
            {[...Array(7)].map((_, i) => (
              <div key={i} className="kpi-card" style={{ minHeight: 90, background: 'var(--slate-100)' }} />
            ))}
          </div>
        ) : summary ? (
          <div className="kpi-grid">
            <KpiCard
              label="MAE" value={fmt(summary.mae_mw)} unit=" MW"
              sublabel="Mean absolute error" accent="teal"
              icon={<TrendingUp size={11} />}
            />
            <KpiCard
              label="RMSE" value={fmt(summary.rmse_mw)} unit=" MW"
              sublabel="Root mean square error" accent="amber"
              icon={<TrendingUp size={11} />}
            />
            <KpiCard
              label="Bias" value={fmt(summary.bias_mw)} unit=" MW"
              sublabel="+ve = over-forecast" accent="red"
              icon={<TrendingUp size={11} />}
            />
            <KpiCard
              label="WAPE"
              value={summary.wape_pct != null ? fmt(summary.wape_pct) : null}
              unit="%" sublabel="Weighted abs. % error" accent="violet"
            />
            <KpiCard
              label="Coverage" value={fmt(summary.coverage_pct)} unit="%"
              sublabel={`${summary.valid_intervals} / ${summary.expected_intervals} intervals`}
              accent="green" icon={<Database size={11} />}
            />
            <KpiCard
              label="Missing Forecast" value={summary.missing_forecast_count}
              sublabel="Intervals without forecast" accent="amber"
            />
            <KpiCard
              label="Missing Actual" value={summary.missing_actual_count}
              sublabel="Intervals without actual" accent="red"
            />
          </div>
        ) : null}

        {/* Full-width time-series */}
        <div className="dashboard-grid mt-6">
          <TimeSeriesPanel />
        </div>

        {/* 2-col row: error bars + daily accuracy */}
        <div className="grid-2-col mt-6">
          <ErrorSeriesPanel />
          <DailyAccuracyPanel />
        </div>

        {/* 2-col row: time-of-day + distribution */}
        <div className="grid-2-col mt-6">
          <TimeOfDayPanel />
          <ErrorDistributionPanel />
        </div>

        {/* Top errors */}
        <div className="mt-6">
          <TopErrorsPanel />
        </div>

        {/* Intervals table */}
        <div className="mt-6">
          <IntervalsPanel />
        </div>

        {/* Methodology accordion */}
        <MethodologyAccordion meta={meta} />

      </main>
    </div>
  )
}
