"""Servicio del agente: responde a un mensaje inbound usando Claude.

Flujo principal:
1. Carga la Persona desde el WaNumber de la conversación.
2. Comprueba horario comercial; si fuera → envía out_of_hours_message.
3. Construye system prompt + historia de turnos (N=10) + tools del tenant.
4. Loop tool_use: invoca Claude, ejecuta tools que pida, repite hasta end_turn
   o hasta superar caps (max 5 tool calls / max costo / timeout).
5. Persiste el outbound en WaMessage (con llm_metadata).
6. Acumula métricas en usage_metrics y registra cada tool en tool_invocations.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.agent.facts_extractor import load_top_facts
from src.agent.llm import call_claude_messages
from src.agent.models import Persona
from src.agent.prompt_builder import build_system_prompt, is_within_business_hours
from src.agent.rate_limiter import is_rate_limited
from src.billing.models import UsageMetric
from src.billing.quota import check_quota
from src.config import get_settings
from src.agent.tool_runner import (
    DEFAULT_TOOL_TIMEOUT_S,
    collect_tools_for_conversation,
    execute_tool,
    log_tool_invocation,
)
from src.messaging import dispatcher
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaMessage, WaNumber

logger = logging.getLogger(__name__)

_HISTORY_TURNS = 10
_MAX_TOOL_CALLS_PER_TURN = 5
_MAX_COST_PER_TURN_USD = 0.05  # 5¢ default — futuro: configurable por número
_TOOL_LOOP_HARD_LIMIT = 8  # safety net contra loops infinitos del modelo
_FALLBACK_ESCALATION_TEXT = (
    "Disculpá, no logro resolverlo desde acá. Te derivo a un humano del equipo."
)
_TOOLS_RULES = (
    "\nReglas estrictas para usar herramientas:\n"
    "- Cuando hables de un producto debés incluir el precio y link EXACTOS "
    "que devolvió la herramienta. No inventes datos.\n"
    "- Si no usaste la herramienta de búsqueda, decí explícitamente que vas "
    "a consultar antes de dar precios o stock.\n"
    "- Si el cliente pide hablar con un humano, o detectás una queja seria, "
    "llamá la herramienta escalar_a_humano.\n"
)


def _load_persona(db: Session, conversation: WaConversation) -> Persona | None:
    """Carga la Persona asociada al WaNumber de la conversación, o None."""
    with bypass_tenant_filter():
        wn = db.query(WaNumber).filter(WaNumber.id == conversation.wa_number_id).first()
    if wn is None or wn.persona_id is None:
        return None
    with bypass_tenant_filter():
        persona = db.query(Persona).filter(Persona.id == wn.persona_id).first()
    return persona


def _build_messages(
    db: Session, conversation: WaConversation, inbound_msg: WaMessage
) -> list[dict]:
    """Construye la lista de mensajes para la API Anthropic."""
    rows = (
        db.query(WaMessage)
        .filter(
            WaMessage.wa_conversation_id == conversation.id,
            WaMessage.tenant_id == conversation.tenant_id,
            WaMessage.id != inbound_msg.id,
            WaMessage.text != "",
        )
        .order_by(WaMessage.created_at.desc())
        .limit(_HISTORY_TURNS)
        .all()
    )
    rows = list(reversed(rows))

    messages: list[dict] = []

    ai_summary = getattr(conversation, "ai_summary", None)
    if ai_summary:
        messages.append({"role": "user", "content": "[Contexto previo de la conversación]"})
        messages.append({"role": "assistant", "content": ai_summary})

    for row in rows:
        role = "user" if row.direction == "in" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + row.text
        else:
            messages.append({"role": role, "content": row.text})

    inbound_text = inbound_msg.text or ""
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + inbound_text
    else:
        messages.append({"role": "user", "content": inbound_text})

    return messages


def _maybe_trigger_summary(db: Session, conversation: WaConversation) -> None:
    """Genera resumen de la conversación si turn_count supera la ventana."""
    turn_count = getattr(conversation, "turn_count", 0) or 0
    if turn_count > _HISTORY_TURNS:
        from src.agent.summarizer import summarize_conversation
        summarize_conversation(db, conversation)


def _persist_outbound(
    db: Session,
    conversation: WaConversation,
    wn: WaNumber,
    text: str,
    llm_metadata: dict | None = None,
    hallucination_flag: bool = False,
) -> WaMessage:
    """Envía el texto via dispatcher y persiste el WaMessage outbound."""
    result = dispatcher.send_text(
        db,
        conversation.tenant_id,
        conversation.wa_contact_phone,
        text,
        wa_number=wn,
        conversation=conversation,
    )

    msg = WaMessage(
        tenant_id=conversation.tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conversation.id,
        direction="out",
        text=text,
        wa_message_id=result.wa_message_id,
        ack="sent" if result.success else "",
        error="" if result.success else result.error,
        raw_payload=result.raw_response or {},
        llm_metadata=llm_metadata or {},
        hallucination_flag=hallucination_flag,
    )
    if result.success:
        msg.sent_at = datetime.now(UTC)
    else:
        msg.ack = "failed"
        msg.failed_at = datetime.now(UTC)
        logger.warning(
            "agent: fallo envío outbound conv=%s error=%s",
            conversation.id,
            result.error,
        )

    db.add(msg)
    db.commit()

    # Broadcast WS a clientes conectados al inbox de esta conversación (Fase 23A).
    try:
        from src.messaging.ws_manager import manager as ws_manager
        ws_manager.broadcast_from_sync(
            conversation.id,
            {
                "event": "message",
                "message_id": str(msg.id),
                "direction": msg.direction,
                "text": msg.text,
                "source": "bot",
                "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            },
        )
    except Exception:
        pass

    return msg


def _update_usage_metrics(
    db: Session,
    tenant_id: uuid.UUID,
    metadata: dict,
    *,
    msgs_in: int = 1,
    msgs_out: int = 1,
) -> None:
    """Upsert atómico en usage_metrics (acumula por día)."""
    today = date.today()
    tokens_in = metadata.get("input_tokens", 0)
    tokens_out = metadata.get("output_tokens", 0)
    cost_usd = float(metadata.get("cost_usd", 0.0))
    cost_cents = round(cost_usd * 100, 4)

    stmt = pg_insert(UsageMetric).values(
        tenant_id=tenant_id,
        metric_date=today,
        messages_in=msgs_in,
        messages_out=msgs_out,
        llm_input_tokens=tokens_in,
        llm_output_tokens=tokens_out,
        llm_cost_cents=cost_cents,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "metric_date"],
        set_={
            "messages_in": UsageMetric.messages_in + stmt.excluded.messages_in,
            "messages_out": UsageMetric.messages_out + stmt.excluded.messages_out,
            "llm_input_tokens": UsageMetric.llm_input_tokens + stmt.excluded.llm_input_tokens,
            "llm_output_tokens": UsageMetric.llm_output_tokens + stmt.excluded.llm_output_tokens,
            "llm_cost_cents": UsageMetric.llm_cost_cents + stmt.excluded.llm_cost_cents,
        },
    )
    db.execute(stmt)
    db.commit()


def _content_block_to_dict(block) -> dict:
    """Convierte un ContentBlock del SDK a dict serializable para mensajes."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    return {"type": btype or "unknown"}


