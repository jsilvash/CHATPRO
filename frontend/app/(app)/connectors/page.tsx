"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { apiFetch, apiGet } from "@/lib/api"
import type { ConnectorConfigOut, ConnectorDefOut } from "@/lib/types"
import { EmptyState } from "@/components/EmptyState"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useToast } from "@/hooks/use-toast"
import {
  Plus,
  Plug,
  Trash2,
  Settings,
  AlertCircle,
  CheckCircle2,
  Clock,
  XCircle,
} from "lucide-react"

function statusBadge(status: string) {
  const map: Record<string, { label: string; color: string; icon: React.ElementType }> = {
    connected: { label: "Conectado", color: "text-green-700 bg-green-100 dark:text-green-400 dark:bg-green-900/30", icon: CheckCircle2 },
    pending: { label: "Pendiente", color: "text-yellow-700 bg-yellow-100 dark:text-yellow-400 dark:bg-yellow-900/30", icon: Clock },
    error: { label: "Error", color: "text-red-700 bg-red-100 dark:text-red-400 dark:bg-red-900/30", icon: XCircle },
    disabled: { label: "Desactivado", color: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800", icon: XCircle },
  }
  const s = map[status] ?? { label: status, color: "text-zinc-500 bg-zinc-100", icon: AlertCircle }
  const Icon = s.icon
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium", s.color)}>
      <Icon className="w-3 h-3" />
      {s.label}
    </span>
  )
}

function connectorLabel(name: string | null) {
  if (name === "woocommerce") return "WooCommerce"
  if (name === "shopify") return "Shopify"
  return name ?? "Desconocido"
}

// ── Modal crear conector ──────────────────────────────────────────────────────

interface CreateModalProps {
  defs: ConnectorDefOut[]
  onClose: () => void
  onCreated: (c: ConnectorConfigOut) => void
}

