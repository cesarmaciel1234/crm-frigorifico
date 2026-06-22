import pytest

from app.config import Config


@pytest.fixture()
def app(tmp_path):
    """App Flask con base SQLite aislada por test."""
    db_path = tmp_path / "test_master.db"
    Config.DB_PATH = str(db_path)
    Config.TESTING = True
    Config.MT_API_KEY = ""
    Config.MASTER_PASSWORD = "test-master-pw"
    Config.AUDIT_DELETE_PASSWORD = "test-master-pw"
    Config.SECRET_KEY = "test-secret-key"

    from app import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    from app.database import get_db

    with get_db() as conn:
        yield conn


@pytest.fixture()
def auth_client(app):
    """Cliente con autenticación habilitada."""
    Config.MT_API_KEY = "test-api-key-secure"
    Config.MASTER_PASSWORD = "test-audit-pw"
    Config.AUDIT_DELETE_PASSWORD = "test-audit-pw"
    client = app.test_client()
    client.environ_base["HTTP_X_API_KEY"] = "test-api-key-secure"
    return client
