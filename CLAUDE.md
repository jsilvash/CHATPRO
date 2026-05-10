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
| 22 | Fix pre-existentes + rate limit + search semántico + hallucination_flag | ✅ PR abierto |

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
└── test_historial_pedidos.py — historial_pedidos_contacto real: email/phone/contact_id, aislamiento, Shopify (Fase 20)
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

- Implementar las 4 opciones en esta sesión, en orden A → B → C → D:

    A. WebSocket para inbox en tiempo real:
       - Endpoint WebSocket /ws/inbox/{conversation_id} autenticado con token en query param
         (?token=<jwt>). Si el token es inválido o no corresponde al tenant de la conversación → cerrar con 1008.
       - ConnectionManager in-memory: dict[UUID, set[WebSocket]] (conversation_id → clientes).
         Al recibir un WaMessage outbound del bot o del agente, broadcast a todos los clientes
         conectados a esa conversation_id.
       - Integrar el broadcast en service._persist_outbound() y en inbox/api.py::reply().
       - Implementar en src/messaging/ws_manager.py (ConnectionManager) e incluir el router
         en src/main.py.
       - Tests en tests/test_fase23_ws.py: connect OK, connect sin token → 1008, connect con
         token de otro tenant → 1008, broadcast llega a todos los conectados, disconnect limpia el set.

    B. Export de datos del tenant (GDPR):
       - POST /v1/tenants/me/export → crea registro ExportJob (tabla nueva, migración 0018) con
         status="queued" y dispara tarea Celery export_tenant_data(job_id).
       - ExportJob: id UUID, tenant_id, status (queued/running/done/error), error TEXT,
         storage_uri TEXT, created_at, finished_at.
       - Tarea Celery: exporta contacts, wa_messages, orders, contact_facts a CSV individuales,
         los empaqueta en ZIP en memoria, lo sube a S3/MinIO con path
         exports/{tenant_id}/{job_id}.zip y actualiza status + storage_uri.
       - GET /v1/tenants/me/export/{job_id}/status → {status, created_at, finished_at, error}.
       - GET /v1/tenants/me/export/{job_id}/download → signed URL (TTL 15 min) al ZIP en S3.
         Si status != "done" → 400. Si storage_uri vacío → 404.
       - Aislamiento: tenant B no puede ver ni descargar export de tenant A.
       - Tests en tests/test_fase23_export.py: crear job, status queued/done/error, download URL,
         aislamiento, task mock (no S3 real).

    C. Soporte multi-idioma en personas (i18n):
       - Agregar a Persona (migración 0019):
           locale_secondary = Column(ARRAY(Text), default=[])  — ej: ["en", "pt"]
           auto_detect_locale = Column(Boolean, default=False)
       - prompt_builder.build_system_prompt(): si auto_detect_locale=True, añadir al final del
         system prompt el bloque:
           "IDIOMA: Detectá el idioma del último mensaje del usuario y respondé en ese mismo idioma.
            Idiomas soportados: {locale_primary} + {locale_secondary_joined}.
            Si el idioma no está en la lista, respondé en {locale_primary}."
       - API CRUD de personas (src/agent/api.py): exponer locale_secondary y auto_detect_locale
         en PersonaOut, PersonaCreate y PersonaUpdate.
       - Tests en tests/test_fase23_i18n.py: prompt incluye bloque idioma si auto_detect=True,
         no lo incluye si False, locale_secondary se serializa correctamente, CRUD actualiza campos.

    D. Endpoint de métricas en tiempo real via SSE:
       - GET /v1/metrics/stream → Server-Sent Events (text/event-stream). Autenticado con JWT
         (header Authorization o query param ?token=<jwt>).
       - Cada 30 segundos (configurable: METRICS_STREAM_INTERVAL_S: int = 30 en Settings)
         emite un evento con JSON:
           {messages_in_today, messages_out_today, conversations_active,
            llm_cost_cents_today, timestamp}
         Calculado con queries COUNT/SUM sobre usage_metrics y wa_conversations.
       - Al conectar, emite el primer evento inmediatamente (sin esperar 30s).
       - Si el cliente desconecta (asyncio.CancelledError), cierra silenciosamente.
       - Implementar en src/billing/api.py (o nuevo src/metrics/api.py si es más limpio).
       - Tests en tests/test_fase23_sse.py: respuesta es text/event-stream, primer evento
         contiene los campos esperados, 401 sin token, aislamiento tenant.

- NO tocar: src/knowledge/ salvo indicación.
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