function CreateModal({ defs, onClose, onCreated }: CreateModalProps) {
  const [step, setStep] = useState<"choose" | "form">("choose")
  const [chosen, setChosen] = useState<ConnectorDefOut | null>(null)
  const [displayName, setDisplayName] = useState("")
  const [creds, setCreds] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const wooFields = [
    { key: "site_url", label: "URL del sitio", placeholder: "https://mitienda.com" },
    { key: "consumer_key", label: "Consumer Key", placeholder: "ck_..." },
    { key: "consumer_secret", label: "Consumer Secret", placeholder: "cs_..." },
  ]
  const shopifyFields = [
    { key: "shop_domain", label: "Dominio Shopify", placeholder: "mitienda.myshopify.com" },
    { key: "access_token", label: "Access Token", placeholder: "shpat_..." },
    { key: "api_secret", label: "API Secret", placeholder: "" },
  ]

  const fields = chosen?.name === "woocommerce" ? wooFields : shopifyFields

  async function handleSubmit() {
    if (!chosen) return
    setLoading(true)
    setError(null)
    try {
      const config = await apiFetch<ConnectorConfigOut>("/v1/connector-configs", {
        method: "POST",
        body: JSON.stringify({ connector_name: chosen.name, display_name: displayName || connectorLabel(chosen.name) }),
      })
      // configurar credenciales
      const configured = await apiFetch<ConnectorConfigOut>(`/v1/connector-configs/${config.id}/configure`, {
        method: "POST",
        body: JSON.stringify({ credentials: creds }),
      })
      onCreated(configured)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al crear conector")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          {step === "choose" ? "Elegir conector" : `Configurar ${connectorLabel(chosen?.name ?? null)}`}
        </h2>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        {step === "choose" ? (
          <div className="grid grid-cols-2 gap-3">
            {defs.filter(d => d.enabled).map(def => (
              <button
                key={def.id}
                onClick={() => { setChosen(def); setStep("form") }}
                className="p-4 rounded-lg border-2 border-zinc-200 dark:border-zinc-700 hover:border-blue-400 dark:hover:border-blue-500 text-left transition-colors"
              >
                <p className="font-medium text-zinc-900 dark:text-zinc-50">{connectorLabel(def.name)}</p>
                <p className="text-xs text-zinc-500 mt-0.5">{def.kind}</p>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <Label htmlFor="display_name">Nombre</Label>
              <Input
                id="display_name"
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                placeholder={connectorLabel(chosen?.name ?? null)}
                className="mt-1"
              />
            </div>
            {fields.map(f => (
              <div key={f.key}>
                <Label htmlFor={f.key}>{f.label}</Label>
                <Input
                  id={f.key}
                  value={creds[f.key] ?? ""}
                  onChange={e => setCreds(prev => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  className="mt-1"
                />
              </div>
            ))}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={loading}>Cancelar</Button>
          {step === "form" && (
            <Button onClick={handleSubmit} disabled={loading}>
              {loading ? "Creando…" : "Crear conector"}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Página principal ──────────────────────────────────────────────────────────

export default function ConnectorsPage() {
  const router = useRouter()
  const { success, error: toastError } = useToast()
  const [configs, setConfigs] = useState<ConnectorConfigOut[]>([])
  const [defs, setDefs] = useState<ConnectorDefOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [cfgs, dfs] = await Promise.all([
        apiGet<ConnectorConfigOut[]>("/v1/connector-configs"),
        apiGet<ConnectorDefOut[]>("/v1/connectors"),
      ])
      setConfigs(cfgs)
      setDefs(dfs)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar conectores")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleDelete(id: string) {
    if (!confirm("¿Eliminar este conector? Se perderán los datos sincronizados.")) return
    setDeleting(id)
    try {
      await apiFetch(`/v1/connector-configs/${id}`, { method: "DELETE" })
      setConfigs(prev => prev.filter(c => c.id !== id))
      success("Conector eliminado")
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Error al eliminar")
    } finally {
      setDeleting(null)
    }
  }

  function handleCreated(c: ConnectorConfigOut) {
    setConfigs(prev => [...prev, c])
    setShowCreate(false)
    success("Conector creado correctamente")
    router.push(`/connectors/${c.id}`)
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Conectores</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Integraciones con plataformas de e-commerce</p>
        </div>
        <Button onClick={() => setShowCreate(true)} className="gap-2">
          <Plus className="w-4 h-4" />
          Agregar conector
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-6">
                <div className="animate-pulse space-y-2">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-3/4" />
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-1/2" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : configs.length === 0 ? (
        <EmptyState
          icon={Plug}
          title="Sin conectores"
          description="Conecta tu tienda WooCommerce o Shopify para que el agente pueda responder sobre productos y pedidos."
          action={
            <Button variant="outline" onClick={() => setShowCreate(true)} className="gap-2">
              <Plus className="w-4 h-4" />
              Agregar conector
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {configs.map(c => (
            <Card key={c.id} className="relative group">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="text-base truncate">{c.display_name}</CardTitle>
                    <p className="text-xs text-zinc-500 mt-0.5">{connectorLabel(c.connector_name)}</p>
                  </div>
                  {statusBadge(c.status)}
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {c.last_error && (
                  <p className="text-xs text-red-600 dark:text-red-400 truncate" title={c.last_error}>
                    {c.last_error}
                  </p>
                )}
                <p className="text-xs text-zinc-400">
                  Actualizado: {c.last_full_sync_at
                    ? new Date(c.last_full_sync_at).toLocaleDateString("es", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
                    : "Nunca"}
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1 gap-1.5"
                    onClick={() => router.push(`/connectors/${c.id}`)}
                  >
                    <Settings className="w-3.5 h-3.5" />
                    Gestionar
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-900/20"
                    onClick={() => handleDelete(c.id)}
                    disabled={deleting === c.id}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateModal
          defs={defs}
          onClose={() => setShowCreate(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  )
}
