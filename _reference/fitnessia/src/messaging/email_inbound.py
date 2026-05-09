"""Email inbound — captura respuestas de clientes y las vuelca al CRM.

Flujo:
1. Cuando el sistema envía un email con `case_id` (o `tenant_id + to_email`),
   `build_reply_address(...)` genera una dirección única con token HMAC:
   `reply+case-{id}.{token}@{reply_domain}`.
2. Ese `Reply-To` se inyecta en el header del email saliente.
3. El cliente responde → su MTA manda el correo a la dirección de reply.
4. El proveedor de inbound (SendGrid Inbound Parse, Postmark, Mailgun, etc.)
   hace POST al webhook `/webhook/email` con el cuerpo del mensaje.
5. `process_inbound_email` valida el token, resuelve el contacto y registra
   la interacción en el timeline omnicanal (`ContactInteraction`).

Seguridad: el token HMAC firma `(case_id, tenant_id)` con `email_reply_secret`
(fallback a `jwt_secret`). Un atacante no puede inyectar interacciones a un
caso arbitrario sin conocer el secret.
"""

from __future__ import annotations

import base64
import hmac
import logging
import re
from datetime import datetime
from hashlib import sha256
from typing import Optional

from src.config import get_settings

logger = logging.getLogger(__name__)

_REPLY_RE = re.compile(r"reply\+case-(\d+)\.([A-Za-z0-9_\-]+)@")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _secret() -> bytes:
    s = get_settings()
    raw = s.email_reply_secret or s.jwt_secret or "synex-reply"
    return raw.encode("utf-8")


def _sign(case_id: int, tenant_id: int) -> str:
    msg = f"{tenant_id}:{case_id}".encode("utf-8")
    mac = hmac.new(_secret(), msg, sha256).digest()
    # URL-safe b64 corto (12 bytes = 16 chars) — suficiente para resistir
    # adivinanza y mantener direcciones legibles.
    return base64.urlsafe_b64encode(mac[:12]).decode("ascii").rstrip("=")


def build_reply_address(case_id: int, tenant_id: int, reply_domain: str = "") -> Optional[str]:
    """Construye la dirección Reply-To con token firmado para este caso."""
    domain = reply_domain or get_settings().email_reply_domain
    if not domain or not case_id:
        return None
    token = _sign(case_id, tenant_id)
    return f"reply+case-{case_id}.{token}@{domain}"


def parse_reply_address(address: str) -> Optional[tuple[int, str]]:
    """Extrae (case_id, token) de una dirección tipo reply+case-N.TOKEN@."""
    if not address:
        return None
    m = _REPLY_RE.search(address)
    if not m:
        return None
    try:
        return int(m.group(1)), m.group(2)
    except ValueError:
        return None


def verify_reply_token(case_id: int, tenant_id: int, token: str) -> bool:
    expected = _sign(case_id, tenant_id)
    return hmac.compare_digest(expected, token)


def _extract_email(field: str) -> str:
    """Extrae solo la dirección email de un header 'Nombre <a@b>'."""
    if not field:
        return ""
    m = _EMAIL_RE.search(field)
    return (m.group(0) if m else field.strip()).lower()


def _strip_quoted_reply(text: str) -> str:
    """Mejor esfuerzo: corta citas ('On ... wrote:', '>'-prefixed)."""
    if not text:
        return ""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if stripped.lower().startswith("on ") and "wrote:" in stripped.lower():
            break
        if stripped.startswith("-----") and "original message" in stripped.lower():
            break
        out.append(line)
    return "\n".join(out).strip()


def _resolve_tenant_from_recipient(db, recipient: str):
    """Mapea el destinatario (`bajas@gym.com`, `contacto@gym.com`, …) al tenant.

    Estrategia:
    1. Match exacto contra `tenant.settings_json.email.aliases` (claves del
       dict son direcciones completas).
    2. Match por dominio contra `tenant.settings_json.email.from_email`
       (si el From oficial del tenant es `hola@gym.com`, todos los emails
       a `*@gym.com` se atribuyen a este tenant).
    3. Si solo hay 1 tenant activo, usarlo (despliegues single-tenant).

    Devuelve `Tenant | None`. La capa caller decide qué hacer con None.
    """
    import json
    from src.db.models import Tenant
    from src.db.tenant_context import bypass_tenant_filter

    if not recipient or "@" not in recipient:
        return None
    addr = recipient.strip().lower()
    domain = addr.split("@", 1)[1]

    with bypass_tenant_filter():
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()  # noqa: E712

    # 1) Match por alias exacto
    for t in tenants:
        try:
            s = json.loads(t.settings_json or "{}")
        except Exception:
            continue
        aliases = (s.get("email") or {}).get("aliases") or {}
        if addr in {k.lower() for k in aliases.keys()}:
            return t

    # 2) Match por dominio del from_email del tenant
    for t in tenants:
        try:
            s = json.loads(t.settings_json or "{}")
        except Exception:
            continue
        fe = (s.get("email") or {}).get("from_email", "").lower()
        if fe and "@" in fe and fe.split("@", 1)[1] == domain:
            return t

    # 3) Single-tenant fallback
    if len(tenants) == 1:
        return tenants[0]

    return None


