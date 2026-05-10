from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

ConnectorKind = Literal["ecommerce", "knowledge", "crm", "calendar"]


class ToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    callable_ref: str


class SyncResult(BaseModel):
    items_processed: int
    items_created: int
    items_updated: int
    items_deleted: int
    errors: list[str]
    started_at: datetime
    finished_at: datetime
    cursor: str | None = None


class SearchResult(BaseModel):
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
    """Contrato que todo conector externo debe implementar."""

    name: str
    kind: ConnectorKind
    version: str

    def __init__(self, tenant_id: UUID, config_id: UUID) -> None: ...

    @abstractmethod
    def configure(self, credentials: dict[str, Any]) -> None: ...

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def sync_full(self) -> SyncResult: ...

    @abstractmethod
    def sync_incremental(self, since: datetime) -> SyncResult: ...

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> WebhookVerification: ...

    @abstractmethod
    def webhook_handler(self, payload: dict[str, Any], headers: dict[str, str]) -> None: ...

    @abstractmethod
    def expose_tools(self) -> list[ToolSchema]: ...

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...
