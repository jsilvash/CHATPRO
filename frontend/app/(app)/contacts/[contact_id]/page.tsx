"use client"

import { useEffect, useState } from "react"
import { use } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { ContactOut, FactOut, ConversationListResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ArrowLeft, Phone, Tag, MessageSquare, Loader2, AlertCircle } from "lucide-react"

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
          {facts.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                <Tag className="w-3.5 h-3.5" />
                Datos conocidos
              </h2>
              <div className="flex flex-wrap gap-2">
                {facts.map((fact) => (
                  <div
                    key={fact.id}
                    className="flex items-center gap-1 px-3 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-sm"
                  >
                    <span className="font-medium text-zinc-600 dark:text-zinc-400">{fact.key}:</span>
                    <span className="text-zinc-800 dark:text-zinc-200">{fact.value_text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

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
