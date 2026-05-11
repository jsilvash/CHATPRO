import { type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-16 px-4 text-center", className)}>
      {/* Ilustración SVG simple */}
      <div className="relative mb-4">
        <div className="w-20 h-20 rounded-full bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center">
          <Icon className="w-9 h-9 text-zinc-400 dark:text-zinc-500" strokeWidth={1.5} />
        </div>
        {/* Anillo decorativo */}
        <div className="absolute inset-0 rounded-full border-2 border-dashed border-zinc-200 dark:border-zinc-700 scale-110" />
      </div>

      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 mb-1">{title}</h3>
      {description && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-xs leading-relaxed mb-4">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
