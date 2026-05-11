"use client"

import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Key, Plus, Trash2, Copy, Check, Loader2, AlertCircle, X, Eye, EyeOff } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch, apiGet } from "@/lib/api"
import { formatDateTime } from "@/lib/date"
import type { ApiKeyOut } from "@/lib/types"

interface ApiKeyCreated extends ApiKeyOut {
  token: string
}

const SCOPE_LABELS: Record<string, string> = {
  read: "Lectura",
  write: "Escritura",
  admin: "Admin",
}

export default function ApiKeysPage() {
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState("")
  const [scopes, setScopes] = useState<string[]>(["read"])
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [showInclRevoked, setShowInclRevoked] = useState(false)

  const { data: keys, isLoading } = useQuery<ApiKeyOut[]>({
    queryKey: ["api-keys", showInclRevoked],
    queryFn: () => apiGet<ApiKeyOut[]>("/v1/api-keys", showInclRevoked ? { incluir_revocadas: 1 } : undefined),
  })

  const createMut = useMutation({
    mutationFn: () =>
      apiFetch<ApiKeyCreated>("/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), scopes }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["api-keys"] })
      setCreated(data)
      setShowCreate(false)
      setName("")
      setScopes(["read"])
      setError(null)
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Error al crear API key"),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["api-keys"] })
      setDeleteConfirm(null)
    },
  })

  async function copyToken() {
    if (!created?.token) return
    await navigator.clipboard.writeText(created.token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function toggleScope(s: string) {
    setScopes((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    )
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Key className="w-5 h-5 text-zinc-500" />
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">API Keys</h1>
        </div>
        <Button size="sm" onClick={() => { setShowCreate(true); setCreated(null) }}>
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          Nueva API key
        </Button>
      </div>

      {/* Token creado — mostrar una sola vez */}
      {created && (
        <div className="rounded-lg border border-green-300 bg-green-50 dark:bg-green-950/30 dark:border-green-800 p-4 space-y-2">
          <p className="text-sm font-semibold text-green-800 dark:text-green-400">
            API key creada: <span className="font-mono">{created.name}</span>
          </p>
          <p className="text-xs text-green-700 dark:text-green-500">
            Copia este token ahora — no se mostrará de nuevo.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-white dark:bg-zinc-900 border border-green-200 dark:border-green-800 rounded px-2 py-1.5 font-mono text-zinc-700 dark:text-zinc-300 truncate">
              {created.token}
            </code>
            <Button size="sm" variant="outline" onClick={copyToken} className="h-7 text-xs shrink-0">
              {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
            </Button>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setCreated(null)} className="h-6 text-xs">
            Cerrar
          </Button>
        </div>
      )}

      {/* Formulario de creación */}
      {showCreate && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Nueva API key</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Nombre</label>
              <Input
                placeholder="Ej: Integración CRM"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-sm"
              />
            </div>

            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Permisos</label>
              <div className="flex gap-2">
                {["read", "write", "admin"].map((s) => (
                  <button
                    key={s}
                    onClick={() => toggleScope(s)}
                    className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                      scopes.includes(s)
                        ? "bg-zinc-900 text-white border-zinc-900 dark:bg-zinc-100 dark:text-zinc-900 dark:border-zinc-100"
                        : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700"
                    }`}
                  >
                    {SCOPE_LABELS[s]}
                  </button>
                ))}
              </div>
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
                disabled={!name.trim() || scopes.length === 0 || createMut.isPending}
                className="h-7 text-xs"
              >
                {createMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Crear"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Filtro */}
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-zinc-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showInclRevoked}
            onChange={(e) => setShowInclRevoked(e.target.checked)}
            className="rounded"
          />
          Incluir revocadas
        </label>
      </div>

      {/* Lista */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-zinc-400">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Cargando...
        </div>
      ) : !keys?.length ? (
        <div className="text-center py-16 text-zinc-400 text-sm">
          <Key className="w-8 h-8 mx-auto mb-3 opacity-30" />
          <p>No hay API keys creadas.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {keys.map((key) => (
            <Card key={key.id} className={key.revoked_at ? "opacity-60" : ""}>
              <CardContent className="py-3 px-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">{key.name}</p>
                      {key.revoked_at && (
                        <Badge variant="destructive" className="text-xs">Revocada</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <code className="text-xs text-zinc-400 font-mono">{key.prefix}••••••••</code>
                      <div className="flex gap-1">
                        {key.scopes.map((s) => (
                          <Badge key={s} variant="secondary" className="text-xs px-1.5 py-0">
                            {SCOPE_LABELS[s] ?? s}
                          </Badge>
                        ))}
                      </div>
                    </div>
                    <p className="text-xs text-zinc-400 mt-0.5">
                      Creada {formatDateTime(new Date(key.created_at))}
                      {key.last_used_at && ` · Último uso ${formatDateTime(new Date(key.last_used_at))}`}
                    </p>
                  </div>

                  {!key.revoked_at && (
                    deleteConfirm === key.id ? (
                      <div className="flex items-center gap-1 shrink-0">
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => deleteMut.mutate(key.id)}
                          disabled={deleteMut.isPending}
                          className="h-6 text-xs px-2"
                        >
                          Revocar
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
                        onClick={() => setDeleteConfirm(key.id)}
                        className="h-6 w-6 p-0 text-zinc-400 hover:text-red-500 shrink-0"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    )
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
