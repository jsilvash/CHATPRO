"use client"

import { useEffect, useState, useCallback } from "react"
import { apiFetch, apiGet } from "@/lib/api"
import type { PersonaListResponse, PersonaResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AlertCircle, Plus, Bot, Pencil, Trash2, X } from "lucide-react"

// ── Form de persona ───────────────────────────────────────────────────────────

interface PersonaFormData {
  name: string
  system_prompt: string
  tone: string
  locale: string
  locale_secondary: string
  auto_detect_locale: boolean
  timezone: string
  model_id: string
}

const EMPTY_FORM: PersonaFormData = {
  name: "",
  system_prompt: "",
  tone: "amigable",
  locale: "es-CL",
  locale_secondary: "",
  auto_detect_locale: false,
  timezone: "America/Santiago",
  model_id: "claude-sonnet-4-6",
}

function personaToForm(p: PersonaResponse): PersonaFormData {
  return {
    name: p.name,
    system_prompt: p.system_prompt,
    tone: p.tone,
    locale: p.locale,
    locale_secondary: p.locale_secondary.join(", "),
    auto_detect_locale: p.auto_detect_locale,
    timezone: p.timezone,
    model_id: p.model_id,
  }
}

interface PersonaFormProps {
  initial?: PersonaResponse
  onSave: (data: PersonaFormData) => Promise<void>
  onCancel: () => void
  loading: boolean
  error: string | null
}

