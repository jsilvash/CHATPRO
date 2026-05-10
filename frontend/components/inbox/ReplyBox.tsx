"use client"

import { useState } from "react"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

interface ReplyBoxProps {
  onSend: (text: string) => Promise<void>
  disabled?: boolean
}

export function ReplyBox({ onSend, disabled }: ReplyBoxProps) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)

  async function handleSend() {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setSending(true)
    try {
      await onSend(trimmed)
      setText("")
    } finally {
      setSending(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-zinc-200 dark:border-zinc-700 p-4 bg-white dark:bg-zinc-900">
      <div className="flex gap-2 items-end">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Escribe un mensaje... (Ctrl+Enter para enviar)"
          className="flex-1 resize-none min-h-[72px] max-h-40"
          disabled={disabled || sending}
        />
        <Button
          onClick={handleSend}
          disabled={!text.trim() || sending || disabled}
          size="icon"
          className="h-9 w-9 shrink-0"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
