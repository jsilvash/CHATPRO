"use client"

import { useEffect, useState } from "react"
import { use } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { UserOut, UserStats } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  ArrowLeft,
  MessageSquare,
  Clock,
  StickyNote,
  Calendar,
  Loader2,
  AlertCircle,
} from "lucide-react"

function formatSeconds(seconds: number | null): string {
  if (seconds === null) return "—"
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Propietario",
  admin: "Administrador",
  agent: "Agente",
}

export default function UserDetailPage({ params }: { params: Promise<{ user_id: string }> }) {
  const { user_id } = use(params)
  const [user, setUser] = useState<UserOut | null>(null)
  const [stats, setStats] = useState<UserStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetch() {
      try {
        const [u, s] = await Promise.all([
          apiGet<UserOut>(`/v1/users/${user_id}`),
          apiGet<UserStats>(`/v1/users/${user_id}/stats`),
        ])
        setUser(u)
        setStats(s)
      } catch (e) {
        setError(e instanceof Error ? e.message : "Error al cargar usuario")
      } finally {
        setLoading(false)
      }
    }
    fetch()
  }, [user_id])

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/users">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Detalle de usuario</h1>
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
      ) : user ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{user.full_name || user.email}</span>
                <div className="flex gap-2">
                  <Badge variant={user.is_active ? "success" : "destructive"}>
                    {user.is_active ? "Activo" : "Inactivo"}
                  </Badge>
                  <Badge variant="secondary">{ROLE_LABELS[user.role] ?? user.role}</Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-700 dark:text-zinc-300 w-24">Email</span>
                <span>{user.email}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-700 dark:text-zinc-300 w-24">Creado</span>
                <span>{new Date(user.created_at).toLocaleDateString("es")}</span>
              </div>
            </CardContent>
          </Card>

          {stats && (
            <div>
              <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3">
                Estadísticas
              </h2>
              <div className="grid grid-cols-2 gap-4">
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <MessageSquare className="w-5 h-5 text-blue-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Conversaciones activas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.conversations_active}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <Calendar className="w-5 h-5 text-green-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Conversaciones hoy</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.conversations_today}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <Clock className="w-5 h-5 text-yellow-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Primera respuesta (promedio)</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {formatSeconds(stats.avg_first_response_sec)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <div className="flex items-center gap-3">
                      <StickyNote className="w-5 h-5 text-purple-500" />
                      <div>
                        <p className="text-xs text-zinc-500">Notas creadas</p>
                        <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                          {stats.notes_count}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
