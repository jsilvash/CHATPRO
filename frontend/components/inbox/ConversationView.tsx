"use client"

import { useEffect, useRef, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { UserCheck, X, Tag, Plus, Trash2, FileText } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import { MessageBubble } from "./MessageBubble"
import { ReplyBox } from "./ReplyBox"
import { apiFetch, apiGet, API_URL } from "@/lib/api"
import { useConversationSocket } from "@/hooks/use-conversation-socket"
import { formatDateTime } from "@/lib/date"
import type { ConversationDetail, MessageOut, NoteOut } from "@/lib/types"

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "success" | "warning" | "info"> = {
  bot: "secondary",
  agent: "success",
  waiting_agent: "warning",
  closed: "info",
}

const STATUS_LABELS: Record<string, string> = {
  bot: "Bot",
  agent: "Agente",
  waiting_agent: "Esperando",
  closed: "Cerrado",
}

interface ConversationViewProps {
  convId: string
}

export function ConversationView({ convId }: ConversationViewProps) {
  const qc = useQueryClient()
  const router = useRouter()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [localMessages, setLocalMessages] = useState<MessageOut[]>([])
  const [newTag, setNewTag] = useState("")
  const [newNote, setNewNote] = useState("")
  const [addingTag, setAddingTag] = useState(false)
  const [addingNote, setAddingNote] = useState(false)

  // Carga detalle de la conversación
  const { data, isLoading, error } = useQuery<ConversationDetail>({
    queryKey: ["conversation", convId],
    queryFn: () => apiGet<ConversationDetail>(`/v1/inbox/${convId}`),
    refetchInterval: 60_000,
  })

  // Carga notas
  const { data: notes } = useQuery<NoteOut[]>({
    queryKey: ["conversation-notes", convId],
    queryFn: () => apiGet<NoteOut[]>(`/v1/inbox/${convId}/notes`),
  })

  // Sincroniza mensajes iniciales
  useEffect(() => {
    if (data?.messages) {
      setLocalMessages(data.messages)
    }
  }, [data?.messages])

  // Scroll al último mensaje
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [localMessages])

  // WebSocket: nuevos mensajes en tiempo real
  useConversationSocket({
    convId,
    onMessage: (msg) => {
      if (msg.event === "message" && msg.message_id && msg.text) {
        setLocalMessages((prev) => {
          if (prev.find((m) => m.id === msg.message_id)) return prev
          const newMsg: MessageOut = {
            id: msg.message_id!,
            direction: (msg.direction as "in" | "out") ?? "in",
            text: msg.text!,
            ack: "sent",
            sent_at: msg.sent_at ?? new Date().toISOString(),
            created_at: msg.sent_at ?? new Date().toISOString(),
          }
          return [...prev, newMsg]
        })
      }
    },
  })

  // Mutaciones
  const takeMut = useMutation({
    mutationFn: () => apiFetch(`/v1/inbox/${convId}/take`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversation", convId] }),
  })

  const closeMut = useMutation({
    mutationFn: () => apiFetch(`/v1/inbox/${convId}/close`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", convId] })
      qc.invalidateQueries({ queryKey: ["inbox"] })
    },
  })

  const replyMut = useMutation({
    mutationFn: (text: string) =>
      apiFetch<{ message_id: string; wa_message_id: string; success: boolean }>(
        `/v1/inbox/${convId}/reply`,
        { method: "POST", body: JSON.stringify({ text }) },
      ),
  })

  const addTagMut = useMutation({
    mutationFn: (tag: string) =>
      apiFetch(`/v1/inbox/${convId}/tags`, {
        method: "POST",
        body: JSON.stringify({ tag }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation", convId] })
      setNewTag("")
      setAddingTag(false)
    },
  })

  const removeTagMut = useMutation({
    mutationFn: (tag: string) =>
      apiFetch(`/v1/inbox/${convId}/tags/${encodeURIComponent(tag)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversation", convId] }),
  })

  const addNoteMut = useMutation({
    mutationFn: (text: string) =>
      apiFetch(`/v1/inbox/${convId}/notes`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversation-notes", convId] })
      setNewNote("")
      setAddingNote(false)
    },
  })

  const deleteNoteMut = useMutation({
    mutationFn: (noteId: string) =>
      apiFetch(`/v1/inbox/${convId}/notes/${noteId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversation-notes", convId] }),
  })

  async function handleSend(text: string) {
    const result = await replyMut.mutateAsync(text)
    if (result?.success !== false) {
      // El WS debería recibir el mensaje, pero como fallback lo agregamos localmente
    }
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-400 text-sm">
        Cargando conversación...
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-red-400 text-sm">
        No se pudo cargar la conversación
      </div>
    )
  }

  const conv = data.conversation
  const canReply = conv.status === "agent" || conv.status === "waiting_agent"
  const canTake = conv.status === "waiting_agent"
  const canClose = conv.status !== "bot" && conv.status !== "closed"

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Panel central: mensajes */}
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Header */}
        <div className="px-4 py-3 border-b border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-sm text-zinc-900 dark:text-zinc-50 truncate">
                {conv.wa_contact_name || conv.wa_contact_phone}
              </h2>
              <Badge variant={STATUS_VARIANTS[conv.status] ?? "secondary"} className="text-xs shrink-0">
                {STATUS_LABELS[conv.status] ?? conv.status}
              </Badge>
            </div>
            <p className="text-xs text-zinc-400">{conv.wa_contact_phone}</p>
          </div>

          {/* Acciones */}
          <div className="flex items-center gap-2 shrink-0">
            {canTake && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => takeMut.mutate()}
                disabled={takeMut.isPending}
                className="text-xs h-7"
              >
                <UserCheck className="w-3.5 h-3.5 mr-1" />
                Tomar
              </Button>
            )}
            {canClose && (
              <Button
                size="sm"
                variant="destructive"
                onClick={() => closeMut.mutate()}
                disabled={closeMut.isPending}
                className="text-xs h-7"
              >
                <X className="w-3.5 h-3.5 mr-1" />
                Cerrar
              </Button>
            )}
          </div>
        </div>

        {/* Mensajes */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3 bg-zinc-50 dark:bg-zinc-950">
          {localMessages.length === 0 ? (
            <p className="text-xs text-zinc-400 text-center mt-8">Sin mensajes</p>
          ) : (
            localMessages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Reply box */}
        <ReplyBox onSend={handleSend} disabled={!canReply} />
      </div>

      {/* Panel derecho: info lateral */}
      <aside className="w-64 shrink-0 border-l border-zinc-200 dark:border-zinc-700 flex flex-col overflow-y-auto bg-white dark:bg-zinc-900">
        {/* Info conversación */}
        <div className="p-4 border-b border-zinc-100 dark:border-zinc-800">
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
            Información
          </h3>
          <div className="space-y-1 text-xs text-zinc-500">
            <p>
              <span className="font-medium text-zinc-700 dark:text-zinc-300">Turnos:</span>{" "}
              {conv.turn_count}
            </p>
            {conv.first_response_at && (
              <p>
                <span className="font-medium text-zinc-700 dark:text-zinc-300">1ª respuesta:</span>{" "}
                {formatDateTime(new Date(conv.first_response_at))}
              </p>
            )}
            {conv.ai_summary && (
              <div className="mt-2 p-2 bg-zinc-50 dark:bg-zinc-800 rounded text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
                {conv.ai_summary}
              </div>
            )}
          </div>
        </div>

        <Separator />

        {/* Tags */}
        <div className="p-4 border-b border-zinc-100 dark:border-zinc-800">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
              Etiquetas
            </h3>
            <button
              onClick={() => setAddingTag((v) => !v)}
              className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              <Tag className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="flex flex-wrap gap-1 mb-2">
            {conv.tags.map((tag) => (
              <span
                key={tag}
                className="flex items-center gap-1 text-xs bg-zinc-100 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300 px-2 py-0.5 rounded-full"
              >
                {tag}
                <button
                  onClick={() => removeTagMut.mutate(tag)}
                  className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
                >
                  <X className="w-2.5 h-2.5" />
                </button>
              </span>
            ))}
            {conv.tags.length === 0 && (
              <p className="text-xs text-zinc-400">Sin etiquetas</p>
            )}
          </div>

          {addingTag && (
            <div className="flex gap-1">
              <Input
                placeholder="nueva etiqueta"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newTag.trim()) addTagMut.mutate(newTag.trim())
                }}
                className="h-6 text-xs"
              />
              <Button
                size="icon"
                variant="ghost"
                onClick={() => newTag.trim() && addTagMut.mutate(newTag.trim())}
                className="h-6 w-6"
              >
                <Plus className="w-3 h-3" />
              </Button>
            </div>
          )}
        </div>

        <Separator />

        {/* Notas internas */}
        <div className="p-4 flex-1">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
              Notas internas
            </h3>
            <button
              onClick={() => setAddingNote((v) => !v)}
              className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              <FileText className="w-3.5 h-3.5" />
            </button>
          </div>

          {addingNote && (
            <div className="mb-3 space-y-1">
              <Textarea
                placeholder="Escribe una nota..."
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                className="text-xs min-h-[60px] resize-none"
              />
              <div className="flex justify-end gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => { setAddingNote(false); setNewNote("") }}
                  className="h-6 text-xs px-2"
                >
                  Cancelar
                </Button>
                <Button
                  size="sm"
                  onClick={() => newNote.trim() && addNoteMut.mutate(newNote.trim())}
                  disabled={!newNote.trim() || addNoteMut.isPending}
                  className="h-6 text-xs px-2"
                >
                  Guardar
                </Button>
              </div>
            </div>
          )}

          <div className="space-y-2">
            {!notes?.length && !addingNote && (
              <p className="text-xs text-zinc-400">Sin notas</p>
            )}
            {notes?.map((note) => (
              <div
                key={note.id}
                className="group relative text-xs bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 rounded p-2 text-zinc-700 dark:text-zinc-300"
              >
                <p className="pr-4 whitespace-pre-wrap">{note.text}</p>
                <p className="text-zinc-400 mt-1">{formatDateTime(new Date(note.created_at))}</p>
                <button
                  onClick={() => deleteNoteMut.mutate(note.id)}
                  className="absolute top-1.5 right-1.5 text-zinc-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  )
}
