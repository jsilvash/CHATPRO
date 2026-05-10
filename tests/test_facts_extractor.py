"""Tests del extractor de hechos del contacto (Fase 4)."""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.agent.facts_extractor import (
    _parse_facts,
    extract_contact_facts,
    get_or_create_contact,
    load_top_facts,
)
from src.contacts.models import Contact, ContactFact
from src.tenancy.context import tenant_scope
from src.wa.models import WaConversation, WaMessage


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _make_conversation(db, tenant_id, number_id):
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=number_id,
        wa_contact_phone="56912345678",
        wa_contact_name="Juan Test",
        turn_count=5,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_message(db, conv, direction="in", text="Hola"):
    msg = WaMessage(
        tenant_id=conv.tenant_id,
        wa_number_id=conv.wa_number_id,
        wa_conversation_id=conv.id,
        direction=direction,
        text=text,
    )
    db.add(msg)
    db.flush()
    return msg


# ────────────────────────────────────────────────────────────
# Tests _parse_facts
# ────────────────────────────────────────────────────────────


def test_parse_facts_valid_json():
    raw = '[{"key": "nombre_real", "value": "Juan", "value_type": "text", "confidence": 0.9}]'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0].key == "nombre_real"
    assert facts[0].value == "Juan"
    assert facts[0].confidence == 0.9


def test_parse_facts_json_in_markdown_block():
    raw = '```json\n[{"key": "vegano", "value": "true", "value_type": "bool", "confidence": 0.85}]\n```'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0].key == "vegano"


def test_parse_facts_empty_array():
    assert _parse_facts("[]") == []


def test_parse_facts_invalid_json():
    assert _parse_facts("no es json") == []


def test_parse_facts_key_normalized():
    raw = '[{"key": "  Nombre Real  ", "value": "Ana", "confidence": 0.8}]'
    facts = _parse_facts(raw)
    assert facts[0].key == "nombre_real"


def test_parse_facts_confidence_clamped():
    raw = '[{"key": "foo", "value": "bar", "confidence": 1.5}]'
    facts = _parse_facts(raw)
    assert facts[0].confidence == 1.0


def test_parse_facts_invalid_value_type_defaults_to_text():
    raw = '[{"key": "foo", "value": "bar", "value_type": "blob", "confidence": 0.9}]'
    facts = _parse_facts(raw)
    assert facts[0].value_type == "text"


# ────────────────────────────────────────────────────────────
# Tests get_or_create_contact
# ────────────────────────────────────────────────────────────


def test_get_or_create_contact_creates_new(db, tenant_a, client_a):
    tenant, owner = tenant_a
    with tenant_scope(tenant.id):
        contact = get_or_create_contact(db, tenant.id, "56911111111", "María")
    assert contact.id is not None
    assert contact.phone_e164 == "56911111111"
    assert contact.display_name == "María"


