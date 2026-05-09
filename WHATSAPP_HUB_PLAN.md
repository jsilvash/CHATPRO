# WhatsApp Hub Multi-Tenant — Plan de Producto y Arquitectura

> **Estado:** borrador para revisión. **No tocar código** hasta OK explícito del usuario.
> **Rama de trabajo:** `claude/whatsapp-hub-saas-zSFto`
> **Repo:** `CHATPRO` (vacío al momento de escribir este plan).
> **Fecha:** 2026-05-09

---

## 0. Nota sobre archivos de referencia

El prompt original referenciaba `WHATSAPP_HUB_ARQUITECTURA_ACTUAL.md` y `CLAUDE.md` de FitnessIA. El usuario subió a este repo, en la rama `_reference/fitnessia`, una copia de:

- `_reference/fitnessia/CLAUDE.md` (148 líneas — reglas del proyecto SYNEX y ciclo de fases).
- `_reference/fitnessia/src/messaging/` (todo el módulo de mensajería, incluyendo Evolution/Meta/email — solo nos interesa lo WAHA).

**`WHATSAPP_HUB_ARQUITECTURA_ACTUAL.md` no se encontró**. Compensamos leyendo directamente el código fuente que se va a portar. Archivos clave revisados línea por línea:

| Archivo | LOC | Qué saqué |
|---|---|---|
| `_reference/fitnessia/src/messaging/waha_client.py` | 930 | Cliente WAHA completo, `WahaAPIError(status=-1/-2)`, todos los endpoints (sendText/Image/Video/File/Audio/Voice, sendSeen, start/stopTyping, sendPresence, sessions, contacts, lid-pn). Resolución LID→PN en 4 capas + cache + lock. `ensure_waha_webhooks()` que re-aplica webhooks en cada arranque (CRÍTICO: WAHA NO persiste config de webhooks aunque sí persiste auth). `_preload_lid_pn_cache()` workaround para bug WAHA donde `/contacts/lid-pn` no responde aunque el store tenga el dato. |
| `_reference/fitnessia/src/messaging/wa_lid_rehydrate.py` | 80 | Helper outbound: rehidrata `conv.wa_contact_phone` (LID persistido) al PN real antes de enviar. Constante `_LID_MIN_DIGITS = 14`. |
| `_reference/fitnessia/src/messaging/wa_tenant_lookup.py` | 112 | Routing del webhook: `get_wa_number_by_instance_name()` con cache 5 min TTL + `bypass_tenant_filter()`. Idem `get_tenant_by_phone_number_id()` para Cloud API (descartado en Hub). |
| `_reference/fitnessia/src/messaging/dispatcher.py` | 658 | Fan-out por `connection_type` (`qr`/`waha`/`cloud_api`). En el Hub queda solo el camino WAHA — descartar las ramas QR-Evolution y Cloud. `resolve_wa_number(tenant_id, purpose)` con cadena de fallback: purpose+default → purpose → default → primero. |
| `_reference/fitnessia/src/messaging/typing_state.py` | 65 | Cache in-memory por `(instance_name, phone)` con TTL configurable. Util para detectar typing del contacto y postergar respuesta del bot. |
| `_reference/fitnessia/CLAUDE.md` | 148 | Reglas: ciclo de fases (una fase = una sesión = un PR = un merge = un QA = siguiente), handoff entre sesiones, zonas multi-agente, "no push a main sin autorización". Idioma español. |

**Hallazgos del código que actualizan este plan** (detalle en §12 Fase 1):

1. **El campo se llama `evolution_instance_name`** en el modelo legacy — herencia de cuando Evolution era el QR provider. En el Hub renombrar a `waha_session_name` desde el día 1.
2. **El discriminador `connection_type`** (qr/waha/cloud_api) **se elimina**: en el Hub solo existe WAHA. Borrar el branching del dispatcher.
3. **`WahaAPIError` tiene dos status code locales especiales:**
   - `-1` → config faltante / DNS / timeout / payload inválido.
   - `-2` → LID sin resolver, **ABORT** (no fallback a `@lid` como chatId — causaría envío a número ficticio, ya pasó en prod 2026-04-23).
4. **Webhook events obligatorios:** `["message", "message.any", "message.ack", "session.status"]`. Borrar `message.ack` deja la fase de ACK inerte aunque el código esté bien.
5. **`ensure_waha_webhooks()` debe correr en cada arranque del backend.** No es opcional — WAHA persiste credenciales pero no webhooks.
6. **`_preload_lid_pn_cache()` mitiga bug de WAHA** (issue documentado en el código): `/contacts/lid-pn` devuelve vacío aunque el store sí tiene `lid` en el contacto. Es preload por sesión activa al arranque.
7. **`purpose` con enum cerrado `lifecycle|support`** (legacy gym): en el Hub son **tags libres** (decisión ya tomada en el prompt). Eliminar el enum.

> Estos hallazgos se traducen a tareas concretas en §12 Fase 1.

---

## 1. Nombre del producto (3 opciones)

| Opción | Razonamiento | Pros | Contras |
|---|---|---|---|
| **Conexa** | Del verbo "conectar". Evoca conexión múltiple, multi-canal, multi-tenant. Suena bien en español, corto, dominio `.com` probablemente caro pero `.app` o `.io` libres. | Marca limpia, no se ata a WhatsApp (extensible a IG/SMS después), género femenino acorde a "plataforma". | Genérica, ya hay marcas similares (Conekta de pagos en MX). |
| **NodoChat** | "Nodo" como punto de conexión + "Chat". Describe la función: cada tenant es un nodo del hub. | Descriptivo, técnico, bilingüe (Nodo/Node), sin colisiones evidentes. | Suena infra/devops, menos comercial. |
| **Plurall** | Del latín "plural" — multi-tenant + multi-número + multi-rubro. Doble L para diferenciación visual. | Distintivo, pegadizo, dominio probable libre. | Inventado, hay que enseñar la marca; "Plurall" ya existe como plataforma de educación en BR. |

**Recomendación: `Conexa`.** Las dos primeras son fuertes; "NodoChat" es muy literal y "Plurall" colisiona con educación. "Conexa" tiene espacio de marca y permite expandir más allá de WhatsApp.

> **Decisión pendiente del usuario.** El plan asume "Conexa" como nombre provisional para naming de paquetes y dominio interno; se renombrará en bloque cuando el usuario decida.

---

## 2. Repo nuevo o monorepo en FitnessIA

**Recomendación: repo nuevo (este: `CHATPRO`).**

| Eje | Repo nuevo (recomendado) | Monorepo en FitnessIA |
|---|---|---|
| Acoplamiento dominio | Cero. Modelo limpio, sin gym. | Alto riesgo de filtrar conceptos gym. |
| Releases | Independientes, versionado propio. | Coupled — un fix gym puede arrastrar Hub. |
| CI/CD | Pipeline simple. | Pipeline grande, builds más lentos. |
| Testing | Aislado, fixtures de tenant puros. | Mezcla con fixtures gym, riesgo de contaminación. |
| Reutilización | Copiar código WAHA limpio (~1500 LOC). Costo bajo y único. | Importar de `src/messaging/...` directo. |
| Equipo futuro | Permite squad dedicado al Hub. | Equipo único, prioridades en conflicto. |
| Billing/legal | Producto separado, fácil spinoff. | Requiere extracción posterior costosa. |

**Conclusión:** repo nuevo. La duplicación inicial del cliente WAHA (~1500 LOC) es one-time y se compensa con todos los demás ejes. La vía contraria (extraer después) es históricamente costosa y se posterga indefinidamente.

---

## 3. Stack tecnológico

**Recomendación: mantener Python (FastAPI + Postgres + Redis + Celery) en backend; Next.js + TypeScript en frontend.**

### Backend

| Componente | Elección | Razón |
|---|---|---|
| Lenguaje | Python 3.12 | Reusa código WAHA + cliente Anthropic + ecosystem maduro (woocommerce, pgvector, pypdf). |
| Web framework | FastAPI | Async nativo, OpenAPI auto, Pydantic, ya conocido por el equipo. |
| ORM | SQLAlchemy 2.0 + Alembic | Estándar Python, soporta pgvector, migraciones limpias. |
| BD primaria | PostgreSQL 16 | Transacciones, JSONB, full-text search, y **pgvector** sin infra extra. |
| Cache / pub-sub | Redis 7 | Rate limit, locks distribuidos, Celery broker, websockets pub/sub. |
| Cola de tareas | Celery 5 + Redis | Sync WooCommerce, embeddings, extracción de hechos, retries. |
| Observabilidad | OpenTelemetry + Prometheus + Loki | Logs estructurados con `tenant_id` siempre. |
| Auth | JWT (HS256 o RS256) + refresh | Simple, stateless; refresh en Redis para revocación. |
| Tests | pytest + pytest-asyncio + factory-boy | Estándar. Fixtures con tenant aislado. |

