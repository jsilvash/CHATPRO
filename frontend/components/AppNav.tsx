"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { MessageSquare, LayoutDashboard, LogOut, Users, Phone, BarChart2, Plug, Zap, Smartphone, Bot, Clock3, BookOpen, Key, Webhook, Sun, Moon, Monitor, Menu, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { JWTPayload } from "@/lib/auth"
import { useNotifications } from "@/hooks/use-notifications"
import { Badge } from "@/components/ui/badge"
import { useTheme } from "@/components/ThemeProvider"
import { useState } from "react"

interface AppNavProps {
  session: JWTPayload
}

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/inbox", label: "Inbox", icon: MessageSquare },
  { href: "/users", label: "Usuarios", icon: Users },
  { href: "/contacts", label: "Contactos", icon: Phone },
  { href: "/sla", label: "SLA", icon: BarChart2 },
  { href: "/connectors", label: "Conectores", icon: Plug },
  { href: "/canned-responses", label: "Respuestas rápidas", icon: Zap },
  { href: "/wa-numbers", label: "Números WA", icon: Smartphone },
  { href: "/personas", label: "Personas IA", icon: Bot },
  { href: "/office-hours", label: "Horarios", icon: Clock3 },
  { href: "/knowledge", label: "Conocimiento", icon: BookOpen },
  { href: "/api-keys", label: "API Keys", icon: Key },
  { href: "/webhooks", label: "Webhooks", icon: Webhook },
]

export function AppNav({ session }: AppNavProps) {
  const pathname = usePathname()
  const router = useRouter()
  const { waitingCount } = useNotifications()
  const { theme, setTheme } = useTheme()
  const [mobileOpen, setMobileOpen] = useState(false)

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" })
    router.push("/login")
    router.refresh()
  }

  const themeOptions: { value: "light" | "dark" | "system"; icon: React.ElementType; label: string }[] = [
    { value: "light", icon: Sun, label: "Claro" },
    { value: "dark", icon: Moon, label: "Oscuro" },
    { value: "system", icon: Monitor, label: "Sistema" },
  ]

  const navContent = (
    <>
      {/* Logo */}
      <div className="px-4 py-5 border-b border-zinc-200 dark:border-zinc-700 flex items-center justify-between">
        <div className="min-w-0">
          <h1 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">ChatPro</h1>
          <p className="text-xs text-zinc-500 truncate">{session.email ?? session.sub}</p>
        </div>
        {/* Cerrar en móvil */}
        <button
          onClick={() => setMobileOpen(false)}
          className="md:hidden ml-2 p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
          aria-label="Cerrar menú"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Nav links */}
      <div className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isActive = pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              onClick={() => setMobileOpen(false)}
              prefetch
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                isActive
                  ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50"
                  : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-50",
              )}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="flex-1">{label}</span>
              {href === "/inbox" && (
                <span
                  aria-live="polite"
                  aria-atomic="true"
                  aria-label={waitingCount > 0 ? `${waitingCount} conversaciones esperando` : undefined}
                >
                  {waitingCount > 0 && (
                    <Badge variant="warning" className="text-xs px-1.5 py-0" aria-hidden="true">
                      {waitingCount}
                    </Badge>
                  )}
                </span>
              )}
            </Link>
          )
        })}
      </div>

      {/* Tema + Logout */}
      <div className="px-2 py-4 border-t border-zinc-200 dark:border-zinc-700 space-y-1">
        {/* Toggle tema */}
        <div className="flex items-center gap-1 px-3 py-1">
          {themeOptions.map(({ value, icon: Icon, label }) => (
            <button
              key={value}
              onClick={() => setTheme(value)}
              title={label}
              className={cn(
                "flex-1 flex items-center justify-center p-1.5 rounded-md text-xs transition-colors",
                theme === value
                  ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-700 dark:text-zinc-50"
                  : "text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300",
              )}
              aria-label={`Tema ${label}`}
            >
              <Icon className="w-3.5 h-3.5" />
            </button>
          ))}
        </div>
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-50 transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Cerrar sesión
        </button>
      </div>
    </>
  )

  return (
    <>
      {/* Botón hamburguesa visible solo en móvil */}
      <button
        onClick={() => setMobileOpen(true)}
        className="md:hidden fixed top-3 left-3 z-40 p-2 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-50 shadow-sm"
        aria-label="Abrir menú"
      >
        <Menu className="w-4 h-4" />
      </button>

      {/* Overlay oscuro en móvil */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/40"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar — fijo en desktop, deslizable en móvil */}
      <nav
        className={cn(
          "flex flex-col h-full bg-white border-r border-zinc-200 dark:bg-zinc-900 dark:border-zinc-700 shrink-0 z-50",
          // Desktop: siempre visible, ancho fijo
          "md:relative md:translate-x-0 md:w-56",
          // Móvil: overlay deslizable desde la izquierda
          "fixed inset-y-0 left-0 w-64 transition-transform duration-200",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
        aria-label="Navegación principal"
      >
        {navContent}
      </nav>
    </>
  )
}
