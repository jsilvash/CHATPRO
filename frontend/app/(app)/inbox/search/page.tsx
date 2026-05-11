"use client"

import { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { apiGet } from "@/lib/api"
import type { SearchResponse, MessageSearchResult } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { ArrowLeft, Search, MessageSquare } from "lucide-react"

function highlight(text: string, query: string) {
  if (!query.trim()) return text
  const words = query.trim().split(/\s+/)
  const re = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi")
  const parts = text.split(re)
  return (
    <>
      {parts.map((part, i) =>
        re.test(part) ? (
          <mark key={i} className="bg-yellow-100 dark:bg-yellow-900/50 text-yellow-900 dark:text-yellow-200 rounded px-0.5">
            {part}
          </mark>
        ) : part
      )}
    </>
  )
}

function ContextMsg({ msg, query }: { msg: { direction: string; text: string; created_at: string }; query: string }) {
  return (
    <div className={`text-xs px-2 py-1 rounded ${msg.direction === "out" ? "ml-8 bg-zinc-100 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300" : "mr-8 bg-zinc-50 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"}`}>
      {highlight(msg.text, query)}
    </div>
  )
}

function ResultCard({ result, query, onOpen }: { result: MessageSearchResult; query: string; onOpen: (convId: string) => void }) {
  return (
    <Card className="cursor-pointer hover:border-zinc-300 dark:hover:border-zinc-600 transition-colors" onClick={() => onOpen(result.conversation_id)}>
      <CardContent className="pt-4 space-y-2">
        {/* Contexto antes */}
        {result.context_before.map(m => (
          <ContextMsg key={m.id} msg={m} query="" />
        ))}

        {/* Mensaje principal */}
        <div className={`text-sm px-3 py-2 rounded-lg font-medium border ${result.direction === "out" ? "ml-6 bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-100" : "mr-6 bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-50"}`}>
          {highlight(result.text, query)}
        </div>

        {/* Contexto después */}
        {result.context_after.map(m => (
          <ContextMsg key={m.id} msg={m} query="" />
        ))}

        <div className="flex items-center gap-2 pt-1">
          <MessageSquare className="w-3 h-3 text-zinc-400" />
          <span className="text-xs text-zinc-400 font-mono">{result.conversation_id.slice(0, 8)}…</span>
          <span className="text-xs text-zinc-400 ml-auto">
            {new Date(result.created_at).toLocaleString("es", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

export default function InboxSearchPage() {
  const router = useRouter()
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiGet<SearchResponse>("/v1/inbox/search", { q, limit: 50 })
      setResults(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al buscar")
    } finally {
      setLoading(false)
    }
  }, [query])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSearch()
  }

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.push("/inbox")} className="gap-1.5">
          <ArrowLeft className="w-3.5 h-3.5" />
          Volver
        </Button>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Buscar en mensajes</h1>
      </div>

      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" />
          <Input
            placeholder="Buscar texto en conversaciones..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            className="pl-9"
            autoFocus
          />
        </div>
        <Button onClick={handleSearch} disabled={loading || !query.trim()}>
          {loading ? "Buscando…" : "Buscar"}
        </Button>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          {error}
        </div>
      )}

      {results && (
        <div className="space-y-4">
          <p className="text-sm text-zinc-500">
            {results.total} {results.total === 1 ? "resultado" : "resultados"} para <strong>&ldquo;{query}&rdquo;</strong>
          </p>

          {results.items.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-zinc-400 gap-3">
              <Search className="w-10 h-10" />
              <p className="text-sm">Sin resultados</p>
            </div>
          ) : (
            <div className="space-y-3">
              {results.items.map(r => (
                <ResultCard
                  key={r.id}
                  result={r}
                  query={query}
                  onOpen={convId => router.push(`/inbox/${convId}`)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {!results && !loading && (
        <div className="flex flex-col items-center py-16 text-zinc-400 gap-3">
          <Search className="w-10 h-10" />
          <p className="text-sm">Escribe algo para buscar en el historial de mensajes</p>
        </div>
      )}
    </div>
  )
}