**Por qué NO Bun/Node en backend:** el cliente WAHA, ACK, LID→PN ya están en Python en el sistema actual; reescribir es retrabajo gratuito. Anthropic SDK Python es maduro. WooCommerce REST se llama trivialmente desde httpx.

**Por qué NO Go/Rust:** sería overkill para un MVP. Python escala lo suficiente para 10-100 tenants antes de tener que reconsiderar.

### Frontend

| Componente | Elección | Razón |
|---|---|---|
| Framework | Next.js 15 (App Router) + React 19 | Inbox tipo chat necesita UI rica; ecosystem React es dominante. |
| Lenguaje | TypeScript strict | Reduce bugs en formularios largos (config tenant, conector). |
| UI kit | shadcn/ui + Radix + Tailwind | Componentes accesibles, theming sencillo, sin lock-in. |
| State | TanStack Query + Zustand | Server-state + client-state separados. |
| Auth flow | NextAuth (provider credentials) o passthrough JWT del backend | Simple, soporta SSO después. |
| Realtime | WebSocket nativo desde FastAPI o Pusher/Ably si escalamos | Inbox necesita typing/ACK en vivo. |

### Despliegue

| Capa | Elección | Notas |
|---|---|---|
| Contenedores | Docker + docker-compose para dev | Compose perfila `web/worker/beat/redis/postgres/waha/minio`. |
| Producción | Kubernetes (k3s/EKS) o Fly.io | Decidir según volumen esperado. Para MVP: Fly.io o Render. |
| Storage | S3 (Cloudflare R2 preferido por costo) | Ver §9. |
| WAHA | Imagen oficial WAHA Plus self-hosted | Ver §4. |

---

## 4. Aislamiento WAHA por tenant

### Opciones evaluadas

**A — Una instancia WAHA compartida, múltiples sesiones nombradas `{tenant_id}_{wa_number_id}`**

- Costo: bajo. Una sola instancia, baseline de RAM ~200 MB + ~50 MB por sesión.
- Blast radius: alto. Crash o cuelgue de WAHA tira N tenants.
- Ban cruzado: moderado. Si Meta bloquea la IP, todas las sesiones afectadas. Mitigable con outbound proxy por sesión, pero complejo.
- Operación: simple. Una DB, un endpoint webhook, un health check.

**B — Una instancia WAHA por tenant**

- Costo: alto. Baseline ~200 MB por instancia × N tenants. Para 50 tenants ya son 10 GB solo en baseline.
- Blast radius: mínimo. Cada tenant aislado.
- Ban cruzado: nulo (cada uno con su contenedor; IP compartida sigue siendo riesgo, pero bajo).
- Operación: más compleja. Discovery, balanceo, restarts independientes.

**C — Pool con sharding**

- N instancias WAHA. Función de hash determinística `sharder(tenant_id) → waha_node_id`.
- Costo: medio. Crece linealmente con instancias, pero amortiza baseline entre múltiples tenants.
- Blast radius: limitado a la shard. Crash afecta `1/N` tenants.
- Operación: media. Service registry, healthchecks, rebalanceo bajo demanda.

### Recomendación

**Arrancar con A; diseñar el código asumiendo C.**

Concretamente:

1. **Modelo:** la tabla `wa_sessions` incluye `waha_node_id` desde el día 1 (default `"default"`).
2. **Cliente WAHA:** acepta `base_url` configurable por sesión, leído desde `wa_session.waha_node_id` → resolución a URL en config (mapa `{node_id: url}`).
3. **Sharding helper:** `select_node_for_tenant(tenant_id) → node_id` con hash consistente. Hoy retorna siempre `"default"`. Cuando crecemos, devolverá según hash.
4. **Webhook receiver:** un solo endpoint `/webhook/waha/{node_id}` autenticado con `X-WAHA-Token` y un secreto por nodo.
5. **Migración a C:** cuando se cruce el umbral (criterio: `>100 sesiones activas` OR `>5 tenants enterprise`), agregar nodos al mapa, rebalancear con un job batch.

**No vamos a B** salvo que un tenant enterprise lo exija contractualmente (en ese caso pasa a un nodo dedicado vía `waha_node_id`).

---

## 5. Vector DB para RAG y búsqueda de productos

**Recomendación: pgvector desde el día 1. Migrar a Qdrant si pasamos 10M vectores totales o si el query latency P95 excede 200ms con índice HNSW bien tuneado.**

### Comparativa

| Eje | pgvector | Qdrant | Weaviate |
|---|---|---|---|
| Infra extra | Ninguna (Postgres existente). | Servicio aparte. | Servicio aparte. |
| Atomicidad metadata-vector | Sí (mismo Postgres). | No (sync separado). | No (sync separado). |
| Filtros multi-tenant | `WHERE tenant_id = X` con índice compuesto. Excelente en HNSW partial indexes. | Payload filtering excelente. | Multi-tenancy nativa por colección. |
| Escala | Cómodo hasta 1-5M vectores. Más allá requiere tuning serio. | 100M+ documentado. | 100M+ documentado. |
| Costo | Cero adicional. | RAM-hungry. | RAM-hungry. |
| Operación | Backups con pg_dump. | Servicio nuevo a operar. | Servicio nuevo a operar. |

### Decisiones pgvector

- **Modelo de embeddings:** Voyage AI `voyage-3` (1024 dims, multi-idioma, calidad superior a OpenAI text-embedding-3-small). Fallback a `text-embedding-3-large` (3072) si Voyage no está disponible en la región.
- **Métrica:** cosine similarity.
- **Índice:** HNSW (`m=16, ef_construction=64`). Para tablas con `tenant_id` en filtro fijo, índice parcial por tenant si crece mucho.
- **Tablas con vector:**
  - `kb_chunks` (RAG genérico).
  - `product_embeddings` (búsqueda de productos).
  - `contact_memory_embeddings` (memoria semántica del contacto).
- **Aislamiento:** todos los queries forzados con `tenant_id` y, donde aplica, `wa_number_id`. Sin excepciones.

---

## 6. Arquitectura de memoria del contacto

### a) Datos estructurados base — tabla `contacts`

Columnas en `contacts` (datos universales):

```
id UUID PK
tenant_id UUID FK NOT NULL
phone_e164 TEXT NOT NULL
display_name TEXT
first_name TEXT
last_name TEXT
email TEXT
birth_date DATE
address_line TEXT
address_city TEXT
address_country TEXT
locale TEXT  -- es-CL, es-MX, en-US...
preferred_channel TEXT  -- 'whatsapp' por ahora
opt_in_marketing BOOLEAN DEFAULT FALSE
opt_in_marketing_at TIMESTAMPTZ
created_at, updated_at
UNIQUE(tenant_id, phone_e164)
INDEX(tenant_id, phone_e164)
INDEX(tenant_id, email) WHERE email IS NOT NULL
```

Tags en tabla join `contact_tags(tenant_id, contact_id, tag, created_by, created_at)`.

### b) Hechos extraídos / custom — tabla `contact_facts`

```
id UUID PK
tenant_id UUID FK NOT NULL
contact_id UUID FK NOT NULL
key TEXT NOT NULL          -- slug: 'talla_preferida','vegano','no_responder_dom'
value_text TEXT
value_type TEXT NOT NULL   -- 'text'|'number'|'date'|'bool'|'json'
source TEXT NOT NULL       -- 'manual'|'extracted'|'imported'|'connector'
confidence NUMERIC(3,2)    -- 0.00-1.00, NULL si manual
extracted_from_message_id UUID FK NULL
created_by_user_id UUID FK NULL  -- si manual
last_confirmed_at TIMESTAMPTZ
created_at, updated_at
UNIQUE(tenant_id, contact_id, key)
INDEX(tenant_id, contact_id)
```

### c) Extracción automática de hechos

**Flujo:**

