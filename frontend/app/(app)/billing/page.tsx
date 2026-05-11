"use client"

import React, { useEffect, useState, useCallback } from "react"
import { apiGet } from "@/lib/api"
import type { TenantMetricsDashboard } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CreditCard, MessageSquare, Users, HardDrive, Zap, AlertCircle, RefreshCw, TrendingUp } from "lucide-react"
import { cn } from "@/lib/utils"

interface MetricsSummary {
  period_start: string
  period_end: string
  messages_in: number
  messages_out: number
  conversations_active: number
  llm_input_tokens: number
  llm_output_tokens: number
  llm_cost_cents: number
  storage_bytes: number
  api_requests: number
}

interface QuotaOut {
  tenant_id: string
  max_messages_per_month: number | null
  max_conversations_active: number | null
  max_llm_cost_cents_per_month: number | null
  max_storage_bytes: number | null
  max_api_requests_per_day: number | null
  updated_at: string
}

function UsageBar({
  label,
  used,
  max,
  unit,
  icon: Icon,
  color,
}: {
  label: string
  used: number
  max: number | null
  unit: string
  icon: React.ElementType
  color: string
}) {
  const pct = max && max > 0 ? Math.min((used / max) * 100, 100) : null
  const isWarning = pct !== null && pct >= 80
  const isCritical = pct !== null && pct >= 95

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={cn("w-7 h-7 rounded-md flex items-center justify-center", color)}>
            <Icon className="w-3.5 h-3.5" />
          </div>
          <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">{label}</span>
        </div>
        <div className="text-right">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
            {used.toLocaleString("es")} {unit}
          </span>
          {max !== null && (
            <span className="text-xs text-zinc-400 ml-1">
              / {max.toLocaleString("es")}
            </span>
          )}
        </div>
      </div>
      {pct !== null ? (
        <div className="h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              isCritical ? "bg-red-500" : isWarning ? "bg-yellow-500" : "bg-green-500",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : (
        <div className="h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
          <div className="h-full w-full bg-zinc-200 dark:bg-zinc-700 rounded-full" title="Sin límite configurado" />
        </div>
      )}
      {pct !== null && (
        <p className={cn("text-xs", isCritical ? "text-red-500" : isWarning ? "text-yellow-600 dark:text-yellow-400" : "text-zinc-400")}>
          {isCritical ? "Cuota casi agotada" : isWarning ? `${pct.toFixed(0)}% utilizado` : `${pct.toFixed(0)}% utilizado`}
        </p>
      )}
      {pct === null && (
        <p className="text-xs text-zinc-400">Sin límite configurado</p>
      )}
    </div>
  )
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-4 pb-4">
        <p className="text-xs text-zinc-500 dark:text-zinc-400">{label}</p>
        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50 mt-0.5">{value}</p>
        {sub && <p className="text-xs text-zinc-400 mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  )
}

