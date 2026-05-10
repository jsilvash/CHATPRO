"""Tests Fase 23C — Soporte multi-idioma en personas (i18n).

Cubre:
- prompt incluye bloque IDIOMA si auto_detect_locale=True.
- No incluye bloque si auto_detect_locale=False.
- locale_secondary se serializa correctamente en CRUD.
- PATCH /v1/personas/{id} actualiza locale_secondary y auto_detect_locale.
- Bloque incluye locale_primary + locale_secondary en la lista de soportados.
- Si locale_secondary vacío, bloque solo muestra locale_primary.
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.models import Persona
from src.agent.prompt_builder import build_system_prompt


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_persona(db, tenant_id: uuid.UUID, **kwargs) -> Persona:
    defaults = {
        "name": "Bot Test",
        "system_prompt": "Sos un asistente.",
        "tone": "amigable",
        "locale": "es-CL",
        "timezone": "America/Santiago",
        "out_of_hours_message": "",
        "business_hours_json": {},
        "model_id": "claude-sonnet-4-6",
        "locale_secondary": [],
        "auto_detect_locale": False,
    }
    defaults.update(kwargs)
    p = Persona(tenant_id=tenant_id, **defaults)
    db.add(p)
    db.flush()
    return p


# ── Tests de prompt_builder ───────────────────────────────────────────────────


def test_prompt_sin_auto_detect_no_incluye_bloque_idioma(db, tenant_a):
    """Si auto_detect_locale=False, el system prompt NO contiene el bloque IDIOMA."""
    tenant, owner = tenant_a
    persona = _make_persona(db, tenant.id, auto_detect_locale=False)
    prompt = build_system_prompt(persona)
    assert "IDIOMA" not in prompt


def test_prompt_con_auto_detect_incluye_bloque_idioma(db, tenant_a):
    """Si auto_detect_locale=True, el system prompt contiene el bloque IDIOMA."""
    tenant, owner = tenant_a
    persona = _make_persona(
        db, tenant.id,
        locale="es-CL",
        locale_secondary=["en", "pt"],
        auto_detect_locale=True,
    )
    prompt = build_system_prompt(persona)
    assert "IDIOMA" in prompt
    assert "es-CL" in prompt
    assert "en" in prompt
    assert "pt" in prompt


def test_prompt_bloque_idioma_respaldo_es_locale_primary(db, tenant_a):
    """El bloque indica que si el idioma no está soportado, responder en locale_primary."""
    tenant, owner = tenant_a
    persona = _make_persona(
        db, tenant.id,
        locale="es-MX",
        locale_secondary=["en"],
        auto_detect_locale=True,
    )
    prompt = build_system_prompt(persona)
    assert "es-MX" in prompt
    # El fallback debe mencionar el locale principal
    assert prompt.count("es-MX") >= 2  # aparece en lista y como fallback


def test_prompt_auto_detect_sin_secundarios(db, tenant_a):
    """Con auto_detect=True y locale_secondary vacío, el bloque solo muestra locale_primary."""
    tenant, owner = tenant_a
    persona = _make_persona(
        db, tenant.id,
        locale="es-CL",
        locale_secondary=[],
        auto_detect_locale=True,
    )
    prompt = build_system_prompt(persona)
    assert "IDIOMA" in prompt
    assert "es-CL" in prompt


# ── Tests de CRUD API ────────────────────────────────────────────────────────


def test_crear_persona_con_i18n(client_a, tenant_a, db):
    """POST /v1/personas con locale_secondary y auto_detect_locale."""
    resp = client_a.post("/v1/personas", json={
        "name": "Bot i18n",
        "system_prompt": "Hola",
        "locale": "es-CL",
        "locale_secondary": ["en", "pt"],
        "auto_detect_locale": True,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["locale_secondary"] == ["en", "pt"]
    assert data["auto_detect_locale"] is True


def test_crear_persona_i18n_defaults(client_a, tenant_a, db):
    """POST /v1/personas sin campos i18n → defaults vacíos."""
    resp = client_a.post("/v1/personas", json={
        "name": "Bot sin i18n",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["locale_secondary"] == []
    assert data["auto_detect_locale"] is False


def test_patch_persona_actualiza_i18n(client_a, tenant_a, db):
    """PATCH /v1/personas/{id} actualiza locale_secondary y auto_detect_locale."""
    tenant, owner = tenant_a

    # Crear sin i18n
    resp = client_a.post("/v1/personas", json={"name": "Bot", "system_prompt": "X"})
    assert resp.status_code == 201
    persona_id = resp.json()["id"]

    # Actualizar con i18n
    resp2 = client_a.patch(f"/v1/personas/{persona_id}", json={
        "locale_secondary": ["en"],
        "auto_detect_locale": True,
    })
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["locale_secondary"] == ["en"]
    assert data2["auto_detect_locale"] is True


def test_get_persona_serializa_i18n(client_a, tenant_a, db):
    """GET /v1/personas/{id} devuelve locale_secondary como lista."""
    tenant, owner = tenant_a
    persona = _make_persona(
        db, tenant.id,
        locale_secondary=["en", "fr"],
        auto_detect_locale=True,
    )

    resp = client_a.get(f"/v1/personas/{persona.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["locale_secondary"]) == {"en", "fr"}
    assert data["auto_detect_locale"] is True
