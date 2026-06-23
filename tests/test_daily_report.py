"""Tests del informe diario ejecutivo."""
import pytest

from app.config import Config
from app.database import init_db
from app.services.clientes import registrar_cliente
from app.services.daily_report import build_daily_report
from app.services.report_delivery import render_daily_report_html, render_daily_report_pdf, whatsapp_summary_text


@pytest.fixture()
def tenant_db(tmp_path):
    Config.DB_PATH = str(tmp_path / "daily_report_test.db")
    Config.TESTING = True
    Config.DATABASE_URL = ""
    init_db()
    yield


def test_build_daily_report_structure(tenant_db):
    registrar_cliente("Carniceria Test", 100000, "A", saldo_inicial=5000)
    report = build_daily_report()
    assert report["version"] == "informe_diario_v1"
    assert "resumen" in report
    assert "clientes_a_cobrar" in report
    assert "obligaciones_a_pagar" in report
    assert report["resumen"]["total_a_cobrar"] >= 5000
    assert len(report["clientes_a_cobrar"]) >= 1


def test_render_html_and_pdf(tenant_db):
    report = build_daily_report()
    html_doc = render_daily_report_html(report)
    assert "Informe ejecutivo diario" in html_doc
    assert "Clientes a cobrar" in html_doc

    pdf = render_daily_report_pdf(report)
    assert pdf[:4] == b"%PDF"

    texto = whatsapp_summary_text(report)
    assert "Informe diario" in texto
    assert "A cobrar" in texto
