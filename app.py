"""Flask application — Phase 1 + Phase 2a: API connectivity, data health, and analytics."""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify

from backend.config import Config
from backend.data_store import get_summary, meta, refresh_all, store
from backend import analytics

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if Config.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── App factory ──────────────────────────────────────────────────────────
app = Flask(__name__)


# ── Startup hook ─────────────────────────────────────────────────────────
_loaded = False


@app.before_request
def _lazy_load():
    """Load data on the first request (not at import time)."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    problems = Config.validate()
    if problems:
        log.warning("Config problems – data will NOT load:\n  • %s", "\n  • ".join(problems))
        meta["status"] = "config_error"
        meta["errors"] = problems
        return

    log.info("First request – loading data from APIs …")
    try:
        refresh_all()
    except Exception as exc:
        log.exception("Failed to load data on startup")
        meta["status"] = "error"
        meta["errors"].append(str(exc))


# ── Routes ───────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return jsonify({
        "app": "GoVocal + Typeform Admin Dashboard",
        "phase": "2a",
        "status": meta["status"],
        "hint": "Try /api/health, /api/data/summary, or /api/analytics/summary",
    })


@app.route("/api/health")
def health():
    """Quick health-check: shows config validity + data load status."""
    problems = Config.validate()
    return jsonify({
        "config_ok": len(problems) == 0,
        "config_problems": problems,
        "data_status": meta["status"],
        "last_refresh": meta["last_refresh"],
        "errors": meta["errors"],
        "dataframes_loaded": list(store.keys()),
    })


@app.route("/api/data/summary")
def data_summary():
    """Detailed summary: row counts, email overlap, etc."""
    return jsonify(get_summary())


@app.route("/api/data/refresh", methods=["POST"])
def data_refresh():
    """Re-fetch all data from both APIs."""
    problems = Config.validate()
    if problems:
        return jsonify({"error": "config_invalid", "problems": problems}), 400
    try:
        summary = refresh_all()
        return jsonify(summary)
    except Exception as exc:
        log.exception("Refresh failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/data/tables")
def list_tables():
    """List all DataFrames and a few sample rows from each."""
    tables: dict = {}
    for name, df in store.items():
        tables[name] = {
            "rows": len(df),
            "columns": list(df.columns),
            "sample": df.head(3).to_dict(orient="records"),
        }
    return jsonify(tables)


@app.route("/api/data/table/<name>")
def get_table(name: str):
    """Return the full contents of a single DataFrame as JSON."""
    if name not in store:
        return jsonify({"error": f"Table '{name}' not found", "available": list(store.keys())}), 404
    df = store[name]
    return jsonify({
        "name": name,
        "rows": len(df),
        "columns": list(df.columns),
        "data": df.to_dict(orient="records"),
    })


# ── Analytics routes (Phase 2a) ─────────────────────────────────────────


@app.route("/api/analytics/summary")
def analytics_summary():
    """All Phase 2a metrics in one payload."""
    return jsonify(analytics.compute_all())


@app.route("/api/analytics/participants")
def analytics_participants():
    """Total identified + anonymous participants."""
    return jsonify(analytics.compute_total_participants())


@app.route("/api/analytics/actions")
def analytics_actions():
    """Total actions (surveys, ideas, reactions)."""
    return jsonify(analytics.compute_total_actions())


@app.route("/api/analytics/conversion")
def analytics_conversion():
    """Survey → deliberation conversion rate."""
    return jsonify(analytics.compute_conversion_rate())


@app.route("/api/analytics/idea-selections")
def analytics_idea_selections():
    """Idea selection breakdown from Typeform surveys."""
    return jsonify(analytics.compute_idea_selection_breakdown())


@app.route("/api/analytics/participation-breakdown")
def analytics_participation_breakdown():
    """Per-source participation & action counts."""
    return jsonify(analytics.compute_participation_breakdown())


# ── Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
