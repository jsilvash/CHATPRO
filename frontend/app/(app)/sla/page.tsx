"use client"

import { useState, useEffect, useCallback } from "react"
import { apiGet } from "@/lib/api"
import type { SLAReport } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Clock, TrendingUp, RefreshCw, AlertCircle, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

function toISODate(d: Date) {
  return d.toISOString().split("T")[0]
}

function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—"
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const sec = Math.round(s % 60)
    return sec > 0 ? `${m}m ${sec}s` : `${m}m`
  }
  return `${(s / 3600).toFixed(1)}h`
}

function MetricRow({
  label,
  avg,
  p50,
  p90,
}: {
  label: string
  avg: number | null
  p50: number | null
  p90: number | null
}) {
  const noData = avg === null && p50 === null && p90 === null
  return (
    <div className="space-y-2">
      <p className="text-xs text-zinc-500">{label}</p>
      <div className="grid grid-cols-3 gap-3">
        <div className="text-center p-3 rounded-lg bg-zinc-50 dark:bg-zinc-800">
          <p className="text-xs text-zinc-500 mb-1">Promedio</p>
          <p className={cn("text-xl font-bold", noData ? "text-zinc-300 dark:text-zinc-600" : "text-zinc-900 dark:text-zinc-50")}>
            {formatSeconds(avg)}
          </p>
        </div>
        <div className="text-center p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20">
          <p className="text-xs text-blue-500 mb-1">P50</p>
          <p className={cn("text-xl font-bold", noData ? "text-zinc-300 dark:text-zinc-600" : "text-blue-700 dark:text-blue-300")}>
            {formatSeconds(p50)}
          </p>
        </div>
        <div className="text-center p-3 rounded-lg bg-orange-50 dark:bg-orange-900/20">
          <p className="text-xs text-orange-500 mb-1">P90</p>
          <p className={cn("text-xl font-bold", noData ? "text-zinc-300 dark:text-zinc-600" : "text-orange-700 dark:text-orange-300")}>
            {formatSeconds(p90)}
          </p>
        </div>
      </div>
      {noData && (
        <p className="text-xs text-zinc-400 text-center">Sin datos para el período seleccionado</p>
      )}
    </div>
  )
}

export default function SLAPage() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today)
  thirtyDaysAgo.setDate(today.getDate() - 30)

  const [dateFrom, setDateFrom] = useState(toISODate(thirtyDaysAgo))
  const [dateTo, setDateTo] = useState(toISODate(today))
  const [report, setReport] = useState<SLAReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchReport = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiGet<SLAReport>("/v1/inbox/sla-report", {
        date_from: dateFrom,
        date_to: dateTo,
      })
      setReport(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar reporte SLA")
    } finally {
      setLoading(false)
    }
  }, [dateFrom, dateTo])

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Reporte SLA</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            Tiempos de respuesta y resolución de conversaciones
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchReport} disabled={loading}>
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Actualizar
        </Button>
      </div>

      {/* Filtros de fecha */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-end gap-4">
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="date_from">Desde</Label>
              <Input
                id="date_from"
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </div>
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="date_to">Hasta</Label>
              <Input
                id="date_to"
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading && !report ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
        </div>
      ) : report ? (
        <div className="space-y-4">
          {/* Resumen */}
          <div className="grid grid-cols-2 gap-4">
            <Card>
              <CardContent className="pt-5 text-center">
                <p className="text-xs text-zinc-500 mb-1">Total conversaciones</p>
                <p className="text-3xl font-bold text-zinc-900 dark:text-zinc-50">
                  {report.total_conversations}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5 text-center">
                <p className="text-xs text-zinc-500 mb-1">Resueltas</p>
                <p className="text-3xl font-bold text-zinc-900 dark:text-zinc-50">
                  {report.resolved_conversations}
                </p>
                {report.total_conversations > 0 && (
                  <p className="text-xs text-zinc-400 mt-1">
                    {Math.round((report.resolved_conversations / report.total_conversations) * 100)}% del total
                  </p>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Clock className="w-4 h-4 text-blue-500" />
                Primera respuesta
              </CardTitle>
            </CardHeader>
            <CardContent>
              <MetricRow
                label="Tiempo hasta la primera respuesta (bot o agente)"
                avg={report.avg_first_response_seconds}
                p50={report.p50_first_response_seconds}
                p90={report.p90_first_response_seconds}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-green-500" />
                Resolución
              </CardTitle>
            </CardHeader>
            <CardContent>
              <MetricRow
                label="Tiempo total desde apertura hasta cierre"
                avg={report.avg_resolution_seconds}
                p50={report.p50_resolution_seconds}
                p90={report.p90_resolution_seconds}
              />
            </CardContent>
          </Card>

          <p className="text-xs text-zinc-400">
            Período: {new Date(report.date_from).toLocaleDateString("es")} —{" "}
            {new Date(report.date_to).toLocaleDateString("es")}
          </p>
        </div>
      ) : null}
    </div>
  )
}
