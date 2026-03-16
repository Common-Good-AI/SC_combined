"""Unified idea analytics — per-idea view with reactions and demographic breakdowns.

Builds a rich view of each GoVocal ideation idea including:
- Idea metadata (id, title, body as plain text)
- Author demographics (resolved from gv_users or idea custom fields)
- Reaction totals (upvotes, downvotes)
- Reaction demographic breakdowns (by age bucket, race, region, urban/rural, political lean)
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon

from backend.data_store import store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Race code → human-readable label
_RACE_LABELS: dict[str, str] = {
    "white_9jz": "White",
    "black_or_african_american_v5c": "Black or African American",
    "hispanic_or_latino_gi4": "Hispanic or Latino",
    "asian_ib1": "Asian",
    "american_indian_or_alaska_native_unq": "American Indian or Alaska Native",
    "middle_eastern_or_north_african_mxd": "Middle Eastern or North African",
    "prefer_not_to_say_jo5": "Prefer not to say",
}

# Political lean code → human-readable label
_POLITICAL_LABELS: dict[str, str] = {
    "very_liberal_gp0": "Very Liberal",
    "somewhat_liberal_10r": "Liberal",
    "moderate_middle_of_the_road_jgq": "Moderate",
    "somewhat_conservative_3bp": "Conservative",
    "very_conservative_iwv": "Very Conservative",
    "prefer_not_to_say_dgj": "Prefer not to say",
}

# Typeform political labels → canonical labels (align with GoVocal human-readable)
_TF_POLITICAL_NORMALIZE: dict[str, str] = {
    "Very Liberal": "Very Liberal",
    "Liberal": "Liberal",
    "Moderate": "Moderate",
    "Conservative": "Conservative",
    "Very Conservative": "Very Conservative",
    "Not sure": "Not sure",
}

# Extra race codes that may appear (not in the main map but valid)
_EXTRA_RACE_LABELS: dict[str, str] = {
    "native_hawaiian_or_pacific_islander_ujy": "Native Hawaiian or Pacific Islander",
}

AGE_BUCKETS = [
    (18, 29, "18-29"),
    (30, 39, "30-39"),
    (40, 49, "40-49"),
    (50, 59, "50-59"),
    (60, 69, "60-69"),
    (70, 200, "70+"),
]

# Zipcode → region / urban_rural lookup
_ZIPCODE_GEO: dict[str, dict[str, str]] = {}


_ZIPCODE_GEO_LOADED = False

# ---------------------------------------------------------------------------
# Bridging Score Constants
# ---------------------------------------------------------------------------

# Demographic dimensions and their base weights for bridging score
# Political lean is most important (0.4), others split evenly
BRIDGING_DIMENSIONS: list[str] = ["political_lean", "age_bucket", "race", "region", "urban_rural"]
BRIDGING_BASE_WEIGHTS: dict[str, float] = {
    "political_lean": 0.50,
    "age_bucket": 0.10,
    "race": 0.10,
    "region": 0.10,
    "urban_rural": 0.20,
}

# Thresholds for bridging score confidence
BRIDGING_MIN_KNOWN_DEMO_REACTIONS = 15   # Below this, bridging score = None (insufficient data)
BRIDGING_FULL_CONFIDENCE_REACTIONS = 30  # At this level, demographic_confidence = 1.0
BRIDGING_ENGAGEMENT_SCALE = 50  # k in engagement_confidence = 1 - exp(-total/k)

# Approval ratio settings
BRIDGING_APPROVAL_EXPONENT = 1.5  # exponent for (upvotes/total)^n — higher = harsher penalty for low approval
BRIDGING_APPROVAL_WEIGHT = 0.7   # weight (0-1) of approval_factor in final score; 1.0 = full influence, 0.0 = ignored

# Engagement volume settings (dynamically computed as max reactions across all ideas)
BRIDGING_ENGAGEMENT_REFERENCE_FALLBACK = 150  # fallback if reactions data is unavailable
BRIDGING_ENGAGEMENT_WEIGHT = 0.2  # weight (0-1) of engagement_factor in final score; 1.0 = full influence, 0.0 = ignored

# Cross-coalition (JSD) settings
BRIDGING_MIN_DOWNVOTES_FOR_JSD = 5  # Need this many downvotes with known demos to compute JSD
BRIDGING_UPVOTE_DIVERSITY_WEIGHT = 0.7  # w1 in composite when JSD is available
BRIDGING_CROSS_COALITION_WEIGHT = 0.3   # w2 in composite when JSD is available

# Polarization penalty settings (per-group approval rate variance)
BRIDGING_POLARIZATION_PENALTY_WEIGHT = 0.6  # How strongly polarization penalizes the score (0=ignore, 1=full penalty)
BRIDGING_POLARIZATION_MIN_GROUP_VOTES = 3   # Minimum votes in a group to include it in variance calculation


def _load_zipcode_geo() -> None:
    """Load zipcode_county.json into a zipcode → {region, urban_rural} lookup."""
    global _ZIPCODE_GEO, _ZIPCODE_GEO_LOADED
    if _ZIPCODE_GEO_LOADED:
        return
    _ZIPCODE_GEO_LOADED = True
    # File lives alongside this module in backend/
    path = Path(__file__).resolve().parent / "zipcode_county.json"
    if not path.exists():
        log.warning("zipcode_county.json not found at %s", path)
        return
    with open(path) as f:
        data = json.load(f)
    for entry in data:
        zc = str(entry.get("zipcode", "")).strip()
        if zc:
            _ZIPCODE_GEO[zc] = {
                "region": entry.get("region"),
                "urban_rural": entry.get("urban_rural"),
            }
    log.info("Loaded %d zipcode geo entries", len(_ZIPCODE_GEO))


def _normalize_zipcode(raw: str | None) -> str | None:
    """Normalize a zipcode value to a plain integer string (e.g. '29588.0' → '29588')."""
    if not raw:
        return None
    s = str(raw).strip()
    try:
        return str(int(float(s)))
    except (ValueError, TypeError):
        return s


def _resolve_geo(zipcode: str | None) -> dict[str, str | None]:
    """Return region and urban_rural for a zipcode."""
    _load_zipcode_geo()
    zc = _normalize_zipcode(zipcode)
    if not zc:
        return {"region": None, "urban_rural": None}
    geo = _ZIPCODE_GEO.get(zc, {})
    return {
        "region": geo.get("region"),
        "urban_rural": geo.get("urban_rural"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_html(html: str | None) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(html))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_val(val: Any) -> str | None:
    """Return None for NaN / blank / 'nan' values, else stripped string."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if s.lower() in ("", "nan", "none"):
        return None
    return s


