"""Tests Fase 26-C: Historial de cambios de status.

Cubre:
- take_conversation registra waiting_agent → agent.
- close_conversation registra agent → bot.
- reply_conversation (desde waiting_agent) registra waiting_agent → agent.
- _auto_escalate_if_needed registra bot → waiting_agent.
- GET /v1/inbox/{conv_id}/status-history devuelve lista ordenada.
- Aislamiento multi-tenant.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.auth.tokens import create_access_token
from src.db.models import User
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner
from tests.test_fase26_auto_assign import _make_agent, _make_conversation, _make_wa_number


class TestStatusHistory:
    def test_take_registra_historial(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        # conversación en waiting_agent para poder hacer take
        conv = _make_conversation(db, tenant.id, wn, status="waiting_agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        resp = client.post(f"/v1/inbox/{conv.id}/take")
        assert resp.status_code == 200

        hist_resp = client.get(f"/v1/inbox/{conv.id}/status-history")
        assert hist_resp.status_code == 200
        history = hist_resp.json()
        assert len(history) >= 1
        last = history[-1]
        assert last["old_status"] == "waiting_agent"
        assert last["new_status"] == "agent"
        assert last["changed_by_user_id"] == str(owner.id)

    def test_close_registra_historial(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        resp = client.post(f"/v1/inbox/{conv.id}/close")
        assert resp.status_code == 200

        hist_resp = client.get(f"/v1/inbox/{conv.id}/status-history")
        history = hist_resp.json()
        assert len(history) >= 1
        last = history[-1]
        assert last["old_status"] == "agent"
        assert last["new_status"] == "bot"

    def test_reply_desde_waiting_agent_registra_historial(self, client, db, tenant_a):
        from unittest.mock import MagicMock, patch

        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="waiting_agent")
        db.commit()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.wa_message_id = "wa_test_123"
        mock_result.error = ""
        mock_result.raw_response = {}

        with patch("src.messaging.dispatcher.send_text", return_value=mock_result):
            client.headers.update(_auth_header(owner))
            resp = client.post(
                f"/v1/inbox/{conv.id}/reply",
                json={"text": "Hola desde agente"},
            )
        assert resp.status_code == 200

        hist_resp = client.get(f"/v1/inbox/{conv.id}/status-history")
        history = hist_resp.json()
        transitions = [(h["old_status"], h["new_status"]) for h in history]
        assert ("waiting_agent", "agent") in transitions

    def test_auto_escalate_registra_historial(self, db):
        from src.agent.service import _auto_escalate_if_needed

        tenant, owner = _create_tenant_and_owner(
            db,
            f"sh-t1-{uuid.uuid4().hex[:4]}",
            f"sh1-{uuid.uuid4().hex[:4]}@t.com",
        )
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="bot")
        db.commit()

        _auto_escalate_if_needed(db, conv, "max_tool_calls")
        db.commit()

        from src.inbox.models import ConversationStatusHistory
        from src.tenancy.context import bypass_tenant_filter
        with bypass_tenant_filter():
            history = (
                db.query(ConversationStatusHistory)
                .filter(ConversationStatusHistory.wa_conversation_id == conv.id)
                .order_by(ConversationStatusHistory.changed_at.asc())
                .all()
            )
        assert len(history) >= 1
        assert history[-1].old_status == "bot"
        assert history[-1].new_status == "waiting_agent"
        assert history[-1].changed_by_user_id is None

    def test_historial_ordenado_por_fecha(self, client, db, tenant_a):
        from unittest.mock import MagicMock, patch

        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="waiting_agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        # take → waiting_agent→agent
        client.post(f"/v1/inbox/{conv.id}/take")
        # close → agent→bot
        client.post(f"/v1/inbox/{conv.id}/close")

        hist_resp = client.get(f"/v1/inbox/{conv.id}/status-history")
        history = hist_resp.json()
        assert len(history) >= 2
        # Verificar orden temporal
        dates = [h["changed_at"] for h in history]
        assert dates == sorted(dates)

    def test_historial_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b
        wn_a = _make_wa_number(db, tenant_a_obj.id)
        conv_a = _make_conversation(db, tenant_a_obj.id, wn_a, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner_a))
        client.post(f"/v1/inbox/{conv_a.id}/close")

        # owner_b no puede ver historial de conv_a
        client.headers.update(_auth_header(owner_b))
        resp = client.get(f"/v1/inbox/{conv_a.id}/status-history")
        assert resp.status_code == 404

    def test_historial_vacio_si_sin_cambios(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="bot")
        db.commit()

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/inbox/{conv.id}/status-history")
        assert resp.status_code == 200
        assert resp.json() == []
