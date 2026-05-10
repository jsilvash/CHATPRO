"use client"

import { formatDistanceToNow } from "@/lib/date"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { ConversationSummary } from "@/lib/types"

const STATUS_LABELS: Record<string, string> = {
  bot: "Bot",
  agent: "Agente",
  waiting_agent: "Esperando",
  closed: "Cerrado",
}

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "success" | "warning" | "destructive" | "info"> = {
  bot: "secondary",
  agent: "success",
  waiting_agent: "warning",
  closed: "info",
}

function initials(name: string) {
  return name
    .split(" ")
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
}

interface ConversationCardProps {
  conv: ConversationSummary
  isActive?: boolean
  onClick?: () => void
}

export function ConversationCard({ conv, isActive, onClick }: ConversationCardProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-4 py-3 border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors",
        isActive && "bg-zinc-100 dark:bg-zinc-800",
      )}
    >
      <div className="flex items-start gap-3">
        {/* Avatar */}
        <div className="w-9 h-9 rounded-full bg-zinc-200 dark:bg-zinc-700 flex items-center justify-center text-xs font-medium text-zinc-600 dark:text-zinc-300 shrink-0">
          {initials(conv.wa_contact_name || conv.wa_contact_phone)}
        </div>

        <div className="flex-1 min-w-0">
          {/* Nombre + badge */}
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-zinc-900 dark:text-zinc-50 truncate">
              {conv.wa_contact_name || conv.wa_contact_phone}
            </span>
            <Badge variant={STATUS_VARIANTS[conv.status] ?? "secondary"} className="text-xs shrink-0">
              {STATUS_LABELS[conv.status] ?? conv.status}
            </Badge>
          </div>

          {/* Teléfono */}
          <p className="text-xs text-zinc-400 truncate">{conv.wa_contact_phone}</p>

          {/* Tags */}
          {conv.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {conv.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="text-xs bg-zinc-100 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400 px-1.5 py-0.5 rounded"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Tiempo */}
        {conv.last_message_at && (
          <span className="text-xs text-zinc-400 shrink-0 mt-0.5">
            {formatDistanceToNow(new Date(conv.last_message_at))}
          </span>
        )}
      </div>
    </button>
  )
}