1. Trigger: cada 5 turnos del contacto OR al cerrar conversación OR a demanda desde inbox.
2. Job Celery `extract_contact_facts(tenant_id, contact_id, conversation_id)`.
3. Carga: últimos 30 turnos + hechos previos del contacto + persona del agente.
4. Llama a Claude Haiku con prompt estructurado:
   - "Sos un extractor. Devolvé JSON `[{key, value, value_type, confidence, evidence_message_id}]`. Solo hechos concretos, no opiniones. No inventes."
5. Valida con Pydantic. Filtra `confidence >= 0.7`.
6. Upsert en `contact_facts`. Si existe con valor distinto y nueva confidence > vieja → reemplaza y log en `audit_log`. Si confidence menor → ignora pero registra.
7. Embeddings: si `value_text` es texto rico (no enum), genera embedding y guarda en `contact_memory_embeddings`.

**Costo:** Haiku ~ $0.0008/llamada con 30 turnos. Cap por tenant: `max_extractions_per_day` (default 500).

### d) Inyección en system prompt

**Sin saturar:** orden de prioridad para inclusión en prompt:

1. **Datos estructurados** (`contacts`): hasta 200 tokens. JSON compacto.
2. **Hechos top-K** (`contact_facts`): K=10 por defecto. Ranking: `(recency * 0.4) + (confidence * 0.4) + (manual_boost * 0.2)`.
3. **Resumen de conversación previa** (`contact_memory_summary`, si existe): hasta 300 tokens.

Resultado renderizado:

```
<memoria_contacto>
{nombre: "Juan Pérez", phone: "+56912345678", email: "juan@x.cl",
 tags: ["vip","veggie"]}
Hechos:
- talla_preferida: M (manual)
- vegano: true (extraído, conf 0.95)
- prefiere_envio: jueves (extraído, conf 0.8)
Resumen: Cliente recurrente, compró 3 veces, última consulta sobre stock de zapatillas X.
</memoria_contacto>
```

Si excede 1500 tokens totales → recortar primero hechos de baja confidence, luego resumen.

### e) Búsqueda semántica sobre memoria

- Tabla `contact_memory_embeddings`:
  ```
  id UUID PK
  tenant_id UUID FK
  contact_id UUID FK
  source_type TEXT  -- 'fact'|'message_window'|'summary'
  source_id UUID    -- FK opcional
  content TEXT NOT NULL
  embedding VECTOR(1024) NOT NULL
  created_at TIMESTAMPTZ
  INDEX(tenant_id, contact_id)
  INDEX USING hnsw (embedding vector_cosine_ops)
  ```
- Tool del agente `recordar(query: str, top_k: int = 5)` → devuelve los chunks más relevantes con sus fuentes y timestamps.
- Filtro siempre con `tenant_id AND contact_id`.

### f) Edición manual

- Endpoint `PATCH /contacts/{id}` (datos base) y `PUT /contacts/{id}/facts/{key}` (hechos).
- UI inbox: panel lateral con datos + lista editable de hechos.
- Audit log obligatorio en cada edición.

---

## 7. Interfaz `Connector` — ABC en código

```python
# src/connectors/base.py
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal, Any
from uuid import UUID
from pydantic import BaseModel


ConnectorKind = Literal["ecommerce", "knowledge", "crm", "calendar"]


class ToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]   # JSON Schema (Anthropic tools format)
    callable_ref: str               # "connectors.woocommerce:search_products"


class SyncResult(BaseModel):
    items_processed: int
    items_created: int
    items_updated: int
    items_deleted: int
    errors: list[str]
    started_at: datetime
    finished_at: datetime
    cursor: str | None = None       # para incremental


class SearchResult(BaseModel):
    id: str
    title: str
    snippet: str
    url: str | None
    score: float
    metadata: dict[str, Any]


class WebhookVerification(BaseModel):
    valid: bool
    reason: str | None = None


class Connector(ABC):
    """Contrato que TODO conector externo debe implementar.

    Cada conector pertenece a un tenant. El núcleo del Hub no conoce los
    detalles del proveedor: solo invoca esta interfaz.
    """

    name: str
    kind: ConnectorKind
    version: str

    def __init__(self, tenant_id: UUID, config_id: UUID): ...

    @abstractmethod
    def configure(self, credentials: dict[str, Any]) -> None:
        """Persiste y valida credenciales. Cifra at rest."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Llama un endpoint barato del proveedor para validar credenciales."""

    @abstractmethod
    def sync_full(self) -> SyncResult: ...

    @abstractmethod
    def sync_incremental(self, since: datetime) -> SyncResult: ...

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification: ...

    @abstractmethod
    def webhook_handler(self, payload: dict[str, Any], headers: dict[str, str]) -> None: ...

    @abstractmethod
    def expose_tools(self) -> list[ToolSchema]:
        """Tools que el agente Claude puede llamar."""

    @abstractmethod
    def search(self, query: str, top_k: int = 10, filters: dict[str, Any] | None = None) -> list[SearchResult]: ...
```

### Implementación WooCommerce (esqueleto)

```python
# src/connectors/woocommerce/connector.py
class WooCommerceConnector(Connector):
    name = "woocommerce"
    kind = "ecommerce"
    version = "1.0.0"

    def configure(self, credentials):
        # credentials = {"site_url", "consumer_key", "consumer_secret"}
        # Persistir cifrado en `connector_configs.encrypted_credentials`.
        ...

    def test_connection(self):
        return self._client.get("/wc/v3/system_status").status_code == 200

    def sync_full(self):
        # paginar /wc/v3/products, persistir en `products`,
        # encolar embeddings de descripcion+atributos
        ...

    def sync_incremental(self, since):
        # /wc/v3/products?modified_after=since
        ...

    def verify_webhook(self, payload, headers):
        # X-WC-Webhook-Signature = HMAC-SHA256(secret, body) base64
        ...

    def webhook_handler(self, payload, headers):
        # mapear topic -> action: product.updated, order.updated, ...
        ...

    def expose_tools(self):
        return [
            ToolSchema(
                name="buscar_productos",
                description="Busca productos del catálogo del negocio. Recibe query libre.",
                input_schema={"type":"object","properties":{
                    "query":{"type":"string"},
                    "max_results":{"type":"integer","default":5}}},
                callable_ref="connectors.woocommerce.tools:buscar_productos",
            ),
            ToolSchema(
                name="consultar_stock_y_precio",
                description="Consulta stock, precio y link de un producto por SKU o ID.",
                input_schema={"type":"object","properties":{
                    "sku":{"type":"string"},"product_id":{"type":"integer"}}},
                callable_ref="connectors.woocommerce.tools:consultar_stock_y_precio",
            ),
            ToolSchema(
                name="historial_pedidos_contacto",
                description="Lista los pedidos del contacto actual (resueltos por email/teléfono).",
                input_schema={"type":"object","properties":{
                    "limit":{"type":"integer","default":5}}},
                callable_ref="connectors.woocommerce.tools:historial_pedidos_contacto",
            ),
        ]

    def search(self, query, top_k=10, filters=None):
        # búsqueda híbrida: pgvector cosine + ts_vector full-text + rerank
        ...
```

### Cómo se agrega Shopify después (sin tocar core)

```python
# src/connectors/shopify/connector.py
class ShopifyConnector(Connector):
    name = "shopify"
    kind = "ecommerce"
    version = "1.0.0"
    # implementa los mismos métodos contra Shopify Admin API REST
    # productos -> persiste en MISMA tabla `products` con `connector_id`
    # webhooks -> verifica firma HMAC X-Shopify-Hmac-SHA256
    # tools -> mismos nombres semánticos: buscar_productos, consultar_stock_y_precio,
    # historial_pedidos_contacto -> el agente no nota la diferencia
    ...

# Registro
# src/connectors/registry.py
CONNECTOR_REGISTRY: dict[str, type[Connector]] = {
    "woocommerce": WooCommerceConnector,
    "shopify": ShopifyConnector,   # solo agregar acá
}
```

**Clave del diseño:** los **nombres de tools son semánticos, no específicos del proveedor**. El agente llama `buscar_productos` y el conector activo del tenant resuelve a Woo o a Shopify. La persona del agente nunca menciona Woo/Shopify.

---

## 8. Sincronización WooCommerce

### Sync inicial (full)

- Trigger: al conectar el conector y pasar `test_connection`.
- Job Celery `wc_sync_full(tenant_id, config_id)`:
  1. Categorías y atributos (poco volumen).
  2. Productos paginando `per_page=100`. Por página: persistir en `products`, encolar `embed_product(product_id)`.
  3. Variaciones por cada producto variable.
  4. Cupones (read-only, opcional).
