"""Despachador de tools del agente.

Responsabilidades:
- Recolectar los tools disponibles para una conversación (built-in + conectores).
- Resolver ``callable_ref`` de cada ToolSchema a una función Python real.
- Ejecutar la tool con timeout y capturar latencia / errores.

Las tools de conectores se descubren via ``Connector.expose_tools()``.
Las tools built-in viven en este módulo (escalar_a_humano).
"""

from __future__ import annotations

import concurrent.futures
import importlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, ConnectorDef
from src.connectors.registry import get_connector_class
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_S = 10.0


# ── Tools built-in (no pertenecen a ningún conector) ────────────────────────


def escalar_a_humano(
    motivo: str = "",
    *,
    db: Session,
    conversation: WaConversation,
    **_kwargs,
) -> dict:
    """Marca la conversación como ``waiting_agent`` y registra el HandoffEvent."""
    from datetime import UTC, datetime

    from src.inbox.models import HandoffEvent

    motivo_efectivo = motivo or "solicitado_por_cliente"
    conversation.status = "waiting_agent"
    db.add(conversation)

    evento = HandoffEvent(
        tenant_id=conversation.tenant_id,
        wa_conversation_id=conversation.id,
        motivo=motivo_efectivo,
        opened_at=datetime.now(UTC),
    )
    db.add(evento)
    db.flush()
    return {
        "ok": True,
        "mensaje": (
            "Te derivo a un agente humano. En breve te contactamos para ayudarte."
        ),
        "motivo": motivo_efectivo,
    }


_BUILTIN_TOOLS: dict[str, dict] = {
    "escalar_a_humano": {
        "schema": {
            "name": "escalar_a_humano",
            "description": (
                "Deriva la conversación a un agente humano. Úsalo cuando el cliente "
                "lo pida explícitamente, cuando detectes una queja seria, o cuando "
                "no puedas resolver el caso con las herramientas disponibles."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "motivo": {
                        "type": "string",
                        "description": "Motivo del escalamiento (interno).",
                    },
                },
            },
        },
        "callable": escalar_a_humano,
    },
}


# ── Recolección de tools por conversación ───────────────────────────────────


@dataclass
class ResolvedTool:
    """Tool ya resuelto y listo para invocar."""

    schema: dict
    callable_func: Callable
    extra_kwargs: dict
    is_builtin: bool


def collect_tools_for_conversation(
    db: Session,
    conversation: WaConversation,
) -> tuple[list[dict], dict[str, ResolvedTool]]:
    """Devuelve (tools_para_anthropic, índice nombre→ResolvedTool).

    Recorre los ``ConnectorConfig`` activos del tenant, instancia cada conector,
    pide su ``expose_tools()`` y agrega las built-in. Si un conector no se puede
    cargar, se loggea pero no se rompe — el agente sigue con los tools que sí
    se resolvieron.
    """
    schemas: list[dict] = []
    index: dict[str, ResolvedTool] = {}

    # Built-in tools — siempre disponibles.
    for name, defn in _BUILTIN_TOOLS.items():
        schemas.append(defn["schema"])
        index[name] = ResolvedTool(
            schema=defn["schema"],
            callable_func=defn["callable"],
            extra_kwargs={},
            is_builtin=True,
        )

    # Tools de conectores activos del tenant.
    with bypass_tenant_filter():
        configs = (
            db.query(ConnectorConfig, ConnectorDef)
            .join(ConnectorDef, ConnectorConfig.connector_def_id == ConnectorDef.id)
            .filter(
                ConnectorConfig.tenant_id == conversation.tenant_id,
                ConnectorConfig.status == "connected",
                ConnectorDef.enabled.is_(True),
            )
            .all()
        )

    for config, defn in configs:
        try:
            connector_cls = get_connector_class(defn.name)
        except KeyError:
            logger.warning("Conector '%s' no registrado — se omite", defn.name)
            continue

        try:
            connector = connector_cls(
                tenant_id=conversation.tenant_id,
                config_id=config.id,
                db=db,
            )
            tool_schemas = connector.expose_tools()
        except Exception:
            logger.exception(
                "fallo expose_tools en conector %s config=%s", defn.name, config.id
            )
            continue

        for ts in tool_schemas:
            try:
                func = _resolve_callable(ts.callable_ref)
            except Exception:
                logger.exception(
                    "no se pudo resolver callable_ref=%s tool=%s",
                    ts.callable_ref,
                    ts.name,
                )
                continue

            anthropic_schema = {
                "name": ts.name,
                "description": ts.description,
                "input_schema": ts.input_schema,
            }
            schemas.append(anthropic_schema)
            index[ts.name] = ResolvedTool(
                schema=anthropic_schema,
                callable_func=func,
                extra_kwargs={
                    "tenant_id": conversation.tenant_id,
                    "config_id": config.id,
                },
                is_builtin=False,
            )

    return schemas, index


