# SYNEX - Instrucciones del Proyecto

> **IMPORTANTE para sesiones nuevas:** Lee primero la seccion "Contexto rapido del sistema" para evitar redescubrir cosas ya implementadas.

---

## Contexto rapido del sistema

SYNEX (rebranding en curso de "Fitness IA") es una plataforma SaaS multi-tenant de Business Intelligence + automatizacion para gimnasios. Grupo N y E es el cliente inicial (2-3 sedes). Deploy: Railway.

### Stack
- **Backend:** Python + FastAPI
- **BD:** PostgreSQL (prod) / SQLite (dev)
- **Frontend:** HTML + JS Vanilla + Tailwind (sin React/Vue). SPA con hash routing.
- **IA:** Anthropic Claude (Sonnet/Haiku)
- **Mensajeria:** WhatsApp Cloud API + SendGrid
- **Jobs:** Celery + Redis
- **Integracion gimnasio:** FitnessBrain API (tambien soporta EVO/Glofox a futuro)

### Que YA esta implementado (inventario para no redescubrir)

| Capa | Implementacion | Archivos clave |
|------|---------------|----------------|
| Sync FitnessBrain | Clientes, contratos activos/historicos, asistencias, reservas, ventas | `src/integrations/fitnessbrain.py` |
| RiskScore | Score 0-100 + nivel (bajo/medio/alto/critico) + days_inactive + days_to_expiry | `src/engine/risk.py`, tabla `risk_scores` |
| Motor de reglas | Evaluacion diaria de inactividad (7d/14d/21d/30d) y contratos por vencer | `src/engine/rules.py`, `src/engine/inactivity.py` |
| Playbooks | Configuracion declarativa por `(case_type, module, stage)` | `src/engine/pipeline_engine.py` (PLAYBOOKS) |
| Creacion auto de casos | Inactividad dispara `module="riesgo"` stage `7d/14d/21d/30d`; vencimiento dispara renovacion | `src/engine/inactivity.py:196-204` |
| CRM omnicanal | Cases, interactions, identities, tasks, documents, macros | migracion `m1n2o3p4q5r6` |
| Backoffice SPA | Mi Dia, Contacto 360, Caso detalle, Pipeline, Inbox | `backoffice/js/` (myday.js, contact360.js, crm.js) |
| Dashboard BI | 60+ KPIs + chat IA con Claude | `dashboard/`, `src/api/routes/dashboard.py` |
| Mensajeria | Envio WA/email automatico desde reglas | `src/messaging/` |
| Multi-tenant | Aislamiento por `tenant_id` en todas las tablas | `src/db/models.py` |

### Modelo de datos (lo critico)

- `clients.external_id` = **RUT chileno** (no es ID generico, es el RUT). Viene como `userid` de FitnessBrain.
- `clients.lifecycle_stage`: `nuevo | activo | fiel | vip | reactivado | en_pausa | ex_cliente` (clasificacion automatica por antigueedad/LTV/renovaciones).
- `clients`: `name, lastname, lastname2, email, cellphone, club_id, birthdate, is_vip, is_beca, total_ltv, years_as_member, consecutive_renewals`.
- `contracts`: `plan_name, plan_value, plan_type (PRE|RP), plan_status (Nuevo|Renovacion), start_date, end_date, is_active, beca`.
- `attendances`: check-ins (`client_id`, `date`, `club_id`). Sincronizados diariamente.
- `risk_scores`: `score, risk_level, days_inactive, last_attendance, frequency_trend, days_to_expiry`. Ultimo por cliente es el vigente.
- `cases`: `case_type (lifecycle|support)`, `module` (comercial/onboarding/retencion/renovacion/riesgo/rescate/reactivacion para lifecycle; membresia/finiquito/congelamiento/reclamo/consulta/cobranza para support), `stage`, `status`, `priority (1=critica..5=baja)`, `sla_due_at`, `metadata_json`, `parent_case_id`.
- `leads.rut` (campo explicito, **solo para leads** — clientes usan `external_id`).

### Flujos automaticos criticos (no romper)

1. **Daily pipeline** (Celery): sync FitnessBrain --> recalcula RiskScore --> `_evaluate_inactivity_rules` --> crea/actualiza casos --> envia mensajes WA/email segun playbook.
2. **Stages de inactividad:** `7d` (WA "te extranamos"), `14d` (WA+email + clase gratuita), `21d` (tarea interna de llamada), `30d` (oferta de reenganche).
3. **Renovacion:** disparadores a 30d/21d/14d/7d antes del `end_date` del contrato.

### Convenciones ya establecidas

- **Playbook declarativo:** para agregar/modificar comportamiento de un stage, editar `PLAYBOOKS` en `src/engine/pipeline_engine.py`. NO hardcodear logica en endpoints ni frontend.
- **NBA (Next Best Action):** derivada 100% del playbook del stage via `next_best_action(case)`. Si ves "Revisar caso" canal "nota" en produccion = esta cayendo al `_FALLBACK_STAGE` (falta config para ese stage).
- **Nombre del sistema:** SYNEX (nuevo), Fitness IA (legacy en docs y UI). No renombrar UI sin aprobacion.
- **Idioma:** todo en espanol (codigo, UI, comentarios, commits).

