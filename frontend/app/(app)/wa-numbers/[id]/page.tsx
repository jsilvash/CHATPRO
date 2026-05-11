"use client"

import { useEffect, useState, useCallback, useRef, use } from "react"
import { useRouter } from "next/navigation"
import { apiGet, apiFetch } from "@/lib/api"
import type { WaNumberResponse, WaNumberMetricsOut, PersonaListResponse } from "@/lib/types"
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
  Bot,
  Save,
  RefreshCw,
  QrCode,
  X,
  Loader2,
} from "lucide-react"

interface QrResponse {
  status: string
  qr_base64: string
}

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

// ── QR Modal ───────────────────────────────────────────────────────────────────

function QrModal({ numberId, onClose }: { numberId: string; onClose: () => void }) {
  const [qr, setQr] = useState<QrResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchQr = useCallback(async () => {
    try {
      const res = await apiGet<QrResponse>(`/v1/wa-numbers/${numberId}/qr`)
      setQr(res)
      if (res.status === "WORKING") {
        // Sesión ya conectada → cerrar modal
        if (intervalRef.current) clearInterval(intervalRef.current)
        setTimeout(onClose, 1500)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al obtener QR")
    } finally {
      setLoading(false)
    }
  }, [numberId, onClose])

  useEffect(() => {
    fetchQr()
    // Refrescar QR cada 20s (los QR de WAHA expiran)
    intervalRef.current = setInterval(fetchQr, 20_000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [fetchQr])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl p-6 max-w-sm w-full mx-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <QrCode className="w-5 h-5 text-zinc-500" />
            <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-50">
              Escanear código QR
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            aria-label="Cerrar"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-sm text-zinc-500">
          Abre WhatsApp en tu teléfono → Dispositivos vinculados → Vincular un dispositivo y escanea este código.
        </p>

        {loading ? (
          <div className="flex items-center justify-center h-48">
            <Loader2 className="w-8 h-8 animate-spin text-zinc-400" />
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        ) : qr?.status === "WORKING" ? (
          <div className="flex flex-col items-center gap-2 py-6">
            <CheckCircle2 className="w-12 h-12 text-green-500" />
            <p className="text-sm font-medium text-green-700 dark:text-green-400">¡Conectado correctamente!</p>
          </div>
        ) : qr?.qr_base64 ? (
          <div className="flex flex-col items-center gap-3">
            <img
              src={`data:image/png;base64,${qr.qr_base64}`}
              alt="QR code para vincular WhatsApp"
              className="w-48 h-48 rounded-lg border border-zinc-200 dark:border-zinc-700"
            />
            <p className="text-xs text-zinc-400">El código se actualiza automáticamente cada 20 segundos</p>
          </div>
        ) : (
          <div className="flex items-center justify-center h-32 text-sm text-zinc-400">
            No hay QR disponible. El estado actual es: {qr?.status}
          </div>
        )}

        <Button variant="outline" size="sm" className="w-full" onClick={onClose}>
          Cerrar
        </Button>
      </div>
    </div>
  )
}

// ── Página principal ───────────────────────────────────────────────────────────

const NON_WORKING_STATUSES = new Set(["STARTING", "SCAN_QR_CODE", "FAILED", "STOPPED", ""])

export default function WaNumberDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()

  const [waNumber, setWaNumber] = useState<WaNumberResponse | null>(null)
  const [metrics, setMetrics] = useState<WaNumberMetricsOut | null>(null)
  const [personas, setPersonas] = useState<PersonaListResponse["items"]>([])
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>("")
  const [assignedPersonaId, setAssignedPersonaId] = useState<string | null>(null)
  const [personaSaving, setPersonaSaving] = useState(false)
  const [personaSuccess, setPersonaSuccess] = useState(false)
  const [dateFrom, setDateFrom] = useState(todayMinus(30))
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10))
  const [loading, setLoading] = useState(true)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showQrModal, setShowQrModal] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

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

  const loadPersonas = useCallback(async () => {
    try {
      const res = await apiGet<PersonaListResponse>("/v1/personas")
      setPersonas(res.items)
      const stored = localStorage.getItem(`wa-number-persona-${id}`)
      if (stored) {
        setAssignedPersonaId(stored)
        setSelectedPersonaId(stored)
      }
    } catch {
      // silencioso
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
  useEffect(() => { loadPersonas() }, [loadPersonas])
  useEffect(() => { if (!loading) loadMetrics() }, [loading, loadMetrics])

  // Polling cada 5s cuando el estado no es WORKING
  useEffect(() => {
    if (!waNumber) return
    const shouldPoll = NON_WORKING_STATUSES.has(waNumber.session_status)
    if (shouldPoll) {
      pollingRef.current = setInterval(async () => {
        try {
          const wn = await apiGet<WaNumberResponse>(`/v1/wa-numbers/${id}`)
          setWaNumber(wn)
          // Si pasó a WORKING, detener polling
          if (!NON_WORKING_STATUSES.has(wn.session_status)) {
            if (pollingRef.current) clearInterval(pollingRef.current)
          }
        } catch {
          // silencioso
        }
      }, 5_000)
    } else {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
    return () => { if (pollingRef.current) clearInterval(pollingRef.current) }
  }, [id, waNumber?.session_status]) // eslint-disable-line react-hooks/exhaustive-deps

  async function handleAssignPersona() {
    setPersonaSaving(true)
    setPersonaSuccess(false)
    try {
      await apiFetch(`/v1/wa-numbers/${id}/persona`, {
        method: "PATCH",
        body: JSON.stringify({ persona_id: selectedPersonaId || null }),
      })
      setAssignedPersonaId(selectedPersonaId || null)
      if (selectedPersonaId) {
        localStorage.setItem(`wa-number-persona-${id}`, selectedPersonaId)
      } else {
        localStorage.removeItem(`wa-number-persona-${id}`)
      }
      setPersonaSuccess(true)
      setTimeout(() => setPersonaSuccess(false), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al asignar persona")
    } finally {
      setPersonaSaving(false)
    }
  }

  async function handleReconnect() {
    setReconnecting(true)
    setError(null)
    try {
      // Intentar iniciar sesión WAHA y mostrar el QR
      setShowQrModal(true)
    } finally {
      setReconnecting(false)
    }
  }

  const assignedPersona = personas.find(p => p.id === assignedPersonaId)
  const isNotWorking = waNumber ? NON_WORKING_STATUSES.has(waNumber.session_status) : false

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
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <Smartphone className="w-5 h-5 text-zinc-400 shrink-0" />
            <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">{waNumber.label}</h1>
            {sessionStatusBadge(waNumber.session_status)}
            {/* Indicador de polling activo */}
            {isNotWorking && (
              <span className="flex items-center gap-1 text-xs text-zinc-400">
                <RefreshCw className="w-3 h-3 animate-spin" />
                Verificando estado…
              </span>
            )}
          </div>
          <p className="text-sm text-zinc-500 font-mono ml-8">
            {waNumber.phone ?? waNumber.waha_session_name}
          </p>

          {/* Alerta QR / Reconectar */}
          {waNumber.session_status === "SCAN_QR_CODE" && (
            <div className="ml-0 flex items-center gap-3 p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
              <QrCode className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-medium text-blue-800 dark:text-blue-300">
                  Es necesario escanear el QR para conectar este número
                </p>
                <p className="text-xs text-blue-600 dark:text-blue-400 mt-0.5">
                  Abre el modal y escanea con WhatsApp en tu teléfono
                </p>
              </div>
              <Button
                size="sm"
                onClick={() => setShowQrModal(true)}
                className="gap-1.5 bg-blue-600 hover:bg-blue-700 text-white"
              >
                <QrCode className="w-3.5 h-3.5" />
                Ver QR
              </Button>
            </div>
          )}

          {(waNumber.session_status === "FAILED" || waNumber.session_status === "STOPPED") && (
            <div className="flex items-center gap-3 p-3 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800">
              <XCircle className="w-5 h-5 text-red-500 shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-medium text-red-800 dark:text-red-300">
                  La sesión de WhatsApp está {waNumber.session_status === "FAILED" ? "en error" : "detenida"}
                </p>
                <p className="text-xs text-red-600 dark:text-red-400 mt-0.5">
                  Usa el botón de reconectar para reiniciar la sesión y escanear el QR
                </p>
              </div>
              <Button
                size="sm"
                variant="destructive"
                onClick={handleReconnect}
                disabled={reconnecting}
                className="gap-1.5"
              >
                {reconnecting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="w-3.5 h-3.5" />
                )}
                Reconectar
              </Button>
            </div>
          )}
        </div>
      )}

      {/* QR Modal */}
      {showQrModal && (
        <QrModal
          numberId={id}
          onClose={() => {
            setShowQrModal(false)
            // Refrescar estado del número al cerrar el modal
            loadNumber()
          }}
        />
      )}

      {/* Asignación de persona */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Bot className="w-4 h-4 text-zinc-500" />
            Persona IA asignada
          </CardTitle>
        </CardHeader>
        <CardContent>
          {assignedPersona && (
            <div className="mb-3 p-2 rounded bg-zinc-50 dark:bg-zinc-800 text-xs text-zinc-600 dark:text-zinc-400">
              <span className="font-medium text-zinc-700 dark:text-zinc-300">Actual:</span>{" "}
              {assignedPersona.name}
              {assignedPersona.tone && (
                <span className="ml-2 text-zinc-400">· {assignedPersona.tone}</span>
              )}
            </div>
          )}
          <div className="flex items-end gap-3">
            <div className="flex-1">
              <Label htmlFor="persona_select" className="text-xs mb-1 block">Seleccionar persona</Label>
              <select
                id="persona_select"
                value={selectedPersonaId}
                onChange={e => setSelectedPersonaId(e.target.value)}
                className="w-full h-9 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm px-3 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-400"
              >
                <option value="">— Sin persona asignada —</option>
                {personas.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <Button
              size="sm"
              onClick={handleAssignPersona}
              disabled={personaSaving}
              className="gap-1.5"
            >
              <Save className="w-3.5 h-3.5" />
              {personaSaving ? "Guardando…" : "Guardar"}
            </Button>
          </div>
          {personaSuccess && (
            <p className="mt-2 text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              Persona actualizada correctamente
            </p>
          )}
        </CardContent>
      </Card>

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