function PersonaForm({ initial, onSave, onCancel, loading, error }: PersonaFormProps) {
  const [form, setForm] = useState<PersonaFormData>(initial ? personaToForm(initial) : EMPTY_FORM)

  function set(key: keyof PersonaFormData, value: string | boolean) {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            {initial ? "Editar persona" : "Nueva persona"}
          </h2>
          <button onClick={onCancel} className="text-zinc-400 hover:text-zinc-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <Label htmlFor="name">Nombre *</Label>
            <Input id="name" value={form.name} onChange={e => set("name", e.target.value)} className="mt-1" placeholder="Mi asistente" />
          </div>

          <div>
            <Label htmlFor="system_prompt">Prompt del sistema</Label>
            <textarea
              id="system_prompt"
              value={form.system_prompt}
              onChange={e => set("system_prompt", e.target.value)}
              rows={4}
              className="mt-1 w-full text-sm rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-zinc-900 dark:text-zinc-50 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-400 resize-y"
              placeholder="Eres un asistente amigable de…"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="tone">Tono</Label>
              <select
                id="tone"
                value={form.tone}
                onChange={e => set("tone", e.target.value)}
                className="mt-1 w-full text-sm rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-400"
              >
                <option value="amigable">Amigable</option>
                <option value="formal">Formal</option>
                <option value="neutral">Neutral</option>
                <option value="técnico">Técnico</option>
                <option value="empático">Empático</option>
              </select>
            </div>
            <div>
              <Label htmlFor="locale">Idioma principal</Label>
              <Input id="locale" value={form.locale} onChange={e => set("locale", e.target.value)} className="mt-1" placeholder="es-CL" />
            </div>
          </div>

          <div>
            <Label htmlFor="locale_secondary">Idiomas secundarios</Label>
            <Input
              id="locale_secondary"
              value={form.locale_secondary}
              onChange={e => set("locale_secondary", e.target.value)}
              className="mt-1"
              placeholder="en-US, pt-BR (separados por coma)"
            />
            <p className="text-xs text-zinc-400 mt-1">Separados por coma</p>
          </div>

          <div className="flex items-center gap-2">
            <input
              id="auto_detect_locale"
              type="checkbox"
              checked={form.auto_detect_locale}
              onChange={e => set("auto_detect_locale", e.target.checked)}
              className="h-4 w-4 rounded border-zinc-300"
            />
            <Label htmlFor="auto_detect_locale" className="cursor-pointer">
              Detectar idioma automáticamente
            </Label>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="timezone">Zona horaria</Label>
              <Input id="timezone" value={form.timezone} onChange={e => set("timezone", e.target.value)} className="mt-1" placeholder="America/Santiago" />
            </div>
            <div>
              <Label htmlFor="model_id">Modelo IA</Label>
              <select
                id="model_id"
                value={form.model_id}
                onChange={e => set("model_id", e.target.value)}
                className="mt-1 w-full text-sm rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-zinc-900 dark:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-400"
              >
                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5</option>
                <option value="claude-opus-4-7">Claude Opus 4.7</option>
              </select>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onCancel} disabled={loading}>Cancelar</Button>
          <Button onClick={() => onSave(form)} disabled={loading || !form.name.trim()}>
            {loading ? "Guardando…" : initial ? "Guardar cambios" : "Crear persona"}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Página principal ──────────────────────────────────────────────────────────

export default function PersonasPage() {
  const [personas, setPersonas] = useState<PersonaResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)
  const [editing, setEditing] = useState<PersonaResponse | null | "new">(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await apiGet<PersonaListResponse>("/v1/personas")
      setPersonas(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar personas")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  function buildPayload(form: PersonaFormData) {
    return {
      name: form.name,
      system_prompt: form.system_prompt,
      tone: form.tone,
      locale: form.locale,
      locale_secondary: form.locale_secondary
        .split(",")
        .map(s => s.trim())
        .filter(Boolean),
      auto_detect_locale: form.auto_detect_locale,
      timezone: form.timezone,
      model_id: form.model_id,
    }
  }

  async function handleCreate(form: PersonaFormData) {
    setFormLoading(true)
    setFormError(null)
    try {
      const created = await apiFetch<PersonaResponse>("/v1/personas", {
        method: "POST",
        body: JSON.stringify(buildPayload(form)),
      })
      setPersonas(prev => [created, ...prev])
      setEditing(null)
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Error al crear persona")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleUpdate(form: PersonaFormData) {
    if (!editing || editing === "new") return
    setFormLoading(true)
    setFormError(null)
    try {
      const updated = await apiFetch<PersonaResponse>(`/v1/personas/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify(buildPayload(form)),
      })
      setPersonas(prev => prev.map(p => p.id === updated.id ? updated : p))
      setEditing(null)
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Error al actualizar persona")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("¿Eliminar esta persona? Esta acción no se puede deshacer.")) return
    setDeleting(id)
    try {
      await apiFetch(`/v1/personas/${id}`, { method: "DELETE" })
      setPersonas(prev => prev.filter(p => p.id !== id))
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
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Personas IA</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Configura la personalidad y comportamiento del agente</p>
        </div>
        <Button onClick={() => { setEditing("new"); setFormError(null) }} className="gap-2">
          <Plus className="w-4 h-4" />
          Nueva persona
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-4">
                <div className="animate-pulse space-y-2">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-1/3" />
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-2/3" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : personas.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-zinc-400 gap-3">
          <Bot className="w-12 h-12" />
          <p className="text-sm">No hay personas configuradas.</p>
          <Button variant="outline" onClick={() => setEditing("new")} className="gap-2">
            <Plus className="w-4 h-4" />
            Crear la primera
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {personas.map(p => (
            <Card key={p.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <Bot className="w-4 h-4 text-zinc-400 shrink-0" />
                    <CardTitle className="text-base">{p.name}</CardTitle>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => { setEditing(p); setFormError(null) }}
                      className="gap-1.5 text-zinc-500"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(p.id)}
                      disabled={deleting === p.id}
                      className="text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex flex-wrap gap-2">
                  <span className="text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 px-2 py-0.5 rounded-full">
                    Tono: {p.tone}
                  </span>
                  <span className="text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 px-2 py-0.5 rounded-full">
                    {p.locale}
                  </span>
                  {p.auto_detect_locale && (
                    <span className="text-xs bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 px-2 py-0.5 rounded-full">
                      Auto-idioma
                    </span>
                  )}
                  {p.locale_secondary.length > 0 && (
                    <span className="text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-500 px-2 py-0.5 rounded-full">
                      +{p.locale_secondary.join(", ")}
                    </span>
                  )}
                  <span className="text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-500 px-2 py-0.5 rounded-full font-mono">
                    {p.model_id}
                  </span>
                </div>
                {p.system_prompt && (
                  <p className="text-xs text-zinc-500 line-clamp-2">{p.system_prompt}</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {editing === "new" && (
        <PersonaForm
          onSave={handleCreate}
          onCancel={() => setEditing(null)}
          loading={formLoading}
          error={formError}
        />
      )}
      {editing && editing !== "new" && (
        <PersonaForm
          initial={editing}
          onSave={handleUpdate}
          onCancel={() => setEditing(null)}
          loading={formLoading}
          error={formError}
        />
      )}
    </div>
  )
}