### Referencias a docs extensas (no duplicar aqui)

- `FitnessIA_Manual_Sistema.md` (1237 lineas) — detalle por modulo, tablas, KPIs, calculos.
- `FitnessIA_Manual_Operativo.md` (785 lineas) — flujos de usuario paso a paso.
- `AUDITORIA_PRODUCCION_2026-04-16.md` + `BACKLOG_AUDITORIA.md` — estado actual y deuda tecnica.
- `PLAN_REMEDIACION_2026-04-09.md`, `FASE_1_SINCERIDAD.md`, `FASE_2_1A_LOG_FIEL.md` — fases de limpieza en curso.

---

## Reglas generales

- Este proyecto es un sistema de retencion y ventas para gimnasios (Grupo N y E)
- Stack: Python (FastAPI), SQLite/PostgreSQL, HTML/JS (backoffice y dashboard)
- Desplegado en Railway (produccion). NO hacer push a main sin autorizacion explicita del usuario
- Idioma: siempre responder en espanol

## Ciclo de trabajo por fases (REGLA CRITICA)

El proyecto se avanza en fases chicas. Cada fase tiene su MD propio en la raiz (`FASE_X_Y_*.md`). El ciclo es estricto:

**Una fase = una sesion = un PR chico = un merge = un QA en prod = siguiente fase.**

No se agrupan fases. No se mezclan cambios de fases distintas en la misma rama. Si durante una fase aparecen issues de otra fase, se anotan en `ESTADO_FASES_*.md` o se abre un MD nuevo — no se meten en la rama actual.

### Discovery antes de codigo

Al empezar cualquier fase nueva:

1. Leer SOLO el MD de esa fase + `ESTADO_FASES_*.md` vigente. Nada mas.
2. Si el MD no existe, escribirlo primero con opciones A/B/C + recomendacion y esperar OK del usuario antes de tocar codigo.
3. Si el MD existe pero el estado del repo no matchea, hacer discovery corto y actualizar el MD antes de ejecutar.

### Handoff entre sesiones

**Al cerrar cada sesion** (despues de mergear el PR y actualizar `ESTADO_FASES_*.md`), generar el **prompt de arranque de la proxima sesion**. Formato obligatorio:

```
Retomo Fase X.Y — <titulo>. Contexto:
- Rama activa: <nombre> (estado: <limpia / con commit WIP / etc>)
- Main acaba de mergear PR #<n>.
- Primero: <comandos de setup — checkout, rebase, etc>.
- Despues: leer SOLO <archivos.md>. Ejecutar los N pendientes del MD:
  1. ...
  2. ...
- Finalizar: <comandos de commit/push/PR/deploy/QA>.
- NO tocar: <lista de cosas fuera de alcance — stashes, otras fases, etc>.
- Al cerrar esta sesion, generar el prompt de arranque de la proxima (esta misma regla, recursiva).
```

El usuario copia ese bloque a una sesion nueva y arranca sin re-explicar contexto. Esta regla es recursiva: cada sesion cierra pasandole la posta a la siguiente.

## Reglas para trabajo multi-agente

IMPORTANTE: Pueden haber varios agentes de Claude Code trabajando en este proyecto al mismo tiempo.
Para evitar conflictos, cada agente DEBE seguir estas reglas:

### 1. Antes de editar, verificar
- Antes de modificar un archivo, hacer `git diff` para verificar que no hay cambios sin commitear en ese archivo
- Si otro agente ya modifico el archivo, NO sobrescribir. Informar al usuario

### 2. Zonas del proyecto
El proyecto esta dividido en zonas. Cada agente debe trabajar en SU zona asignada:

| Zona | Carpetas | Descripcion |
|------|----------|-------------|
| **Backend API** | `src/api/`, `src/db/`, `src/security/` | Endpoints, base de datos, autenticacion |
| **Motor IA** | `src/engine/`, `src/fitnessbrain/`, `src/messaging/` | Logica de retencion, IA, mensajeria |
| **Frontend** | `backoffice/`, `dashboard/` | Interfaz web, CSS, JavaScript |
| **Infra** | `scripts/`, `tests/`, `alembic/`, `docker-compose.yml`, `Dockerfile` | DevOps, migraciones, testing |
| **Tareas** | `src/scheduler/`, `src/tasks/`, `src/integrations/` | Jobs programados, integraciones externas |

### 3. Archivos compartidos (CUIDADO)
Estos archivos los pueden necesitar varias zonas. Solo editarlos con extrema precaucion:
- `src/utils/` - utilidades compartidas
- `requirements.txt` - dependencias Python
- `.env` / `.env.example` - variables de entorno
- `run.py` - punto de entrada de la app
- `src/storage/` - almacenamiento compartido

Si necesitas modificar un archivo compartido, informar al usuario primero.

### 4. Commits
- Hacer commits frecuentes con mensajes descriptivos
- Formato: `[zona] descripcion` (ejemplo: `[frontend] agregar filtro por sede`)
- NO hacer push a main/origin sin autorizacion

### 5. No romper produccion
- No modificar `docker-compose.yml`, `Dockerfile`, `railway.toml`, `deploy.sh` sin autorizacion
- No borrar archivos de migraciones existentes
- No modificar la base de datos de produccion directamente
