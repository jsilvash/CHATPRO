import { cn } from "@/lib/utils"
import { formatDateTime } from "@/lib/date"
import type { MessageOut } from "@/lib/types"

interface MessageBubbleProps {
  message: MessageOut
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isOut = message.direction === "out"
  const time = message.sent_at ?? message.created_at

  return (
    <div className={cn("flex", isOut ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[70%] rounded-2xl px-4 py-2 shadow-sm",
          isOut
            ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 rounded-br-sm"
            : "bg-white text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50 border border-zinc-100 dark:border-zinc-700 rounded-bl-sm",
        )}
      >
        <p className="text-sm whitespace-pre-wrap break-words">{message.text}</p>
        <p
          className={cn(
            "text-xs mt-1",
            isOut ? "text-zinc-400 dark:text-zinc-600" : "text-zinc-400",
          )}
        >
          {formatDateTime(new Date(time))}
        </p>
      </div>
    </div>
  )
}
