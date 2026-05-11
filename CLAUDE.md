# CHATPRO — Instrucciones del Proyecto

> **Para sesiones nuevas:** Leer primero "Contexto rápido" y el MD de la fase activa. Nada más.

---

## Contexto rápido del sistema

ChatPro es una plataforma SaaS **multi-tenant** de WhatsApp Hub. Permite a N tenants independientes gestionar N números de WhatsApp con agentes de IA, memoria de contactos, conectores de e-commerce (WooCommerce primero, Shopify después) y base de conocimiento RAG. Cada tenant tiene su propio panel, números, agentes, catálogo y métricas.

### Stack
- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2.0 (sync) + Alembic
- **BD:** PostgreSQL 16 + pgvector (Fase 5+)
- **Cache / Colas:** Redis 7 + Celery 5
- **Frontend:** Next.js 16 + TypeScript + shadcn/ui + TanStack Query v5 + Zustand (Fase F2+)
- **IA:** Anthropic Claude (Sonnet/Haiku según costo)
- **WhatsApp:** WAHA Plus (ÚNICO transporte — sin Cloud API, sin Evolution, sin templates Meta)

### Fases completas

| Fase | Título | Estado |
|---|---|---|
| 0 | Skeleton + multi-tenant + auth + aislamiento | ✅ Mergeada a main |
| 1 | Conexión WAHA de un número + webhook + persistencia | ✅ Mergeada a main |
| 2 | Bot Claude con persona/locale/tono | ✅ Mergeada a main |
| 3 | Memoria corto plazo (últimos N turnos + resumen) | ✅ Mergeada a main |
| 4 | Memoria largo plazo (hechos del contacto) | ✅ Mergeada a main |
| 5 | Connector ABC + WooCommerce sync full | ✅ Mergeada a main |
| 6 | Sync incremental WooCommerce + embeddings | ✅ Mergeada a main |
| 7 | Tools del agente (catálogo, stock, órdenes) | ✅ Mergeada a main |
| 8 | Inbox + handoff humano | ✅ Mergeada a main |
| 9 | RAG genérico (PDF/URL) | ✅ Mergeada a main |
| 10 | API pública + webhooks salientes | ✅ Mergeada a main |
| 11 | Billing + métricas + cuotas | ✅ Mergeada a main |
| 12 | Shopify connector (validación interfaz) | ✅ Mergeada a main |
| 16 (retomo) | Fix fallos pre-existentes test_billing + test_knowledge | ✅ Mergeada a main |
| 18 | Shopify connector completo + búsqueda semántica en agente | ✅ Mergeada a main |
| 19 | Connector API REST completa (CRUD + PATCH + webhook-info + sync-incr + orders) | ✅ Mergeada a main |
| 20 | historial_pedidos_contacto real (orders reales + match email/phone + Shopify) | ✅ Mergeada a main |
| 21 | Webhooks salientes conector + panel métricas + anti-hallucination logging | ✅ Mergeada a main |
| 22 | Fix pre-existentes + rate limit + search semántico + hallucination_flag | ✅ Mergeada a main |
| 23 | WebSocket inbox + Export GDPR + i18n personas + métricas SSE | ✅ Mergeada a main |
| 24 | Webhooks conversación + métricas WaNumber + búsqueda inbox + resumen al cerrar | ✅ Mergeada a main |
| 25 | SLA panel + tags conversaciones + canned responses + notificaciones WS | ✅ Mergeada a main |
| 26 | Auto-asignación round-robin + notas internas + historial status + templates variables | ✅ Mergeada a main |
| 27 | Filtros inbox + bulk actions + stats usuario + webhook events notas | ✅ Mergeada a main |
| 28 | Dashboard métricas + deactivate user + export CSV + búsqueda contactos | ✅ Mergeada a main |
| 29 | Office hours + waiting time + menciones notas + métricas agente | ✅ Mergeada a main |
| F1 | Discovery frontend (arquitectura, decisiones, stack) | ✅ Completada |
| F2 | Frontend Next.js: Login + Inbox list + Inbox detalle + WebSocket | ✅ PR abierto |
| F3 | Frontend: Dashboard operativo + gestión usuarios + contactos + SLA | ✅ PR abierto |
| F4 | Frontend: Conectores + canned responses + métricas SSE | ✅ Mergeada a main |
| F5 | Frontend: Números WA + personas IA + filtros inbox avanzados | ✅ PR abierto |
| F6 | Frontend: Office hours + asignación persona WA + métricas agente + historial status | ✅ PR abierto |
| F7 | Frontend: Bulk actions inbox + agentes mejorado + office hours grid + overdue UX | ✅ PR abierto |
| F8 | Frontend: Panel KB + API keys/webhooks + contacto mejorado + inbox menciones/canned | ✅ PR abierto |
| F9 | Frontend: GDPR export + billing/uso + KB stats + toasts + Command palette Cmd+K | ✅ Mergeada a main |

### Plan completo
Ver `WHATSAPP_HUB_PLAN.md` en la raíz (1300+ líneas, todos los detalles de arquitectura).

---

## Reglas de multi-tenancy (CRÍTICAS)

1. **`tenant_id` en TODA tabla operativa.** Sin excepción. Índice obligatorio.
2. **Nunca** exponer datos de un tenant a otro bajo ninguna ruta.
3. `tenant_scope(tenant_id)` se establece en `get_current_user` (dependencia FastAPI). Todos los handlers que necesiten la BD ya tienen el contexto.
4. `bypass_tenant_filter()` solo en: webhook handlers (antes de resolver tenant), startup hooks, lookup global por slug/email. **Nunca** en endpoints de negocio.
5. `get_current_tenant_id()` lanza `RuntimeError` si se llama sin contexto. Esto es intencional — falla visible mejor que fuga silenciosa.
6. Tests de aislamiento obligatorios: list / get-by-id / mutate / search / tenant-get.

### Transporte WAHA (Fase 1+)
- Un solo `connection_type = "waha"`. No hay QR-Evolution ni Cloud API.
- Campo del modelo: `waha_session_name` (no `evolution_instance_name`).
- `ensure_waha_webhooks()` corre en cada arranque del backend (WAHA no persiste webhooks).
- Eventos webhook: `["message","message.any","message.ack","session.status"]` — los cuatro, siempre.
- LID→PN: 4 capas + cache + lock. ABORT si no resuelve (nunca `@lid` como chatId).

---

## Estructura del proyecto

```
src/
├── main.py              — FastAPI app + lifespan
├── config.py            — Settings (pydantic-settings, lru_cache)
├── db/
│   ├── base.py          — Base + TimestampMixin
│   ├── models.py        — Todos los modelos SQLAlchemy
│   └── session.py       — engine, get_db dependency
├── tenancy/
│   └── context.py       — tenant_scope, bypass_tenant_filter, get_current_tenant_id
├── auth/
│   ├── passwords.py     — hash_password, verify_password (bcrypt)
│   ├── tokens.py        — create_access_token, create_refresh_token, decode_token (PyJWT)
│   └── dependencies.py  — get_current_user, require_role
├── messaging/           — waha_client, lid_resolver, wa_lookup, dispatcher, ack, typing_state, webhook
├── wa/                  — modelos WA (WaNumber/WaSession/WaConversation/WaMessage) + api REST
├── utils/               — phone (normalize)
├── contacts/            — modelos Contact/ContactFact + API REST (Fase 4+)
├── celery_app.py        — Celery app instance (broker Redis, autodiscover tasks)
├── connectors/          — ABC Connector, crypto AES-GCM, registry, WooCommerce (Fases 5-6)
│   └── woocommerce/     — WooCommerceConnector completo + tasks.py (embed_product) + webhook.py
├── agent/               — (Fase 2+) service, prompt_builder, llm, facts_extractor, summarizer, tool_runner (Fase 7)
├── inbox/               — (Fase 8) HandoffEvent + API REST /inbox (list/get/take/reply/close)
├── knowledge/           — (Fase 9) KbDocument/KbChunk, ingestor PDF/URL, búsqueda RAG híbrida, API /knowledge
├── public_api/          — (Fase 10) ApiKey, WebhookOut, WebhookDelivery; CRUD /v1/api-keys y /v1/webhooks; dispatcher con reintentos
└── api/
    └── v1/
        ├── router.py    — incluye todos los routers v1
        ├── auth.py      — /auth/login, /auth/refresh, /auth/logout
        ├── tenants.py   — /tenants (POST public), /tenants/me, /tenants/{id}
        ├── users.py     — /users CRUD (tenant-scoped)
        └── me.py        — /me
tests/
├── conftest.py          — fixtures: db (con rollback), client, tenant_a/b, client_a/b
├── test_isolation.py    — 5 escenarios de aislamiento + sanity checks
├── test_ack.py          — 16 transiciones ACK + timestamps + failed terminal
├── test_waha_lid.py     — LID ABORT(-2), cache, capas de resolución
├── test_wa_api.py           — REST WaNumber: alta, send, QR, isolation
├── test_waha_webhook.py     — eventos message/ack/session.status, dedupe, X-WAHA-Token
├── test_facts_extractor.py  — extractor hechos: parse, get_or_create, upsert, idempotencia
├── test_contacts_api.py     — REST contacts: CRUD, facts upsert/delete, isolation
├── test_connectors.py       — cifrado AES-GCM, configure/test/sync_full, API, aislamiento
├── test_woo_incremental.py  — HMAC, webhook handler, embed_product task, search híbrida, aislamiento (Fase 6)
├── test_inbox_api.py        — inbox REST + bot mudo + escalar_a_humano + isolation
├── test_knowledge.py        — chunking, ingestión PDF/URL, búsqueda híbrida, tool agente, API, aislamiento
├── test_public_api.py       — CRUD api-keys/webhooks, authn por token, delivery, reintentos, dead letter, aislamiento
├── test_historial_pedidos.py — historial_pedidos_contacto real: email/phone/contact_id, aislamiento, Shopify (Fase 20)
├── test_fase24_conv_webhooks.py — webhooks de conversación: message.received/sent, conversation.created/status_changed, aislamiento
├── test_fase24_wa_metrics.py    — métricas WaNumber: messages_in/out, conversations, top_contacts, filtros fecha, aislamiento
├── test_fase24_inbox_search.py  — búsqueda full-text inbox: match, contexto ±2, filtros, 400 sin q, aislamiento
└── test_fase24_summary_on_close.py — resumen al cerrar: disparo tarea, skip ≤5 turnos, LLM mock, ai_summary en GET
```

