"""Flask application — Phase 1–3: API connectivity, data health, analytics, and UI."""

from __future__ import annotations

import atexit
import logging
import os
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from authlib.integrations.flask_client import OAuth
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for

from backend.config import Config
from backend.data_store import get_summary, load_from_cache, meta, refresh_all, refresh_incremental, store
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
app.secret_key = Config.SECRET_KEY or "dev-fallback-key"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)


# ── Background scheduler (periodic data refresh) ─────────────────────────
def _scheduled_refresh() -> None:
    """Called by APScheduler to keep in-memory data fresh."""
    log.info("Scheduled refresh starting (interval=%.1fh) …", Config.REFRESH_INTERVAL_HOURS)
    try:
        idea_analytics.invalidate_cache()
        refresh_incremental()
        log.info("Scheduled refresh complete.")
    except Exception:
        log.exception("Scheduled refresh failed")


_scheduler = BackgroundScheduler(daemon=True)
_scheduler.add_job(
    _scheduled_refresh,
    trigger="interval",
    hours=Config.REFRESH_INTERVAL_HOURS,
    id="data_refresh",
    max_instances=1,   # skip if a previous run is still in progress
    coalesce=True,     # merge missed runs into one
)
# Guard: in Flask dev mode the reloader spawns a child process — only start
# the scheduler in the child (WERKZEUG_RUN_MAIN=true) or in production.
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))

# ── Google OAuth (OpenID Connect) ────────────────────────────────────────
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=Config.GOOGLE_CLIENT_ID,
    client_secret=Config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Email allow-list helper ─────────────────────────────────────────────
def is_email_allowed(email: str) -> bool:
    """Return True if *email* matches the configured allow-lists (OR logic)."""
    email = email.lower().strip()
    if email in Config.ALLOWED_EMAILS:
        return True
    domain = email.rsplit("@", 1)[-1]
    if domain in Config.ALLOWED_DOMAINS:
        return True
    return False


# ── Authentication ───────────────────────────────────────────────────────
_AUTH_EXEMPT = {"/login", "/login/google", "/auth/callback", "/api/health"}


@app.before_request
def _require_login():
    """Redirect unauthenticated users to the login page."""
    path = request.path
    # Allow static assets, login flow, and health check
    if path.startswith("/static/") or path in _AUTH_EXEMPT:
        return None
    if not session.get("user"):
        return redirect(url_for("login"))


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

    # Try loading cached data from disk first, then do an incremental
    # refresh to pick up anything new.  Only fall back to a full refresh
    # if no cache exists.
    try:
        if load_from_cache():
            log.info("Cache loaded – running incremental refresh …")
            refresh_incremental()
        else:
            log.info("No cache – running full refresh …")
            refresh_all()
    except Exception as exc:
        log.exception("Failed to load data on startup")
        meta["status"] = "error"
        meta["errors"].append(str(exc))


# ── Routes ───────────────────────────────────────────────────────────────


@app.route("/login")
def login():
    """Show a login page, or redirect to Google if already in flow."""
    if session.get("user"):
        return redirect("/")
    return send_from_directory(app.static_folder, "login.html")


@app.route("/login/google")
def login_google():
    """Initiate the Google OAuth flow."""
    redirect_uri = url_for("auth_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    """Handle the OAuth callback from Google."""
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.google.userinfo()
    email = userinfo.get("email", "").lower().strip()
    if not email:
        return send_from_directory(app.static_folder, "denied.html"), 403
    if not is_email_allowed(email):
        log.warning("Access denied for email: %s", email)
        return send_from_directory(app.static_folder, "denied.html"), 403
    session.permanent = True
    session["user"] = {"email": email, "name": userinfo.get("name", email)}
    return redirect("/")


@app.route("/logout")
def logout():
    """Clear the session and redirect to login."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/me")
