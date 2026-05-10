"""Tests de la API pública y webhooks salientes (Fase 10).

Cubre:
1. Creación de API key (retorna token raw).
2. Revocación de API key.
3. Autenticación por token (get_api_key dependency).
4. Scope requerido rechaza key sin scope.
5. Key revocada rechaza autenticación.
6. CRUD de webhooks.
7. Emisión de evento (emit_event crea deliveries).
8. Delivery exitoso actualiza estado y contadores.
9. Delivery fallido programa reintento con delay correcto.
10. Dead letter después de MAX_ATTEMPTS.
11. Aislamiento: tenant A no ve keys/webhooks de tenant B.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import Tenant, User
from src.public_api.dispatcher import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    deliver_webhook,
    emit_event,
    process_due_deliveries,
)
from src.public_api.models import ApiKey, WebhookDelivery, WebhookOut


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_tenant(db, slug: str, email: str) -> tuple[Tenant, User]:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=hash_password("secret123"),
        full_name=f"Owner {slug}",
        role="owner",
    )
    db.add(owner)
    db.flush()
    return tenant, owner


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _create_api_key(db, tenant: Tenant, user: User, scopes: list[str] | None = None) -> tuple[str, ApiKey]:
    """Crea una ApiKey en BD y devuelve (token_raw, key)."""
    raw = secrets.token_hex(32)
    key = ApiKey(
        tenant_id=tenant.id,
        name="test key",
        hashed_key=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:8],
        scopes=scopes or ["read"],
        created_by_user_id=user.id,
    )
    db.add(key)
    db.flush()
    return raw, key


def _create_webhook(
    db,
    tenant: Tenant,
    events: list[str] | None = None,
    enabled: bool = True,
) -> WebhookOut:
    wh = WebhookOut(
        tenant_id=tenant.id,
        url="https://example.com/webhook",
        secret="super-secret",
        events=events or ["message.received"],
        enabled=enabled,
    )
    db.add(wh)
    db.flush()
    return wh


# ────────────────────────────────────────────────────────────────────────────
# 1. CRUD de API keys
# ────────────────────────────────────────────────────────────────────────────


class TestApiKeysCRUD:
    def test_crear_key_devuelve_token(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        resp = client.post(
            "/v1/api-keys",
            json={"name": "mi integración", "scopes": ["read"]},
            headers=_auth(owner),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "token" in data
        assert len(data["token"]) == 64  # hex de 32 bytes
        assert data["prefix"] == data["token"][:8]
        assert data["scopes"] == ["read"]
        assert data["revoked_at"] is None

    def test_crear_key_sin_scope_falla(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        resp = client.post(
            "/v1/api-keys",
            json={"name": "x", "scopes": []},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_crear_key_scope_invalido_falla(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        resp = client.post(
            "/v1/api-keys",
            json={"name": "x", "scopes": ["inexistente"]},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_listar_keys(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, _key = _create_api_key(db, _tenant, owner)
        resp = client.get("/v1/api-keys", headers=_auth(owner))
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_listar_keys_excluye_revocadas_por_defecto(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, key = _create_api_key(db, _tenant, owner)
        key.revoked_at = datetime.now(timezone.utc)
        db.flush()

        resp = client.get("/v1/api-keys", headers=_auth(owner))
        assert resp.status_code == 200
        ids = [k["id"] for k in resp.json()]
        assert str(key.id) not in ids

    def test_listar_keys_incluir_revocadas(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, key = _create_api_key(db, _tenant, owner)
        key.revoked_at = datetime.now(timezone.utc)
        db.flush()

        resp = client.get("/v1/api-keys?incluir_revocadas=true", headers=_auth(owner))
        ids = [k["id"] for k in resp.json()]
        assert str(key.id) in ids

    def test_obtener_key(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, key = _create_api_key(db, _tenant, owner)
        resp = client.get(f"/v1/api-keys/{key.id}", headers=_auth(owner))
        assert resp.status_code == 200
        assert resp.json()["id"] == str(key.id)

    def test_revocar_key(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, key = _create_api_key(db, _tenant, owner)
        resp = client.delete(f"/v1/api-keys/{key.id}", headers=_auth(owner))
        assert resp.status_code == 204
        db.refresh(key)
        assert key.revoked_at is not None

    def test_revocar_key_ya_revocada_falla(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _raw, key = _create_api_key(db, _tenant, owner)
        key.revoked_at = datetime.now(timezone.utc)
        db.flush()
        resp = client.delete(f"/v1/api-keys/{key.id}", headers=_auth(owner))
        assert resp.status_code == 409


# ────────────────────────────────────────────────────────────────────────────
# 2. Autenticación por token (get_api_key)
# ────────────────────────────────────────────────────────────────────────────


class TestApiKeyAuth:
    def test_authn_por_token_exitosa(self, client, db):
        tenant, owner = _make_tenant(db, "authn-tenant", "authn@test.com")
        raw, _key = _create_api_key(db, tenant, owner, scopes=["read"])
        resp = client.get(
            "/v1/api-keys/me",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == str(tenant.id)
        assert "read" in data["scopes"]

    def test_authn_actualiza_last_used_at(self, client, db):
        tenant, owner = _make_tenant(db, "lastused-tenant", "lastused@test.com")
        raw, key = _create_api_key(db, tenant, owner)
        assert key.last_used_at is None

        client.get("/v1/api-keys/me", headers={"Authorization": f"Bearer {raw}"})
        db.refresh(key)
        assert key.last_used_at is not None

    def test_authn_token_invalido_rechazado(self, client, db):
        resp = client.get(
            "/v1/api-keys/me",
            headers={"Authorization": "Bearer tokeninvalido1234567890abcdef"},
        )
        assert resp.status_code == 401

    def test_authn_key_revocada_rechazada(self, client, db):
        tenant, owner = _make_tenant(db, "revoked-tenant", "revoked@test.com")
        raw, key = _create_api_key(db, tenant, owner)
        key.revoked_at = datetime.now(timezone.utc)
        db.flush()

        resp = client.get(
            "/v1/api-keys/me",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 401

    def test_scope_insuficiente_rechazado(self, client, db):
        """Scope 'admin' requerido pero key tiene solo 'read' → 403."""
        tenant, owner = _make_tenant(db, "scope-tenant", "scope@test.com")
        raw, _key = _create_api_key(db, tenant, owner, scopes=["read"])

        from fastapi import APIRouter, Depends
        from fastapi.testclient import TestClient
        from src.main import app
        from src.public_api.dependencies import get_api_key
        from src.public_api.models import ApiKey as ApiKeyModel
        from src.db.session import get_db

        test_router = APIRouter()

        @test_router.get("/test-scope-admin")
        def _test_scope(k: ApiKeyModel = Depends(get_api_key("admin"))):
            return {"ok": True}

        app.include_router(test_router)

        def override():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override

        try:
            with TestClient(app) as tc:
                resp2 = tc.get(
                    "/test-scope-admin",
                    headers={"Authorization": f"Bearer {raw}"},
                )
            assert resp2.status_code == 403
        finally:
            app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────────────────────
# 3. CRUD de webhooks
# ────────────────────────────────────────────────────────────────────────────


class TestWebhooksCRUD:
    def test_crear_webhook(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://mi-app.com/hook",
                "events": ["message.received"],
            },
            headers=_auth(owner),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://mi-app.com/hook"
        assert "message.received" in data["events"]
        assert "secret" in data
        assert len(data["secret"]) > 10

    def test_crear_webhook_evento_invalido_falla(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        resp = client.post(
            "/v1/webhooks",
            json={"url": "https://x.com/hook", "events": ["evento.inventado"]},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_listar_webhooks(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        _create_webhook(db, _tenant)
        resp = client.get("/v1/webhooks", headers=_auth(owner))
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_obtener_webhook(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        wh = _create_webhook(db, _tenant)
        resp = client.get(f"/v1/webhooks/{wh.id}", headers=_auth(owner))
        assert resp.status_code == 200
        assert resp.json()["id"] == str(wh.id)

    def test_actualizar_webhook(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        wh = _create_webhook(db, _tenant, events=["message.received"])
        resp = client.put(
            f"/v1/webhooks/{wh.id}",
            json={"events": ["message.sent", "conversation.escalated"]},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        assert "message.sent" in resp.json()["events"]

    def test_eliminar_webhook(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        wh = _create_webhook(db, _tenant)
        resp = client.delete(f"/v1/webhooks/{wh.id}", headers=_auth(owner))
        assert resp.status_code == 204
        assert db.query(WebhookOut).filter(WebhookOut.id == wh.id).first() is None

    def test_listar_entregas(self, client, tenant_a, db):
        _tenant, owner = tenant_a
        wh = _create_webhook(db, _tenant)
        delivery = WebhookDelivery(
            tenant_id=_tenant.id,
            webhook_id=wh.id,
            event="message.received",
            payload={"msg": "hola"},
            status="success",
            attempts=1,
        )
        db.add(delivery)
        db.flush()
        resp = client.get(f"/v1/webhooks/{wh.id}/deliveries", headers=_auth(owner))
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ────────────────────────────────────────────────────────────────────────────
# 4. Dispatcher — emit_event
# ────────────────────────────────────────────────────────────────────────────


class TestEmitEvent:
    def test_emit_crea_deliveries_para_hooks_suscritos(self, db):
        tenant, owner = _make_tenant(db, "emit-tenant", "emit@test.com")
        wh1 = _create_webhook(db, tenant, events=["message.received"])
        wh2 = _create_webhook(db, tenant, events=["message.sent"])

        count = emit_event(tenant.id, "message.received", {"body": "hola"}, db)
        assert count == 1

        deliveries = db.query(WebhookDelivery).filter(
            WebhookDelivery.tenant_id == tenant.id
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].webhook_id == wh1.id
        assert deliveries[0].status == "pending"
        assert deliveries[0].event == "message.received"

    def test_emit_no_crea_delivery_para_hook_deshabilitado(self, db):
        tenant, owner = _make_tenant(db, "emit2-tenant", "emit2@test.com")
        _create_webhook(db, tenant, events=["message.received"], enabled=False)

        count = emit_event(tenant.id, "message.received", {"body": "hola"}, db)
        assert count == 0

    def test_emit_sin_webhooks_devuelve_cero(self, db):
        tenant, owner = _make_tenant(db, "emit3-tenant", "emit3@test.com")
        count = emit_event(tenant.id, "message.received", {}, db)
        assert count == 0

    def test_emit_multiples_hooks_suscritos(self, db):
        tenant, owner = _make_tenant(db, "emit4-tenant", "emit4@test.com")
        _create_webhook(db, tenant, events=["message.received", "message.sent"])
        _create_webhook(db, tenant, events=["message.received"])

        count = emit_event(tenant.id, "message.received", {}, db)
        assert count == 2


# ────────────────────────────────────────────────────────────────────────────
# 5. Dispatcher — deliver_webhook
# ────────────────────────────────────────────────────────────────────────────


class TestDeliverWebhook:
    def _make_delivery(self, db, tenant: Tenant, wh: WebhookOut) -> WebhookDelivery:
        now = datetime.now(timezone.utc)
        d = WebhookDelivery(
            tenant_id=tenant.id,
            webhook_id=wh.id,
            event="message.received",
            payload={"test": True},
            status="pending",
            attempts=0,
            next_attempt_at=now,
        )
        db.add(d)
        db.flush()
        return d

    def test_delivery_exitoso(self, db):
        tenant, owner = _make_tenant(db, "del1-tenant", "del1@test.com")
        wh = _create_webhook(db, tenant)
        delivery = self._make_delivery(db, tenant, wh)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        result = deliver_webhook(delivery, wh, db, http_client=mock_client)

        assert result is True
        assert delivery.status == "success"
        assert delivery.attempts == 1
        assert delivery.next_attempt_at is None
        db.refresh(wh)
        assert wh.consecutive_failures == 0
        assert wh.last_success_at is not None

    def test_delivery_fallido_programa_reintento(self, db):
        tenant, owner = _make_tenant(db, "del2-tenant", "del2@test.com")
        wh = _create_webhook(db, tenant)
        delivery = self._make_delivery(db, tenant, wh)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        result = deliver_webhook(delivery, wh, db, http_client=mock_client)

        assert result is False
        assert delivery.status == "failed"
        assert delivery.attempts == 1
        # Primer reintento: 60s
        assert delivery.next_attempt_at is not None
        delta = delivery.next_attempt_at - datetime.now(timezone.utc)
        assert 50 <= delta.total_seconds() <= 70

        db.refresh(wh)
        assert wh.consecutive_failures == 1
        assert wh.last_failure_at is not None

    def test_delivery_error_http_programa_reintento(self, db):
        tenant, owner = _make_tenant(db, "del3-tenant", "del3@test.com")
        wh = _create_webhook(db, tenant)
        delivery = self._make_delivery(db, tenant, wh)

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        result = deliver_webhook(delivery, wh, db, http_client=mock_client)

        assert result is False
        assert delivery.status == "failed"
        assert "Connection refused" in delivery.last_response_body

    def test_delivery_dead_tras_max_intentos(self, db):
        tenant, owner = _make_tenant(db, "del4-tenant", "del4@test.com")
        wh = _create_webhook(db, tenant)
        delivery = self._make_delivery(db, tenant, wh)

        # Simular que ya tiene MAX_ATTEMPTS - 1 intentos previos
        delivery.attempts = MAX_ATTEMPTS - 1
        delivery.status = "failed"
        db.flush()

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        result = deliver_webhook(delivery, wh, db, http_client=mock_client)

        assert result is False
        assert delivery.status == "dead"
        assert delivery.next_attempt_at is None
        assert delivery.attempts == MAX_ATTEMPTS

    def test_schedule_de_reintentos_correcto(self, db):
        """Verifica que los delays de reintento siguen la secuencia definida."""
        tenant, owner = _make_tenant(db, "del5-tenant", "del5@test.com")
        wh = _create_webhook(db, tenant)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "err"
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        expected_delays = RETRY_DELAYS  # [60, 300, 1800, 7200, 43200, 86400]

        for i, expected_delay in enumerate(expected_delays):
            now = datetime.now(timezone.utc)
            delivery = WebhookDelivery(
                tenant_id=tenant.id,
                webhook_id=wh.id,
                event="message.received",
                payload={},
                status="pending" if i == 0 else "failed",
                attempts=i,
                next_attempt_at=now,
            )
            db.add(delivery)
            db.flush()

            deliver_webhook(delivery, wh, db, http_client=mock_client)

            if i < MAX_ATTEMPTS - 1:
                delta = delivery.next_attempt_at - now
                assert abs(delta.total_seconds() - expected_delay) < 5, (
                    f"Intento {i+1}: esperado {expected_delay}s, "
                    f"obtenido {delta.total_seconds():.0f}s"
                )


# ────────────────────────────────────────────────────────────────────────────
# 6. Dispatcher — process_due_deliveries
# ────────────────────────────────────────────────────────────────────────────


class TestProcessDueDeliveries:
    def test_procesa_deliveries_pendientes_vencidos(self, db):
        tenant, owner = _make_tenant(db, "proc1-tenant", "proc1@test.com")
        wh = _create_webhook(db, tenant)

        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        delivery = WebhookDelivery(
            tenant_id=tenant.id,
            webhook_id=wh.id,
            event="message.received",
            payload={"x": 1},
            status="pending",
            attempts=0,
            next_attempt_at=past,
        )
        db.add(delivery)
        db.flush()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        processed = process_due_deliveries(db, http_client=mock_client)
        assert processed == 1
        assert delivery.status == "success"

    def test_no_procesa_deliveries_futuros(self, db):
        tenant, owner = _make_tenant(db, "proc2-tenant", "proc2@test.com")
        wh = _create_webhook(db, tenant)

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        delivery = WebhookDelivery(
            tenant_id=tenant.id,
            webhook_id=wh.id,
            event="message.received",
            payload={},
            status="pending",
            attempts=0,
            next_attempt_at=future,
        )
        db.add(delivery)
        db.flush()

        mock_client = MagicMock(spec=httpx.Client)
        processed = process_due_deliveries(db, http_client=mock_client)
        assert processed == 0
        mock_client.post.assert_not_called()

    def test_no_procesa_deliveries_dead_o_success(self, db):
        tenant, owner = _make_tenant(db, "proc3-tenant", "proc3@test.com")
        wh = _create_webhook(db, tenant)
        past = datetime.now(timezone.utc) - timedelta(seconds=1)

        for status in ("success", "dead"):
            d = WebhookDelivery(
                tenant_id=tenant.id,
                webhook_id=wh.id,
                event="message.received",
                payload={},
                status=status,
                attempts=1,
                next_attempt_at=past,
            )
            db.add(d)
        db.flush()

        mock_client = MagicMock(spec=httpx.Client)
        processed = process_due_deliveries(db, http_client=mock_client)
        assert processed == 0


# ────────────────────────────────────────────────────────────────────────────
# 7. Aislamiento multi-tenant
# ────────────────────────────────────────────────────────────────────────────


class TestAislamiento:
    def test_keys_tenant_a_no_visibles_para_tenant_b(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        _raw, _key = _create_api_key(db, _tenant_a, owner_a)

        resp = client.get("/v1/api-keys", headers=_auth(owner_b))
        assert resp.status_code == 200
        ids = [k["id"] for k in resp.json()]
        assert str(_key.id) not in ids

    def test_get_key_ajena_devuelve_404(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        _raw, key_a = _create_api_key(db, _tenant_a, owner_a)

        resp = client.get(f"/v1/api-keys/{key_a.id}", headers=_auth(owner_b))
        assert resp.status_code == 404

    def test_revocar_key_ajena_devuelve_404(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        _raw, key_a = _create_api_key(db, _tenant_a, owner_a)

        resp = client.delete(f"/v1/api-keys/{key_a.id}", headers=_auth(owner_b))
        assert resp.status_code == 404

    def test_webhooks_tenant_a_no_visibles_para_tenant_b(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        wh_a = _create_webhook(db, _tenant_a)

        resp = client.get("/v1/webhooks", headers=_auth(owner_b))
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert str(wh_a.id) not in ids

    def test_get_webhook_ajeno_devuelve_404(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        wh_a = _create_webhook(db, _tenant_a)

        resp = client.get(f"/v1/webhooks/{wh_a.id}", headers=_auth(owner_b))
        assert resp.status_code == 404

    def test_eliminar_webhook_ajeno_devuelve_404(self, client, tenant_a, tenant_b, db):
        _tenant_a, owner_a = tenant_a
        _tenant_b, owner_b = tenant_b
        wh_a = _create_webhook(db, _tenant_a)

        resp = client.delete(f"/v1/webhooks/{wh_a.id}", headers=_auth(owner_b))
        assert resp.status_code == 404

    def test_emit_event_no_afecta_webhooks_de_otro_tenant(self, db):
        tenant_a, owner_a = _make_tenant(db, "aisla1-a", "aisla1a@test.com")
        tenant_b, owner_b = _make_tenant(db, "aisla1-b", "aisla1b@test.com")

        _create_webhook(db, tenant_a, events=["message.received"])
        _create_webhook(db, tenant_b, events=["message.received"])

        # Emitir solo para tenant_a
        count = emit_event(tenant_a.id, "message.received", {}, db)
        assert count == 1

        # Solo hay una delivery, y pertenece a tenant_a
        deliveries = db.query(WebhookDelivery).all()
        assert len(deliveries) == 1
        assert deliveries[0].tenant_id == tenant_a.id
