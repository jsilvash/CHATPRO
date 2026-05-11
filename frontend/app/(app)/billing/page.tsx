"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api"
import type { MetricsSummaryOut, QuotaOut, UsageMetricOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  CreditCard,
  MessageSquare,
  Brain,
  HardDrive,
  Key,
  AlertCircle,
  Loader2,
  BarChart3,
} from "lucide-react"
import { cn } from "@/lib/utils"

function todayMinus(days: number) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

// Barra de progreso de cuota
function QuotaBar({
  label,
  used,
  limit,
  format,
}: {
  label: string
  used: number
  limit: number | null
  format?: (v: number) => string
}) {
  const fmt = format ?? ((v: number) => v.toLocaleString("es"))
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : null
  const color =
    pct === null ? "bg-zinc-200 dark:bg-zinc-700" :
    pct >= 90 ? "bg-red-500" :
    pct >= 70 ? "bg-yellow-500" :
    "bg-green-500"

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-zinc-600 dark:text-zinc-400">{label}</span>
        <span className="font-medium text-zinc-800 dark:text-zinc-200">
          {fmt(used)}{limit ? ` / ${fmt(limit)}` : " (sin límite)"}
        </span>
      </div>
      <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", color)}
          style={{ width: limit ? `${pct}%` : "0%" }}
        />
      </div>
      {pct !== null && (
        <p className="text-xs text-right text-zinc-400">{pct}% utilizado</p>
      )}
    </div>
  )
}

// Gráfico de barras simple en CSS/SVG
function MiniBarChart({
  data,
  field,
  label,
  color,
}: {
  data: UsageMetricOut[]
  field: keyof UsageMetricOut
  label: string
  color: string
}) {
  const values = data.map((d) => Number(d[field]))
  const max = Math.max(...values, 1)

  return (
    <div>
      <p className="text-xs font-medium text-zinc-500 mb-2">{label}</p>
      <div className="flex items-end gap-0.5 h-14">
        {values.map((v, i) => {
          const h = Math.max(2, Math.round((v / max) * 56))
          return (
            <div
              key={i}
              title={`${data[i].metric_date}: ${v.toLocaleString("es")}`}
              className={cn("flex-1 rounded-sm", color, "cursor-default")}
              style={{ height: `${h}px` }}
            />
          )
        })}
      </div>
      <div className="flex justify-between mt-1">
        {data.length > 0 && (
          <>
            <span className="text-[10px] text-zinc-400">{data[0].metric_date.slice(5)}</span>
            <span className="text-[10px] text-zinc-400">{data[data.length - 1].metric_date.slice(5)}</span>
          </>
        )}
      </div>
    </div>
  )
}

export default function BillingPage() {
  const [period] = useState(30)

  const { data: summary, isLoading: loadSummary } = useQuery<MetricsSummaryOut>({
    queryKey: ["billing-summary", period],
    queryFn: () =>
      apiGet<MetricsSummaryOut>("/v1/metrics/summary", {
        days: period,
      }),
  })

  const { data: quota } = useQuery<QuotaOut>({
    queryKey: ["billing-quota"],
    queryFn: () => apiGet<QuotaOut>("/v1/quotas"),
  })

  const { data: history } = useQuery<UsageMetricOut[]>({
    queryKey: ["billing-history", period],
    queryFn: () =>
      apiGet<UsageMetricOut[]>("/v1/metrics", {
        from: todayMinus(period),
        to: today(),
      }),
  })

  return (
    <div className="p-6 max-w-4xl space-y-6">
      <div className="flex items-center gap-2">
        <CreditCard className="w-5 h-5 text-zinc-500" />
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
          Billing y uso
        </h1>
        <Badge variant="secondary" className="text-xs">Últimos {period} días</Badge>
      </div>

      {loadSummary && (
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          Cargando datos de uso...
        </div>
      )}

      {/* Resumen de uso */}
      {summary && (
        <div>
          <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Resumen del período
          </h2>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Card>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-zinc-500">Mensajes recibidos</p>
                  <MessageSquare className="w-3.5 h-3.5 text-blue-400" />
                </div>
                <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {summary.messages_in.toLocaleString("es")}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-zinc-500">Mensajes enviados</p>
                  <MessageSquare className="w-3.5 h-3.5 text-indigo-400" />
                </div>
                <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {summary.messages_out.toLocaleString("es")}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-zinc-500">Costo LLM</p>
                  <Brain className="w-3.5 h-3.5 text-purple-400" />
                </div>
                <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {formatCents(summary.llm_cost_cents)}
                </p>
                <p className="text-xs text-zinc-400 mt-0.5">
                  {(summary.llm_input_tokens + summary.llm_output_tokens).toLocaleString("es")} tokens
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-zinc-500">Almacenamiento</p>
                  <HardDrive className="w-3.5 h-3.5 text-teal-400" />
                </div>
                <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {formatBytes(summary.storage_bytes)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-zinc-500">Peticiones API</p>
                  <Key className="w-3.5 h-3.5 text-amber-400" />
                </div>
                <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {summary.api_requests.toLocaleString("es")}
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {/* Cuotas */}
      {quota && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-zinc-400" />
              Cuotas del plan
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <QuotaBar
              label="Mensajes por mes"
              used={summary?.messages_in ?? 0 + (summary?.messages_out ?? 0)}
              limit={quota.max_messages_per_month}
            />
            <QuotaBar
              label="Conversaciones activas"
              used={summary?.conversations_active ?? 0}
              limit={quota.max_conversations_active}
            />
            <QuotaBar
              label="Costo LLM por mes"
              used={summary?.llm_cost_cents ?? 0}
              limit={quota.max_llm_cost_cents_per_month}
              format={formatCents}
            />
            <QuotaBar
              label="Almacenamiento"
              used={summary?.storage_bytes ?? 0}
              limit={quota.max_storage_bytes}
              format={formatBytes}
            />
            <QuotaBar
              label="Peticiones API por día"
              used={summary?.api_requests ?? 0}
              limit={quota.max_api_requests_per_day}
            />
            <p className="text-xs text-zinc-400">
              Última actualización: {new Date(quota.updated_at).toLocaleDateString("es")}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Historial gráfico */}
      {history && history.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-zinc-400" />
              Historial diario
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <MiniBarChart
              data={history}
              field="messages_in"
              label="Mensajes recibidos"
              color="bg-blue-400"
            />
            <MiniBarChart
              data={history}
              field="messages_out"
              label="Mensajes enviados"
              color="bg-indigo-400"
            />
            <MiniBarChart
              data={history}
              field="llm_cost_cents"
              label="Costo LLM (centavos)"
              color="bg-purple-400"
            />
            <MiniBarChart
              data={history}
              field="api_requests"
              label="Peticiones API"
              color="bg-amber-400"
            />
          </CardContent>
        </Card>
      )}

      {history && history.length === 0 && !loadSummary && (
        <div className="text-center py-12 text-zinc-400 text-sm">
          <BarChart3 className="w-8 h-8 mx-auto mb-3 opacity-30" />
          <p>No hay datos de uso en los últimos {period} días.</p>
        </div>
      )}
    </div>
  )
}
