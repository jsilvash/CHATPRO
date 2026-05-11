import { ConversationView } from "@/components/inbox/ConversationView"
import { InboxList } from "@/components/inbox/InboxList"

interface Props {
  params: Promise<{ conv_id: string }>
}

export default async function ConversationPage({ params }: Props) {
  const { conv_id } = await params

  return (
    <div className="flex h-full">
      {/* Panel izquierdo: lista */}
      <aside className="w-80 shrink-0 border-r border-zinc-200 dark:border-zinc-700 flex flex-col h-full bg-white dark:bg-zinc-900">
        <InboxList />
      </aside>
      {/* Panel central + derecho */}
      <ConversationView convId={conv_id} />
    </div>
  )
}
