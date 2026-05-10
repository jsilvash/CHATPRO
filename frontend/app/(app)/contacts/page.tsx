"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { ContactSearchOut } from "@/lib/types"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Search, Phone, MessageSquare, ChevronRight, Loader2 } from "lucide-react"

export default function ContactsPage() {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<ContactSearchOut[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      setSearched(false)
      return
    }
    setLoading(true)
    try {
      const data = await apiGet<ContactSearchOut[]>("/v1/contacts/search", { q, limit: 20 })
      setResults(data)
      setSearched(true)
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => search(query), 350)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, search])

  return (
    <div className="p-6 max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Contactos</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Busca contactos por nombre o número de teléfono</p>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
        <Input
          className="pl-9"
          placeholder="Nombre o número de teléfono..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-zinc-400" />
        )}
      </div>

      {/* Resultados */}
      {results.length > 0 ? (
        <div className="space-y-2">
          {results.map((contact) => (
            <Link key={contact.id} href={`/contacts/${contact.id}`}>
              <Card className="hover:border-zinc-300 dark:hover:border-zinc-600 transition-colors cursor-pointer">
                <CardContent className="py-3 px-4">
                  <div className="flex items-center gap-3">
                    <div className="p-2 rounded-full bg-blue-100 dark:bg-blue-900/30">
                      <Phone className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">
                        {contact.display_name || contact.phone_e164}
                      </p>
                      <p className="text-xs text-zinc-400">{contact.phone_e164}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="gap-1">
                        <MessageSquare className="w-3 h-3" />
                        {contact.conversations_count}
                      </Badge>
                      <ChevronRight className="w-4 h-4 text-zinc-300" />
                    </div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : searched && !loading ? (
        <p className="text-center text-sm text-zinc-400 py-12">
          No se encontraron contactos para &quot;{query}&quot;
        </p>
      ) : !query ? (
        <div className="text-center py-16 space-y-2">
          <Search className="w-8 h-8 text-zinc-200 mx-auto dark:text-zinc-700" />
          <p className="text-sm text-zinc-400">Escribe para buscar contactos</p>
        </div>
      ) : null}
    </div>
  )
}
