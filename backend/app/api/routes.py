from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.cessao_store import QueryFilters


api_bp = Blueprint("api", __name__)


def _store():
    return current_app.config["STORE"]


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
