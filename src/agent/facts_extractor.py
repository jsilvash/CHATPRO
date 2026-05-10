"""Extractor de hechos persistentes del contacto (Fase 4).

Analiza los últimos N mensajes de una conversación con Claude Haiku y extrae
hechos clave/valor sobre el contacto (nombre real, preferencias, productos
mencionados, restricciones, etc.).  El resultado se upsertea en ``contact_facts``.

Idempotencia: la restricción UNIQUE(tenant_id, contact_id, key) garantiza que
re-ejecutar el extractor no duplica hechos.  Solo actualiza si la nueva
confidence supera la registrada.

Versión del prompt: v1
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from src.contacts.models import Contact, ContactFact
from src.wa.models import WaConversation, WaMessage

logger = logging.getLogger(__name__)

_FACTS_PROMPT_VERSION = "v1"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_MESSAGES_FOR_EXTRACTION = 30
_MIN_CONFIDENCE = 0.7

_EXTRACTION_SYSTEM = (
    "Sos un extractor de información estructurada. Analizá la conversación de WhatsApp "
    "y extraé hechos concretos sobre el CLIENTE (no sobre el bot). "
    "Devolvé ÚNICAMENTE un array JSON con objetos de la forma: "
    '[{"key": "slug_en_minusculas", "value": "valor", "value_type": "text|number|date|bool|json", '
    '"confidence": 0.0-1.0}]. '
    "Solo hechos concretos y verificables (nombre real, preferencias, restricciones, "
    "productos mencionados, datos de contacto, etc.). "
    "NO inventes datos. NO incluyas opiniones ni inferencias débiles. "
    "Filtrá hechos con confidence < 0.7. "
    "Si no hay hechos claros, devolvé un array vacío []."
)


class _ExtractedFact(BaseModel):
    key: str
    value: str
    value_type: str = "text"
    confidence: float = 0.7

    @field_validator("key")
    @classmethod
    def _normalize_key(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")[:80]

    @field_validator("value_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        allowed = {"text", "number", "date", "bool", "json"}
        return v if v in allowed else "text"

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


def get_or_create_contact(db: Session, tenant_id: uuid.UUID, phone: str, name: str = "") -> Contact:
    """Devuelve el Contact existente o crea uno nuevo para el teléfono dado."""
    from src.tenancy.context import bypass_tenant_filter

    with bypass_tenant_filter():
        contact = (
            db.query(Contact)
            .filter(Contact.tenant_id == tenant_id, Contact.phone_e164 == phone)
            .first()
        )
    if contact is None:
        contact = Contact(
            tenant_id=tenant_id,
            phone_e164=phone,
            display_name=name or None,
        )
        db.add(contact)
        db.flush()
    elif name and not contact.display_name:
        contact.display_name = name
        db.add(contact)
        db.flush()
    return contact


def extract_contact_facts(db: Session, conv: WaConversation) -> None:
    """Extrae hechos del contacto a partir de la conversación y los upsertea.

    Si falla por cualquier motivo, loguea y retorna silenciosamente.
    """
    try:
        _do_extract(db, conv)
    except Exception:
        logger.exception("facts_extractor: falló para conv=%s", conv.id)


def _do_extract(db: Session, conv: WaConversation) -> None:
    from src.agent.llm import call_claude
    from src.tenancy.context import bypass_tenant_filter

    # Obtener o crear el Contact asociado a la conversación.
    contact = get_or_create_contact(
        db, conv.tenant_id, conv.wa_contact_phone, conv.wa_contact_name
    )

    # Cargar últimos mensajes.
    with bypass_tenant_filter():
        rows = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.text != "",
            )
            .order_by(WaMessage.created_at.desc())
            .limit(_MAX_MESSAGES_FOR_EXTRACTION)
            .all()
        )
    rows = list(reversed(rows))

    if not rows:
        return

    # Cargar hechos previos para contexto (evita re-extraer lo ya conocido).
    with bypass_tenant_filter():
        existing_facts: list[ContactFact] = (
            db.query(ContactFact)
            .filter(
                ContactFact.tenant_id == conv.tenant_id,
                ContactFact.contact_id == contact.id,
            )
            .all()
        )

    # Construir transcript.
    lines: list[str] = []
    if existing_facts:
        known = ", ".join(f"{f.key}={f.value_text}" for f in existing_facts)
        lines.append(f"[Hechos ya conocidos: {known}]\n")

    for row in rows:
        who = "Cliente" if row.direction == "in" else "Bot"
        lines.append(f"{who}: {row.text}")

    transcript = "\n".join(lines)
    messages = [{"role": "user", "content": transcript}]

    raw_text, _ = call_claude(
        _EXTRACTION_SYSTEM,
        messages,
        model_id=_HAIKU_MODEL,
        max_tokens=512,
    )

    facts = _parse_facts(raw_text)
    if not facts:
        return

    # Upsert: actualizar solo si nueva confidence > existente.
    existing_map = {f.key: f for f in existing_facts}

    for fact in facts:
        if fact.confidence < _MIN_CONFIDENCE:
            continue

        existing = existing_map.get(fact.key)
        new_confidence = Decimal(str(fact.confidence))

        if existing is not None:
            if existing.confidence is not None and new_confidence <= existing.confidence:
                continue
            existing.value_text = fact.value
            existing.value_type = fact.value_type
            existing.confidence = new_confidence
            existing.source = "extracted"
            db.add(existing)
        else:
            cf = ContactFact(
                tenant_id=conv.tenant_id,
                contact_id=contact.id,
                key=fact.key,
                value_text=fact.value,
                value_type=fact.value_type,
                source="extracted",
                confidence=new_confidence,
            )
            db.add(cf)

    db.commit()


def _parse_facts(raw: str) -> list[_ExtractedFact]:
    """Parsea el JSON devuelto por Haiku; tolera texto envuelto en ```json."""
    text = raw.strip()
    if "```" in text:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("facts_extractor: respuesta no es JSON válido: %r", raw[:200])
        return []

    if not isinstance(data, list):
        return []

    result: list[_ExtractedFact] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            result.append(_ExtractedFact(**item))
        except Exception:
            continue
    return result


def load_top_facts(
    db: Session,
    tenant_id: uuid.UUID,
    phone: str,
    top_k: int = 10,
) -> list[ContactFact]:
    """Carga los top-K hechos del contacto ordenados por confidence desc.

    Ranking basado en el plan §6d: confidence (0.4) + manual_boost (source='manual').
    """
    from src.tenancy.context import bypass_tenant_filter

    with bypass_tenant_filter():
        contact = (
            db.query(Contact)
            .filter(Contact.tenant_id == tenant_id, Contact.phone_e164 == phone)
            .first()
        )
    if contact is None:
        return []

    with bypass_tenant_filter():
        facts = (
            db.query(ContactFact)
            .filter(
                ContactFact.tenant_id == tenant_id,
                ContactFact.contact_id == contact.id,
            )
            .order_by(
                ContactFact.confidence.desc().nullslast(),
                ContactFact.updated_at.desc(),
            )
            .limit(top_k)
            .all()
        )
    return facts