def _route_unknown_email(
    db,
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body_text: str,
    in_reply_to: str,
    references: str,
) -> dict:
    """Email sin token (frío o con reply-to perdido). Resolver tenant +
    contacto + case y registrar interacción."""
    import json
    from src.db.models import Case, Lead
    from src.db.tenant_context import tenant_scope
    from src.engine.case_registry import record_interaction
    from src.engine.contact_resolver import (
        ResolvedContact,
        resolve_by_email,
        upsert_channel_identity,
    )
    from src.messaging.email_classifier import classify_email

    result = {"status": "ignored", "reason": ""}

    target = (to_address or "").strip().lower()
    sender = _extract_email(from_address)

    tenant = _resolve_tenant_from_recipient(db, target)
    if not tenant:
        result["reason"] = "tenant_not_found"
        logger.warning("Inbound email a %s: no se pudo mapear a tenant", target)
        return result

    try:
        tenant_settings = json.loads(tenant.settings_json or "{}")
    except Exception:
        tenant_settings = {}

    # Clasificar tema (alias > keywords > IA > default)
    case_type, module, confidence, source = classify_email(
        subject, body_text, to_address=target, tenant_settings=tenant_settings,
    )

    with tenant_scope(tenant.id):
        # Resolver remitente
        resolved = resolve_by_email(db, tenant.id, sender)

        # Si el contacto YA existe y tiene un caso abierto del mismo tema,
        # apendear ahí (no abrir uno nuevo).
        existing_case = None
        if resolved.is_known:
            q = db.query(Case).filter(
                Case.tenant_id == tenant.id,
                Case.status.in_(("open", "waiting", "snoozed")),
            )
            if resolved.client_id:
                q = q.filter(Case.client_id == resolved.client_id)
            elif resolved.lead_id:
                q = q.filter(Case.lead_id == resolved.lead_id)
            # Preferimos un caso del mismo módulo si existe.
            same_module = q.filter(Case.module == module).order_by(Case.updated_at.desc()).first()
            existing_case = same_module or q.order_by(Case.updated_at.desc()).first()

        # Si no hay contacto, crear Lead mínimo
        if not resolved.is_known:
            lead = Lead(
                tenant_id=tenant.id,
                name=sender.split("@", 1)[0] if sender else "Email entrante",
                email=sender or None,
                source="email_inbound",
            )
            db.add(lead)
            db.flush()
            resolved = ResolvedContact(
                tenant_id=tenant.id,
                contact_type="lead",
                lead_id=lead.id,
                email=sender,
            )

        # Crear caso nuevo si no había uno apto
        if not existing_case:
            new_case = Case(
                tenant_id=tenant.id,
                contact_type=resolved.contact_type,
                lead_id=resolved.lead_id,
                client_id=resolved.client_id,
                contact_email=sender,
                case_type=case_type,
                module=module,
                stage="nuevo",
                status="open",
                priority=2,
                source_channel="email",
            )
            db.add(new_case)
            db.flush()
            existing_case = new_case
            logger.info(
                "Email inbound creó Case #%d (%s/%s, source=%s, conf=%.2f) para tenant=%d",
                new_case.id, case_type, module, source, confidence, tenant.id,
            )
        else:
            logger.info(
                "Email inbound apendido a Case #%d existente (%s/%s) para tenant=%d",
                existing_case.id, existing_case.case_type, existing_case.module, tenant.id,
            )

        # Identidad email
        if sender:
            upsert_channel_identity(
                db, tenant.id, "email", sender,
                lead_id=resolved.lead_id, client_id=resolved.client_id,
                commit=False,
            )

        record_interaction(
            db,
            tenant_id=tenant.id,
            resolved=resolved,
            channel="email",
            direction="inbound",
            summary=subject or "(sin asunto)",
            case_id=existing_case.id,
            payload_kind="email_message",
            payload_ref_id=in_reply_to or "",
            metadata={
                "from": sender,
                "to": target,
                "subject": subject,
                "body_text": body_text,
                "in_reply_to": in_reply_to,
                "references": references,
                "classifier_source": source,
                "classifier_confidence": confidence,
            },
            commit=False,
        )

        if existing_case.status == "snoozed":
            existing_case.status = "open"
            existing_case.snooze_until = None
        existing_case.updated_at = datetime.utcnow()

        db.commit()

    result["status"] = "ok"
    result["case_id"] = existing_case.id
    result["tenant_id"] = tenant.id
    result["matched_contact"] = resolved.contact_type
    result["routing"] = {
        "via": "from_match" if resolved.is_known else "new_lead",
        "classifier_source": source,
        "classifier_confidence": confidence,
        "case_type": case_type,
        "module": module,
    }
    return result