def api_me():
    """Return the currently logged-in user."""
    user = session.get("user")
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify(user)


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
    """Re-fetch data from both APIs.

    By default performs an **incremental** refresh (only new/updated records).
    Pass ``?full=true`` to force a complete re-fetch of all data.
    """
    problems = Config.validate()
    if problems:
        return jsonify({"error": "config_invalid", "problems": problems}), 400
    try:
        idea_analytics.invalidate_cache()
        full = request.args.get("full", "").lower() in ("true", "1", "yes")
        if full:
            summary = refresh_all()
        else:
            summary = refresh_incremental()
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


@app.route("/api/analytics/idea-tags")
def analytics_idea_tags():
    """Idea count by input topic (tag)."""
    return jsonify(analytics.compute_idea_tags_breakdown())


@app.route("/api/analytics/votes-by-tag")
def analytics_votes_by_tag():
    """Upvotes and downvotes aggregated by input topic (tag)."""
    return jsonify(analytics.compute_votes_by_tag())


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
    """Ideas sorted by consensus score (descending). Includes only ideas with a score."""
    all_ideas = idea_analytics.build_idea_view()
    if not all_ideas or not isinstance(all_ideas, list):
        return jsonify([])
    # Filter to ideas that have a consensus score, sort descending
    scored = [i for i in all_ideas if i.get("bridging", {}).get("consensus_score") is not None]
    scored.sort(key=lambda x: x["bridging"]["consensus_score"], reverse=True)
    return jsonify(scored)


@app.route("/api/analytics/participation-timeline")
def analytics_participation_timeline():
    """Daily participation counts by action type (surveys, ideas, reactions)."""
    return jsonify(analytics.compute_participation_timeline())


@app.route("/api/analytics/participation-timeline/by-source")
def analytics_participation_timeline_by_source():
    """Daily new unique participants, broken down by tier."""
    return jsonify(analytics.compute_participants_timeline())


@app.route("/api/analytics/visits")
def analytics_visits():
    """Daily visit counts from GoVocal Insights API."""
    return jsonify(analytics.compute_visits_timeline())


@app.route("/api/analytics/participation-rate")
def analytics_participation_rate():
    """GoVocal Participation Rate over 24h, 36h, and 7 days."""
    return jsonify(analytics.compute_participation_rate())


@app.route("/api/analytics/demographics-baseline")
def analytics_demographics_baseline():
    """Demographic distribution of all voters — used as JSD baseline."""
    return jsonify(idea_analytics.get_population_demographics())

@app.route("/api/debug/scoring")
def debug_scoring():
    """Diagnostic endpoint showing the state of scoring caches."""
    from backend.idea_analytics import (
        _email_demo_cache, _userid_email_map, _user_demo_cache,
        _population_baseline, _population_baseline_built,
        _platform_approval_rate, _platform_approval_rate_built,
        BRIDGING_DIMENSIONS,
    )
    import pandas as pd

    reactions_df = store.get("gv_reactions", pd.DataFrame())
    voter_ids = set()
    if not reactions_df.empty and "user_id" in reactions_df.columns:
        voter_ids = set(reactions_df["user_id"].dropna().unique())

    users_with_demo = sum(
        1 for d in _user_demo_cache.values()
        if any(v is not None for v in d.values())
    )
    voters_with_demo = sum(
        1 for u in voter_ids
        if any(_user_demo_cache.get(u, {}).get(d) is not None for d in BRIDGING_DIMENSIONS)
    )

    return jsonify({
        "email_demo_cache_size": len(_email_demo_cache),
        "userid_email_map_size": len(_userid_email_map),
        "user_demo_cache_size": len(_user_demo_cache),
        "users_with_demographics": users_with_demo,
        "population_baseline_built": _population_baseline_built,
        "population_baseline": {
            dim: {"groups": len(dist), "total_weight": round(sum(dist.values()), 4)}
            for dim, dist in _population_baseline.items()
        },
        "platform_approval_rate": _platform_approval_rate,
        "platform_approval_rate_built": _platform_approval_rate_built,
        "total_reactions": len(reactions_df),
        "unique_voters": len(voter_ids),
        "voters_in_cache": sum(1 for u in voter_ids if u in _user_demo_cache),
        "voters_with_demographics": voters_with_demo,
    })

# ── Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