> **Nota fase-0:** `src/main.py` agrega `tenant_context_middleware` que setea
> `_tenant_id_var` en el contexto asyncio del request. Sin esto, ContextVar
> no propaga entre la dependencia `get_current_user` (un threadpool worker)
> y el endpoint (otro worker distinto). No tocar `src/auth/` ni `src/tenancy/`.

---

## Ciclo de trabajo por fases (REGLA CRÍTICA)

**Una fase = una sesión = un PR chico = un merge = un QA en prod = siguiente fase.**

No se agrupan fases. No se mezclan cambios de fases distintas en la misma rama. Si durante una fase aparece algo de otra fase, se anota en el MD correspondiente — no se mete en la rama actual.

### Discovery antes de código

Al empezar una fase nueva:
1. Leer SOLO el MD de esa fase + estado del repo. Nada más.
2. Si el MD no existe, escribirlo primero con opciones A/B/C + recomendación y esperar OK.
3. Si el MD existe pero el repo no matchea, hacer discovery corto y actualizar el MD.

### Handoff entre sesiones

Al cerrar cada sesión (después de mergear y actualizar estado), generar el **prompt de arranque de la próxima**:

```
Retomo Fase X — <título>. Contexto:
- Rama activa: <nombre> (estado: <limpia / con commit WIP>)
- Main acaba de mergear PR #<n>.
- Primero: <comandos de checkout, rebase, etc.>
- Después: leer SOLO <archivos>. Ejecutar los N pendientes:
  1. ...
- Finalizar: <commit/push/PR/QA>.
- NO tocar: <cosas fuera de alcance>.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```

---

## Reglas de trabajo multi-agente

| Zona | Carpetas | Descripción |
|---|---|---|
| **Backend Core** | `src/db/`, `src/tenancy/`, `src/auth/`, `src/config.py` | Modelos, sesión, auth, tenancy |
| **Transporte WA** | `src/messaging/`, `src/wa/` | Cliente WAHA, modelos WA, webhook |
| **Agente IA** | `src/agent/` | Claude, tools, memoria, prompt builder |
| **Conectores** | `src/connectors/` | WooCommerce, Shopify, RAG, CRM |
| **API** | `src/api/` | Endpoints REST |
| **Frontend** | `frontend/` | Next.js app (Fase 8+) |
| **Infra** | `alembic/`, `tests/`, `scripts/`, `docker-compose.yml` | DB, tests, DevOps |

**Archivos compartidos — modificar con autorización previa:**
- `src/db/models.py` (agregar modelos nuevos está bien; cambiar existentes requiere aviso)
- `pyproject.toml` (agregar deps)
- `.env.example`
- `src/main.py`

### Commits
- Formato: `[zona] descripción` — ej: `[transporte] portar waha_client de FitnessIA`
- No hacer push a main sin autorización explícita del usuario.
- No usar `--no-verify` salvo que el usuario lo pida explícitamente.

### Idioma
Todo en español: código, UI, comentarios, commits, docs. Sin excepción.

---

## Decisiones de arquitectura tomadas (no reabrir sin razón)