def process_inbound_email(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    text_body: str = "",
    html_body: str = "",
    in_reply_to: str = "",
    references: str = "",
    envelope_to: str = "",
) -> dict:
    """Procesa un email entrante.

    Decisión de ruteo:

    1. **Token Reply-To válido** → append al case original (lifecycle o
       support, da igual). Es el camino más confiable.
    2. **Sin token / token inválido** → fallback robusto:
       - Resuelve tenant por destinatario (alias exacto > dominio > single).
       - Resuelve contacto por From email.
       - Si hay caso abierto del mismo módulo → append.
       - Si no, clasifica por keywords/alias/IA → crea Case nuevo.
       - Si no hay contacto → crea Lead nuevo + Case nuevo.

    Returns dict con metadata del procesamiento (debugging/auditoría).
    Nunca raises: errores se loggean y devuelven `status=error`.
    """
    result = {"status": "ignored", "reason": ""}
    try:
        # Preferir envelope_to (el destinatario REAL en SMTP) sobre el To:
        # (el header puede tener varias direcciones y no reflejar la ruta).
        target = envelope_to or to_address or ""
        parsed = parse_reply_address(target)

        from src.db.models import Case
        from src.db.session import SessionLocal
        from src.db.tenant_context import bypass_tenant_filter, tenant_scope
        from src.engine.case_registry import record_interaction
        from src.engine.contact_resolver import (
            ResolvedContact,
            resolve_by_email,
            upsert_channel_identity,
        )

        db = SessionLocal()
        try:
            # ── Camino 1: Reply-To token válido ──────────────────────────
            if not parsed:
                # Sin token → fallback al ruteo robusto (Camino 2).
                logger.info("Inbound email a %s sin token, usando fallback routing", target)
                return _route_unknown_email(
                    db,
                    from_address=from_address,
                    to_address=target,
                    subject=subject,
                    body_text=(_strip_quoted_reply(text_body or "") or (html_body or ""))[:4000],
                    in_reply_to=in_reply_to,
                    references=references,
                )

            case_id, token = parsed

            with bypass_tenant_filter():
                case = db.query(Case).filter(Case.id == case_id).first()
            if not case:
                # Token apunta a caso inexistente → fallback en lugar de descartar.
                logger.warning("Token apunta a case=%d inexistente, fallback routing", case_id)
                return _route_unknown_email(
                    db,
                    from_address=from_address, to_address=target, subject=subject,
                    body_text=(_strip_quoted_reply(text_body or "") or (html_body or ""))[:4000],
                    in_reply_to=in_reply_to, references=references,
                )

            if not verify_reply_token(case_id, case.tenant_id, token):
                result["reason"] = "bad_token"
                logger.warning("Reply token mismatch for case=%d", case_id)
                return result

            sender = _extract_email(from_address)
            body_text = _strip_quoted_reply(text_body or "") or (html_body or "")
            body_text = body_text[:4000]

            with tenant_scope(case.tenant_id):
                resolved = resolve_by_email(db, case.tenant_id, sender)
                if not resolved.is_known and case.client_id:
                    # Caso ya está ligado a un cliente/lead — reutilizamos ese contacto
                    resolved = ResolvedContact(
                        tenant_id=case.tenant_id,
                        contact_type="client",
                        client_id=case.client_id,
                        email=sender,
                    )
                elif not resolved.is_known and case.lead_id:
                    resolved = ResolvedContact(
                        tenant_id=case.tenant_id,
                        contact_type="lead",
                        lead_id=case.lead_id,
                        email=sender,
                    )

                if sender and resolved.is_known:
                    upsert_channel_identity(
                        db, case.tenant_id, "email", sender,
                        lead_id=resolved.lead_id, client_id=resolved.client_id,
                        commit=False,
                    )

                record_interaction(
                    db,
                    tenant_id=case.tenant_id,
                    resolved=resolved,
                    channel="email",
                    direction="inbound",
                    summary=f"Re: {subject}\n\n{body_text}" if subject else body_text,
                    case_id=case.id,
                    payload_kind="email_message",
                    payload_ref_id=in_reply_to or "",
                    metadata={
                        "from": sender,
                        "to": target,
                        "subject": subject,
                        "in_reply_to": in_reply_to,
                        "references": references,
                    },
                    commit=False,
                )

                # Si el caso estaba snoozed, despertarlo para atención.
                if case.status == "snoozed":
                    case.status = "open"
                    case.snooze_until = None
                # Marcar que hay respuesta pendiente del cliente.
                case.updated_at = datetime.utcnow()

                db.commit()

            result["status"] = "ok"
            result["case_id"] = case_id
            result["tenant_id"] = case.tenant_id
            result["matched_contact"] = resolved.contact_type
            return result
        finally:
            db.close()

    except Exception as e:
        logger.exception("process_inbound_email failed: %s", e)
        result["status"] = "error"
        result["reason"] = str(e)
        return result


__all__ = [
    "build_reply_address",
    "parse_reply_address",
    "verify_reply_token",
    "process_inbound_email",
]
