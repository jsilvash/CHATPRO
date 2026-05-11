"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { MessageSquare, LayoutDashboard, LogOut, Users, Phone, BarChart2, Plug, Zap, Smartphone, Bot, Clock3, BookOpen, Key, Webhook, Search, CreditCard, Settings, Shield } from "lucide-react"
import { cn } from "@/lib/utils"
import type { JWTPayload } from "@/lib/auth"
import { useNotifications } from "@/hooks/use-notifications"
import { useOverdueCount } from "@/hooks/use-overdue"
import { Badge } from "@/components/ui/badge"

interface AppNavProps {
  session: JWTPayload
}

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/billing", label: "Billing", icon: CreditCard },
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
  { href: "/audit-log", label: "Audit Log", icon: Shield },
  { href: "/settings", label: "Configuración", icon: Settings },
]

export function AppNav({ session }: AppNavProps) {
  const pathname = usePathname()
  const router = useRouter()
  const { waitingCount } = useNotifications()
  const overdueCount = useOverdueCount()

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" })
    router.push("/login")
    router.refresh()
  }

  return (
    <nav className="w-56 flex flex-col h-full bg-white border-r border-zinc-200 dark:bg-zinc-900 dark:border-zinc-700 shrink-0">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-zinc-200 dark:border-zinc-700">
        <h1 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">ChatPro</h1>
        <p className="text-xs text-zinc-500 truncate">{session.email ?? session.sub}</p>
      </div>

      {/* Nav links */}
      <div className="flex-1 px-2 py-4 space-y-1">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isActive = pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
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
              {href === "/inbox" && overdueCount > 0 && (
                <Badge variant="destructive" className="text-xs px-1.5 py-0" title={`${overdueCount} conversaciones vencidas (+30min)`}>
                  {overdueCount}!
                </Badge>
              )}
            </Link>
          )
        })}
      </div>

      {/* Logout + search hint */}
      <div className="px-2 py-4 border-t border-zinc-200 dark:border-zinc-700 space-y-1">
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-50 transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Cerrar sesión
        </button>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs text-zinc-400 select-none">
          <Search className="w-3.5 h-3.5" />
          <span className="flex-1">Buscar</span>
          <kbd className="font-mono text-[10px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">⌘K</kbd>
        </div>
      </div>
    </nav>
  )
}