def _age_bucket(raw: Any) -> str | None:
    """Convert a raw age value to an age bucket string."""
    val = _clean_val(raw)
    if val is None:
        return None
    try:
        age = int(float(val))
    except (ValueError, TypeError):
        return None
    for lo, hi, label in AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    if age < 18:
        return "Under 18"
    return "65+"


def _human_race(raw: Any) -> str | None:
    """Convert a race code to a human-readable label (handles both GoVocal codes and plain strings)."""
    val = _clean_val(raw)
    if val is None:
        return None
    # Try GoVocal code lookup first
    label = _RACE_LABELS.get(val) or _EXTRA_RACE_LABELS.get(val)
    if label:
        return label
    # Already a human-readable label (e.g. from Typeform) — return as-is
    return val


def _human_political(raw: Any) -> str | None:
    """Convert a political lean code to a human-readable label (handles both GoVocal codes and Typeform labels)."""
    val = _clean_val(raw)
    if val is None:
        return None
    # Try GoVocal code lookup first
    label = _POLITICAL_LABELS.get(val)
    if label:
        return label
    # Try Typeform label normalisation
    label = _TF_POLITICAL_NORMALIZE.get(val)
    if label:
        return label
    # Unknown — return as-is
    return val


# ---------------------------------------------------------------------------
# Unified demographics lookup (merges all sources via email)
# ---------------------------------------------------------------------------

_EMPTY_DEMO: dict[str, str | None] = {
    "age_bucket": None, "race": None, "region": None,
    "urban_rural": None, "political_lean": None,
}

