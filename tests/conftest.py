import pytest

@pytest.fixture()
def app(tmp_path):
    """App Flask con base SQLite aislada por test."""
    db_path = tmp_path / "test_master.db"
    from app.config import Config
    Config.DB_PATH = str(db_path)

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
