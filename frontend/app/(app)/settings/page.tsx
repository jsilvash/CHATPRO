"use client"

import { useEffect, useState, useCallback } from "react"
import { apiGet, apiFetch } from "@/lib/api"
import type { TenantResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Settings,
  AlertCircle,
  CheckCircle2,
  Building2,
  Hash,
  Calendar,
  Shield,
  Loader2,
  TriangleAlert,
} from "lucide-react"
import { cn } from "@/lib/utils"

function planBadge(plan: string) {
  const colors: Record<string, string> = {
    free: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    starter: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    pro: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
    enterprise: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  }
  return (
    <span
      className={cn(
        "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold capitalize",
        colors[plan.toLowerCase()] ?? "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
      )}
    >
      {plan}
    </span>
  )
}

function InfoRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-zinc-100 dark:border-zinc-800 last:border-0">
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Icon className="w-4 h-4" />
        {label}
      </div>
      <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{value}</div>
    </div>
  )
}

export default function SettingsPage() {
  const [tenant, setTenant] = useState<TenantResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Edición de nombre
  const [editName, setEditName] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Danger zone
  const [deleteConfirm, setDeleteConfirm] = useState("")
  const [showDangerConfirm, setShowDangerConfirm] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const t = await apiGet<TenantResponse>("/v1/tenants/me")
      setTenant(t)
      setEditName(t.name)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar configuración")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleSaveName(e: React.FormEvent) {
    e.preventDefault()
    if (!editName.trim()) return
    setSaving(true)
    setSaveSuccess(false)
    setSaveError(null)
    try {
      const updated = await apiFetch<TenantResponse>("/v1/tenants/me", {
        method: "PATCH",
        body: JSON.stringify({ name: editName.trim() }),
      })
      setTenant(updated)
      setEditName(updated.name)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 4000)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Error al guardar")
    } finally {
      setSaving(false)
    }
  }

  const isDirty = tenant ? editName.trim() !== tenant.name : false

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Settings className="w-5 h-5 text-zinc-400" />
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Configuración</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Ajustes de tu cuenta y espacio de trabajo</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Información del tenant */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Información del espacio de trabajo</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-3 animate-pulse">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex justify-between py-3 border-b border-zinc-100 dark:border-zinc-800">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-1/4" />
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-1/3" />
                </div>
              ))}
            </div>
          ) : tenant && (
            <>
              <InfoRow icon={Hash} label="Slug" value={<span className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded">{tenant.slug}</span>} />
              <InfoRow icon={Shield} label="Plan" value={planBadge(tenant.plan)} />
              <InfoRow icon={Building2} label="Estado" value={
                tenant.is_active
                  ? <span className="text-green-600 dark:text-green-400 text-xs font-medium">Activo</span>
                  : <span className="text-red-600 dark:text-red-400 text-xs font-medium">Inactivo</span>
              } />
              <InfoRow icon={Calendar} label="Creado" value={
                new Date(tenant.created_at).toLocaleDateString("es", { day: "2-digit", month: "long", year: "numeric" })
              } />
            </>
          )}
        </CardContent>
      </Card>

      {/* Editar nombre */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Nombre del espacio de trabajo</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSaveName} className="space-y-3">
            <div>
              <Label htmlFor="tenant_name" className="text-xs">Nombre</Label>
              <Input
                id="tenant_name"
                value={editName}
                onChange={e => setEditName(e.target.value)}
                placeholder="Mi empresa"
                className="mt-1"
                maxLength={100}
                disabled={loading}
              />
              <p className="text-xs text-zinc-400 mt-1">
                El nombre que verás en tu panel y en las notificaciones.
              </p>
            </div>

            {saveError && (
              <div className="flex items-center gap-2 p-2 rounded bg-red-50 text-red-700 text-xs dark:bg-red-900/20 dark:text-red-400">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                {saveError}
              </div>
            )}
            {saveSuccess && (
              <div className="flex items-center gap-2 p-2 rounded bg-green-50 text-green-700 text-xs dark:bg-green-900/20 dark:text-green-400">
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                Nombre actualizado correctamente
              </div>
            )}

            <Button
              type="submit"
              size="sm"
              disabled={saving || !isDirty || loading}
            >
              {saving ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  Guardando…
                </>
              ) : "Guardar cambios"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Zona peligrosa */}
      <Card className="border-red-200 dark:border-red-900/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium text-red-700 dark:text-red-400 flex items-center gap-2">
            <TriangleAlert className="w-4 h-4" />
            Zona peligrosa
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/10 space-y-3">
            <div>
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">Eliminar espacio de trabajo</p>
              <p className="text-xs text-zinc-500 mt-0.5">
                Esta acción eliminará permanentemente tu cuenta, todos tus números de WhatsApp, conversaciones,
                contactos, conectores y datos asociados. Esta acción no se puede deshacer.
              </p>
            </div>

            {!showDangerConfirm ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setShowDangerConfirm(true)}
              >
                Eliminar espacio de trabajo
              </Button>
            ) : (
              <div className="space-y-2">
                <Label className="text-xs text-zinc-700 dark:text-zinc-300">
                  Escribe <span className="font-mono font-semibold">{tenant?.slug ?? "el-slug"}</span> para confirmar:
                </Label>
                <Input
                  value={deleteConfirm}
                  onChange={e => setDeleteConfirm(e.target.value)}
                  placeholder={tenant?.slug ?? "slug"}
                  className="h-8 text-sm"
                />
                <div className="flex gap-2">
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleteConfirm !== tenant?.slug}
                    onClick={() => {
                      // El backend no expone este endpoint aún — mostrar aviso
                      setSaveError("La eliminación de cuenta no está disponible en este plan. Contacta con soporte.")
                      setShowDangerConfirm(false)
                      setDeleteConfirm("")
                    }}
                  >
                    Confirmar eliminación
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { setShowDangerConfirm(false); setDeleteConfirm("") }}
                  >
                    Cancelar
  </Button>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
