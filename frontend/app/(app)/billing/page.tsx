"use client"

import { useEffect, useState, useCallback } from "react"
import { apiGet } from "@/lib/api"
import type { UsageMetricOut, MetricsSummaryOut, QuotaOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  CreditCard,
  MessageSquare,
  TrendingUp,
  Cpu,
  Database,
  AlertCircle,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"

// ── SVG bar chart ──────────────────────────────────────────────────────────────

interface BarData {
  label: string
  valueIn: number
  valueOut: number
}

function BarChart({ data, maxVal }: { data: BarData[]; maxVal: number }) {
  const W = 560
  const H = 120
  const PAD_LEFT = 36
  const PAD_BOTTOM = 24
  const BAR_GAP = 2
  const chartW = W - PAD_LEFT
  const chartH = H - PAD_BOTTOM

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-zinc-400">
        Sin datos en el período
      </div>
    )
  }

  const barGroupW = chartW / data.length
  const barW = Math.max(4, barGroupW / 2 - BAR_GAP)
  const scale = maxVal > 0 ? chartH / maxVal : 1

  // Etiquetas del eje Y (0, 25%, 50%, 75%, 100%)
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(maxVal * f))

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      aria-label="Gráfico de mensajes por día"
    >
      {/* Líneas guía */}
      {yTicks.map(tick => {
        const y = chartH - tick * scale
        return (
          <g key={tick}>
            <line
              x1={PAD_LEFT} y1={y} x2={W} y2={y}
              stroke="currentColor"
              strokeWidth="0.5"
              className="text-zinc-200 dark:text-zinc-700"
            />
            <text
              x={PAD_LEFT - 4} y={y + 3}
              textAnchor="end"
              fontSize="8"
              className="fill-zinc-400"
            >
              {tick >= 1000 ? `${Math.round(tick / 1000)}k` : tick}
            </text>
          </g>
        )
      })}

      {/* Barras */}
      {data.map((d, i) => {
        const x = PAD_LEFT + i * barGroupW
        const hIn = d.valueIn * scale
        const hOut = d.valueOut * scale
        // etiqueta cada ~7 días
        const showLabel = data.length <= 7 || i % Math.ceil(data.length / 7) === 0

        return (
          <g key={i}>
            {/* Mensajes entrantes */}
            <rect
              x={x + BAR_GAP}
              y={chartH - hIn}
              width={barW}
              height={hIn}
              rx="1"
              className="fill-sky-400 dark:fill-sky-500"
            >
              <title>{`${d.label}: ${d.valueIn} recibidos`}</title>
            </rect>
            {/* Mensajes salientes */}
            <rect
              x={x + BAR_GAP + barW + 1}
              y={chartH - hOut}
              width={barW}
              height={hOut}
              rx="1"
              className="fill-indigo-400 dark:fill-indigo-500"
            >
              <title>{`${d.label}: ${d.valueOut} enviados`}</title>
            </rect>
            {showLabel && (
              <text
                x={x + barGroupW / 2}
                y={H - 4}
                textAnchor="middle"
                fontSize="7"
                className="fill-zinc-400"
              >
                {d.label.slice(5)} {/* MM-DD */}
              </text>
            )}
          </g>
        )
      })}

      {/* Eje X */}
      <line
        x1={PAD_LEFT} y1={chartH} x2={W} y2={chartH}
        stroke="currentColor"
        strokeWidth="0.5"
        className="text-zinc-300 dark:text-zinc-600"
      />
    </svg>
  )
}

// ── Barra de cuota ─────────────────────────────────────────────────────────────

