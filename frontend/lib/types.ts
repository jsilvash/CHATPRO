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

// Conectores
export interface ConnectorDefOut {
  id: string
  name: string
  kind: string
  version: string
  enabled: boolean
}

export interface ConnectorConfigOut {
  id: string
  tenant_id: string
  connector_def_id: string
  connector_name: string | null
  display_name: string
  status: string
  last_full_sync_at: string | null
  last_incremental_sync_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface ConnectorStatsOut {
  last_full_sync_at: string | null
  last_incremental_sync_at: string | null
  last_error: string | null
  status: string
  products_count: number
  orders_count: number
}

export interface SearchResultOut {
  id: string
  external_id: string
  name: string
  price: number | null
  url: string | null
  score: number
}

export interface SearchResultsOut {
  results: SearchResultOut[]
  total: number
}

// Canned responses
export interface CannedResponseOut {
  id: string
  tenant_id: string
  shortcode: string
  text: string
  variables: string[]
  created_by_user_id: string | null
  created_at: string
  updated_at: string
}

export interface CannedResponseListOut {
  items: CannedResponseOut[]
  total: number
  page: number
  page_size: number
}

export interface RenderOut {
  id: string
  shortcode: string
  original_text: string
  rendered_text: string
  variables_used: Record<string, string>
  variables_missing: string[]
}

// WA Numbers
export interface WaNumberResponse {
  id: string
  tenant_id: string
  label: string
  waha_session_name: string
  waha_node_id: string | null
  phone: string | null
  tags: string[]
  is_default: boolean
  active: boolean
  created_at: string
  session_status: string
}

export interface WaNumberListResponse {
  items: WaNumberResponse[]
  total: number
}

export interface TopContactEntry {
  phone: string
  count: number
}

export interface WaNumberMetricsOut {
  wa_number_id: string
  date_from: string
  date_to: string
  messages_in: number
  messages_out: number
  conversations_total: number
  conversations_active: number
  top_contacts: TopContactEntry[]
}

// Personas
export interface PersonaResponse {
  id: string
  tenant_id: string
  name: string
  system_prompt: string
  tone: string
  locale: string
  timezone: string
  out_of_hours_message: string
  business_hours_json: Record<string, unknown>
  model_id: string
  locale_secondary: string[]
  auto_detect_locale: boolean
}

export interface PersonaListResponse {
  items: PersonaResponse[]
  total: number
}

// Inbox search
export interface MessageSearchResult {
  id: string
  conversation_id: string
  direction: "in" | "out"
  text: string
  created_at: string
  context_before: { id: string; direction: string; text: string; created_at: string }[]
  context_after: { id: string; direction: string; text: string; created_at: string }[]
}

export interface SearchResponse {
  items: MessageSearchResult[]
  total: number
}

// Métricas agente por período
export interface AgentMetricsOut {
  user_id: string
  date_from: string
  date_to: string
  conversations_handled: number
  avg_first_response_sec: number | null
  avg_resolution_sec: number | null
  messages_sent: number
  notes_created: number
  busiest_hour: number | null
}

// Office hours
export interface OfficeHoursOut {
  id: string
  tenant_id: string
  wa_number_id: string | null
  day_of_week: number
  hour_start: number
  hour_end: number
  is_active: boolean
  out_of_hours_message: string
  created_at: string
  updated_at: string
}

export interface OfficeHoursListOut {
  items: OfficeHoursOut[]
  total: number
}

// Status history
export interface ConversationStatusHistoryEntry {
  id: string
  old_status: string | null
  new_status: string
  changed_by_user_id: string | null
  changed_at: string
}

// Métricas SSE
export interface StreamMetrics {
  messages_in_today: number
  messages_out_today: number
  conversations_active: number
  llm_cost_cents_today: number
  timestamp: string
}

// Knowledge Base
export interface KbDocumentOut {
  id: string
  tenant_id: string
  wa_number_id: string | null
  title: string
  source_type: string
  source_uri: string | null
  status: string
  error: string | null
}

// API Keys
export interface ApiKeyOut {
  id: string
  tenant_id: string
  name: string
  prefix: string
  scopes: string[]
  last_used_at: string | null
  revoked_at: string | null
  created_by_user_id: string | null
  created_at: string
}

// Webhooks salientes
export interface WebhookOutItem {
  id: string
  tenant_id: string
  url: string
  events: string[]
  enabled: boolean
  consecutive_failures: number
  last_success_at: string | null
  last_failure_at: string | null
  created_at: string
}
