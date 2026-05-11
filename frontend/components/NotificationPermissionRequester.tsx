"use client"

import { useEffect } from "react"
import { requestNotificationPermission } from "@/hooks/use-notifications"

export function NotificationPermissionRequester() {
  useEffect(() => {
    if (typeof window === "undefined" || !("Notification" in window)) return
    if (Notification.permission === "default") {
      // Pedir permiso después de un breve delay para no interrumpir el primer render
      const timer = setTimeout(() => {
        requestNotificationPermission()
      }, 3000)
      return () => clearTimeout(timer)
    }
  }, [])

  return null
}
