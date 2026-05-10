"""ShopifyConnector — implementación para Fase 12.

Implementa:
- configure()        — persiste y cifra shop_url + access_token; valida campos requeridos.
- test_connection()  — llama GET /admin/api/2024-01/shop.json para validar credenciales.
- sync_full()        — pagina /admin/api/2024-01/products.json con cursor y persiste en ``products``.
- sync_incremental() — placeholder (fase futura).
- verify_webhook()   — verifica HMAC-SHA256 (X-Shopify-Hmac-Sha256).
- webhook_handler()  — placeholder (fase futura).
- expose_tools()     — 2 tools semánticos para el agente.
- search()           — búsqueda full-text sobre ``products`` en BD local.
"""

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from base64 import b64encode
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

_API_VERSION = "2024-01"
_PAGE_SIZE = 250  # máximo permitido por Shopify
_REQUEST_TIMEOUT = 30.0


class ShopifyConnector(Connector):
    """Conector para Shopify Admin REST API."""

    name = "shopify"
    kind = "ecommerce"
    version = "1.0.0"

    def __init__(self, tenant_id: uuid.UUID, config_id: uuid.UUID, db=None) -> None:
        super().__init__(tenant_id, config_id, db)
        self._credentials: dict | None = None

    @contextmanager
    def _get_db(self):
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
        shop_url = creds["shop_url"].rstrip("/")
        return httpx.Client(
            base_url=f"{shop_url}/admin/api/{_API_VERSION}",
            headers={
                "X-Shopify-Access-Token": creds["access_token"],
                "User-Agent": "ChatPro-ShopifyConnector/1.0",
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )

    # ── configure ─────────────────────────────────────────────────────────────

    def configure(self, credentials: dict) -> None:
        """Persiste credenciales cifradas y genera webhook_secret."""
        required = {"shop_url", "access_token"}
        missing = required - credentials.keys()
        if missing:
            raise ValueError(f"Faltan campos requeridos: {missing}")

        shop_url = credentials["shop_url"].rstrip("/")
        if not shop_url.startswith(("http://", "https://")):
            raise ValueError("shop_url debe comenzar con http:// o https://")

        blob = encrypt_credentials({
            "shop_url": shop_url,
            "access_token": credentials["access_token"],
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
        """Llama GET /shop.json y actualiza status en BD."""
        try:
            with self._build_client() as client:
                resp = client.get("/shop.json")
            ok = resp.status_code == 200
            status = "connected" if ok else "error"
            error_msg = None if ok else f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            ok = False
            status = "error"
            error_msg = str(exc)[:500]
            logger.warning("Shopify test_connection falló: %s", exc)

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
        """Pagina /products.json con cursor y persiste en tabla products."""
        started_at = datetime.now(timezone.utc)
        stats = {"processed": 0, "created": 0, "updated": 0, "deleted": 0}
        errors: list[str] = []

        try:
            self._sync_products_pages(stats, errors)
        except Exception as exc:
            errors.append(f"Error fatal en sync_full: {exc}")
            logger.exception("sync_full Shopify falló")
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
        """Pagina usando cursor (Link header) de Shopify."""
        params: dict = {"limit": _PAGE_SIZE, "status": "active"}
        with self._build_client() as client:
            while True:
                try:
                    resp = client.get("/products.json", params=params)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    errors.append(f"Petición Shopify: {exc}")
                    break

                data = resp.json()
                items = data.get("products", [])
                if not items:
                    break

                for raw_product in items:
                    try:
                        self._upsert_product(raw_product, stats)
                    except Exception as exc:
                        errors.append(f"Producto {raw_product.get('id')}: {exc}")

                stats["processed"] += len(items)

                # Shopify usa cursor via Link header
                next_url = _parse_next_link(resp.headers.get("Link", ""))
                if not next_url:
                    break
                # Extraer page_info del next_url y usar en la siguiente petición
                page_info = _extract_page_info(next_url)
                if not page_info:
                    break
                params = {"limit": _PAGE_SIZE, "page_info": page_info}

    def _upsert_product(self, raw: dict, stats: dict) -> None:
        external_id = str(raw["id"])
        shop_url = self._load_credentials()["shop_url"].rstrip("/")
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

            product_data = _map_shopify_product(raw, self.tenant_id, self.config_id, shop_url)

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

    # ── sync_incremental (fase futura) ────────────────────────────────────────

    def sync_incremental(self, since: datetime) -> SyncResult:
        started_at = datetime.now(timezone.utc)
        return SyncResult(
            items_processed=0,
            items_created=0,
            items_updated=0,
            items_deleted=0,
            errors=["sync_incremental no implementado aún (fase futura)"],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

    # ── verify_webhook ────────────────────────────────────────────────────────

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification:
        """Verifica X-Shopify-Hmac-Sha256 (HMAC-SHA256 base64)."""
        with self._get_db() as db:
            config = db.get(ConnectorConfig, self.config_id)
            if config is None:
                return WebhookVerification(valid=False, reason="config no encontrada")
            secret = config.webhook_secret

        sig_header = headers.get("x-shopify-hmac-sha256", "")
        if not sig_header:
            return WebhookVerification(valid=False, reason="header firma ausente")

        expected = b64encode(
            hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        ).decode()

        if not hmac.compare_digest(expected, sig_header):
            return WebhookVerification(valid=False, reason="firma inválida")

        return WebhookVerification(valid=True)

    # ── webhook_handler (fase futura) ─────────────────────────────────────────

    def webhook_handler(self, payload: dict, headers: dict[str, str]) -> None:
        pass

    # ── expose_tools ──────────────────────────────────────────────────────────

    def expose_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="shopify_buscar_productos",
                description=(
                    "Busca productos del catálogo Shopify del negocio por nombre, "
                    "descripción o SKU. Devuelve nombre, precio, stock y link. Úsalo "
                    "cuando el cliente pregunte por un producto o servicio."
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
                callable_ref="connectors.shopify.tools:buscar_productos",
            ),
            ToolSchema(
                name="shopify_consultar_stock_y_precio",
                description=(
                    "Consulta stock disponible, precio y link de un producto Shopify "
                    "específico por SKU o ID de producto. Úsalo cuando el cliente "
                    "pregunte por disponibilidad o precio exacto de un ítem."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string", "description": "SKU del producto"},
                        "product_id": {
                            "type": "string",
                            "description": "ID externo del producto en Shopify",
                        },
                    },
                },
                callable_ref="connectors.shopify.tools:consultar_stock_y_precio",
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

def _map_shopify_product(raw: dict, tenant_id: uuid.UUID, config_id: uuid.UUID, shop_url: str = "") -> dict:
    """Mapea un producto del API Shopify al schema de la tabla ``products``."""
    variants = raw.get("variants") or []
    first_variant = variants[0] if variants else {}

    price = _to_decimal(first_variant.get("price"))
    compare_at = _to_decimal(first_variant.get("compare_at_price"))

    # En Shopify: compare_at_price es el precio original (tachado), price es el actual.
    if compare_at and compare_at > (price or 0):
        price_regular = compare_at
        price_sale = price
    else:
        price_regular = price
        price_sale = None

    stock_quantity = first_variant.get("inventory_quantity")
    inventory_policy = first_variant.get("inventory_policy", "deny")
    if stock_quantity is None:
        stock_status = None
    elif stock_quantity > 0:
        stock_status = "in_stock"
    elif inventory_policy == "continue":
        stock_status = "on_backorder"
    else:
        stock_status = "out_of_stock"

    images = raw.get("images") or []
    categories_list = []
    for custom_coll in raw.get("collections") or []:
        if title := custom_coll.get("title"):
            categories_list.append(title)
    tags = raw.get("tags", "")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "connector_config_id": config_id,
        "external_id": str(raw.get("id", "")),
        "sku": first_variant.get("sku") or None,
        "name": raw.get("title", ""),
        "description_short": _strip_html(raw.get("body_html", ""))[:500] or None,
        "description_long": _strip_html(raw.get("body_html", "")) or None,
        "price_regular": price_regular,
        "price_sale": price_sale,
        "currency": None,  # Shopify devuelve moneda a nivel de tienda, no por producto
        "stock_quantity": stock_quantity,
        "stock_status": stock_status,
        "url": f"{shop_url}/products/{raw['handle']}" if shop_url and raw.get("handle") else None,
        "images": [img.get("src") for img in images] or None,
        "categories": categories_list or tag_list or None,
        "attributes": {
            opt["name"]: opt.get("values", [])
            for opt in (raw.get("options") or [])
        } or None,
        "variations": [
            {"id": v.get("id"), "sku": v.get("sku"), "price": v.get("price")}
            for v in variants[1:]
        ] or None,
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
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_next_link(link_header: str) -> str | None:
    """Extrae la URL 'next' del header Link de Shopify."""
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1)
    return None


def _extract_page_info(url: str) -> str | None:
    """Extrae el parámetro page_info de una URL."""
    match = re.search(r"[?&]page_info=([^&]+)", url)
    if match:
        return match.group(1)
    return None


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
