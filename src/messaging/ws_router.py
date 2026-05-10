"""WebSocket endpoints para comunicación en tiempo real.

Rutas:
- /ws/inbox/{conversation_id}?token=<jwt>  — mensajes de inbox (Fase 23A)
- /ws/notifications/{tenant_id}?token=<jwt> — notificaciones de agentes (Fase 25D)

Auth: Token JWT (type=access) en query param ?token=.
Si el token es inválido o el tenant no coincide → cerrar con código 1008.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from src.auth.tokens import decode_token
from src.db.session import get_db
from src.messaging.ws_manager import manager, notification_manager
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/inbox/{conversation_id}")
async def ws_inbox(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    token: str = "",
    db: Session = Depends(get_db),
) -> None:
    """Conecta un cliente al canal WS de una conversación."""
    try:
        payload = decode_token(token, "access")
        token_tenant_id = uuid.UUID(payload["tid"])
    except (ValueError, KeyError, Exception):
        await websocket.close(code=1008)
        return

    with bypass_tenant_filter():
        conv = (
            db.query(WaConversation)
            .filter(WaConversation.id == conversation_id)
            .first()
        )

    if conv is None or conv.tenant_id != token_tenant_id:
        await websocket.close(code=1008)
        return

    await manager.connect(conversation_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(conversation_id, websocket)


@router.websocket("/ws/notifications/{tenant_id}")
async def ws_notifications(
    websocket: WebSocket,
    tenant_id: uuid.UUID,
    token: str = "",
) -> None:
    """Conecta un agente al canal de notificaciones del tenant.

    Recibe eventos ``conversation.waiting_agent`` cuando el bot escala
    una conversación y necesita intervención humana.
    """
    try:
        payload = decode_token(token, "access")
        token_tenant_id = uuid.UUID(payload["tid"])
    except (ValueError, KeyError, Exception):
        await websocket.close(code=1008)
        return

    if token_tenant_id != tenant_id:
        await websocket.close(code=1008)
        return

    await notification_manager.connect(tenant_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect(tenant_id, websocket)
