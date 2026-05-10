"""Tareas Celery del módulo agente (Fase 24D).

- ``summarize_on_close``: genera resumen de una conversación al cerrarse si
  tiene > 5 turnos. Usa Claude Haiku y persiste en WaConversation.ai_summary.
"""

from __future__ import annotations

import logging
import uuid

from src.celery_app import celery_app

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = (
    "Resumí la siguiente conversación de soporte/ventas en 2-3 oraciones. "
    "Incluí el tema principal tratado y la resolución o estado final (si la hubo). "
    "Sé conciso y objetivo."
)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"


@celery_app.task(name="agent.summarize_on_close", bind=True, max_retries=2)
def summarize_on_close(self, conv_id: str) -> dict:
    """Genera y persiste un resumen de conversación al cerrarse.

    Solo actúa si la conversación tiene > 5 mensajes de tipo texto.
    """
    from src.db.session import get_db_session
    from src.tenancy.context import bypass_tenant_filter
    from src.wa.models import WaConversation, WaMessage

    try:
        with get_db_session() as db:
            with bypass_tenant_filter():
                conv = db.query(WaConversation).filter(
                    WaConversation.id == uuid.UUID(conv_id)
                ).first()

            if conv is None:
                logger.warning("summarize_on_close: conversación %s no encontrada", conv_id)
                return {"status": "not_found"}

            with bypass_tenant_filter():
                messages = (
                    db.query(WaMessage)
                    .filter(
                        WaMessage.wa_conversation_id == conv.id,
                        WaMessage.tenant_id == conv.tenant_id,
                        WaMessage.text != "",
                    )
                    .order_by(WaMessage.created_at.asc())
                    .all()
                )

            turn_count = sum(
                1 for m in messages if m.direction in ("in", "out")
            )
            if turn_count <= 5:
                return {"status": "skipped", "turn_count": turn_count}

            # Construir historial para el LLM.
            transcript_lines = []
            for m in messages:
                role = "Cliente" if m.direction == "in" else "Agente"
                transcript_lines.append(f"{role}: {m.text}")
            transcript = "\n".join(transcript_lines)

            from src.agent.llm import call_claude_messages
            system_prompt = _SUMMARY_PROMPT
            user_msg = f"Conversación:\n{transcript}"

            response, _meta = call_claude_messages(
                system_prompt,
                [{"role": "user", "content": user_msg}],
                model_id=_HAIKU_MODEL,
                max_tokens=256,
            )
            summary = "".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            ).strip()

            if summary:
                with bypass_tenant_filter():
                    conv.ai_summary = summary
                    db.add(conv)
                    db.commit()
                logger.info(
                    "summarize_on_close: resumen persistido conv=%s len=%d",
                    conv_id, len(summary),
                )
                return {"status": "ok", "summary_len": len(summary)}

            return {"status": "empty_summary"}

    except Exception as exc:
        logger.exception("summarize_on_close: error inesperado conv=%s", conv_id)
        raise self.retry(exc=exc, countdown=60)
