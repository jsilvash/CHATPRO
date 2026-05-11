# Fase F1 — Frontend Next.js: Discovery

> **Estado:** Discovery. No hay código de producción todavía. Esperando OK del usuario.

---

## Estado actual

### ¿Qué hay en `frontend/` hoy?

Nada. El directorio `frontend/` no existe. El repo es 100% backend Python/FastAPI.

### Backend: lo que tenemos disponible para consumir

El backend expone **60+ endpoints REST** bajo `/v1` + **2 endpoints WebSocket** + **1 SSE stream**.
Todos los endpoints de negocio requieren `Authorization: Bearer <access_token>` (JWT HS256, 1h de vida).

#### Endpoints críticos para una v1 mínima viable

| Grupo | Endpoints clave | Para qué pantalla |
|---|---|---|
| Auth | `POST /v1/auth/login`, `POST /v1/auth/refresh`, `POST /v1/auth/logout` | `/login` |
| Me / Tenant | `GET /v1/me`, `GET /v1/tenants/me` | Header/nav global |
| WaNumbers | `GET/POST /v1/wa-numbers`, `GET /v1/wa-numbers/{id}/qr`, `GET /v1/wa-numbers/{id}/metrics` | `/numbers`, `/numbers/[id]` |
| Inbox (lista) | `GET /v1/inbox` (filtros: status, tag, assigned, búsqueda, paginación) | `/inbox` |
| Inbox (detalle) | `GET /v1/inbox/{conv_id}`, `POST /v1/inbox/{conv_id}/reply`, `POST /v1/inbox/{conv_id}/take`, `POST /v1/inbox/{conv_id}/close` | `/inbox/[conv_id]` |
| Inbox (notas/tags) | `POST/GET/DELETE /v1/inbox/{conv_id}/notes`, `POST/DELETE /v1/inbox/{conv_id}/tags` | `/inbox/[conv_id]` |
| WebSocket inbox | `ws://…/ws/inbox/{conv_id}?token=<jwt>` | `/inbox/[conv_id]` tiempo real |
| Notificaciones WS | `ws://…/ws/notifications/{tenant_id}?token=<jwt>` | Badge global + alertas |
| Contactos | `GET /v1/contacts/search`, `GET /v1/contacts/{id}`, `GET /v1/contacts/{id}/facts` | `/contacts` |
| Métricas dashboard | `GET /v1/metrics/dashboard` | `/dashboard` |
| Métricas SSE | `GET /v1/metrics/stream` (text/event-stream, Bearer o `?token=`) | `/dashboard` live |
| Conectores | `GET /v1/connectors`, `GET/POST /v1/connector-configs`, `POST …/configure`, `POST …/sync`, `GET …/stats` | `/connectors` |
| Knowledge | `GET /v1/knowledge`, `POST /v1/knowledge/upload`, `DELETE /v1/knowledge/{id}` | `/knowledge` |
| Personas | `GET/POST/PUT/DELETE /v1/personas` (vía `src/agent/api.py`) | `/settings/persona` |
| Usuarios | `GET/POST/PATCH /v1/users`, `PATCH /v1/users/{id}/deactivate`, `GET /v1/users/available-agents` | `/settings/team` |
| Billing | `GET /v1/billing/quotas`, `GET /v1/billing/metrics/summary`, `POST /v1/tenants/me/export` | `/settings/billing` |
| API Keys públicas | `GET/POST/DELETE /v1/api-keys` | `/settings/api-keys` |
| Respuestas rápidas | `GET/POST/PATCH/DELETE /v1/canned-responses`, `GET /v1/canned-responses/search` | `/settings/canned` |
| SLA report | `GET /v1/inbox/sla-report` | `/dashboard` o subpágina |

#### JWT: estructura relevante para el frontend

```
access token (1h):  { sub: user_id, tid: tenant_id, role: "owner"|"admin"|"agent", type: "access", exp, iat }
refresh token (7d): { sub: user_id, tid: tenant_id, jti, type: "refresh", exp, iat }
```

El backend acepta **solo `Authorization: Bearer <token>`** — no tiene endpoint de cookie nativo.
Para usar cookies httpOnly, Next.js necesita un middleware que convierta la cookie → header Bearer al hacer proxy.

---

## Opciones de arquitectura

### A — App Router puro (Next.js 15 RSC)

- **Server Components** para páginas de datos (inbox list, contactos, métricas).
- **Client Components** solo para interactividad (WebSocket, formularios, SSE).
- **Auth:** Next.js Middleware + cookies httpOnly. El middleware lee la cookie y añade el header `Authorization: Bearer` en los `fetch` del servidor (con `server-only`). El access token se guarda en cookie httpOnly; el refresh token también en cookie httpOnly separada.
- **Data fetching:** `fetch` nativo en RSC con `cache: "no-store"`, TanStack Query en Client Components.
- **Pro:** máximo rendimiento inicial, menos JS en cliente, SEO (irrelevante para SaaS pero gratis), acceso a cookies en middleware sin exponer token al JS del browser.
- **Con:** curva de aprendizaje RSC + Server Actions; WebSocket e SSE **requieren** Client Component + lógica de hidratación cuidadosa; depuración más compleja al mezclar contextos server/client.

