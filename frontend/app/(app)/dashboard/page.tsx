"use client"

import { useEffect, useState, useCallback } from "react"
import { apiGet } from "@/lib/api"
import type { TenantMetricsDashboard } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  MessageSquare,
  Bot,
  Users,
  Clock,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  RefreshCw,
} from "lucide-react"
import { cn } from "@/lib/utils"

function MetricCard({
  title,
  value,
  icon: Icon,
  color,
  subtitle,
}: {
  title: string
  value: number | string
  icon: React.ElementType
  color: string
  subtitle?: string
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">{title}</p>
            <p className="mt-1 text-3xl font-bold text-zinc-900 dark:text-zinc-50">{value}</p>
            {subtitle && <p className="mt-1 text-xs text-zinc-400">{subtitle}</p>}
          </div>
          <div className={cn("p-2 rounded-lg", color)}>
            <Icon className="w-5 h-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<TenantMetricsDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchMetrics = useCallback(async () => {
    try {
      const data = await apiGet<TenantMetricsDashboard>("/v1/metrics/dashboard")
      setMetrics(data)
      setLastUpdated(new Date())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar métricas")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchMetrics()
    const interval = setInterval(fetchMetrics, 30_000)
    return () => clearInterval(interval)
  }, [fetchMetrics])

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Dashboard</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Métricas operativas en tiempo real</p>
        </div>
        <button
          onClick={fetchMetrics}
          className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        >
          <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
          {lastUpdated ? `Actualizado ${lastUpdated.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}` : "Actualizando..."}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading && !metrics ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-6">
                <div className="animate-pulse space-y-2">
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-3/4" />
                  <div className="h-8 bg-zinc-200 dark:bg-zinc-700 rounded w-1/2" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : metrics ? (
        <div className="space-y-6">
          {/* Sección: Conversaciones */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Conversaciones
            </h2>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <MetricCard
                title="Total"
                value={metrics.conversations_total}
                icon={MessageSquare}
                color="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                subtitle="Todas las conversaciones"
              />
              <MetricCard
                title="Activas"
                value={metrics.conversations_active}
                icon={TrendingUp}
                color="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                subtitle="En curso ahora"
              />
              <MetricCard
                title="Con bot"
                value={metrics.conversations_bot}
                icon={Bot}
                color="bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400"
                subtitle="Atendidas por IA"
              />
              <MetricCard
                title="Cerradas hoy"
                value={metrics.conversations_closed_today}
                icon={TrendingDown}
                color="bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                subtitle="Resueltas en el día"
              />
            </div>
          </div>

          {/* Sección: Mensajes */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Mensajes hoy
            </h2>
            <div className="grid grid-cols-2 lg:grid-cols-2 gap-4 max-w-xl">
              <MetricCard
                title="Recibidos"
                value={metrics.messages_in_today}
                icon={TrendingDown}
                color="bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400"
                subtitle="Mensajes entrantes"
              />
              <MetricCard
                title="Enviados"
                value={metrics.messages_out_today}
                icon={TrendingUp}
                color="bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400"
                subtitle="Mensajes salientes"
              />
            </div>
          </div>

          {/* Sección: Agentes */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Agentes
            </h2>
            <div className="grid grid-cols-2 lg:grid-cols-2 gap-4 max-w-xl">
              <MetricCard
                title="En línea"
                value={metrics.agents_online}
                icon={Users}
                color="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                subtitle="Agentes activos"
              />
              <MetricCard
                title="Sin asignar"
                value={metrics.unassigned_waiting}
                icon={Clock}
                color={
                  metrics.unassigned_waiting > 0
                    ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                    : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                }
                subtitle="Esperando agente"
              />
            </div>
          </div>
        </div>
      ) : null}

      <p className="text-xs text-zinc-400">Se actualiza automáticamente cada 30 segundos.</p>
    </div>
  )
}
