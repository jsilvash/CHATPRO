"use client"

import { useEffect, useState, useCallback, use } from "react"
import { useRouter } from "next/navigation"
import { apiGet } from "@/lib/api"
import type { WaNumberResponse, WaNumberMetricsOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import {
  ArrowLeft,
  Smartphone,
  MessageSquare,
  Users,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  TrendingUp,
} from "lucide-react"

function sessionStatusBadge(status: string) {
  const map: Record<string, { label: string; color: string; icon: React.ElementType }> = {
    WORKING: { label: "Conectado", color: "text-green-700 bg-green-100 dark:text-green-400 dark:bg-green-900/30", icon: CheckCircle2 },
    STARTING: { label: "Iniciando", color: "text-yellow-700 bg-yellow-100 dark:text-yellow-400 dark:bg-yellow-900/30", icon: Clock },
    SCAN_QR_CODE: { label: "Esperando QR", color: "text-blue-700 bg-blue-100 dark:text-blue-400 dark:bg-blue-900/30", icon: Clock },
    FAILED: { label: "Error", color: "text-red-700 bg-red-100 dark:text-red-400 dark:bg-red-900/30", icon: XCircle },
    STOPPED: { label: "Detenido", color: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800", icon: XCircle },
  }
  const s = map[status] ?? { label: status || "Sin sesión", color: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800", icon: AlertCircle }
  const Icon = s.icon
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium", s.color)}>
      <Icon className="w-3 h-3" />
      {s.label}
    </span>
  )
}

function StatCard({ icon: Icon, label, value, sub }: { icon: React.ElementType; label: string; value: number | string; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-zinc-100 dark:bg-zinc-800">
            <Icon className="w-4 h-4 text-zinc-600 dark:text-zinc-300" />
          </div>
          <div>
            <p className="text-xs text-zinc-500">{label}</p>
            <p className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50 mt-0.5">{value}</p>
            {sub && <p className="text-xs text-zinc-400 mt-0.5">{sub}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function todayMinus(days: number) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export default function WaNumberDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()

  const [waNumber, setWaNumber] = useState<WaNumberResponse | null>(null)
  const [metrics, setMetrics] = useState<WaNumberMetricsOut | null>(null)
  const [dateFrom, setDateFrom] = useState(todayMinus(30))
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10))
  const [loading, setLoading] = useState(true)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadNumber = useCallback(async () => {
    try {
      const wn = await apiGet<WaNumberResponse>(`/v1/wa-numbers/${id}`)
      setWaNumber(wn)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar número")
    } finally {
      setLoading(false)
    }
  }, [id])

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true)
    try {
      const m = await apiGet<WaNumberMetricsOut>(`/v1/wa-numbers/${id}/metrics`, {
        date_from: dateFrom,
        date_to: dateTo,
      })
      setMetrics(m)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar métricas")
    } finally {
      setMetricsLoading(false)
    }
  }, [id, dateFrom, dateTo])

  useEffect(() => { loadNumber() }, [loadNumber])
  useEffect(() => { if (!loading) loadMetrics() }, [loading, loadMetrics])

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.push("/wa-numbers")} className="gap-1.5">
          <ArrowLeft className="w-3.5 h-3.5" />
          Volver
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="animate-pulse space-y-4">
          <div className="h-6 bg-zinc-200 dark:bg-zinc-700 rounded w-1/3" />
          <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-1/4" />
        </div>
      ) : waNumber && (
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <Smartphone className="w-5 h-5 text-zinc-400" />
            <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">{waNumber.label}</h1>
            {sessionStatusBadge(waNumber.session_status)}
          </div>
          <p className="text-sm text-zinc-500 font-mono ml-8">
            {waNumber.phone ?? waNumber.waha_session_name}
          </p>
        </div>
      )}

      {/* Filtro de fechas */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Período de análisis</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-end gap-4">
            <div>
              <Label htmlFor="date_from" className="text-xs">Desde</Label>
              <Input
                id="date_from"
                type="date"
                value={dateFrom}
                onChange={e => setDateFrom(e.target.value)}
                className="mt-1 w-40"
              />
            </div>
            <div>
              <Label htmlFor="date_to" className="text-xs">Hasta</Label>
              <Input
                id="date_to"
                type="date"
                value={dateTo}
                onChange={e => setDateTo(e.target.value)}
                className="mt-1 w-40"
              />
            </div>
            <Button onClick={loadMetrics} disabled={metricsLoading} size="sm">
              {metricsLoading ? "Cargando…" : "Aplicar"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Métricas */}
      {metricsLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-5">
                <div className="animate-pulse space-y-2">
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-2/3" />
                  <div className="h-7 bg-zinc-200 dark:bg-zinc-700 rounded w-1/2" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : metrics && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard icon={MessageSquare} label="Mensajes entrantes" value={metrics.messages_in} />
            <StatCard icon={TrendingUp} label="Mensajes salientes" value={metrics.messages_out} />
            <StatCard icon={Users} label="Conversaciones" value={metrics.conversations_total} />
            <StatCard
              icon={Users}
              label="Conversaciones activas"
              value={metrics.conversations_active}
              sub={`${metrics.conversations_total > 0 ? Math.round(metrics.conversations_active / metrics.conversations_total * 100) : 0}% del total`}
            />
          </div>

          {/* Top contactos */}
          {metrics.top_contacts.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Top 5 contactos (por mensajes recibidos)</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {metrics.top_contacts.map((tc, idx) => (
                    <div key={tc.phone} className="flex items-center gap-3">
                      <span className="text-xs font-mono text-zinc-400 w-4">{idx + 1}</span>
                      <span className="flex-1 text-sm font-mono text-zinc-700 dark:text-zinc-300">{tc.phone}</span>
                      <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{tc.count}</span>
                      <span className="text-xs text-zinc-400">msgs</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
