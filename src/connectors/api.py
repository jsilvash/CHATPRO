"""API REST de conectores y catálogo de productos (Fase 5).

Endpoints:
- GET    /v1/connectors                            — lista tipos de conector disponibles
- GET    /v1/connector-configs                     — lista configs del tenant
- POST   /v1/connector-configs                     — crea una config
- GET    /v1/connector-configs/{id}                — detalle de una config
- POST   /v1/connector-configs/{id}/configure      — persiste credenciales cifradas
- POST   /v1/connector-configs/{id}/test           — prueba la conexión
- POST   /v1/connector-configs/{id}/sync           — dispara sync_full
- DELETE /v1/connector-configs/{id}                — elimina config + productos
- GET    /v1/connector-configs/{id}/products       — lista productos sincronizados
- GET    /v1/connector-configs/{id}/products/{pid} — detalle de un producto
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.connectors.models import ConnectorConfig, ConnectorDef, Product
from src.connectors.registry import get_connector_class, get_registry
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter

router = APIRouter(tags=["connectors"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class ConnectorDefOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    version: str
    enabled: bool

    model_config = {"from_attributes": True}


class ConnectorConfigOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    connector_def_id: uuid.UUID
    display_name: str
    status: str
    last_full_sync_at: datetime | None
    last_incremental_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectorConfigCreate(BaseModel):
    connector_name: str
    display_name: str


class ConnectorCredentials(BaseModel):
    site_url: str
    consumer_key: str
    consumer_secret: str


class ProductOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    connector_config_id: uuid.UUID
    external_id: str
    sku: str | None
    name: str
    description_short: str | None
    price_regular: float | None
    price_sale: float | None
    currency: str | None
    stock_quantity: int | None
    stock_status: str | None
    url: str | None
    categories: list | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncResultOut(BaseModel):
    items_processed: int
    items_created: int
    items_updated: int
    items_deleted: int
    errors: list[str]
    started_at: datetime
    finished_at: datetime


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_config_or_404(
    config_id: uuid.UUID, tenant_id: uuid.UUID, db: Session
) -> ConnectorConfig:
    with bypass_tenant_filter():
        config = db.get(ConnectorConfig, config_id)
    if config is None or config.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config no encontrada")
    return config


def _ensure_connector_defs(db: Session) -> None:
    """Sincroniza los ConnectorDef en BD con el registro en código."""
    from src.connectors.woocommerce.connector import get_or_create_connector_def

    registry = get_registry()
    for name, cls in registry.items():
        get_or_create_connector_def(db, name=name, kind=cls.kind, version=cls.version)
    db.commit()


# ── Endpoints — tipos de conector disponibles ─────────────────────────────────


@router.get("/connectors", response_model=list[ConnectorDefOut])
def listar_tipos_conector(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_connector_defs(db)
    with bypass_tenant_filter():
        defs = db.query(ConnectorDef).filter(ConnectorDef.enabled.is_(True)).all()
    return defs


# ── Endpoints — configs del tenant ────────────────────────────────────────────


@router.get("/connector-configs", response_model=list[ConnectorConfigOut])
def listar_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    configs = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.tenant_id == current_user.tenant_id)
        .all()
    )
    return configs


@router.post("/connector-configs", response_model=ConnectorConfigOut, status_code=201)
def crear_config(
    body: ConnectorConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_connector_defs(db)

    with bypass_tenant_filter():
        defn = db.query(ConnectorDef).filter(ConnectorDef.name == body.connector_name).first()
    if defn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Conector '{body.connector_name}' no disponible",
        )

    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        connector_def_id=defn.id,
        display_name=body.display_name,
        status="pending",
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/connector-configs/{config_id}", response_model=ConnectorConfigOut)
def obtener_config(
    config_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_config_or_404(config_id, current_user.tenant_id, db)


@router.delete("/connector-configs/{config_id}", status_code=204)
def eliminar_config(
    config_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = _get_config_or_404(config_id, current_user.tenant_id, db)
    db.delete(config)
    db.commit()


# ── Endpoints — operaciones del conector ──────────────────────────────────────


@router.post("/connector-configs/{config_id}/configure", response_model=ConnectorConfigOut)
def configurar_credenciales(
    config_id: uuid.UUID,
    body: ConnectorCredentials,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = _get_config_or_404(config_id, current_user.tenant_id, db)

    with bypass_tenant_filter():
        defn = db.get(ConnectorDef, config.connector_def_id)

    connector_cls = get_connector_class(defn.name)
    connector = connector_cls(current_user.tenant_id, config_id, db=db)

    try:
        connector.configure(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    db.refresh(config)
    return config


@router.post("/connector-configs/{config_id}/test")
def probar_conexion(
    config_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = _get_config_or_404(config_id, current_user.tenant_id, db)

    with bypass_tenant_filter():
        defn = db.get(ConnectorDef, config.connector_def_id)

    connector_cls = get_connector_class(defn.name)
    connector = connector_cls(current_user.tenant_id, config_id, db=db)

    ok = connector.test_connection()
    # El conector ya actualizó config en la sesión (via flush); no hace falta refresh.
    return {"ok": ok, "status": config.status, "last_error": config.last_error}


@router.post("/connector-configs/{config_id}/sync", response_model=SyncResultOut)
def sincronizar_full(
    config_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = _get_config_or_404(config_id, current_user.tenant_id, db)

    if config.status not in ("connected", "error"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El conector debe estar en estado 'connected' antes de sincronizar. "
                   "Ejecuta /configure y /test primero.",
        )

    with bypass_tenant_filter():
        defn = db.get(ConnectorDef, config.connector_def_id)

    connector_cls = get_connector_class(defn.name)
    connector = connector_cls(current_user.tenant_id, config_id, db=db)

    result = connector.sync_full()
    return result


# ── Endpoints — productos ─────────────────────────────────────────────────────


@router.get("/connector-configs/{config_id}/products", response_model=list[ProductOut])
def listar_productos(
    config_id: uuid.UUID,
    q: str | None = Query(None, description="Filtrar por nombre/SKU"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_deleted: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_config_or_404(config_id, current_user.tenant_id, db)

    query = db.query(Product).filter(
        Product.tenant_id == current_user.tenant_id,
        Product.connector_config_id == config_id,
    )
    if not include_deleted:
        query = query.filter(Product.deleted_at.is_(None))
    if q:
        query = query.filter(
            Product.name.ilike(f"%{q}%") | Product.sku.ilike(f"%{q}%")
        )

    return query.offset(skip).limit(limit).all()


@router.get("/connector-configs/{config_id}/products/{product_id}", response_model=ProductOut)
def obtener_producto(
    config_id: uuid.UUID,
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_config_or_404(config_id, current_user.tenant_id, db)

    product = (
        db.query(Product)
        .filter(
            Product.tenant_id == current_user.tenant_id,
            Product.connector_config_id == config_id,
            Product.id == product_id,
        )
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return product
