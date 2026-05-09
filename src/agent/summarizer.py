"""Resumidor de conversaciones para memoria de corto plazo (Fase 3).

Genera un resumen conciso de la conversación usando Claude Haiku
y lo persiste en wa_conversations.ai_summary.

Versión del prompt: v1
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.wa.models import WaConversation, WaMessage

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT_VERSION = "v1"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_MESSAGES_FOR_SUMMARY = 30

_SUMMARY_SYSTEM = (
    "Eres un asistente que resume conversaciones de WhatsApp de forma concisa. "
    "Genera un resumen en 3-5 oraciones que capture: tema principal, datos clave "
    "del cliente (nombre, necesidades, productos mencionados) y estado de la "
    "conversación. Máximo 300 tokens. Responde solo con el resumen, sin preámbulos."
)


def summarize_conversation(db: Session, conv: WaConversation) -> None:
    """Genera y persiste un resumen en conv.ai_summary usando Claude Haiku.

    Si falla por cualquier motivo, loguea y retorna silenciosamente para
    no interrumpir el flujo del agente.
    """
    try:
        _do_summarize(db, conv)
    except Exception:
        logger.exception("summarizer: falló para conv=%s", conv.id)


def _do_summarize(db: Session, conv: WaConversation) -> None:
    from src.agent.llm import call_claude
    from src.tenancy.context import bypass_tenant_filter

    with bypass_tenant_filter():
        rows = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.text != "",
            )
            .order_by(WaMessage.created_at.desc())
            .limit(_MAX_MESSAGES_FOR_SUMMARY)
            .all()
        )
    rows = list(reversed(rows))

    if not rows:
        return

    lines: list[str] = []
    if conv.ai_summary:
        lines.append(f"[RESUMEN ANTERIOR]\n{conv.ai_summary}\n[FIN RESUMEN ANTERIOR]\n")

    for row in rows:
        who = "Cliente" if row.direction == "in" else "Bot"
        lines.append(f"{who}: {row.text}")

    transcript = "\n".join(lines)
    messages = [{"role": "user", "content": transcript}]

    summary_text, _ = call_claude(
        _SUMMARY_SYSTEM,
        messages,
        model_id=_HAIKU_MODEL,
        max_tokens=300,
    )

    versioned_summary = f"[{_SUMMARY_PROMPT_VERSION}] {summary_text.strip()}"

    with bypass_tenant_filter():
        conv.ai_summary = versioned_summary
        db.add(conv)
        db.commit()
