"""Tests Fase 26-B: Notas internas en conversaciones.

Cubre:
- POST /v1/inbox/{conv_id}/notes → 201 con nota creada.
- GET  /v1/inbox/{conv_id}/notes → lista ordenada.
- DELETE /v1/inbox/{conv_id}/notes/{note_id} → 204 si es el autor.
- DELETE por no-autor → 403.
- GET /v1/inbox/{conv_id} incluye notes_count.
- Aislamiento multi-tenant.
"""

from __future__ import annotations

import uuid

import pytest

from src.auth.tokens import create_access_token
from src.db.models import User
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner
from tests.test_fase26_auto_assign import _make_agent, _make_conversation, _make_wa_number


# ── Tests notas ───────────────────────────────────────────────────────────────


class TestNotes:
    def _setup(self, db, slug_suffix=""):
        tenant, owner = _create_tenant_and_owner(
            db,
            f"notes-t{slug_suffix}-{uuid.uuid4().hex[:4]}",
            f"notes{slug_suffix}-{uuid.uuid4().hex[:4]}@t.com",
        )
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()
        return tenant, owner, conv

    def test_crear_nota_devuelve_201(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        resp = client.post(
            f"/v1/inbox/{conv.id}/notes",
            json={"text": "Nota de prueba"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "Nota de prueba"
        assert data["user_id"] == str(owner.id)
        assert data["wa_conversation_id"] == str(conv.id)

    def test_listar_notas(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Primera"})
        client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Segunda"})

        resp = client.get(f"/v1/inbox/{conv.id}/notes")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert items[0]["text"] == "Primera"
        assert items[1]["text"] == "Segunda"

    def test_eliminar_nota_autor(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        create_resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Para borrar"})
        note_id = create_resp.json()["id"]

        resp = client.delete(f"/v1/inbox/{conv.id}/notes/{note_id}")
        assert resp.status_code == 204

        # Ya no aparece
        list_resp = client.get(f"/v1/inbox/{conv.id}/notes")
        assert all(n["id"] != note_id for n in list_resp.json())

    def test_eliminar_nota_otro_usuario_da_403(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        agent2 = _make_agent(db, tenant.id)
        db.commit()

        client.headers.update(_auth_header(owner))
        create_resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Nota owner"})
        note_id = create_resp.json()["id"]

        # Cambiar a otro usuario
        client.headers.update(_auth_header(agent2))
        resp = client.delete(f"/v1/inbox/{conv.id}/notes/{note_id}")
        assert resp.status_code == 403

    def test_notes_count_en_detalle_conversacion(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        # Sin notas, notes_count = 0
        resp = client.get(f"/v1/inbox/{conv.id}")
        assert resp.status_code == 200
        assert resp.json()["conversation"]["notes_count"] == 0

        # Agregar 2 notas
        client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Nota 1"})
        client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Nota 2"})

        resp = client.get(f"/v1/inbox/{conv.id}")
        assert resp.json()["conversation"]["notes_count"] == 2

    def test_notas_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b
        wn_a = _make_wa_number(db, tenant_a_obj.id)
        wn_b = _make_wa_number(db, tenant_b_obj.id)
        conv_a = _make_conversation(db, tenant_a_obj.id, wn_a, status="agent")
        conv_b = _make_conversation(db, tenant_b_obj.id, wn_b, status="agent")
        db.commit()

        # Owner A crea nota en su conversación
        client.headers.update(_auth_header(owner_a))
        client.post(f"/v1/inbox/{conv_a.id}/notes", json={"text": "Nota A"})

        # Owner B no puede ver la conversación de A
        client.headers.update(_auth_header(owner_b))
        resp = client.get(f"/v1/inbox/{conv_a.id}/notes")
        assert resp.status_code == 404

    def test_nota_texto_vacio_da_422(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "   "})
        assert resp.status_code == 422

    def test_notes_count_en_listado(self, client, db, tenant_a):
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="agent")
        db.commit()

        client.headers.update(_auth_header(owner))
        client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Nota listado"})

        resp = client.get("/v1/inbox")
        assert resp.status_code == 200
        items = resp.json()["items"]
        found = next((i for i in items if i["id"] == str(conv.id)), None)
        assert found is not None
        assert found["notes_count"] >= 1