| Decisión | Elección | Ver en plan |
|---|---|---|
| Vector DB | pgvector (migrar a Qdrant si > 10M vectores) | §5 |
| Aislamiento WAHA | Una instancia compartida, sharding cuando escale | §4 |
| Frontend | Next.js 15 + TypeScript | §3 |
| Modelo embedding | Voyage AI voyage-3 (1024 dims) | §5 |
| Connector interface | ABC con `configure/sync_full/sync_incremental/webhook_handler/expose_tools/search` | §7 |
| Cifrado credenciales | AES-GCM con DEK por config, KEK desde `CONNECTOR_MASTER_KEY` env var | §11 |
| DB session inyectada | Conector recibe `db=` opcional; si no, abre `get_db_session()` propio. Clave para tests. | Fase 5 |
| Auth | JWT stateless (access 1h, refresh 7d) | §3 |
| Embedding en products | Columna `embedding VECTOR(1024)` directamente en tabla `products` (nullable). No tabla separada. | Fase 6 |
| Webhook WooCommerce | Auth por HMAC-SHA256 + base64 en `X-WC-Webhook-Signature`. Endpoint usa `Depends(get_db)` para que TestClient inyecte DB de test. | Fase 6 |
| Celery tasks | `src/celery_app.py` centraliza la instancia. Tasks importan deps a nivel de módulo para facilitar mock en tests. | Fase 6 |
| get_settings en connectors | `get_settings` se importa a nivel de módulo (no dentro de funciones) en connectors que lo usan, para que `patch("src.connectors.X.connector.get_settings")` funcione en tests. | Fase 18 |
| Tool names semánticos | `expose_tools()` usa nombres sin prefijo de proveedor: `buscar_productos`, `consultar_stock_y_precio`. El agente no sabe si hay Woo o Shopify detrás. | Fase 18 |
| buscar_productos semántico | `buscar_productos` en ambos tools.py llama `connector.search()` (pgvector + keyword + RRF) en lugar de ILIKE directo. | Fase 18 |
| Webhook Shopify | Auth por HMAC-SHA256 + base64 en `X-Shopify-Hmac-Sha256`. Topic en `X-Shopify-Topic`. Endpoint `/webhooks/shopify/{tenant_id}/{config_id}`. | Fase 18 |
| Connector API genérica | `POST /configure` acepta `{"credentials": {...}}` genérico; cada conector valida sus propios campos requeridos. NO usar `ConnectorCredentials` con campos fijos. | Fase 19 |
| connector_name en respuesta | `ConnectorConfigOut` incluye `connector_name` (enriquecido via JOIN a `ConnectorDef`). Helper `_enrich_config(config, db)`. | Fase 19 |
| Webhook info endpoint | `GET /v1/connector-configs/{id}/webhook-info` devuelve `{webhook_url, webhook_secret, connector_name}`. URL construida con `settings.public_base_url`. | Fase 19 |
| historial_pedidos_contacto real | Tabla `orders` existente (Fase 6). Match por `customer_email` OR `customer_phone`. `contact_id` pasa en `extra_kwargs` del tool_runner para resolver Contact. Shopify reutiliza la implementación de Woo (misma tabla). Migración 0015 agrega `customer_phone` y `placed_at` a `orders`. | Fase 20 |
| Webhooks salientes de conectores | `_dispatch_event(event, payload)` privado en ambos connectors. Llama `emit_event()` del dispatcher existente (Fase 10) con la sesión DB interna del conector. `webhook_handler` separa `order.created` vs `order.updated` y `orders/create` vs `orders/updated`. No hay tabla nueva. | Fase 21 |
| Stats endpoint conector | `GET /v1/connector-configs/{id}/stats` calcula products_count y orders_count con COUNT queries filtradas por tenant_id + connector_config_id. Schema `ConnectorStatsOut`. Aislamiento multi-tenant verificado. | Fase 21 |
| Anti-hallucination logging completo | `run_agent_turn` loggea warning con `conversation_id`, `response_text[:300]` y `tool_results` completos cuando `hallucination_flag=True`. Sin migración de BD — solo log. | Fase 21 |
| contact_id en WaConversation | `WaConversation.contact_id` columna FK nullable a `contacts.id`. Migración 0016. Necesaria para `tool_runner.collect_tools_for_conversation` que la pasa como `extra_kwarg` a los conectores. | Fase 22 |
| Rate limiting sliding window | `src/agent/rate_limiter.py`: ventana in-memory (collections.deque) por `(tenant_id, wa_contact_phone)`. Fallback Redis si `REDIS_URL` configurado. Límites en Settings: `RATE_LIMIT_MESSAGES=10`, `RATE_LIMIT_WINDOW_SECONDS=60`. Inyectado al inicio de `respond()` — retorno silencioso si superado. | Fase 22 |
| Search endpoint público | `GET /v1/connector-configs/{id}/search?q=<query>&max_results=5` llama `connector.search()` existente. Schemas `SearchResultOut` y `SearchResultsOut` en `src/connectors/api.py`. Requiere auth, aislamiento por tenant_id. | Fase 22 |
| hallucination_flag persistido | `WaMessage.hallucination_flag Boolean default False` (migración 0017). `_run_agent_loop()` retorna 4-tupla con el flag. `_persist_outbound()` acepta y persiste el flag en el mensaje de salida. | Fase 22 |
| WebSocket inbox tiempo real | `src/messaging/ws_manager.py`: ConnectionManager con dict{conv_id→set[WS]}. Broadcast async + `broadcast_from_sync()` via `run_coroutine_threadsafe`. Endpoint `/ws/inbox/{conv_id}?token=<jwt>` autenticado. Integrado en `_persist_outbound()` y en `inbox/api.py::reply_conversation()`. `set_main_loop()` en lifespan startup. | Fase 23 |
| ExportJob GDPR | `src/billing/models.ExportJob` (migración 0018): id/tenant_id/status/error/storage_uri/created_at/finished_at. Tarea Celery `billing.export_tenant_data(job_id)`: exporta contacts+wa_messages+orders+contact_facts a CSV, ZIP en memoria, sube a S3/MinIO. Endpoints: POST /v1/tenants/me/export → 202, GET status, GET download (URL firmada TTL 15min). Aislamiento por tenant_id. `boto3>=1.35.0` en deps. | Fase 23 |
| i18n en Persona | `Persona.locale_secondary ARRAY(Text) default []` + `auto_detect_locale Boolean default False` (migración 0019). `build_system_prompt()` añade bloque IDIOMA al final si `auto_detect_locale=True`. CRUD expone campos en PersonaCreate/PersonaUpdate/PersonaResponse. | Fase 23 |
| Métricas SSE | `GET /v1/metrics/stream` → `text/event-stream`. Auth flexible: Bearer header O `?token=<jwt>` (helper `_resolve_sse_user`). Emite primer evento inmediatamente; luego cada `METRICS_STREAM_INTERVAL_S` seg (Settings, default 30). Payload: messages_in_today, messages_out_today, conversations_active, llm_cost_cents_today, timestamp. Queries sobre `usage_metrics` y `wa_conversations`. CancelledError cierra silenciosamente. | Fase 23 |
| S3 config en Settings | `s3_bucket_name`, `s3_endpoint_url`, `s3_access_key`, `s3_secret_key`, `s3_region` en Settings. `src/billing/storage.py`: `upload_bytes()` + `generate_presigned_url()`. Abstracción mockeable en tests. | Fase 23 |
| metrics_stream_interval_s | `METRICS_STREAM_INTERVAL_S: int = 30` en Settings. Controla el intervalo de emisión SSE. | Fase 23 |
| Webhooks de conversación | `emit_event()` (Fase 10) reutilizado en: `messaging/webhook.py` (message.received + conversation.created), `agent/service._persist_outbound()` (message.sent bot), `inbox/api.reply_conversation()` (message.sent agente), `inbox/api.take/close_conversation()` (conversation.status_changed). Fire-and-forget con try/except. `_get_or_create_conversation` devuelve `(conv, is_new: bool)` para emitir solo en conv nueva. | Fase 24 |
| Métricas WaNumber | `GET /v1/wa-numbers/{id}/metrics` con params `date_from`/`date_to` (default hoy-30d/hoy). COUNT sobre `wa_messages` y `wa_conversations` filtrados por `wa_number_id` + tenant. `top_contacts`: top 5 convs por mensajes entrantes, resuelto a phone via JOIN en Python. Schema `WaNumberMetricsOut` + `TopContactEntry` en `src/wa/api.py`. | Fase 24 |
| Búsqueda full-text inbox | `GET /v1/inbox/search?q=` usa `body_tsv` GENERATED ALWAYS (migración 0020, GIN index). Columna referenciada via `column("body_tsv", TSVECTOR)` (no en ORM). `plainto_tsquery('spanish', q)`. Contexto ±2 por subqueries `created_at < msg` desc limit 2 + `created_at > msg` asc limit 2. 400 si q vacío o ausente. | Fase 24 |
| Resumen al cerrar (Celery) | `src/agent/tasks.summarize_on_close(conv_id)` Celery task. Disparada en `close_conversation` si `conv.turn_count > 5`. Usa Claude Haiku (`claude-haiku-4-5-20251001`). Persiste en `WaConversation.ai_summary` (columna existente desde Fase 3). `ConversationSummary` y `ConversationDetail` exponen `ai_summary`. `src.agent` añadido al `autodiscover_tasks`. | Fase 24 |
| SLA timestamps | `WaConversation.first_response_at` (DateTime nullable, migración 0021): se setea en `_persist_outbound()` (bot) y `reply_conversation()` (agente), solo si es NULL. `WaConversation.resolved_at`: se setea en `close_conversation()`. `GET /v1/inbox/sla-report?date_from=&date_to=` devuelve avg/p50/p90 de primera respuesta y resolución, filtrado por tenant + rango de fechas. | Fase 25 |
| Tags en conversaciones | Tabla `conversation_tags` (migración 0022): tenant_id, wa_conversation_id FK, tag VARCHAR(64), created_by_user_id FK nullable, created_at. Unique (wa_conversation_id, tag). Tags normalizadas a minúsculas. `POST /v1/inbox/{conv_id}/tags` → 201/200 idempotente. `DELETE /v1/inbox/{conv_id}/tags/{tag}` → 204. `GET /v1/inbox?tag=xxx` filtra por subquery. `ConversationSummary.tags: list[str]` cargado via `_load_tags()` batch query. Aislamiento por tenant_id en toda query. | Fase 25 |
| Canned responses | Tabla `canned_responses` (migración 0023): tenant_id, shortcode VARCHAR(64), text, created_by_user_id, timestamps. Unique (tenant_id, shortcode). CRUD completo en `src/inbox/canned_api.py` bajo `/v1/canned-responses`. PATCH actualiza `updated_at` manualmente. El mismo shortcode puede existir en tenants distintos. Router registrado en `src/api/v1/router.py`. | Fase 25 |
| NotificationManager WS | `src/messaging/ws_manager.NotificationManager`: dict{tenant_id→set[WebSocket]}, misma arquitectura que `ConnectionManager`. Singleton `notification_manager`. `broadcast_from_sync()` usa `run_coroutine_threadsafe` igual que manager. Endpoint `/ws/notifications/{tenant_id}?token=<jwt>` en `ws_router.py`. Broadcast de `conversation.waiting_agent` en `_auto_escalate_if_needed()` (service.py). Schema: `{event, conversation_id, wa_contact_phone, tenant_id, timestamp ISO8601}`. | Fase 25 |
| Auto-asignación round-robin | `_find_agent_with_least_load(db, tenant_id)` en `service.py`: filtra User con role in (agent,admin) + is_active=True. Cuenta convs activas (status agent|waiting_agent) por assigned_user_id. Retorna el user_id del mínimo. Llamado en `_auto_escalate_if_needed()` después de crear HandoffEvent. Si no hay agentes → deja `assigned_user_id=None`. `GET /v1/users/available-agents` en `src/api/v1/users.py` (declarado ANTES de `/{user_id}` para evitar conflicto de ruta). Responde con `items: [{id,email,full_name,role,conv_count}], total`. Ordenado por conv_count ASC. | Fase 26 |
| Notas internas en conversaciones | Tabla `conversation_notes` (migración 0024): tenant_id, wa_conversation_id FK, user_id FK, text, created_at. Index (tenant_id, wa_conversation_id). `POST /v1/inbox/{conv_id}/notes` → 201. `GET` → lista ASC. `DELETE /{note_id}` → 204 solo si `note.user_id == current_user.id`, sino 403. `ConversationSummary.notes_count: int` cargado via `_load_notes_count()` batch COUNT query. Incluido en list, get, close. | Fase 26 |
| Historial de status de conversación | Tabla `conversation_status_history` (migración 0025): tenant_id, wa_conversation_id FK, old_status, new_status, changed_by_user_id FK nullable, changed_at. Helper `_record_status_change()` en `inbox/api.py`. Llamado en: `take_conversation` (waiting_agent→agent), `close_conversation` (X→bot), `reply_conversation` (waiting_agent→agent si promueve). `_auto_escalate_if_needed` en `service.py` registra bot→waiting_agent (changed_by_user_id=None). `GET /v1/inbox/{conv_id}/status-history` → lista ordenada por changed_at ASC. | Fase 26 |
| Templates con variables en canned responses | Variables: `{{nombre}}`, `{{producto}}` etc. Regex `_VAR_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")`. Validación `_validate_variables()` en create y update: 422 si variable mal formada. `CannedResponseOut.variables: list[str]` = lista de nombres de variables en el texto. Helper `from_orm_with_vars()` en schema. `GET /v1/canned-responses/{id}/render` acepta query params como variables, devuelve `{rendered_text, variables_used, variables_missing, original_text}`. `GET /v1/canned-responses/search?q=` busca case-insensitive en shortcode OR text (declarado antes de `/{canned_id}`). | Fase 26 |
| Filtros avanzados en inbox | `GET /v1/inbox` ahora acepta `assigned_user_id`, `date_from`, `date_to`, `search` (ILIKE en wa_contact_name OR wa_contact_phone). Paginación cambiada de `limit/offset` a `page/page_size` con `total_pages = ceil(total/page_size)` en la respuesta. `ConversationListResponse` añade `page`, `page_size`, `total_pages`. Aislamiento multi-tenant en todos los filtros vía `bypass_tenant_filter` + filtro explícito de `tenant_id`. | Fase 27 |
| Bulk actions en inbox | Tres endpoints declarados ANTES de `/{conv_id}` para evitar conflictos de routing: `POST /v1/inbox/bulk-assign` (valida que user_id sea del mismo tenant, 422 si no), `POST /v1/inbox/bulk-tag` (upsert idempotente de etiqueta), `POST /v1/inbox/bulk-close` (status→bot + registra ConversationStatusHistory). Convs de otro tenant se ignoran silenciosamente (no error). Respuesta uniforme `{updated: N, errors: []}`. | Fase 27 |
| Stats de usuario (agente) | `GET /v1/users/{user_id}/stats` declarado DESPUÉS de `/available-agents` y ANTES de `/{user_id}`. Accesible por admin/owner O el propio usuario. Calcula: `conversations_active` (status agent\|waiting_agent), `conversations_today` (status bot + resolved_at=hoy), `avg_first_response_sec` (avg de first_response_at - created_at, null si sin datos), `notes_count` (notas del usuario). 404 si el user_id no pertenece al tenant. | Fase 27 |
| Webhook events notas e historial | `note.created` emitido via `emit_event()` en `create_note` (inbox/api.py) después del commit. Payload: `{event, conversation_id, note_id, user_id, text_preview[:100], tenant_id}`. Fire-and-forget con try/except. `conversation.status_changed` emitido en `_auto_escalate_if_needed` (agent/service.py) usando la `db` ya existente del caller (no abre sesión nueva). | Fase 27 |
| Dashboard métricas operativas | `GET /v1/metrics/dashboard` (diferente a `/v1/metrics/summary` existente en billing que es histórico). Módulo `src/api/v1/metrics_dashboard.py` registrado en router. Schema `TenantMetricsDashboard`: conversations_total/active/bot/closed_today, messages_in/out_today, agents_online, unassigned_waiting. Requiere auth (cualquier rol). Aislamiento con `bypass_tenant_filter` + filtro explicit tenant_id. | Fase 28 |
| Deactivate user endpoint | `PATCH /v1/users/{user_id}/deactivate` en `src/api/v1/users.py`, declarado ANTES de `GET /{user_id}`. Solo admin/owner. 422 si autodesactivación. Reasigna convs activas vía `_find_agent_with_least_load()` (ya en service.py). Si no hay agentes → convs quedan `assigned_user_id=None`. Respuesta: `{deactivated_user_id, reassigned_conversations, new_assignee_id}`. | Fase 28 |
| Export CSV inbox | `GET /v1/inbox/export` declarado ANTES de `/{conversation_id}` (mismo patrón que /bulk-*). Acepta mismos filtros que GET /v1/inbox. Sin paginación. Content-Type text/csv. Columnas: id, wa_contact_name, wa_contact_phone, status, assigned_user_id, created_at, last_message_at, resolved_at, tags, notes_count. Tags separadas por `\|`. | Fase 28 |
| Búsqueda contactos | `GET /v1/contacts/search?q=&limit=` declarado ANTES de `/{contact_id}` en `src/contacts/api.py`. ILIKE en `phone_e164 OR display_name`. `conversations_count` calculado via COUNT en `WaConversation.contact_id`. Schema `ContactSearchOut`. Aislamiento por `current_user.tenant_id`. | Fase 28 |
| Office hours | Módulo `src/office_hours/` (models.py + service.py + api.py). Tabla `office_hours` (migración 0026): tenant_id, wa_number_id FK nullable, day_of_week (0=lunes), hour_start, hour_end, is_active, out_of_hours_message. Registros específicos del número tienen prioridad sobre globales (wa_number_id=None). Sin registros activos → bot siempre atiende. `check_office_hours()` usa `list(db.query(...).all())` para compatibilidad con MagicMock en tests. Inyectado al inicio de `agent/service.respond()`. CRUD en `/v1/office-hours`. | Fase 29 |
| Waiting since en conversaciones | `WaConversation.waiting_since` (DateTime nullable, migración 0027). Se setea en `_auto_escalate_if_needed()` al pasar a waiting_agent; se limpia en `take_conversation()` y `reply_conversation()` al pasar a agent. `ConversationSummary.waiting_minutes: int | None` calculado en `_conv_summary()`. `GET /v1/inbox?sort=waiting_time` ordena por `waiting_since ASC NULLS LAST`. `GET /v1/inbox/overdue?threshold_minutes=30` declarado ANTES de `/{conversation_id}`. | Fase 29 |
| Menciones en notas (@usuario) | Migración 0028 agrega `mentions JSONB default '[]'` a `conversation_notes`. `_MENTION_PATTERN = re.compile(r"@([\w.+-]+(?:@[\w.-]+)?)")` en `inbox/api.py`. `_resolve_mentions(text, tenant_id, db)` retorna `list[uuid.UUID]` buscando por email exacto o full_name case-insensitive (solo usuarios activos del tenant). Persiste como lista de strings UUID en JSONB. Notifica via `notification_manager.broadcast_from_sync(tenant_id, {event: "note.mention", ...})`. `NoteOut.mentions: list[uuid.UUID]`. | Fase 29 |
| Métricas de agente por período | `GET /v1/users/{user_id}/metrics?date_from=&date_to=` declarado ANTES de `/{user_id}` GET. Accesible por admin/owner O el propio usuario; 403 si agente ve a otro agente; 404 si user_id no pertenece al tenant. Calcula: `conversations_handled` (resolved_at en período), `avg_first_response_sec`, `avg_resolution_sec` (ambos en segundos float nullable), `messages_sent` (direction=out), `notes_created`, `busiest_hour` (hora 0-23 con más msgs enviados, nullable). Schema `AgentMetricsOut` en `src/api/v1/users.py`. | Fase 29 |
| Frontend stack | Next.js 16.2.x + TypeScript + Tailwind v4 + shadcn/ui (manual) + TanStack Query v5 + Zustand + jose. Directorio `frontend/` en raíz del repo. | Fase F2 |
| Auth frontend | Cookies httpOnly `chatpro_access` + `chatpro_refresh`. Next.js API Routes hacen proxy al backend. `proxy.ts` (guard de rutas, v16 renombró `middleware.ts`→`proxy.ts`). `cookies()` es async en v16. | Fase F2 |
| WS frontend | `useConversationSocket(convId)`: WebSocket a `/ws/inbox/{convId}?token=<jwt>`. Token obtenido de `/api/auth/ws-token` (Next.js API route que lee cookie httpOnly). Auto-reconexión tras 3s. | Fase F2 |
| Notificaciones WS | `useNotifications()`: WebSocket a `/ws/notifications/{tenant_id}?token=`. Decodifica tenant_id del JWT en cliente con `decodeJwt(jose)`. Badge de `waiting_agent` en nav. | Fase F2 |
| Inbox frontend | InboxList (filtros status/search/tags + paginación), ConversationCard, ConversationView (mensajes + acciones take/close/reply + panel tags+notas). `params` en pages son `Promise<{...}>` en Next.js 16 — deben ser awaited. | Fase F2 |
| Dashboard frontend | `app/(app)/dashboard/page.tsx`: client component, `apiGet("/v1/metrics/dashboard")` con auto-refresh `setInterval(30_000)`. Widgets con iconos lucide-react. Skeleton de carga con `animate-pulse`. | Fase F3 |
| Gestión usuarios frontend | `app/(app)/users/page.tsx`: lista con deactivate modal inline. `app/(app)/users/[user_id]/page.tsx`: detalle + stats (2 endpoints en paralelo con Promise.all). `app/(app)/users/agents/page.tsx`: carga de trabajo con LoadBar. | Fase F3 |
| Contactos frontend | `app/(app)/contacts/page.tsx`: búsqueda debounced (350ms) llamando `GET /v1/contacts/search`. `app/(app)/contacts/[contact_id]/page.tsx`: carga `GET /v1/contacts/{id}` + `GET /v1/contacts/{id}/facts` + lista inbox en paralelo; filtra convs por phone_e164. | Fase F3 |
| SLA frontend | `app/(app)/sla/page.tsx`: `GET /v1/inbox/sla-report?date_from&date_to` con filtros de fecha. Formato plano del backend: `avg_first_response_seconds`, `p50_first_response_seconds`, etc. Muestra total y resueltas con % de resolución. | Fase F3 |
| Nav F3 | `AppNav.tsx` añade rutas: `/users` (Users icon), `/contacts` (Phone icon), `/sla` (BarChart2 icon) con `isActive = pathname.startsWith(href)`. | Fase F3 |
| Panel conectores | `app/(app)/connectors/page.tsx`: lista con cards (status badge, display_name, connector_name). Modal `CreateModal` con selección de tipo (WooCommerce/Shopify) + form credenciales. DELETE con confirm. Navega a `/connectors/{id}`. | Fase F4 |
| Detalle conector | `app/(app)/connectors/[config_id]/page.tsx`: tabs Estadísticas / Buscar productos / Credenciales. StatsSection: products_count + orders_count + sync timestamps + botón sync. ProductSearchSection: form + resultados con score. ConfigureSection: form credenciales con campos tipo password para secrets. | Fase F4 |
| Canned responses | `app/(app)/canned-responses/page.tsx`: lista con búsqueda debounced 350ms. CannedForm inline para create/edit. PreviewModal con render de variables. Acciones por item: preview, editar, eliminar. Detección de variables `{{nombre}}` en textarea con chips inline. | Fase F4 |
| Dashboard SSE | `useMetricsSSE` hook en dashboard/page.tsx: EventSource a `/v1/metrics/stream?token=<jwt>`. Token obtenido de `/api/auth/ws-token`. onError: reconexión tras 10s. Actualiza `messages_in_today`, `messages_out_today`, `conversations_active` via overlay. Polling fallback cada 30s si SSE no conectado. Badge "En vivo" con `Radio` icon cuando SSE activo. | Fase F4 |
| Nav F4 | `AppNav.tsx` añade rutas: `/connectors` (Plug icon), `/canned-responses` (Zap icon). | Fase F4 |
| Panel números WA | `app/(app)/wa-numbers/page.tsx`: lista de WaNumbers con session_status badge (WORKING/STARTING/SCAN_QR_CODE/FAILED/STOPPED). Click navega a detalle. `app/(app)/wa-numbers/[id]/page.tsx`: card con label + phone + status. Filtro de fechas (date_from/date_to) aplicado on-demand. Métricas: messages_in/out, conversations_total/active, top_contacts. | Fase F5 |
| Personas IA | `app/(app)/personas/page.tsx`: lista + CRUD completo en modal (create/edit). Campos: name, system_prompt, tone (select), locale, locale_secondary (coma-separado), auto_detect_locale (checkbox), timezone, model_id (select). POST /v1/personas + PATCH /v1/personas/{id} + DELETE. | Fase F5 |
| Filtros avanzados inbox | `InboxList.tsx` añade panel colapsable (toggle SlidersHorizontal icon): assigned_user_id (select via /v1/users/available-agents), date_from, date_to. Botón export CSV (Download icon) que descarga GET /v1/inbox/export con filtros actuales como blob. Botón búsqueda full-text (Search icon) navega a `/inbox/search`. | Fase F5 |
| Búsqueda full-text inbox | `app/(app)/inbox/search/page.tsx`: input + botón buscar. GET /v1/inbox/search?q=. Muestra resultados con highlighting de palabras clave, contexto ±2 mensajes (context_before/context_after). Click en resultado navega a la conversación. | Fase F5 |
| Nav F5 | `AppNav.tsx` añade rutas: `/wa-numbers` (Smartphone icon), `/personas` (Bot icon). | Fase F5 |
| Asignación persona a WaNumber (frontend) | `/wa-numbers/[id]/page.tsx` incluye selector de persona (`GET /v1/personas` + `PATCH /v1/wa-numbers/{id}/persona`). Muestra persona actual. Persiste `persona_id` en `localStorage` (el backend no lo expone en `WaNumberResponse`). Botón "Guardar" con feedback de éxito. | Fase F6 |
| Office hours frontend | `app/(app)/office-hours/page.tsx`: CRUD completo. Tabla agrupada por día. Inline edit con fila expandible. Selector de número WA opcional (global si vacío). Campos: day_of_week (select), hour_start/hour_end (number inputs), is_active (checkbox), out_of_hours_message. CRUD via `/v1/office-hours`. | Fase F6 |
| Métricas agente por período (frontend) | `/users/[user_id]/page.tsx` añade sección "Métricas por período" con filtros date_from/date_to + botón "Calcular". Llama `GET /v1/users/{id}/metrics`. Muestra: conversations_handled, avg_first_response_sec, avg_resolution_sec, messages_sent, notes_created, busiest_hour. | Fase F6 |
| Historial de status (frontend) | `ConversationView.tsx` añade sección colapsable "Historial" en panel lateral. Carga `GET /v1/inbox/{id}/status-history` bajo demanda (enabled=showHistory). Muestra timeline con old_status → new_status + timestamp. | Fase F6 |
| Nav F6 | `AppNav.tsx` añade ruta: `/office-hours` (Clock3 icon). | Fase F6 |
| Bulk actions inbox (frontend) | `InboxList.tsx`: checkbox de selección múltiple en `ConversationCard` (modo bulk activado por toggle). Barra flotante cuando hay selección: bulk-assign (select agente), bulk-tag (input), bulk-close (confirm). POST /v1/inbox/bulk-assign, /bulk-tag, /bulk-close. | Fase F7 |
| Agentes mejorado (frontend) | `/users/agents`: filtros date_from/date_to + tabla comparativa con métricas. GET /v1/users/{id}/metrics en paralelo para todos los agentes. Toggle lista/tabla. | Fase F7 |
| Office hours grid semanal (frontend) | `/office-hours`: toggle List/LayoutGrid. Grid 7 días × 24 horas con celdas coloreadas (verde=cubierto, gris=sin cobertura). | Fase F7 |
| Overdue UX (frontend) | `useOverdueCount` hook: polling 60s a GET /v1/inbox/overdue?threshold_minutes=30. Badge rojo con "N!" en link /inbox del nav. Filtro "Solo vencidas" en InboxList. | Fase F7 |
| Nav F7 | `AppNav.tsx` no añade rutas nuevas en F7 (features dentro de páginas existentes). | Fase F7 |
| Panel Knowledge Base (frontend) | `app/(app)/knowledge/page.tsx`: lista documentos (GET /v1/knowledge), upload PDF (multipart a POST /v1/knowledge/upload), ingestión URL (mismo endpoint con campo url), DELETE /v1/knowledge/{id} con confirm. Estado: ready/processing/error con badge. | Fase F8 |
| Panel API Keys (frontend) | `app/(app)/api-keys/page.tsx`: lista (GET /v1/api-keys), crear (POST con name+scopes), revocar (DELETE). Token mostrado UNA sola vez tras crear, con botón de copy. Filtro incluir-revocadas. | Fase F8 |
| Panel Webhooks (frontend) | `app/(app)/webhooks/page.tsx`: lista (GET /v1/webhooks), crear (POST con url+events+secret), toggle enabled (PUT), eliminar (DELETE). Secreto HMAC mostrado una sola vez. Eventos: message.received/sent, conversation.escalated/closed. | Fase F8 |
| Vista contacto mejorada (frontend) | `/contacts/[id]`: facts con key/value/source/confidence visibles. Edición inline por fila (hover → pencil icon). Nuevo hecho con form key+value. DELETE con confirm. PUT /v1/contacts/{id}/facts/{key}, DELETE /v1/contacts/{id}/facts/{key}. | Fase F8 |
| Menciones @usuario en notas (frontend) | `ConversationView.tsx`: textarea de notas detecta `@query` con regex, muestra autocomplete de agentes disponibles (GET /v1/users/available-agents). `insertMention()` reemplaza el texto antes del cursor. Dropdown posicionado absolutamente sobre el textarea. | Fase F8 |
| Canned responses en reply (frontend) | `ReplyBox.tsx` acepta `onCannedToggle` + `showCannedActive`. Botón Zap (⚡) toggle muestra picker encima de la caja. Picker: input de búsqueda + lista filtrable de canned responses (GET /v1/canned-responses). Click inserta y envía directamente. | Fase F8 |
| Nav F8 | `AppNav.tsx` añade rutas: `/knowledge` (BookOpen icon), `/api-keys` (Key icon), `/webhooks` (Webhook icon). | Fase F8 |
| Toast system | `hooks/use-toast.ts`: `useToastStore` (Zustand) + `useToast()` hook. `components/Toaster.tsx`: lista fija bottom-right, auto-dismiss 4s, variantes default/success/destructive. Incluido en `Providers.tsx`. | Fase F9 |
| GDPR Export frontend | `ExportSection` en dashboard/page.tsx: POST /v1/tenants/me/export → job.id, polling cada 3s GET /v1/tenants/me/export/{id}/status, cuando done GET /v1/tenants/me/export/{id}/download → href. Toast de éxito/error. | Fase F9 |
| Billing page frontend | `app/(app)/billing/page.tsx`: GET /v1/metrics/summary (días=30) + GET /v1/quotas + GET /v1/metrics (historial). `QuotaBar` con barra de progreso y % utilizado. `MiniBarChart` CSS puro (flex+div). | Fase F9 |
| KB Stats frontend | `KbStats` component en /knowledge: calcula totales a partir del array de docs ya cargado (sin endpoint extra). Desglosa ready/processing/error + pdf/url. | Fase F9 |
| Command Palette | `components/CommandPalette.tsx`: Cmd+K/Ctrl+K via `AppShell.tsx` (client component envuelve layout). Busca contactos (GET /v1/contacts/search) + convs (GET /v1/inbox?search=) en paralelo. Debounce 300ms. Navegación ↑↓/Enter/Esc. Hint ⌘K en AppNav. | Fase F9 |
| AppShell | `components/AppShell.tsx`: client component que registra el listener Cmd+K y renderiza `CommandPalette`. Envuelve el layout de la app para mantener el layout server-component. | Fase F9 |
| Nav F9 | `AppNav.tsx` añade ruta: `/billing` (CreditCard icon). | Fase F9 |

