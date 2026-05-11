"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { apiFetch, apiGet } from "@/lib/api"
import type { CannedResponseOut, CannedResponseListOut, RenderOut } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Plus,
  Search,
  Pencil,
  Trash2,
  Eye,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Zap,
  X,
} from "lucide-react"

// ── Form crear/editar ──────────────────────────────────────────────────────────

interface CannedFormProps {
  initial?: CannedResponseOut
  onSave: (item: CannedResponseOut) => void
  onCancel: () => void
}

function CannedForm({ initial, onSave, onCancel }: CannedFormProps) {
  const [shortcode, setShortcode] = useState(initial?.shortcode ?? "")
  const [text, setText] = useState(initial?.text ?? "")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const variables = Array.from(text.matchAll(/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g)).map(m => m[1])
  const uniqueVars = [...new Set(variables)]

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      let result: CannedResponseOut
      if (initial) {
        result = await apiFetch<CannedResponseOut>(`/v1/canned-responses/${initial.id}`, {
          method: "PATCH",
          body: JSON.stringify({ shortcode: shortcode.trim(), text: text.trim() }),
        })
      } else {
        result = await apiFetch<CannedResponseOut>("/v1/canned-responses", {
          method: "POST",
          body: JSON.stringify({ shortcode: shortcode.trim(), text: text.trim() }),
        })
      }
      onSave(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al guardar")
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}
      <div>
        <Label htmlFor="shortcode">Shortcode</Label>
        <Input
          id="shortcode"
          value={shortcode}
          onChange={e => setShortcode(e.target.value)}
          placeholder="saludo_inicial"
          required
          className="mt-1 font-mono"
        />
      </div>
      <div>
        <Label htmlFor="text">Texto del template</Label>
        <textarea
          id="text"
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="Hola {{nombre}}, ¿en qué te puedo ayudar hoy?"
          required
          rows={4}
          className="mt-1 w-full rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-50 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y"
        />
        {uniqueVars.length > 0 && (
          <p className="mt-1 text-xs text-zinc-500">
            Variables detectadas:{" "}
            {uniqueVars.map(v => (
              <code key={v} className="mx-0.5 px-1 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded text-blue-600 dark:text-blue-400">
                {`{{${v}}}`}
              </code>
            ))}
          </p>
        )}
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={loading}>Cancelar</Button>
        <Button type="submit" disabled={loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
          {initial ? "Guardar cambios" : "Crear template"}
        </Button>
      </div>
    </form>
  )
}

// ── Modal preview/render ───────────────────────────────────────────────────────

interface PreviewModalProps {
  item: CannedResponseOut
  onClose: () => void
}

