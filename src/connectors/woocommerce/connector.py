"""WooCommerceConnector — implementación completa para Fases 5 y 6.

Implementa:
- configure()         — persiste y cifra credenciales; valida campos requeridos.
- test_connection()   — llama /wc/v3/system_status con las credenciales actuales.
- sync_full()         — pagina /wc/v3/products y persiste en tabla ``products``.
- sync_incremental()  — GET /wc/v3/products?modified_after=since (Fase 6).
- verify_webhook()    — verifica firma HMAC-SHA256 (Fase 6).
- webhook_handler()   — procesa product/order events (Fase 6).
- embed_product()     — genera embedding con Voyage/OpenAI y lo guarda (Fase 6).
- expose_tools()      — 3 tools semánticos para el agente.
- search()            — búsqueda híbrida texto + pgvector cosine + rerank (Fase 6).
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from base64 import b64decode
from contextlib import contextmanager
from datetime import UTC, datetime

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
from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product, ProductEmbedding
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
        """Reutiliza ``_db_session`` si existe, o abre una nueva."""
        if self._db_session is not None:
            yield self._db_session
        else:
            with get_db_session() as db:
                yield db

    def _load_credentials(self) -> dict:
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
        self._credentials = None

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
        started_at = datetime.now(UTC)
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

        return SyncResult(
            items_processed=stats["processed"],
            items_created=stats["created"],
            items_updated=stats["updated"],
            items_deleted=stats["deleted"],
            errors=errors,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    def _sync_products_pages(
        self,
        stats: dict,
        errors: list[str],
        extra_params: dict | None = None,
    ) -> None:
        page = 1
        params_base = {
            "per_page": _PAGE_SIZE,
            "status": "publish",
            "orderby": "id",
            "order": "asc",
        }
        if extra_params:
            params_base.update(extra_params)

        with self._build_client() as client:
            while True:
                try:
                    resp = client.get(
                        "/wp-json/wc/v3/products",
                        params={**params_base, "page": page},
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
                config.last_full_sync_at = datetime.now(UTC)
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

    def _update_config_incremental_sync_ok(self) -> None:
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config:
                config.last_incremental_sync_at = datetime.now(UTC)
                config.last_error = None
                if self._db_session is None:
                    db.commit()
                else:
                    db.flush()

    # ── sync_incremental ──────────────────────────────────────────────────────

    def sync_incremental(self, since: datetime) -> SyncResult:
        """Sincronización incremental de productos modificados desde ``since``."""
        started_at = datetime.now(UTC)
        stats = {"processed": 0, "created": 0, "updated": 0, "deleted": 0}
        errors: list[str] = []

        since_iso = since.isoformat()
        try:
            self._sync_products_pages(
                stats,
                errors,
                extra_params={"modified_after": since_iso, "orderby": "modified"},
            )
        except Exception as exc:
            errors.append(f"Error en sync_incremental: {exc}")
            logger.exception("sync_incremental WooCommerce falló")
        else:
            self._update_config_incremental_sync_ok()

        return SyncResult(
            items_processed=stats["processed"],
            items_created=stats["created"],
            items_updated=stats["updated"],
            items_deleted=stats["deleted"],
            errors=errors,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # ── verify_webhook ────────────────────────────────────────────────────────

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification:
        """Verifica X-WC-Webhook-Signature (HMAC-SHA256 base64)."""
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config is None:
                return WebhookVerification(valid=False, reason="config no encontrada")
            if config.tenant_id != self.tenant_id:
                return WebhookVerification(valid=False, reason="tenant no coincide")
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

    # ── webhook_handler ───────────────────────────────────────────────────────

    def webhook_handler(self, payload: dict, headers: dict[str, str]) -> None:
        """Procesa un webhook verificado de WooCommerce.

        Topics soportados: product.created, product.updated, product.deleted,
        order.created, order.updated.
        """
        topic = headers.get("x-wc-webhook-topic", "")
        logger.info(
            "webhook_handler: topic=%s tenant=%s config=%s",
            topic, self.tenant_id, self.config_id,
        )

        if topic in ("product.created", "product.updated"):
            stats: dict = {"processed": 0, "created": 0, "updated": 0, "deleted": 0}
            self._upsert_product(payload, stats)
            # Intenta generar embedding (puede fallar si no hay clave API configurada)
            external_id = str(payload.get("id", ""))
            if external_id:
                product_id = self._get_product_id_by_external(external_id)
                if product_id:
                    try:
                        self.embed_product(product_id)
                    except Exception as exc:
                        logger.warning(
                            "embed_product falló para external_id=%s: %s",
                            external_id, exc,
                        )

        elif topic == "product.deleted":
            external_id = str(payload.get("id", ""))
            if external_id:
                self._soft_delete_by_external_id(external_id)

        elif topic in ("order.created", "order.updated"):
            self._upsert_order(payload)

        else:
            logger.debug("topic desconocido ignorado: %s", topic)

    # ── embed_product ─────────────────────────────────────────────────────────

    def embed_product(self, product_id: uuid.UUID) -> None:
        """Genera y guarda el embedding del producto en ``product_embeddings``."""
        from src.connectors.embeddings import generate_embedding

        with self._get_db() as db:
            product = db.get(Product, product_id)
            if product is None or product.tenant_id != self.tenant_id:
                return

            parts = [product.name]
            if product.description_short:
                parts.append(product.description_short)
            if product.sku:
                parts.append(f"SKU: {product.sku}")
            if isinstance(product.categories, list):
                cats = ", ".join(str(c) for c in product.categories if c)
                if cats:
                    parts.append(f"Categorías: {cats}")
            content = " | ".join(p for p in parts if p)

            vector = generate_embedding(content)

            existing = (
                db.query(ProductEmbedding)
                .filter(
                    ProductEmbedding.product_id == product_id,
                    ProductEmbedding.tenant_id == self.tenant_id,
                )
                .first()
            )
            if existing:
                existing.content = content
                existing.embedding = vector
            else:
                emb = ProductEmbedding(
                    id=uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    product_id=product_id,
                    content=content,
                    embedding=vector,
                )
                db.add(emb)

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

    # ── search (híbrida: ILIKE + pgvector cosine + rerank) ────────────────────

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Búsqueda híbrida: texto (ILIKE) + vectorial (pgvector cosine) + rerank.

        Pesos: texto=0.4, vector=0.6. Si embeddings no disponibles, solo texto.
        """
        # {product_id_str: {"product": Product, "text_score": float, "vec_score": float}}
        candidates: dict[str, dict] = {}

        with self._get_db() as db:
            # ── 1. Búsqueda por texto (ILIKE) ─────────────────────────────────
            text_matches = (
                db.query(Product)
                .filter(
                    Product.tenant_id == self.tenant_id,
                    Product.connector_config_id == self.config_id,
                    Product.deleted_at.is_(None),
                )
                .filter(
                    Product.name.ilike(f"%{query}%")
                    | Product.description_short.ilike(f"%{query}%")
                    | Product.sku.ilike(f"%{query}%"),
                )
                .limit(top_k * 2)
                .all()
            )
            for p in text_matches:
                candidates[str(p.id)] = {"product": p, "text_score": 1.0, "vec_score": 0.0}

            # ── 2. Búsqueda vectorial (cosine via pgvector) ───────────────────
            try:
                from src.connectors.embeddings import generate_embedding

                query_vector = generate_embedding(query)
                qvec_str = "[" + ",".join(str(float(v)) for v in query_vector) + "]"

                rows = db.execute(
                    text("""
                        SELECT p.id::text,
                               1 - (pe.embedding <=> CAST(:qvec AS vector)) AS similarity
                        FROM products p
                        JOIN product_embeddings pe ON pe.product_id = p.id
                        WHERE p.tenant_id = :tid
                          AND p.connector_config_id = :cid
                          AND p.deleted_at IS NULL
                          AND pe.tenant_id = :tid
                        ORDER BY pe.embedding <=> CAST(:qvec AS vector)
                        LIMIT :k
                    """),
                    {
                        "qvec": qvec_str,
                        "tid": str(self.tenant_id),
                        "cid": str(self.config_id),
                        "k": top_k,
                    },
                ).fetchall()

                for row in rows:
                    pid_str = str(row[0])
                    similarity = max(0.0, float(row[1]))
                    if pid_str in candidates:
                        candidates[pid_str]["vec_score"] = similarity
                    else:
                        product = db.get(Product, uuid.UUID(pid_str))
                        if product:
                            candidates[pid_str] = {
                                "product": product,
                                "text_score": 0.0,
                                "vec_score": similarity,
                            }

            except Exception as exc:
                logger.debug("Búsqueda vectorial no disponible: %s", exc)

            # ── 3. Rerank por score combinado ─────────────────────────────────
            ranked = sorted(
                candidates.values(),
                key=lambda x: x["text_score"] * 0.4 + x["vec_score"] * 0.6,
                reverse=True,
            )

            results = []
            for item in ranked[:top_k]:
                p = item["product"]
                score = item["text_score"] * 0.4 + item["vec_score"] * 0.6
                results.append(
                    SearchResult(
                        id=str(p.id),
                        title=p.name,
                        snippet=p.description_short or "",
                        url=p.url,
                        score=score,
                        metadata={
                            "sku": p.sku,
                            "external_id": p.external_id,
                            "price_regular": float(p.price_regular) if p.price_regular else None,
                            "price_sale": float(p.price_sale) if p.price_sale else None,
                            "currency": p.currency,
                            "stock_quantity": p.stock_quantity,
                            "stock_status": p.stock_status,
                        },
                    )
                )
            return results

    # ── Helpers privados ──────────────────────────────────────────────────────

    def _get_product_id_by_external(self, external_id: str) -> uuid.UUID | None:
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

    def _soft_delete_by_external_id(self, external_id: str) -> None:
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
                product.deleted_at = datetime.now(UTC)
                if self._db_session is None:
                    db.commit()
                else:
                    db.flush()

    def _upsert_order(self, raw: dict) -> None:
        external_id = str(raw.get("id", ""))
        if not external_id or external_id == "":
            return

        placed_at = None
        date_str = raw.get("date_created") or raw.get("date_created_gmt")
        if date_str:
            try:
                placed_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
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

            data = {
                "tenant_id": self.tenant_id,
                "connector_config_id": self.config_id,
                "external_id": external_id,
                "status": raw.get("status"),
                "total": _to_decimal(raw.get("total")),
                "currency": raw.get("currency"),
                "placed_at": placed_at,
                "raw": raw,
            }

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
            else:
                db.add(Order(id=uuid.uuid4(), **data))

            if self._db_session is None:
                db.commit()
            else:
                db.flush()


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
        "updated_at": datetime.now(UTC),
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
