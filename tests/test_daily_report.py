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
    assert report["version"] == "informe_empresarial_v2"
    assert "resumen" in report
    assert "balance" in report
    assert "resumen_ejecutivo" in report
    assert "operacional" in report
    assert "antiguedad" in report
    assert "clientes_a_cobrar" in report
    assert "obligaciones_a_pagar" in report
    assert "vencimientos_vencidos" in report
    assert "top_cfr" in report
    assert report["resumen"]["total_a_cobrar"] >= 5000
    assert len(report["clientes_a_cobrar"]) >= 1
    assert len(report["resumen_ejecutivo"]) >= 1


def test_render_html_and_pdf(tenant_db):
    report = build_daily_report()
    html_doc = render_daily_report_html(report)
    assert "Informe Empresarial" in html_doc
    assert "Resumen ejecutivo" in html_doc
    assert "Clientes a cobrar" in html_doc
    assert "Antigüedad de deuda" in html_doc

    pdf = render_daily_report_pdf(report)
    assert pdf[:4] == b"%PDF"

    texto = whatsapp_summary_text(report)
    assert "Informe Empresarial" in texto
    assert "Patrimonio" in texto
