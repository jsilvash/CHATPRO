# Cómo agregar un nuevo conector a ChatPro

Este documento es el checklist de referencia para implementar un conector de terceros
(e-commerce, CRM, calendario, etc.) siguiendo la arquitectura del Hub.

---

## Checklist de implementación

### 1. Crear el directorio del conector

```
src/connectors/<nombre>/
├── __init__.py
├── connector.py   — clase principal que implementa el ABC
└── tools.py       — funciones invocables por el agente Claude
```

### 2. Implementar la clase en `connector.py`

Tu clase debe heredar de `src.connectors.base.Connector` e implementar **todos** los métodos abstractos:

| Método | Descripción | Estado mínimo |
|---|---|---|
| `configure(credentials)` | Valida y persiste credenciales cifradas AES-GCM | Obligatorio |
| `test_connection()` | Llama un endpoint barato del proveedor | Obligatorio |
| `sync_full()` | Sincronización completa del catálogo | Obligatorio |
| `sync_incremental(since)` | Sync incremental desde timestamp | Puede ser placeholder |
| `verify_webhook(payload, headers)` | Verifica autenticidad del webhook | Puede ser placeholder |
| `webhook_handler(payload, headers)` | Procesa webhook verificado | Puede ser placeholder |
| `expose_tools()` | Lista de `ToolSchema` para el agente | Obligatorio |
| `search(query, top_k, filters)` | Búsqueda full-text sobre catálogo local | Obligatorio |

**Atributos de clase obligatorios:**
```python
name = "mi_conector"     # clave única del registro
kind = "ecommerce"       # "ecommerce" | "knowledge" | "crm" | "calendar"
version = "1.0.0"
```

#### Patrón `_get_db()`

Siempre usar el context manager `_get_db()` heredado para acceder a la BD.
Esto permite que los tests inyecten una sesión con rollback:

```python
with self._get_db() as db:
    config = db.get(ConnectorConfig, self.config_id)
    # ...
    if self._db_session is None:
        db.commit()
    else:
        db.flush()   # el test controla el commit
```

#### Cifrado de credenciales

Usar siempre `encrypt_credentials` / `decrypt_credentials` de `src.connectors.crypto`:

```python
from src.connectors.crypto import encrypt_credentials, decrypt_credentials

blob = encrypt_credentials({"api_key": "...", "shop_url": "..."})
config.encrypted_credentials = blob
```

El blob usa AES-GCM con DEK aleatorio por config y KEK desde `CONNECTOR_MASTER_KEY` env var.

#### Ejemplo mínimo de `configure()`

```python
def configure(self, credentials: dict) -> None:
    required = {"api_key", "base_url"}
    missing = required - credentials.keys()
    if missing:
        raise ValueError(f"Faltan campos requeridos: {missing}")

    blob = encrypt_credentials(credentials)
    with self._get_db() as db:
        config = db.get(ConnectorConfig, self.config_id)
        config.encrypted_credentials = blob
        if not config.webhook_secret:
            config.webhook_secret = secrets.token_hex(32)
        config.status = "pending"
        if self._db_session is None:
            db.commit()
        else:
            db.flush()
```

### 3. Implementar tools en `tools.py`

Cada función tool debe:
- Recibir `tenant_id`, `config_id` y `db` como keyword arguments.
- Usar el helper `_db_ctx(db)` para abrir o reutilizar la sesión.
- Retornar un `dict` serializable que el agente Claude recibirá como `tool_result`.
- Filtrar siempre por `tenant_id` Y `config_id` para garantizar aislamiento.

```python
def mi_tool(
    query: str,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    db=None,
    **_kwargs,
) -> dict:
    with _db_ctx(db) as session:
        products = session.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.connector_config_id == config_id,
            Product.deleted_at.is_(None),
            Product.name.ilike(f"%{query}%"),
        ).all()
        # ...
```

### 4. Registrar en `src/connectors/registry.py`

Agregar una función de carga diferida y llamarla en `_build_registry()`:

```python
def _load_mi_conector():
    from src.connectors.mi_conector.connector import MiConectorConnector
    return MiConectorConnector

def _build_registry():
    reg = {}
    # ... conectores existentes ...
    try:
        reg["mi_conector"] = _load_mi_conector()
    except Exception:
        pass
    return reg
```

### 5. Crear migración Alembic (si agrega tablas nuevas)

Si el conector necesita tablas propias (ej. para órdenes, clientes, etc.):

```bash
alembic revision --autogenerate -m "mi_conector: tabla órdenes"
# revisar la migración generada
alembic upgrade head
```

Si solo usa la tabla `products` existente, no hace falta nueva migración.

### 6. Escribir tests en `tests/test_<nombre>.py`

Tests obligatorios:

| Test | Descripción |
|---|---|
| `test_configure_persiste_credenciales` | Verifica blob cifrado en BD + round-trip decrypt |
| `test_configure_falla_sin_campos` | ValueError si faltan campos requeridos |
| `test_test_connection_ok` | Retorna `True` y status `connected` con mock 200 |
| `test_test_connection_error` | Retorna `False` y status `error` con mock 4xx/5xx |
| `test_sync_full_crea_productos` | Parsea respuesta mock y crea en tabla `products` |
| `test_sync_full_actualiza_existente` | Segunda sync actualiza sin duplicar |
| `test_tool_buscar_productos` | Tool encuentra producto seeded |
| `test_tool_consultar_stock` | Tool retorna stock por SKU o ID |
| `test_expose_tools_retorna_esquemas` | Verifica nombres y `callable_ref` |
| `test_aislamiento_sync_full` | Tenant B no ve productos de sync de tenant A |
| `test_aislamiento_tool` | Tool de tenant B no lee datos de tenant A |
| `test_aislamiento_configure_tenant_incorrecto` | RuntimeError si config pertenece a otro tenant |

Usar siempre `db` fixture inyectado (sesión con rollback) para que los tests sean atómicos.

### 7. Verificar que los tests existentes siguen pasando

```bash
uv run pytest tests/test_connectors.py tests/test_<nombre>.py -v
```

---

## Referencia de implementaciones existentes

| Conector | Archivo | API usada |
|---|---|---|
| WooCommerce | `src/connectors/woocommerce/connector.py` | REST v3, auth Basic (consumer_key/secret) |
| Shopify | `src/connectors/shopify/connector.py` | Admin REST 2024-01, auth X-Shopify-Access-Token |

---

## Preguntas frecuentes

**¿Puedo agregar campos extra al modelo `Product`?**
No sin una migración Alembic. El modelo `Product` es compartido por todos los conectores e-commerce.
Usa el campo `raw` (JSONB) para datos extras del proveedor que no tienen columna propia.

**¿Cómo manejo paginación cursor vs offset?**
- WooCommerce usa offset (`page=N`).
- Shopify usa cursor (`page_info` del Link header).
- Otros APIs pueden usar `after_id`, `since_id`, etc.
Adaptar `_sync_products_pages()` según el proveedor.

**¿Cómo verifico webhooks del proveedor?**
Implementar `verify_webhook()` con el mecanismo del proveedor:
- WooCommerce: HMAC-SHA256 base64 en `X-WC-Webhook-Signature`.
- Shopify: HMAC-SHA256 base64 en `X-Shopify-Hmac-Sha256`.
- Usar `hmac.compare_digest()` para comparación segura.

**¿Qué moneda reportar?**
El campo `currency` en `Product` puede quedar `None` si la moneda se obtiene a nivel de tienda
y no por producto. El agente puede consultarla vía `test_connection()` o un endpoint de tienda.