export default function BillingPage() {
  const [summary, setSummary] = useState<MetricsSummary | null>(null)
  const [quota, setQuota] = useState<QuotaOut | null>(null)
  const [dashboard, setDashboard] = useState<TenantMetricsDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [sum, q, dash] = await Promise.allSettled([
        apiGet<MetricsSummary>("/v1/metrics/summary"),
        apiGet<QuotaOut>("/v1/quotas"),
        apiGet<TenantMetricsDashboard>("/v1/metrics/dashboard"),
      ])
      if (sum.status === "fulfilled") setSummary(sum.value)
      if (q.status === "fulfilled") setQuota(q.value)
      if (dash.status === "fulfilled") setDashboard(dash.value)
      if (sum.status === "rejected" && q.status === "rejected") {
        setError("No tienes permiso para ver esta sección (requiere rol owner o admin)")
      }
      setLastUpdated(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const totalMessages = summary ? summary.messages_in + summary.messages_out : 0
  const costEuros = summary ? (summary.llm_cost_cents / 100).toFixed(2) : "0.00"
  const storageKb = summary ? Math.round(summary.storage_bytes / 1024) : 0

  return (
    <div className="p-6 space-y-8 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <CreditCard className="w-5 h-5 text-zinc-500" />
            <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Billing y uso</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-0.5">
            Métricas del mes en curso y estado de cuotas
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        >
          <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
          {lastUpdated
            ? `Actualizado ${lastUpdated.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}`
            : "Actualizar"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-yellow-50 text-yellow-700 text-sm dark:bg-yellow-900/20 dark:text-yellow-400 border border-yellow-200 dark:border-yellow-800">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Métricas del mes */}
      {summary && (
        <div>
          <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Métricas del mes
            <span className="normal-case font-normal ml-2 text-zinc-400">
              ({new Date(summary.period_start).toLocaleDateString("es", { month: "long", year: "numeric" })})
            </span>
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            <StatCard label="Mensajes totales" value={totalMessages.toLocaleString("es")} sub="Entrantes + salientes" />
            <StatCard label="Mensajes recibidos" value={summary.messages_in.toLocaleString("es")} />
            <StatCard label="Mensajes enviados" value={summary.messages_out.toLocaleString("es")} />
            <StatCard label="Coste LLM" value={`€${costEuros}`} sub={`${(summary.llm_input_tokens + summary.llm_output_tokens).toLocaleString("es")} tokens`} />
            <StatCard label="Peticiones API" value={summary.api_requests.toLocaleString("es")} />
            <StatCard label="Almacenamiento" value={`${storageKb.toLocaleString("es")} KB`} />
          </div>
        </div>
      )}

      {/* Estado en tiempo real */}
      {dashboard && (
        <div>
          <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Estado actual
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard label="Convs. activas" value={dashboard.conversations_active} />
            <StatCard label="Con bot" value={dashboard.conversations_bot} />
            <StatCard label="Agentes online" value={dashboard.agents_online} />
            <StatCard label="Sin asignar" value={dashboard.unassigned_waiting} />
          </div>
        </div>
      )}

      {/* Cuotas */}
      <div>
        <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-4">
          Cuotas
        </h2>
        {loading && !quota ? (
          <Card>
            <CardContent className="pt-6 space-y-6">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="animate-pulse space-y-2">
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-1/3" />
                  <div className="h-2 bg-zinc-200 dark:bg-zinc-700 rounded" />
                </div>
              ))}
            </CardContent>
          </Card>
        ) : quota ? (
          <Card>
            <CardContent className="pt-6 space-y-6">
              <UsageBar
                label="Mensajes / mes"
                used={totalMessages}
                max={quota.max_messages_per_month}
                unit="msgs"
                icon={MessageSquare}
                color="bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"
              />
              <UsageBar
                label="Conversaciones activas"
                used={dashboard?.conversations_active ?? 0}
                max={quota.max_conversations_active}
                unit="convs"
                icon={Users}
                color="bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400"
              />
              <UsageBar
                label="Coste LLM / mes"
                used={summary ? Math.round(summary.llm_cost_cents) : 0}
                max={quota.max_llm_cost_cents_per_month}
                unit="¢"
                icon={Zap}
                color="bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400"
              />
              <UsageBar
                label="Almacenamiento"
                used={summary?.storage_bytes ?? 0}
                max={quota.max_storage_bytes}
                unit="bytes"
                icon={HardDrive}
                color="bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400"
              />
              <UsageBar
                label="Peticiones API / día"
                used={dashboard ? Math.round((summary?.api_requests ?? 0) / 30) : 0}
                max={quota.max_api_requests_per_day}
                unit="reqs"
                icon={TrendingUp}
                color="bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400"
              />
            </CardContent>
          </Card>
        ) : (
          <p className="text-sm text-zinc-400">No se pudieron cargar las cuotas.</p>
        )}
      </div>

      <p className="text-xs text-zinc-400">
        Las cuotas son configuradas por el administrador del sistema. Contacta con soporte si necesitas aumentar límites.
      </p>
    </div>
  )
}
