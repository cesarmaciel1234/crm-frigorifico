from flask import Flask
from app.config import Config
from app.database import init_db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Database
    with app.app_context():
        init_db()

    # Register Blueprints
    from app.routes.views import views_bp
    from app.routes.api import api_bp
    
    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp)

    return app
