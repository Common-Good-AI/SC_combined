"""Flask application — Phase 1–3: API connectivity, data health, analytics, and UI."""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, send_from_directory

from backend.config import Config
from backend.data_store import get_summary, meta, refresh_all, store
from backend import analytics
from backend import idea_analytics

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if Config.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── App factory ──────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="frontend", static_url_path="/static")


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
    return send_from_directory(app.static_folder, "index.html")


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
        idea_analytics.invalidate_cache()
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


# ── Idea analytics routes ────────────────────────────────────────────────


@app.route("/api/ideas")
def ideas_list():
    """Unified idea view — all ideation ideas with reactions & demographics."""
    result = idea_analytics.build_idea_view()
    return jsonify(result)


@app.route("/api/ideas/<idea_id>")
def ideas_detail(idea_id: str):
    """Single idea with full reaction & demographic breakdown."""
    result = idea_analytics.build_idea_view(idea_id)
    if result is None:
        return jsonify({"error": f"Idea '{idea_id}' not found"}), 404
    return jsonify(result)


@app.route("/api/ideas/bridging")
def ideas_by_bridging():
    """Ideas sorted by bridging score (descending). Includes only ideas with a score."""
    all_ideas = idea_analytics.build_idea_view()
    if not all_ideas or not isinstance(all_ideas, list):
        return jsonify([])
    # Filter to ideas that have a bridging score, sort descending
    scored = [i for i in all_ideas if i.get("bridging", {}).get("bridging_score") is not None]
    scored.sort(key=lambda x: x["bridging"]["bridging_score"], reverse=True)
    return jsonify(scored)


@app.route("/api/analytics/participation-timeline")
def analytics_participation_timeline():
    """Daily participation counts by action type (surveys, ideas, reactions)."""
    return jsonify(analytics.compute_participation_timeline())


@app.route("/api/analytics/participation-timeline/by-source")
def analytics_participation_timeline_by_source():
    """Daily new unique participants, broken down by tier."""
    return jsonify(analytics.compute_participants_timeline())


# ── Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
