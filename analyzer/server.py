"""analyzer/server.py — Flask API server for the STS2 dashboard."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, render_template, request

from .parser import load_run_files
from .stats import (
    compute_ancients,
    compute_cards,
    compute_diagnostic,
    compute_encounters,
    compute_events,
    compute_overview,
    compute_potions,
    compute_relics,
    compute_rest_sites,
    compute_run_detail,
    compute_runs_list,
    filter_runs,
    get_ancients,
    get_ascensions,
    get_characters,
)


def create_app(history_path: str) -> Flask:
    root = os.path.dirname(os.path.dirname(__file__))
    template_dir = os.path.join(root, "templates")
    static_dir = os.path.join(root, "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["HISTORY_PATH"] = history_path

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load() -> list:
        return load_run_files(app.config["HISTORY_PATH"])

    def _bool(value: str | None, default: bool = True) -> bool:
        if value is None:
            return default
        return value.lower() not in ("false", "0", "no", "off")

    def _filters() -> dict[str, Any]:
        raw_asc = request.args.get("ascension")
        ascension = int(raw_asc) if raw_asc is not None and raw_asc != "" else None
        return {
            "character": request.args.get("character") or None,
            "ascension": ascension,
            "ancient": request.args.get("ancient") or None,
            "exclude_multiplayer": _bool(request.args.get("exclude_multiplayer"), True),
            "exclude_abandoned": _bool(request.args.get("exclude_abandoned"), True),
        }

    # ── Frontend ──────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("index.html")

    # ── API ───────────────────────────────────────────────────────────────────

    @app.route("/api/diagnostic")
    def api_diagnostic():
        all_runs = _load()
        return jsonify(compute_diagnostic(all_runs))

    @app.route("/api/characters")
    def api_characters():
        all_runs = _load()
        return jsonify(get_characters(all_runs))

    @app.route("/api/ascensions")
    def api_ascensions():
        all_runs = _load()
        return jsonify(get_ascensions(all_runs))

    @app.route("/api/ancients")
    def api_ancients():
        all_runs = _load()
        return jsonify(get_ancients(all_runs))

    @app.route("/api/ancient_stats")
    def api_ancient_stats():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_ancients(runs))

    @app.route("/api/overview")
    def api_overview():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_overview(runs))

    @app.route("/api/cards")
    def api_cards():
        min_offered = int(request.args.get("min_samples", 1))
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_cards(runs, min_offered=min_offered))

    @app.route("/api/relics")
    def api_relics():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_relics(runs))

    @app.route("/api/potions")
    def api_potions():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_potions(runs))

    @app.route("/api/encounters")
    def api_encounters():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_encounters(runs))

    @app.route("/api/rest_sites")
    def api_rest_sites():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_rest_sites(runs))

    @app.route("/api/events")
    def api_events():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_events(runs))

    @app.route("/api/runs")
    def api_runs():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_runs_list(runs))

    @app.route("/api/run/<path:filename>")
    def api_run_detail(filename: str):
        all_runs = _load()
        for run in all_runs:
            if run.get("_filename") == filename:
                return jsonify(compute_run_detail(run))
        return jsonify({"error": "Run not found"}), 404

    return app
