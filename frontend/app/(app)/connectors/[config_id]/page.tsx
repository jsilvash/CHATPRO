"use client"

import { use, useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { apiFetch, apiGet } from "@/lib/api"
import type { ConnectorConfigOut, ConnectorStatsOut, SearchResultOut, SearchResultsOut } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import {
  ArrowLeft,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Package,
  ShoppingCart,
  Search,
  ExternalLink,
  Loader2,
} from "lucide-react"

function connectorLabel(name: string | null) {
  if (name === "woocommerce") return "WooCommerce"
  if (name === "shopify") return "Shopify"
  return name ?? "Desconocido"
}

function fmt(val: string | null) {
  if (!val) return "Nunca"
  return new Date(val).toLocaleString("es", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  })
}

// ── Sección stats ─────────────────────────────────────────────────────────────

function StatsSection({ configId }: { configId: string }) {
  const [stats, setStats] = useState<ConnectorStatsOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const s = await apiGet<ConnectorStatsOut>(`/v1/connector-configs/${configId}/stats`)
      setStats(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar stats")
    } finally {
      setLoading(false)
    }
  }, [configId])

  useEffect(() => { load() }, [load])

  async function handleSync() {
    setSyncing(true)
    try {
      await apiFetch(`/v1/connector-configs/${configId}/sync`, { method: "POST" })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al sincronizar")
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return (
    <div className="grid grid-cols-2 gap-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Card key={i}><CardContent className="pt-6 animate-pulse"><div className="h-8 bg-zinc-200 dark:bg-zinc-700 rounded" /></CardContent></Card>
      ))}
    </div>
  )

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Card>
              <CardContent className="pt-6 flex items-center gap-3">
                <div className="p-2 rounded-lg bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                  <Package className="w-5 h-5" />
                </div>
                <div>
                  <p className="text-xs text-zinc-500">Productos</p>
                  <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{stats.products_count.toLocaleString()}</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 flex items-center gap-3">
                <div className="p-2 rounded-lg bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
                  <ShoppingCart className="w-5 h-5" />
                </div>
                <div>
                  <p className="text-xs text-zinc-500">Pedidos</p>
                  <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{stats.orders_count.toLocaleString()}</p>
                </div>
              </CardContent>
            </Card>
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div className="space-y-0.5">
              <p className="text-zinc-500 text-xs">Última sync completa</p>
              <p className="text-zinc-700 dark:text-zinc-300">{fmt(stats.last_full_sync_at)}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-zinc-500 text-xs">Última sync incremental</p>
              <p className="text-zinc-700 dark:text-zinc-300">{fmt(stats.last_incremental_sync_at)}</p>
            </div>
          </div>
          {stats.last_error && (
            <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
              <p className="font-medium">Último error:</p>
              <p className="mt-0.5 break-all">{stats.last_error}</p>
            </div>
          )}
        </>
      )}
      <Button onClick={handleSync} disabled={syncing} variant="outline" className="gap-2">
        <RefreshCw className={cn("w-4 h-4", syncing && "animate-spin")} />
        {syncing ? "Sincronizando…" : "Sincronizar ahora"}
      </Button>
    </div>
  )
}

// ── Sección búsqueda de productos ─────────────────────────────────────────────