### B — Client-side SPA clásica (Next.js 15 sin RSC)

- Todas las páginas son Client Components (`"use client"` global o páginas como SPA).
- **Auth:** `localStorage` o `sessionStorage` para el access token + `httpOnly cookie` solo para el refresh token (gestionado por una API Route `/api/auth/refresh`).
- **Data fetching:** TanStack Query para todo.
- **Pro:** familiar para cualquier dev React, integración directa de WebSocket e SSE con hooks, fácil debugging, sin complicaciones de hidratación.
- **Con:** access token expuesto en JS (riesgo XSS si hay alguna vulnerabilidad de inyección), más JS enviado al cliente, carga inicial ligeramente mayor.

### C — Híbrido: shell RSC + páginas Client (Recomendado)

- **Layout/shell** en RSC: navigation, sidebar, header, guards de auth, carga inicial de datos de sesión (`/v1/me`).
- **Páginas de app** como Client Components: toda la lógica de negocio, WebSocket, formularios, SSE.
- **Auth:** cookies httpOnly para ambos tokens (access + refresh). Una API Route de Next.js (`/api/auth/login`, `/api/auth/refresh`) actúa de proxy: recibe credenciales, llama al backend, setea las cookies. El middleware de Next.js lee la cookie de access token y la inyecta como `Authorization` header en los fetch del RSC shell. Los Client Components hacen fetch directamente al backend con el token leído de un context de React (poblado en el layout RSC → pasado como prop inicial o via `server/client boundary`).
- **Renovación automática del token:** interceptor en TanStack Query (o Axios si se usa) que detecta 401 → llama `/api/auth/refresh` → reintenta. El refresh token viaja solo en cookie httpOnly, nunca expuesto a JS.
- **Pro:** seguridad razonable (access token en cookie httpOnly, no en localStorage), familiaridad de Client Components para la lógica compleja, RSC solo donde aporta valor (shell), WebSocket e SSE naturales en Client.
- **Con:** más "moving parts" que B puro; el paso de datos RSC→Client requiere convención clara (props o Context).

---

## Recomendación

**Opción C — Híbrido RSC shell + Client Components.**

Razones:

1. **Seguridad:** el access token en `localStorage` (Opción B) queda expuesto a cualquier XSS. Con httpOnly cookies (Opción C) el token nunca toca el JS del browser directamente.
2. **Practicidad:** las páginas más complejas (inbox con WebSocket, métricas con SSE) son Client Components de todas formas. Forzar RSC puro (Opción A) en ellas añade complejidad sin ganancia real en un SaaS privado.
3. **DX familiar:** el 95% del código que un dev escribe sigue siendo React/hooks estándar.
4. **Rendimiento inicial:** el shell (nav + datos de sesión) se renderiza en el servidor → TTFB más rápido en la primera carga.

**Tradeoff aceptado:** la capa de proxy en Next.js API Routes (`/api/auth/*`) añade ~1 RTT al login y al refresh, pero solo ocurre en esos momentos, no en cada request.

---

## Routing propuesto (mínimo para v1)

```
/login                          → Formulario login. Público.
/dashboard                      → Métricas resumen + SSE stream en vivo.
/numbers                        → Lista de WaNumbers + estado WAHA (QR si disconnected).
/numbers/[id]                   → Config del número, métricas, logout WAHA.
/inbox                          → Lista de conversaciones (filtros, paginación, bulk actions).
/inbox/[conv_id]                → Conversación en detalle: mensajes, reply, take/close, tags, notas, WebSocket.
/contacts                       → Búsqueda de contactos + hechos (facts).
/connectors                     → Lista de conectores configurados + sync.
/connectors/[id]                → Config del conector, productos, órdenes, stats.
/knowledge                      → Documentos KB (upload PDF/URL, lista, delete).
/settings/persona               → CRUD de personas del agente IA.
/settings/team                  → CRUD de usuarios + deactivate + stats.
/settings/billing               → Cuotas, métricas de billing, export GDPR.
/settings/api-keys              → API keys públicas.
/settings/canned                → Respuestas rápidas (shortcode, text, variables).
```

**Rutas fuera del v1 mínimo** (se pueden agregar después sin romper nada):

```
/inbox/sla-report               → Panel SLA p50/p90.
/settings/webhooks              → Webhooks salientes públicos.
/settings/office-hours          → Horario de atención (Fase 29A).
```

---

## Primer endpoint a consumir

**`/inbox` → lista de conversaciones → `/inbox/[conv_id]`.**

Razones:

1. Es el **corazón del producto**: la pantalla que los agentes usan decenas de veces al día.
2. Integra el mayor número de endpoints en una sola pantalla (list, detail, reply, tags, notas, WebSocket).
3. Valida la arquitectura de autenticación completa (login → token → request autenticado) desde el día 1.
4. Toda la demás UI (dashboard, contacts, connectors) puede esperar sin bloquear el valor core.

