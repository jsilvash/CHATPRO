"""WooCommerceConnector — implementación completa para Fase 5.

Implementa:
- configure()       — persiste y cifra credenciales; valida campos requeridos.
- test_connection() — llama /wc/v3/system_status con las credenciales actuales.
- sync_full()       — pagina /wc/v3/products y persiste en tabla ``products``.
- sync_incremental()— placeholder (Fase 6).
- verify_webhook()  — verifica firma HMAC-SHA256 (Fase 6).
- webhook_handler() — placeholder (Fase 6).
- expose_tools()    — 3 tools semánticos para el agente.
- search()          — búsqueda full-text sobre ``products`` (pgvector en Fase 6).
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

from src.connectors.base import (
    Connector,
    SearchResult,
    SyncResult,
    ToolSchema,
    WebhookVerification,
)
from src.connectors.crypto import decrypt_credentials, encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef, Product
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
        started_at = datetime.now(timezone.utc)
        return SyncResult(
            items_processed=0,
            items_created=0,
            items_updated=0,
            items_deleted=0,
            errors=["sync_incremental no implementado aún (Fase 6)"],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
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
        pass

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
        """Búsqueda full-text sobre productos en BD local."""
        results: list[SearchResult] = []
        with self._get_db() as db:
            q = (
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
                .limit(top_k)
                .all()
            )

            for p in q:
                results.append(
                    SearchResult(
                        id=str(p.id),
                        title=p.name,
                        snippet=p.description_short or "",
                        url=p.url,
                        score=1.0,
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