- Throttling: max 4 req/s al Woo del tenant para no estresar su WP.
- Tiempo estimado: 5-15 min para catálogos de 1-5K productos.
- Reportar progreso en `connector_configs.last_sync_status` cada N páginas.

### Sync incremental

**Mecanismo principal: webhooks de WooCommerce.**

- Endpoint: `POST /webhooks/woo/{tenant_id}/{connector_config_id}`.
- Auth: `X-WC-Webhook-Signature` (HMAC-SHA256 con secret por config).
- Topics suscritos:
  - `product.created`
  - `product.updated` (incluye cambios de stock)
  - `product.deleted`
  - `order.created`
  - `order.updated`
- Handler: persiste en `products`/`orders`, encola re-embedding si cambió título/descripción/atributos.

**Mecanismo secundario: polling (fallback).**

- Cron diario `wc_sync_incremental_safety(since=last_full_sync_at)` para captar webhooks perdidos.
- Cron por tenant cada 30 min con flag `aggressive_polling` para tenants con stock muy volátil.

### Frecuencia

| Evento | Mecanismo | Latencia |
|---|---|---|
| Producto nuevo | Webhook | < 5 s |
| Stock cambia | Webhook | < 5 s |
| Precio cambia | Webhook | < 5 s |
| Producto borrado | Webhook (tombstone) | < 5 s |
| Orden creada | Webhook | < 5 s |
| Cupones | Polling 6h | 6 h |
| Safety net global | Polling 24h | 24 h |

### Stock

- **No** mantener cache fresco del stock en BD para garantías estrictas: el endpoint de tool `consultar_stock_y_precio` puede preguntar al Woo en vivo cuando `flags.live_stock=true`.
- Default: BD local con TTL implícito (se confía en webhooks). Si webhook falla → safety net 24h corrige.

---

## 9. Storage de archivos

**Recomendación: S3-compatible, prefijado por tenant.**

| Eje | S3-compatible (R2/AWS) | DB (bytea) | Sistema de archivos local |
|---|---|---|---|
| Costo | Bajo (R2 sin egress). | Caro a escala. | Dependiente del host. |
| Escala | Ilimitada. | DB se infla. | Limitada. |
| Backup | Lifecycle nativo. | Backups DB pesados. | Manual. |
| Multi-tenant | Prefijos / policies. | `tenant_id` columna. | Difícil. |

### Diseño

- **Provider:** Cloudflare R2 (preferido por costo de egress = 0). AWS S3 alternativa.
- **Bucket:** uno por ambiente (`conexa-files-prod`, `conexa-files-stg`, `conexa-files-dev`).
- **Layout:**
  ```
  s3://conexa-files-prod/
    {tenant_id}/
      kb/{document_id}/{filename}
      kb/{document_id}/chunks/{chunk_id}.txt   (opcional, mayor traza)
      products/{product_id}/{image_id}.{ext}
      messages/{message_id}/{filename}         (media de WhatsApp)
      avatars/{contact_id}.jpg
  ```
- **Acceso:**
  - Subida: signed URL POST (TTL 5 min) generado por backend después de validar `tenant_id` y permisos.
  - Lectura interna: SDK con credenciales del servicio.
  - Lectura cliente: signed URL GET (TTL configurable, default 15 min).
- **Aislamiento:**
  - **Capa 1 (lógica):** todo path se construye en código a partir de `tenant_id` autenticado. Imposible que el cliente A pida `/{tenant_b}/...` si pasa por nuestro signer.
  - **Capa 2 (futuro, si tenant enterprise lo pide):** policies IAM por prefix con roles distintos por tenant.
- **Dev local:** MinIO con misma API.

---

## 10. Agente con tools — runtime

### Loop básico (Anthropic Messages API)

```python
def run_agent_turn(conversation, user_message):
    messages = build_history(conversation)        # últimos N + resumen
    system = render_system_prompt(conversation)   # persona + memoria + horario
    tools = collect_tools_for_number(conversation.wa_number_id)

    cost_acc = 0.0
    tool_calls = 0
    MAX_TOOL_CALLS_PER_TURN = 5
    MAX_COST_PER_TURN_CENTS = number.policy.max_cost_per_message_cents

    while True:
        if tool_calls >= MAX_TOOL_CALLS_PER_TURN:
            return finalize("max_tool_calls", "lo derivo a un humano")
        if cost_acc * 100 >= MAX_COST_PER_TURN_CENTS:
            return finalize("max_cost", "lo derivo a un humano")

        resp = anthropic.messages.create(
            model=model_for(conversation),     # Sonnet o Haiku según policy
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=1024,
        )
        cost_acc += compute_cost(resp.usage, model_for(conversation))
        log_llm_call(resp, cost_acc)

        if resp.stop_reason == "end_turn":
            return finalize("ok", resp.content)

        if resp.stop_reason == "tool_use":
            for block in resp.content:
                if block.type != "tool_use": continue
                tool_calls += 1
                result = execute_tool(
                    block.name, block.input,
                    tenant_id=conversation.tenant_id,
                    contact_id=conversation.contact_id,
                    timeout_s=10,
                )
                log_tool_invocation(...)
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                ]})
            continue

        return finalize("unknown_stop", "...")
```

### Decisiones clave

| Tema | Decisión | Por qué |
|---|---|---|
| **Cuándo Claude llama tool** | Decisión propia del modelo, guiada por descripciones precisas en `expose_tools()`. | El modelo decide mejor que reglas hardcoded. Calidad depende de descripciones. |
| **Modelo por defecto** | Sonnet 4.6 para conversación; Haiku 4.5 para extracción de hechos y resúmenes. | Sonnet calidad/costo balanceado, Haiku ~10x más barato para tareas estructuradas. |
| **Cap de tool calls por turno** | 5. Si excede → escalate. | Evita loops infinitos sin penalizar consultas legítimas. |
| **Cap de costo por turno** | Configurable por número (default 5¢). Si excede → escalate. | Control de costo por tenant. |
| **Timeout por tool** | 10 s default; configurable por tool. | Evita colgar al usuario; mejor responder con "lo consulto y te aviso". |
| **Logging** | Tabla `tool_invocations` (input, output, latency, error, cost). Trace ID por turno. | Debug + métricas + facturación. |
| **Rate limit** | Por `(tenant_id, contact_id)` y por `(tenant_id, wa_number_id)`. Sliding window Redis. | Evita abuso si un contacto manda 100 msgs/min. |
| **Rate limit WhatsApp side** | Throttle de envío saliente con jitter humano (1-3s entre msgs por número), max msgs/min por número, max msgs/hora. Configurable por tenant. | Reduce riesgo de ban. |
| **Detección de loops** | Si el mismo tool se llama 3+ veces con input similar en un turno → abortar y escalate. | Defensa adicional. |

### Anti-hallucination en e-commerce

- Tool `buscar_productos` retorna SIEMPRE: `id, sku, nombre, precio, stock, url`.
- System prompt incluye regla dura: "Cuando hables de un producto debes incluir el precio y link exactos del resultado del tool. Si no usaste el tool, decí explícitamente que vas a consultar."
- Validación post-respuesta: si la respuesta contiene un precio numérico que no aparece en los `tool_result` recientes → flag y reescritura. (Implementación en Fase 7.)

---

## 11. Modelo de datos completo (DDL resumido)

### Convención

- Todas las tablas operativas tienen `tenant_id UUID NOT NULL` con índice.
- PKs UUID v7 (orden temporal, mejor cache locality que v4).
- Timestamps `TIMESTAMPTZ`.
- Soft delete solo donde aplica (`deleted_at TIMESTAMPTZ NULL`); resto, hard delete con audit log.
- FKs con `ON DELETE` explícito (`CASCADE` solo en datos hijos del tenant).

### Tablas