---

## Prompt de arranque — Fase F10 (siguiente prioridad frontend)

```
Retomo Fase F10 — Frontend: Mejoras UX avanzadas (segunda ronda) + onboarding + configuración tenant.
Contexto:
- Fase F9 (GDPR export + billing + KB stats + toasts + command palette) completada. PR abierto en rama claude/fase-f9-frontend-LZSSE.
- Rama nueva: git fetch origin main && git checkout -b claude/fase-f10-frontend-XXXXX origin/main
- Main contiene backend Fases 0-29 + frontend F2-F9 en Next.js 16 + shadcn/ui.
- Primero: leer SOLO CLAUDE.md (secciones "Decisiones F9" y este prompt). Nada más.

Estado tras Fase F9 (ya en rama claude/fase-f9-frontend-LZSSE):
  A. GDPR Export en dashboard: ExportSection con POST /v1/tenants/me/export + polling status + botón descarga.
  B. Página /billing: GET /v1/metrics/summary + GET /v1/quotas + GET /v1/metrics (historial).
     Mini gráfico CSS/barras. Cuotas con barras de progreso. 4 métricas resumen.
  C. KB Stats en /knowledge: KbStats component con contadores ready/processing/error + desglose PDF vs URL.
  D. Sistema de Toasts: useToastStore (Zustand) + Toaster component + useToast hook.
     Integrado en /api-keys, /webhooks, /knowledge. AppShell.tsx con keyboard listener Cmd+K.
  E. Command Palette: CommandPalette.tsx con búsqueda de contactos + conversaciones.
     Apertura Cmd+K via AppShell. Navegación con ↑↓ y Enter. Hint ⌘K en AppNav.
  F. Nav: AppNav añade /billing (CreditCard icon).

Opciones a implementar en Fase F10 (en orden A → B → C → D):
  A. Configuración del tenant (perfil de empresa):
     - /settings: PATCH /v1/tenants/me con campos nombre_empresa, slug (readonly), plan (readonly).
     - Cambio de contraseña del usuario actual: PATCH /v1/me/password.
     - Avatar/logo del tenant (campo logo_url en TenantResponse si existe).

  B. Onboarding wizard (primera vez):
     - Si el tenant no tiene números WA ni conectores → mostrar banner/modal de bienvenida en dashboard.
     - Pasos: 1) Añadir número WA, 2) Configurar una persona IA, 3) (opcional) Conectar tienda.
     - Puede ser un simple banner dismissable con links a las páginas relevantes.

  C. Mejoras UX inbox:
     - Unsaved changes warning en ReplyBox: si hay texto en la caja y el usuario navega fuera.
     - Timestamps relativos en mensajes (Ej: "hace 5 min" usando Intl.RelativeTimeFormat).
     - Indicador de escritura en ConversationView (si existe endpoint de typing state en el backend).

  D. Tabla de audit log:
     - /audit-log: GET /v1/audit-log (ya existe en billing/api.py).
     - Filtros: action, target_type, date_from, date_to.
     - Paginación. Columnas: fecha, actor, acción, tipo objetivo, IP.

Reglas:
- NO tocar src/ Python
- Arrancar dev server y probar manualmente antes de reportar listo
- Commit + push + PR + actualizar CLAUDE.md + generar prompt F11
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```

