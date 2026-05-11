"use client"

import React, { useEffect, useState, useRef, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Search, MessageSquare, Phone, Loader2 } from "lucide-react"
import { apiGet } from "@/lib/api"
import type { ContactSearchOut, ConversationListResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

interface Result {
  id: string
  label: string
  sub: string
  href: string
  group: "Conversaciones" | "Contactos"
  icon: React.ElementType
}

export function CommandPalette() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Atajo de teclado global
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        setOpen((o) => !o)
      }
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  // Enfocar input al abrir
  useEffect(() => {
    if (open) {
      setQuery("")
      setResults([])
      setSelected(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  const search = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); setLoading(false); return }
    setLoading(true)
    try {
      const [contacts, convs] = await Promise.allSettled([
        apiGet<ContactSearchOut[]>("/v1/contacts/search", { q, limit: 5 }),
        apiGet<ConversationListResponse>("/v1/inbox", { search: q, page_size: 5 }),
      ])

      const all: Result[] = []

      if (convs.status === "fulfilled" && convs.value?.items) {
        convs.value.items.forEach((c) => {
          all.push({
            id: `conv-${c.id}`,
            label: c.wa_contact_name || c.wa_contact_phone,
            sub: c.wa_contact_phone,
            href: `/inbox/${c.id}`,
            group: "Conversaciones",
            icon: MessageSquare,
          })
        })
      }

      if (contacts.status === "fulfilled" && contacts.value) {
        contacts.value.forEach((c) => {
          all.push({
            id: `contact-${c.id}`,
            label: c.display_name || c.phone_e164,
            sub: c.phone_e164,
            href: `/contacts/${c.id}`,
            group: "Contactos",
            icon: Phone,
          })
        })
      }

      setResults(all)
      setSelected(0)
    } finally {
      setLoading(false)
    }
  }, [])

  function handleInput(q: string) {
    setQuery(q)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => search(q), 300)
  }

  function navigate(href: string) {
    setOpen(false)
    router.push(href)
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
    }
  }

  if (!open) return null

  const groups = ["Conversaciones", "Contactos"] as const
  const byGroup = (g: string) => results.filter((r) => r.group === g)

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center pt-[15vh] bg-black/50 backdrop-blur-sm"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg bg-white dark:bg-zinc-900 rounded-xl shadow-2xl border border-zinc-200 dark:border-zinc-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-200 dark:border-zinc-700">
          {loading ? (
            <Loader2 className="w-4 h-4 shrink-0 text-zinc-400 animate-spin" />
          ) : (
            <Search className="w-4 h-4 shrink-0 text-zinc-400" />
          )}
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Buscar conversaciones y contactos…"
            className="flex-1 bg-transparent text-sm text-zinc-900 dark:text-zinc-50 outline-none placeholder:text-zinc-400"
          />
          <kbd className="hidden sm:flex items-center gap-0.5 text-xs text-zinc-400 bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded">
            Esc
          </kbd>
        </div>

        {/* Resultados */}
        <div className="max-h-80 overflow-y-auto">
          {!query.trim() && (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              Escribe para buscar contactos y conversaciones
            </p>
          )}

          {query.trim() && !loading && results.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-zinc-400">
              Sin resultados para &ldquo;{query}&rdquo;
            </p>
          )}

          {groups.map((group) => {
            const items = byGroup(group)
            if (!items.length) return null
            return (
              <div key={group}>
                <p className="px-4 pt-3 pb-1 text-xs font-medium text-zinc-400 uppercase tracking-wide">
                  {group}
                </p>
                {items.map((item, i) => {
                  const globalIdx = results.indexOf(item)
                  const Icon = item.icon
                  return (
                    <button
                      key={item.id}
                      onClick={() => navigate(item.href)}
                      className={cn(
                        "w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors",
                        globalIdx === selected
                          ? "bg-zinc-100 dark:bg-zinc-800"
                          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50",
                      )}
                      onMouseEnter={() => setSelected(globalIdx)}
                    >
                      <div className="w-7 h-7 rounded-md bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                        <Icon className="w-3.5 h-3.5 text-zinc-500" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">{item.label}</p>
                        <p className="text-xs text-zinc-400 truncate">{item.sub}</p>
                      </div>
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-zinc-100 dark:border-zinc-800 flex items-center gap-3 text-xs text-zinc-400">
          <span className="flex items-center gap-1">
            <kbd className="bg-zinc-100 dark:bg-zinc-800 px-1 rounded">↑↓</kbd> navegar
          </span>
          <span className="flex items-center gap-1">
            <kbd className="bg-zinc-100 dark:bg-zinc-800 px-1 rounded">↵</kbd> abrir
          </span>
          <span className="flex items-center gap-1">
            <kbd className="bg-zinc-100 dark:bg-zinc-800 px-1 rounded">Esc</kbd> cerrar
          </span>
        </div>
      </div>
    </div>
  )
}
