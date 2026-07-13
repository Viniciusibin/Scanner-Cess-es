from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.cessao_store import QueryFilters
from ..services.diario_runner import DiarioRunner


api_bp = Blueprint("api", __name__)


def _store():
    return current_app.config["STORE"]


def _diario_runner() -> DiarioRunner:
    return current_app.config["DIARIO_RUNNER"]


@api_bp.route("", methods=["GET"])
@api_bp.route("/", methods=["GET"])
def api_index():
    return jsonify(
        {
            "name": "scanner-cessoes-api",
            "version": "v1",
            "endpoints": {
                "health": "/api/v1/health",
                "meta": "/api/v1/meta",
                "cessoes": "/api/v1/cessoes",
                "diario_rodar": "/api/v1/diario/rodar",
                "diario_status": "/api/v1/diario/status",
            },
        }
    )


@api_bp.get("/health")
def health():
    store = _store()
    summary = store.summary()
    return jsonify(
        {
            "status": "ok",
            "dataset": summary,
        }
    )


@api_bp.get("/meta")
def meta():
    store = _store()
    return jsonify(store.metadata())


@api_bp.get("/cessoes")
def list_cessoes():
    filters = QueryFilters.from_request_args(
        request.args, current_app.config["SETTINGS"]
    )
    payload = _store().query(filters)
    return jsonify(payload)


@api_bp.post("/diario/rodar")
def diario_rodar():
    runner = _diario_runner()
    body = request.get_json(silent=True) or {}
    tribunais = body.get("tribunais")
    dias_lookback_raw = body.get("dias_lookback")
    dias_lookback = int(dias_lookback_raw) if dias_lookback_raw is not None else None

    iniciado = runner.iniciar(tribunais=tribunais, dias_lookback=dias_lookback)
    if not iniciado:
        return jsonify({"status": "ja_rodando", **runner.status()}), 409
    return jsonify({"status": "iniciado", **runner.status()}), 202


@api_bp.get("/diario/status")
def diario_status():
    return jsonify(_diario_runner().status())
