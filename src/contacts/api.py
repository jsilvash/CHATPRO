"""API REST de contactos y hechos (Fase 4).

Endpoints:
- GET    /v1/contacts                          — lista contactos del tenant
- GET    /v1/contacts/{id}                     — detalle de un contacto
- PATCH  /v1/contacts/{id}                     — edita datos base
- GET    /v1/contacts/{id}/facts               — lista hechos del contacto
- PUT    /v1/contacts/{id}/facts/{key}         — upsert de un hecho (manual)
- DELETE /v1/contacts/{id}/facts/{key}         — elimina un hecho
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.contacts.models import Contact, ContactFact
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter

router = APIRouter(prefix="/contacts", tags=["contacts"])


# ────────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────────


class ContactOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    phone_e164: str
    display_name: str | None
    first_name: str | None
    last_name: str | None
    email: str | None
    locale: str | None
    opt_in_marketing: bool

    model_config = {"from_attributes": True}


class ContactPatch(BaseModel):
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    locale: str | None = None
    opt_in_marketing: bool | None = None


class FactOut(BaseModel):
    id: uuid.UUID
    key: str
    value_text: str | None
    value_type: str
    source: str
    confidence: float | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, obj: ContactFact) -> "FactOut":
        return cls(
            id=obj.id,
            key=obj.key,
            value_text=obj.value_text,
            value_type=obj.value_type,
            source=obj.source,
            confidence=float(obj.confidence) if obj.confidence is not None else None,
        )


class FactUpsert(BaseModel):
    value_text: str
    value_type: str = "text"


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _get_contact_or_404(
    contact_id: uuid.UUID, current_user: User, db: Session
) -> Contact:
    with bypass_tenant_filter():
        contact = (
            db.query(Contact)
            .filter(
                Contact.id == contact_id,
                Contact.tenant_id == current_user.tenant_id,
            )
            .first()
        )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")
    return contact


def _get_fact_or_404(
    contact: Contact, key: str, db: Session
) -> ContactFact:
    with bypass_tenant_filter():
        fact = (
            db.query(ContactFact)
            .filter(
                ContactFact.contact_id == contact.id,
                ContactFact.tenant_id == contact.tenant_id,
                ContactFact.key == key,
            )
            .first()
        )
    if fact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hecho no encontrado")
    return fact


# ────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────


@router.get("", response_model=list[ContactOut])
def list_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    with bypass_tenant_filter():
        contacts = (
            db.query(Contact)
            .filter(Contact.tenant_id == current_user.tenant_id)
            .order_by(Contact.created_at.desc())
            .limit(200)
            .all()
        )
    return contacts


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(
    contact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_contact_or_404(contact_id, current_user, db)


@router.patch("/{contact_id}", response_model=ContactOut)
def patch_contact(
    contact_id: uuid.UUID,
    body: ContactPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _get_contact_or_404(contact_id, current_user, db)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)

    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/{contact_id}/facts", response_model=list[FactOut])
def list_facts(
    contact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _get_contact_or_404(contact_id, current_user, db)
    with bypass_tenant_filter():
        facts = (
            db.query(ContactFact)
            .filter(
                ContactFact.contact_id == contact.id,
                ContactFact.tenant_id == contact.tenant_id,
            )
            .order_by(ContactFact.key)
            .all()
        )
    return [FactOut.from_orm(f) for f in facts]


@router.put("/{contact_id}/facts/{key}", response_model=FactOut, status_code=status.HTTP_200_OK)
def upsert_fact(
    contact_id: uuid.UUID,
    key: str,
    body: FactUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _get_contact_or_404(contact_id, current_user, db)
    key = key.strip().lower()

    with bypass_tenant_filter():
        existing = (
            db.query(ContactFact)
            .filter(
                ContactFact.contact_id == contact.id,
                ContactFact.tenant_id == contact.tenant_id,
                ContactFact.key == key,
            )
            .first()
        )

    if existing is not None:
        existing.value_text = body.value_text
        existing.value_type = body.value_type
        existing.source = "manual"
        existing.confidence = None
        existing.created_by_user_id = current_user.id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return FactOut.from_orm(existing)

    fact = ContactFact(
        tenant_id=contact.tenant_id,
        contact_id=contact.id,
        key=key,
        value_text=body.value_text,
        value_type=body.value_type,
        source="manual",
        confidence=None,
        created_by_user_id=current_user.id,
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    return FactOut.from_orm(fact)


@router.delete("/{contact_id}/facts/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(
    contact_id: uuid.UUID,
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _get_contact_or_404(contact_id, current_user, db)
    fact = _get_fact_or_404(contact, key.strip().lower(), db)
    db.delete(fact)
    db.commit()
