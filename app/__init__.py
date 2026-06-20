import os

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.config import Config
from app.database import init_db
from app.security import generate_secret, register_security


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config["SECRET_KEY"]:
        if app.config["TESTING"]:
            app.config["SECRET_KEY"] = "test-secret-key"
        elif app.config["DEBUG"]:
            app.config["SECRET_KEY"] = generate_secret()
        else:
            app.logger.warning(
                "SECRET_KEY no configurada en producción. Generando temporal. "
                "Definila en variables de entorno para persistir sesiones."
            )
            app.config["SECRET_KEY"] = generate_secret()

    register_security(app)

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per minute"],
        storage_uri="memory://",
    )
    app.extensions["limiter"] = limiter

    with app.app_context():
        init_db()

    from app.routes.views import views_bp
    from app.routes.api import api_bp

    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp)

    limiter.limit("10 per minute")(app.view_functions["views.auth_login_json"])
    limiter.limit("5 per minute")(app.view_functions["views.login"])

    if not app.config["TESTING"] and not app.config["DEBUG"]:
        if not Config.MT_API_KEY:
            app.logger.warning(
                "MT_API_KEY no configurada: la API queda abierta. "
                "Configure MT_API_KEY antes de exponer a internet."
            )
        if not Config.AUDIT_DELETE_PASSWORD:
            app.logger.warning(
                "AUDIT_DELETE_PASSWORD no configurada: "
                "no se podrán eliminar registros de auditoría."
            )

    return app