---

## Prompt de arranque — Fase F5 (siguiente prioridad frontend)

```
Retomo Fase F5 — Frontend: Office hours + números WA + personas + gestión inbox avanzada.
Contexto:
- Fase F4 (conectores + canned responses + métricas SSE) completada. PR abierto en rama claude/fase-f4-frontend-tMph5.
- Rama nueva: git checkout -b claude/fase-f5-frontend-XXXXX origin/main
- Main contiene backend Fases 0-28. Frontend en frontend/ con Next.js 16 + shadcn/ui.
- Primero: leer SOLO CLAUDE.md (secciones "Decisiones F4" y este prompt). Nada más.

Estado tras Fase F4 (ya en rama):
  A. Conectores: /connectors (lista+crear+eliminar) y /connectors/[id] (stats+buscar+credenciales).
  B. Canned responses: /canned-responses (CRUD + búsqueda debounced + modal preview variables).
  C. Dashboard SSE: EventSource a /v1/metrics/stream + fallback polling 30s + badge "En vivo".
  D. Nav: AppNav añade /connectors (Plug) y /canned-responses (Zap).

Opciones a implementar en Fase F5 (en orden A → B → C → D):
  A. Gestión de office hours (requiere Fase 29 backend mergeada):
     - CRUD /v1/office-hours
     - Selección de número WA + día de semana + hora inicio/fin
  B. Panel números WA:
     - Listar (GET /v1/wa-numbers), ver estado WAHA
     - Métricas por número (GET /v1/wa-numbers/{id}/metrics con filtro fecha)
     - Página /wa-numbers/[id] con métricas + top contacts
  C. Gestión de personas (IA):
     - CRUD /v1/personas (GET/POST/PATCH/DELETE)
     - Campos: nombre, tono, locale, locale_secondary, auto_detect_locale
  D. Filtros avanzados inbox:
     - Filtro por assigned_user_id + date_from + date_to en /inbox
     - Export CSV (GET /v1/inbox/export) con botón de descarga
     - Búsqueda full-text (GET /v1/inbox/search?q=)

Reglas:
- NO tocar src/ Python
- Arrancar dev server y probar manualmente antes de reportar listo
- Commit + push + actualizar CLAUDE.md + generar prompt F6
```

