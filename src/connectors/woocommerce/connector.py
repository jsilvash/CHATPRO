"""WooCommerceConnector — implementación completa Fases 5 y 6.

Implementa:
- configure()        — persiste y cifra credenciales; valida campos requeridos.
- test_connection()  — llama /wc/v3/system_status con las credenciales actuales.
- sync_full()        — pagina /wc/v3/products y persiste en tabla ``products``.
- sync_incremental() — pagina /wc/v3/products?modified_after=since (Fase 6).
- verify_webhook()   — verifica firma HMAC-SHA256 (X-WC-Webhook-Signature).
- webhook_handler()  — despacha por topic: product/order events (Fase 6).
- expose_tools()     — 3 tools semánticos para el agente.
- search()           — búsqueda híbrida pgvector cosine + BM25 ts_rank (Fase 6).
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from base64 import b64decode
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from src.connectors.base import (
    Connector,
    SearchResult,
    SyncResult,
    ToolSchema,
    WebhookVerification,
)
from src.connectors.crypto import decrypt_credentials, encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product
from src.db.session import get_db_session

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_REQUEST_TIMEOUT = 30.0


class WooCommerceConnector(Connector):
    """Conector para WooCommerce REST API v3."""

    name = "woocommerce"
    kind = "ecommerce"
    version = "1.0.0"

    # ── Inicialización ────────────────────────────────────────────────────────

    def __init__(self, tenant_id: uuid.UUID, config_id: uuid.UUID, db=None) -> None:
        super().__init__(tenant_id, config_id, db)
        self._credentials: dict | None = None

    @contextmanager
    def _get_db(self):
        """Context manager que reutiliza ``_db_session`` si existe, o abre una nueva."""
        if self._db_session is not None:
            yield self._db_session
        else:
            with get_db_session() as db:
                yield db

    def _load_credentials(self) -> dict:
        """Carga y descifra las credenciales desde BD."""
        if self._credentials is not None:
            return self._credentials

        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config is None:
                raise RuntimeError(f"ConnectorConfig {self.config_id} no encontrada")
            if config.tenant_id != self.tenant_id:
                raise RuntimeError("tenant_id no coincide con la config")
            if not config.encrypted_credentials:
                raise RuntimeError("Conector no configurado — llama configure() primero")
            self._credentials = decrypt_credentials(bytes(config.encrypted_credentials))
        return self._credentials

    def _build_client(self) -> httpx.Client:
        creds = self._load_credentials()
        return httpx.Client(
            base_url=creds["site_url"].rstrip("/"),
            auth=(creds["consumer_key"], creds["consumer_secret"]),
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "ChatPro-WooConnector/1.0"},
        )

    # ── configure ─────────────────────────────────────────────────────────────

    def configure(self, credentials: dict) -> None:
        """Persiste credenciales cifradas y genera webhook_secret."""
        required = {"site_url", "consumer_key", "consumer_secret"}
        missing = required - credentials.keys()
        if missing:
            raise ValueError(f"Faltan campos requeridos: {missing}")

        site_url = credentials["site_url"].rstrip("/")
        if not site_url.startswith(("http://", "https://")):
            raise ValueError("site_url debe comenzar con http:// o https://")

        blob = encrypt_credentials({
            "site_url": site_url,
            "consumer_key": credentials["consumer_key"],
            "consumer_secret": credentials["consumer_secret"],
        })
        self._credentials = None  # invalidar cache

        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config is None:
                raise RuntimeError(f"ConnectorConfig {self.config_id} no encontrada")
            config.encrypted_credentials = blob
            if not config.webhook_secret:
                config.webhook_secret = secrets.token_hex(32)
            config.status = "pending"
            config.last_error = None
            if self._db_session is None:
                db.commit()
            else:
                db.flush()

    # ── test_connection ───────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Llama /wc/v3/system_status y actualiza status en BD."""
        try:
            with self._build_client() as client:
                resp = client.get("/wp-json/wc/v3/system_status")

            ok = resp.status_code == 200
            status = "connected" if ok else "error"
            error_msg = None if ok else f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            ok = False
            status = "error"
            error_msg = str(exc)[:500]
            logger.warning("WooCommerce test_connection falló: %s", exc)

        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config:
                config.status = status
                config.last_error = error_msg
                if self._db_session is None:
                    db.commit()
                else:
                    db.flush()

        return ok

    # ── sync_full ─────────────────────────────────────────────────────────────

    def sync_full(self) -> SyncResult:
        """Pagina /wc/v3/products y persiste en tabla products."""
        started_at = datetime.now(timezone.utc)
        stats = {"processed": 0, "created": 0, "updated": 0, "deleted": 0}
        errors: list[str] = []

        try:
            self._sync_products_pages(stats, errors)
        except Exception as exc:
            errors.append(f"Error fatal en sync_full: {exc}")
            logger.exception("sync_full WooCommerce falló")
            self._update_config_sync_error(str(exc)[:500])
        else:
            self._update_config_sync_ok()

        finished_at = datetime.now(timezone.utc)
        return SyncResult(
            items_processed=stats["processed"],
            items_created=stats["created"],
            items_updated=stats["updated"],
            items_deleted=stats["deleted"],
            errors=errors,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _sync_products_pages(self, stats: dict, errors: list[str]) -> None:
        page = 1
        with self._build_client() as client:
            while True:
                try:
                    resp = client.get(
                        "/wp-json/wc/v3/products",
                        params={
                            "per_page": _PAGE_SIZE,
                            "page": page,
                            "status": "publish",
                            "orderby": "id",
                            "order": "asc",
                        },
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    errors.append(f"Página {page}: {exc}")
                    break

                items = resp.json()
                if not items:
                    break

                for raw_product in items:
                    try:
                        self._upsert_product(raw_product, stats)
                    except Exception as exc:
                        errors.append(
                            f"Producto {raw_product.get('id')}: {exc}"
                        )

                stats["processed"] += len(items)

                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page >= total_pages:
                    break
                page += 1

    def _upsert_product(self, raw: dict, stats: dict) -> None:
        external_id = str(raw["id"])
        with self._get_db() as db:
            existing = (
                db.query(Product)
                .filter(
                    Product.tenant_id == self.tenant_id,
                    Product.connector_config_id == self.config_id,
                    Product.external_id == external_id,
                )
                .first()
            )

            product_data = _map_woo_product(raw, self.tenant_id, self.config_id)

            if existing is None:
                product = Product(**product_data)
                db.add(product)
                stats["created"] += 1
            else:
                for k, v in product_data.items():
                    if k not in ("id", "created_at"):
                        setattr(existing, k, v)
                stats["updated"] += 1

            if self._db_session is None:
                db.commit()
            else:
                db.flush()

    def _update_config_sync_ok(self) -> None:
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config:
                config.last_full_sync_at = datetime.now(timezone.utc)
                config.last_error = None
                config.status = "connected"
                if self._db_session is None:
                    db.commit()

    def _update_config_sync_error(self, msg: str) -> None:
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config:
                config.last_error = msg
                config.status = "error"
                if self._db_session is None:
                    db.commit()

    # ── sync_incremental (Fase 6) ─────────────────────────────────────────────

    def sync_incremental(self, since: datetime) -> SyncResult:
        """Sincroniza productos modificados después de ``since``.

        Usa el parámetro ``modified_after`` de la WC REST API v3.
        Actualiza ``last_incremental_sync_at`` en ConnectorConfig.
        """
        started_at = datetime.now(timezone.utc)
        stats = {"processed": 0, "created": 0, "updated": 0, "deleted": 0}
        errors: list[str] = []

        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        try:
            page = 1
            with self._build_client() as client:
                while True:
                    try:
                        resp = client.get(
                            "/wp-json/wc/v3/products",
                            params={
                                "modified_after": since_iso,
                                "per_page": _PAGE_SIZE,
                                "page": page,
                                "orderby": "modified",
                                "order": "asc",
                            },
                        )
                        resp.raise_for_status()
                    except httpx.HTTPError as exc:
                        errors.append(f"Página {page}: {exc}")
                        break

                    items = resp.json()
                    if not items:
                        break

                    for raw_product in items:
                        try:
                            self._upsert_product(raw_product, stats)
                        except Exception as exc:
                            errors.append(f"Producto {raw_product.get('id')}: {exc}")

                    stats["processed"] += len(items)
                    total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                    if page >= total_pages:
                        break
                    page += 1

        except Exception as exc:
            errors.append(f"Error fatal en sync_incremental: {exc}")
            logger.exception("sync_incremental WooCommerce falló")
        else:
            with self._get_db() as db:
                config = db.get(ConnectorConfig, self.config_id)
                if config:
                    config.last_incremental_sync_at = datetime.now(timezone.utc)
                    config.last_error = None
                    if self._db_session is None:
                        db.commit()

        finished_at = datetime.now(timezone.utc)
        return SyncResult(
            items_processed=stats["processed"],
            items_created=stats["created"],
            items_updated=stats["updated"],
            items_deleted=stats["deleted"],
            errors=errors,
            started_at=started_at,
            finished_at=finished_at,
        )

    # ── verify_webhook (Fase 6) ───────────────────────────────────────────────

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification:
        """Verifica X-WC-Webhook-Signature (HMAC-SHA256 base64)."""
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config is None:
                return WebhookVerification(valid=False, reason="config no encontrada")
            secret = config.webhook_secret

        sig_header = headers.get("x-wc-webhook-signature", "")
        if not sig_header:
            return WebhookVerification(valid=False, reason="header firma ausente")

        expected = hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).digest()
        try:
            received = b64decode(sig_header)
        except Exception:
            return WebhookVerification(valid=False, reason="firma mal codificada")

        if not hmac.compare_digest(expected, received):
            return WebhookVerification(valid=False, reason="firma inválida")

        return WebhookVerification(valid=True)

    # ── webhook_handler (Fase 6) ──────────────────────────────────────────────

    def webhook_handler(self, payload: dict, headers: dict[str, str]) -> None:
        """Despacha por topic; encola re-embedding si cambia nombre/descripción."""
        topic = headers.get("x-wc-webhook-topic", "").lower()

        if topic in ("product.created", "product.updated"):
            stats: dict = {"created": 0, "updated": 0}
            self._upsert_product(payload, stats)
            product_id = self._get_product_id_by_external(str(payload.get("id", "")))
            if product_id:
                try:
                    from src.connectors.tasks import embed_product_sync
                    embed_product_sync(product_id, self._db_session)
                except Exception:
                    logger.warning(
                        "embed_product_sync falló para producto %s", product_id, exc_info=True
                    )

        elif topic == "product.deleted":
            self._soft_delete_product(str(payload.get("id", "")))

        elif topic in ("order.created", "order.updated"):
            self._upsert_order(payload)

        else:
            logger.debug("webhook_handler: topic '%s' ignorado", topic)

    def _get_product_id_by_external(self, external_id: str) -> uuid.UUID | None:
        """Retorna el UUID interno del producto dado su external_id."""
        with self._get_db() as db:
            product = (
                db.query(Product)
                .filter(
                    Product.tenant_id == self.tenant_id,
                    Product.connector_config_id == self.config_id,
                    Product.external_id == external_id,
                )
                .first()
            )
            return product.id if product else None

    def _soft_delete_product(self, external_id: str) -> None:
        """Marca el producto como eliminado (soft-delete)."""
        with self._get_db() as db:
            product = (
                db.query(Product)
                .filter(
                    Product.tenant_id == self.tenant_id,
                    Product.connector_config_id == self.config_id,
                    Product.external_id == external_id,
                )
                .first()
            )
            if product:
                product.deleted_at = datetime.now(timezone.utc)
                if self._db_session is None:
                    db.commit()
                else:
                    db.flush()

    def _upsert_order(self, raw: dict) -> None:
        """Crea o actualiza un pedido en la tabla ``orders``."""
        external_id = str(raw.get("id", ""))
        if not external_id or external_id == "0":
            logger.warning("_upsert_order: payload sin id válido")
            return

        placed_at: datetime | None = None
        date_created = raw.get("date_created") or raw.get("date_created_gmt")
        if date_created:
            try:
                placed_at = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        with self._get_db() as db:
            existing = (
                db.query(Order)
                .filter(
                    Order.tenant_id == self.tenant_id,
                    Order.connector_config_id == self.config_id,
                    Order.external_id == external_id,
                )
                .first()
            )
            if existing is None:
                order = Order(
                    id=uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    connector_config_id=self.config_id,
                    external_id=external_id,
                    status=raw.get("status"),
                    total=_to_decimal(raw.get("total")),
                    currency=raw.get("currency"),
                    placed_at=placed_at,
                    raw=raw,
                )
                db.add(order)
            else:
                existing.status = raw.get("status", existing.status)
                existing.total = _to_decimal(raw.get("total")) or existing.total
                existing.currency = raw.get("currency", existing.currency)
                existing.placed_at = placed_at or existing.placed_at
                existing.raw = raw
                existing.updated_at = datetime.now(timezone.utc)

            if self._db_session is None:
                db.commit()
            else:
                db.flush()

    # ── expose_tools ──────────────────────────────────────────────────────────

    def expose_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="buscar_productos",
                description=(
                    "Busca productos del catálogo del negocio por nombre, descripción o "
                    "categoría. Devuelve nombre, precio, stock y link. Úsalo cuando el "
                    "cliente pregunte por un producto o servicio."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Texto de búsqueda libre"},
                        "max_results": {
                            "type": "integer",
                            "default": 5,
                            "description": "Máximo de resultados (1-20)",
                        },
                    },
                    "required": ["query"],
                },
                callable_ref="connectors.woocommerce.tools:buscar_productos",
            ),
            ToolSchema(
                name="consultar_stock_y_precio",
                description=(
                    "Consulta stock disponible, precio y link de un producto específico "
                    "por SKU o ID de producto. Úsalo cuando el cliente pregunte por "
                    "disponibilidad o precio exacto de un ítem."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string", "description": "SKU del producto"},
                        "product_id": {
                            "type": "string",
                            "description": "ID externo del producto",
                        },
                    },
                },
                callable_ref="connectors.woocommerce.tools:consultar_stock_y_precio",
            ),
            ToolSchema(
                name="historial_pedidos_contacto",
                description=(
                    "Lista los últimos pedidos del contacto actual resueltos por email "
                    "o teléfono. Úsalo cuando el cliente pregunte por el estado de sus "
                    "órdenes o historial de compras."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Máximo de órdenes a retornar",
                        },
                    },
                },
                callable_ref="connectors.woocommerce.tools:historial_pedidos_contacto",
            ),
        ]

    # ── search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Búsqueda híbrida: pgvector cosine + BM25 ts_rank con fusión RRF.

        Si la búsqueda semántica falla (sin API key, sin embeddings) degrada
        a BM25 puro. Si BM25 no encuentra nada (search_tsv no poblada o query
        sin tokens) usa ILIKE como último recurso.
        """
        candidates = top_k * 2 if top_k * 2 <= 20 else 20
        with self._get_db() as db:
            sem_rows: list[dict] = []
            try:
                from src.knowledge.embeddings import get_query_embedding
                query_vec = get_query_embedding(query)
                sem_rows = _semantic_search_products(
                    query_vec, self.tenant_id, self.config_id, candidates, db
                )
            except Exception:
                logger.debug("Búsqueda semántica de productos no disponible", exc_info=True)

            bm25_rows = _bm25_search_products(
                query, self.tenant_id, self.config_id, candidates, db
            )

            if not sem_rows and not bm25_rows:
                bm25_rows = _ilike_search_products(
                    query, self.tenant_id, self.config_id, top_k, db
                )

            merged = _rrf_merge_products(sem_rows, bm25_rows, top_k)
            return merged


# ── Helpers de mapeo ──────────────────────────────────────────────────────────

def _map_woo_product(raw: dict, tenant_id: uuid.UUID, config_id: uuid.UUID) -> dict:
    """Mapea un producto del API Woo al schema de la tabla ``products``."""
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "connector_config_id": config_id,
        "external_id": str(raw.get("id", "")),
        "sku": raw.get("sku") or None,
        "name": raw.get("name", ""),
        "description_short": _strip_html(raw.get("short_description", "")),
        "description_long": _strip_html(raw.get("description", "")),
        "price_regular": _to_decimal(raw.get("regular_price")),
        "price_sale": _to_decimal(raw.get("sale_price")),
        "currency": None,
        "stock_quantity": raw.get("stock_quantity"),
        "stock_status": raw.get("stock_status"),
        "url": raw.get("permalink"),
        "images": [img.get("src") for img in raw.get("images", [])] or None,
        "categories": [c.get("name") for c in raw.get("categories", [])] or None,
        "attributes": {
            a["name"]: [o["name"] for o in a.get("options", [])]
            for a in raw.get("attributes", [])
        } or None,
        "variations": raw.get("variations") or None,
        "raw": raw,
        "deleted_at": None,
        "updated_at": datetime.now(timezone.utc),
    }


def _to_decimal(value) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _product_text(p: dict) -> str:
    """Construye el texto embedible del producto (compatibilidad Fase 6)."""
    parts = [
        p.get("name", ""),
        p.get("short_description", ""),
        p.get("description", ""),
        p.get("sku", ""),
    ]
    for cat in p.get("categories") or []:
        parts.append(cat.get("name", ""))
    for attr in p.get("attributes") or []:
        name = attr.get("name", "")
        options = ", ".join(attr.get("options") or [])
        if name:
            parts.append(f"{name}: {options}")
    return " ".join(s for s in parts if s).strip()


_RRF_K = 60


def _semantic_search_products(
    query_vec: list[float],
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    limit: int,
    db,
) -> list[dict]:
    """Búsqueda por similitud coseno sobre products.embedding."""
    sql = text("""
        SELECT
            id::text,
            name,
            description_short,
            sku,
            url,
            external_id,
            price_regular,
            price_sale,
            currency,
            stock_quantity,
            stock_status,
            1 - (embedding <=> CAST(:query_vec AS vector)) AS score
        FROM products
        WHERE tenant_id = :tenant_id
          AND connector_config_id = :config_id
          AND deleted_at IS NULL
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
    """)
    rows = db.execute(sql, {
        "query_vec": str(query_vec),
        "tenant_id": str(tenant_id),
        "config_id": str(config_id),
        "limit": limit,
    }).fetchall()
    return [_row_to_dict(r) for r in rows]


def _bm25_search_products(
    query: str,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    limit: int,
    db,
) -> list[dict]:
    """Búsqueda BM25 usando la columna search_tsv generada."""
    try:
        sql = text("""
            SELECT
                id::text,
                name,
                description_short,
                sku,
                url,
                external_id,
                price_regular,
                price_sale,
                currency,
                stock_quantity,
                stock_status,
                ts_rank(search_tsv, plainto_tsquery('spanish', :query)) AS score
            FROM products
            WHERE tenant_id = :tenant_id
              AND connector_config_id = :config_id
              AND deleted_at IS NULL
              AND search_tsv @@ plainto_tsquery('spanish', :query)
            ORDER BY score DESC
            LIMIT :limit
        """)
        rows = db.execute(sql, {
            "query": query,
            "tenant_id": str(tenant_id),
            "config_id": str(config_id),
            "limit": limit,
        }).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        logger.debug("BM25 products falló (search_tsv no disponible?)", exc_info=True)
        return []


def _ilike_search_products(
    query: str,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    limit: int,
    db,
) -> list[dict]:
    """Fallback ILIKE cuando BM25 y semántica no están disponibles."""
    rows = (
        db.query(Product)
        .filter(
            Product.tenant_id == tenant_id,
            Product.connector_config_id == config_id,
            Product.deleted_at.is_(None),
            Product.name.ilike(f"%{query}%")
            | Product.description_short.ilike(f"%{query}%")
            | Product.sku.ilike(f"%{query}%"),
        )
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "description_short": r.description_short or "",
            "sku": r.sku,
            "url": r.url,
            "external_id": r.external_id,
            "price_regular": float(r.price_regular) if r.price_regular else None,
            "price_sale": float(r.price_sale) if r.price_sale else None,
            "currency": r.currency,
            "stock_quantity": r.stock_quantity,
            "stock_status": r.stock_status,
            "score": 0.5,
        }
        for r in rows
    ]


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description_short": row.description_short or "",
        "sku": row.sku,
        "url": row.url,
        "external_id": row.external_id,
        "price_regular": float(row.price_regular) if row.price_regular else None,
        "price_sale": float(row.price_sale) if row.price_sale else None,
        "currency": row.currency,
        "stock_quantity": row.stock_quantity,
        "stock_status": row.stock_status,
        "score": float(row.score),
    }


def _rrf_merge_products(
    sem: list[dict],
    bm25: list[dict],
    top_k: int,
) -> list[SearchResult]:
    scores: dict[str, float] = {}
    index: dict[str, dict] = {}

    for rank, item in enumerate(sem):
        pid = item["id"]
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        index[pid] = item

    for rank, item in enumerate(bm25):
        pid = item["id"]
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        index[pid] = item

    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)[:top_k]
    results = []
    for pid in sorted_ids:
        p = index[pid]
        results.append(
            SearchResult(
                id=pid,
                title=p["name"],
                snippet=p["description_short"],
                url=p["url"],
                score=scores[pid],
                metadata={
                    "sku": p["sku"],
                    "external_id": p["external_id"],
                    "price_regular": p["price_regular"],
                    "price_sale": p["price_sale"],
                    "currency": p["currency"],
                    "stock_quantity": p["stock_quantity"],
                    "stock_status": p["stock_status"],
                },
            )
        )
    return results


def get_or_create_connector_def(db, name: str, kind: str, version: str) -> ConnectorDef:
    """Retorna o crea el ConnectorDef para este conector."""
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == name).first()
    if existing:
        existing.version = version
        existing.kind = kind
        return existing
    defn = ConnectorDef(id=uuid.uuid4(), name=name, kind=kind, version=version)
    db.add(defn)
    db.flush()
    return defn