```sql
-- =============== Identidad y tenancy ===============

CREATE TABLE tenants (
  id UUID PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',  -- active|suspended|deleted
  plan TEXT NOT NULL DEFAULT 'free',
  locale_default TEXT NOT NULL DEFAULT 'es',
  timezone TEXT NOT NULL DEFAULT 'America/Santiago',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT,                  -- argon2id; NULL si SSO
  display_name TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tenant_users (
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                  -- 'owner'|'admin'|'agent'|'viewer'
  invited_at TIMESTAMPTZ,
  accepted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX ON tenant_users(user_id);

-- =============== WhatsApp (WAHA) ===============

CREATE TABLE wa_numbers (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  phone_e164 TEXT,                     -- conocido tras conexión
  status TEXT NOT NULL,                -- 'pending_qr'|'connecting'|'connected'|'disconnected'|'banned'
  persona_id UUID REFERENCES personas(id),
  locale TEXT NOT NULL DEFAULT 'es',
  business_hours JSONB,                -- {"mon":[["09:00","18:00"]],...}
  out_of_hours_message TEXT,
  rate_limit_msgs_per_min INT NOT NULL DEFAULT 6,
  rate_limit_msgs_per_hour INT NOT NULL DEFAULT 200,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON wa_numbers(tenant_id);
CREATE UNIQUE INDEX ON wa_numbers(tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL;

CREATE TABLE wa_sessions (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  wa_number_id UUID NOT NULL REFERENCES wa_numbers(id) ON DELETE CASCADE,
  waha_node_id TEXT NOT NULL DEFAULT 'default',
  waha_session_name TEXT NOT NULL,     -- '{tenant_id}_{wa_number_id}'
  state TEXT NOT NULL,                 -- mirror del status de WAHA
  qr_code_b64 TEXT,                    -- temporal, durante alta
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON wa_sessions(waha_node_id, waha_session_name);
CREATE INDEX ON wa_sessions(tenant_id, wa_number_id);

CREATE TABLE wa_conversations (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  wa_number_id UUID NOT NULL REFERENCES wa_numbers(id),
  contact_id UUID NOT NULL REFERENCES contacts(id),
  status TEXT NOT NULL DEFAULT 'bot',  -- 'bot'|'waiting_agent'|'agent'|'closed'
  assigned_user_id UUID REFERENCES users(id),
  ai_summary TEXT,
  last_message_at TIMESTAMPTZ,
  unread_count INT NOT NULL DEFAULT 0,
  closed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON wa_conversations(tenant_id, wa_number_id, status, last_message_at DESC);
CREATE INDEX ON wa_conversations(tenant_id, contact_id);
CREATE INDEX ON wa_conversations(tenant_id, assigned_user_id) WHERE assigned_user_id IS NOT NULL;

CREATE TABLE wa_messages (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES wa_conversations(id) ON DELETE CASCADE,
  wa_number_id UUID NOT NULL REFERENCES wa_numbers(id),
  contact_id UUID NOT NULL REFERENCES contacts(id),
  direction TEXT NOT NULL,             -- 'in'|'out'
  source TEXT NOT NULL,                -- 'contact'|'bot'|'agent'|'system'
  waha_message_id TEXT,                -- ID externo
  body TEXT,
  media_url TEXT,
  media_kind TEXT,                     -- 'image'|'audio'|'video'|'document'|'sticker'
  media_meta JSONB,
  ack_status TEXT,                     -- 'sent'|'delivered'|'read'|'failed'  (monotónico)
  ack_updated_at TIMESTAMPTZ,
  reply_to_message_id UUID,
  cost_cents NUMERIC(8,4),             -- IA cost atribuible
  llm_metadata JSONB,                  -- model, tokens, etc.
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON wa_messages(tenant_id, conversation_id, created_at);
CREATE INDEX ON wa_messages(tenant_id, contact_id, created_at);
CREATE UNIQUE INDEX ON wa_messages(tenant_id, waha_message_id) WHERE waha_message_id IS NOT NULL;
-- ACK monotónico se enforce en aplicación (orden sent < delivered < read; failed terminal)
-- Full-text search:
ALTER TABLE wa_messages ADD COLUMN body_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(body,''))) STORED;
CREATE INDEX ON wa_messages USING gin(body_tsv);

-- LID->PN cache (portado de FitnessIA, capa 4)
CREATE TABLE wa_lid_pn_map (
  tenant_id UUID NOT NULL,
  lid TEXT NOT NULL,
  pn TEXT NOT NULL,
  resolved_via TEXT NOT NULL,          -- '@lid_in_chat'|'contact_card'|'group_lookup'|'cache_replay'
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, lid)
);

-- =============== Contactos y memoria ===============

CREATE TABLE contacts (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  phone_e164 TEXT NOT NULL,
  display_name TEXT,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  birth_date DATE,
  address_line TEXT,
  address_city TEXT,
  address_country TEXT,
  locale TEXT,
  opt_in_marketing BOOLEAN NOT NULL DEFAULT FALSE,
  opt_in_marketing_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, phone_e164)
);
CREATE INDEX ON contacts(tenant_id, email) WHERE email IS NOT NULL;

CREATE TABLE contact_tags (
  tenant_id UUID NOT NULL,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  created_by UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, contact_id, tag)
);
CREATE INDEX ON contact_tags(tenant_id, tag);

CREATE TABLE contact_facts (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value_text TEXT,
  value_type TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence NUMERIC(3,2),
  extracted_from_message_id UUID REFERENCES wa_messages(id),
  created_by_user_id UUID REFERENCES users(id),
  last_confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, contact_id, key)
);
CREATE INDEX ON contact_facts(tenant_id, contact_id);

CREATE TABLE contact_memory_embeddings (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,           -- 'fact'|'message_window'|'summary'
  source_id UUID,
  content TEXT NOT NULL,
  embedding VECTOR(1024) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON contact_memory_embeddings(tenant_id, contact_id);
CREATE INDEX ON contact_memory_embeddings USING hnsw (embedding vector_cosine_ops);

-- =============== Persona y agentes ===============

CREATE TABLE personas (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name TEXT NOT NULL,                  -- "Asesora Showroom"
  system_prompt TEXT NOT NULL,
  tone TEXT,                           -- 'cercana'|'formal'|'breve'|...
  locale TEXT NOT NULL DEFAULT 'es',
  model_default TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
  model_extraction TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
  policy JSONB NOT NULL DEFAULT '{}',  -- caps, escalation rules
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON personas(tenant_id);

-- =============== Conectores ===============

CREATE TABLE connectors (
  id UUID PRIMARY KEY,                 -- declarado en código; sincronizado a esta tabla
  name TEXT UNIQUE NOT NULL,           -- 'woocommerce','shopify',...
  kind TEXT NOT NULL,
  version TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE connector_configs (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connector_id UUID NOT NULL REFERENCES connectors(id),
  display_name TEXT NOT NULL,
  encrypted_credentials BYTEA NOT NULL,  -- AES-GCM con DEK por tenant
  webhook_secret TEXT NOT NULL,
  status TEXT NOT NULL,                -- 'pending'|'connected'|'error'|'disabled'
  last_full_sync_at TIMESTAMPTZ,
  last_incremental_sync_at TIMESTAMPTZ,
  last_error TEXT,
  config JSONB NOT NULL DEFAULT '{}',  -- mapeos custom
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON connector_configs(tenant_id);
CREATE INDEX ON connector_configs(tenant_id, connector_id);

-- =============== E-commerce (WooCommerce + futuros) ===============

CREATE TABLE products (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connector_config_id UUID NOT NULL REFERENCES connector_configs(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,           -- ID en proveedor
  sku TEXT,
  name TEXT NOT NULL,
  description_short TEXT,
  description_long TEXT,
  price_regular NUMERIC(12,2),
  price_sale NUMERIC(12,2),
  currency TEXT,
  stock_quantity INT,
  stock_status TEXT,                   -- 'in_stock'|'out_of_stock'|'on_backorder'
  url TEXT,
  images JSONB,
  categories JSONB,
  attributes JSONB,
  variations JSONB,
  raw JSONB,                           -- payload original del proveedor
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, connector_config_id, external_id)
);
CREATE INDEX ON products(tenant_id, sku) WHERE sku IS NOT NULL;
CREATE INDEX ON products(tenant_id, connector_config_id);
ALTER TABLE products ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('spanish',
      coalesce(name,'')||' '||coalesce(description_short,'')||' '||coalesce(description_long,'')
    )
  ) STORED;
CREATE INDEX ON products USING gin(search_tsv);

CREATE TABLE product_embeddings (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  content TEXT NOT NULL,               -- texto que se embebió
  embedding VECTOR(1024) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON product_embeddings(tenant_id, product_id);
CREATE INDEX ON product_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE orders (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connector_config_id UUID NOT NULL REFERENCES connector_configs(id),
  external_id TEXT NOT NULL,
  contact_id UUID REFERENCES contacts(id),  -- resuelto por email/teléfono
  status TEXT,
  total NUMERIC(12,2),
  currency TEXT,
  placed_at TIMESTAMPTZ,
  raw JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, connector_config_id, external_id)
);
CREATE INDEX ON orders(tenant_id, contact_id);

-- =============== KB / RAG genérico ===============

CREATE TABLE kb_documents (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  wa_number_id UUID REFERENCES wa_numbers(id),  -- NULL = todos los números del tenant
  title TEXT NOT NULL,
  source_type TEXT NOT NULL,           -- 'pdf'|'docx'|'txt'|'md'|'url'
  source_uri TEXT,
  storage_uri TEXT,                    -- s3://...
  status TEXT NOT NULL,                -- 'queued'|'processing'|'ready'|'failed'
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON kb_documents(tenant_id, wa_number_id);

CREATE TABLE kb_chunks (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  document_id UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
  wa_number_id UUID,
  content TEXT NOT NULL,
  position INT NOT NULL,
  embedding VECTOR(1024) NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON kb_chunks(tenant_id, document_id);
CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops);
ALTER TABLE kb_chunks ADD COLUMN content_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(content,''))) STORED;
CREATE INDEX ON kb_chunks USING gin(content_tsv);

-- =============== Tools y observabilidad del agente ===============

CREATE TABLE tool_invocations (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  conversation_id UUID NOT NULL REFERENCES wa_conversations(id) ON DELETE CASCADE,
  message_id UUID REFERENCES wa_messages(id),
  tool_name TEXT NOT NULL,
  input_json JSONB NOT NULL,
  output_json JSONB,
  status TEXT NOT NULL,                -- 'ok'|'error'|'timeout'
  error TEXT,
  latency_ms INT,
  cost_cents NUMERIC(8,4),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON tool_invocations(tenant_id, conversation_id, created_at);
CREATE INDEX ON tool_invocations(tenant_id, tool_name, created_at);

-- =============== API pública y webhooks salientes ===============

CREATE TABLE api_keys (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  hashed_key TEXT NOT NULL,            -- sha256 del token; el token solo se muestra al crear
  prefix TEXT NOT NULL,                -- "ck_live_a1b2c3..." (8 char display)
  scopes TEXT[] NOT NULL,
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_by_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON api_keys(tenant_id);
CREATE UNIQUE INDEX ON api_keys(hashed_key);

CREATE TABLE webhooks_out (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  secret TEXT NOT NULL,
  events TEXT[] NOT NULL,              -- ['message.received','message.sent','conversation.escalated',...]
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  last_success_at TIMESTAMPTZ,
  last_failure_at TIMESTAMPTZ,
  consecutive_failures INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON webhooks_out(tenant_id);

CREATE TABLE webhook_deliveries (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  webhook_id UUID NOT NULL REFERENCES webhooks_out(id) ON DELETE CASCADE,
  event TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL,                -- 'pending'|'success'|'failed'|'dead'
  attempts INT NOT NULL DEFAULT 0,
  last_response_status INT,
  last_response_body TEXT,
  next_attempt_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON webhook_deliveries(tenant_id, status, next_attempt_at);

-- =============== Métricas, billing, audit ===============

CREATE TABLE usage_metrics (
  tenant_id UUID NOT NULL,
  metric_date DATE NOT NULL,
  messages_in INT NOT NULL DEFAULT 0,
  messages_out INT NOT NULL DEFAULT 0,
  conversations_active INT NOT NULL DEFAULT 0,
  llm_input_tokens BIGINT NOT NULL DEFAULT 0,
  llm_output_tokens BIGINT NOT NULL DEFAULT 0,
  llm_cost_cents NUMERIC(12,4) NOT NULL DEFAULT 0,
  storage_bytes BIGINT NOT NULL DEFAULT 0,
  api_requests INT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, metric_date)
);

CREATE TABLE quotas (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  max_messages_per_month INT,
  max_conversations_active INT,
  max_llm_cost_cents_per_month INT,
  max_storage_bytes BIGINT,
  max_api_requests_per_day INT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id UUID PRIMARY KEY,
  tenant_id UUID,                      -- NULL para acciones globales (admin del Hub)
  actor_user_id UUID,
  actor_api_key_id UUID,
  action TEXT NOT NULL,                -- 'contact.fact.update', 'connector.configure', ...
  target_type TEXT,
  target_id UUID,
  diff JSONB,
  ip TEXT,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log(tenant_id, created_at DESC);
CREATE INDEX ON audit_log(tenant_id, target_type, target_id);
```

