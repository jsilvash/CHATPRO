"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Search, X, MessageSquare, Phone, Loader2 } from "lucide-react"
import { apiGet } from "@/lib/api"
import type { ContactSearchOut, ConversationListResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

interface SearchResult {
  type: "contact" | "conversation"
  id: string
  label: string
  sublabel?: string
  href: string
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const router = useRouter()
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (open) {
      setQuery("")
      setResults([])
      setSelected(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      return
    }
    setLoading(true)
    try {
      const [contacts, convs] = await Promise.all([
        apiGet<ContactSearchOut[]>("/v1/contacts/search", { q, limit: 5 }),
        apiGet<ConversationListResponse>("/v1/inbox", { search: q, page: 1, page_size: 5 }),
      ])

      const contactResults: SearchResult[] = (contacts ?? []).map((c) => ({
        type: "contact" as const,
        id: c.id,
        label: c.display_name ?? c.phone_e164,
        sublabel: c.phone_e164,
        href: `/contacts/${c.id}`,
      }))

      const convResults: SearchResult[] = (convs?.items ?? []).map((c) => ({
        type: "conversation" as const,
        id: c.id,
        label: c.wa_contact_name || c.wa_contact_phone,
        sublabel: `${c.status} · ${c.turn_count} turnos`,
        href: `/inbox/${c.id}`,
      }))

      setResults([...contactResults, ...convResults])
    } catch {
      // ignorar errores de red
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current!)
    debounceRef.current = setTimeout(() => search(query), 300)
    return () => clearTimeout(debounceRef.current!)
  }, [query, search])

  function navigate(href: string) {
    router.push(href)
    onClose()
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setSelected((s) => Math.min(s + 1, results.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setSelected((s) => Math.max(s - 1, 0))
    } else if (e.key === "Enter" && results[selected]) {
      navigate(results[selected].href)
    } else if (e.key === "Escape") {
      onClose()
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg mx-4 bg-white dark:bg-zinc-900 rounded-xl shadow-2xl border border-zinc-200 dark:border-zinc-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-100 dark:border-zinc-800">
          {loading ? (
            <Loader2 className="w-4 h-4 text-zinc-400 animate-spin shrink-0" />
          ) : (
            <Search className="w-4 h-4 text-zinc-400 shrink-0" />
          )}
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Buscar contactos o conversaciones..."
            className="flex-1 text-sm bg-transparent outline-none text-zinc-900 dark:text-zinc-50 placeholder:text-zinc-400"
          />
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Resultados */}
        {results.length > 0 ? (
          <ul className="py-2 max-h-72 overflow-y-auto">
            {results.map((r, i) => (
              <li key={r.id}>
                <button
                  className={cn(
                    "w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors",
                    selected === i && "bg-zinc-50 dark:bg-zinc-800",
                  )}
                  onClick={() => navigate(r.href)}
                  onMouseEnter={() => setSelected(i)}
                >
                  <div className={cn(
                    "w-7 h-7 rounded-full flex items-center justify-center shrink-0",
                    r.type === "contact"
                      ? "bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"
                      : "bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400",
                  )}>
                    {r.type === "contact" ? <Phone className="w-3.5 h-3.5" /> : <MessageSquare className="w-3.5 h-3.5" />}
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-zinc-800 dark:text-zinc-200 truncate">{r.label}</p>
                    {r.sublabel && (
                      <p className="text-xs text-zinc-500 truncate">{r.sublabel}</p>
                    )}
                  </div>
                  <span className="ml-auto text-xs text-zinc-400 shrink-0">
                    {r.type === "contact" ? "Contacto" : "Conversación"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : query && !loading ? (
          <p className="px-4 py-6 text-sm text-center text-zinc-400">
            Sin resultados para &quot;{query}&quot;
          </p>
        ) : !query ? (
          <p className="px-4 py-4 text-xs text-center text-zinc-400">
            Escribe para buscar contactos o conversaciones
          </p>
        ) : null}

        <div className="px-4 py-2 border-t border-zinc-100 dark:border-zinc-800 flex items-center gap-4 text-xs text-zinc-400">
          <span><kbd className="font-mono">↑↓</kbd> navegar</span>
          <span><kbd className="font-mono">↵</kbd> abrir</span>
          <span><kbd className="font-mono">Esc</kbd> cerrar</span>
        </div>
      </div>
    </div>
  )
}
