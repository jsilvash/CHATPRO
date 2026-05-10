"""Tests Fase 29A — Office Hours (horarios de atención del bot).

Cubre:
1. Listar vacío
2. CRUD completo (crear, obtener, actualizar, eliminar)
3. Bot atiende dentro del horario
4. Bot no responde fuera de horario (sin mensaje configurado)
5. Bot envía out_of_hours_message fuera de horario
6. Aislamiento multi-tenant
7. Registro específico por número vs. global del tenant
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import User
from src.office_hours.models import OfficeHours
from src.office_hours.service import check_office_hours
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ──────────────────────────────────────────────────────────────────


def _headers(user: User) -> dict:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test",
        waha_session_name=f"sess-{uuid.uuid4().hex[:8]}",
        phone="5491100000001",
    )
    db.add(wn)
    db.flush()
    return wn


# ── Test 1: listar vacío ──────────────────────────────────────────────────────


def test_listar_office_hours_vacio(client_a):
    r = client_a.get("/v1/office-hours")
    assert r.status_code == 200
    assert r.json() == []


# ── Test 2: CRUD completo ─────────────────────────────────────────────────────


def test_crud_completo(client_a):
    # Crear
    body = {
        "day_of_week": 0,
        "hour_start": 9,
        "hour_end": 18,
        "is_active": True,
        "out_of_hours_message": "Estamos fuera de horario.",
    }
    r = client_a.post("/v1/office-hours", json=body)
    assert r.status_code == 201
    data = r.json()
    assert data["day_of_week"] == 0
    assert data["hour_start"] == 9
    assert data["hour_end"] == 18
    assert data["is_active"] is True
    assert data["out_of_hours_message"] == "Estamos fuera de horario."
    rec_id = data["id"]

    # Obtener
    r = client_a.get(f"/v1/office-hours/{rec_id}")
    assert r.status_code == 200
    assert r.json()["id"] == rec_id

    # Listar
    r = client_a.get("/v1/office-hours")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Actualizar
    r = client_a.put(f"/v1/office-hours/{rec_id}", json={"is_active": False, "hour_end": 17})
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert r.json()["hour_end"] == 17

    # Eliminar
    r = client_a.delete(f"/v1/office-hours/{rec_id}")
    assert r.status_code == 204

    # Confirmar eliminación
    r = client_a.get(f"/v1/office-hours/{rec_id}")
    assert r.status_code == 404


# ── Test 3: validación campos ─────────────────────────────────────────────────


def test_validacion_campos(client_a):
    r = client_a.post("/v1/office-hours", json={
        "day_of_week": 7,  # inválido
        "hour_start": 9,
        "hour_end": 18,
    })
    assert r.status_code == 422

    r = client_a.post("/v1/office-hours", json={
        "day_of_week": 1,
        "hour_start": 25,  # inválido
        "hour_end": 18,
    })
    assert r.status_code == 422


# ── Test 4: check_office_hours — dentro del horario ─────────────────────────


def test_service_dentro_horario(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    oh = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        day_of_week=now.weekday(),
        hour_start=0,
        hour_end=23,
        is_active=True,
        out_of_hours_message="Cerrado.",
    )
    db.add(oh)
    db.flush()

    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is True
    assert msg == ""


# ── Test 5: check_office_hours — fuera del horario, sin mensaje ──────────────


def test_service_fuera_horario_sin_mensaje(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    # Configurar horario para un día diferente al de hoy.
    now = datetime.now(UTC)
    wrong_day = (now.weekday() + 1) % 7  # mañana

    oh = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        day_of_week=wrong_day,
        hour_start=9,
        hour_end=18,
        is_active=True,
        out_of_hours_message="",
    )
    db.add(oh)
    db.flush()

    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is False
    assert msg == ""


# ── Test 6: check_office_hours — fuera del horario, con mensaje ──────────────


def test_service_fuera_horario_con_mensaje(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    wrong_day = (now.weekday() + 1) % 7

    oh = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        day_of_week=wrong_day,
        hour_start=9,
        hour_end=18,
        is_active=True,
        out_of_hours_message="Atendemos de lunes a viernes 9-18h.",
    )
    db.add(oh)
    db.flush()

    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is False
    assert "9-18" in msg


# ── Test 7: aislamiento multi-tenant ─────────────────────────────────────────


def test_aislamiento_tenant(client_a, client_b, db, tenant_a):
    tenant, owner = tenant_a

    # Crear office hours en tenant A
    r = client_a.post("/v1/office-hours", json={
        "day_of_week": 2,
        "hour_start": 8,
        "hour_end": 20,
    })
    assert r.status_code == 201
    rec_id = r.json()["id"]

    # Tenant B no ve los office hours de Tenant A
    r = client_b.get("/v1/office-hours")
    assert r.status_code == 200
    ids_b = [item["id"] for item in r.json()]
    assert rec_id not in ids_b

    # Tenant B tampoco puede obtener el registro de A por ID
    r = client_b.get(f"/v1/office-hours/{rec_id}")
    assert r.status_code == 404


# ── Test 8: sin registros activos → sin restricción ──────────────────────────


def test_sin_registros_sin_restriccion(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is True
    assert msg == ""


# ── Test 9: registro específico número tiene prioridad sobre global ───────────


def test_numero_especifico_tiene_prioridad(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    today = now.weekday()
    wrong_day = (today + 1) % 7

    # Registro global del tenant: cubre hoy (debería devolver dentro si no hay específico).
    global_rec = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=None,
        day_of_week=today,
        hour_start=0,
        hour_end=23,
        is_active=True,
        out_of_hours_message="Global fuera de horario.",
    )
    db.add(global_rec)

    # Registro específico del número: NO cubre hoy → fuera de horario.
    specific_rec = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        day_of_week=wrong_day,
        hour_start=9,
        hour_end=18,
        is_active=True,
        out_of_hours_message="Específico fuera de horario.",
    )
    db.add(specific_rec)
    db.flush()

    # El registro específico del número tiene prioridad; como no cubre hoy → fuera de horario.
    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is False
    assert "Específico" in msg


# ── Test 10: registro inactivo ignorado ──────────────────────────────────────


def test_registro_inactivo_ignorado(db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    wrong_day = (now.weekday() + 1) % 7

    # Registro inactivo que cubre todos los días — no debe considerarse.
    oh = OfficeHours(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        day_of_week=wrong_day,
        hour_start=9,
        hour_end=18,
        is_active=False,
        out_of_hours_message="No debería usarse.",
    )
    db.add(oh)
    db.flush()

    # Sin registros activos → sin restricción.
    within, msg = check_office_hours(db, tenant.id, wn.id)
    assert within is True
    assert msg == ""
