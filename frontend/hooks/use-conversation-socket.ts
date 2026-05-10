"use client"

import { useEffect, useRef, useCallback } from "react"

export interface WsMessage {
  event: string
  message_id?: string
  direction?: string
  text?: string
  source?: string
  sent_at?: string
}

interface UseConversationSocketOptions {
  convId: string
  onMessage: (msg: WsMessage) => void
  enabled?: boolean
}

export function useConversationSocket({
  convId,
  onMessage,
  enabled = true,
}: UseConversationSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const onMessageRef = useRef(onMessage)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  onMessageRef.current = onMessage

  const connect = useCallback(async () => {
    if (!enabled || !convId) return

    try {
      // Obtiene el token para el handshake del WebSocket
      const res = await fetch("/api/auth/ws-token")
      if (!res.ok) return
      const { token } = await res.json()

      const wsUrl = `${
        process.env.NEXT_PUBLIC_API_URL?.replace(/^http/, "ws") ?? "ws://localhost:8000"
      }/ws/inbox/${convId}?token=${token}`

      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WsMessage
          onMessageRef.current(data)
        } catch {
          // ignore parse errors
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        // Reconectar tras 3 segundos
        reconnectTimeout.current = setTimeout(() => connect(), 3000)
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch {
      // ignore connection errors, will retry
    }
  }, [convId, enabled])

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
}