---

## Backlog futuro — fases propuestas (no priorizadas)

> Estas fases no están planificadas con detalle todavía. Cuando se las retome, hacer
> discovery + escribir MD propio antes de tocar código (regla del proyecto).

### 🔴 Bloqueantes para v1 comercial (sin esto no se vende)

| # | Título | Por qué bloquea |
|---|---|---|
| F1 | **Frontend Next.js (panel del tenant)** | Backend tiene 60+ endpoints REST sin UI. Un cliente no puede usar ChatPro hoy. Estimado: 5-10 fases (auth/login screen, alta de números, inbox, configuración, métricas, conectores, KB, agentes). |
| F2 | **Deploy a producción + CI/CD** | Solo existe `docker-compose.yml` para dev. Falta Railway/Fly.io/k8s, pipeline de tests automáticos, migraciones automatizadas, secrets management. |
| F3 | **Stripe billing (cobro real)** | Fase 11 hizo cuotas y métricas, pero el cobro automático mensual no existe. Sin esto no se factura. |
| F4 | **Email transaccional** | No hay SMTP/SES/Resend integrado para signup, password recovery, alertas de cuota, notificaciones de agente. |
| F5 | **Resolver §14 del plan** | Nombre definitivo del producto, dominio, providers de IA/storage/email — siguen sin decidirse formalmente. |

### 🟡 Funcionalidades del backlog original sin implementar

| # | Título | Origen |
|---|---|---|
| F6 | **Tool de agenda** (Google Calendar / Calendly) | Plan §3 Fase 7 mencionaba "Tool: agendar (calendar abstracto)" — no existe. |
| F7 | **Tools custom por tenant** | HTTP request al backend del tenant con auth firmada. Plan §3 Fase 7. |
| F8 | **Conectores e-commerce adicionales** | Tienda Nube, Magento, VTEX, Jumpseller. Hoy solo Woo + Shopify. |
| F9 | **Conectores CRM** | HubSpot, Pipedrive, Salesforce. Plan §5. |
| F10 | **Conectores helpdesk** | Zendesk, Intercom, Freshdesk. Plan §5. |
| F11 | **Conectores KB extra** | Notion, Google Drive, Google Sheets, Confluence. Plan §5. |
| F12 | **Sharding WAHA multi-instancia** | Hoy todos los tenants comparten `waha_node_id="default"`. El campo existe pero el sharder lógico no. Plan §4. |
| F13 | **Observabilidad real** | OpenTelemetry + Prometheus + Loki — mencionado en plan §3, no implementado. |
| F14 | **Audit log inmutable + retención configurable** | Plan §13.10 y §9 — parcial. |

### 🟢 Numeración faltante (cosmético)

Fases 13, 14, 15, 17 nunca existieron — saltos de numeración cuando se agregaron
feature phases (16 retomo, 18+). No es deuda, es histórico.

---

## Prompt de arranque — Fase 30 (siguiente prioridad del backlog)

```
Retomo Fase 30 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 29 (office hours + waiting time + menciones notas + métricas agente) en PR #25.
- Rama nueva: git fetch origin main && git checkout -b claude/phase30-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18-29 funcionales.
- Primero: leer SOLO CLAUDE.md (sección "Decisiones de arquitectura" y este prompt). Nada más.

Estado tras Fase 29 (ya en rama, pendiente merge a main):
  A. Office hours (respuestas automáticas programadas):
     - Módulo src/office_hours/ (models.py, service.py, api.py).
     - Tabla office_hours (migración 0026): tenant_id, wa_number_id FK nullable,
       day_of_week, hour_start, hour_end, is_active, out_of_hours_message.
     - CRUD en GET/POST/PUT/DELETE /v1/office-hours.
     - check_office_hours() inyectado en agent/service.respond().
     - Tests en tests/test_fase29_office_hours.py (10 tests).
  B. Waiting time (conversaciones en espera con tiempo):
     - WaConversation.waiting_since (DateTime nullable, migración 0027).
     - ConversationSummary.waiting_minutes calculado en _conv_summary().
     - GET /v1/inbox?sort=waiting_time ordena por waiting_since ASC NULLS LAST.
     - GET /v1/inbox/overdue?threshold_minutes=30 endpoint.
     - Tests en tests/test_fase29_waiting_time.py (7 tests).
  C. Menciones en notas (@usuario):
     - conversation_notes.mentions JSONB (migración 0028).
     - _resolve_mentions() en inbox/api.py: busca @email o @nombre.
     - Notificación WS via notification_manager (event: "note.mention").
     - NoteOut.mentions: list[uuid.UUID].
     - Tests en tests/test_fase29_note_mentions.py (7 tests).
  D. Métricas de agente por período:
     - GET /v1/users/{user_id}/metrics?date_from=&date_to=
     - Calcula: conversations_handled, avg_first_response_sec, avg_resolution_sec,
       messages_sent, notes_created, busiest_hour.
     - Acceso: admin/owner O propio usuario. 403 agente→otro. 404 si no en tenant.
     - Tests en tests/test_fase29_agent_metrics.py (7 tests).
  - Total: 31 tests nuevos, 0 regresiones.
  - Fallo pre-existente conocido: test_fase23_export.py::test_crear_export_job_devuelve_202
    (export_tenant_data importado localmente, no patcheable — no es regresión).

Opciones a implementar en Fase 30 (en ESTRICTO orden A→B→C→D SIN pausar):
Para cada opción: implementar código + tests + ejecutar tests + avanzar solo si pasan.
Comando de tests: DATABASE_URL=postgresql://chatpro:chatpro@localhost:5432/chatpro_test uv run pytest

────────────────────────────────────────────────────────────────
OPCIÓN A — Chatbot con transferencia a número externo
────────────────────────────────────────────────────────────────
- Cuando el bot no puede resolver una consulta, ofrecer transferencia a soporte por
  un número de WhatsApp externo configurable por tenant/número.
- Campo `escalation_phone` en WaNumber (o tabla separada de config bot).
- Acción del agente: si llama tool `escalar_a_soporte`, bot envía link wa.me/ al usuario
  y registra HandoffEvent con reason="external_escalation".
- Tests en tests/test_fase30_escalation.py.

────────────────────────────────────────────────────────────────
OPCIÓN B — Encuestas de satisfacción (CSAT) automáticas
────────────────────────────────────────────────────────────────
- Al cerrar conversación, enviar encuesta automática con botones interactivos WAHA
  (1-5 estrellas o 👍/👎).
- Tabla `csat_responses`: tenant_id, wa_conversation_id, score, comment, responded_at.
- Config por tenant: is_csat_enabled, csat_question_text.
- GET /v1/inbox/csat-report?date_from=&date_to= → avg_score, total_responses, distribution.
- Tests en tests/test_fase30_csat.py.

────────────────────────────────────────────────────────────────
OPCIÓN C — Blacklist / contactos bloqueados
────────────────────────────────────────────────────────────────
- Tabla `blocked_contacts`: tenant_id, wa_number_id FK nullable, phone_e164, reason, blocked_at.
- Si número entrante está en blacklist → ignorar webhook silenciosamente (no crear conv).
- POST /v1/blocked-contacts, DELETE /v1/blocked-contacts/{id}, GET list.
- Tests en tests/test_fase30_blacklist.py.

────────────────────────────────────────────────────────────────
OPCIÓN D — Panel de rendimiento de knowledge base (RAG)
────────────────────────────────────────────────────────────────
- Loggear cada llamada a busqueda_rag: query, k_results, top_score, used_in_response.
- Tabla `rag_query_log`: tenant_id, kb_document_id FK nullable, query_text, results_count,
  top_score, created_at.
- GET /v1/knowledge/stats → total_queries, avg_top_score, queries_without_results,
  top_documents [{doc_id, title, hit_count}].
- Tests en tests/test_fase30_rag_stats.py.

────────────────────────────────────────────────────────────────
Al cerrar (tras completar D y que todos los tests pasen):
  1. git add <archivos específicos>
  2. git commit -m "feat(fase-30): escalación externa + CSAT + blacklist + RAG stats"
  3. git push -u origin <rama>
  4. Crear PR con descripción detallada de cada opción.
  5. Actualizar CLAUDE.md:
     - Tabla de fases: Fase 29 → ✅ Mergeada a main, Fase 30 → ✅ PR abierto
     - Agregar decisiones arquitectónicas de Fase 30
     - Reemplazar "Prompt de arranque — Fase 30" con "Prompt de arranque — Fase 31"
  6. Generar el prompt de arranque para Fase 31 (regla recursiva).

NO tocar: src/knowledge/ salvo indicación explícita (excepto opción D).
```

---

## Prompt de arranque — Fase 29 (COMPLETADA — ver Fase 30 arriba)

