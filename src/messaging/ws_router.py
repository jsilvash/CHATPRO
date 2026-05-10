"""WebSocket endpoint para inbox en tiempo real (Fase 23A).

Ruta:  /ws/inbox/{conversation_id}?token=<jwt_access>

Auth:
- Token JWT (type=access) en query param ?token=.
- Si el token es inválido o falta → cerrar con código 1008 (Policy Violation).
- Si el tenant_id del token no coincide con el tenant de la conversación → 1008.

Ciclo de vida:
- On connect: registrar en ConnectionManager.
- On disconnect: limpiar del set (WebSocketDisconnect).
- Mensajes del cliente: ignorados (el canal es solo de server→client).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from src.auth.tokens import decode_token
from src.db.session import get_db
from src.messaging.ws_manager import manager
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
    """Conecta un cliente al canal WS de una conversación.

    El servidor hace push de cada mensaje outbound (bot o agente) a todos
    los clientes conectados a esa conversation_id.
    """
    # ── Auth: validar JWT ────────────────────────────────────────────────────
    try:
        payload = decode_token(token, "access")
        token_tenant_id = uuid.UUID(payload["tid"])
    except (ValueError, KeyError, Exception):
        await websocket.close(code=1008)
        return

    # ── Verificar que la conversación pertenece al tenant del token ──────────
    with bypass_tenant_filter():
        conv = (
            db.query(WaConversation)
            .filter(WaConversation.id == conversation_id)
            .first()
        )

    if conv is None or conv.tenant_id != token_tenant_id:
        await websocket.close(code=1008)
        return

    # ── Aceptar y registrar ──────────────────────────────────────────────────
    await manager.connect(conversation_id, websocket)
    try:
        while True:
            # Canal server→client: ignoramos mensajes entrantes del cliente.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(conversation_id, websocket)
