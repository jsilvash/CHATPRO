"use client"

import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Webhook, Plus, Trash2, Loader2, AlertCircle, X, ToggleLeft, ToggleRight, Copy, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/EmptyState"
import { apiFetch, apiGet } from "@/lib/api"
import { formatDateTime } from "@/lib/date"
import type { WebhookOutItem } from "@/lib/types"

const ALL_EVENTS = [
  "message.received",
  "message.sent",
  "conversation.escalated",
  "conversation.closed",
]

const EVENT_LABELS: Record<string, string> = {
  "message.received": "Mensaje recibido",
  "message.sent": "Mensaje enviado",
  "conversation.escalated": "Conv. escalada",
  "conversation.closed": "Conv. cerrada",
}

export default function WebhooksPage() {
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [webhookUrl, setWebhookUrl] = useState("")
  const [events, setEvents] = useState<string[]>(["message.received"])
  const [secret, setSecret] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [copiedSecret, setCopiedSecret] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  const { data: hooks, isLoading } = useQuery<WebhookOutItem[]>({
    queryKey: ["webhooks"],
    queryFn: () => apiGet<WebhookOutItem[]>("/v1/webhooks"),
  })

  const createMut = useMutation({
    mutationFn: () =>
      apiFetch<WebhookOutItem & { secret: string }>("/v1/webhooks", {
        method: "POST",
        body: JSON.stringify({
          url: webhookUrl.trim(),
          events,
          enabled: true,
          secret: secret.trim() || undefined,
        }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["webhooks"] })
      if (data.secret) setCreatedSecret(data.secret)
      setShowCreate(false)
      setWebhookUrl("")
      setEvents(["message.received"])
      setSecret("")
      setError(null)
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Error al crear webhook"),
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiFetch(`/v1/webhooks/${id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/webhooks/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webhooks"] })
      setDeleteConfirm(null)
    },
  })

  function toggleEvent(e: string) {
    setEvents((prev) =>
      prev.includes(e) ? prev.filter((x) => x !== e) : [...prev, e]
    )
  }

  async function copySecret() {
    if (!createdSecret) return
    await navigator.clipboard.writeText(createdSecret)
    setCopiedSecret(true)
    setTimeout(() => setCopiedSecret(false), 2000)
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Webhook className="w-5 h-5 text-zinc-500" />
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Webhooks salientes</h1>
        </div>
        <Button size="sm" onClick={() => { setShowCreate(true); setCreatedSecret(null) }}>
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          Nuevo webhook
        </Button>
      </div>

      {/* Secreto creado */}
      {createdSecret && (
        <div className="rounded-lg border border-green-300 bg-green-50 dark:bg-green-950/30 dark:border-green-800 p-4 space-y-2">
          <p className="text-sm font-semibold text-green-800 dark:text-green-400">
            Webhook creado — secreto HMAC:
          </p>
          <p className="text-xs text-green-700 dark:text-green-500">
            Guarda este secreto ahora — no se mostrará de nuevo.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-white dark:bg-zinc-900 border border-green-200 dark:border-green-800 rounded px-2 py-1.5 font-mono text-zinc-700 dark:text-zinc-300 truncate">
              {createdSecret}
            </code>
            <Button size="sm" variant="outline" onClick={copySecret} className="h-7 text-xs shrink-0">
              {copiedSecret ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
            </Button>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setCreatedSecret(null)} className="h-6 text-xs">
            Cerrar
          </Button>
        </div>
      )}

      {/* Formulario de creación */}
      {showCreate && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Nuevo webhook</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <label className="text-xs text-zinc-500 mb-1 block">URL destino</label>
              <Input
                placeholder="https://tu-servidor.com/webhook"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
                className="h-8 text-sm"
              />
            </div>

            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Eventos</label>
              <div className="flex flex-wrap gap-2">
                {ALL_EVENTS.map((ev) => (
                  <button
                    key={ev}
                    onClick={() => toggleEvent(ev)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                      events.includes(ev)
                        ? "bg-zinc-900 text-white border-zinc-900 dark:bg-zinc-100 dark:text-zinc-900"
                        : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700"
                    }`}
                  >
                    {EVENT_LABELS[ev] ?? ev}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-xs text-zinc-500 mb-1 block">
                Secreto HMAC <span className="text-zinc-400">(opcional, se auto-genera si se omite)</span>
              </label>
              <Input
                placeholder="mi-secreto-seguro"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                className="h-8 text-sm font-mono"
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded p-2">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="ghost" onClick={() => { setShowCreate(false); setError(null) }} className="h-7 text-xs">
                Cancelar
              </Button>
              <Button
                size="sm"
                onClick={() => createMut.mutate()}
                disabled={!webhookUrl.trim() || events.length === 0 || createMut.isPending}
                className="h-7 text-xs"
              >
                {createMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Crear"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Lista */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-zinc-400">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Cargando...
        </div>
      ) : !hooks?.length ? (
        <EmptyState
          icon={Webhook}
          title="Sin webhooks"
          description="Configura un webhook para recibir notificaciones cuando ocurran eventos en ChatPro."
        />
      ) : (
        <div className="space-y-3">
          {hooks.map((hook) => (
            <Card key={hook.id}>
              <CardContent className="py-3 px-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-mono text-zinc-700 dark:text-zinc-300 truncate">
                        {hook.url}
                      </p>
                      <Badge variant={hook.enabled ? "success" : "secondary"} className="text-xs shrink-0">
                        {hook.enabled ? "Activo" : "Pausado"}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {hook.events.map((ev) => (
                        <Badge key={ev} variant="secondary" className="text-xs px-1.5 py-0">
                          {EVENT_LABELS[ev] ?? ev}
                        </Badge>
                      ))}
                    </div>
                    <p className="text-xs text-zinc-400">
                      Creado {formatDateTime(new Date(hook.created_at))}
                      {hook.consecutive_failures > 0 && (
                        <span className="text-red-500 ml-2">· {hook.consecutive_failures} fallos consecutivos</span>
                      )}
                      {hook.last_success_at && (
                        <span className="ml-2">· Último éxito {formatDateTime(new Date(hook.last_success_at))}</span>
                      )}
                    </p>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => toggleMut.mutate({ id: hook.id, enabled: !hook.enabled })}
                      disabled={toggleMut.isPending}
                      className="h-7 w-7 p-0 text-zinc-400 hover:text-zinc-700"
                      title={hook.enabled ? "Pausar" : "Activar"}
                    >
                      {hook.enabled
                        ? <ToggleRight className="w-4 h-4 text-green-500" />
                        : <ToggleLeft className="w-4 h-4" />
                      }
                    </Button>

                    {deleteConfirm === hook.id ? (
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => deleteMut.mutate(hook.id)}
                          disabled={deleteMut.isPending}
                          className="h-6 text-xs px-2"
                        >
                          Eliminar
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleteConfirm(null)}
                          className="h-6 w-6 p-0"
                        >
                          <X className="w-3 h-3" />
                        </Button>
                      </div>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleteConfirm(hook.id)}
                        className="h-6 w-6 p-0 text-zinc-400 hover:text-red-500"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
