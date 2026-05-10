import { InboxList } from "@/components/inbox/InboxList"

export default function InboxPage() {
  return (
    <div className="flex h-full">
      {/* Panel izquierdo: lista de conversaciones */}
      <aside className="w-80 shrink-0 border-r border-zinc-200 dark:border-zinc-700 flex flex-col h-full bg-white dark:bg-zinc-900">
        <InboxList />
      </aside>
      {/* Placeholder cuando no hay conversación seleccionada */}
      <div className="flex-1 flex items-center justify-center text-zinc-400 text-sm">
        Selecciona una conversación
      </div>
    </div>
  )
}