**Secuencia de implementación recomendada:**
1. `/login` (valida el flujo auth completo + cookies)
2. `/dashboard` (valida TanStack Query + SSE básico)
3. `/inbox` (lista, filtros, paginación)
4. `/inbox/[conv_id]` (detalle + WebSocket + reply)
5. Resto en paralelo o bajo demanda.

---

## Dependencias técnicas a resolver antes de codificar

### Variables de entorno

```env
# .env.local (Next.js)
NEXT_PUBLIC_API_URL=http://localhost:8000    # URL base del backend (browser)
API_URL=http://backend:8000                  # URL base del backend (server-side, Docker)
NEXTAUTH_SECRET=<32-char random>             # Para firmar cookies de sesión
COOKIE_NAME=chatpro_session                  # Nombre de la cookie httpOnly
```

### Estrategia de autenticación

```
Browser → POST /api/auth/login → Next.js API Route
  → llama POST {API_URL}/v1/auth/login
  → recibe { access_token, refresh_token }
  → setea 2 cookies httpOnly:
      chatpro_access  (max-age: 3600,  secure, httpOnly, sameSite: lax)
      chatpro_refresh (max-age: 604800, secure, httpOnly, sameSite: lax)
  → responde 200 al browser (sin tokens en body)

Browser → cualquier request al backend:
  → Next.js Middleware intercepta → lee chatpro_access → añade Authorization: Bearer
  → si 401 → llama /api/auth/refresh → renueva cookies → reintenta

Browser → WebSocket / SSE:
  → No soportan cookies en handshake WebSocket (rfc6455)
  → Solución: endpoint /api/auth/ws-token → devuelve access_token en body (short-lived, 5min)
    solo para el handshake ws://…?token=<jwt>
  → Para SSE (fetch con EventSource): usar Fetch + Authorization header desde Client Component
    (el access_token se expone al Client Component una sola vez vía prop del layout RSC)
```

### Librería de componentes UI

**shadcn/ui + Tailwind CSS** (ya elegido en WHATSAPP_HUB_PLAN.md §3, no reabrir).

Componentes clave necesarios desde el día 1:
- `DataTable` (react-table via shadcn) → inbox list, contacts, users
- `Sheet` (drawer lateral) → detalle de conversación en mobile
- `Badge`, `Avatar`, `Dropdown` → estado WAHA, usuario asignado
- `Textarea` + `Button` → reply box
- `Dialog` → confirmaciones (close conv, deactivate user)
- `Tabs` → settings, detalle connector

### Manejo de WebSocket en Next.js

Hook personalizado `useConversationSocket(convId: string)`:

```typescript
// hooks/use-conversation-socket.ts
// Conecta a ws://API_WS/ws/inbox/{convId}?token=<wsToken>
// wsToken se obtiene de /api/auth/ws-token (Next.js API Route)
// Reconexión automática con backoff exponencial
// Devuelve: { messages: WsMessage[], status: "connecting"|"open"|"closed" }
```

Hook de notificaciones `useNotifications(tenantId: string)`:

```typescript
// hooks/use-notifications.ts
// Conecta a ws://API_WS/ws/notifications/{tenantId}?token=<wsToken>
// Devuelve: { notifications: Notification[], unreadCount: number }
```

**No se necesita librería externa** (socket.io, etc.) — el backend es WebSocket nativo de FastAPI.

### State management

- **TanStack Query v5** para todo el server state (fetch, cache, invalidación, optimistic updates).
- **Zustand** para client state puro: sidebar collapsed, selected conversations (bulk), notificaciones no leídas.
- **React Context** solo para: datos de sesión del usuario (`MeContext`) pasados desde el RSC layout.

### Manejo del token en Client Components

El RSC layout (server-side) lee la cookie `chatpro_access`, decodifica el payload JWT (solo claims públicos: user_id, tenant_id, role — **no lo reenvía al cliente**), y pasa esos datos como props al `MeContext`. Los Client Components nunca ven el token; hacen fetch a través de un thin proxy (`/api/proxy/[...path]`) que lee la cookie server-side y añade el `Authorization` header.

Alternativa más simple si el proxy añade latencia: el Client Component llama al backend directamente con el token leído de una cookie **no-httpOnly** de acceso read-only (solo el access token, sin el refresh). Decisión a tomar al arrancar.

---

## Resumen de decisiones pendientes (para OK del usuario)

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| 1 | Arquitectura | A (RSC puro) / B (SPA) / C (Híbrido) | **C** |
| 2 | Access token en cliente | httpOnly cookie + proxy / cookie legible / localStorage | httpOnly + proxy Next.js |
| 3 | Primer módulo a construir | Inbox / Dashboard / Numbers | **Inbox** (login → inbox list → detalle) |
| 4 | UI Kit | shadcn/ui + Tailwind (ya decidido) | ✅ Confirmado en plan |
| 5 | `NEXT_PUBLIC_API_URL` para dev | `http://localhost:8000` | ✅ Estándar |
| 6 | Nombre del package (`/frontend` o `/app`) | `frontend/` | `frontend/` (consistente con CLAUDE.md) |

---

*Generado en sesión Fase F1 — discovery. No se escribió código de producción.*
