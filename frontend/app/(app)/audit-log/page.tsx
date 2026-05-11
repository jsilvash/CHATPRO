"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Shield, ChevronLeft, ChevronRight, Download, Loader2, Filter } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { apiGet, API_URL } from "@/lib/api"
import { formatDateTime } from "@/lib/date"

interface AuditLogOut {
  id: string
  created_at: string
  actor_user_id: string | null
  actor_api_key_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  ip: string | null
}

interface AuditLogPage {
  items: AuditLogOut[]
  total: number
  page: number
  pages: number
}

const ACTION_VARIANT: Record<string, "default" | "success" | "destructive" | "warning" | "secondary"> = {
  login: "success",
  logout: "secondary",
  create: "success",
  update: "default",
  delete: "destructive",
  export: "warning",
  revoke: "destructive",
}

function actionVariant(action: string) {
  const key = Object.keys(ACTION_VARIANT).find((k) => action.toLowerCase().includes(k))
  return key ? ACTION_VARIANT[key] : "secondary"
}

function truncate(s: string | null, n = 8) {
  if (!s) return "—"
  return s.length <= n ? s : s.slice(0, n) + "…"
}

export default function AuditLogPage() {
  const [page, setPage] = useState(1)
  const [filterAction, setFilterAction] = useState("")
  const [filterTarget, setFilterTarget] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [showFilters, setShowFilters] = useState(false)

  const params: Record<string, string | number> = { page, limit: 50 }
  if (filterAction) params.action = filterAction
  if (filterTarget) params.target_type = filterTarget
  if (dateFrom) params.date_from = dateFrom
  if (dateTo) params.date_to = dateTo

  const { data, isLoading, error } = useQuery<AuditLogPage>({
    queryKey: ["audit-log", page, filterAction, filterTarget, dateFrom, dateTo],
    queryFn: () => apiGet<AuditLogPage>("/v1/audit-log", params),
    staleTime: 30_000,
  })

  async function handleExport() {
    const res = await fetch(`${API_URL}/v1/audit-log/export`, { credentials: "include" })
    if (!res.ok) return
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "audit-log.csv"
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="w-6 h-6 text-zinc-600" />
          <div>
            <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">Audit Log</h1>
            {data && (
              <p className="text-xs text-zinc-400 mt-0.5">{data.total.toLocaleString("es")} entradas totales</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowFilters((v) => !v)}
            className="gap-2"
          >
            <Filter className="w-3.5 h-3.5" />
            Filtros
          </Button>
          <Button variant="outline" size="sm" onClick={handleExport} className="gap-2">
            <Download className="w-3.5 h-3.5" />
            Exportar CSV
          </Button>
        </div>
      </div>

      {/* Filtros */}
      {showFilters && (
        <Card>
          <CardContent className="pt-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="space-y-1">
                <Label className="text-xs text-zinc-500">Acción</Label>
                <Input
                  value={filterAction}
                  onChange={(e) => { setFilterAction(e.target.value); setPage(1) }}
                  placeholder="ej: login, create…"
                  className="text-sm h-8"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-zinc-500">Tipo objetivo</Label>
                <Input
                  value={filterTarget}
                  onChange={(e) => { setFilterTarget(e.target.value); setPage(1) }}
                  placeholder="ej: user, tenant…"
                  className="text-sm h-8"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-zinc-500">Desde</Label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => { setDateFrom(e.target.value); setPage(1) }}
                  className="text-sm h-8"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-zinc-500">Hasta</Label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => { setDateTo(e.target.value); setPage(1) }}
                  className="text-sm h-8"
                />
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Tabla */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
        </div>
      ) : error ? (
        <div className="text-center py-20 text-zinc-500 text-sm">Error al cargar el audit log</div>
      ) : !data?.items.length ? (
        <div className="text-center py-20 text-zinc-500 text-sm">No hay entradas en el audit log</div>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 dark:bg-zinc-800 border-b border-zinc-200 dark:border-zinc-700">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                    Fecha
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                    Actor
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                    Acción
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                    Objetivo
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                    IP
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800 bg-white dark:bg-zinc-900">
                {data.items.map((entry) => (
                  <tr key={entry.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                    <td className="px-4 py-3 text-xs text-zinc-500 whitespace-nowrap">
                      {formatDateTime(new Date(entry.created_at))}
                    </td>
                    <td className="px-4 py-3">
                      {entry.actor_user_id ? (
                        <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300">
                          usr:{truncate(entry.actor_user_id)}
                        </span>
                      ) : entry.actor_api_key_id ? (
                        <span className="text-xs font-mono text-blue-600 dark:text-blue-400">
                          key:{truncate(entry.actor_api_key_id)}
                        </span>
                      ) : (
                        <span className="text-xs text-zinc-400">sistema</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={actionVariant(entry.action)} className="text-xs font-normal">
                        {entry.action}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      {entry.target_type ? (
                        <span className="text-xs text-zinc-700 dark:text-zinc-300">
                          {entry.target_type}
                          {entry.target_id && (
                            <span className="text-zinc-400 ml-1 font-mono">
                              :{truncate(entry.target_id)}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-xs text-zinc-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500 font-mono">
                      {entry.ip ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Paginación */}
          <div className="flex items-center justify-between">
            <p className="text-xs text-zinc-500">
              Página {data.page} de {data.pages}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="gap-1"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Anterior
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= (data.pages ?? 1)}
                className="gap-1"
              >
                Siguiente
                <ChevronRight className="w-3.5 h-3.5" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
