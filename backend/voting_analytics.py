"""Voting-phase analytics for the Issue Selection phase.

Analyses basket/vote data for the GoVocal voting phase and computes:
- Per-issue vote percentages
- Top-X coverage (what % of voters have at least 1 vote in top X)
- Voter demographic breakdowns (age, race, political lean, region)
- Typeform survey completion counts
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from backend.api_client.typeform_api import TypeformClient
from backend.data_store import store
from backend.idea_analytics import (
    _build_email_demo_cache,
    _build_userid_email_map,
    _cache_lock,
    _strip_html,
    BRIDGING_DIMENSIONS,
)
import backend.idea_analytics as _ia

log = logging.getLogger(__name__)

VOTING_PHASE_ID = "ac56dfd7-63c3-4c55-ac8e-9c41186c4e5a"


def _submitted_baskets() -> pd.DataFrame:
    """Return baskets for the voting phase that have been submitted."""
    baskets = store.get("gv_baskets", pd.DataFrame())
    if baskets.empty:
        return pd.DataFrame()
    phase_baskets = baskets[baskets["phase_id"] == VOTING_PHASE_ID].copy()
    # Only count submitted baskets (submitted_at is not null)
    return phase_baskets[phase_baskets["submitted_at"].notna()]


def compute_voting_results() -> dict[str, Any]:
    """Compute per-issue vote percentages for the voting phase.

    Returns a dict with:
      - total_voters: int
      - total_votes: int
      - issues: list of dicts sorted by vote_pct descending, each with:
          idea_id, title, votes, vote_pct (% of voters who voted for this issue)
    """
    submitted = _submitted_baskets()
    if submitted.empty:
        return {"total_voters": 0, "total_votes": 0, "issues": []}

    basket_ideas = store.get("gv_basket_ideas", pd.DataFrame())
    if basket_ideas.empty:
        return {"total_voters": 0, "total_votes": 0, "issues": []}

    submitted_ids = set(submitted["id"])
    total_voters = len(submitted)

    # Filter basket_ideas to only submitted baskets in this phase
    phase_votes = basket_ideas[basket_ideas["basket_id"].isin(submitted_ids)].copy()
    total_votes = int(phase_votes["votes"].sum()) if "votes" in phase_votes.columns else len(phase_votes)

    # Count voters per idea (each basket_idea = 1 voter for that idea)
    idea_voter_counts = phase_votes.groupby("idea_id").size().reset_index(name="voters")

    # Join idea titles
    ideas_df = store.get("gv_ideas", pd.DataFrame())
    if not ideas_df.empty and "id" in ideas_df.columns:
        titles = ideas_df[["id", "title"]].rename(columns={"id": "idea_id"})
        idea_voter_counts = idea_voter_counts.merge(titles, on="idea_id", how="left")
    else:
        idea_voter_counts["title"] = ""

    idea_voter_counts["vote_pct"] = round(idea_voter_counts["voters"] / total_voters * 100, 1)
    idea_voter_counts = idea_voter_counts.sort_values("vote_pct", ascending=False)

    issues = []
    for _, row in idea_voter_counts.iterrows():
        title = row.get("title", "")
        if isinstance(title, dict):
            title = title.get("en", str(title))
        issues.append({
            "idea_id": row["idea_id"],
            "title": _strip_html(str(title)) if title else "",
            "voters": int(row["voters"]),
            "vote_pct": float(row["vote_pct"]),
        })

    return {
        "total_voters": total_voters,
        "total_votes": total_votes,
        "issues": issues,
    }


def compute_top_x_coverage(x: int, min_votes: int = 1) -> dict[str, Any]:
    """Compute what % of voters have at least *min_votes* votes in the top X issues.

    Returns a dict with:
      - x: the requested top-issue count
      - min_votes: the minimum number of votes required
      - top_ideas: list of {idea_id, title, voters}
      - voters_with_match: int
      - voters_total: int
      - coverage_pct: float
    """
    empty = {"x": x, "min_votes": min_votes, "top_ideas": [],
             "voters_with_match": 0, "voters_total": 0, "coverage_pct": 0.0}

    submitted = _submitted_baskets()
    if submitted.empty:
        return empty

    basket_ideas = store.get("gv_basket_ideas", pd.DataFrame())
    if basket_ideas.empty:
        return empty

    submitted_ids = set(submitted["id"])
    total_voters = len(submitted)
    phase_votes = basket_ideas[basket_ideas["basket_id"].isin(submitted_ids)].copy()

    # Identify top X ideas by total voters
    idea_voter_counts = phase_votes.groupby("idea_id").size().reset_index(name="voters")
    idea_voter_counts = idea_voter_counts.sort_values("voters", ascending=False)
    top_x_ideas = idea_voter_counts.head(x)
    top_idea_ids = set(top_x_ideas["idea_id"])

    # Find baskets that voted for at least *min_votes* top-X ideas
    votes_in_top = phase_votes[phase_votes["idea_id"].isin(top_idea_ids)]
    basket_hit_counts = votes_in_top.groupby("basket_id").size()
    qualifying_baskets = set(basket_hit_counts[basket_hit_counts >= min_votes].index)

    # Build basket→user mapping so we report user count
    basket_user = submitted.set_index("id")["user_id"].to_dict()
    voters_with_match = len({basket_user[bid] for bid in qualifying_baskets if bid in basket_user})

    # Enrich top ideas with titles
    ideas_df = store.get("gv_ideas", pd.DataFrame())
    title_map: dict[str, str] = {}
    if not ideas_df.empty and "id" in ideas_df.columns:
        for _, row in ideas_df.iterrows():
            t = row.get("title", "")
            if isinstance(t, dict):
                t = t.get("en", str(t))
            title_map[row["id"]] = _strip_html(str(t)) if t else ""

    top_ideas_list = []
    for _, row in top_x_ideas.iterrows():
        top_ideas_list.append({
            "idea_id": row["idea_id"],
            "title": title_map.get(row["idea_id"], ""),
            "voters": int(row["voters"]),
        })

    coverage_pct = round(voters_with_match / total_voters * 100, 1) if total_voters > 0 else 0.0

    return {
        "x": x,
        "min_votes": min_votes,
        "top_ideas": top_ideas_list,
        "voters_with_match": voters_with_match,
        "voters_total": total_voters,
        "coverage_pct": coverage_pct,
    }


def compute_voter_demographics() -> dict[str, Any]:
    """Compute demographic breakdowns for voters in the voting phase.

    Returns a dict with:
      - total_voters: int
      - voters_with_demographics: int
      - demographics: dict keyed by dimension name, each containing:
          - groups: list of {label, count, pct}
          - known: int (voters with this dimension known)
    """
    submitted = _submitted_baskets()
    if submitted.empty:
        return {"total_voters": 0, "voters_with_demographics": 0, "demographics": {}}

    total_voters = len(submitted)
    voter_user_ids = submitted["user_id"].dropna().unique().tolist()

    # Build demographic caches (thread-safe)
    with _cache_lock:
        _build_email_demo_cache()
        _build_userid_email_map()

    # Resolve demographics for each voter
    voter_demos: list[dict[str, str | None]] = []
    for uid in voter_user_ids:
        email = _ia._userid_email_map.get(uid)
        if email:
            demo = _ia._email_demo_cache.get(email, {})
        else:
            demo = {}
        voter_demos.append(demo)

    # Count voters with any demographic data
    voters_with_demo = sum(
        1 for d in voter_demos
        if any(d.get(dim) is not None for dim in BRIDGING_DIMENSIONS)
    )

    # Build distribution for each dimension
    dimensions_to_report = ["age_bucket", "race", "political_lean", "region"]
    demographics: dict[str, Any] = {}

    for dim in dimensions_to_report:
        counts: dict[str, int] = {}
        known = 0
        for demo in voter_demos:
            val = demo.get(dim)
            if val is not None:
                known += 1
                counts[val] = counts.get(val, 0) + 1
        groups = sorted(
            [{"label": label, "count": count, "pct": round(count / known * 100, 1) if known > 0 else 0.0}
             for label, count in counts.items()],
            key=lambda g: g["count"],
            reverse=True,
        )
        demographics[dim] = {"groups": groups, "known": known}

    return {
        "total_voters": total_voters,
        "voters_with_demographics": voters_with_demo,
        "demographics": demographics,
    }


# ── Survey completion counts ─────────────────────────────────────────────

# Cache form titles so we only fetch from Typeform once per process
_form_title_cache: dict[str, str] = {}


def _get_form_title(form_id: str) -> str:
    """Return the human-readable title for *form_id*, fetching once from Typeform."""
    if form_id in _form_title_cache:
        return _form_title_cache[form_id]
    try:
        tf = TypeformClient()
        defn = tf.get_form_definition(form_id)
        title = defn.get("title", form_id)
    except Exception:
        log.warning("Could not fetch title for form %s", form_id)
        title = form_id
    _form_title_cache[form_id] = title
    return title


SURVEY_FORM_IDS = ["CLIThuG3", "A0l4rOL3", "vPlG5hrP", "vsy52uwm", "GUsjAeNu", "KdHzkJeL", "PmPIQkd8", "YcnYy8ah"]


def compute_survey_completions() -> list[dict[str, Any]]:
    """Return partial/completed counts for each survey form.

    Returns a list of dicts, each with:
      - form_id, title, completed, partial, total
    """
    results: list[dict[str, Any]] = []
    for form_id in SURVEY_FORM_IDS:
        key = f"tf_{form_id}"
        df = store.get(key, pd.DataFrame())
        if df.empty:
            completed = 0
            partial = 0
        else:
            completed = int((df["response_type"] == "completed").sum())
            partial = int((df["response_type"] == "partial").sum())
        title = _get_form_title(form_id)
        results.append({
            "form_id": form_id,
            "title": title,
            "completed": completed,
            "partial": partial,
            "total": completed + partial,
        })
    return results
