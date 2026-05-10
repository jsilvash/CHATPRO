"""Tests del prompt builder: persona/locale/horario comercial."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.agent.prompt_builder import build_system_prompt, is_within_business_hours


def _make_persona(**kwargs) -> SimpleNamespace:
    """Crea un objeto Persona-like sin tocar la BD (usa SimpleNamespace)."""
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "Test Bot",
        "system_prompt": "Eres un asistente de prueba.",
        "tone": "amigable",
        "locale": "es-CL",
        "timezone": "America/Santiago",
        "out_of_hours_message": "Estamos fuera de horario.",
        "business_hours_json": {},
        "model_id": "claude-sonnet-4-6",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestBuildSystemPrompt:
    def test_incluye_system_prompt_personalizado(self):
        p = _make_persona(system_prompt="Eres Valentina, asistente de ventas.")
        prompt = build_system_prompt(p)
        assert "Valentina" in prompt

    def test_tono_amigable(self):
        p = _make_persona(tone="amigable")
        prompt = build_system_prompt(p)
        assert "amigable" in prompt.lower() or "empático" in prompt.lower()

    def test_tono_formal(self):
        p = _make_persona(tone="formal")
        prompt = build_system_prompt(p)
        assert "formal" in prompt.lower() or "profesional" in prompt.lower()

    def test_tono_neutral(self):
        p = _make_persona(tone="neutral")
        prompt = build_system_prompt(p)
        assert "neutro" in prompt.lower() or "objetivo" in prompt.lower()

    def test_incluye_locale(self):
        p = _make_persona(locale="en-US")
        prompt = build_system_prompt(p)
        assert "en-US" in prompt

    def test_incluye_timezone(self):
        p = _make_persona(timezone="America/Mexico_City")
        prompt = build_system_prompt(p)
        assert "America/Mexico_City" in prompt

    def test_sin_system_prompt_personalizado(self):
        p = _make_persona(system_prompt="")
        prompt = build_system_prompt(p)
        assert prompt  # debe tener algo (tone + locale + tz)

    def test_incluye_instruccion_texto_plano(self):
        p = _make_persona()
        prompt = build_system_prompt(p)
        assert "texto plano" in prompt.lower() or "markdown" in prompt.lower()


class TestIsWithinBusinessHours:
    def _santiago_tz(self):
        return ZoneInfo("America/Santiago")

    def test_sin_horario_configurado_siempre_disponible(self):
        p = _make_persona(business_hours_json={})
        assert is_within_business_hours(p) is True

    def test_horario_none_siempre_disponible(self):
        p = _make_persona(business_hours_json=None)
        assert is_within_business_hours(p) is True

    def test_dentro_de_horario_lunes_a_viernes(self):
        # Simula martes 10:00 Santiago
        tz = ZoneInfo("America/Santiago")
        fake_now = datetime(2026, 5, 5, 10, 0, 0, tzinfo=tz)  # martes
        bh = {"tz": "America/Santiago", "days": {"mon-fri": ["09:00", "18:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is True

    def test_fuera_de_horario_fin_de_semana(self):
        # Simula sábado 10:00 Santiago — no cubre el rango mon-fri
        tz = ZoneInfo("America/Santiago")
        fake_now = datetime(2026, 5, 9, 10, 0, 0, tzinfo=tz)  # sábado
        bh = {"tz": "America/Santiago", "days": {"mon-fri": ["09:00", "18:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is False

    def test_fuera_de_horario_antes_de_apertura(self):
        tz = ZoneInfo("America/Santiago")
        fake_now = datetime(2026, 5, 5, 8, 0, 0, tzinfo=tz)  # martes 08:00
        bh = {"tz": "America/Santiago", "days": {"mon-fri": ["09:00", "18:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is False

    def test_fuera_de_horario_despues_de_cierre(self):
        tz = ZoneInfo("America/Santiago")
        fake_now = datetime(2026, 5, 5, 19, 0, 0, tzinfo=tz)  # martes 19:00
        bh = {"tz": "America/Santiago", "days": {"mon-fri": ["09:00", "18:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is False

    def test_sabado_cubierto_por_rango_mon_sat(self):
        tz = ZoneInfo("America/Santiago")
        fake_now = datetime(2026, 5, 9, 11, 0, 0, tzinfo=tz)  # sábado 11:00
        bh = {"tz": "America/Santiago", "days": {"mon-sat": ["09:00", "14:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is True

    def test_dias_individuales(self):
        tz = ZoneInfo("America/Santiago")
        # Domingo
        fake_now = datetime(2026, 5, 10, 11, 0, 0, tzinfo=tz)  # domingo 11:00
        bh = {"tz": "America/Santiago", "days": {"sun": ["10:00", "14:00"]}}
        p = _make_persona(business_hours_json=bh)
        with patch("src.agent.prompt_builder.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = is_within_business_hours(p)
        assert result is True

    def test_timezone_invalido_usa_utc(self):
        bh = {"tz": "Invalid/Timezone", "days": {"mon-fri": ["00:00", "23:59"]}}
        p = _make_persona(business_hours_json=bh)
        # No debe explotar — cae a UTC y aplica horario
        result = is_within_business_hours(p)
        assert isinstance(result, bool)

    def test_horario_malformado_horas_invalidas(self):
        bh = {"tz": "America/Santiago", "days": {"mon-fri": ["bad", "value"]}}
        p = _make_persona(business_hours_json=bh)
        result = is_within_business_hours(p)
        assert result is False  # ningún rango aplica → fuera de horario