_email_demo_cache: dict[str, dict[str, str | None]] = {}
_userid_email_map: dict[str, str] = {}


def _build_email_demo_cache() -> None:
    """Build an email → best-merged demographics dict from unified_demographics.

    For each email, iterates over all rows (from any source) and picks the
    first non-null value for each demographic field.  Values are normalised
    to human-readable labels and zipcode is resolved to region / urban_rural.
    """
    global _email_demo_cache
    if _email_demo_cache:
        return

    unified = store.get("unified_demographics")
    if unified is None or unified.empty:
        log.warning("unified_demographics not available — demographic lookups will be empty")
        return

    # Accumulate best non-null value per email per field
    merged: dict[str, dict[str, str | None]] = {}
    for _, row in unified.iterrows():
        email = _clean_val(row.get("email"))
        if not email:
            continue
        email = email.lower().strip()

        if email not in merged:
            merged[email] = {"age": None, "race": None, "zipcode": None, "political_lean": None}

        entry = merged[email]
        for field in ("age", "race", "zipcode", "political_lean"):
            if entry[field] is None:
                val = _clean_val(row.get(field))
                if val is not None:
                    entry[field] = val

    # Now convert to the final format with normalised labels and geo
    for email, raw in merged.items():
        geo = _resolve_geo(raw["zipcode"])
        _email_demo_cache[email] = {
            "age_bucket": _age_bucket(raw["age"]),
            "race": _human_race(raw["race"]),
            "region": geo["region"],
            "urban_rural": geo["urban_rural"],
            "political_lean": _human_political(raw["political_lean"]),
        }

    log.info("Email demo cache: %d emails with demographics", len(_email_demo_cache))


def _build_userid_email_map() -> None:
    """Build a user_id → email map from gv_users for fast lookups."""
    global _userid_email_map
    if _userid_email_map:
        return

    users = store.get("gv_users")
    if users is None:
        return

    for _, row in users.iterrows():
        uid = _clean_val(row.get("id"))
        email = _clean_val(row.get("email"))
        if uid and email:
            _userid_email_map[uid] = email.lower().strip()

    log.info("User ID → email map: %d entries", len(_userid_email_map))


def _get_demo_by_email(email: str | None) -> dict[str, str | None]:
    """Look up demographics for an email from the unified cache."""
    _build_email_demo_cache()
    if not email:
        return dict(_EMPTY_DEMO)
    return _email_demo_cache.get(email.lower().strip(), dict(_EMPTY_DEMO))


def _get_demo_by_user_id(user_id: str | None) -> dict[str, str | None]:
    """Look up demographics for a user_id via user_id → email → unified cache."""
    _build_userid_email_map()
    if not user_id:
        return dict(_EMPTY_DEMO)
    email = _userid_email_map.get(user_id)
    return _get_demo_by_email(email)


def _get_idea_author_demo(idea_row: pd.Series) -> dict[str, str | None]:
    """Resolve author demographics from unified demographics.

    Resolution order:
    1. author_id → email → unified demographics
    2. idea custom field email → unified demographics
    """
    # Try via author_id
    author_id = _clean_val(idea_row.get("author_id"))
    if author_id:
        demo = _get_demo_by_user_id(author_id)
        if any(v is not None for v in demo.values()):
            return demo

    # Fallback: idea custom field email → unified
    email = _clean_val(idea_row.get("custom_field_values.u_email_5vp"))
    if not email:
        email = _clean_val(idea_row.get("custom_field_values.u_email_rzm"))
    if email:
        demo = _get_demo_by_email(email)
        if any(v is not None for v in demo.values()):
            return demo

    return dict(_EMPTY_DEMO)


def _bucket_counter(values: list[str | None]) -> dict[str, int]:
    """Count non-null values and return sorted dict."""
    counts: dict[str, int] = {}
    for v in values:
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


# ---------------------------------------------------------------------------
# Pre-compute user demographics lookup (cached per refresh)
# ---------------------------------------------------------------------------

_user_demo_cache: dict[str, dict[str, str | None]] = {}


