from flask import Flask
from flask_cors import CORS

from .api.routes import api_bp
from .core.config import Settings
from .services.cessao_store import FileBackedCessaoStore


def create_app() -> Flask:
    settings = Settings()

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["SETTINGS"] = settings
    app.config["STORE"] = FileBackedCessaoStore(settings.database_dir)

    CORS(app, resources={r"/api/*": {"origins": settings.cors_origins}})
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.get("/")
    def root():
        return {
            "name": "scanner-cessoes-api",
            "version": "v1",
            "docs": {
                "health": "/api/v1/health",
                "meta": "/api/v1/meta",
                "cessoes": "/api/v1/cessoes",
            },
        }

    return app
