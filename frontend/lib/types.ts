export interface ConversationSummary {
  id: string
  tenant_id: string
  wa_number_id: string
  wa_contact_phone: string
  wa_contact_name: string
  status: "bot" | "agent" | "waiting_agent" | "closed"
  assigned_user_id: string | null
  last_message_at: string | null
  turn_count: number
  ai_summary: string | null
  first_response_at: string | null
  resolved_at: string | null
  tags: string[]
  notes_count: number
  created_at: string
}

export interface ConversationListResponse {
  items: ConversationSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface MessageOut {
  id: string
  direction: "in" | "out"
  text: string
  ack: string
  sent_at: string | null
  created_at: string
}

export interface NoteOut {
  id: string
  tenant_id: string
  wa_conversation_id: string
  user_id: string
  text: string
  created_at: string
}

export interface ConversationDetail {
  conversation: ConversationSummary
  messages: MessageOut[]
  tool_invocations: unknown[]
  handoff_events: unknown[]
}

export interface TagOut {
  id: string
  tag: string
  created_by_user_id: string | null
  created_at: string
}