def _extract_text(response) -> str:
    return "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )


def _accumulate_metadata(acc: dict, meta: dict) -> None:
    """Suma tokens y costo de cada llamada a Claude en el turno."""
    acc["model"] = meta.get("model", acc.get("model", ""))
    acc["input_tokens"] = acc.get("input_tokens", 0) + meta.get("input_tokens", 0)
    acc["output_tokens"] = acc.get("output_tokens", 0) + meta.get("output_tokens", 0)
    acc["cache_read_input_tokens"] = (
        acc.get("cache_read_input_tokens", 0)
        + meta.get("cache_read_input_tokens", 0)
    )
    acc["cost_usd"] = round(
        acc.get("cost_usd", 0.0) + meta.get("cost_usd", 0.0), 8
    )


def _run_agent_loop(
    db: Session,
    conversation: WaConversation,
    inbound_msg: WaMessage,
    persona: Persona,
    system_prompt: str,
    initial_messages: list[dict],
) -> tuple[str, dict, str, bool]:
    """Loop de Claude + tools.

    Retorna ``(texto_respuesta, metadata_acumulada, motivo_fin, hallucination_flag)``
    donde ``motivo_fin`` es uno de: ``end_turn``, ``escalated``, ``max_tool_calls``,
    ``max_cost``, ``unknown_stop``, ``hard_limit``.
    """
    tools_schemas, tools_index = collect_tools_for_conversation(db, conversation)
    use_tools = bool(tools_schemas)
    final_system = system_prompt + (_TOOLS_RULES if use_tools else "")

    messages = list(initial_messages)
    aggregated: dict = {
        "model": persona.model_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost_usd": 0.0,
    }

    tool_calls = 0
    iterations = 0
    all_tool_outputs: list[Any] = []

    while True:
        iterations += 1
        if iterations > _TOOL_LOOP_HARD_LIMIT:
            logger.warning(
                "agent loop: hard limit alcanzado conv=%s", conversation.id
            )
            return _FALLBACK_ESCALATION_TEXT, aggregated, "hard_limit", False

        response, meta = call_claude_messages(
            final_system,
            messages,
            model_id=persona.model_id,
            tools=tools_schemas if use_tools else None,
        )
        _accumulate_metadata(aggregated, meta)

        if aggregated["cost_usd"] >= _MAX_COST_PER_TURN_USD:
            logger.warning(
                "agent loop: cap de costo alcanzado conv=%s cost=%.6f",
                conversation.id,
                aggregated["cost_usd"],
            )
            return _FALLBACK_ESCALATION_TEXT, aggregated, "max_cost", False

        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "end_turn":
            text = _extract_text(response)
            hallucination = _check_anti_hallucination(text, all_tool_outputs)
            if hallucination:
                logger.warning(
                    "anti-hallucination: precio no grounded | conversation_id=%s | "
                    "respuesta=%r | tool_outputs=%r",
                    conversation.id,
                    text[:300],
                    all_tool_outputs,
                )
            return text, aggregated, "end_turn", hallucination

        if stop_reason != "tool_use":
            logger.warning(
                "agent loop: stop_reason inesperado=%s conv=%s",
                stop_reason,
                conversation.id,
            )
            text = _extract_text(response) or _FALLBACK_ESCALATION_TEXT
            return text, aggregated, "unknown_stop", False

        # Procesar bloques tool_use.
        tool_use_blocks = [
            b for b in response.content if getattr(b, "type", None) == "tool_use"
        ]

        # Append assistant turn (con todos los content blocks tal cual).
        messages.append(
            {
                "role": "assistant",
                "content": [_content_block_to_dict(b) for b in response.content],
            }
        )

        tool_results: list[dict] = []
        escalation_message: str | None = None

        for block in tool_use_blocks:
            tool_calls += 1
            if tool_calls > _MAX_TOOL_CALLS_PER_TURN:
                logger.warning(
                    "agent loop: cap tool_calls alcanzado conv=%s",
                    conversation.id,
                )
                return _FALLBACK_ESCALATION_TEXT, aggregated, "max_tool_calls", False

            result = execute_tool(
                tool_name=block.name,
                tool_input=block.input,
                tools_index=tools_index,
                db=db,
                conversation=conversation,
                timeout_s=DEFAULT_TOOL_TIMEOUT_S,
            )
            log_tool_invocation(
                db,
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                inbound_message_id=inbound_msg.id,
                tool_name=block.name,
                tool_use_id=block.id,
                tool_input=block.input or {},
                result=result,
            )

            if result.output:
                all_tool_outputs.append(result.output)

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _stringify_for_anthropic(result.output),
                    "is_error": result.status != "success",
                }
            )

            if (
                block.name == "escalar_a_humano"
                and result.status == "success"
                and isinstance(result.output, dict)
            ):
                escalation_message = result.output.get("mensaje") or _FALLBACK_ESCALATION_TEXT

        messages.append({"role": "user", "content": tool_results})

        if escalation_message is not None:
            return escalation_message, aggregated, "escalated", False


