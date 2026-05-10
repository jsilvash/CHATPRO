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

// Dashboard métricas
export interface TenantMetricsDashboard {
  conversations_total: number
  conversations_active: number
  conversations_bot: number
  conversations_closed_today: number
  messages_in_today: number
  messages_out_today: number
  agents_online: number
  unassigned_waiting: number
}

// Usuarios
export interface UserOut {
  id: string
  tenant_id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
  created_at: string
}

export interface UserListResponse {
  items: UserOut[]
  total: number
  page: number
  page_size: number
}

export interface UserStats {
  user_id: string
  conversations_active: number
  conversations_today: number
  avg_first_response_sec: number | null
  notes_count: number
}

export interface AvailableAgent {
  id: string
  email: string
  full_name: string
  role: string
  conv_count: number
}

export interface AvailableAgentsResponse {
  items: AvailableAgent[]
  total: number
}

export interface DeactivateUserResponse {
  deactivated_user_id: string
  reassigned_conversations: number
  new_assignee_id: string | null
}

// Contactos
export interface ContactSearchOut {
  id: string
  phone_e164: string
  display_name: string | null
  email: string | null
  conversations_count: number
  created_at: string
}

export interface ContactOut {
  id: string
  tenant_id: string
  phone_e164: string
  display_name: string | null
  first_name: string | null
  last_name: string | null
  email: string | null
  locale: string | null
  opt_in_marketing: boolean
}

export interface FactOut {
  id: string
  key: string
  value_text: string | null
  value_type: string
  source: string
  confidence: number | null
}

// SLA (formato plano del backend)
export interface SLAReport {
  date_from: string
  date_to: string
  total_conversations: number
  resolved_conversations: number
  avg_first_response_seconds: number | null
  avg_resolution_seconds: number | null
  p50_first_response_seconds: number | null
  p90_first_response_seconds: number | null
  p50_resolution_seconds: number | null
  p90_resolution_seconds: number | null
}
