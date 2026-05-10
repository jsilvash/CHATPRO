"use client"

import { useEffect, useRef, useCallback, useState } from "react"
import { decodeJwt } from "jose"

export interface NotificationEvent {
  event: string
  conversation_id: string
  wa_contact_phone: string
  tenant_id: string
  timestamp: string
}

export function useNotifications() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [waitingCount, setWaitingCount] = useState(0)
  const [lastEvent, setLastEvent] = useState<NotificationEvent | null>(null)

  const connect = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/ws-token")
      if (!res.ok) return
      const { token } = await res.json()

      // Extraer tenant_id del token JWT
      const payload = decodeJwt(token) as { tenant_id?: string }
      const tenantId = payload.tenant_id
      if (!tenantId) return

      const wsUrl = `${
        process.env.NEXT_PUBLIC_API_URL?.replace(/^http/, "ws") ?? "ws://localhost:8000"
      }/ws/notifications/${tenantId}?token=${token}`

      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as NotificationEvent
          setLastEvent(data)
          if (data.event === "conversation.waiting_agent") {
            setWaitingCount((prev) => prev + 1)
          }
        } catch {
          // ignore
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        reconnectTimeout.current = setTimeout(() => connect(), 5000)
      }

      ws.onerror = () => ws.close()
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  function clearWaitingCount() {
    setWaitingCount(0)
  }

  return { waitingCount, lastEvent, clearWaitingCount }
}
