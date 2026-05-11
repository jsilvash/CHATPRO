"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { apiGet, apiFetch, API_URL } from "@/lib/api"
import type { TenantMetricsDashboard, StreamMetrics, ExportJobOut, ExportStatusOut } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  MessageSquare,
  Bot,
  Users,
  Clock,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  RefreshCw,
  Radio,
  Download,
  Loader2,
  CheckCircle2,
  ExternalLink,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useToast } from "@/hooks/use-toast"

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

// ── SSE hook ──────────────────────────────────────────────────────────────────

function useMetricsSSE(onUpdate: (m: StreamMetrics) => void) {
  const [sseActive, setSseActive] = useState(false)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    let es: EventSource

    async function connect() {
      // Obtener token para SSE (reutiliza el endpoint de ws-token)
      try {
        const res = await fetch("/api/auth/ws-token")
        if (!res.ok) throw new Error("no token")
        const { token } = await res.json()
        const url = `${API_URL}/v1/metrics/stream?token=${encodeURIComponent(token)}`
        es = new EventSource(url)
        esRef.current = es

        es.onopen = () => setSseActive(true)

        es.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data) as StreamMetrics
            onUpdate(data)
          } catch {
            // ignorar payloads no-JSON
          }
        }

        es.onerror = () => {
          es.close()
          setSseActive(false)
          // reintentar en 10s
          setTimeout(connect, 10_000)
        }
      } catch {
        setSseActive(false)
      }
    }

    connect()

    return () => {
      esRef.current?.close()
    }
  }, [onUpdate])

  return { sseActive }
}

// ── GDPR Export ────────────────────────────────────────────────────────────────