def _ensure_user_demo_cache() -> None:
    """Build a user_id → demographics dict for fast reaction lookups.

    Uses the unified demographics (via email) so that Typeform data
    is included alongside GoVocal user and idea custom field data.
    """
    global _user_demo_cache
    if _user_demo_cache:
        return

    _build_email_demo_cache()
    _build_userid_email_map()

    for uid, email in _userid_email_map.items():
        demo = _email_demo_cache.get(email)
        if demo:
            _user_demo_cache[uid] = dict(demo)
        else:
            _user_demo_cache[uid] = dict(_EMPTY_DEMO)

    log.info("User demo cache: %d user IDs", len(_user_demo_cache))


# ---------------------------------------------------------------------------
# Population baseline for bridging score normalization
# ---------------------------------------------------------------------------

_population_baseline: dict[str, dict[str, float]] = {}
_population_baseline_built = False
_max_idea_reactions: int = BRIDGING_ENGAGEMENT_REFERENCE_FALLBACK


def _build_population_baseline() -> None:
    """Build the global demographic distribution across all voters with known demographics.

    This is the baseline for KL/JSD-based diversity scoring. A bridging score of 1.0
    means the idea's upvote distribution matches the overall voter population.

    Also computes `_max_idea_reactions` — the highest total reaction count across
    all ideas — used as the engagement factor reference so that the score is
    relative to the most-engaged-with idea.
    """
    global _population_baseline, _population_baseline_built, _max_idea_reactions
    if _population_baseline_built:
        return
    _population_baseline_built = True

    _ensure_user_demo_cache()

    reactions_df = store.get("gv_reactions", pd.DataFrame())
    if reactions_df.empty:
        log.warning("No reactions data — population baseline will be empty")
        return

    # Compute max reactions per idea for engagement factor reference
    if "reactable_id" in reactions_df.columns:
        per_idea_counts = reactions_df["reactable_id"].value_counts()
        _max_idea_reactions = int(per_idea_counts.max()) if len(per_idea_counts) > 0 else BRIDGING_ENGAGEMENT_REFERENCE_FALLBACK
    else:
        _max_idea_reactions = BRIDGING_ENGAGEMENT_REFERENCE_FALLBACK
    log.info("Max idea reactions (engagement reference): %d", _max_idea_reactions)

    # Get unique voter user_ids (users who have reacted to at least one idea)
    voter_ids = set()
    if "user_id" in reactions_df.columns:
        voter_ids = set(reactions_df["user_id"].dropna().unique())

    # Collect demographic values for each dimension
    dim_values: dict[str, list[str]] = {dim: [] for dim in BRIDGING_DIMENSIONS}

    for uid in voter_ids:
        demo = _user_demo_cache.get(uid, {})
        for dim in BRIDGING_DIMENSIONS:
            val = demo.get(dim)
            if val is not None:
                dim_values[dim].append(val)

    # Convert to probability distributions
    for dim, values in dim_values.items():
        if not values:
            _population_baseline[dim] = {}
            continue
        total = len(values)
        counts: dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        _population_baseline[dim] = {k: v / total for k, v in counts.items()}

    log.info(
        "Population baseline built: %s",
        {dim: len(dist) for dim, dist in _population_baseline.items()},
    )


def invalidate_cache() -> None:
    """Clear cached lookups (call after data refresh)."""
    global _user_demo_cache, _email_demo_cache, _userid_email_map, _ZIPCODE_GEO, _ZIPCODE_GEO_LOADED
    global _population_baseline, _population_baseline_built, _max_idea_reactions
    _user_demo_cache = {}
    _email_demo_cache = {}
    _userid_email_map = {}
    _ZIPCODE_GEO = {}
    _ZIPCODE_GEO_LOADED = False
    _population_baseline = {}
    _population_baseline_built = False
    _max_idea_reactions = BRIDGING_ENGAGEMENT_REFERENCE_FALLBACK


# ---------------------------------------------------------------------------
# Reaction demographic breakdown
# ---------------------------------------------------------------------------

