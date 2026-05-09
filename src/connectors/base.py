"""Interfaz ABC que todo conector externo debe implementar.

El núcleo del Hub solo conoce esta interfaz; los detalles del proveedor
quedan encapsulados en cada implementación concreta.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

ConnectorKind = Literal["ecommerce", "knowledge", "crm", "calendar"]


class ToolSchema(BaseModel):
    """Definición de un tool que el agente Claude puede invocar."""

    name: str
    description: str
    input_schema: dict[str, Any]
    callable_ref: str


class SyncResult(BaseModel):
    """Resultado de una operación de sincronización."""

    items_processed: int
    items_created: int
    items_updated: int
    items_deleted: int
    errors: list[str]
    started_at: datetime
    finished_at: datetime
    cursor: str | None = None


class SearchResult(BaseModel):
    """Un resultado de búsqueda semántica/full-text."""

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
    """Contrato que todo conector externo debe implementar.

    Cada instancia pertenece a un tenant. El núcleo del Hub no conoce
    los detalles del proveedor: solo invoca esta interfaz.

    ``db`` es opcional: cuando se pasa (desde un endpoint FastAPI), el conector
    reutiliza esa sesión en lugar de abrir una nueva. Esto es necesario para que
    los tests puedan usar la sesión de rollback del conftest.
    """

    name: str
    kind: ConnectorKind
    version: str

    def __init__(self, tenant_id: UUID, config_id: UUID, db=None) -> None:
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._db_session = db  # sesión inyectada desde el endpoint

    @abstractmethod
    def configure(self, credentials: dict[str, Any]) -> None:
        """Persiste y valida credenciales cifradas at rest."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Llama un endpoint barato del proveedor para validar credenciales."""

    @abstractmethod
    def sync_full(self) -> SyncResult:
        """Sincronización completa del catálogo desde cero."""

    @abstractmethod
    def sync_incremental(self, since: datetime) -> SyncResult:
        """Sincronización incremental desde ``since``."""

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification:
        """Verifica autenticidad de un webhook entrante del proveedor."""

    @abstractmethod
    def webhook_handler(self, payload: dict[str, Any], headers: dict[str, str]) -> None:
        """Procesa un webhook ya verificado."""

    @abstractmethod
    def expose_tools(self) -> list[ToolSchema]:
        """Tools que el agente Claude puede llamar para este conector."""

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Búsqueda semántica/full-text sobre el catálogo."""
