"use client"

import React, { useState, useEffect, useRef } from "react"
import { Download, Loader2, CheckCircle2, AlertCircle, FileArchive } from "lucide-react"
import { apiFetch, apiGet } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"

interface ExportJob {
  id: string
  status: "queued" | "processing" | "done" | "error"
  error?: string | null
  storage_uri?: string | null
  created_at: string
  finished_at?: string | null
}

const STATUS_LABEL: Record<ExportJob["status"], string> = {
  queued: "En cola…",
  processing: "Procesando…",
  done: "Listo para descargar",
  error: "Error al exportar",
}

const STATUS_ICON: Record<ExportJob["status"], React.ElementType> = {
  queued: Loader2,
  processing: Loader2,
  done: CheckCircle2,
  error: AlertCircle,
}

export function GdprExport() {
  const { success, error: toastError } = useToast()
  const [job, setJob] = useState<ExportJob | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => () => stopPolling(), [])

  async function startExport() {
    setStarting(true)
    try {
      const newJob = await apiFetch<ExportJob>("/v1/tenants/me/export", { method: "POST" })
      setJob(newJob)
      pollRef.current = setInterval(() => pollStatus(newJob.id), 3000)
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Error al iniciar exportación")
    } finally {
      setStarting(false)
    }
  }

  async function pollStatus(jobId: string) {
    try {
      const updated = await apiGet<ExportJob>(`/v1/tenants/me/export/${jobId}/status`)
      setJob(updated)
      if (updated.status === "done") {
        stopPolling()
        success("Exportación completada — descarga disponible")
      } else if (updated.status === "error") {
        stopPolling()
        toastError(updated.error ?? "Error en la exportación")
      }
    } catch {
      // silencioso — el polling reintentará
    }
  }

  async function handleDownload() {
    if (!job) return
    try {
      const res = await apiGet<{ download_url: string }>(`/v1/tenants/me/export/${job.id}/download`)
      window.open(res.download_url, "_blank")
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Error al descargar")
    }
  }

  const jobStatus = job?.status ?? null
  const Icon = jobStatus ? STATUS_ICON[jobStatus as ExportJob["status"]] : FileArchive
  const isRunning = jobStatus === "queued" || jobStatus === "processing"
  const statusLabel = jobStatus ? STATUS_LABEL[jobStatus as ExportJob["status"]] : ""

  return (
    <div className="flex items-center gap-3 p-4 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900">
      <div className="w-9 h-9 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
        <Icon className={`w-4 h-4 text-zinc-500 ${isRunning ? "animate-spin" : ""} ${jobStatus === "done" ? "text-green-500" : ""} ${jobStatus === "error" ? "text-red-500" : ""}`} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
          Exportar datos (GDPR)
        </p>
        {job ? (
          <p className={`text-xs mt-0.5 ${jobStatus === "error" ? "text-red-500" : "text-zinc-400"}`}>
            {statusLabel}
          </p>
        ) : (
          <p className="text-xs text-zinc-400 mt-0.5">
            Descarga un ZIP con todos tus datos
          </p>
        )}
      </div>
      <div className="shrink-0">
        {!job || jobStatus === "error" ? (
          <Button
            size="sm"
            variant="outline"
            onClick={startExport}
            disabled={starting}
            className="gap-2"
          >
            {starting ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Download className="w-3.5 h-3.5" />
            )}
            {starting ? "Iniciando…" : "Exportar"}
          </Button>
        ) : jobStatus === "done" ? (
          <Button size="sm" onClick={handleDownload} className="gap-2">
            <Download className="w-3.5 h-3.5" />
            Descargar
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled className="gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            {statusLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
