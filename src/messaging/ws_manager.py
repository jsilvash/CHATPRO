"""Gestor de conexiones WebSocket para el inbox en tiempo real (Fase 23A).

ConnectionManager mantiene un dict {conversation_id → set[WebSocket]}.
El broadcast se puede disparar desde código async O desde código sync (service.py,
inbox/api.py) usando el event loop registrado al arranque.
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