def _resolve_callable(callable_ref: str) -> Callable:
    """Resuelve "modulo.submodulo:funcion" a un callable importable."""
    if ":" in callable_ref:
        module_path, func_name = callable_ref.split(":", 1)
    else:
        module_path, _, func_name = callable_ref.rpartition(".")
    if not callable_ref.startswith("src.") and not module_path.startswith("src."):
        module_path = f"src.{module_path}"
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


# ── Ejecución con timeout ───────────────────────────────────────────────────


@dataclass
class ToolExecutionResult:
    output: dict
    status: str  # success | error | timeout | unknown_tool
    error: str
    latency_ms: int


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    tools_index: dict[str, ResolvedTool],
    db: Session,
    conversation: WaConversation,
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
) -> ToolExecutionResult:
    """Ejecuta una tool con timeout y retorna salida + metadatos.

    Si la tool no existe, devuelve ``status=unknown_tool``.
    Si excede ``timeout_s``, devuelve ``status=timeout``.
    Cualquier otra excepción → ``status=error`` con mensaje truncado.
    """
    t0 = time.perf_counter()

    resolved = tools_index.get(tool_name)
    if resolved is None:
        return ToolExecutionResult(
            output={"error": f"tool '{tool_name}' no disponible"},
            status="unknown_tool",
            error=f"tool '{tool_name}' no registrada",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    kwargs = dict(resolved.extra_kwargs)
    kwargs["db"] = db
    if resolved.is_builtin:
        kwargs["conversation"] = conversation

    safe_input = tool_input if isinstance(tool_input, dict) else {}

    def _invoke() -> dict:
        return resolved.callable_func(**safe_input, **kwargs)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_invoke)
            output = future.result(timeout=timeout_s)
        if not isinstance(output, dict):
            output = {"resultado": output}
        return ToolExecutionResult(
            output=output,
            status="success",
            error="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
    except concurrent.futures.TimeoutError:
        logger.warning("tool '%s' excedió timeout (%.1fs)", tool_name, timeout_s)
        return ToolExecutionResult(
            output={"error": "timeout"},
            status="timeout",
            error=f"timeout {timeout_s}s",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:
        logger.exception("tool '%s' falló", tool_name)
        return ToolExecutionResult(
            output={"error": "tool_error"},
            status="error",
            error=str(exc)[:500],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


# ── Persistencia del registro ───────────────────────────────────────────────


def log_tool_invocation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    inbound_message_id: uuid.UUID | None,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict,
    result: ToolExecutionResult,
) -> None:
    """Persiste un row en ``tool_invocations``."""
    from src.agent.models import ToolInvocation

    inv = ToolInvocation(
        tenant_id=tenant_id,
        wa_conversation_id=conversation_id,
        wa_message_id=inbound_message_id,
        tool_name=tool_name,
        tool_use_id=tool_use_id or "",
        input=tool_input or {},
        output=result.output or {},
        status=result.status,
        error=result.error or "",
        latency_ms=result.latency_ms,
    )
    db.add(inv)
    db.flush()
