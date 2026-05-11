"use client"

import { useEffect, useState, useCallback } from "react"
import { apiGet } from "@/lib/api"
import { CheckCircle2, Circle, ChevronDown, ChevronUp, X, Rocket } from "lucide-react"
import { cn } from "@/lib/utils"

interface ChecklistStep {
  id: string
  label: string
  description: string
  href: string
  done: boolean
}

const STORAGE_KEY = (tenantId: string) => `chatpro-onboarding-dismissed-${tenantId}`

async function checkSteps(): Promise<Record<string, boolean>> {
  const results: Record<string, boolean> = {
    wa_number: false,
    persona: false,
    connector: false,
    agent: false,
  }

  try {
    const [wa, personas, connectors, users] = await Promise.allSettled([
      apiGet<{ items: unknown[]; total: number }>("/v1/wa-numbers"),
      apiGet<{ items: unknown[]; total: number }>("/v1/personas"),
      apiGet<{ items: unknown[]; total: number }>("/v1/connector-configs"),
      apiGet<{ items: unknown[]; total: number }>("/v1/users"),
    ])

    if (wa.status === "fulfilled") results.wa_number = (wa.value?.total ?? 0) > 0
    if (personas.status === "fulfilled") results.persona = (personas.value?.total ?? 0) > 0
    if (connectors.status === "fulfilled") results.connector = (connectors.value?.total ?? 0) > 0
    if (users.status === "fulfilled") results.agent = (users.value?.total ?? 0) > 1 // más de 1 = hay agentes además del owner
  } catch {
    // silencioso
  }

  return results
}

interface Props {
  tenantId: string
}

export function OnboardingChecklist({ tenantId }: Props) {
  const [steps, setSteps] = useState<ChecklistStep[]>([])
  const [loading, setLoading] = useState(true)
  const [collapsed, setCollapsed] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const load = useCallback(async () => {
    const done = await checkSteps()
    setSteps([
      {
        id: "wa_number",
        label: "Conectar número de WhatsApp",
        description: "Añade y conecta tu primer número de WhatsApp",
        href: "/wa-numbers",
        done: done.wa_number,
      },
      {
        id: "persona",
        label: "Crear una persona IA",
        description: "Define la personalidad y tono de tu asistente",
        href: "/personas",
        done: done.persona,
      },
      {
        id: "connector",
        label: "Añadir un conector",
        description: "Conecta tu tienda WooCommerce o Shopify",
        href: "/connectors",
        done: done.connector,
      },
      {
        id: "agent",
        label: "Invitar un agente",
        description: "Añade un miembro del equipo para gestionar conversaciones",
        href: "/users",
        done: done.agent,
      },
    ])
    setLoading(false)
  }, [])

  useEffect(() => {
    // Verificar si ya fue descartado
    const key = STORAGE_KEY(tenantId)
    if (typeof window !== "undefined" && localStorage.getItem(key) === "1") {
      setDismissed(true)
      return
    }
    load()
  }, [tenantId, load])

  function handleDismiss() {
    localStorage.setItem(STORAGE_KEY(tenantId), "1")
    setDismissed(true)
  }

  if (dismissed) return null
  if (loading) return null // no mostrar skeleton — es opcional

  const completedCount = steps.filter(s => s.done).length
  const allDone = completedCount === steps.length

  // Auto-ocultar si todo está completo Y no se ha interactuado
  if (allDone && !localStorage.getItem(STORAGE_KEY(tenantId))) {
    return null
  }

  const progress = Math.round((completedCount / steps.length) * 100)

  return (
    <div className="rounded-xl border border-zinc-200 bg-white dark:bg-zinc-900 dark:border-zinc-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-950/40 dark:to-purple-950/40 border-b border-zinc-200 dark:border-zinc-700">
        <div className="flex items-center gap-2.5">
          <Rocket className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
          <div>
            <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
              Primeros pasos
            </span>
            <span className="ml-2 text-xs text-zinc-500">
              {completedCount}/{steps.length} completados
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="p-1 rounded hover:bg-white/60 dark:hover:bg-zinc-800/60 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
            aria-label={collapsed ? "Expandir" : "Contraer"}
          >
            {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </button>
          <button
            onClick={handleDismiss}
            className="p-1 rounded hover:bg-white/60 dark:hover:bg-zinc-800/60 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
            aria-label="Descartar"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1 bg-zinc-100 dark:bg-zinc-800">
        <div
          className="h-full bg-indigo-500 transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Steps */}
      {!collapsed && (
        <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {steps.map(step => (
            <a
              key={step.id}
              href={step.done ? undefined : step.href}
              className={cn(
                "flex items-start gap-3 px-4 py-3 transition-colors",
                step.done
                  ? "opacity-60 cursor-default"
                  : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50 cursor-pointer",
              )}
            >
              <div className="mt-0.5 shrink-0">
                {step.done ? (
                  <CheckCircle2 className="w-4 h-4 text-green-500" />
                ) : (
                  <Circle className="w-4 h-4 text-zinc-300 dark:text-zinc-600" />
                )}
              </div>
              <div>
                <p className={cn(
                  "text-sm font-medium",
                  step.done
                    ? "line-through text-zinc-400 dark:text-zinc-500"
                    : "text-zinc-900 dark:text-zinc-100",
                )}>
                  {step.label}
                </p>
                {!step.done && (
                  <p className="text-xs text-zinc-500 mt-0.5">{step.description}</p>
                )}
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