```
Retomo Fase 29 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 28 (dashboard métricas + deactivate user + export CSV + búsqueda contactos) mergeada a main. PR #23.
- Rama nueva: git fetch origin main && git checkout -b claude/phase29-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18-28 funcionales.
- Primero: leer SOLO CLAUDE.md (sección "Decisiones de arquitectura" y este prompt). Nada más.

Estado tras Fase 28 (ya en main):
  A. Dashboard de métricas operativas:
     - GET /v1/metrics/dashboard: conversations_total/active/bot/closed_today,
       messages_in/out_today, agents_online, unassigned_waiting.
     - Módulo src/api/v1/metrics_dashboard.py registrado en router.
     - Tests en tests/test_fase28_metrics_summary.py (7 tests).
  B. Deactivate user con reasignación:
     - PATCH /v1/users/{user_id}/deactivate: is_active=False + reasigna convs activas.
     - Reasignación vía _find_agent_with_least_load(). Sin agentes → convs sin asignar.
     - Respuesta: {deactivated_user_id, reassigned_conversations, new_assignee_id}.
     - Tests en tests/test_fase28_user_deactivate.py (7 tests).
  C. Export CSV inbox:
     - GET /v1/inbox/export: mismos filtros que GET /v1/inbox, sin paginación.
     - Content-Type text/csv, columnas: id, wa_contact_name, wa_contact_phone, status,
       assigned_user_id, created_at, last_message_at, resolved_at, tags, notes_count.
     - Tests en tests/test_fase28_inbox_export.py (7 tests).
  D. Búsqueda de contactos:
     - GET /v1/contacts/search?q=&limit=: ILIKE en phone_e164 OR display_name.
     - Respuesta incluye conversations_count vía WaConversation.contact_id.
     - Tests en tests/test_fase28_contact_search.py (7 tests).
  - Total: 28 tests nuevos, 0 regresiones.
  - Fallo pre-existente conocido: test_fase23_export.py::test_crear_export_job_devuelve_202
    (export_tenant_data importado localmente, no patcheable — no es regresión).

Opciones a implementar en Fase 29 (en ESTRICTO orden A→B→C→D SIN pausar):
Para cada opción: implementar código + tests + ejecutar tests + avanzar solo si pasan.
Comando de tests: DATABASE_URL=postgresql://chatpro:chatpro@localhost:5432/chatpro_test uv run pytest

────────────────────────────────────────────────────────────────
OPCIÓN A — Respuestas automáticas programadas (office hours)
────────────────────────────────────────────────────────────────
- Tabla `office_hours` (migración nueva): tenant_id, wa_number_id FK nullable,
  day_of_week (0-6, 0=lunes), hour_start (0-23), hour_end (0-23), is_active.
- Si hay configuración activa para el número en la hora actual → bot atiende normalmente.
- Si fuera de horario → respuesta automática configurable ("Estamos fuera de horario, atendemos de X a Y").
- GET/POST/PUT/DELETE /v1/office-hours (CRUD completo).
- Integración en webhook handler: si fuera de horario → respuesta y NO escalada.
- Tests en tests/test_fase29_office_hours.py (mínimo 7 tests).

────────────────────────────────────────────────────────────────
OPCIÓN B — Conversaciones en espera con tiempo de espera
────────────────────────────────────────────────────────────────
- Añadir campo `waiting_since` (DateTime nullable) a WaConversation (migración).
  Se setea cuando status pasa a waiting_agent, se limpia al pasar a agent.
- GET /v1/inbox?sort=waiting_time: ordena por waiting_since ASC.
- GET /v1/inbox/overdue?threshold_minutes=30: convs en waiting_agent > threshold.
- ConversationSummary incluye `waiting_minutes: int | None`.
- Tests en tests/test_fase29_waiting_time.py (mínimo 6 tests).

────────────────────────────────────────────────────────────────
OPCIÓN C — Notas con menciones (@usuario)
────────────────────────────────────────────────────────────────
- Al crear una nota, parsear @email o @nombre en el texto.
- Si el usuario mencionado existe en el tenant → notificar vía WS (NotificationManager).
- Schema NotificationEvent para menciones: {event: "note.mention", note_id, conversation_id,
  mentioned_user_id, author_user_id, text_preview, tenant_id}.
- Payload en la nota: campo `mentions: list[uuid]` (IDs de usuarios mencionados).
- Tests en tests/test_fase29_note_mentions.py (mínimo 6 tests).

────────────────────────────────────────────────────────────────
OPCIÓN D — Métricas de agente con histórico (por período)
────────────────────────────────────────────────────────────────
- GET /v1/users/{user_id}/metrics?date_from=&date_to=
  Calcula para el período:
    {conversations_handled, avg_first_response_sec, avg_resolution_sec,
     messages_sent, notes_created, busiest_hour (0-23)}
  Acceso: admin/owner O propio usuario. 404 si no pertenece al tenant.
- Tests en tests/test_fase29_agent_metrics.py (mínimo 6 tests).

────────────────────────────────────────────────────────────────
Al cerrar (tras completar D y que todos los tests pasen):
  1. git add <archivos específicos>
  2. git commit -m "feat(fase-29): office hours + waiting time + menciones notas + métricas agente"
  3. git push -u origin <rama>
  4. Crear PR con descripción detallada de cada opción.
  5. Actualizar CLAUDE.md:
     - Tabla de fases: Fase 28 → ✅ Mergeada a main, Fase 29 → ✅ PR abierto
     - Agregar decisiones arquitectónicas de Fase 29
     - Reemplazar "Prompt de arranque — Fase 29" con "Prompt de arranque — Fase 30"
  6. Generar el prompt de arranque para Fase 30 (regla recursiva).

NO tocar: src/knowledge/, src/connectors/ salvo indicación explícita.
```

---

## Prompt de arranque — Fase 25 (siguiente prioridad del backlog)

```
Retomo Fase 25 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 24 (webhooks conversación + métricas WaNumber + búsqueda inbox + resumen al cerrar) mergeada a main. PR #19.
- Rama nueva: git fetch origin main && git checkout -b claude/phase25-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18-24 funcionales.
- Primero: leer SOLO CLAUDE.md. Nada más.

- Estado tras Fase 24:
    A. Webhooks de conversación:
       - emit_event() (Fase 10) inyectado en: messaging/webhook.py (message.received + conversation.created),
         agent/service._persist_outbound() (message.sent bot), inbox/api.reply_conversation() (message.sent agente),
         inbox/api.take/close_conversation() (conversation.status_changed).
       - _get_or_create_conversation devuelve (conv, is_new: bool) para emitir solo en conv nueva.
       - Tests en tests/test_fase24_conv_webhooks.py (8 tests).
    B. Métricas WaNumber:
       - GET /v1/wa-numbers/{id}/metrics con params date_from/date_to (default hoy-30d/hoy).
       - Respuesta: wa_number_id, date_from, date_to, messages_in, messages_out,
                    conversations_total, conversations_active, top_contacts [{phone, count}].
       - Schema WaNumberMetricsOut + TopContactEntry en src/wa/api.py.
       - Tests en tests/test_fase24_wa_metrics.py (6 tests).
    C. Búsqueda full-text inbox:
       - GET /v1/inbox/search?q=<query>&conversation_id=<uuid>&date_from=<date>&date_to=<date>
       - Usa body_tsv GENERATED ALWAYS (migración 0020, GIN index) via column("body_tsv", TSVECTOR).
       - plainto_tsquery('spanish', q). Contexto ±2 mensajes. 400 si q vacío.
       - Tests en tests/test_fase24_inbox_search.py (8 tests).
    D. Resumen al cerrar:
       - src/agent/tasks.summarize_on_close(conv_id) Celery task.
       - Disparada en close_conversation si conv.turn_count > 5.
       - Claude Haiku (claude-haiku-4-5-20251001). Persiste en WaConversation.ai_summary.
       - ConversationSummary expone ai_summary. src.agent en autodiscover_tasks.
       - Tests en tests/test_fase24_summary_on_close.py (7 tests).
    - Migración 0020: body_tsv en wa_messages con GIN index.
    - Fallo pre-existente conocido: test_fase23_export.py::test_crear_export_job_devuelve_202
      (export_tenant_data importado localmente, no patcheable como attr de módulo — no es regresión).

- Opciones a implementar en Fase 25 (decidir con el usuario al arrancar):

    A. Panel de SLA y tiempos de respuesta:
       - Calcular tiempo_primera_respuesta (bot o agente) por conversación.
       - Calcular tiempo_de_resolución (apertura → close).
       - Endpoint: GET /v1/inbox/sla-report?date_from=&date_to= con percentiles p50/p90/p99.
       - Persistir first_response_at / resolved_at en WaConversación (migración nueva).

    B. Etiquetas (tags) en conversaciones:
       - Tabla conversation_tags: {id, tenant_id, wa_conversation_id, tag, created_by_user_id}.
       - POST /v1/inbox/{conv_id}/tags, DELETE /v1/inbox/{conv_id}/tags/{tag}.
       - GET /v1/inbox?tag=xxx para filtrar conversaciones por etiqueta.
       - Migración nueva.

    C. Templates de respuesta rápida (canned responses):
       - Tabla canned_responses: {id, tenant_id, shortcode, text, created_by}.
       - CRUD /v1/canned-responses.
       - Agente puede usar /buscar_template para respuestas predefinidas.

    D. Notificaciones internas (in-app) para agentes:
       - Cuando una conversación pasa a waiting_agent, notificar a todos los agentes del tenant.
       - WebSocket broadcast al canal /ws/notifications/{tenant_id}?token=<jwt>.
       - Schema NotificationEvent en src/messaging/ws_manager.py.

- NO tocar: src/knowledge/ salvo indicación.
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 26.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```

---

## Prompt de arranque — Fase 24 (siguiente prioridad del backlog)