### Notas

- `flows` (de la lista del prompt) se modela en código v1 como pipelines simples; cuando aparezcan flujos visuales, se agrega tabla `flows` con DSL JSONB.
- `tools` no es tabla — los tools son código (clases en `src/agent/tools/...`); la tabla `tool_invocations` registra ejecuciones, no definiciones.
- pgvector `VECTOR(1024)`: ajustar a la dim del modelo elegido finalmente (Voyage = 1024).
- Cifrado de credenciales en `connector_configs`: AES-GCM con DEK por tenant, KEK en KMS o env var por ambiente.

---

## 12. Plan por fases (una fase = una sesión = un PR)

> Regla del proyecto: una fase no se mezcla con otra. Cada fase abre rama desde la anterior mergeada.

### Fase 0 — Skeleton + multi-tenant + auth + tests de aislamiento

- **Objetivo:** levantar el repo con FastAPI, DB con migraciones, modelo `tenants/users/tenant_users`, login JWT, middleware `tenant_scope()`, endpoint `/v1/me`.
- **Done cuando:**
  - `docker-compose up` levanta web + worker + postgres + redis.
  - Alta de tenant + user owner por endpoint.
  - Login con JWT, token incluye `tenant_id` y `role`.
  - Test E2E: `tenant A` no puede leer `tenant B` por NINGUNA ruta de tenant scoped (al menos 5 ejemplos: list, get-by-id, mutate, search, paginate).
- **Archivos:** `pyproject.toml`, `src/main.py`, `src/config.py`, `src/db.py`, `src/auth/`, `src/tenancy/`, `alembic/...`, `tests/test_isolation.py`, `docker-compose.yml`, `.env.example`, `Makefile`.
- **Riesgos:** olvidar `tenant_id` en alguna query → setear lint (SQLAlchemy event hook que marca queries sin filtro de tenant).
- **PR:** "fase 0: skeleton multi-tenant + auth + aislamiento"

### Fase 1 — Conexión WAHA de un número + webhook + persistencia mensajes

- **Objetivo:** un tenant puede dar de alta un número, escanear QR, recibir y enviar mensajes; persistir en `wa_messages`; ACK monotónico.
- **Done:**
  - Endpoint `POST /v1/wa-numbers` crea sesión WAHA; devuelve QR.
  - Webhook `POST /webhook/waha/{node_id}` autenticado por `X-WAHA-Token`.
  - Mensajes entrantes se guardan; salientes se envían con cliente WAHA portado.
  - LID→PN funcionando con las 4 capas + cache + lock.
  - ACK actualiza con monotonicidad (`sent < delivered < read`; `failed` terminal).
  - `ensure_waha_webhooks()` corre en lifespan startup del backend (re-aplica webhooks tras restart de WAHA).
  - Smoke test E2E con WAHA en docker-compose y un mock-receiver.
