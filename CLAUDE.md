# CHATPRO — Instrucciones del Proyecto

> **Para sesiones nuevas:** Leer primero "Contexto rápido" y el MD de la fase activa. Nada más.

---

## Contexto rápido del sistema

ChatPro es una plataforma SaaS **multi-tenant** de WhatsApp Hub. Permite a N tenants independientes gestionar N números de WhatsApp con agentes de IA, memoria de contactos, conectores de e-commerce (WooCommerce primero, Shopify después) y base de conocimiento RAG. Cada tenant tiene su propio panel, números, agentes, catálogo y métricas.

### Stack
- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2.0 (sync) + Alembic
- **BD:** PostgreSQL 16 + pgvector (Fase 5+)
- **Cache / Colas:** Redis 7 + Celery 5
- **Frontend:** Next.js 15 + TypeScript (Fase 8+)
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
| 26 | Auto-asignación round-robin + notas internas + historial status + templates variables | ✅ PR abierto |

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

---

## Prompt de arranque — Fase 27 (siguiente prioridad del backlog)

```
Retomo Fase 27 — Próxima fase del backlog (ver CLAUDE.md + WHATSAPP_HUB_PLAN.md).
Contexto:
- Fase 26 (auto-asignación round-robin + notas internas + historial status + templates variables) mergeada a main. PR #21.
- Rama nueva: git fetch origin main && git checkout -b claude/phase27-XXXXX origin/main
- Main contiene Fases 0-12 + 16 + 18-26 funcionales.
- Primero: leer SOLO CLAUDE.md. Nada más.

- Estado tras Fase 26:
    A. Auto-asignación round-robin:
       - _find_agent_with_least_load(db, tenant_id) en service.py.
       - _auto_escalate_if_needed asigna al agente con menos convs activas (agent|waiting_agent).
       - Sin agentes → assigned_user_id=None, status=waiting_agent.
       - GET /v1/users/available-agents → [{id, email, full_name, role, conv_count}] ordenado ASC.
       - Tests en tests/test_fase26_auto_assign.py (9 tests).
    B. Notas internas:
       - Tabla conversation_notes (migración 0024).
       - POST /v1/inbox/{conv_id}/notes → 201.
       - GET /v1/inbox/{conv_id}/notes → lista ASC.
       - DELETE /{note_id} → 204 solo autor, 403 si no es autor.
       - ConversationSummary.notes_count: int (cargado via _load_notes_count batch query).
       - Tests en tests/test_fase26_notes.py (8 tests).
    C. Historial de status:
       - Tabla conversation_status_history (migración 0025).
       - _record_status_change() helper en inbox/api.py.
       - Registrado en: take_conversation, close_conversation, reply_conversation (promueve waiting_agent→agent), _auto_escalate_if_needed (bot→waiting_agent, user_id=None).
       - GET /v1/inbox/{conv_id}/status-history → lista ASC por changed_at.
       - Tests en tests/test_fase26_status_history.py (7 tests).
    D. Templates con variables:
       - Variables {{nombre}}, {{producto}} etc. en canned responses.
       - _validate_variables() en create y update → 422 si mal formada.
       - CannedResponseOut.variables: list[str] vía from_orm_with_vars().
       - GET /v1/canned-responses/{id}/render?var=val → {rendered_text, variables_used, variables_missing, original_text}.
       - GET /v1/canned-responses/search?q= → busca en shortcode OR text (case-insensitive). Declarado antes de /{canned_id}.
       - Tests en tests/test_fase26_canned_templates.py (12 tests).
    - Migraciones 0024, 0025.
    - Fallo pre-existente conocido: test_fase23_export.py::test_crear_export_job_devuelve_202
      (export_tenant_data importado localmente, no patcheable — no es regresión).

- Implementar las 4 opciones en esta sesión, en orden A → B → C → D:

    A. Filtros avanzados en listado de inbox:
       - GET /v1/inbox soportar filtros adicionales: ?assigned_user_id=, ?date_from=, ?date_to=,
         ?search=<texto_libre> (búsqueda por wa_contact_name o wa_contact_phone).
       - Paginación mejorada: devolver página actual + total_pages en la respuesta.
       - Tests en tests/test_fase27_inbox_filters.py.

    B. Bulk actions en inbox:
       - POST /v1/inbox/bulk-assign body: {conversation_ids: [uuid], user_id: uuid}
         → asigna múltiples conversaciones al agente indicado. Devuelve {updated: N, errors: []}.
       - POST /v1/inbox/bulk-tag body: {conversation_ids: [uuid], tag: str}
         → aplica etiqueta a múltiples conversaciones.
       - POST /v1/inbox/bulk-close body: {conversation_ids: [uuid]}
         → cierra múltiples conversaciones (status→bot). Registra status_history para cada una.
       - Tests en tests/test_fase27_bulk_actions.py.

    C. Gestión de usuarios mejorada:
       - GET /v1/users/{user_id}/stats → {conversations_active, conversations_today, avg_response_time_s, notes_count}.
       - PATCH /v1/users/{user_id}/deactivate → pone is_active=False y re-asigna sus convs activas
         al agente con menos carga (round-robin).
       - Tests en tests/test_fase27_user_management.py.

    D. Webhook events para notas e historial:
       - Emitir evento note.created cuando se crea una nota interna.
         Payload: {conversation_id, note_id, user_id, text_preview: text[:100], tenant_id}.
       - Emitir evento conversation.status_changed cuando cambia status (ya existe en take/close,
         agregar también cuando lo hace _auto_escalate_if_needed).
       - Tests en tests/test_fase27_webhook_events.py.

- NO tocar: src/knowledge/ salvo indicación.
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 28.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
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
       - Persistir first_response_at / resolved_at en WaConversation (migración nueva).

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