def _reaction_demo_breakdown(reactions_df: pd.DataFrame) -> dict[str, Any]:
    """Compute upvote/downvote breakdown by each demographic dimension."""
    _ensure_user_demo_cache()

    up = reactions_df[reactions_df["mode"] == "up"] if "mode" in reactions_df.columns else pd.DataFrame()
    down = reactions_df[reactions_df["mode"] == "down"] if "mode" in reactions_df.columns else pd.DataFrame()

    def _demo_for_reactions(df: pd.DataFrame) -> dict[str, list[str | None]]:
        demos: dict[str, list[str | None]] = {
            "age_bucket": [], "race": [], "region": [], "urban_rural": [], "political_lean": [],
        }
        for _, r in df.iterrows():
            uid = _clean_val(r.get("user_id"))
            d = _user_demo_cache.get(uid, {}) if uid else {}
            for key in demos:
                demos[key].append(d.get(key))
        return demos

    up_demos = _demo_for_reactions(up)
    down_demos = _demo_for_reactions(down)

    breakdown: dict[str, Any] = {}
    for dim in ("age_bucket", "race", "political_lean", "region", "urban_rural"):
        breakdown[dim] = {
            "upvotes": _bucket_counter(up_demos[dim]),
            "downvotes": _bucket_counter(down_demos[dim]),
        }

    return breakdown


# ---------------------------------------------------------------------------
# Bridging Score Computation
# ---------------------------------------------------------------------------

def _counts_to_distribution(counts: dict[str, int]) -> dict[str, float]:
    """Convert bucket counts to a probability distribution."""
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def _jsd_between_distributions(
    dist_a: dict[str, float],
    dist_b: dict[str, float],
) -> float:
    """Compute Jensen-Shannon divergence between two distributions.

    Returns a value in [0, 1] where 0 = identical distributions.
    Handles cases where distributions have different keys by unioning them.
    """
    if not dist_a or not dist_b:
        return 1.0  # Maximum divergence if one is empty

    # Union of all keys
    all_keys = sorted(set(dist_a.keys()) | set(dist_b.keys()))
    if not all_keys:
        return 1.0

    # Build aligned probability vectors (add small epsilon to avoid log(0))
    eps = 1e-10
    p = np.array([dist_a.get(k, 0.0) + eps for k in all_keys])
    q = np.array([dist_b.get(k, 0.0) + eps for k in all_keys])

    # Normalize to ensure they sum to 1
    p = p / p.sum()
    q = q / q.sum()

    # scipy's jensenshannon returns the JS distance (sqrt of divergence)
    # Use the distance directly (not squared) for better sensitivity to
    # distribution shifts among minority subgroups.
    js_distance = jensenshannon(p, q)
    return float(js_distance)


def _compute_dimension_coverage(
    upvote_demos: dict[str, list[str | None]],
    downvote_demos: dict[str, list[str | None]],
) -> dict[str, float]:
    """Compute coverage (fraction of non-null values) for each dimension."""
    coverage: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        up_values = upvote_demos.get(dim, [])
        down_values = downvote_demos.get(dim, [])
        all_values = up_values + down_values
        if not all_values:
            coverage[dim] = 0.0
        else:
            non_null = sum(1 for v in all_values if v is not None)
            coverage[dim] = non_null / len(all_values)
    return coverage


def _compute_effective_weights(coverage: dict[str, float]) -> dict[str, float]:
    """Compute effective weights by adjusting base weights for coverage, then renormalizing."""
    effective: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        effective[dim] = BRIDGING_BASE_WEIGHTS[dim] * coverage.get(dim, 0.0)

    total = sum(effective.values())
    if total == 0:
        return {dim: 0.0 for dim in BRIDGING_DIMENSIONS}

    return {dim: w / total for dim, w in effective.items()}


