"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter, usePathname } from "next/navigation"
import { Search, RefreshCw } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { ConversationCard } from "./ConversationCard"
import { apiGet } from "@/lib/api"
import type { ConversationListResponse } from "@/lib/types"

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

  const { data, isLoading, refetch } = useQuery<ConversationListResponse>({
    queryKey: ["inbox", statusFilter, search, page],
    queryFn: () =>
      apiGet<ConversationListResponse>("/v1/inbox", {
        status: statusFilter || undefined,
        search: search || undefined,
        page,
        page_size: 50,
      }),
    refetchInterval: 30_000,
  })

  const activeConvId = pathname.split("/inbox/")[1]

  return (
    <div className="flex flex-col h-full">
      {/* Cabecera + buscar */}
      <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold text-sm text-zinc-900 dark:text-zinc-50 flex-1">Inbox</h2>
          <button
            onClick={() => refetch()}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
            title="Actualizar"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-400" />
          <Input
            placeholder="Buscar..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="pl-8 h-8 text-xs"
          />
        </div>
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
          <div className="p-4 text-sm text-zinc-400 text-center">Sin conversaciones</div>
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
