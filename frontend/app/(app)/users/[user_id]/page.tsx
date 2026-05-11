"use client"

import { useEffect, useState } from "react"
import { use } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { UserOut, UserStats, AgentMetricsOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  ArrowLeft,
  MessageSquare,
  Clock,
  StickyNote,
  Calendar,
  Loader2,
  AlertCircle,
  TrendingUp,
  BarChart2,
  CheckCircle2,
  Zap,
} from "lucide-react"

function formatSeconds(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—"
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

function todayMinus(days: number) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Propietario",
  admin: "Administrador",
  agent: "Agente",
}

export default function UserDetailPage({ params }: { params: Promise<{ user_id: string }> }) {
  const { user_id } = use(params)
  const [user, setUser] = useState<UserOut | null>(null)
  const [stats, setStats] = useState<UserStats | null>(null)
  const [agentMetrics, setAgentMetrics] = useState<AgentMetricsOut | null>(null)
  const [dateFrom, setDateFrom] = useState(todayMinus(30))
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10))
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [metricsError, setMetricsError] = useState<string | null>(null)

  useEffect(() => {
    async function fetch() {
      try {
        const [u, s] = await Promise.all([
          apiGet<UserOut>(`/v1/users/${user_id}`),
          apiGet<UserStats>(`/v1/users/${user_id}/stats`),
        ])
        setUser(u)
        setStats(s)
      } catch (e) {
        setError(e instanceof Error ? e.message : "Error al cargar usuario")
      } finally {
        setLoading(false)
      }
    }
    fetch()
  }, [user_id])

  async function loadMetrics() {
    setMetricsLoading(true)
    setMetricsError(null)
    try {
      const m = await apiGet<AgentMetricsOut>(`/v1/users/${user_id}/metrics`, {
        date_from: dateFrom,
        date_to: dateTo,
      })
      setAgentMetrics(m)
    } catch (e) {
      setMetricsError(e instanceof Error ? e.message : "Error al cargar métricas")
    } finally {
      setMetricsLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/users">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Detalle de usuario</h1>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
        </div>
      ) : user ? (
        <>
          {/* Info del usuario */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{user.full_name || user.email}</span>
                <div className="flex gap-2">
                  <Badge variant={user.is_active ? "success" : "destructive"}>
                    {user.is_active ? "Activo" : "Inactivo"}
                  </Badge>
                  <Badge variant="secondary">{ROLE_LABELS[user.role] ?? user.role}</Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-700 dark:text-zinc-300 w-24">Email</span>
                <span>{user.email}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-700 dark:text-zinc-300 w-24">Creado</span>
                <span>{new Date(user.created_at).toLocaleDateString("es")}</span>
              </div>
            </CardContent>
          </Card>

          {/* Estadísticas actuales */}
          {stats && (
            <div>
              <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
                Estado actual
              </h2>
              <div className="grid grid-cols-2 gap-4">
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <MessageSquare className="w-5 h-5 text-blue-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Conversaciones activas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.conversations_active}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <Calendar className="w-5 h-5 text-green-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Conversaciones hoy</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.conversations_today}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <Clock className="w-5 h-5 text-yellow-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Primera respuesta (promedio)</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {formatSeconds(stats.avg_first_response_sec)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <StickyNote className="w-5 h-5 text-purple-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Notas creadas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.notes_count}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {/* Métricas por período */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
              <BarChart2 className="w-4 h-4" />
              Métricas por período
            </h2>

            <Card className="mb-4">
              <CardContent className="pt-4">
                <div className="flex items-end gap-4 flex-wrap">
                  <div>
                    <Label htmlFor="m_date_from" className="text-xs">Desde</Label>
                    <Input
                      id="m_date_from"
                      type="date"
                      value={dateFrom}
                      onChange={e => setDateFrom(e.target.value)}
                      className="mt-1 w-40"
                    />
                  </div>
                  <div>
                    <Label htmlFor="m_date_to" className="text-xs">Hasta</Label>
                    <Input
                      id="m_date_to"
                      type="date"
                      value={dateTo}
                      onChange={e => setDateTo(e.target.value)}
                      className="mt-1 w-40"
                    />
                  </div>
                  <Button onClick={loadMetrics} disabled={metricsLoading} size="sm">
                    {metricsLoading ? "Cargando…" : "Calcular"}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {metricsError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm mb-4">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {metricsError}
              </div>
            )}

            {metricsLoading ? (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Card key={i}>
                    <CardContent className="pt-5">
                      <div className="animate-pulse space-y-2">
                        <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-3/4" />
                        <div className="h-7 bg-zinc-200 dark:bg-zinc-700 rounded w-1/2" />
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : agentMetrics ? (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Conversaciones resueltas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {agentMetrics.conversations_handled}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <Clock className="w-5 h-5 text-yellow-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Primera respuesta (prom.)</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {formatSeconds(agentMetrics.avg_first_response_sec)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <TrendingUp className="w-5 h-5 text-blue-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Tiempo resolución (prom.)</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {formatSeconds(agentMetrics.avg_resolution_sec)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <MessageSquare className="w-5 h-5 text-indigo-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Mensajes enviados</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {agentMetrics.messages_sent}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <StickyNote className="w-5 h-5 text-purple-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Notas creadas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {agentMetrics.notes_created}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-start gap-3">
                      <Zap className="w-5 h-5 text-orange-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-zinc-500">Hora más activa</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {agentMetrics.busiest_hour !== null
                            ? `${String(agentMetrics.busiest_hour).padStart(2, "0")}:00`
                            : "—"}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            ) : (
              <p className="text-xs text-zinc-400 text-center py-8">
                Selecciona un período y haz clic en «Calcular» para ver las métricas.
              </p>
            )}
          </div>
        </>
      ) : null}
    </div>
  )
}