function ProductSearchSection({ configId }: { configId: string }) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResultOut[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiGet<SearchResultsOut>(`/v1/connector-configs/${configId}/search`, {
        q: query.trim(),
        max_results: 10,
      })
      setResults(data.results)
      setSearched(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al buscar")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSearch} className="flex gap-2">
        <Input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Buscar productos por nombre…"
          className="flex-1"
        />
        <Button type="submit" disabled={loading} className="gap-2">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Buscar
        </Button>
      </form>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {searched && results.length === 0 && !error && (
        <p className="text-sm text-zinc-400 text-center py-8">Sin resultados para "{query}"</p>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          {results.map(r => (
            <div
              key={r.id}
              className="flex items-center justify-between p-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">{r.name}</p>
                <div className="flex items-center gap-3 mt-0.5">
                  {r.price != null && (
                    <span className="text-xs text-zinc-500">${r.price.toFixed(2)}</span>
                  )}
                  <span className="text-xs text-zinc-400">Score: {r.score.toFixed(3)}</span>
                </div>
              </div>
              {r.url && (
                <a
                  href={r.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-3 text-blue-600 hover:text-blue-700 dark:text-blue-400"
                >
                  <ExternalLink className="w-4 h-4" />
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Sección configurar credenciales ──────────────────────────────────────────

function ConfigureSection({ config, onUpdated }: { config: ConnectorConfigOut; onUpdated: (c: ConnectorConfigOut) => void }) {
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
  const fields = config.connector_name === "woocommerce" ? wooFields : shopifyFields

  const [creds, setCreds] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  async function handleSave() {
    setLoading(true)
    setError(null)
    setSuccess(false)
    try {
      const updated = await apiFetch<ConnectorConfigOut>(`/v1/connector-configs/${config.id}/configure`, {
        method: "POST",
        body: JSON.stringify({ credentials: creds }),
      })
      onUpdated(updated)
      setSuccess(true)
      setCreds({})
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al guardar")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-500">
        Ingresa las credenciales para reconectar o actualizar el acceso al conector.
        Los valores actuales no se muestran por seguridad.
      </p>
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}
      {success && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-green-50 text-green-700 text-sm dark:bg-green-900/20 dark:text-green-400">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          Credenciales actualizadas correctamente.
        </div>
      )}
      <div className="space-y-3">
        {fields.map(f => (
          <div key={f.key}>
            <Label htmlFor={`cred-${f.key}`}>{f.label}</Label>
            <Input
              id={`cred-${f.key}`}
              type={f.key.includes("secret") || f.key.includes("token") ? "password" : "text"}
              value={creds[f.key] ?? ""}
              onChange={e => setCreds(prev => ({ ...prev, [f.key]: e.target.value }))}
              placeholder={f.placeholder}
              className="mt-1"
            />
          </div>
        ))}
      </div>
      <Button onClick={handleSave} disabled={loading} className="gap-2">
        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
        Guardar credenciales
      </Button>
    </div>
  )
}

// ── Página ─────────────────────────────────────────────────────────────────────

type Params = { config_id: string }

export default function ConnectorDetailPage({ params }: { params: Promise<Params> }) {
  const { config_id } = use(params)
  const router = useRouter()
  const [config, setConfig] = useState<ConnectorConfigOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"stats" | "search" | "config">("stats")

  useEffect(() => {
    apiGet<ConnectorConfigOut>(`/v1/connector-configs/${config_id}`)
      .then(setConfig)
      .catch(e => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false))
  }, [config_id])

  if (loading) return (
    <div className="p-6">
      <div className="animate-pulse space-y-4">
        <div className="h-6 bg-zinc-200 dark:bg-zinc-700 rounded w-48" />
        <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-32" />
      </div>
    </div>
  )

  if (error || !config) return (
    <div className="p-6">
      <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
        <AlertCircle className="w-4 h-4 shrink-0" />
        {error ?? "Conector no encontrado"}
      </div>
    </div>
  )

  const tabs: { key: "stats" | "search" | "config"; label: string }[] = [
    { key: "stats", label: "Estadísticas" },
    { key: "search", label: "Buscar productos" },
    { key: "config", label: "Credenciales" },
  ]

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.push("/connectors")}
          className="text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">{config.display_name}</h1>
          <p className="text-sm text-zinc-500">{connectorLabel(config.connector_name)} · {config.status}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-zinc-200 dark:border-zinc-700 gap-1">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === t.key
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div>
        {activeTab === "stats" && <StatsSection configId={config_id} />}
        {activeTab === "search" && <ProductSearchSection configId={config_id} />}
        {activeTab === "config" && <ConfigureSection config={config} onUpdated={setConfig} />}
      </div>
    </div>
  )
}