def _compute_group_approval_variance(
    up_demos: dict[str, list[str | None]],
    down_demos: dict[str, list[str | None]],
    dim: str,
) -> float:
    """Compute variance of per-group approval rates for a dimension.

    Returns a normalised value in [0, 1] where 0 means all groups approve
    at the same rate and 1 means maximum polarization (some groups 100%
    approve while others 0% approve).
    """
    up_counts = _bucket_counter([v for v in up_demos[dim] if v is not None])
    down_counts = _bucket_counter([v for v in down_demos[dim] if v is not None])

    all_groups = set(up_counts.keys()) | set(down_counts.keys())
    if len(all_groups) < 2:
        return 0.0

    approval_rates: list[float] = []
    for group in all_groups:
        u = up_counts.get(group, 0)
        d = down_counts.get(group, 0)
        total = u + d
        if total >= BRIDGING_POLARIZATION_MIN_GROUP_VOTES:
            approval_rates.append(u / total)

    if len(approval_rates) < 2:
        return 0.0

    # Variance of approval rates: 0 = unanimous, 0.25 = maximum split
    mean = sum(approval_rates) / len(approval_rates)
    variance = sum((r - mean) ** 2 for r in approval_rates) / len(approval_rates)
    # Normalise: max possible variance is 0.25 (half at 0%, half at 100%)
    return min(1.0, variance / 0.25)


