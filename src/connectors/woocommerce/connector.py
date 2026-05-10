"""WooCommerce connector — Fase 6: sync incremental + webhooks + embeddings + búsqueda híbrida."""

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from src.connectors.base import (
    Connector,
    SearchResult,
    SyncResult,
    ToolSchema,
    WebhookVerification,
)
from src.connectors.embeddings import embed_texts
from src.connectors.models import ConnectorConfig, Product

logger = logging.getLogger(__name__)

_STOCK_STATUS_MAP = {
    "instock": "in_stock",
    "outofstock": "out_of_stock",
    "onbackorder": "on_backorder",
}

_WC_PER_PAGE = 100


class WooCommerceConnector(Connector):
    name = "woocommerce"
    kind = "ecommerce"
    version = "1.0.0"

    def __init__(
        self,
        tenant_id: UUID,
        config_id: UUID,
        session: Session,
        voyage_api_key: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._session = session
        self._voyage_api_key = voyage_api_key
        self._config: ConnectorConfig | None = None
        self._creds: dict[str, str] | None = None

    # ------------------------------------------------------------------ config

    def _load_config(self) -> ConnectorConfig:
        if self._config is None:
            cfg = self._session.get(ConnectorConfig, self.config_id)
            if cfg is None or str(cfg.tenant_id) != str(self.tenant_id):
                raise ValueError(
                    f"ConnectorConfig {self.config_id} no encontrada para tenant {self.tenant_id}"
                )
            self._config = cfg
        return self._config

    def _get_creds(self) -> dict[str, str]:
        if self._creds is None:
            raw = self._load_config().encrypted_credentials
            self._creds = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        return self._creds

    def _make_client(self) -> httpx.Client:
        creds = self._get_creds()
        return httpx.Client(
            base_url=creds["site_url"].rstrip("/"),
            auth=(creds["consumer_key"], creds["consumer_secret"]),
            timeout=30.0,
        )

    def configure(self, credentials: dict[str, Any]) -> None:
        """Persiste credenciales (cifrado real delgado: JSON + BYTEA; AES-GCM en Fase posterior)."""
        cfg = self._load_config()
        cfg.encrypted_credentials = json.dumps(credentials).encode()
        cfg.updated_at = datetime.now(tz=timezone.utc)
        self._session.flush()
        self._creds = None

    def test_connection(self) -> bool:
        try:
            with self._make_client() as client:
                r = client.get("/wc/v3/system_status")
                return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------ sync

    def sync_full(self) -> SyncResult:
        started = datetime.now(tz=timezone.utc)
        result = SyncResult(
            items_processed=0,
            items_created=0,
            items_updated=0,
            items_deleted=0,
            errors=[],
            started_at=started,
            finished_at=started,
        )
        with self._make_client() as client:
            page = 1
            while True:
                resp = client.get(
                    "/wc/v3/products",
                    params={"per_page": _WC_PER_PAGE, "page": page, "status": "publish"},
                )
                if resp.status_code != 200:
                    result.errors.append(f"HTTP {resp.status_code} en página {page}")
                    break
                products = resp.json()
                if not products:
                    break
                for p in products:
                    created = self._upsert_product(p)
                    result.items_processed += 1
                    if created:
                        result.items_created += 1
                    else:
                        result.items_updated += 1
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page >= total_pages:
                    break
                page += 1

        cfg = self._load_config()
        cfg.last_full_sync_at = datetime.now(tz=timezone.utc)
        self._session.flush()
        result.finished_at = datetime.now(tz=timezone.utc)
        return result

    def sync_incremental(self, since: datetime) -> SyncResult:
        """GET /wc/v3/products?modified_after=<since> con paginación completa."""
        started = datetime.now(tz=timezone.utc)
        result = SyncResult(
            items_processed=0,
            items_created=0,
            items_updated=0,
            items_deleted=0,
            errors=[],
            started_at=started,
            finished_at=started,
            cursor=since.isoformat(),
        )
        since_iso = since.isoformat()
        with self._make_client() as client:
            page = 1
            while True:
                resp = client.get(
                    "/wc/v3/products",
                    params={
                        "modified_after": since_iso,
                        "per_page": _WC_PER_PAGE,
                        "page": page,
                        "status": "any",
                    },
                )
                if resp.status_code != 200:
                    result.errors.append(f"HTTP {resp.status_code} en página {page}")
                    break
                products = resp.json()
                if not products:
                    break
                for p in products:
                    created = self._upsert_product(p)
                    result.items_processed += 1
                    if created:
                        result.items_created += 1
                    else:
                        result.items_updated += 1
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page >= total_pages:
                    break
                page += 1

        cfg = self._load_config()
        cfg.last_incremental_sync_at = datetime.now(tz=timezone.utc)
        self._session.flush()
        result.finished_at = datetime.now(tz=timezone.utc)
        result.cursor = result.finished_at.isoformat()
        return result

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification:
        """Verifica X-WC-Webhook-Signature = base64(HMAC-SHA256(secret, raw_body))."""
        cfg = self._load_config()
        # headers pueden llegar en cualquier casing
        sig_header = headers.get("x-wc-webhook-signature") or headers.get(
            "X-WC-Webhook-Signature"
        )
        if not sig_header:
            return WebhookVerification(
                valid=False, reason="Falta encabezado X-WC-Webhook-Signature"
            )
        computed = base64.b64encode(
            hmac.digest(cfg.webhook_secret.encode(), payload, hashlib.sha256)
        ).decode()
        if not hmac.compare_digest(computed, sig_header):
            return WebhookVerification(valid=False, reason="Firma inválida")
        return WebhookVerification(valid=True)

    def webhook_handler(self, payload: dict[str, Any], headers: dict[str, str]) -> None:
        """Procesa eventos WooCommerce: product.created/updated/deleted, order.*"""
        topic = headers.get("x-wc-webhook-topic") or headers.get(
            "X-WC-Webhook-Topic", ""
        )
        resource, _, event = topic.partition(".")
        if resource == "product":
            if event in ("created", "updated", "restored"):
                self._upsert_product(payload)
            elif event == "deleted":
                self._soft_delete_product(str(payload.get("id", "")))
        # order.* — deferred Fase 7

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Búsqueda híbrida: full-text (40%) + cosine similarity (60%). Prefiltro tenant_id."""
        results: dict[str, SearchResult] = {}

        # --- full-text (tsvector GIN index) ---
        fts_sql = text(
            """
            SELECT id::text, name, description_short, url, sku, price_regular, stock_status,
                   ts_rank(search_tsv, plainto_tsquery('spanish', :query)) AS score
            FROM products
            WHERE tenant_id = :tenant_id
              AND deleted_at IS NULL
              AND search_tsv @@ plainto_tsquery('spanish', :query)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        fts_rows = self._session.execute(
            fts_sql,
            {"query": query, "tenant_id": str(self.tenant_id), "top_k": top_k},
        ).fetchall()
        for row in fts_rows:
            results[row.id] = SearchResult(
                id=row.id,
                title=row.name or "",
                snippet=(row.description_short or "")[:300],
                url=row.url,
                score=float(row.score) * 0.4,
                metadata=_product_metadata(row),
            )

        # --- vector similarity (pgvector HNSW) ---
        if self._voyage_api_key:
            try:
                query_embedding = embed_texts([query], self._voyage_api_key)[0]
                vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
                vec_sql = text(
                    """
                    SELECT id::text, name, description_short, url, sku, price_regular, stock_status,
                           1 - (embedding <=> CAST(:query_vec AS vector)) AS score
                    FROM products
                    WHERE tenant_id = :tenant_id
                      AND deleted_at IS NULL
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:query_vec AS vector)
                    LIMIT :top_k
                    """
                )
                vec_rows = self._session.execute(
                    vec_sql,
                    {
                        "query_vec": vec_str,
                        "tenant_id": str(self.tenant_id),
                        "top_k": top_k,
                    },
                ).fetchall()
                for row in vec_rows:
                    vec_score = float(row.score) * 0.6
                    if row.id in results:
                        existing = results[row.id]
                        results[row.id] = SearchResult(
                            id=existing.id,
                            title=existing.title,
                            snippet=existing.snippet,
                            url=existing.url,
                            score=existing.score + vec_score,
                            metadata=existing.metadata,
                        )
                    else:
                        results[row.id] = SearchResult(
                            id=row.id,
                            title=row.name or "",
                            snippet=(row.description_short or "")[:300],
                            url=row.url,
                            score=vec_score,
                            metadata=_product_metadata(row),
                        )
            except Exception as exc:
                logger.warning("Búsqueda vectorial falló, solo FTS: %s", exc)

        return sorted(results.values(), key=lambda r: r.score, reverse=True)[:top_k]

    def expose_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="buscar_productos",
                description="Busca productos del catálogo del negocio. Recibe query libre.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                callable_ref="connectors.woocommerce.tools:buscar_productos",
            ),
            ToolSchema(
                name="consultar_stock_y_precio",
                description="Consulta stock, precio y link de un producto por SKU o ID.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "product_id": {"type": "integer"},
                    },
                },
                callable_ref="connectors.woocommerce.tools:consultar_stock_y_precio",
            ),
            ToolSchema(
                name="historial_pedidos_contacto",
                description="Lista los pedidos del contacto actual (resueltos por email/teléfono).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 5},
                    },
                },
                callable_ref="connectors.woocommerce.tools:historial_pedidos_contacto",
            ),
        ]

    # ------------------------------------------------------------------ privados

    def _upsert_product(self, wc_product: dict[str, Any]) -> bool:
        """Inserta o actualiza un producto. Retorna True si fue creación."""
        external_id = str(wc_product["id"])
        existing = self._session.scalar(
            select(Product).where(
                Product.tenant_id == self.tenant_id,
                Product.connector_config_id == self.config_id,
                Product.external_id == external_id,
            )
        )
        mapped = _map_product(wc_product)
        embedding = self._compute_embedding(wc_product)
        if embedding is not None:
            mapped["embedding"] = embedding

        now = datetime.now(tz=timezone.utc)
        if existing:
            for key, val in mapped.items():
                setattr(existing, key, val)
            existing.updated_at = now
            existing.deleted_at = None
            return False
        else:
            self._session.add(
                Product(
                    tenant_id=self.tenant_id,
                    connector_config_id=self.config_id,
                    external_id=external_id,
                    created_at=now,
                    updated_at=now,
                    **mapped,
                )
            )
            return True

    def _soft_delete_product(self, external_id: str) -> None:
        self._session.execute(
            update(Product)
            .where(
                Product.tenant_id == self.tenant_id,
                Product.connector_config_id == self.config_id,
                Product.external_id == external_id,
            )
            .values(deleted_at=datetime.now(tz=timezone.utc))
        )

    def _compute_embedding(self, wc_product: dict[str, Any]) -> list[float] | None:
        if not self._voyage_api_key:
            return None
        content = _product_text(wc_product)
        if not content:
            return None
        try:
            results = embed_texts([content], self._voyage_api_key)
            return results[0] if results else None
        except Exception as exc:
            logger.warning(
                "Embedding falló para producto %s: %s", wc_product.get("id"), exc
            )
            return None


# ------------------------------------------------------------------ helpers puros

def _map_product(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": p.get("sku") or None,
        "name": p.get("name", ""),
        "description_short": p.get("short_description") or None,
        "description_long": p.get("description") or None,
        "price_regular": _to_decimal(p.get("regular_price")),
        "price_sale": _to_decimal(p.get("sale_price")),
        "currency": p.get("currency") or None,
        "stock_quantity": p.get("stock_quantity"),
        "stock_status": _STOCK_STATUS_MAP.get(
            p.get("stock_status", ""), p.get("stock_status")
        ),
        "url": p.get("permalink") or None,
        "images": p.get("images"),
        "categories": p.get("categories"),
        "attributes": p.get("attributes"),
        "variations": p.get("variations"),
        "raw": p,
    }


def _product_text(p: dict[str, Any]) -> str:
    """Construye el texto que se embebería para el producto."""
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


def _to_decimal(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _product_metadata(row: Any) -> dict[str, Any]:
    return {
        "sku": row.sku,
        "price": str(row.price_regular or ""),
        "stock_status": row.stock_status or "",
    }
