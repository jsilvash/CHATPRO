"use client"

import { useEffect, useState } from "react"

const WARN_BEFORE_SECONDS = 5 * 60 // 5 minutos antes

export type SessionStatus = "ok" | "expiring_soon" | "expired"

export function useSessionExpiry(tokenExp: number | undefined) {
  const [status, setStatus] = useState<SessionStatus>("ok")
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)

  useEffect(() => {
    if (!tokenExp) return

    function check() {
      const now = Math.floor(Date.now() / 1000)
      const remaining = tokenExp! - now

      if (remaining <= 0) {
        setStatus("expired")
        setSecondsLeft(0)
      } else if (remaining <= WARN_BEFORE_SECONDS) {
        setStatus("expiring_soon")
        setSecondsLeft(remaining)
      } else {
        setStatus("ok")
        setSecondsLeft(remaining)
      }
    }

    check()
    const interval = setInterval(check, 10_000)
    return () => clearInterval(interval)
  }, [tokenExp])

  return { status, secondsLeft }
}
