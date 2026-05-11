"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { apiGet } from "@/lib/api"
import type { AvailableAgent, AvailableAgentsResponse } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ArrowLeft, UserCircle, Loader2, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"

function LoadBar({ value, max }: { value: number; max: number }) {
  const pct = max === 0 ? 0 : Math.round((value / max) * 100)
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            pct > 70 ? "bg-red-400" : pct > 40 ? "bg-yellow-400" : "bg-green-400",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400 w-10 text-right">{value} conv.</span>
    </div>
  )
}

export default function AvailableAgentsPage() {
  const [agents, setAgents] = useState<AvailableAgent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<AvailableAgentsResponse>("/v1/users/available-agents")
      .then((d) => setAgents(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false))
  }, [])

  const maxConvs = Math.max(1, ...agents.map((a) => a.conv_count))

  return (
    <div className="p-6 max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <Link href="/users">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
            Agentes disponibles
          </h1>
          <p className="text-sm text-zinc-500">Carga de trabajo actual por agente</p>
        </div>
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
      ) : agents.length === 0 ? (
        <p className="text-center text-sm text-zinc-400 py-12">No hay agentes disponibles.</p>
      ) : (
        <div className="space-y-2">
          {agents.map((agent, idx) => (
            <Card key={agent.id}>
              <CardContent className="py-3 px-4">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-bold text-zinc-400 w-5 text-right">{idx + 1}</span>
                  <div className="p-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800">
                    <UserCircle className="w-4 h-4 text-zinc-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">
                      {agent.full_name || agent.email}
                    </p>
                    <LoadBar value={agent.conv_count} max={maxConvs} />
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
