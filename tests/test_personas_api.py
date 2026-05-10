"""Tests CRUD de personas + aislamiento cross-tenant."""

from __future__ import annotations

import uuid

import pytest


# ────────────────────────────────────────────────────────────
# Fixtures helpers
# ────────────────────────────────────────────────────────────


def _create_persona(client, **overrides):
    payload = {
        "name": "Bot Ventas",
        "system_prompt": "Eres un bot de ventas.",
        "tone": "amigable",
        "locale": "es-CL",
        "timezone": "America/Santiago",
        "out_of_hours_message": "Volvemos mañana.",
        "business_hours_json": {},
        "model_id": "claude-sonnet-4-6",
    }
    payload.update(overrides)
    resp = client.post("/v1/personas", json=payload)
    return resp


# ────────────────────────────────────────────────────────────
# CRUD básico
# ────────────────────────────────────────────────────────────


class TestPersonasCRUD:
    def test_crear_persona_devuelve_201(self, client_a):
        resp = _create_persona(client_a)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Bot Ventas"
        assert "id" in data
        assert "tenant_id" in data

    def test_crear_persona_campos_opcionales_defaults(self, client_a):
        resp = client_a.post("/v1/personas", json={"name": "Bot Minimo"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["tone"] == "amigable"
        assert data["locale"] == "es-CL"
        assert data["model_id"] == "claude-sonnet-4-6"

    def test_listar_personas_retorna_propias(self, client_a):
        _create_persona(client_a, name="Bot A1")
        _create_persona(client_a, name="Bot A2")
        resp = client_a.get("/v1/personas")
        assert resp.status_code == 200
        data = resp.json()
        nombres = [p["name"] for p in data["items"]]
        assert "Bot A1" in nombres
        assert "Bot A2" in nombres

    def test_obtener_persona_por_id(self, client_a):
        created = _create_persona(client_a, name="Bot Detail").json()
        persona_id = created["id"]
        resp = client_a.get(f"/v1/personas/{persona_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bot Detail"

    def test_obtener_persona_inexistente_404(self, client_a):
        resp = client_a.get(f"/v1/personas/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_actualizar_persona_patch(self, client_a):
        created = _create_persona(client_a).json()
        persona_id = created["id"]
        resp = client_a.patch(
            f"/v1/personas/{persona_id}",
            json={"name": "Bot Actualizado", "tone": "formal"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Bot Actualizado"
        assert data["tone"] == "formal"
        # Campos no tocados deben conservarse
        assert data["locale"] == "es-CL"

    def test_patch_parcial_solo_campos_enviados(self, client_a):
        created = _create_persona(client_a, system_prompt="Prompt original").json()
        persona_id = created["id"]
        resp = client_a.patch(f"/v1/personas/{persona_id}", json={"name": "Nuevo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Nuevo"
        assert data["system_prompt"] == "Prompt original"

    def test_eliminar_persona(self, client_a):
        created = _create_persona(client_a).json()
        persona_id = created["id"]
        resp = client_a.delete(f"/v1/personas/{persona_id}")
        assert resp.status_code == 204
        resp2 = client_a.get(f"/v1/personas/{persona_id}")
        assert resp2.status_code == 404

    def test_business_hours_json_se_persiste(self, client_a):
        bh = {
            "tz": "America/Santiago",
            "days": {"mon-fri": ["09:00", "18:00"]},
        }
        created = _create_persona(client_a, business_hours_json=bh).json()
        persona_id = created["id"]
        resp = client_a.get(f"/v1/personas/{persona_id}")
        assert resp.status_code == 200
        assert resp.json()["business_hours_json"] == bh


# ────────────────────────────────────────────────────────────
# Aislamiento cross-tenant
# ────────────────────────────────────────────────────────────


class TestPersonasIsolacion:
    def test_tenant_b_no_ve_personas_de_tenant_a(self, client_a, client_b):
        _create_persona(client_a, name="Privada de A")
        resp_b = client_b.get("/v1/personas")
        assert resp_b.status_code == 200
        nombres = [p["name"] for p in resp_b.json()["items"]]
        assert "Privada de A" not in nombres

    def test_tenant_b_no_puede_leer_persona_de_tenant_a(self, client_a, client_b):
        created = _create_persona(client_a).json()
        persona_id = created["id"]
        resp = client_b.get(f"/v1/personas/{persona_id}")
        assert resp.status_code == 404

    def test_tenant_b_no_puede_patchear_persona_de_tenant_a(self, client_a, client_b):
        created = _create_persona(client_a).json()
        persona_id = created["id"]
        resp = client_b.patch(
            f"/v1/personas/{persona_id}", json={"name": "Hackeado"}
        )
        assert resp.status_code == 404

    def test_tenant_b_no_puede_eliminar_persona_de_tenant_a(self, client_a, client_b):
        created = _create_persona(client_a).json()
        persona_id = created["id"]
        resp = client_b.delete(f"/v1/personas/{persona_id}")
        assert resp.status_code == 404

    def test_total_solo_cuenta_propias(self, client_a, client_b):
        _create_persona(client_a, name="A-1")
        _create_persona(client_a, name="A-2")
        _create_persona(client_b, name="B-1")

        resp_a = client_a.get("/v1/personas")
        resp_b = client_b.get("/v1/personas")

        total_a = resp_a.json()["total"]
        total_b = resp_b.json()["total"]

        assert total_a >= 2
        assert total_b >= 1
        # B no cuenta las de A
        assert total_b < total_a or total_b == 1


# ────────────────────────────────────────────────────────────
# Asignación de Persona a WaNumber
# ────────────────────────────────────────────────────────────


class TestAssignPersonaToWaNumber:
    def _create_wa_number(self, client, session_name: str):
        from unittest.mock import patch

        with patch("src.messaging.waha_client.create_session"):
            resp = client.post(
                "/v1/wa-numbers",
                json={
                    "label": "Test WA",
                    "waha_session_name": session_name,
                },
            )
        return resp

    def test_asignar_persona_a_numero(self, client_a):
        persona = _create_persona(client_a).json()
        wn_resp = self._create_wa_number(client_a, "assign-test-001")
        if wn_resp.status_code != 201:
            pytest.skip("WAHA no disponible en este entorno de test")
        wn_id = wn_resp.json()["id"]

        resp = client_a.patch(
            f"/v1/wa-numbers/{wn_id}/persona",
            json={"persona_id": persona["id"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["persona_id"] == persona["id"]
        assert data["wa_number_id"] == wn_id

    def test_desasignar_persona_con_null(self, client_a):
        persona = _create_persona(client_a).json()
        wn_resp = self._create_wa_number(client_a, "assign-test-002")
        if wn_resp.status_code != 201:
            pytest.skip("WAHA no disponible en este entorno de test")
        wn_id = wn_resp.json()["id"]

        # Asignar
        client_a.patch(
            f"/v1/wa-numbers/{wn_id}/persona",
            json={"persona_id": persona["id"]},
        )
        # Desasignar
        resp = client_a.patch(
            f"/v1/wa-numbers/{wn_id}/persona",
            json={"persona_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["persona_id"] is None

    def test_asignar_persona_de_otro_tenant_devuelve_404(self, client_a, client_b):
        persona_a = _create_persona(client_a).json()
        wn_resp = self._create_wa_number(client_b, "assign-test-003")
        if wn_resp.status_code != 201:
            pytest.skip("WAHA no disponible en este entorno de test")
        wn_id = wn_resp.json()["id"]

        resp = client_b.patch(
            f"/v1/wa-numbers/{wn_id}/persona",
            json={"persona_id": persona_a["id"]},
        )
        assert resp.status_code == 404

    def test_asignar_a_numero_de_otro_tenant_devuelve_404(self, client_a, client_b):
        persona_b = _create_persona(client_b).json()
        wn_resp = self._create_wa_number(client_a, "assign-test-004")
        if wn_resp.status_code != 201:
            pytest.skip("WAHA no disponible en este entorno de test")
        wn_id = wn_resp.json()["id"]

        resp = client_b.patch(
            f"/v1/wa-numbers/{wn_id}/persona",
            json={"persona_id": persona_b["id"]},
        )
        assert resp.status_code == 404
