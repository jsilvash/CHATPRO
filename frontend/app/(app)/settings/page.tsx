"use client"

import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Settings, User, Building2, Lock, Save, Loader2, Eye, EyeOff } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { apiGet, apiFetch } from "@/lib/api"
import { useToast } from "@/hooks/use-toast"
import { decodeJwt } from "jose"

interface TenantOut {
  id: string
  slug: string
  name: string
  plan: string
  is_active: boolean
  created_at: string
}

interface MeOut {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
}

function getJwt(): string | null {
  if (typeof document === "undefined") return null
  return (
    document.cookie
      .split("; ")
      .find((r) => r.startsWith("chatpro_access="))
      ?.split("=")[1] ?? null
  )
}

function getUserIdFromJwt(): string | null {
  try {
    const tok = getJwt()
    if (!tok) return null
    const payload = decodeJwt(tok) as { sub?: string }
    return payload.sub ?? null
  } catch {
    return null
  }
}

const PLAN_LABEL: Record<string, string> = {
  free: "Gratuito",
  starter: "Starter",
  pro: "Pro",
  enterprise: "Enterprise",
}

export default function SettingsPage() {
  const qc = useQueryClient()
  const { success, error: toastError } = useToast()

  const { data: tenant, isLoading: loadingTenant } = useQuery<TenantOut>({
    queryKey: ["tenant-me"],
    queryFn: () => apiGet<TenantOut>("/v1/tenants/me"),
  })

  const { data: me, isLoading: loadingMe } = useQuery<MeOut>({
    queryKey: ["me"],
    queryFn: () => apiGet<MeOut>("/v1/me"),
  })

  // Perfil usuario
  const [fullName, setFullName] = useState("")
  const [fullNameDirty, setFullNameDirty] = useState(false)

  // Contraseña
  const [pwCurrent, setPwCurrent] = useState("")
  const [pwNew, setPwNew] = useState("")
  const [pwConfirm, setPwConfirm] = useState("")
  const [showPw, setShowPw] = useState(false)

  // Init form values once me loads
  const initName = me?.full_name ?? ""
  const displayName = fullNameDirty ? fullName : initName

  const updateProfileMut = useMutation({
    mutationFn: async (name: string) => {
      const uid = getUserIdFromJwt()
      if (!uid) throw new Error("No se pudo obtener el ID de usuario")
      return apiFetch(`/v1/users/${uid}`, {
        method: "PATCH",
        body: JSON.stringify({ full_name: name }),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] })
      success("Perfil actualizado", "Tu nombre se guardó correctamente")
      setFullNameDirty(false)
    },
    onError: (e) => {
      toastError("Error al actualizar", e instanceof Error ? e.message : "Error desconocido")
    },
  })

  const changePasswordMut = useMutation({
    mutationFn: async () => {
      return apiFetch("/v1/me/password", {
        method: "PATCH",
        body: JSON.stringify({ current_password: pwCurrent, new_password: pwNew }),
      })
    },
    onSuccess: () => {
      success("Contraseña actualizada", "Tu contraseña se cambió correctamente")
      setPwCurrent("")
      setPwNew("")
      setPwConfirm("")
    },
    onError: (e) => {
      toastError("Error al cambiar contraseña", e instanceof Error ? e.message : "Error desconocido")
    },
  })

  function handleProfileSave() {
    const name = fullNameDirty ? fullName : initName
    if (!name.trim()) return
    updateProfileMut.mutate(name.trim())
  }

  function handlePasswordChange() {
    if (!pwCurrent || !pwNew) return
    if (pwNew !== pwConfirm) {
      toastError("Las contraseñas no coinciden", "Verifica que las contraseñas nuevas sean iguales")
      return
    }
    if (pwNew.length < 8) {
      toastError("Contraseña muy corta", "La contraseña debe tener al menos 8 caracteres")
      return
    }
    changePasswordMut.mutate()
  }

  if (loadingTenant || loadingMe) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
      </div>
    )
  }

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Settings className="w-6 h-6 text-zinc-600" />
        <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">Configuración</h1>
      </div>

      {/* Tenant info */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Building2 className="w-4 h-4 text-zinc-400" />
            Información de la empresa
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label className="text-xs text-zinc-500">Nombre</Label>
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
                {tenant?.name ?? "—"}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-zinc-500">Slug</Label>
              <p className="text-sm font-mono text-zinc-600 dark:text-zinc-400">
                {tenant?.slug ?? "—"}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-zinc-500">Plan</Label>
              <Badge variant="secondary" className="font-normal">
                {PLAN_LABEL[tenant?.plan ?? ""] ?? tenant?.plan ?? "—"}
              </Badge>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-zinc-500">Estado</Label>
              <Badge variant={tenant?.is_active ? "success" : "destructive"}>
                {tenant?.is_active ? "Activo" : "Inactivo"}
              </Badge>
            </div>
          </div>
          <p className="text-xs text-zinc-400">
            Para cambiar el nombre o plan de la empresa, contacta con soporte.
          </p>
        </CardContent>
      </Card>

      {/* Perfil de usuario */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <User className="w-4 h-4 text-zinc-400" />
            Perfil de usuario
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email" className="text-xs text-zinc-500">
              Email
            </Label>
            <Input id="email" value={me?.email ?? ""} disabled className="bg-zinc-50 dark:bg-zinc-800 text-sm" />
            <p className="text-xs text-zinc-400">El email no puede modificarse</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="fullName" className="text-xs text-zinc-500">
              Nombre completo
            </Label>
            <Input
              id="fullName"
              value={displayName}
              onChange={(e) => {
                setFullName(e.target.value)
                setFullNameDirty(true)
              }}
              placeholder="Tu nombre"
              className="text-sm"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-zinc-500">Rol</Label>
            <Badge variant="secondary" className="font-normal capitalize">
              {me?.role ?? "—"}
            </Badge>
          </div>

          <Button
            onClick={handleProfileSave}
            disabled={updateProfileMut.isPending || (!fullNameDirty)}
            size="sm"
            className="gap-2"
          >
            {updateProfileMut.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            Guardar perfil
          </Button>
        </CardContent>
      </Card>

      <Separator />

      {/* Cambio de contraseña */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Lock className="w-4 h-4 text-zinc-400" />
            Cambiar contraseña
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="pwCurrent" className="text-xs text-zinc-500">
              Contraseña actual
            </Label>
            <div className="relative">
              <Input
                id="pwCurrent"
                type={showPw ? "text" : "password"}
                value={pwCurrent}
                onChange={(e) => setPwCurrent(e.target.value)}
                placeholder="••••••••"
                className="pr-10 text-sm"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
              >
                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pwNew" className="text-xs text-zinc-500">
              Nueva contraseña
            </Label>
            <Input
              id="pwNew"
              type={showPw ? "text" : "password"}
              value={pwNew}
              onChange={(e) => setPwNew(e.target.value)}
              placeholder="Mín. 8 caracteres"
              className="text-sm"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pwConfirm" className="text-xs text-zinc-500">
              Confirmar nueva contraseña
            </Label>
            <Input
              id="pwConfirm"
              type={showPw ? "text" : "password"}
              value={pwConfirm}
              onChange={(e) => setPwConfirm(e.target.value)}
              placeholder="Repite la nueva contraseña"
              className="text-sm"
            />
          </div>

          <Button
            onClick={handlePasswordChange}
            disabled={changePasswordMut.isPending || !pwCurrent || !pwNew || !pwConfirm}
            size="sm"
            variant="outline"
            className="gap-2"
          >
            {changePasswordMut.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Lock className="w-3.5 h-3.5" />
            )}
            Cambiar contraseña
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