```
Retomo Fase 24 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 23 (WebSocket inbox + Export GDPR + i18n personas + métricas SSE) mergeada a main. PR #18.
- Rama nueva: git fetch origin main && git checkout -b claude/phase24-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18-23 funcionales.
- Primero: leer SOLO CLAUDE.md. Nada más.

- Estado tras Fase 23:
    A. WebSocket inbox (/ws/inbox/{conv_id}?token=<jwt>):
       - ConnectionManager en src/messaging/ws_manager.py. Singleton `manager`.
       - broadcast_from_sync() usa run_coroutine_threadsafe con loop registrado en lifespan.
       - Integrado en service._persist_outbound() y inbox/api.py::reply_conversation().
       - Tests en tests/test_fase23_ws.py (sync + async con mock WebSocket).
    B. Export GDPR:
       - ExportJob en billing/models.py. Migración 0018.
       - Tarea Celery billing.export_tenant_data. src/billing/tasks.py.
       - Endpoints: POST /v1/tenants/me/export (202), GET status, GET download (URL S3 firmada).
       - src/billing/storage.py: upload_bytes + generate_presigned_url (mockeable).
       - Tests en tests/test_fase23_export.py.
    C. i18n Persona:
       - locale_secondary (ARRAY Text) + auto_detect_locale (Boolean). Migración 0019.
       - build_system_prompt() inyecta bloque IDIOMA si auto_detect_locale=True.
       - PersonaCreate/PersonaUpdate/PersonaResponse exponen los nuevos campos.
       - Tests en tests/test_fase23_i18n.py.
    D. Métricas SSE:
       - GET /v1/metrics/stream → text/event-stream. Auth Bearer header O ?token=.
       - METRICS_STREAM_INTERVAL_S: int = 30 en Settings.
       - Tests en tests/test_fase23_sse.py.
    - boto3>=1.35.0 añadido a pyproject.toml.
    - Fallos pre-existentes conocidos: ninguno conocido en este momento.

- Opciones a implementar en Fase 24 (decidir con el usuario al arrancar):

    A. Notificaciones push outbound vía webhooks para eventos de conversación:
       - Eventos: conversation.created, conversation.status_changed, message.received, message.sent.
       - Reutilizar WebhookOut + WebhookDelivery de Fase 10 (src/public_api/).
       - Inyectar emit_event() en el dispatcher y en service.respond().
       - Tests en tests/test_fase24_conv_webhooks.py.

    B. Dashboard de métricas por número (WaNumber):
       - GET /v1/wa-numbers/{id}/metrics → messages_in/out por día, conversations activas,
         top contacts por volumen. Queries sobre wa_messages y wa_conversations.
       - Filtro por período (date_from, date_to, default 30 días).
       - Tests en tests/test_fase24_wa_metrics.py.

    C. Búsqueda full-text de mensajes en inbox:
       - GET /v1/inbox/search?q=<query>&conversation_id=<uuid>&date_from=...&date_to=...
       - Usa body_tsv (GIN index ya existente en wa_messages).
       - Devuelve lista de mensajes con contexto (±2 mensajes antes/después).
       - Tests en tests/test_fase24_inbox_search.py.

    D. Resumen automático de conversación al cerrar:
       - Al llamar POST /v1/inbox/{conv_id}/close, si la conversación tiene >5 turnos,
         disparar tarea Celery summarize_on_close(conv_id) que genera y persiste
         WaConversation.ai_summary con Claude Haiku.
       - Tests en tests/test_fase24_summary_on_close.py.

- NO tocar: src/knowledge/ salvo indicación.
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 25.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```

---

## Prompt de arranque — Fase 23 (siguiente prioridad del backlog)

```
Retomo Fase 23 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 22 (fix pre-existentes + rate limit + search semántico + hallucination_flag) mergeada a main. PR abierto.
- Rama nueva: git fetch origin main && git checkout -b claude/phase23-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18 + 19 + 20 + 21 + 22 funcionales.
- Primero: leer SOLO CLAUDE.md. Nada más.

- Estado tras Fase 22:
    - contact_id en WaConversation (migración 0016). Tool runner puede pasar contact_id a conectores.
    - Rate limiting sliding window por (tenant_id, wa_contact_phone). In-memory + fallback Redis.
      Configuración: RATE_LIMIT_MESSAGES=10, RATE_LIMIT_WINDOW_SECONDS=60 en Settings.
      Inyectado en service.respond() — retorno silencioso si superado.
    - GET /v1/connector-configs/{id}/search?q=<query>&max_results=5 operativo.
      Schemas SearchResultOut + SearchResultsOut en src/connectors/api.py.
    - hallucination_flag en WaMessage (migración 0017, Boolean default False).
      _run_agent_loop() retorna 4-tupla. _persist_outbound() lo persiste.
    - test_connector_api_v19.py: fixture tenant_a desempacada en helpers _make_config/_make_order.
    - test_agent_tools.py::test_collect_incluye_builtin: WaConversation.contact_id añadido.
    - Fallos pre-existentes conocidos (NO causados por Fase 22):
        * Ninguno conocido en este momento.

- Opciones a implementar en Fase 23 (decidir con el usuario al arrancar):

    A. WebSocket para inbox en tiempo real:
       - Endpoint WebSocket /ws/inbox/{conversation_id} (autenticado con token en query param).
       - Broadcast de nuevos mensajes a clientes conectados al abrir una conversación.
       - State management en memoria (dict conversation_id → set[WebSocket]).
       - Tests en tests/test_fase23_ws.py.

    B. Export de datos del tenant (GDPR):
       - POST /v1/tenants/me/export → dispara job Celery que empaqueta:
           contacts, wa_messages, orders, contact_facts en ZIP (CSV por tabla).
       - GET /v1/tenants/me/export/{job_id}/status → estado del job (queued/done/error).
       - GET /v1/tenants/me/export/{job_id}/download → signed URL a S3 del ZIP.
       - Tests en tests/test_fase23_export.py.

    C. Soporte multi-idioma en personas (i18n):
       - Agregar campos `locale_secondary TEXT[]` y `auto_detect_locale BOOLEAN` a Persona.
       - Migración 0018.
       - Prompt builder: si auto_detect_locale=True, incluir instrucción de detectar el idioma
         del usuario y responder en ese idioma.
       - Tests en tests/test_fase23_i18n.py.

    D. Dashboard de métricas en tiempo real via SSE:
       - GET /v1/metrics/stream → Server-Sent Events con contadores actualizados cada 30s:
           messages_in, messages_out, conversations_active, llm_cost_today.
       - Autenticado con JWT.
       - Tests básicos de SSE en tests/test_fase23_sse.py.

- NO tocar: src/knowledge/, src/billing/, src/inbox/ salvo indicación.
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 24.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```

---

## Prompt de arranque — Fase 22 (siguiente prioridad del backlog)

```
Retomo Fase 22 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 21 (webhooks salientes conectores + stats + anti-hallucination logging) mergeada a main. PR #16.
- Rama nueva: git fetch origin main && git checkout -b claude/phase22-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18 + 19 + 20 + 21 funcionales.
- Primero: leer SOLO CLAUDE.md. Nada más.

- Estado tras Fase 21:
    - webhook_handler() en WooCommerce y Shopify llama _dispatch_event() tras _upsert_product/_upsert_order.
    - Eventos soportados: "connector.product_updated", "connector.order_created", "connector.order_updated".
    - _dispatch_event() privado en ambos connectors: llama emit_event() del dispatcher (src/public_api/dispatcher.py).
    - order.created y order.updated separados en ambos connectors (antes estaban agrupados).
    - GET /v1/connector-configs/{id}/stats → ConnectorStatsOut: products_count, orders_count, status, last_syncs.
    - Anti-hallucination: run_agent_turn loggea warning con conversation_id + response[:300] + tool_results.
    - 16 tests nuevos en tests/test_fase21.py.
    - Fallos pre-existentes conocidos (NO causados por Fase 21):
        * test_connector_api_v19.py (27 fallos): fixture tenant_a no desempacada correctamente.
        * test_agent_tools.py::test_collect_incluye_builtin: WaConversation no tiene contact_id.

- Implementar las 4 opciones en esta sesión, en orden A → B → C → D:

    A. Fix fallos pre-existentes (2 bugs independientes):
       1. tests/test_connector_api_v19.py (27 fallos): el fixture `tenant_a` devuelve una
          tupla (tenant, token) pero los tests lo usan como si fuera solo el tenant.
          Solución: actualizar el fixture o los tests para desempacar correctamente.
       2. tests/test_agent_tools.py::test_collect_incluye_builtin (1 fallo):
          `WaConversation` no tiene columna `contact_id`.
          Solución: añadir `contact_id = Column(UUID, nullable=True, index=True)` al modelo
          WaConversation en src/models/wa_conversation.py y crear migración Alembic 0016.

    B. Rate limiting por tenant/contacto:
       - Sliding window in-memory (collections.deque) con fallback Redis si REDIS_URL configurado.
       - Clave: (tenant_id, wa_contact_phone). Límites configurables en Settings:
           RATE_LIMIT_MESSAGES: int = 10
           RATE_LIMIT_WINDOW_SECONDS: int = 60
       - Si se supera el límite, respond_to_message() retorna silenciosamente sin llamar al agente
         (no envía respuesta al contacto, loggea WARNING con tenant_id + phone + count).
       - Implementar en src/agent/rate_limiter.py, inyectar en src/agent/service.py::respond_to_message().
       - Tests en tests/test_fase22_ratelimit.py.

    C. Endpoint de búsqueda semántica pública:
       - GET /v1/connector-configs/{id}/search?q=<query>&max_results=5
       - Llamar connector.search(query, max_results) (ya existe en ambos connectors).
       - Requiere autenticación (current_user), filtra por tenant_id igual que /stats.
       - Responder con lista de productos: id, external_id, name, price, url, score (si disponible).
       - Añadir schema SearchResultOut y SearchResultsOut en src/connectors/api.py.
       - Tests en tests/test_fase22_search.py.

    D. Persistir hallucination_flag en wa_messages:
       - Añadir columna `hallucination_flag = Column(Boolean, default=False)` a WaMessage
         en src/models/wa_message.py y crear migración Alembic 0017.
       - En src/agent/service.py::respond_to_message(): tras llamar run_agent_turn(),
         si result.hallucination_flag is True → setear hallucination_flag=True en el
         WaMessage de salida antes del db.commit().
       - Tests en tests/test_fase22_hallucination_flag.py.

- NO tocar: src/knowledge/, src/billing/, src/inbox/ salvo indicación.
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 23.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```
