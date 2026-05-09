"""Tests de agent_service.respond con stub del SDK anthropic.

Nunca llama a la API real. El cliente Anthropic se mockea completamente.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agent.models import Persona
from src.wa.models import WaConversation, WaMessage, WaNumber


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────


@pytest.fixture
def tenant_with_persona(db, tenant_a):
    tenant, owner = tenant_a
    persona = Persona(
        tenant_id=tenant.id,
        name="Asistente",
        system_prompt="Eres un asistente de prueba.",
        tone="amigable",
        locale="es-CL",
        timezone="America/Santiago",
        out_of_hours_message="Estamos fuera de horario.",
        business_hours_json={},
        model_id="claude-sonnet-4-6",
    )
    db.add(persona)
    db.flush()
    return tenant, owner, persona


@pytest.fixture
def wa_number_with_persona(db, tenant_with_persona):
    tenant, owner, persona = tenant_with_persona
    wn = WaNumber(
        tenant_id=tenant.id,
        label="Test Number",
        waha_session_name="test-session-agent",
        persona_id=persona.id,
    )
    db.add(wn)
    db.flush()
    return wn, persona


@pytest.fixture
def conversation_and_inbound(db, wa_number_with_persona):
    wn, persona = wa_number_with_persona
    conv = WaConversation(
        tenant_id=wn.tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone="56912345678",
        wa_contact_name="Juan Test",
    )
    db.add(conv)
    db.flush()

    inbound = WaMessage(
        tenant_id=wn.tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="in",
        text="Hola, necesito ayuda",
        wa_message_id="wa-in-001",
        ack="",
        raw_payload={},
        llm_metadata={},
    )
    db.add(inbound)
    db.flush()
    return conv, inbound


def _fake_anthropic_response(text: str = "Hola, estoy aquí para ayudarte."):
    """Crea un stub de respuesta Anthropic Messages."""
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 30
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0

    block = MagicMock()
    block.type = "text"
    block.text = text

    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    resp.stop_reason = "end_turn"
    return resp


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


class TestAgentRespond:
    def test_respond_sin_persona_no_hace_nada(self, db, tenant_a):
        """Si el WaNumber no tiene persona_id, respond() retorna silenciosamente."""
        tenant, owner = tenant_a
        wn = WaNumber(
            tenant_id=tenant.id,
            label="Sin persona",
            waha_session_name="test-no-persona",
            persona_id=None,
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56900000001",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Hola",
            wa_message_id="wa-nop-001",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        from src.agent import service as agent_service
        agent_service.respond(db, conv, inbound)

        outbound_count = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .count()
        )
        assert outbound_count == 0

    def test_respond_genera_outbound_con_mock(
        self, db, conversation_and_inbound, wa_number_with_persona
    ):
        """respond() persiste un WaMessage outbound cuando el mock devuelve texto."""
        conv, inbound = conversation_and_inbound
        wn, persona = wa_number_with_persona

        fake_resp = _fake_anthropic_response("Hola, ¿en qué te puedo ayudar?")

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = fake_resp

            mock_send.return_value = {"id": {"_serialized": "true_56912345678_ABC"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert outbound.text == "Hola, ¿en qué te puedo ayudar?"
        assert outbound.direction == "out"

    def test_respond_guarda_llm_metadata(
        self, db, conversation_and_inbound, wa_number_with_persona
    ):
        """llm_metadata se persiste con tokens y costo en el WaMessage outbound."""
        conv, inbound = conversation_and_inbound

        fake_resp = _fake_anthropic_response("Respuesta de prueba")

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = fake_resp
            mock_send.return_value = {"id": {"_serialized": "true_56912345678_XYZ"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        meta = outbound.llm_metadata
        assert "input_tokens" in meta
        assert "output_tokens" in meta
        assert "cost_usd" in meta
        assert "latency_ms" in meta
        assert meta["model"] == "claude-sonnet-4-6"

    def test_respond_out_of_hours_envia_mensaje_fuera_horario(
        self, db, tenant_a
    ):
        """Cuando está fuera de horario, envía out_of_hours_message sin llamar a Claude."""
        tenant, owner = tenant_a

        # Horario imposible: solo lunes de 00:01 a 00:02 → siempre fuera
        persona = Persona(
            tenant_id=tenant.id,
            name="Bot horario",
            system_prompt="",
            tone="amigable",
            locale="es-CL",
            timezone="America/Santiago",
            out_of_hours_message="Nos comunicaremos pronto.",
            business_hours_json={
                "tz": "America/Santiago",
                "days": {"mon": ["00:01", "00:02"]},
            },
            model_id="claude-sonnet-4-6",
        )
        db.add(persona)
        db.flush()

        wn = WaNumber(
            tenant_id=tenant.id,
            label="Bot horario",
            waha_session_name="test-hours-session",
            persona_id=persona.id,
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56911111111",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Hola",
            wa_message_id="wa-ooh-001",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_send.return_value = {"id": {"_serialized": "true_56911111111_OOH"}}
            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)
            # Claude NO debe llamarse
            mock_cls.assert_not_called()

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert outbound.text == "Nos comunicaremos pronto."
        # llm_metadata vacío (no hubo llamada a Claude)
        assert outbound.llm_metadata == {}

    def test_respond_error_en_claude_no_rompe_flujo(
        self, db, conversation_and_inbound
    ):
        """Un error en la llamada a Claude se logea pero no propaga excepción."""
        conv, inbound = conversation_and_inbound

        with patch("src.agent.llm.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = RuntimeError("API error")

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)  # No debe lanzar

    def test_respond_construye_historial_de_turnos(
        self, db, conversation_and_inbound, wa_number_with_persona
    ):
        """El contexto enviado a Claude incluye mensajes previos de la conversación."""
        conv, inbound = conversation_and_inbound
        wn, _ = wa_number_with_persona

        # Agregar 3 mensajes de historial
        for i, (direction, text) in enumerate([
            ("in", "Hola"),
            ("out", "¡Hola! ¿En qué puedo ayudarte?"),
            ("in", "Quiero saber los precios"),
        ]):
            m = WaMessage(
                tenant_id=conv.tenant_id,
                wa_number_id=wn.id,
                wa_conversation_id=conv.id,
                direction=direction,
                text=text,
                wa_message_id=f"wa-hist-{i:03d}",
                ack="",
                raw_payload={},
                llm_metadata={},
            )
            db.add(m)
        db.flush()

        fake_resp = _fake_anthropic_response("Nuestros precios son...")
        captured_messages = []

        def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return fake_resp

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = capture_create
            mock_send.return_value = {"id": {"_serialized": "true_56912345678_H01"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        assert len(captured_messages) >= 1
        assert captured_messages[-1]["role"] == "user"
