"""analyzer/server.py — Flask API server for the STS2 dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Optional

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
    compute_shop_stats,
    filter_runs,
    get_ancients,
    get_ascensions,
    get_characters,
)


def _ids_from_image_dir(image_dir: str) -> List[str]:
    """Return sorted uppercase IDs derived from PNG filenames in *image_dir*.

    e.g. ``akabeko.png`` → ``AKABEKO``
    """
    d = Path(image_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem.upper() for p in d.glob("*.png"))


def _ids_from_card_data(card_data_file: str) -> List[str]:
    """Return sorted card IDs from the scraped ``card_data.json`` file."""
    try:
        with open(card_data_file, encoding="utf-8") as f:
            data = json.load(f)
        return sorted(data.keys())
    except Exception:  # noqa: BLE001
        return []


def create_app(history_path: str) -> Flask:
    root = os.path.dirname(os.path.dirname(__file__))
    template_dir = os.path.join(root, "templates")
    static_dir = os.path.join(root, "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["HISTORY_PATH"] = history_path

    # ── Run-file cache ────────────────────────────────────────────────────────
    # Re-loading hundreds of .run files on every API request is expensive.
    # We cache the result and only invalidate when the history directory's
    # modification time changes (i.e. a new .run file has been added/removed).
    _cache: dict = {"runs": [], "mtime": -1.0}

    def _dir_mtime(path: str) -> float:
        """Return the latest mtime of any file in the directory tree."""
        try:
            mtimes = [
                os.path.getmtime(os.path.join(dp, f))
                for dp, _dirs, files in os.walk(path)
                for f in files
                if f.endswith(".run") and not f.endswith(".run.backup")
            ]
            return max(mtimes) if mtimes else 0.0
        except OSError:
            return 0.0

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load() -> list:
        current_mtime = _dir_mtime(app.config["HISTORY_PATH"])
        if current_mtime != _cache["mtime"]:
            _cache["runs"] = load_run_files(app.config["HISTORY_PATH"])
            _cache["mtime"] = current_mtime
        return _cache["runs"]

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

    def _known_cards() -> Optional[List[str]]:
        """IDs from card_data.json (populated by the scraper)."""
        ids = _ids_from_card_data(os.path.join(static_dir, "card_images", "card_data.json"))
        return ids or None

    def _known_relics() -> Optional[List[str]]:
        """IDs from relic_data.json (if available), else from downloaded image filenames."""
        ids = _ids_from_card_data(os.path.join(static_dir, "relic_images", "relic_data.json"))
        return ids or (_ids_from_image_dir(os.path.join(static_dir, "relic_images")) or None)

    def _known_potions() -> Optional[List[str]]:
        """IDs from potion_data.json (if available), else from downloaded image filenames."""
        ids = _ids_from_card_data(os.path.join(static_dir, "potion_images", "potion_data.json"))
        return ids or (_ids_from_image_dir(os.path.join(static_dir, "potion_images")) or None)

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
        min_picked = int(request.args.get("min_picked", 1))
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_cards(runs, min_picked=min_picked, known_cards=_known_cards()))

    @app.route("/api/relics")
    def api_relics():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_relics(runs, known_relics=_known_relics()))

    @app.route("/api/potions")
    def api_potions():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_potions(runs, known_potions=_known_potions()))

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

    @app.route("/api/shops")
    def api_shops():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_shop_stats(runs))

    @app.route("/api/runs")
    def api_runs():
        runs = filter_runs(_load(), **_filters())
        return jsonify(compute_runs_list(runs))

    @app.route("/api/card_data")
    def api_card_data():
        """Return scraped card metadata from card_data.json (empty dict if not yet scraped)."""
        data_file = os.path.join(static_dir, "card_images", "card_data.json")
        try:
            with open(data_file, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except FileNotFoundError:
            return jsonify({})
        except Exception:
            return jsonify({"error": "Failed to load card data"}), 500

    @app.route("/api/relic_data")
    def api_relic_data():
        """Return scraped relic metadata from relic_data.json (empty dict if not yet scraped)."""
        data_file = os.path.join(static_dir, "relic_images", "relic_data.json")
        try:
            with open(data_file, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except FileNotFoundError:
            return jsonify({})
        except Exception:
            return jsonify({"error": "Failed to load relic data"}), 500

    @app.route("/api/potion_data")
    def api_potion_data():
        """Return scraped potion metadata from potion_data.json (empty dict if not yet scraped)."""
        data_file = os.path.join(static_dir, "potion_images", "potion_data.json")
        try:
            with open(data_file, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except FileNotFoundError:
            return jsonify({})
        except Exception:
            return jsonify({"error": "Failed to load potion data"}), 500

    @app.route("/api/run/<path:filename>")
    def api_run_detail(filename: str):
        all_runs = _load()
        for run in all_runs:
            if run.get("_filename") == filename:
                return jsonify(compute_run_detail(run))
        return jsonify({"error": "Run not found"}), 404

    return app