function UsageBar({
  label,
  icon: Icon,
  used,
  max,
  unit = "",
}: {
  label: string
  icon: React.ElementType
  used: number
  max: number | null
  unit?: string
}) {
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : null

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-1.5 text-zinc-600 dark:text-zinc-400">
          <Icon className="w-3.5 h-3.5" />
          {label}
        </div>
        <span className="text-xs text-zinc-500">
          {formatNum(used)}{unit}
          {max ? ` / ${formatNum(max)}${unit}` : " (sin límite)"}
        </span>
      </div>
      {pct !== null && (
        <div className="h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all",
              pct >= 95 ? "bg-red-500" : pct >= 80 ? "bg-yellow-500" : "bg-indigo-500",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      {pct !== null && pct >= 80 && (
        <p className={cn("text-xs", pct >= 95 ? "text-red-600 dark:text-red-400" : "text-yellow-600 dark:text-yellow-400")}>
          {pct >= 95 ? "Límite casi alcanzado — considera actualizar tu plan." : `${pct}% del límite usado.`}
        </p>
      )}
    </div>
  )
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

// ── Período selector ───────────────────────────────────────────────────────────

type Period = "7d" | "30d" | "month"

function getPeriod(period: Period): { from: string; to: string } {
  const to = new Date()
  const from = new Date()
  if (period === "7d") {
    from.setDate(to.getDate() - 6)
  } else if (period === "30d") {
    from.setDate(to.getDate() - 29)
  } else {
    from.setDate(1) // primer día del mes
  }
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
  }
}

// ── Página ─────────────────────────────────────────────────────────────────────

export default function BillingPage() {
  const [summary, setSummary] = useState<MetricsSummaryOut | null>(null)
  const [quotas, setQuotas] = useState<QuotaOut | null>(null)
  const [dailyMetrics, setDailyMetrics] = useState<UsageMetricOut[]>([])
  const [loading, setLoading] = useState(true)
  const [chartLoading, setChartLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [period, setPeriod] = useState<Period>("30d")

  const loadSummaryAndQuotas = useCallback(async () => {
    try {
      const [s, q] = await Promise.all([
        apiGet<MetricsSummaryOut>("/v1/metrics/summary"),
        apiGet<QuotaOut>("/v1/quotas"),
      ])
      setSummary(s)
      setQuotas(q)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar datos")
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDaily = useCallback(async (p: Period) => {
    setChartLoading(true)
    try {
      const { from, to } = getPeriod(p)
      const metrics = await apiGet<UsageMetricOut[]>("/v1/metrics", { from, to })
      setDailyMetrics(metrics)
    } catch {
      setDailyMetrics([])
    } finally {
      setChartLoading(false)
    }
  }, [])

  useEffect(() => { loadSummaryAndQuotas() }, [loadSummaryAndQuotas])
  useEffect(() => { loadDaily(period) }, [loadDaily, period])

  const barData: BarData[] = dailyMetrics.map(m => ({
    label: m.metric_date,
    valueIn: m.messages_in,
    valueOut: m.messages_out,
  }))
  const maxVal = Math.max(1, ...barData.flatMap(d => [d.valueIn, d.valueOut]))

  const PERIODS: { value: Period; label: string }[] = [
    { value: "7d", label: "7 días" },
    { value: "30d", label: "30 días" },
    { value: "month", label: "Este mes" },
  ]

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <CreditCard className="w-5 h-5 text-zinc-400" />
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Uso y facturación</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Consumo del mes actual y cuotas de tu plan</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Resumen del mes */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Resumen del mes</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="animate-pulse space-y-2">
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-2/3" />
                  <div className="h-7 bg-zinc-200 dark:bg-zinc-700 rounded w-1/2" />
                </div>
              ))}
            </div>
          ) : summary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { icon: MessageSquare, label: "Mensajes in", value: formatNum(summary.messages_in), color: "text-sky-600 dark:text-sky-400" },
                { icon: TrendingUp, label: "Mensajes out", value: formatNum(summary.messages_out), color: "text-indigo-600 dark:text-indigo-400" },
                { icon: Cpu, label: "Coste LLM", value: formatCents(summary.llm_cost_cents), color: "text-purple-600 dark:text-purple-400" },
                { icon: Database, label: "Peticiones API", value: formatNum(summary.api_requests), color: "text-zinc-600 dark:text-zinc-400" },
              ].map(({ icon: Icon, label, value, color }) => (
                <div key={label} className="space-y-1">
                  <div className={cn("flex items-center gap-1.5 text-xs", color)}>
                    <Icon className="w-3.5 h-3.5" />
                    {label}
                  </div>
                  <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{value}</p>
                </div>
              ))}
            </div>
          )}
          {summary && (
            <p className="mt-4 text-xs text-zinc-400">
              Período: {new Date(summary.period_start).toLocaleDateString("es")} – {new Date(summary.period_end).toLocaleDateString("es")}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Cuotas */}
      {!loading && quotas && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Cuotas del plan</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <UsageBar
              label="Mensajes / mes"
              icon={MessageSquare}
              used={(summary?.messages_in ?? 0) + (summary?.messages_out ?? 0)}
              max={quotas.max_messages_per_month}
            />
            <UsageBar
              label="Coste LLM / mes"
              icon={Cpu}
              used={Math.round(summary?.llm_cost_cents ?? 0)}
              max={quotas.max_llm_cost_cents_per_month}
              unit="¢"
            />
            <UsageBar
              label="Almacenamiento"
              icon={Database}
              used={Math.round((summary?.storage_bytes ?? 0) / 1024 / 1024)}
              max={quotas.max_storage_bytes ? Math.round(quotas.max_storage_bytes / 1024 / 1024) : null}
              unit=" MB"
            />
            <UsageBar
              label="Peticiones API / día"
              icon={TrendingUp}
              used={0}
              max={quotas.max_api_requests_per_day}
            />
          </CardContent>
        </Card>
      )}

      {/* Gráfico histórico */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-medium">Mensajes por día</CardTitle>
            <div className="flex gap-1">
              {PERIODS.map(({ value, label }) => (
                <Button
                  key={value}
                  variant={period === value ? "default" : "outline"}
                  size="sm"
                  className="h-7 text-xs px-2.5"
                  onClick={() => setPeriod(value)}
                >
                  {label}
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {chartLoading ? (
            <div className="flex items-center justify-center h-32">
              <Loader2 className="w-5 h-5 animate-spin text-zinc-400" />
            </div>
          ) : (
            <>
              <BarChart data={barData} maxVal={maxVal} />
              {/* Leyenda */}
              <div className="flex items-center gap-4 mt-2">
                <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                  <span className="w-3 h-3 rounded-sm bg-sky-400 dark:bg-sky-500 inline-block" />
                  Recibidos
                </span>
                <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                  <span className="w-3 h-3 rounded-sm bg-indigo-400 dark:bg-indigo-500 inline-block" />
                  Enviados
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Tabla de días */}
      {!chartLoading && dailyMetrics.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Detalle diario</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-zinc-100 dark:border-zinc-800">
                    <th className="text-left py-2 text-zinc-500 font-medium">Fecha</th>
                    <th className="text-right py-2 text-zinc-500 font-medium">Recibidos</th>
                    <th className="text-right py-2 text-zinc-500 font-medium">Enviados</th>
                    <th className="text-right py-2 text-zinc-500 font-medium">Coste LLM</th>
                    <th className="text-right py-2 text-zinc-500 font-medium">API reqs</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-50 dark:divide-zinc-900">
                  {dailyMetrics.map(m => (
                    <tr key={m.metric_date} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/30">
                      <td className="py-1.5 text-zinc-700 dark:text-zinc-300 font-mono">{m.metric_date}</td>
                      <td className="py-1.5 text-right text-zinc-900 dark:text-zinc-100">{m.messages_in}</td>
                      <td className="py-1.5 text-right text-zinc-900 dark:text-zinc-100">{m.messages_out}</td>
                      <td className="py-1.5 text-right text-zinc-500">{formatCents(m.llm_cost_cents)}</td>
                      <td className="py-1.5 text-right text-zinc-500">{m.api_requests}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