def test_get_or_create_contact_returns_existing(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        c1 = get_or_create_contact(db, tenant.id, "56922222222")
        c2 = get_or_create_contact(db, tenant.id, "56922222222")
    assert c1.id == c2.id


def test_get_or_create_contact_updates_name_if_missing(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        c1 = get_or_create_contact(db, tenant.id, "56933333333")
        assert c1.display_name is None
        c2 = get_or_create_contact(db, tenant.id, "56933333333", "Pedro")
    assert c2.display_name == "Pedro"
    assert c1.id == c2.id


# ────────────────────────────────────────────────────────────
# Tests extract_contact_facts
# ────────────────────────────────────────────────────────────


def test_extract_facts_upserts_new_facts(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        from src.db.models import User
        from src.wa.models import WaNumber
        wn = WaNumber(tenant_id=tenant.id, label="Test", waha_session_name=f"ses_{uuid.uuid4().hex[:8]}")
        db.add(wn)
        db.flush()
        conv = _make_conversation(db, tenant.id, wn.id)
        _make_message(db, conv, "in", "Me llamo Carlos, soy vegano")

    haiku_response = '[{"key": "nombre_real", "value": "Carlos", "value_type": "text", "confidence": 0.92}]'

    with patch("src.agent.llm.call_claude", return_value=(haiku_response, {})):
        with tenant_scope(tenant.id):
            extract_contact_facts(db, conv)

    from src.tenancy.context import bypass_tenant_filter
    with bypass_tenant_filter():
        contact = db.query(Contact).filter(
            Contact.tenant_id == tenant.id,
            Contact.phone_e164 == conv.wa_contact_phone,
        ).first()
    assert contact is not None

    with bypass_tenant_filter():
        facts = db.query(ContactFact).filter(
            ContactFact.contact_id == contact.id,
        ).all()
    assert any(f.key == "nombre_real" and f.value_text == "Carlos" for f in facts)


def test_extract_facts_skips_low_confidence(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        from src.wa.models import WaNumber
        wn = WaNumber(tenant_id=tenant.id, label="Test", waha_session_name=f"ses_{uuid.uuid4().hex[:8]}")
        db.add(wn)
        db.flush()
        conv = _make_conversation(db, tenant.id, wn.id)
        _make_message(db, conv, "in", "Quizás me llamo Juan")

    haiku_response = '[{"key": "nombre_real", "value": "Juan", "value_type": "text", "confidence": 0.5}]'

    with patch("src.agent.llm.call_claude", return_value=(haiku_response, {})):
        with tenant_scope(tenant.id):
            extract_contact_facts(db, conv)

    from src.tenancy.context import bypass_tenant_filter
    with bypass_tenant_filter():
        contact = db.query(Contact).filter(
            Contact.tenant_id == tenant.id,
            Contact.phone_e164 == conv.wa_contact_phone,
        ).first()

    if contact is None:
        return  # No se creó contact porque no había hechos válidos

    with bypass_tenant_filter():
        facts = db.query(ContactFact).filter(
            ContactFact.contact_id == contact.id,
            ContactFact.key == "nombre_real",
        ).all()
    assert len(facts) == 0


def test_extract_facts_does_not_overwrite_higher_confidence(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        from src.wa.models import WaNumber
        wn = WaNumber(tenant_id=tenant.id, label="Test", waha_session_name=f"ses_{uuid.uuid4().hex[:8]}")
        db.add(wn)
        db.flush()
        conv = _make_conversation(db, tenant.id, wn.id)
        _make_message(db, conv, "in", "Soy vegano")

        # Pre-crear un hecho con alta confidence.
        contact = get_or_create_contact(db, tenant.id, conv.wa_contact_phone)
        existing = ContactFact(
            tenant_id=tenant.id,
            contact_id=contact.id,
            key="dieta",
            value_text="vegano",
            value_type="text",
            source="extracted",
            confidence=Decimal("0.95"),
        )
        db.add(existing)
        db.flush()

    haiku_response = '[{"key": "dieta", "value": "omnivoro", "value_type": "text", "confidence": 0.75}]'

    with patch("src.agent.llm.call_claude", return_value=(haiku_response, {})):
        with tenant_scope(tenant.id):
            extract_contact_facts(db, conv)

    from src.tenancy.context import bypass_tenant_filter
    with bypass_tenant_filter():
        fact = db.query(ContactFact).filter(
            ContactFact.contact_id == contact.id,
            ContactFact.key == "dieta",
        ).first()
    assert fact.value_text == "vegano"  # no fue reemplazado


def test_extract_facts_silent_on_llm_error(db, tenant_a):
    """Si la llamada al LLM falla, extract_contact_facts no propaga la excepción."""
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        from src.wa.models import WaNumber
        wn = WaNumber(tenant_id=tenant.id, label="Test", waha_session_name=f"ses_{uuid.uuid4().hex[:8]}")
        db.add(wn)
        db.flush()
        conv = _make_conversation(db, tenant.id, wn.id)

    with patch("src.agent.llm.call_claude", side_effect=RuntimeError("boom")):
        extract_contact_facts(db, conv)  # debe ser silencioso


# ────────────────────────────────────────────────────────────
# Tests load_top_facts
# ────────────────────────────────────────────────────────────


def test_load_top_facts_returns_empty_for_unknown_phone(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        facts = load_top_facts(db, tenant.id, "00000000000")
    assert facts == []


def test_load_top_facts_respects_top_k(db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = get_or_create_contact(db, tenant.id, "56944444444")
        for i in range(5):
            db.add(ContactFact(
                tenant_id=tenant.id,
                contact_id=contact.id,
                key=f"hecho_{i}",
                value_text=f"valor_{i}",
                value_type="text",
                source="extracted",
                confidence=Decimal(f"0.{70 + i}"),
            ))
        db.flush()
        facts = load_top_facts(db, tenant.id, "56944444444", top_k=3)
    assert len(facts) == 3
