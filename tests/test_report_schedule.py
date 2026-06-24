"""Envío programado del informe empresarial."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.report_delivery import get_report_recipients, weekly_report_skip_reason


def test_get_report_recipients_prefers_empresa_email():
    emp = {
        "email": "jefe@empresa.com",
        "reporte_email_destinatarios": "legacy@old.com",
    }
    assert get_report_recipients(emp) == ["jefe@empresa.com"]


def test_get_report_recipients_legacy_fallback():
    emp = {"reporte_email_destinatarios": "legacy@old.com"}
    assert get_report_recipients(emp) == ["legacy@old.com"]


def test_weekly_report_skip_not_monday():
    emp = {"reporte_email_hora": "05:00"}
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    # Martes 24/06/2025 05:00
    fake = datetime(2025, 6, 24, 5, 0, tzinfo=tz)
    with patch("app.services.report_delivery.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        reason = weekly_report_skip_reason(emp)
    assert reason is not None
    assert "lunes" in reason.lower()


def test_weekly_report_ok_monday_at_five():
    emp = {"reporte_email_hora": "05:00"}
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    # Lunes 23/06/2025 05:00
    fake = datetime(2025, 6, 23, 5, 0, tzinfo=tz)
    with patch("app.services.report_delivery.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        assert weekly_report_skip_reason(emp) is None
