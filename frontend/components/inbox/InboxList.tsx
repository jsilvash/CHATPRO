"use client"

import { useState, useCallback, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter, usePathname } from "next/navigation"
import { Search, RefreshCw, SlidersHorizontal, Download, X, MessageSquare } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ConversationCard } from "./ConversationCard"
import { EmptyState } from "@/components/EmptyState"
import { apiGet, API_URL } from "@/lib/api"
import type { ConversationListResponse, AvailableAgentsResponse } from "@/lib/types"

const STATUS_TABS = [
  { value: "", label: "Todas" },
  { value: "waiting_agent", label: "Esperando" },
  { value: "agent", label: "Agente" },
  { value: "bot", label: "Bot" },
]

export function InboxList() {
  const router = useRouter()
  const pathname = usePathname()
  const [statusFilter, setStatusFilter] = useState("")
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [showFilters, setShowFilters] = useState(false)
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [assignedUserId, setAssignedUserId] = useState("")
  const [exporting, setExporting] = useState(false)

  const hasAdvancedFilters = useMemo(
    () => !!(dateFrom || dateTo || assignedUserId),
    [dateFrom, dateTo, assignedUserId],
  )

  const { data, isLoading, refetch } = useQuery<ConversationListResponse>({
    queryKey: ["inbox", statusFilter, search, page, dateFrom, dateTo, assignedUserId],
    queryFn: () =>
      apiGet<ConversationListResponse>("/v1/inbox", {
        status: statusFilter || undefined,
        search: search || undefined,
        page,
        page_size: 50,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        assigned_user_id: assignedUserId || undefined,
      }),
    refetchInterval: 30_000,
  })

  const { data: agentsData } = useQuery<AvailableAgentsResponse>({
    queryKey: ["available-agents"],
    queryFn: () => apiGet<AvailableAgentsResponse>("/v1/users/available-agents"),
    enabled: showFilters,
  })

  const activeConvId = pathname.split("/inbox/")[1]

  const handleClearFilters = useCallback(() => {
    setDateFrom("")
    setDateTo("")
    setAssignedUserId("")
    setPage(1)
  }, [])

  async function handleExport() {
    setExporting(true)
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set("status", statusFilter)
      if (search) params.set("search", search)
      if (dateFrom) params.set("date_from", dateFrom)
      if (dateTo) params.set("date_to", dateTo)
      if (assignedUserId) params.set("assigned_user_id", assignedUserId)

      const url = `${API_URL}/v1/inbox/export?${params.toString()}`
      const res = await fetch(url, { credentials: "include" })
      if (!res.ok) throw new Error("Error al exportar")

      const blob = await res.blob()
      const link = document.createElement("a")
      link.href = URL.createObjectURL(blob)
      link.download = `inbox-${new Date().toISOString().slice(0, 10)}.csv`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (e) {
      alert(e instanceof Error ? e.message : "Error al exportar CSV")
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Cabecera */}
      <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 space-y-2">
        <div className="flex items-center gap-1">
          <h2 className="font-semibold text-sm text-zinc-900 dark:text-zinc-50 flex-1">Inbox</h2>
          <button
            onClick={() => router.push("/inbox/search")}
            title="Buscar en mensajes"
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors p-1"
          >
            <Search className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setShowFilters(p => !p)}
            title="Filtros avanzados"
            className={`p-1 transition-colors ${hasAdvancedFilters ? "text-blue-600 dark:text-blue-400" : "text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"}`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            title="Exportar CSV"
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors p-1 disabled:opacity-50"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => refetch()}
            title="Actualizar"
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors p-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Búsqueda por nombre/teléfono */}
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-400" />
          <Input
            placeholder="Buscar contacto..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="pl-8 h-8 text-xs"
          />
        </div>

        {/* Panel filtros avanzados */}
        {showFilters && (
          <div className="space-y-2 pt-1 pb-0.5 border-t border-zinc-100 dark:border-zinc-800">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500">Filtros avanzados</span>
              {hasAdvancedFilters && (
                <button
                  onClick={handleClearFilters}
                  className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-0.5 hover:underline"
                >
                  <X className="w-3 h-3" />
                  Limpiar
                </button>
              )}
            </div>
            <div>
              <Label className="text-xs text-zinc-500">Agente asignado</Label>
              <select
                value={assignedUserId}
                onChange={e => { setAssignedUserId(e.target.value); setPage(1) }}
                className="mt-1 w-full text-xs rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-2 py-1.5 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-1 focus:ring-zinc-400"
              >
                <option value="">Todos</option>
                {agentsData?.items.map(a => (
                  <option key={a.id} value={a.id}>{a.full_name || a.email}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <div>
                <Label className="text-xs text-zinc-500">Desde</Label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={e => { setDateFrom(e.target.value); setPage(1) }}
                  className="mt-1 h-7 text-xs"
                />
              </div>
              <div>
                <Label className="text-xs text-zinc-500">Hasta</Label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={e => { setDateTo(e.target.value); setPage(1) }}
                  className="mt-1 h-7 text-xs"
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Tabs de status */}
      <div className="flex border-b border-zinc-100 dark:border-zinc-800 overflow-x-auto">
        {STATUS_TABS.map(({ value, label }) => (
          <button
            key={value}
            onClick={() => { setStatusFilter(value); setPage(1) }}
            className={`px-3 py-2 text-xs font-medium shrink-0 border-b-2 transition-colors ${
              statusFilter === value
                ? "border-zinc-900 text-zinc-900 dark:border-zinc-50 dark:text-zinc-50"
                : "border-transparent text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Lista de conversaciones */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="p-4 text-sm text-zinc-400 text-center">Cargando...</div>
        ) : !data?.items?.length ? (
          <EmptyState
            icon={MessageSquare}
            title="Sin conversaciones"
            description={
              search || hasAdvancedFilters
                ? "No hay conversaciones que coincidan con los filtros aplicados."
                : "Aún no hay conversaciones en este inbox."
            }
          />
        ) : (
          data.items.map((conv) => (
            <ConversationCard
              key={conv.id}
              conv={conv}
              isActive={conv.id === activeConvId}
              onClick={() => router.push(`/inbox/${conv.id}`)}
            />
          ))
        )}
      </div>

      {/* Paginación */}
      {data && data.total_pages > 1 && (
        <div className="p-2 border-t border-zinc-100 dark:border-zinc-800 flex items-center justify-between gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="text-xs h-7 px-2"
          >
            ← Anterior
          </Button>
          <span className="text-xs text-zinc-400">
            {page} / {data.total_pages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
            disabled={page === data.total_pages}
            className="text-xs h-7 px-2"
          >
            Siguiente →
          </Button>
        </div>
      )}
    </div>
  )
}
