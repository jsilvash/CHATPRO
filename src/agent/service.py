"""Servicio del agente: responde a un mensaje inbound usando Claude.

Flujo principal:
1. Carga la Persona desde el WaNumber de la conversación.
2. Comprueba horario comercial; si fuera → envía out_of_hours_message.
3. Construye system prompt + historia de turnos (N=10).
4. Llama a Claude vía llm.call_claude.
5. Persiste el outbound en WaMessage (con llm_metadata).
6. Acumula métricas en usage_metrics.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.agent.llm import call_claude
from src.agent.models import Persona, UsageMetric
from src.agent.prompt_builder import build_system_prompt, is_within_business_hours
from src.messaging import dispatcher
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaMessage, WaNumber

logger = logging.getLogger(__name__)

_HISTORY_TURNS = 10


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
    """Construye la lista de mensajes para la API Anthropic.

    Toma los últimos N=10 turnos previos al inbound actual y agrega el mensaje
    actual como último turno "user".  Si la conversación tiene un ai_summary
    (generado cuando turn_count > N), lo inyecta como primer par user/assistant
    comprimido para preservar contexto anterior a la ventana.  Mensajes
    consecutivos del mismo role se fusionan para satisfacer el formato alternado.
    """
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

    # Inyectar resumen como contexto comprimido al inicio de la ventana.
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

    # Asegurar que el último mensaje histórico sea "assistant" si lo hay,
    # para que el nuevo "user" sea correcto.
    inbound_text = inbound_msg.text or ""
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + inbound_text
    else:
        messages.append({"role": "user", "content": inbound_text})

    return messages


def _maybe_trigger_summary(db: Session, conversation: WaConversation) -> None:
    """Genera resumen de la conversación si turn_count supera la ventana de turnos."""
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
) -> None:
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


def respond(db: Session, conversation: WaConversation, inbound_msg: WaMessage) -> None:
    """Genera y persiste la respuesta del bot para un mensaje inbound.

    Devuelve silenciosamente si la conversación no tiene persona asignada.
    Los errores se logean pero no se propagan para no romper el webhook.
    """
    try:
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

        system_prompt = build_system_prompt(persona)
        messages = _build_messages(db, conversation, inbound_msg)

        t0 = time.perf_counter()
        response_text, metadata = call_claude(
            system_prompt, messages, model_id=persona.model_id
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if latency_ms > 8000:
            logger.warning(
                "agent respuesta lenta: %dms conv=%s", latency_ms, conversation.id
            )

        metadata["latency_ms"] = latency_ms
        _persist_outbound(db, conversation, wn, response_text, llm_metadata=metadata)
        _update_usage_metrics(db, conversation.tenant_id, metadata)

    except Exception:
        logger.exception(
            "agent.respond falló inesperadamente conv=%s", conversation.id
        )
