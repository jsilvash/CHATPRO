"""Tests Fase 26-D: Canned responses con variables y búsqueda.

Cubre:
- Variables válidas {{nombre}}, {{producto}} en texto.
- Validación al crear: variable mal formada → 422.
- Validación al actualizar: variable mal formada → 422.
- GET /{id}/render?nombre=Juan → texto renderizado.
- Render devuelve variables_used y variables_missing.
- GET /search?q=<texto> busca en shortcode y text.
- CannedResponseOut incluye campo variables.
- Aislamiento multi-tenant en search.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import _auth_header, _create_tenant_and_owner


def _make_canned(client, shortcode: str, text: str) -> dict:
    resp = client.post("/v1/canned-responses", json={"shortcode": shortcode, "text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCannedTemplates:
    def test_crear_template_sin_variables(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        data = _make_canned(client, "saludo", "Hola, ¿en qué puedo ayudarte?")
        assert data["variables"] == []

    def test_crear_template_con_variables_validas(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        data = _make_canned(client, "bienvenida", "Hola {{nombre}}, tu pedido {{codigo}} está listo.")
        assert "nombre" in data["variables"]
        assert "codigo" in data["variables"]

    def test_crear_template_variable_mal_formada_da_422(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        resp = client.post(
            "/v1/canned-responses",
            json={"shortcode": "mal_var", "text": "Hola {{nombre inválido}}"},
        )
        assert resp.status_code == 422

    def test_actualizar_template_variable_mal_formada_da_422(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        data = _make_canned(client, "patch_var", "Texto original")
        cr_id = data["id"]

        resp = client.patch(
            f"/v1/canned-responses/{cr_id}",
            json={"text": "Hola {{1invalido}}"},
        )
        assert resp.status_code == 422

    def test_render_reemplaza_variables(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        data = _make_canned(client, "render_test", "Hola {{nombre}}, tu producto es {{producto}}.")
        cr_id = data["id"]

        resp = client.get(
            f"/v1/canned-responses/{cr_id}/render",
            params={"nombre": "Juan", "producto": "Zapatillas"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["rendered_text"] == "Hola Juan, tu producto es Zapatillas."
        assert result["variables_used"] == {"nombre": "Juan", "producto": "Zapatillas"}
        assert result["variables_missing"] == []

    def test_render_variable_no_provista(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        data = _make_canned(client, "render_missing", "Hola {{nombre}}, ciudad: {{ciudad}}.")
        cr_id = data["id"]

        resp = client.get(
            f"/v1/canned-responses/{cr_id}/render",
            params={"nombre": "Ana"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "Ana" in result["rendered_text"]
        assert "{{ciudad}}" in result["rendered_text"]
        assert "ciudad" in result["variables_missing"]

    def test_render_original_text_en_respuesta(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        original = "Estimado {{nombre}}, gracias."
        data = _make_canned(client, "render_orig", original)
        cr_id = data["id"]

        resp = client.get(f"/v1/canned-responses/{cr_id}/render", params={"nombre": "Pedro"})
        assert resp.status_code == 200
        result = resp.json()
        assert result["original_text"] == original
        assert result["rendered_text"] == "Estimado Pedro, gracias."

    def test_search_por_shortcode(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        _make_canned(client, "bienvenida_user", "Texto bienvenida")
        _make_canned(client, "despedida_user", "Texto despedida")

        resp = client.get("/v1/canned-responses/search", params={"q": "bienvenida"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        shortcodes = [i["shortcode"] for i in items]
        assert "bienvenida_user" in shortcodes
        assert "despedida_user" not in shortcodes

    def test_search_por_texto(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        _make_canned(client, "sc_zapatilla", "Zapatillas a precio especial")
        _make_canned(client, "sc_camisa", "Camisas de temporada")

        resp = client.get("/v1/canned-responses/search", params={"q": "zapatilla"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        shortcodes = [i["shortcode"] for i in items]
        assert "sc_zapatilla" in shortcodes
        assert "sc_camisa" not in shortcodes

    def test_search_sin_q_da_422(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        resp = client.get("/v1/canned-responses/search", params={"q": ""})
        assert resp.status_code == 422

    def test_search_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b

        # tenant_a crea template
        client.headers.update(_auth_header(owner_a))
        _make_canned(client, f"sc_iso_{uuid.uuid4().hex[:4]}", "Solo del tenant A")

        # tenant_b busca y no debe ver los del tenant_a
        client.headers.update(_auth_header(owner_b))
        resp = client.get("/v1/canned-responses/search", params={"q": "tenant A"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_variables_en_campo_de_respuesta(self, client, tenant_a):
        tenant, owner = tenant_a
        client.headers.update(_auth_header(owner))
        _make_canned(client, "vars_check", "Hola {{nombre}} desde {{ciudad}}")

        # GET list debe incluir variables
        resp = client.get("/v1/canned-responses")
        assert resp.status_code == 200
        items = resp.json()["items"]
        target = next((i for i in items if i["shortcode"] == "vars_check"), None)
        assert target is not None
        assert "nombre" in target["variables"]
        assert "ciudad" in target["variables"]