def _stringify_for_anthropic(output: dict) -> str:
    """Serializa el output de la tool a string JSON (formato esperado por API)."""
    import json
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


_AUTO_ESCALATE_REASONS = frozenset({"max_tool_calls", "max_cost", "hard_limit"})


def _auto_escalate_if_needed(
    db: Session, conversation: WaConversation, end_reason: str
) -> None:
    """Si el bot no pudo resolver, transiciona a waiting_agent y crea HandoffEvent."""
    if end_reason not in _AUTO_ESCALATE_REASONS:
        return
    if conversation.status != "bot":
        return

    from datetime import UTC, datetime

    from src.inbox.models import HandoffEvent

    conversation.status = "waiting_agent"
    db.add(conversation)

    evento = HandoffEvent(
        tenant_id=conversation.tenant_id,
        wa_conversation_id=conversation.id,
        motivo=f"auto_escalado:{end_reason}",
        opened_at=datetime.now(UTC),
    )
    db.add(evento)
    db.flush()


def respond(db: Session, conversation: WaConversation, inbound_msg: WaMessage) -> None:
    """Genera y persiste la respuesta del bot para un mensaje inbound.

    Devuelve silenciosamente si la conversación no tiene persona asignada o si
    ya tiene un humano asignado (status != "bot").
    Los errores se logean pero no se propagan para no romper el webhook.
    """
    try:
        if conversation.status != "bot":
            logger.debug(
                "agent.respond: conversación en status=%s, bot mudo conv=%s",
                conversation.status,
                conversation.id,
            )
            return

        settings = get_settings()
        if is_rate_limited(
            conversation.tenant_id,
            conversation.wa_contact_phone,
            limit=settings.rate_limit_messages,
            window_s=settings.rate_limit_window_seconds,
            redis_url=settings.redis_url,
        ):
            return

        persona = _load_persona(db, conversation)
        if persona is None:
            return

        with bypass_tenant_filter():
            wn = db.query(WaNumber).filter(WaNumber.id == conversation.wa_number_id).first()
        if wn is None:
            return

        if not is_within_business_hours(persona):
            if persona.out_of_hours_message:
                _persist_outbound(db, conversation, wn, persona.out_of_hours_message)
            return

        _maybe_trigger_summary(db, conversation)

        contact_facts = load_top_facts(
            db, conversation.tenant_id, conversation.wa_contact_phone
        )
        system_prompt = build_system_prompt(persona, contact_facts=contact_facts)
        messages = _build_messages(db, conversation, inbound_msg)

        # Verificar cuota de costo LLM antes de la llamada (1 cent mínimo estimado).
        try:
            check_quota(conversation.tenant_id, "llm_cost_cents", 1.0, db)
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException) and exc.status_code == 429:
                raise
            logger.warning("check_quota LLM falló inesperadamente: %s", exc)

        t0 = time.perf_counter()
        response_text, metadata, end_reason, hallucination_flag = _run_agent_loop(
            db, conversation, inbound_msg, persona, system_prompt, messages
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if latency_ms > 8000:
            logger.warning(
                "agent respuesta lenta: %dms conv=%s", latency_ms, conversation.id
            )

        metadata["latency_ms"] = latency_ms
        metadata["end_reason"] = end_reason

        if not response_text:
            response_text = _FALLBACK_ESCALATION_TEXT
            end_reason = end_reason or "empty_response"
            metadata["end_reason"] = end_reason

        # Auto-transición: si el bot no pudo resolver (límite de tools/costo/loop),
        # derivar a waiting_agent para que un humano retome.
        _auto_escalate_if_needed(db, conversation, end_reason)

        _persist_outbound(
            db, conversation, wn, response_text,
            llm_metadata=metadata,
            hallucination_flag=hallucination_flag,
        )
        _update_usage_metrics(db, conversation.tenant_id, metadata)

    except Exception:
        logger.exception(
            "agent.respond falló inesperadamente conv=%s", conversation.id
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fase 7: interfaz simplificada para tests y uso directo sin ORM completo
# ─────────────────────────────────────────────────────────────────────────────

import json
import re
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.agent.llm import _compute_cost, call_claude_messages
from src.agent.tool_executor import execute_tool as _execute_tool_fase7
from src.connectors.base import ToolSchema


@dataclass
class AgentTurnResult:
    """Resultado de un turno del agente con métricas."""

    response_text: str
    stop_reason: str  # 'ok'|'max_tool_calls'|'max_cost'|'escalated'|'error'
    tool_calls_count: int
    total_cost_cents: float
    hallucination_flag: bool = False
    tool_results: list[Any] = field(default_factory=list)


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


def collect_tools(connector: Any | None) -> list[ToolSchema]:
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
    conversation_id: _uuid.UUID,
    tenant_id: _uuid.UUID,
    contact_id: _uuid.UUID | None = None,
    contact_phone: str | None = None,
    contact_email: str | None = None,
    connector: Any | None = None,
    session: Any | None = None,
    system: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tool_calls: int = _MAX_TOOL_CALLS_PER_TURN,
    max_cost_cents: float = _MAX_COST_PER_TURN_USD * 100,
    anthropic_api_key: str | None = None,
) -> AgentTurnResult:
    """
    Ejecuta un turno completo con loop de tool_use.

    Interfaz directa (sin ORM completo de WaConversation) para tests y uso externo.
    """
    from src.agent.tool_executor import execute_tool

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
        if tool_calls_count >= max_tool_calls:
            logger.warning("run_agent_turn: MAX_TOOL_CALLS=%d → escalando", max_tool_calls)
            return AgentTurnResult(
                response_text="He llegado al límite de consultas. Un agente humano te ayudará.",
                stop_reason="max_tool_calls",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                tool_results=all_tool_results,
            )

        if cost_acc >= max_cost_cents:
            logger.warning("run_agent_turn: costo %.4f¢ ≥ límite → escalando", cost_acc)
            return AgentTurnResult(
                response_text="La consulta superó el límite de procesamiento. Un agente humano te ayudará.",
                stop_reason="max_cost",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                tool_results=all_tool_results,
            )

        resp, metadata = call_claude_messages(
            sys_prompt,
            working_messages,
            model_id=model,
            max_tokens=1024,
            tools=anthropic_tools if anthropic_tools else None,
        )
        call_cost_cents = metadata.get("cost_usd", 0.0) * 100
        cost_acc += call_cost_cents

        if resp.stop_reason == "end_turn":
            response_text = "".join(
                b.text for b in resp.content if b.type == "text"
            )
            hallucination = _check_anti_hallucination(response_text, all_tool_results)
            if hallucination:
                logger.warning(
                    "anti-hallucination: precio no grounded | conversation_id=%s | "
                    "respuesta=%r | tool_results=%r",
                    conversation_id,
                    response_text[:300],
                    all_tool_results,
                )
            return AgentTurnResult(
                response_text=response_text,
                stop_reason="ok",
                tool_calls_count=tool_calls_count,
                total_cost_cents=cost_acc,
                hallucination_flag=hallucination,
                tool_results=all_tool_results,
            )

        if resp.stop_reason == "tool_use":
            tool_result_blocks: list[dict[str, Any]] = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                tool_calls_count += 1
                schema = tool_registry.get(block.name)
                callable_ref = schema.callable_ref if schema else f"unknown:{block.name}"

                result, status, latency_ms = _execute_tool_fase7(
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
                _log_inv(session, tenant_id, conversation_id, block.name,
                         dict(block.input), result, status, latency_ms)
                all_tool_results.append(result)

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

                if block.name == "escalar_a_humano":
                    working_messages.append({"role": "assistant", "content": resp.content})
                    working_messages.append({"role": "user", "content": tool_result_blocks})
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

        return AgentTurnResult(
            response_text="Ocurrió un error inesperado.",
            stop_reason="error",
            tool_calls_count=tool_calls_count,
            total_cost_cents=cost_acc,
            tool_results=all_tool_results,
        )


def _log_inv(session, tenant_id, conversation_id, tool_name, tool_input,
             result, status, latency_ms):
    if session is None:
        return
    try:
        from src.agent.models import ToolInvocation
        from datetime import datetime, timezone
        inv = ToolInvocation(
            id=_uuid.uuid4(),
            tenant_id=tenant_id,
            wa_conversation_id=conversation_id,
            tool_name=tool_name,
            tool_use_id="",
            input=tool_input,
            output=result if isinstance(result, (dict, list)) else {"value": str(result)},
            status=status,
            latency_ms=latency_ms,
        )
        session.add(inv)
        session.flush()
    except Exception as exc:
        logger.warning("No se pudo persistir tool_invocation: %s", exc)


# ─── Anti-hallucination ───────────────────────────────────────────────────────

_PRICE_RE = re.compile(
    r"(?:AR\$|\$|USD|ARS|€|precio|cuesta|vale|costo)[\s:]*"
    r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


def _collect_numbers(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_numbers(item, out)
    elif isinstance(obj, (int, float)):
        # Normalizar: 45.0 → "45", 45.99 → "45.99", 45.90 → "45.9"
        out.add(str(int(obj)) if obj == int(obj) else str(obj))
        out.add(f"{obj:.2f}")  # también "45.00" para comparar
    elif isinstance(obj, str):
        n = obj.replace(",", ".")
        if re.match(r"^\d+(\.\d+)?$", n):
            out.add(n)
            # Agregar forma sin decimales trailing zeros
            if "." in n:
                out.add(n.rstrip("0").rstrip("."))


def _normalize_price(raw: str) -> str:
    s = raw.strip()
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d{1,2})?$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return s


def _check_anti_hallucination(response_text: str, tool_results: list[Any]) -> bool:
    """
    Retorna True si hay un precio en la respuesta no presente en tool_results.
    """
    if not tool_results:
        return bool(_PRICE_RE.search(response_text))

    grounded: set[str] = set()
    for r in tool_results:
        _collect_numbers(r, grounded)

    for match in _PRICE_RE.finditer(response_text):
        normalized = _normalize_price(match.group(1))
        if normalized not in grounded and normalized.split(".")[0] not in grounded:
            logger.warning(
                "Anti-hallucination: '%s' no en tool_results", match.group(1)
            )
            return True
    return False
