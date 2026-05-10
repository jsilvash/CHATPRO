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
| 6 | Sync incremental WooCommerce + embeddings | ✅ PR #12 abierto |
| 7 | Tools del agente (catálogo, stock, órdenes) | ✅ Mergeada a main |
| 8 | Inbox + handoff humano | ✅ Mergeada a main |
| 9 | RAG genérico (PDF/URL) | ✅ Mergeada a main |
| 10 | API pública + webhooks salientes | ✅ Mergeada a main |
| 11 | Billing + métricas + cuotas | ✅ Mergeada a main |
| 12 | Shopify connector (validación interfaz) | Pendiente |
| 16 (retomo) | Fix fallos pre-existentes test_billing + test_knowledge | ✅ PR #10 mergeado |

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
└── test_public_api.py       — CRUD api-keys/webhooks, authn por token, delivery, reintentos, dead letter, aislamiento
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

---

## Prompt de arranque — Fase 18 (Shopify connector + integración Fase 6 en agente)

```
Retomo Fase 18 — Shopify connector completo + integración búsqueda semántica en agente.
Contexto:
- PR #12 (Fase 6: sync incremental WooCommerce + embeddings) abierto, pendiente de merge.
- Esperar merge de PR #12 a main antes de arrancar, o crear desde PR #12 si ya mergeó.
- Rama nueva: git fetch origin main && git checkout -b claude/shopify-phase18-XXXXX origin/main
- Main contiene Fases 0-5 + 6 (PR #12) + 7-11 funcionales.
- Primero: leer SOLO CLAUDE.md + WHATSAPP_HUB_PLAN.md §7 (Conectores / Shopify).

- Objetivo Fase 18:
    1. ShopifyConnector completo (validate_interface, ya hay esqueleto en src/connectors/shopify/):
       - configure() con site_url + access_token
       - test_connection() contra /admin/api/2024-01/shop.json
       - sync_full() paginando /admin/api/2024-01/products.json (250 por página)
       - sync_incremental(since) con updated_at_min=since
       - verify_webhook() HMAC-SHA256 sobre X-Shopify-Hmac-SHA256
       - webhook_handler() products/orders igual que WooCommerce
       - expose_tools() y search() idénticos a WooCommerce
    2. Endpoint POST /webhooks/shopify/{tenant_id}/{config_id} (igual que WooCommerce)
    3. Integrar búsqueda semántica de Fase 6 en tools del agente (buscar_productos):
       - Actualmente usa ILIKE en tools.py; actualizar para llamar connector.search()
         que ya es híbrida (keyword + pgvector + RRF)
    4. Tests: ShopifyConnector (misma cobertura que WooCommerce), webhook Shopify,
       buscar_productos usa search() semántica, aislamiento.

- Archivos clave a leer:
    - src/connectors/shopify/ (conector existente con interfaz validada)
    - src/connectors/woocommerce/connector.py (modelo a seguir para Shopify)
    - src/connectors/woocommerce/webhook.py (modelo para endpoint Shopify)
    - src/connectors/woocommerce/tools.py (actualizar buscar_productos)
    - tests/test_shopify.py (tests existentes de validación de interfaz)

- NO tocar: src/knowledge/, src/billing/, src/public_api/, src/inbox/ (fases estables).
- Al cerrar: commit + push + PR + actualizar CLAUDE.md + generar prompt Fase 19.
- Al cerrar esta sesión, generar el prompt de arranque de la próxima (regla recursiva).
```
