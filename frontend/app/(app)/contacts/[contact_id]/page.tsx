"use client"

import { useEffect, useState } from "react"
import { use } from "react"
import Link from "next/link"
import { apiGet, apiFetch } from "@/lib/api"
import type { ContactOut, FactOut, ConversationListResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ArrowLeft, Phone, Tag, MessageSquare, Loader2, AlertCircle,
  Plus, Pencil, Trash2, Check, X,
} from "lucide-react"

const STATUS_LABELS: Record<string, string> = {
  bot: "Bot",
  agent: "Agente",
  waiting_agent: "Esperando agente",
  closed: "Cerrada",
}

const STATUS_VARIANT: Record<string, "secondary" | "info" | "warning" | "default"> = {
  bot: "secondary",
  agent: "info",
  waiting_agent: "warning",
  closed: "default",
}

const SOURCE_LABELS: Record<string, string> = {
  manual: "Manual",
  extraction: "IA",
  webhook: "Webhook",
}

interface EditingFact {
  key: string
  value: string
  isNew?: boolean
}

export default function ContactDetailPage({
  params,
}: {
  params: Promise<{ contact_id: string }>
}) {
  const { contact_id } = use(params)
  const [contact, setContact] = useState<ContactOut | null>(null)
  const [facts, setFacts] = useState<FactOut[]>([])
  const [conversations, setConversations] = useState<ConversationListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Fact editing state
  const [editing, setEditing] = useState<EditingFact | null>(null)
  const [saving, setSaving] = useState(false)
  const [factError, setFactError] = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [showNewFact, setShowNewFact] = useState(false)
  const [newKey, setNewKey] = useState("")
  const [newValue, setNewValue] = useState("")

  useEffect(() => {
    async function load() {
      try {
        const [c, f, convs] = await Promise.all([
          apiGet<ContactOut>(`/v1/contacts/${contact_id}`),
          apiGet<FactOut[]>(`/v1/contacts/${contact_id}/facts`),
          apiGet<ConversationListResponse>("/v1/inbox", { page: 1, page_size: 50 }),
        ])
        setContact(c)
        setFacts(f)
        setConversations(convs)
      } catch (e) {
        setError(e instanceof Error ? e.message : "Error al cargar contacto")
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [contact_id])

  const contactConvs = conversations?.items.filter(
    (c) => c.wa_contact_phone === contact?.phone_e164,
  ) ?? []

  async function saveFact() {
    if (!editing) return
    setSaving(true)
    setFactError(null)
    try {
      const updated = await apiFetch<FactOut>(`/v1/contacts/${contact_id}/facts/${editing.key}`, {
        method: "PUT",
        body: JSON.stringify({ value_text: editing.value }),
      })
      setFacts((prev) => {
        const idx = prev.findIndex((f) => f.key === editing.key)
        if (idx >= 0) {
          const next = [...prev]
          next[idx] = updated
          return next
        }
        return [...prev, updated]
      })
      setEditing(null)
    } catch (e) {
      setFactError(e instanceof Error ? e.message : "Error al guardar")
    } finally {
      setSaving(false)
    }
  }

  async function addFact() {
    if (!newKey.trim() || !newValue.trim()) return
    setSaving(true)
    setFactError(null)
    try {
      const created = await apiFetch<FactOut>(`/v1/contacts/${contact_id}/facts/${newKey.trim().toLowerCase()}`, {
        method: "PUT",
        body: JSON.stringify({ value_text: newValue.trim() }),
      })
      setFacts((prev) => {
        const idx = prev.findIndex((f) => f.key === created.key)
        if (idx >= 0) {
          const next = [...prev]
          next[idx] = created
          return next
        }
        return [...prev, created]
      })
      setShowNewFact(false)
      setNewKey("")
      setNewValue("")
    } catch (e) {
      setFactError(e instanceof Error ? e.message : "Error al crear hecho")
    } finally {
      setSaving(false)
    }
  }

  async function deleteFact(key: string) {
    setDeleting(true)
    try {
      await apiFetch(`/v1/contacts/${contact_id}/facts/${key}`, { method: "DELETE" })
      setFacts((prev) => prev.filter((f) => f.key !== key))
      setDeleteConfirm(null)
    } catch (e) {
      setFactError(e instanceof Error ? e.message : "Error al eliminar")
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/contacts">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Detalle de contacto</h1>
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
      ) : contact ? (
        <>
          {/* Info principal */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Phone className="w-4 h-4 text-blue-500" />
                {contact.display_name || contact.phone_e164}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
              <div className="flex gap-2">
                <span className="font-medium text-zinc-700 dark:text-zinc-300 w-28">Teléfono</span>
                <span>{contact.phone_e164}</span>
              </div>
              {contact.first_name && (
                <div className="flex gap-2">
                  <span className="font-medium text-zinc-700 dark:text-zinc-300 w-28">Nombre</span>
                  <span>{[contact.first_name, contact.last_name].filter(Boolean).join(" ")}</span>
                </div>
              )}
              {contact.email && (
                <div className="flex gap-2">
                  <span className="font-medium text-zinc-700 dark:text-zinc-300 w-28">Email</span>
                  <span>{contact.email}</span>
                </div>
              )}
              {contact.locale && (
                <div className="flex gap-2">
                  <span className="font-medium text-zinc-700 dark:text-zinc-300 w-28">Locale</span>
                  <span>{contact.locale}</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Hechos del contacto */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider flex items-center gap-2">
                <Tag className="w-3.5 h-3.5" />
                Datos conocidos
              </h2>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setShowNewFact(true); setFactError(null) }}
                className="h-6 text-xs px-2"
              >
                <Plus className="w-3 h-3 mr-1" />
                Añadir
              </Button>
            </div>

            {factError && (
              <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded p-2 mb-2">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                {factError}
              </div>
            )}

            {/* Formulario nuevo hecho */}
            {showNewFact && (
              <div className="flex gap-2 mb-3 items-end">
                <div className="flex-1">
                  <label className="text-xs text-zinc-400 mb-0.5 block">Clave</label>
                  <Input
                    placeholder="ej: talla_calzado"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    className="h-7 text-xs"
                  />
                </div>
                <div className="flex-1">
                  <label className="text-xs text-zinc-400 mb-0.5 block">Valor</label>
                  <Input
                    placeholder="ej: 42"
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addFact()}
                    className="h-7 text-xs"
                  />
                </div>
                <Button size="sm" onClick={addFact} disabled={saving || !newKey.trim() || !newValue.trim()} className="h-7 text-xs px-2">
                  {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => { setShowNewFact(false); setNewKey(""); setNewValue("") }} className="h-7 w-7 p-0">
                  <X className="w-3 h-3" />
                </Button>
              </div>
            )}

            {facts.length === 0 && !showNewFact ? (
              <p className="text-xs text-zinc-400">Sin datos conocidos.</p>
            ) : (
              <div className="space-y-1.5">
                {facts.map((fact) => (
                  <div
                    key={fact.id}
                    className="group flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-100 dark:border-zinc-700"
                  >
                    {editing?.key === fact.key ? (
                      <>
                        <span className="text-xs font-medium text-zinc-500 w-28 shrink-0">{fact.key}</span>
                        <Input
                          value={editing.value}
                          onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                          onKeyDown={(e) => e.key === "Enter" && saveFact()}
                          className="h-6 text-xs flex-1"
                          autoFocus
                        />
                        <Button size="sm" onClick={saveFact} disabled={saving} className="h-6 w-6 p-0">
                          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)} className="h-6 w-6 p-0">
                          <X className="w-3 h-3" />
                        </Button>
                      </>
                    ) : deleteConfirm === fact.key ? (
                      <>
                        <span className="text-xs text-zinc-500 flex-1">¿Eliminar <strong>{fact.key}</strong>?</span>
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => deleteFact(fact.key)}
                          disabled={deleting}
                          className="h-6 text-xs px-2"
                        >
                          {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : "Sí"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDeleteConfirm(null)} className="h-6 w-6 p-0">
                          <X className="w-3 h-3" />
                        </Button>
                      </>
                    ) : (
                      <>
                        <span className="text-xs font-medium text-zinc-500 w-28 shrink-0">{fact.key}</span>
                        <span className="text-xs text-zinc-800 dark:text-zinc-200 flex-1">{fact.value_text}</span>
                        <div className="flex items-center gap-1">
                          <Badge variant="secondary" className="text-xs px-1.5 py-0 font-normal">
                            {SOURCE_LABELS[fact.source] ?? fact.source}
                          </Badge>
                          {fact.confidence != null && (
                            <span className="text-xs text-zinc-400">{Math.round(fact.confidence * 100)}%</span>
                          )}
                        </div>
                        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setEditing({ key: fact.key, value: fact.value_text ?? "" })}
                            className="h-5 w-5 p-0 text-zinc-400 hover:text-zinc-700"
                          >
                            <Pencil className="w-3 h-3" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setDeleteConfirm(fact.key)}
                            className="h-5 w-5 p-0 text-zinc-400 hover:text-red-500"
                          >
                            <Trash2 className="w-3 h-3" />
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Conversaciones */}
          <div>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
              <MessageSquare className="w-3.5 h-3.5" />
              Conversaciones ({contactConvs.length})
            </h2>
            {contactConvs.length > 0 ? (
              <div className="space-y-2">
                {contactConvs.slice(0, 10).map((conv) => (
                  <Link key={conv.id} href={`/inbox/${conv.id}`}>
                    <Card className="hover:border-zinc-300 dark:hover:border-zinc-600 transition-colors cursor-pointer">
                      <CardContent className="py-2.5 px-4">
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm text-zinc-700 dark:text-zinc-300">
                              {conv.turn_count} turnos
                            </p>
                            <p className="text-xs text-zinc-400">
                              {new Date(conv.last_message_at ?? conv.created_at).toLocaleDateString("es")}
                            </p>
                          </div>
                          <Badge variant={STATUS_VARIANT[conv.status] ?? "secondary"}>
                            {STATUS_LABELS[conv.status] ?? conv.status}
                          </Badge>
                        </div>
                      </CardContent>
                    </Card>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="text-sm text-zinc-400 py-4 text-center">
                No se encontraron conversaciones para este contacto.
              </p>
            )}
          </div>
        </>
      ) : null}
    </div>
  )
}
