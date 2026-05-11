"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { apiGet } from "@/lib/api"
import type { WaNumberListResponse, WaNumberResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  Smartphone,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  ChevronRight,
} from "lucide-react"

function sessionStatusBadge(status: string) {
  const map: Record<string, { label: string; color: string; icon: React.ElementType }> = {
    WORKING: { label: "Conectado", color: "text-green-700 bg-green-100 dark:text-green-400 dark:bg-green-900/30", icon: CheckCircle2 },
    STARTING: { label: "Iniciando", color: "text-yellow-700 bg-yellow-100 dark:text-yellow-400 dark:bg-yellow-900/30", icon: Clock },
    SCAN_QR_CODE: { label: "Esperando QR", color: "text-blue-700 bg-blue-100 dark:text-blue-400 dark:bg-blue-900/30", icon: Clock },
    FAILED: { label: "Error", color: "text-red-700 bg-red-100 dark:text-red-400 dark:bg-red-900/30", icon: XCircle },
    STOPPED: { label: "Detenido", color: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800", icon: XCircle },
  }
  const s = map[status] ?? { label: status || "Sin sesión", color: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800", icon: AlertCircle }
  const Icon = s.icon
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium", s.color)}>
      <Icon className="w-3 h-3" />
      {s.label}
    </span>
  )
}

export default function WaNumbersPage() {
  const router = useRouter()
  const [data, setData] = useState<WaNumberResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await apiGet<WaNumberListResponse>("/v1/wa-numbers")
      setData(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al cargar números")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div>
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Números WhatsApp</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Estado y métricas de cada número conectado</p>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-6">
                <div className="animate-pulse space-y-2">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-2/3" />
                  <div className="h-3 bg-zinc-200 dark:bg-zinc-700 rounded w-1/3" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : data.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-zinc-400 gap-3">
          <Smartphone className="w-12 h-12" />
          <p className="text-sm">No hay números WhatsApp configurados.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {data.map(wn => (
            <Card
              key={wn.id}
              className="cursor-pointer hover:border-zinc-300 dark:hover:border-zinc-600 transition-colors"
              onClick={() => router.push(`/wa-numbers/${wn.id}`)}
            >
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Smartphone className="w-4 h-4 text-zinc-400 shrink-0" />
                    <div className="min-w-0">
                      <CardTitle className="text-base truncate">{wn.label}</CardTitle>
                      <p className="text-xs text-zinc-500 mt-0.5 font-mono">
                        {wn.phone ?? wn.waha_session_name}
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0">{sessionStatusBadge(wn.session_status)}</div>
                </div>
              </CardHeader>
              <CardContent className="flex items-center justify-between">
                <div className="flex gap-1 flex-wrap">
                  {wn.is_default && (
                    <span className="text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 px-2 py-0.5 rounded-full">
                      Predeterminado
                    </span>
                  )}
                  {wn.tags.map(t => (
                    <span key={t} className="text-xs bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300 px-2 py-0.5 rounded-full">
                      {t}
                    </span>
                  ))}
                </div>
                <Button variant="ghost" size="sm" className="gap-1 text-zinc-500">
                  Métricas
                  <ChevronRight className="w-3.5 h-3.5" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
