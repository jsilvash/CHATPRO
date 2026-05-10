"""Gestor de conexiones WebSocket para el inbox en tiempo real.

- ConnectionManager  (Fase 23A): dict {conversation_id → set[WebSocket]}.
  Broadcast de mensajes en conversaciones abiertas.

- NotificationManager (Fase 25D): dict {tenant_id → set[WebSocket]}.
  Broadcast de notificaciones de agentes (conversation.waiting_agent, etc.).

Ambos managers pueden dispararse desde código async O sync usando el event
loop registrado al arranque vía set_main_loop().
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Loop principal de asyncio — registrado en lifespan startup de main.py.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Registra el event loop principal. Llamar en lifespan startup."""
    global _main_loop
    _main_loop = loop


class ConnectionManager:
    """Gestiona conexiones WebSocket agrupadas por conversation_id."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = {}

    async def connect(self, conversation_id: uuid.UUID, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(conversation_id, set()).add(ws)
        logger.debug(
            "ws_manager: conectado conv=%s total=%d",
            conversation_id,
            len(self._connections[conversation_id]),
        )

    def disconnect(self, conversation_id: uuid.UUID, ws: WebSocket) -> None:
        sockets = self._connections.get(conversation_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                del self._connections[conversation_id]
        logger.debug("ws_manager: desconectado conv=%s", conversation_id)

    def client_count(self, conversation_id: uuid.UUID) -> int:
        return len(self._connections.get(conversation_id, set()))

    async def broadcast(self, conversation_id: uuid.UUID, data: dict) -> None:
        """Envía data como JSON a todos los sockets conectados a la conversación."""
        sockets = list(self._connections.get(conversation_id, []))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(conversation_id, ws)

    def broadcast_from_sync(self, conversation_id: uuid.UUID, data: dict) -> None:
        """Schedula el broadcast desde código síncrono (service.py, inbox/api.py).

        Usa run_coroutine_threadsafe con el loop registrado en startup.
        Si el loop no está disponible (entorno de test puro), es no-op silencioso.
        """
        loop = _main_loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast(conversation_id, data), loop
                )
            except Exception as exc:
                logger.warning("ws_manager: broadcast_from_sync falló: %s", exc)


manager = ConnectionManager()


class NotificationManager:
    """Gestiona conexiones WebSocket de notificaciones agrupadas por tenant_id."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = {}

    async def connect(self, tenant_id: uuid.UUID, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(tenant_id, set()).add(ws)
        logger.debug(
            "notif_manager: conectado tenant=%s total=%d",
            tenant_id,
            len(self._connections[tenant_id]),
        )

    def disconnect(self, tenant_id: uuid.UUID, ws: WebSocket) -> None:
        sockets = self._connections.get(tenant_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                del self._connections[tenant_id]
        logger.debug("notif_manager: desconectado tenant=%s", tenant_id)

    def client_count(self, tenant_id: uuid.UUID) -> int:
        return len(self._connections.get(tenant_id, set()))

    async def broadcast(self, tenant_id: uuid.UUID, data: dict) -> None:
        """Envía data como JSON a todos los agentes conectados al canal del tenant."""
        sockets = list(self._connections.get(tenant_id, []))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(tenant_id, ws)

    def broadcast_from_sync(self, tenant_id: uuid.UUID, data: dict) -> None:
        """Schedula el broadcast desde código síncrono.

        Usa run_coroutine_threadsafe con el loop registrado en startup.
        Si el loop no está disponible (test puro), es no-op silencioso.
        """
        loop = _main_loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast(tenant_id, data), loop
                )
            except Exception as exc:
                logger.warning("notif_manager: broadcast_from_sync falló: %s", exc)


notification_manager = NotificationManager()