function ExportSection() {
  const [job, setJob] = useState<ExportJobOut | null>(null)
  const [polling, setPolling] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null)
  const { success, error: toastError } = useToast()
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function startExport() {
    try {
      const j = await apiFetch<ExportJobOut>("/v1/tenants/me/export", { method: "POST" })
      setJob(j)
      setDownloadUrl(null)
      setPolling(true)
    } catch (e) {
      toastError("Error al iniciar exportación", e instanceof Error ? e.message : undefined)
    }
  }

  useEffect(() => {
    if (!polling || !job) return
    pollRef.current = setInterval(async () => {
      try {
        const status = await apiGet<ExportStatusOut>(`/v1/tenants/me/export/${job.id}/status`)
        setJob((prev) => prev ? { ...prev, status: status.status, error: status.error } : prev)
        if (status.status === "done") {
          setPolling(false)
          clearInterval(pollRef.current!)
          const dl = await apiGet<{ url: string }>(`/v1/tenants/me/export/${job.id}/download`)
          setDownloadUrl(dl.url)
          success("Exportación lista", "Tu archivo ZIP está listo para descargar.")
        } else if (status.status === "error") {
          setPolling(false)
          clearInterval(pollRef.current!)
          toastError("Error en exportación", status.error ?? undefined)
        }
      } catch {
        // ignorar error transitorio
      }
    }, 3000)
    return () => clearInterval(pollRef.current!)
  }, [polling, job, success, toastError])

  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">Exportar datos (GDPR)</p>
            <p className="text-xs text-zinc-500 mt-0.5">
              Descarga un ZIP con todos tus contactos, mensajes y pedidos.
            </p>
            {job && job.status === "error" && (
              <p className="text-xs text-red-500 mt-1">{job.error ?? "Error desconocido"}</p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {polling && (
              <span className="flex items-center gap-1 text-xs text-zinc-500">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {job?.status === "running" ? "Generando..." : "En cola..."}
              </span>
            )}
            {downloadUrl ? (
              <Button size="sm" asChild className="h-7 text-xs gap-1">
                <a href={downloadUrl} target="_blank" rel="noopener noreferrer">
                  <Download className="w-3.5 h-3.5" />
                  Descargar
                  <ExternalLink className="w-3 h-3" />
                </a>
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={startExport}
                disabled={polling}
                className="h-7 text-xs gap-1"
              >
                {job?.status === "done" && !downloadUrl ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                ) : (
                  <Download className="w-3.5 h-3.5" />
                )}
                Exportar datos
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// ── Página ─────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<TenantMetricsDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [sseOverlay, setSseOverlay] = useState<Partial<TenantMetricsDashboard>>({})

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

  // Polling de respaldo (solo cuando SSE no está activo)
  const { sseActive } = useMetricsSSE(
    useCallback((stream: StreamMetrics) => {
      // Actualizar campos que vienen del stream
      setSseOverlay(prev => ({
        ...prev,
        messages_in_today: stream.messages_in_today,
        messages_out_today: stream.messages_out_today,
        conversations_active: stream.conversations_active,
      }))
      setLastUpdated(new Date())
    }, [])
  )

  useEffect(() => {
    fetchMetrics()
  }, [fetchMetrics])

  // Polling fallback cada 30s si SSE no está conectado
  useEffect(() => {
    if (sseActive) return
    const interval = setInterval(fetchMetrics, 30_000)
    return () => clearInterval(interval)
  }, [sseActive, fetchMetrics])

  // Combinar métricas base con overlay SSE
  const combined: TenantMetricsDashboard | null = metrics
    ? { ...metrics, ...sseOverlay }
    : null

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Dashboard</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Métricas operativas en tiempo real</p>
        </div>
        <div className="flex items-center gap-3">
          {sseActive && (
            <span className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400 font-medium">
              <Radio className="w-3.5 h-3.5 animate-pulse" />
              En vivo
            </span>
          )}
          <button
            onClick={fetchMetrics}
            className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
          >
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
            {lastUpdated
              ? `Actualizado ${lastUpdated.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}`
              : "Actualizando..."}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading && !combined ? (
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
      ) : combined ? (
        <div className="space-y-6">
          {/* Conversaciones */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Conversaciones
            </h2>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <MetricCard
                title="Total"
                value={combined.conversations_total}
                icon={MessageSquare}
                color="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                subtitle="Todas las conversaciones"
              />
              <MetricCard
                title="Activas"
                value={combined.conversations_active}
                icon={TrendingUp}
                color="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                subtitle="En curso ahora"
              />
              <MetricCard
                title="Con bot"
                value={combined.conversations_bot}
                icon={Bot}
                color="bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400"
                subtitle="Atendidas por IA"
              />
              <MetricCard
                title="Cerradas hoy"
                value={combined.conversations_closed_today}
                icon={TrendingDown}
                color="bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                subtitle="Resueltas en el día"
              />
            </div>
          </div>

          {/* Mensajes */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Mensajes hoy
            </h2>
            <div className="grid grid-cols-2 gap-4 max-w-xl">
              <MetricCard
                title="Recibidos"
                value={combined.messages_in_today}
                icon={TrendingDown}
                color="bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400"
                subtitle="Mensajes entrantes"
              />
              <MetricCard
                title="Enviados"
                value={combined.messages_out_today}
                icon={TrendingUp}
                color="bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400"
                subtitle="Mensajes salientes"
              />
            </div>
          </div>

          {/* Agentes */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
              Agentes
            </h2>
            <div className="grid grid-cols-2 gap-4 max-w-xl">
              <MetricCard
                title="En línea"
                value={combined.agents_online}
                icon={Users}
                color="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                subtitle="Agentes activos"
              />
              <MetricCard
                title="Sin asignar"
                value={combined.unassigned_waiting}
                icon={Clock}
                color={
                  combined.unassigned_waiting > 0
                    ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                    : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                }
                subtitle="Esperando agente"
              />
            </div>
          </div>
        </div>
      ) : null}

      <p className="text-xs text-zinc-400">
        {sseActive
          ? "Métricas de mensajes y conversaciones activas actualizadas en tiempo real via SSE."
          : "Se actualiza automáticamente cada 30 segundos."}
      </p>

      <ExportSection />
    </div>
  )
}
