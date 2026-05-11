"use client"

import { useState, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { BookOpen, Upload, Link2, Trash2, Loader2, AlertCircle, Plus, X, FileText, Globe } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch, apiGet, API_URL } from "@/lib/api"
import type { KbDocumentOut } from "@/lib/types"

const STATUS_LABEL: Record<string, string> = {
  ready: "Listo",
  processing: "Procesando",
  error: "Error",
  pending: "Pendiente",
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  ready: "success",
  processing: "warning",
  error: "destructive",
  pending: "secondary",
}

type UploadMode = "pdf" | "url" | null

export default function KnowledgePage() {
  const qc = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<UploadMode>(null)
  const [title, setTitle] = useState("")
  const [url, setUrl] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  const { data: docs, isLoading } = useQuery<KbDocumentOut[]>({
    queryKey: ["knowledge-docs"],
    queryFn: () => apiGet<KbDocumentOut[]>("/v1/knowledge"),
    refetchInterval: 10_000,
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/knowledge/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-docs"] })
      setDeleteConfirm(null)
    },
  })

  async function handleUpload() {
    if (!title.trim()) {
      setUploadError("El título es obligatorio")
      return
    }
    if (mode === "url" && !url.trim()) {
      setUploadError("La URL es obligatoria")
      return
    }
    if (mode === "pdf" && !file) {
      setUploadError("Selecciona un archivo PDF")
      return
    }

    setUploading(true)
    setUploadError(null)

    try {
      const form = new FormData()
      form.append("title", title.trim())
      if (mode === "url") {
        form.append("url", url.trim())
      } else if (file) {
        form.append("file", file)
      }

      const res = await fetch(`${API_URL}/v1/knowledge/upload`, {
        method: "POST",
        credentials: "include",
        body: form,
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body)
      }

      qc.invalidateQueries({ queryKey: ["knowledge-docs"] })
      setMode(null)
      setTitle("")
      setUrl("")
      setFile(null)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Error al subir documento")
    } finally {
      setUploading(false)
    }
  }

  function cancelUpload() {
    setMode(null)
    setTitle("")
    setUrl("")
    setFile(null)
    setUploadError(null)
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen className="w-5 h-5 text-zinc-500" />
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Base de conocimiento</h1>
        </div>
        {!mode && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setMode("url")}>
              <Link2 className="w-3.5 h-3.5 mr-1.5" />
              Añadir URL
            </Button>
            <Button size="sm" onClick={() => setMode("pdf")}>
              <Upload className="w-3.5 h-3.5 mr-1.5" />
              Subir PDF
            </Button>
          </div>
        )}
      </div>

      {/* Formulario de upload */}
      {mode && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              {mode === "pdf" ? (
                <><FileText className="w-4 h-4 text-blue-500" /> Subir PDF</>
              ) : (
                <><Globe className="w-4 h-4 text-green-500" /> Ingestar URL</>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Título</label>
              <Input
                placeholder="Nombre del documento"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="h-8 text-sm"
              />
            </div>

            {mode === "url" ? (
              <div>
                <label className="text-xs text-zinc-500 mb-1 block">URL</label>
                <Input
                  placeholder="https://..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  className="h-8 text-sm"
                />
              </div>
            ) : (
              <div>
                <label className="text-xs text-zinc-500 mb-1 block">Archivo PDF</label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  className="hidden"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
                <div className="flex gap-2 items-center">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => fileInputRef.current?.click()}
                    className="h-8 text-xs"
                  >
                    Seleccionar archivo
                  </Button>
                  {file && (
                    <span className="text-xs text-zinc-500 truncate max-w-[200px]">
                      {file.name}
                    </span>
                  )}
                </div>
              </div>
            )}

            {uploadError && (
              <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded p-2">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                {uploadError}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="ghost" onClick={cancelUpload} className="h-7 text-xs">
                Cancelar
              </Button>
              <Button
                size="sm"
                onClick={handleUpload}
                disabled={uploading}
                className="h-7 text-xs"
              >
                {uploading ? (
                  <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Procesando...</>
                ) : (
                  <><Plus className="w-3.5 h-3.5 mr-1.5" /> Añadir</>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Lista de documentos */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-zinc-400">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Cargando documentos...
        </div>
      ) : !docs?.length ? (
        <div className="text-center py-16 text-zinc-400 text-sm">
          <BookOpen className="w-8 h-8 mx-auto mb-3 opacity-30" />
          <p>No hay documentos en la base de conocimiento.</p>
          <p className="text-xs mt-1">Sube un PDF o añade una URL para empezar.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
            <Card key={doc.id} className="hover:border-zinc-300 dark:hover:border-zinc-600 transition-colors">
              <CardContent className="py-3 px-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="mt-0.5 shrink-0">
                      {doc.source_type === "pdf" ? (
                        <FileText className="w-4 h-4 text-blue-400" />
                      ) : (
                        <Globe className="w-4 h-4 text-green-400" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-zinc-800 dark:text-zinc-200 truncate">
                        {doc.title}
                      </p>
                      {doc.source_uri && (
                        <p className="text-xs text-zinc-400 truncate mt-0.5">{doc.source_uri}</p>
                      )}
                      {doc.error && (
                        <p className="text-xs text-red-500 mt-0.5">{doc.error}</p>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <Badge variant={STATUS_VARIANT[doc.status] ?? "secondary"} className="text-xs">
                      {STATUS_LABEL[doc.status] ?? doc.status}
                    </Badge>

                    {deleteConfirm === doc.id ? (
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => deleteMut.mutate(doc.id)}
                          disabled={deleteMut.isPending}
                          className="h-6 text-xs px-2"
                        >
                          Confirmar
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
                        onClick={() => setDeleteConfirm(doc.id)}
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