def _compute_bridging_score(
    idea_reactions: pd.DataFrame,
    total_votes: int,
) -> dict[str, Any]:
    """Compute the bridging score for an idea.

    Returns a dict with:
    - bridging_score: float 0-100 or None if insufficient data
    - confidence_level: "low" | "medium" | "high" | None
    - demographic_coverage: float (fraction of reactions with known demos)
    - engagement_confidence: float 0-1
    - demographic_confidence: float 0-1
    - per_dimension_scores: dict of dimension → score (0-1)
    - downvote_diversity: float 0-1 (separate metric)
    - cross_coalition_used: bool
    """
    _ensure_user_demo_cache()
    _build_population_baseline()

    result: dict[str, Any] = {
        "bridging_score": None,
        "confidence_level": None,
        "demographic_coverage": 0.0,
        "engagement_confidence": 0.0,
        "demographic_confidence": 0.0,
        "approval_factor": 0.0,
        "engagement_factor": 0.0,
        "per_dimension_scores": {},
        "downvote_diversity": None,
        "cross_coalition_used": False,
        "polarization_scores": {},
        "polarization_penalty": 0.0,
    }

    if idea_reactions.empty or total_votes == 0:
        return result

    # Split into upvotes and downvotes
    up = idea_reactions[idea_reactions["mode"] == "up"] if "mode" in idea_reactions.columns else pd.DataFrame()
    down = idea_reactions[idea_reactions["mode"] == "down"] if "mode" in idea_reactions.columns else pd.DataFrame()

    # Collect demographics for each reaction
    def collect_demos(df: pd.DataFrame) -> dict[str, list[str | None]]:
        demos: dict[str, list[str | None]] = {dim: [] for dim in BRIDGING_DIMENSIONS}
        for _, r in df.iterrows():
            uid = _clean_val(r.get("user_id"))
            d = _user_demo_cache.get(uid, {}) if uid else {}
            for dim in BRIDGING_DIMENSIONS:
                demos[dim].append(d.get(dim))
        return demos

    up_demos = collect_demos(up)
    down_demos = collect_demos(down)

    # Approval factor: (upvotes / total) ^ exponent
    approval_ratio = len(up) / total_votes if total_votes > 0 else 0.0
    approval_factor = approval_ratio ** BRIDGING_APPROVAL_EXPONENT
    result["approval_factor"] = round(approval_factor, 4)

    # Count reactions with at least one known demographic value
    def count_known_demo_reactions(df: pd.DataFrame) -> int:
        count = 0
        for _, r in df.iterrows():
            uid = _clean_val(r.get("user_id"))
            d = _user_demo_cache.get(uid, {}) if uid else {}
            if any(d.get(dim) is not None for dim in BRIDGING_DIMENSIONS):
                count += 1
        return count

    up_known = count_known_demo_reactions(up)
    down_known = count_known_demo_reactions(down)
    total_known = up_known + down_known

    result["demographic_coverage"] = total_known / total_votes if total_votes > 0 else 0.0

    # Check minimum threshold
    if total_known < BRIDGING_MIN_KNOWN_DEMO_REACTIONS:
        return result

    # Engagement confidence: 1 - exp(-total_votes / k)
    engagement_conf = 1.0 - math.exp(-total_votes / BRIDGING_ENGAGEMENT_SCALE)
    result["engagement_confidence"] = round(engagement_conf, 4)

    # Engagement factor: log curve relative to the most-reacted idea
    engagement_factor = min(1.0, math.log1p(total_votes) / math.log1p(_max_idea_reactions))
    result["engagement_factor"] = round(engagement_factor, 4)

    # Demographic confidence: min(1, known / threshold)
    demo_conf = min(1.0, total_known / BRIDGING_FULL_CONFIDENCE_REACTIONS)
    result["demographic_confidence"] = round(demo_conf, 4)

    # Compute coverage per dimension and effective weights
    coverage = _compute_dimension_coverage(up_demos, down_demos)
    effective_weights = _compute_effective_weights(coverage)

    # Compute per-dimension diversity scores using JSD from population baseline
    per_dim_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        up_counts = _bucket_counter([v for v in up_demos[dim] if v is not None])
        up_dist = _counts_to_distribution(up_counts)
        pop_dist = _population_baseline.get(dim, {})

        if not up_dist or not pop_dist:
            per_dim_scores[dim] = 0.0
        else:
            jsd = _jsd_between_distributions(up_dist, pop_dist)
            per_dim_scores[dim] = 1.0 - jsd  # Score = 1 when identical to population

    result["per_dimension_scores"] = {dim: round(s, 4) for dim, s in per_dim_scores.items()}

    # Compute per-dimension polarization (approval-rate variance across groups)
    polarization_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        polarization_scores[dim] = _compute_group_approval_variance(up_demos, down_demos, dim)
    result["polarization_scores"] = {dim: round(s, 4) for dim, s in polarization_scores.items()}

    # Weighted polarization penalty (multiplicative)
    weighted_polarization = sum(
        effective_weights[dim] * polarization_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    )
    polarization_factor = 1.0 - BRIDGING_POLARIZATION_PENALTY_WEIGHT * weighted_polarization
    result["polarization_penalty"] = round(weighted_polarization, 4)

    # Compute upvote diversity as weighted average, then apply polarization penalty
    upvote_diversity = sum(
        effective_weights[dim] * per_dim_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    )

    # Compute downvote diversity (separate metric, not in main score)
    down_dim_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        down_counts = _bucket_counter([v for v in down_demos[dim] if v is not None])
        down_dist = _counts_to_distribution(down_counts)
        pop_dist = _population_baseline.get(dim, {})

        if not down_dist or not pop_dist:
            down_dim_scores[dim] = 0.0
        else:
            jsd = _jsd_between_distributions(down_dist, pop_dist)
            down_dim_scores[dim] = 1.0 - jsd

    downvote_diversity = sum(
        effective_weights[dim] * down_dim_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    )
    result["downvote_diversity"] = round(downvote_diversity, 4)

    # Cross-coalition signal (JSD between upvote and downvote distributions)
    # Only compute if we have enough downvotes with known demographics
    cross_coalition_score = 0.0
    use_jsd = down_known >= BRIDGING_MIN_DOWNVOTES_FOR_JSD

    if use_jsd:
        result["cross_coalition_used"] = True
        cross_coalition_dims: dict[str, float] = {}
        for dim in BRIDGING_DIMENSIONS:
            up_counts = _bucket_counter([v for v in up_demos[dim] if v is not None])
            down_counts = _bucket_counter([v for v in down_demos[dim] if v is not None])
            up_dist = _counts_to_distribution(up_counts)
            down_dist = _counts_to_distribution(down_counts)

            if not up_dist or not down_dist:
                cross_coalition_dims[dim] = 0.0
            else:
                jsd = _jsd_between_distributions(up_dist, down_dist)
                # 1 - JSD: score = 1 when upvoters and downvoters look identical
                cross_coalition_dims[dim] = 1.0 - jsd

        cross_coalition_score = sum(
            effective_weights[dim] * cross_coalition_dims[dim]
            for dim in BRIDGING_DIMENSIONS
        )

        # Combine upvote diversity and cross-coalition
        diversity_composite = (
            BRIDGING_UPVOTE_DIVERSITY_WEIGHT * upvote_diversity +
            BRIDGING_CROSS_COALITION_WEIGHT * cross_coalition_score
        )
    else:
        # Fall back to upvote diversity only
        diversity_composite = upvote_diversity

    # Apply multiplicative polarization penalty to diversity composite
    diversity_composite *= polarization_factor

    # Final bridging score
    # Approval and engagement factors are raised to their respective weights
    # so that weight=1.0 means full influence and weight=0.0 means no influence.
    weighted_approval = approval_factor ** BRIDGING_APPROVAL_WEIGHT
    weighted_engagement = engagement_factor ** BRIDGING_ENGAGEMENT_WEIGHT
    bridging_score = engagement_conf * demo_conf * weighted_approval * weighted_engagement * diversity_composite * 100
    result["bridging_score"] = round(bridging_score, 2)

    # Confidence level
    if total_known >= BRIDGING_FULL_CONFIDENCE_REACTIONS and total_votes >= 20:
        result["confidence_level"] = "high"
    elif total_known >= BRIDGING_MIN_KNOWN_DEMO_REACTIONS * 1.5:
        result["confidence_level"] = "medium"
    else:
        result["confidence_level"] = "low"

    return result


