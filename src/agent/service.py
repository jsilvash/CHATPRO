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
from src.agent.models import Persona, UsageMetric
from src.agent.prompt_builder import build_system_prompt, is_within_business_hours
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
    cost = float(metadata.get("cost_usd", 0.0))

    stmt = pg_insert(UsageMetric).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        day=today,
        msgs_in=msgs_in,
        msgs_out=msgs_out,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_usage_metrics_tenant_day",
        set_={
            "msgs_in": UsageMetric.msgs_in + stmt.excluded.msgs_in,
            "msgs_out": UsageMetric.msgs_out + stmt.excluded.msgs_out,
            "tokens_in": UsageMetric.tokens_in + stmt.excluded.tokens_in,
            "tokens_out": UsageMetric.tokens_out + stmt.excluded.tokens_out,
            "cost_usd": UsageMetric.cost_usd + stmt.excluded.cost_usd,
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
) -> tuple[str, dict, str]:
    """Loop de Claude + tools.

    Retorna ``(texto_respuesta, metadata_acumulada, motivo_fin)`` donde
    ``motivo_fin`` es uno de: ``end_turn``, ``escalated``, ``max_tool_calls``,
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

    while True:
        iterations += 1
        if iterations > _TOOL_LOOP_HARD_LIMIT:
            logger.warning(
                "agent loop: hard limit alcanzado conv=%s", conversation.id
            )
            return _FALLBACK_ESCALATION_TEXT, aggregated, "hard_limit"

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
            return _FALLBACK_ESCALATION_TEXT, aggregated, "max_cost"

        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "end_turn":
            return _extract_text(response), aggregated, "end_turn"

        if stop_reason != "tool_use":
            logger.warning(
                "agent loop: stop_reason inesperado=%s conv=%s",
                stop_reason,
                conversation.id,
            )
            text = _extract_text(response) or _FALLBACK_ESCALATION_TEXT
            return text, aggregated, "unknown_stop"

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
                return _FALLBACK_ESCALATION_TEXT, aggregated, "max_tool_calls"

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
            return escalation_message, aggregated, "escalated"


def _stringify_for_anthropic(output: dict) -> str:
    """Serializa el output de la tool a string JSON (formato esperado por API)."""
    import json
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


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

        t0 = time.perf_counter()
        response_text, metadata, end_reason = _run_agent_loop(
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

        _persist_outbound(db, conversation, wn, response_text, llm_metadata=metadata)
        _update_usage_metrics(db, conversation.tenant_id, metadata)

    except Exception:
        logger.exception(
            "agent.respond falló inesperadamente conv=%s", conversation.id
        )