- **Archivos a portar (limpiando legacy):**
  - `src/messaging/waha_client.py` ← portar de `_reference/fitnessia/src/messaging/waha_client.py` **renombrando los siguientes símbolos**:
    - `evolution_instance_name` → `waha_session_name` (en signature de funciones que reciben el campo del modelo) y en el modelo `WhatsAppNumber` → `WaNumber`.
    - Borrar la rama `connection_type == "qr"` y `cloud_api` en `dispatcher.py`. En el Hub no existe el discriminador.
    - Conservar tal cual: `WahaAPIError(status=-1, -2)`, `_resolve_target` con ABORT en LID sin resolver, `resolve_lid_to_pn()` en 4 capas con `_LID_CACHE_LOCK`, `_preload_lid_pn_cache()` workaround, `mark_as_read` (sendSeen global), `send_typing` global.
    - Conservar literal: `webhook_entry["events"] = ["message", "message.any", "message.ack", "session.status"]`. Si se omite `message.ack`, el bloque ACK queda inerte aunque el código esté bien (lección aprendida en SYNEX).
  - `src/messaging/lid_resolver.py` ← portar la lógica de `wa_lid_rehydrate.py`. Mantener `_LID_MIN_DIGITS = 14`. Renombrar fn a `rehydrate_conversation_phone` sin cambiar comportamiento.
  - `src/messaging/wa_lookup.py` ← portar de `wa_tenant_lookup.py` **eliminando** `get_tenant_by_phone_number_id` (Cloud API). Conservar solo `get_wa_number_by_session_name()` con cache 5 min + invalidación.
  - `src/messaging/dispatcher.py` ← reescribir simplificado: solo branch WAHA. Eliminar `purpose` enum y `_tenant_config_for_cloud_api`.
  - `src/messaging/ack.py` ← nueva (no existe en FitnessIA como archivo separado, está esparcido). Implementar invariante de monotonicidad con tabla de transición y tests de las 16 transiciones.
  - `src/messaging/typing_state.py` ← portar tal cual (cache TTL para typing del contacto).
  - `src/messaging/webhook.py` ← nueva. Endpoint `POST /webhook/waha/{node_id}` con verificación `X-WAHA-Token`, dispatch a `message`/`message.any`/`message.ack`/`session.status`. Usa `bypass_tenant_filter()` al inicio para resolver `wa_number_id → tenant_id` y luego entra a `tenant_scope()`.
  - `src/wa/models.py` ← `WaNumber`, `WaSession`, `WaConversation`, `WaMessage`. Sin `connection_type`. Sin `purpose` enum. Sí `tags TEXT[]` libre. `waha_node_id TEXT DEFAULT 'default'` listo para sharding (§4).
  - `src/wa/api.py` ← endpoints REST: alta de número, listar, status, QR, request-pairing-code, logout.
  - `alembic/versions/...` ← migraciones iniciales WAHA.
- **Decisiones operativas (no obvias del código fuente):**
  - **Almacenar el QR como evento transitorio** (no persistir): WAHA emite `qr` por webhook `session.status`; el frontend hace polling al endpoint `GET /v1/wa-numbers/{id}/qr` que consulta WAHA y lo devuelve base64. Una vez `WORKING`, no hay QR.
  - **Reconexión:** un backoff de 3 intentos (0.5/1/2 s) ya está en el cliente; agregar a nivel sesión un retry diferido (5 min, 30 min, 2 h) si `session.status == FAILED`.
  - **No reusar `purpose`:** el tenant define `tags` libres en `wa_numbers.tags`. Eliminar la cadena de fallback por purpose; reemplazar por: tag explícito → `is_default` → primero activo.
- **Riesgos específicos:**
  - **WAHA no persiste webhooks:** si `ensure_waha_webhooks()` no corre en startup, los nuevos mensajes nunca llegan. Test obligatorio: reiniciar WAHA y verificar que entra mensaje.
  - **`@lid` enviado como chatId** (Bug prod 2026-04-23 SYNEX) → mensaje a número ficticio. **No mergear** sin test que verifica que `WahaAPIError(-2)` se levanta cuando LID no resuelve.
  - **`X-WAHA-Token` filtrado**: rotación trivial via PUT `/api/sessions/{name}` config; documentar runbook.
- **PR:** "fase 1: WAHA QR + webhook + persistencia"

### Fase 2 — Bot Claude con persona/locale/tono (sin tools, sin memoria larga)

- **Objetivo:** cuando llega mensaje, el bot responde con Claude Sonnet usando system prompt construido a partir de la persona del número.
- **Done:**
  - Tabla `personas`. Editor en panel (mínimo CRUD por API).
  - Servicio `agent_service.respond(conversation, message)` con últimos N=10 turnos, sin tools.
  - Locale + horario respetados (out-of-hours message).
  - Costo y tokens registrados en `wa_messages.llm_metadata` y `usage_metrics`.
- **Archivos:** `src/agent/service.py`, `src/agent/prompt_builder.py`, `src/agent/llm.py`.
- **PR:** "fase 2: bot Claude con persona y locale"

### Fase 3 — Memoria de corto plazo (últimos N + resumen)

- **Objetivo:** conversaciones largas no exceden tokens; resumen automático.
- **Done:**
  - `wa_conversations.ai_summary` actualizado cuando turnos > N.
  - Resumen con Haiku, prompt versionado.
  - Tests: conversación de 100 turnos no excede 8K tokens en input y mantiene coherencia.
- **PR:** "fase 3: memoria corto plazo + resumen"

### Fase 4 — Memoria de largo plazo (hechos del contacto)

- **Objetivo:** `contact_facts` + extracción automática + edición manual.
- **Done:**
  - Migración `contact_facts` y `contact_memory_embeddings`.
  - Job `extract_contact_facts` con Haiku, idempotente.
  - Inyección de hechos en system prompt (top-K).
  - API `PATCH /contacts/{id}` y `PUT /contacts/{id}/facts/{key}`.
  - UI inbox: panel lateral con datos y hechos editables.
  - Audit log de cambios manuales.
- **PR:** "fase 4: memoria contacto + hechos"

### Fase 5 — `Connector` ABC + WooCommerce sync full

- **Objetivo:** primer conector e-commerce funcionando con sync inicial.
- **Done:**
  - `Connector` ABC + registro.
  - `WooCommerceConnector.configure/test_connection/sync_full` implementado.
  - Tabla `products` poblada con catálogo del tenant.
  - Cifrado de credenciales operativo.
- **PR:** "fase 5: connector ABC + woocommerce sync full"

### Fase 6 — Sync incremental WooCommerce + embeddings de productos

- **Objetivo:** webhooks Woo + embeddings + búsqueda híbrida.
- **Done:**
  - Endpoint `POST /webhooks/woo/{tenant_id}/{config_id}` con verificación HMAC.
  - Handler procesa product/order events; encola embeddings.
  - `product_embeddings` poblada.
  - Tool `search()` del conector con BM25 + cosine + reranking.
- **PR:** "fase 6: woo incremental + embeddings"

### Fase 7 — Tools del agente (catálogo, stock, órdenes)

- **Objetivo:** Claude llama tools del conector activo del tenant.
- **Done:**
  - Loop de tool_use con caps de costo y de tool_calls.
  - Tools: `buscar_productos`, `consultar_stock_y_precio`, `historial_pedidos_contacto`, `escalar_a_humano`.
  - `tool_invocations` registrando cada llamada.
  - E2E: contacto pregunta por producto → bot responde con precio y link reales del Woo.
- **PR:** "fase 7: agent tools + e-commerce"

### Fase 8 — Inbox + handoff humano

- **Objetivo:** UI inbox completa con asignación, macros, notas, edición memoria.
- **Done:**
  - UI Next.js con lista, detalle, typing en vivo (websocket), ACK badges.
  - Asignación manual + automática (round-robin).
  - Status `bot|waiting_agent|agent|closed` con transiciones auditadas.
  - Macros / respuestas predefinidas.
  - Notas internas.
- **PR:** "fase 8: inbox + handoff"

### Fase 9 — RAG genérico (PDF/URL) por tenant y por número

- **Objetivo:** subida de docs, ingestión, tool `buscar_en_kb`.
- **Done:**
  - Upload signed URL.
  - Pipeline ingesta (PDF/DOCX/TXT/MD/URL) con chunker + embed.
  - Tool `buscar_en_kb(query)` con citas (chunk_id + document title).
  - Filtro por `wa_number_id` opcional.
- **PR:** "fase 9: RAG genérico"

### Fase 10 — API pública + webhooks salientes

- **Objetivo:** terceros se integran via API key.
- **Done:**
  - `api_keys` con scopes.
  - Endpoints documentados (OpenAPI): `POST /v1/messages`, `GET /v1/conversations/{id}`, `GET /v1/contacts`, `PATCH /v1/contacts/{id}`, `GET /v1/products`.
  - `webhooks_out` configurables; entrega con retries + DLQ.
- **PR:** "fase 10: API pública + webhooks salientes"

### Fase 11 — Billing + métricas + cuotas

- **Objetivo:** dashboard con métricas, cuotas operativas, exportes, integración Stripe (opcional).
- **Done:**
  - `usage_metrics` agregado por job nocturno.
  - Cuotas enforced en hot path (envío, llamadas IA).
  - Dashboard con mensajes, costo IA, % bot, latencia.
  - Export CSV de métricas + audit_log.
- **PR:** "fase 11: billing + métricas + cuotas"

### Fase 12 — Segundo conector e-commerce (Shopify)

- **Objetivo:** validar interfaz `Connector` agregando Shopify sin tocar core.
- **Done:**
  - `ShopifyConnector` implementa los mismos métodos.
  - Tests del agente pasan idénticos cambiando `connector_config` a Shopify.
  - Documento "cómo agregar un conector" actualizado.
- **PR:** "fase 12: shopify connector"