# ---------------------------------------------------------------------------
# Build unified idea view
# ---------------------------------------------------------------------------

def build_idea_view(idea_id: str | None = None) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Build the unified idea view for all ideation ideas or a single idea.

    Parameters
    ----------
    idea_id : str | None
        If provided, returns a single idea dict. Otherwise returns a list of all ideas.

    Returns
    -------
    list[dict] for all ideas, dict for a single idea, or None if idea_id not found.
    """
    ideas_df = store.get("gv_ideas_ideation")
    if ideas_df is None or ideas_df.empty:
        return [] if idea_id is None else None

    reactions_df = store.get("gv_reactions", pd.DataFrame())

    if idea_id is not None:
        # Single idea
        match = ideas_df[ideas_df["id"] == idea_id]
        if match.empty:
            return None
        return _build_single_idea(match.iloc[0], reactions_df)

    # All ideas
    results = []
    for _, row in ideas_df.iterrows():
        results.append(_build_single_idea(row, reactions_df))

    # Sort by total reactions descending
    results.sort(key=lambda x: x["reactions"]["total"], reverse=True)
    return results


def _build_single_idea(idea_row: pd.Series, reactions_df: pd.DataFrame) -> dict[str, Any]:
    """Build the unified view for a single idea."""
    idea_id = str(idea_row.get("id", ""))

    # Filter reactions for this idea
    if not reactions_df.empty and "reactable_id" in reactions_df.columns:
        idea_reactions = reactions_df[reactions_df["reactable_id"] == idea_id]
    else:
        idea_reactions = pd.DataFrame()

    upvotes = int((idea_reactions["mode"] == "up").sum()) if not idea_reactions.empty and "mode" in idea_reactions.columns else 0
    downvotes = int((idea_reactions["mode"] == "down").sum()) if not idea_reactions.empty and "mode" in idea_reactions.columns else 0
    total = upvotes + downvotes

    # Author demographics
    author_demo = _get_idea_author_demo(idea_row)

    # Reaction demographic breakdown
    demo_breakdown = _reaction_demo_breakdown(idea_reactions) if not idea_reactions.empty else {
        dim: {"upvotes": {}, "downvotes": {}}
        for dim in ("age_bucket", "race", "political_lean", "region", "urban_rural")
    }

    # Build body as plain text
    body_html = idea_row.get("body", "") or ""
    # body can be a dict from json_normalize; handle that
    if isinstance(body_html, dict):
        body_html = body_html.get("en", body_html.get("", str(body_html)))
    body_text = _strip_html(str(body_html))

    # Title
    title = idea_row.get("title", "") or ""
    if isinstance(title, dict):
        title = title.get("en", title.get("", str(title)))

    # Bridging score
    bridging = _compute_bridging_score(idea_reactions, total)

    return {
        "idea_id": idea_id,
        "title": str(title),
        "body": body_text,
        "project_id": _clean_val(idea_row.get("project_id")),
        "created_at": _clean_val(idea_row.get("created_at")),
        "author_demographics": author_demo,
        "reactions": {
            "total": total,
            "upvotes": upvotes,
            "downvotes": downvotes,
            "demographic_breakdown": demo_breakdown,
        },
        "bridging": bridging,
    }
