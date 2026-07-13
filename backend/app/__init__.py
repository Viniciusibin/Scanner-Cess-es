from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS

from .api.routes import api_bp
from .core.config import Settings
from .services.cessao_store import FileBackedCessaoStore
from .services.diario_runner import DiarioRunner

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def create_app() -> Flask:
    settings = Settings()

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["SETTINGS"] = settings
    app.config["STORE"] = FileBackedCessaoStore(settings.database_dir)
    app.config["DIARIO_RUNNER"] = DiarioRunner()

    CORS(app, resources={r"/api/*": {"origins": settings.cors_origins}})
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def frontend_assets(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    return app
