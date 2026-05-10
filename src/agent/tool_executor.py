"""
Ejecutor de tools del agente.

Resuelve `callable_ref` ("module.path:func_name"), llama la función con timeout
y retorna (resultado, status).  El tool `escalar_a_humano` se maneja sin dispatcher.
"""

from __future__ import annotations

import importlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.connectors.base import Connector
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TOOL_TIMEOUT_S: float = 10.0

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool-")


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    callable_ref: str,
    connector: Connector | None = None,
    session: Session | None = None,
    tenant_id: Any = None,
    contact_id: Any = None,
    contact_phone: str | None = None,
    contact_email: str | None = None,
    timeout_s: float = TOOL_TIMEOUT_S,
) -> tuple[Any, str, int]:
    """
    Ejecuta un tool y retorna (result, status, latency_ms).
    status: 'ok' | 'error' | 'timeout'
    """
    if tool_name == "escalar_a_humano":
        return (
            {"escalated": True, "mensaje": "Conversación escalada a un agente humano."},
            "ok",
            0,
        )

    module_path, func_name = callable_ref.rsplit(":", 1)
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        logger.error("No se pudo cargar tool %s (%s): %s", tool_name, callable_ref, exc)
        return {"error": f"Tool no disponible: {exc}"}, "error", 0

    kwargs: dict[str, Any] = {
        **tool_input,
        "connector": connector,
        "session": session,
        "tenant_id": tenant_id,
        "contact_id": contact_id,
        "contact_phone": contact_phone,
        "contact_email": contact_email,
    }

    t0 = time.monotonic()
    future = _pool.submit(func, **kwargs)
    try:
        result = future.result(timeout=timeout_s)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return result, "ok", latency_ms
    except FuturesTimeout:
        future.cancel()
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("Tool %s superó timeout de %.1f s", tool_name, timeout_s)
        return {"error": "timeout"}, "timeout", latency_ms
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("Tool %s falló: %s", tool_name, exc)
        return {"error": str(exc)}, "error", latency_ms
