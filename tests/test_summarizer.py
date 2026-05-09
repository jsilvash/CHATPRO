"""Tests del resumidor de conversaciones (Fase 3).

Nunca llama a la API real. Claude Haiku se mockea completamente.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agent.models import Persona
from src.wa.models import WaConversation, WaMessage, WaNumber


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────


@pytest.fixture
def conv_with_messages(db, tenant_a):
    tenant, _ = tenant_a

    wn = WaNumber(
        tenant_id=tenant.id,
        label="Num resumen",
        waha_session_name="test-session-summarizer",
    )
    db.add(wn)
    db.flush()

    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="56912000001",
        wa_contact_name="Ana Prueba",
    )
    db.add(conv)
    db.flush()

    # 5 mensajes alternados
    for i, (direction, text) in enumerate([
        ("in", "Hola, quiero comprar zapatos"),
        ("out", "¡Hola! ¿Qué talla necesitas?"),
        ("in", "Talla 38, color negro"),
        ("out", "Tenemos stock disponible."),
        ("in", "¿Cuánto cuestan?"),
    ]):
        db.add(WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction=direction,
            text=text,
            wa_message_id=f"wa-sum-{i:03d}",
            ack="",
            raw_payload={},
            llm_metadata={},
        ))
    db.flush()

    return conv


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


class TestSummarizer:
    def test_summarize_genera_ai_summary(self, db, conv_with_messages):
        """summarize_conversation actualiza conv.ai_summary con el texto del mock."""
        conv = conv_with_messages

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            usage = MagicMock()
            usage.input_tokens = 80
            usage.output_tokens = 50
            usage.cache_read_input_tokens = 0
            usage.cache_creation_input_tokens = 0

            block = MagicMock()
            block.type = "text"
            block.text = "Cliente Ana consulta zapatos talla 38 negros."

            resp = MagicMock()
            resp.content = [block]
            resp.usage = usage
            resp.stop_reason = "end_turn"

            mock_client.messages.create.return_value = resp

            from src.agent.summarizer import summarize_conversation
            summarize_conversation(db, conv)

        db.refresh(conv)
        assert conv.ai_summary is not None
        assert "zapatos" in conv.ai_summary
        assert "[v1]" in conv.ai_summary

    def test_summarize_usa_modelo_haiku(self, db, conv_with_messages):
        """El resumen se genera con claude-haiku-4-5-20251001, no Sonnet."""
        conv = conv_with_messages
        captured_model = []

        def capture_create(**kwargs):
            captured_model.append(kwargs.get("model"))
            usage = MagicMock()
            usage.input_tokens = 50
            usage.output_tokens = 20
            usage.cache_read_input_tokens = 0
            usage.cache_creation_input_tokens = 0
            block = MagicMock()
            block.type = "text"
            block.text = "Resumen de prueba."
            resp = MagicMock()
            resp.content = [block]
            resp.usage = usage
            resp.stop_reason = "end_turn"
            return resp

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = capture_create

            from src.agent.summarizer import summarize_conversation
            summarize_conversation(db, conv)

        assert captured_model == ["claude-haiku-4-5-20251001"]

    def test_summarize_incluye_resumen_anterior(self, db, conv_with_messages):
        """Si conv.ai_summary ya existe, su contenido se incluye en el prompt."""
        conv = conv_with_messages
        conv.ai_summary = "[v1] Resumen previo: cliente quiere zapatos."
        db.add(conv)
        db.flush()

        captured_content = []

        def capture_create(**kwargs):
            msgs = kwargs.get("messages", [])
            if msgs:
                captured_content.append(msgs[0].get("content", ""))
            usage = MagicMock()
            usage.input_tokens = 60
            usage.output_tokens = 30
            usage.cache_read_input_tokens = 0
            usage.cache_creation_input_tokens = 0
            block = MagicMock()
            block.type = "text"
            block.text = "Nuevo resumen."
            resp = MagicMock()
            resp.content = [block]
            resp.usage = usage
            resp.stop_reason = "end_turn"
            return resp

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = capture_create

            from src.agent.summarizer import summarize_conversation
            summarize_conversation(db, conv)

        assert captured_content, "No se capturó ningún mensaje"
        assert "RESUMEN ANTERIOR" in captured_content[0]
        assert "Resumen previo" in captured_content[0]

    def test_summarize_error_no_propaga(self, db, conv_with_messages):
        """Un error en la API de Haiku se loguea pero no lanza excepción."""
        conv = conv_with_messages

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = RuntimeError("Haiku API error")

            from src.agent.summarizer import summarize_conversation
            summarize_conversation(db, conv)  # no debe lanzar

        db.refresh(conv)
        assert conv.ai_summary is None

    def test_summarize_sin_mensajes_no_hace_nada(self, db, tenant_a):
        """Si la conversación no tiene mensajes, ai_summary no se modifica."""
        tenant, _ = tenant_a

        wn = WaNumber(
            tenant_id=tenant.id,
            label="Num vacío",
            waha_session_name="test-session-empty-sum",
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56912000002",
        )
        db.add(conv)
        db.flush()

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            from src.agent.summarizer import summarize_conversation
            summarize_conversation(db, conv)
            mock_cls.assert_not_called()

        db.refresh(conv)
        assert conv.ai_summary is None
