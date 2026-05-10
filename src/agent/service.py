"""
Servicio de agente Claude — Fase 7.

Implementa el loop de tool_use con:
  - Cap de tool_calls por turno (MAX_TOOL_CALLS)
  - Cap de costo por turno (max_cost_cents)
  - Logging de tool_invocations en DB
  - Anti-hallucination: precios en respuesta deben provenir de tool_results
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import anthropic

from src.agent.llm import DEFAULT_MODEL, compute_cost_cents, get_client
from src.agent.tool_executor import execute_tool
from src.connectors.base import ToolSchema

if TYPE_CHECKING:
    from src.connectors.base import Connector
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5
DEFAULT_MAX_COST_CENTS = 5.0

# Tool built-in que siempre está disponible
_ESCALAR_TOOL = ToolSchema(
    name="escalar_a_humano",
    description=(
        "Escala la conversación a un agente humano cuando el bot no puede resolver "
        "el problema, el cliente lo solicita explícitamente, o la situación requiere "
        "intervención humana."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "description": "Razón por la que se escala a un agente humano.",
            }
        },
        "required": ["motivo"],
    },
    callable_ref="src.agent.service:_escalate_builtin",
)


def _escalate_builtin(**_: Any) -> dict[str, Any]:
    return {"escalated": True, "mensaje": "Conversación escalada a un agente humano."}


@dataclass
class AgentTurnResult:
    response_text: str
    stop_reason: str  # 'ok' | 'max_tool_calls' | 'max_cost' | 'escalated' | 'error'
    tool_calls_count: int
    total_cost_cents: float
    hallucination_flag: bool = False
    tool_results: list[Any] = field(default_factory=list)


def collect_tools(connector: Connector | None) -> list[ToolSchema]:
    """Reúne los tools del conector activo más el built-in escalar_a_humano."""
    tools: list[ToolSchema] = []
    if connector is not None:
        tools.extend(connector.expose_tools())
    tools.append(_ESCALAR_TOOL)
    return tools


def render_system_prompt(persona_name: str = "Asistente") -> str:
    return (
        f"Eres {persona_name}, un asistente virtual de ventas.\n"
        "REGLA FUNDAMENTAL: Cuando menciones el precio o el link de un producto, "
        "DEBES usar los datos exactos devueltos por el tool. "
        "Si no consultaste el tool, di explícitamente que vas a buscar la información."
    )


def run_agent_turn(
    *,
    messages: list[dict[str, Any]],
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID | None = None,
    contact_phone: str | None = None,
    contact_email: str | None = None,
    connector: Connector | None = None,
    session: Session | None = None,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tool_calls: int = MAX_TOOL_CALLS,
    max_cost_cents: float = DEFAULT_MAX_COST_CENTS,
    anthropic_api_key: str | None = None,
) -> AgentTurnResult:
    """
    Ejecuta un turno completo del agente con loop de tool_use.

    Retorna AgentTurnResult con la respuesta final y métricas del turno.
    """
    client = get_client(anthropic_api_key)
    tools_schemas = collect_tools(connector)
    tool_registry: dict[str, ToolSchema] = {t.name: t for t in tools_schemas}

    anthropic_tools = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools_schemas
    ]

    sys_prompt = system or render_system_prompt()
    working_messages = list(messages)

    cost_acc: float = 0.0
    tool_calls_count: int = 0
    all_tool_results: list[Any] = []

    while True:
        # --- guardianes de cap ---
        if tool_calls_count >= max_tool_calls:
            logger.warning(
                "Turno %s: alcanzó MAX_TOOL_CALLS=%d → escalando",
                conversation_id,
                max_tool_calls,
            )
            return AgentTurnResult(
                response_text="He llegado al límite de consultas por turno. Un agente humano te ayudará.",
                stop_reason="max_tool_calls",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                tool_results=all_tool_results,
            )

        if cost_acc >= max_cost_cents:
            logger.warning(
                "Turno %s: costo %.4f¢ ≥ límite %.4f¢ → escalando",
                conversation_id,
                cost_acc,
                max_cost_cents,
            )
            return AgentTurnResult(
                response_text="La consulta superó el límite de procesamiento. Un agente humano te ayudará.",
                stop_reason="max_cost",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                tool_results=all_tool_results,
            )

        # --- llamada LLM ---
        resp = client.messages.create(
            model=model,
            system=sys_prompt,
            messages=working_messages,
            tools=anthropic_tools,
            max_tokens=1024,
        )
        call_cost = compute_cost_cents(resp.usage, model)
        cost_acc += call_cost
        logger.debug(
            "LLM call: stop_reason=%s tokens=%s cost=%.4f¢ total=%.4f¢",
            resp.stop_reason,
            resp.usage,
            call_cost,
            cost_acc,
        )

        # --- fin de turno normal ---
        if resp.stop_reason == "end_turn":
            response_text = _extract_text(resp.content)
            hallucination = _check_anti_hallucination(response_text, all_tool_results)
            if hallucination:
                logger.warning(
                    "Turno %s: posible precio alucinado en respuesta → flag activado",
                    conversation_id,
                )
            return AgentTurnResult(
                response_text=response_text,
                stop_reason="ok",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                hallucination_flag=hallucination,
                tool_results=all_tool_results,
            )

        # --- tool_use ---
        if resp.stop_reason == "tool_use":
            tool_result_blocks: list[dict[str, Any]] = []

            for block in resp.content:
                if block.type != "tool_use":
                    continue

                tool_calls_count += 1
                schema = tool_registry.get(block.name)
                callable_ref = schema.callable_ref if schema else f"unknown:{block.name}"

                result, status, latency_ms = execute_tool(
                    tool_name=block.name,
                    tool_input=dict(block.input),
                    callable_ref=callable_ref,
                    connector=connector,
                    session=session,
                    tenant_id=tenant_id,
                    contact_id=contact_id,
                    contact_phone=contact_phone,
                    contact_email=contact_email,
                )

                _log_invocation(
                    session=session,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    tool_name=block.name,
                    tool_input=dict(block.input),
                    result=result,
                    status=status,
                    latency_ms=latency_ms,
                )
                all_tool_results.append(result)

                result_content = json.dumps(result, ensure_ascii=False, default=str)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_content,
                    }
                )

                # Detección temprana de escalación explícita
                if block.name == "escalar_a_humano":
                    working_messages.append({"role": "assistant", "content": resp.content})
                    working_messages.append(
                        {"role": "user", "content": tool_result_blocks}
                    )
                    return AgentTurnResult(
                        response_text="Un agente humano continuará la conversación.",
                        stop_reason="escalated",
                        tool_calls_count=tool_calls_count,
                        total_cost_cents=cost_acc,
                        tool_results=all_tool_results,
                    )

            working_messages.append({"role": "assistant", "content": resp.content})
            working_messages.append({"role": "user", "content": tool_result_blocks})
            continue

        # stop_reason inesperado
        logger.error("Stop reason inesperado: %s", resp.stop_reason)
        return AgentTurnResult(
            response_text="Ocurrió un error inesperado. Intenta de nuevo.",
            stop_reason="error",
            tool_calls_count=tool_calls_count,
            total_cost_cents=cost_acc,
            tool_results=all_tool_results,
        )


# ------------------------------------------------------------------ helpers

def _extract_text(content: list[Any]) -> str:
    parts = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _log_invocation(
    *,
    session: Any,
    tenant_id: Any,
    conversation_id: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    result: Any,
    status: str,
    latency_ms: int,
) -> None:
    if session is None:
        return
    try:
        from src.agent.models import ToolInvocation

        inv = ToolInvocation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            input_json=tool_input,
            output_json=result if isinstance(result, (dict, list)) else {"value": result},
            status=status,
            latency_ms=latency_ms,
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(inv)
        session.flush()
    except Exception as exc:
        logger.warning("No se pudo persistir tool_invocation: %s", exc)


# ------------------------------------------------------------------ anti-hallucination

_PRICE_RE = re.compile(
    r"(?:AR\$|\$|USD|ARS|€|precio|cuesta|vale|costo)[\s:]*"
    r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


def _collect_numbers_from_json(obj: Any, out: set[str]) -> None:
    """Recorre recursivamente un objeto JSON y extrae valores numéricos como strings."""
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers_from_json(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_numbers_from_json(item, out)
    elif isinstance(obj, (int, float)):
        out.add(str(obj))
    elif isinstance(obj, str):
        # Incluir strings que parecen números
        normalized = obj.replace(",", ".")
        if re.match(r"^\d+(\.\d+)?$", normalized):
            out.add(normalized)


def _normalize_price(raw: str) -> str:
    """Normaliza '1.500,99' o '1,500.99' a '1500.99'."""
    s = raw.strip()
    # Detectar formato europeo: dígitos separados por punto y decimal por coma
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d{1,2})?$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return s


def _check_anti_hallucination(response_text: str, tool_results: list[Any]) -> bool:
    """
    Retorna True (flag activado) si la respuesta contiene un precio que NO
    aparece en ningún tool_result del turno.
    """
    if not tool_results:
        # Sin tool results: si hay precios en la respuesta → flag
        return bool(_PRICE_RE.search(response_text))

    grounded: set[str] = set()
    for result in tool_results:
        _collect_numbers_from_json(result, grounded)

    for match in _PRICE_RE.finditer(response_text):
        raw = match.group(1)
        normalized = _normalize_price(raw)
        # Tolerancia: buscar el número sin decimales también
        base = normalized.split(".")[0]
        if normalized not in grounded and base not in grounded:
            logger.warning(
                "Anti-hallucination: precio '%s' (norm: '%s') no encontrado en tool_results",
                raw,
                normalized,
            )
            return True

    return False
