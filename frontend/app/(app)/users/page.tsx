"use client"

import { useEffect, useState, useCallback } from "react"
import Link from "next/link"
import { apiGet, apiFetch } from "@/lib/api"
import type { UserOut, UserListResponse } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { UserCircle, ChevronRight, AlertTriangle, Loader2, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"

const ROLE_LABELS: Record<string, string> = {
  owner: "Propietario",
  admin: "Administrador",
  agent: "Agente",
}

const ROLE_VARIANT: Record<string, "default" | "info" | "secondary" | "warning"> = {
  owner: "default",
  admin: "info",
  agent: "secondary",
}

interface DeactivateModalProps {
  user: UserOut
  onConfirm: () => void
  onCancel: () => void
  loading: boolean
}

function DeactivateModal({ user, onConfirm, onCancel, loading }: DeactivateModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl p-6 max-w-sm w-full mx-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 rounded-full bg-red-100 dark:bg-red-900/30">
            <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
          </div>
          <h2 className="font-semibold text-zinc-900 dark:text-zinc-50">Desactivar usuario</h2>
        </div>
        <p className="text-sm text-zinc-600 dark:text-zinc-400 mb-6">
          ¿Desactivar a <span className="font-medium">{user.full_name || user.email}</span>? Sus conversaciones activas serán reasignadas automáticamente.
        </p>
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={loading}>
            Cancelar
          </Button>
          <Button variant="destructive" size="sm" onClick={onConfirm} disabled={loading}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Desactivar"}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function UsersPage() {
  const [users, setUsers] = useState<UserOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deactivatingUser, setDeactivatingUser] = useState<UserOut | null>(null)
  const [deactivateLoading, setDeactivateLoading] = useState(false)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const fetchUsers = useCallback(async () => {
    try {
      const data = await apiGet<UserListResponse>("/v1/users", { limit: 100 })
      setUsers(data.items)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar usuarios")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchUsers()
  }, [fetchUsers])

  async function handleDeactivate() {
    if (!deactivatingUser) return
    setDeactivateLoading(true)
    try {
      const res = await apiFetch<{ deactivated_user_id: string; reassigned_conversations: number }>(
        `/v1/users/${deactivatingUser.id}/deactivate`,
        { method: "PATCH" },
      )
      setSuccessMsg(
        `Usuario desactivado. ${res.reassigned_conversations} conversaciones reasignadas.`,
      )
      setDeactivatingUser(null)
      await fetchUsers()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al desactivar usuario")
    } finally {
      setDeactivateLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Usuarios</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Gestión de agentes y administradores</p>
        </div>
        <Link href="/users/agents">
          <Button variant="outline" size="sm">
            <ShieldCheck className="w-4 h-4" />
            Ver agentes disponibles
          </Button>
        </Link>
      </div>

      {successMsg && (
        <div className="p-3 rounded-lg bg-green-50 text-green-700 text-sm dark:bg-green-900/20 dark:text-green-400">
          {successMsg}
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
        </div>
      ) : (
        <div className="space-y-2">
          {users.map((user) => (
            <Card key={user.id} className={cn(!user.is_active && "opacity-60")}>
              <CardContent className="py-3 px-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-full bg-zinc-100 dark:bg-zinc-800">
                    <UserCircle className="w-5 h-5 text-zinc-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">
                      {user.full_name || user.email}
                    </p>
                    <p className="text-xs text-zinc-400 truncate">{user.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={ROLE_VARIANT[user.role] ?? "secondary"}>
                      {ROLE_LABELS[user.role] ?? user.role}
                    </Badge>
                    {!user.is_active && (
                      <Badge variant="destructive">Inactivo</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {user.is_active && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 text-xs h-7"
                        onClick={() => setDeactivatingUser(user)}
                      >
                        Desactivar
                      </Button>
                    )}
                    <Link href={`/users/${user.id}`}>
                      <Button variant="ghost" size="icon" className="h-7 w-7">
                        <ChevronRight className="w-4 h-4" />
                      </Button>
                    </Link>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
          {users.length === 0 && (
            <p className="text-center text-sm text-zinc-400 py-12">No hay usuarios registrados.</p>
          )}
        </div>
      )}

      {deactivatingUser && (
        <DeactivateModal
          user={deactivatingUser}
          onConfirm={handleDeactivate}
          onCancel={() => setDeactivatingUser(null)}
          loading={deactivateLoading}
        />
      )}
    </div>
  )
}
