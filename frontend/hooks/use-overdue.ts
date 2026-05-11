"use client"

import { useEffect, useState } from "react"
import { apiGet } from "@/lib/api"

interface OverdueResponse {
  total: number
}

export function useOverdueCount(intervalMs = 60_000) {
  const [overdueCount, setOverdueCount] = useState(0)

  async function fetchCount() {
    try {
      const data = await apiGet<OverdueResponse>("/v1/inbox/overdue", { threshold_minutes: 30 })
      setOverdueCount(data.total ?? 0)
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    fetchCount()
    const timer = setInterval(fetchCount, intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])

  return overdueCount
}