function PreviewModal({ item, onClose }: PreviewModalProps) {
  const [vars, setVars] = useState<Record<string, string>>({})
  const [rendered, setRendered] = useState<RenderOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleRender() {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      Object.entries(vars).forEach(([k, v]) => params.set(k, v))
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1/canned-responses/${item.id}/render?${params.toString()}`,
        { credentials: "include" }
      )
      if (!res.ok) throw new Error(await res.text())
      setRendered(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Preview — <code className="text-blue-600 dark:text-blue-400">/{item.shortcode}</code>
          </h2>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-3 rounded-lg bg-zinc-50 dark:bg-zinc-800 text-sm font-mono whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
          {item.text}
        </div>

        {item.variables.length > 0 && (
          <div className="space-y-3">
            <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">Valores de prueba</p>
            {item.variables.map(v => (
              <div key={v}>
                <Label htmlFor={`var-${v}`}>{`{{${v}}}`}</Label>
                <Input
                  id={`var-${v}`}
                  value={vars[v] ?? ""}
                  onChange={e => setVars(prev => ({ ...prev, [v]: e.target.value }))}
                  placeholder={`valor de ${v}`}
                  className="mt-1"
                />
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        {rendered && (
          <div className="space-y-1">
            <p className="text-xs text-zinc-500 font-medium uppercase tracking-wide">Resultado</p>
            <div className="p-3 rounded-lg bg-green-50 dark:bg-green-900/20 text-sm whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
              {rendered.rendered_text}
            </div>
            {rendered.variables_missing.length > 0 && (
              <p className="text-xs text-yellow-600 dark:text-yellow-400">
                Variables sin valor: {rendered.variables_missing.map(v => `{{${v}}}`).join(", ")}
              </p>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose}>Cerrar</Button>
          <Button onClick={handleRender} disabled={loading} className="gap-2">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
            Renderizar
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Página principal ──────────────────────────────────────────────────────────

export default function CannedResponsesPage() {
  const [items, setItems] = useState<CannedResponseOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [searchQuery, setSearchQuery] = useState("")
  const [searching, setSearching] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<CannedResponseOut | null>(null)
  const [previewing, setPreviewing] = useState<CannedResponseOut | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null)

  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await apiGet<CannedResponseListOut>("/v1/canned-responses")
      setItems(data.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar templates")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function doSearch(q: string) {
    if (!q.trim()) { load(); return }
    setSearching(true)
    try {
      const data = await apiGet<CannedResponseListOut>("/v1/canned-responses/search", { q: q.trim() })
      setItems(data.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al buscar")
    } finally {
      setSearching(false)
    }
  }

  function onSearchChange(q: string) {
    setSearchQuery(q)
    if (searchTimeout.current) clearTimeout(searchTimeout.current)
    searchTimeout.current = setTimeout(() => doSearch(q), 350)
  }

  function handleSaved(item: CannedResponseOut) {
    setItems(prev => {
      const idx = prev.findIndex(i => i.id === item.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = item
        return next
      }
      return [item, ...prev]
    })
    setShowForm(false)
    setEditing(null)
  }

  async function handleDelete(id: string) {
    if (!confirm("¿Eliminar este template?")) return
    setDeleting(id)
    try {
      await apiFetch(`/v1/canned-responses/${id}`, { method: "DELETE" })
      setItems(prev => prev.filter(i => i.id !== id))
      setDeleteSuccess(id)
      setTimeout(() => setDeleteSuccess(null), 2000)
    } catch (e) {
      alert(e instanceof Error ? e.message : "Error al eliminar")
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Respuestas rápidas</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Templates con variables para agilizar respuestas</p>
        </div>
        <Button onClick={() => { setEditing(null); setShowForm(true) }} className="gap-2">
          <Plus className="w-4 h-4" />
          Nuevo template
        </Button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
        <Input
          value={searchQuery}
          onChange={e => onSearchChange(e.target.value)}
          placeholder="Buscar por shortcode o texto…"
          className="pl-9"
        />
        {searching && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400 animate-spin" />
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Form inline */}
      {(showForm || editing) && (
        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-medium text-zinc-900 dark:text-zinc-50 mb-4">
              {editing ? "Editar template" : "Nuevo template"}
            </h2>
            <CannedForm
              initial={editing ?? undefined}
              onSave={handleSaved}
              onCancel={() => { setShowForm(false); setEditing(null) }}
            />
          </CardContent>
        </Card>
      )}

      {/* List */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-4 pb-4 animate-pulse">
                <div className="flex gap-4">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-24" />
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded flex-1" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-zinc-400 gap-3">
          <Zap className="w-12 h-12" />
          <p className="text-sm">
            {searchQuery ? `Sin resultados para "${searchQuery}"` : "No hay templates aún."}
          </p>
          {!searchQuery && (
            <Button variant="outline" onClick={() => setShowForm(true)} className="gap-2">
              <Plus className="w-4 h-4" />
              Crear el primero
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <Card key={item.id}>
              <CardContent className="py-3 px-4">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="text-sm font-semibold text-blue-600 dark:text-blue-400">
                        /{item.shortcode}
                      </code>
                      {item.variables.length > 0 && (
                        <div className="flex gap-1 flex-wrap">
                          {item.variables.map(v => (
                            <span
                              key={v}
                              className="text-xs px-1.5 py-0.5 bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 rounded font-mono"
                            >
                              {`{{${v}}}`}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400 line-clamp-2">{item.text}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => setPreviewing(item)}
                      className="p-1.5 rounded-md text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 transition-colors"
                      title="Vista previa"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => { setEditing(item); setShowForm(false) }}
                      className="p-1.5 rounded-md text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 transition-colors"
                      title="Editar"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(item.id)}
                      disabled={deleting === item.id}
                      className="p-1.5 rounded-md text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-900/20 dark:hover:text-red-400 transition-colors"
                      title="Eliminar"
                    >
                      {deleting === item.id
                        ? <Loader2 className="w-4 h-4 animate-spin" />
                        : <Trash2 className="w-4 h-4" />
                      }
                    </button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {previewing && (
        <PreviewModal item={previewing} onClose={() => setPreviewing(null)} />
      )}
    </div>
  )
}
