"""Registro auditable de mensajes enviados.

Este módulo es la **única puerta** para escribir en la tabla ``messages_sent``.
Su propósito es evitar el bug histórico en el que un fallo de proveedor
(WhatsApp/email devuelve ``None``) quedaba registrado como ``status='sent'``
con ``external_id=''`` — un mensaje fantasma que nunca llegó.

Contrato
--------
- ``status='sent'`` requiere un ``external_id`` no vacío (wamid o message-id).
- Si no hay ``external_id``, el helper guarda ``status='failed'`` automáticamente.
- El canal ``internal`` (tareas para el equipo) no envía mensaje real; se
  registra como ``status='task_created'``.

Uso típico
----------
    from src.messaging.audit import record_message_sent

    wamid = send_whatsapp_text(phone, text, tenant_config=ts)
    record_message_sent(
        db,
        tenant_id=tenant_id,
        client_id=client.external_id,
        flow_type="reactivacion_fechas",
        step="cumpleanos",
        channel="whatsapp",
        template="reactivacion_cumpleanos",
        external_id=wamid,  # puede ser None; el helper decide el status
    )
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.db.models import MessageSent

logger = logging.getLogger(__name__)

# Estados canónicos de ``messages_sent.status``.
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_TASK_CREATED = "task_created"  # canal "internal": tarea para el equipo
STATUS_UNKNOWN = "unknown"  # sólo para backfill de datos históricos
STATUS_ABANDONED = "abandoned"  # Centro de Automatizaciones — RETRY-1: 3+ fallos consecutivos


def record_message_sent(
    db: Session,
    *,
    tenant_id: int,
    client_id: str,
    flow_type: str,
    step: str,
    channel: str,
    template: str = "",
    external_id: str | None = None,
    status: str | None = None,
    last_error: str | None = None,
) -> MessageSent:
    """Registra un envío en ``messages_sent`` derivando el ``status`` correcto.

    Args:
        db: sesión SQLAlchemy. El helper hace ``db.add(...)`` pero NO commitea.
        tenant_id: id del tenant.
        client_id: external_id del cliente (string).
        flow_type: flujo que generó el envío (``renovacion``, ``inactividad``…).
        step: paso del flujo (``t_menos_7``, ``cumpleanos``…).
        channel: ``whatsapp``, ``email`` o ``internal``.
        template: nombre de template o alias (``text``, ``text:ai``…).
        external_id: id retornado por el proveedor. ``None`` o ``""`` significa
            que el envío falló (el proveedor no aceptó el mensaje, o no hay
            credenciales). En ese caso el helper registra ``status='failed'``.
        status: forzar un status específico. Úselo sólo si el llamante tiene
            información adicional (ej: ``STATUS_TASK_CREATED`` para tareas).
            Si ``status='sent'`` pero ``external_id`` es vacío, se eleva
            ``ValueError`` para evitar escribir un mensaje fantasma.
        last_error: detalle del fallo del proveedor (Meta/SendGrid). Se
            persiste en ``messages_sent.last_error`` para post-mortems.
            Truncado a 480 caracteres por el modelo.

    Returns:
        La instancia ``MessageSent`` ya agregada a la sesión.

    Raises:
        ValueError: si se intenta forzar ``status='sent'`` sin ``external_id``.
    """
    ext_id = external_id or ""

    if status is None:
        if channel == "internal":
            status = STATUS_TASK_CREATED
        elif ext_id:
            status = STATUS_SENT
        else:
            status = STATUS_FAILED
            logger.info(
                "Mensaje no enviado: tenant=%s client=%s flow=%s step=%s channel=%s "
                "error=%s",
                tenant_id, client_id, flow_type, step, channel, last_error or "n/a",
            )

    if status == STATUS_SENT and not ext_id:
        raise ValueError(
            f"record_message_sent: status='sent' requiere external_id no vacío "
            f"(tenant={tenant_id} client={client_id} flow={flow_type} step={step})"
        )

    msg = MessageSent(
        tenant_id=tenant_id,
        client_id=client_id,
        flow_type=flow_type,
        step=step,
        channel=channel,
        template=template,
        external_id=ext_id,
        status=status,
    )
    # last_error sólo cuando el envío falló y tenemos detalle del proveedor.
    if last_error and status in (STATUS_FAILED, STATUS_ABANDONED):
        msg.last_error = last_error[:480]
    db.add(msg)
    return msg