---

## 13. Riesgos y mitigaciones

| # | Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|---|
| 1 | **Ban de chip WAHA** | Alta | Alto (tenant pierde número) | Rate limiting agresivo (msgs/min, msgs/hora por número), jitter humano (1-3 s entre msgs salientes), aviso al tenant en docs sobre buenas prácticas, no permitir broadcasts masivos. T&C: el ban es responsabilidad del tenant. Detección de pico de envíos → throttle automático. |
| 2 | **Fuga cross-tenant** | Media | Crítico | (a) `tenant_id` en TODA tabla operativa con índice; (b) middleware `tenant_scope()` obligatorio; (c) hook SQLAlchemy que loggea queries sin filtro de tenant en dev/test (CI falla); (d) tests de aislamiento por endpoint en cada fase; (e) row-level security opcional en Postgres como segunda capa; (f) prefijo S3 + signers que firman con `tenant_id` autenticado. |
| 3 | **Costo IA descontrolado** | Alta | Medio | Caps por tenant (msg, día, mes); cap por turno (max tool calls, max cost cents); selección de modelo (Sonnet vs Haiku) según tarea; cache de respuestas para queries idénticas en ventana corta; alertas a partir de 80% de cuota. |
| 4 | **Latencia con catálogos grandes** | Media | Medio | Embeddings precomputados; pgvector HNSW; búsqueda híbrida con prefiltro `tenant_id`; tools con `top_k` bajo (default 5); P95 monitoreado; reranking solo en top-N. |
| 5 | **Sync WooCommerce roto (webhooks fallando)** | Alta | Medio | Safety net diaria (full diff); alertas si `last_incremental_sync_at > 1h`; UI muestra estado de sync; tool `consultar_stock_y_precio` con flag para consulta en vivo cuando se requiera precisión. |
| 6 | **Hallucinations sobre productos** | Alta | Alto (cliente compra inexistente) | System prompt obliga a citar precio/link del tool; validador post-respuesta detecta números no presentes en `tool_result`; modo "estricto" que rechaza respuesta con datos no fundamentados; tests de regresión. |
| 7 | **Pérdida de sesión WAHA (chip desconectado)** | Media | Alto | Polling `session.status` + webhook → marcar `wa_numbers.status='disconnected'`; alerta inmediata al tenant; auto-reconexión con backoff (3 intentos); QR re-emisión sin perder histórico de conversaciones. |
| 8 | **Inconsistencia ACK** | Media | Bajo | Invariante de monotonicidad portado del sistema actual; transición ilegal se descarta + log; tests unitarios cubren las 16 transiciones. |
| 9 | **Webhooks salientes silenciosamente caídos** | Media | Medio | Cola con retries (1m, 5m, 30m, 2h, 12h, 24h), dead letter después de 24h; UI muestra estado por webhook; alertas si `consecutive_failures > 5`. |
| 10 | **Crecimiento de embeddings** (KB + productos + memoria) | Media | Medio (lentitud) | Particionado lógico por `tenant_id`; vacuum/reindex programado; plan de migración a Qdrant si pasamos 10M filas o P95 query > 200 ms. |
| 11 | **Credenciales filtradas (consumer key Woo, tokens API tenant)** | Baja | Crítico | AES-GCM at rest con DEK por tenant; KMS para KEK; logs nunca registran credenciales; rotación facilitada por API; pen-test antes de v1. |
| 12 | **Compartir embeddings entre tenants accidentalmente** | Baja | Crítico | Filtro `tenant_id` obligatorio en TODOS los queries vector; tests específicos; prohibido un índice global sin partitioning. |
| 13 | **Crecimiento de `tool_invocations` y `wa_messages`** | Alta | Medio | Particionado por mes; política de retención por tenant configurable (default 365 d); archivado a S3 frio. |
| 14 | **Hot tenant que satura recursos compartidos** | Media | Alto | Rate limit por tenant; quotas; slot reservation en pool de workers Celery (queues por tier); WAHA sharding cuando aplique. |
| 15 | **WAHA no persiste config de webhooks** (descubierto en código SYNEX `ensure_waha_webhooks`) | Cierta | Crítico (mensajes no entran) | `ensure_waha_webhooks()` en lifespan startup del backend. Health check periódico que verifica que cada sesión activa tiene `webhooks[].url == nuestro_endpoint`. Alerta inmediata si falta. |
| 16 | **`@lid` enviado como chatId → mensaje a número ficticio** (Bug prod SYNEX 2026-04-23, chat Martín) | Baja con LID resolver | Crítico (mensaje al desconocido equivocado) | Política de ABORT (`WahaAPIError(-2)`) en `_resolve_target` cuando LID no resuelve. **Nunca** fallback a `chatId=<lid>@lid`. Test obligatorio que valida el raise. |
| 17 | **WAHA `/contacts/lid-pn` devuelve vacío aunque store lo tiene** (bug WAHA conocido) | Cierta | Medio | `_preload_lid_pn_cache()` al arranque por sesión activa. 4 capas de fallback en `resolve_lid_to_pn` (cache → lid-pn → contacts/{lid} → escaneo full). |
| 18 | **`message.ack` no subscripto silenciosamente** (lección SYNEX FASE_WA_HOOK_ACK_SUBSCRIBE_L) | Media | Alto (UI sin tildes) | `webhook_entry["events"]` literal `["message","message.any","message.ack","session.status"]` con test que verifica config tras `ensure_waha_webhooks`. |

---

## 14. Lo que necesita input del usuario antes de arrancar Fase 0

> **Bloqueo de Fase 0 hasta que el usuario responda:**

1. **Confirmar nombre del producto** (ver §1).
2. **Confirmar repo nuevo** = `CHATPRO` y rama `claude/whatsapp-hub-saas-zSFto` (ya creada).
3. **Stack confirmado** (ver §3): ¿OK con FastAPI + Postgres + Redis + Celery + Next.js? ¿Algún cambio?
4. **Dominio y subdominios** previstos (api., app., wa-webhook., webhook-out.). ¿Quién registra el dominio?
5. **Hosting target** para MVP: Fly.io / Render / k8s propio / VPS. Define IaC.
6. **WAHA Plus license**: ¿ya hay licencia? ¿Self-hosted en qué proveedor? ¿Docker imagen versión?
7. **Anthropic API key** para dev/staging/prod.
8. **Voyage API key** (si aceptan recomendación) o fallback OpenAI key.
9. **S3 provider** (Cloudflare R2 recomendado): credenciales por ambiente.
10. **SMTP / email transaccional** (alta de tenant, recuperación pwd, alertas): SES / Postmark / Resend.
11. **Modelo de billing**: ¿activamos Stripe en Fase 11 o lo posponemos?
12. **Política de retención** default (mensajes, audit_log, embeddings de memoria).
13. **¿Se permite que un tenant pueda exportar todos sus datos (GDPR)?** Si sí, marcar que Fase 11 incluye export-zip.
14. **Confirmar que el plan refleja el sistema actual.** Los archivos de referencia clave (`waha_client.py`, `wa_lid_rehydrate.py`, `wa_tenant_lookup.py`, `dispatcher.py`, `typing_state.py`, `CLAUDE.md`) ya están en la rama `_reference/fitnessia` y se leyeron línea por línea (ver §0). `WHATSAPP_HUB_ARQUITECTURA_ACTUAL.md` no apareció — si existe en otro lado, sería bienvenido para Fase 0/1; si no existe, este plan + el código de `_reference/` son la fuente de verdad.
15. **Equipo de revisión** para PRs: ¿quién aprueba antes de merge?
16. **Idioma de la UI**: ¿solo español o multi-idioma desde el día 1 (i18n)?

---

## Reglas de trabajo (recordatorio)

- Idioma español en todo (código, UI, comentarios, commits).
- **Una fase = una sesión = un PR**. Sin agrupar.
- No tocar código hasta OK explícito del usuario sobre este plan.
- Multi-tenant desde la primera línea (no parche posterior).
- No pushear a main sin autorización explícita.
- Reusar lo bueno (cliente WAHA, LID→PN, ACK, persona, multi-tenant). No reinventar.

---

## Próxima acción

**Pelota al usuario:**
1. Leer este plan.
2. Decidir nombre, hosting, providers de §14.
3. Aportar archivos de referencia (`WHATSAPP_HUB_ARQUITECTURA_ACTUAL.md`) si están disponibles.
4. Dar OK explícito → comienza Fase 0 en una nueva rama desde la rama de este plan.
