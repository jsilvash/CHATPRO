"use client"

import { X, CheckCircle2, AlertCircle, Info } from "lucide-react"
import { useToastStore, type ToastVariant } from "@/hooks/use-toast"
import { cn } from "@/lib/utils"

function ToastIcon({ variant }: { variant?: ToastVariant }) {
  if (variant === "success") return <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
  if (variant === "destructive") return <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
  return <Info className="w-4 h-4 text-blue-500 shrink-0" />
}

export function Toaster() {
  const { toasts, removeToast } = useToastStore()

  if (!toasts.length) return null

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "pointer-events-auto flex items-start gap-3 rounded-lg border px-4 py-3 shadow-lg bg-white dark:bg-zinc-900 text-sm",
            t.variant === "success" && "border-green-200 dark:border-green-800",
            t.variant === "destructive" && "border-red-200 dark:border-red-800",
            (!t.variant || t.variant === "default") && "border-zinc-200 dark:border-zinc-700",
          )}
          role="alert"
        >
          <ToastIcon variant={t.variant} />
          <div className="flex-1 min-w-0">
            <p className="font-medium text-zinc-900 dark:text-zinc-50 leading-snug">{t.title}</p>
            {t.description && (
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{t.description}</p>
            )}
          </div>
          <button
            onClick={() => removeToast(t.id)}
            className="shrink-0 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
            aria-label="Cerrar notificación"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
