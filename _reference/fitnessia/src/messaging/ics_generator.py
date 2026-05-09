"""Generador de archivos .ics (iCalendar RFC 5545).

Genera texto plano sin dependencias externas. Compatible con
Google Calendar, Outlook, Apple Calendar.
"""

from datetime import datetime


def _fold_line(line: str) -> str:
    """RFC 5545: líneas > 75 octetos deben plegarse."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts = []
    while len(line.encode("utf-8")) > 75:
        # Cortar en 75 bytes respetando caracteres multibyte
        cut = 75
        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1
        parts.append(line[:cut])
        line = line[cut:]
    parts.append(line)
    return "\r\n ".join(parts)


def _fmt_dt(dt: datetime) -> str:
    """Formatea datetime a formato iCalendar UTC."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def generate_ics(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    description: str = "",
    location: str = "",
    organizer_email: str = "",
    organizer_name: str = "",
    attendee_email: str = "",
    attendee_name: str = "",
    sequence: int = 0,
) -> str:
    """Genera un archivo .ics con METHOD:REQUEST y recordatorios VALARM."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Fitness BI//Demo Scheduler//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt_dt(datetime.utcnow())}",
        f"DTSTART:{_fmt_dt(dtstart)}",
        f"DTEND:{_fmt_dt(dtend)}",
        _fold_line(f"SUMMARY:{summary}"),
        f"SEQUENCE:{sequence}",
        "STATUS:CONFIRMED",
    ]

    if description:
        # Escapar caracteres especiales en descripción
        desc = description.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
        lines.append(_fold_line(f"DESCRIPTION:{desc}"))

    if location:
        lines.append(_fold_line(f"LOCATION:{location}"))

    if organizer_email:
        org_cn = f';CN="{organizer_name}"' if organizer_name else ""
        lines.append(f"ORGANIZER{org_cn}:mailto:{organizer_email}")

    if attendee_email:
        att_cn = f';CN="{attendee_name}"' if attendee_name else ""
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION{att_cn}:mailto:{attendee_email}")

    # Recordatorio 24 horas antes
    lines.extend([
        "BEGIN:VALARM",
        "TRIGGER:-PT24H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:Demo en 24 horas: {summary}",
        "END:VALARM",
    ])

    # Recordatorio 1 hora antes
    lines.extend([
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:Demo en 1 hora: {summary}",
        "END:VALARM",
    ])

    lines.extend([
        "END:VEVENT",
        "END:VCALENDAR",
    ])

    return "\r\n".join(lines) + "\r\n"


def generate_cancel_ics(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    organizer_email: str = "",
    organizer_name: str = "",
    attendee_email: str = "",
    sequence: int = 1,
) -> str:
    """Genera un .ics con METHOD:CANCEL para eliminar el evento del calendario."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Fitness BI//Demo Scheduler//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt_dt(datetime.utcnow())}",
        f"DTSTART:{_fmt_dt(dtstart)}",
        f"DTEND:{_fmt_dt(dtend)}",
        _fold_line(f"SUMMARY:CANCELADA: {summary}"),
        f"SEQUENCE:{sequence}",
        "STATUS:CANCELLED",
    ]

    if organizer_email:
        org_cn = f';CN="{organizer_name}"' if organizer_name else ""
        lines.append(f"ORGANIZER{org_cn}:mailto:{organizer_email}")

    if attendee_email:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:{attendee_email}")

    lines.extend([
        "END:VEVENT",
        "END:VCALENDAR",
    ])

    return "\r\n".join(lines) + "\r\n"
