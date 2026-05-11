"""Lógica de negocio para verificar si el momento actual está en horario de atención."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.office_hours.models import OfficeHours
from src.tenancy.context import bypass_tenant_filter


def check_office_hours(
    db: Session,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID,
) -> tuple[bool, str]:
    """Verifica si el momento actual está dentro del horario de atención configurado.

    Retorna ``(within_hours, out_of_hours_message)``.

    Reglas:
    - Si no hay registros activos → (True, "") — sin restricción, bot siempre atiende.
    - Se prefieren registros específicos del número sobre los globales del tenant.
    - Si existe al menos un registro aplicable que cubre el momento actual → (True, "").
    - Si existe al menos un registro aplicable pero ninguno cubre el momento → (False, msg).
    """
    now = datetime.now(UTC)
    current_day = now.weekday()   # 0=lunes, 6=domingo
    current_hour = now.hour

    with bypass_tenant_filter():
        all_active: list[OfficeHours] = list(
            db.query(OfficeHours)
            .filter(
                OfficeHours.tenant_id == tenant_id,
                OfficeHours.is_active.is_(True),
                (OfficeHours.wa_number_id == wa_number_id)
                | (OfficeHours.wa_number_id.is_(None)),
            )
            .all()
        )

    if not all_active:
        return True, ""

    # Registros específicos del número tienen prioridad sobre los globales.
    number_specific = [r for r in all_active if r.wa_number_id == wa_number_id]
    applicable = number_specific if number_specific else all_active

    for rec in applicable:
        if (
            rec.day_of_week == current_day
            and rec.hour_start <= current_hour < rec.hour_end
        ):
            return True, ""

    # Fuera de horario: usar el primer mensaje no vacío de los registros aplicables.
    ooh_msg = next(
        (r.out_of_hours_message for r in applicable if r.out_of_hours_message), ""
    )
    return False, ooh_msg
