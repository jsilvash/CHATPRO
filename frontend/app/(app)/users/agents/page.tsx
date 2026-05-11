"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { AvailableAgent, AvailableAgentsResponse, AgentMetricsOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ArrowLeft, UserCircle, Loader2, AlertCircle, RefreshCw, BarChart2 } from "lucide-react"
import { cn } from "@/lib/utils"

function LoadBar({ value, max }: { value: number; max: number }) {
  const pct = max === 0 ? 0 : Math.round((value / max) * 100)
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            pct > 70 ? "bg-red-400" : pct > 40 ? "bg-yellow-400" : "bg-green-400",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400 w-10 text-right">{value} conv.</span>
    </div>
  )
}

function fmtSec(sec: number | null) {
  if (sec == null) return "—"
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.round(sec / 60)}m`
}

interface AgentRow {
  agent: AvailableAgent
  metrics: AgentMetricsOut | null
  loadingMetrics: boolean
}

export default function AvailableAgentsPage() {
  const today = new Date().toISOString().slice(0, 10)
  const thirtyDaysAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10)

  const [rows, setRows] = useState<AgentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState(thirtyDaysAgo)
  const [dateTo, setDateTo] = useState(today)
  const [showMetrics, setShowMetrics] = useState(false)
  const [fetchingMetrics, setFetchingMetrics] = useState(false)

  useEffect(() => {
    setLoading(true)
    apiGet<AvailableAgentsResponse>("/v1/users/available-agents")
      .then((d) => setRows(d.items.map(a => ({ agent: a, metrics: null, loadingMetrics: false }))))
      .catch((e) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false))
  }, [])

  async function loadMetrics() {
    if (rows.length === 0) return
    setFetchingMetrics(true)
    setShowMetrics(true)

    const updated = await Promise.all(
      rows.map(async (row) => {
        try {
          const m = await apiGet<AgentMetricsOut>(`/v1/users/${row.agent.id}/metrics`, {
            date_from: dateFrom,
            date_to: dateTo,
          })
          return { ...row, metrics: m, loadingMetrics: false }
        } catch {
          return { ...row, metrics: null, loadingMetrics: false }
        }
      })
    )
    setRows(updated)
    setFetchingMetrics(false)
  }

  const maxConvs = Math.max(1, ...rows.map((r) => r.agent.conv_count))

  return (
    <div className="p-6 max-w-4xl space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/users">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
            Agentes disponibles
          </h1>
          <p className="text-sm text-zinc-500">Carga de trabajo y métricas por agente</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Filtros de período */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-zinc-500" />
            Métricas por período
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <Label className="text-xs text-zinc-500">Desde</Label>
              <Input
                type="date"
                value={dateFrom}
                onChange={e => setDateFrom(e.target.value)}
                className="mt-1 h-8 text-xs w-36"
              />
            </div>
            <div>
              <Label className="text-xs text-zinc-500">Hasta</Label>
              <Input
                type="date"
                value={dateTo}
                onChange={e => setDateTo(e.target.value)}
                className="mt-1 h-8 text-xs w-36"
              />
            </div>
            <Button
              size="sm"
              onClick={loadMetrics}
              disabled={fetchingMetrics || loading}
              className="gap-1.5 h-8"
            >
              {fetchingMetrics ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Calcular métricas
            </Button>
          </div>
        </CardContent>
      </Card>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
        </div>
      ) : rows.length === 0 ? (
        <p className="text-center text-sm text-zinc-400 py-12">No hay agentes disponibles.</p>
      ) : showMetrics ? (
        /* Vista tabla comparativa con métricas */
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/50">
                    <th className="text-left py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">Agente</th>
                    <th className="text-right py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">Conv. activas</th>
                    <th className="text-right py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">Resueltas período</th>
                    <th className="text-right py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">T. primera respuesta</th>
                    <th className="text-right py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">T. resolución prom.</th>
                    <th className="text-right py-2.5 px-4 text-xs font-medium text-zinc-500 uppercase tracking-wide">Mensajes enviados</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.agent.id} className="border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800/30">
                      <td className="py-3 px-4">
                        <Link href={`/users/${row.agent.id}`} className="flex items-center gap-2 group">
                          <div className="p-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800">
                            <UserCircle className="w-4 h-4 text-zinc-500" />
                          </div>
                          <div>
                            <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 group-hover:text-blue-600 dark:group-hover:text-blue-400 truncate max-w-[140px]">
                              {row.agent.full_name || row.agent.email}
                            </p>
                            <p className="text-xs text-zinc-400">{row.agent.role}</p>
                          </div>
                        </Link>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className={cn(
                          "text-sm font-medium",
                          row.agent.conv_count > maxConvs * 0.7 ? "text-red-500" :
                          row.agent.conv_count > maxConvs * 0.4 ? "text-yellow-500" : "text-green-600"
                        )}>
                          {row.agent.conv_count}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right text-sm text-zinc-700 dark:text-zinc-300">
                        {fetchingMetrics ? <Loader2 className="w-3.5 h-3.5 animate-spin inline text-zinc-400" /> : (row.metrics?.conversations_handled ?? "—")}
                      </td>
                      <td className="py-3 px-4 text-right text-sm text-zinc-700 dark:text-zinc-300">
                        {fetchingMetrics ? <Loader2 className="w-3.5 h-3.5 animate-spin inline text-zinc-400" /> : fmtSec(row.metrics?.avg_first_response_sec ?? null)}
                      </td>
                      <td className="py-3 px-4 text-right text-sm text-zinc-700 dark:text-zinc-300">
                        {fetchingMetrics ? <Loader2 className="w-3.5 h-3.5 animate-spin inline text-zinc-400" /> : fmtSec(row.metrics?.avg_resolution_sec ?? null)}
                      </td>
                      <td className="py-3 px-4 text-right text-sm text-zinc-700 dark:text-zinc-300">
                        {fetchingMetrics ? <Loader2 className="w-3.5 h-3.5 animate-spin inline text-zinc-400" /> : (row.metrics?.messages_sent ?? "—")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : (
        /* Vista de carga de trabajo simple */
        <div className="space-y-2">
          {rows.map((row, idx) => (
            <Card key={row.agent.id}>
              <CardContent className="py-3 px-4">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-bold text-zinc-400 w-5 text-right">{idx + 1}</span>
                  <div className="p-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800">
                    <UserCircle className="w-4 h-4 text-zinc-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">
                      {row.agent.full_name || row.agent.email}
                    </p>
                    <LoadBar value={row.agent.conv_count} max={maxConvs} />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
