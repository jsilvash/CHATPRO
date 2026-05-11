"use client"

import { useRouter } from "next/navigation"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { useSessionExpiry } from "@/hooks/use-session-expiry"

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

interface SessionExpiryBannerProps {
  tokenExp: number | undefined
}

export function SessionExpiryBanner({ tokenExp }: SessionExpiryBannerProps) {
  const { status, secondsLeft } = useSessionExpiry(tokenExp)
  const router = useRouter()

  async function handleRefresh() {
    const res = await fetch("/api/auth/refresh", { method: "POST" })
    if (res.ok) {
      router.refresh()
    } else {
      await fetch("/api/auth/logout", { method: "POST" })
      router.push("/login")
    }
  }

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" })
    router.push("/login")
  }

  if (status === "expired") {
    return (
      <div
        role="alert"
        aria-live="assertive"
        className="flex items-center gap-3 px-4 py-2.5 bg-red-600 text-white text-sm shrink-0"
      >
        <AlertTriangle className="w-4 h-4 shrink-0" />
        <span className="flex-1">Tu sesión ha expirado.</span>
        <button
          onClick={handleLogout}
          className="underline font-medium hover:no-underline focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-1 focus:ring-offset-red-600 rounded"
        >
          Iniciar sesión
        </button>
      </div>
    )
  }

  if (status === "expiring_soon" && secondsLeft !== null) {
    return (
      <div
        role="alert"
        aria-live="polite"
        className="flex items-center gap-3 px-4 py-2 bg-amber-500 text-white text-sm shrink-0"
      >
        <AlertTriangle className="w-4 h-4 shrink-0" />
        <span className="flex-1">
          Tu sesión expira en <strong>{formatSeconds(secondsLeft)}</strong>.
        </span>
        <button
          onClick={handleRefresh}
          className="inline-flex items-center gap-1.5 px-3 py-1 bg-white text-amber-700 rounded text-xs font-medium hover:bg-amber-50 focus:outline-none focus:ring-2 focus:ring-white transition-colors"
        >
          <RefreshCw className="w-3 h-3" />
          Extender sesión
        </button>
      </div>
    )
  }

  return null
}
