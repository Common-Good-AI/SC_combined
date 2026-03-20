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
# Consensus Score Constants
# ---------------------------------------------------------------------------

# Demographic dimensions and their base weights for consensus score
# Political lean is most important (0.4), others split evenly
BRIDGING_DIMENSIONS: list[str] = ["political_lean", "age_bucket", "race", "region", "urban_rural"]
BRIDGING_BASE_WEIGHTS: dict[str, float] = {
    "political_lean": 0.50,
    "age_bucket": 0.10,
    "race": 0.10,
    "region": 0.10,
    "urban_rural": 0.20,
}

# Thresholds for consensus score confidence
BRIDGING_MIN_KNOWN_DEMO_REACTIONS = 20   # Below this, consensus score = None (insufficient data)
BRIDGING_FULL_CONFIDENCE_REACTIONS = 50  # At this level, demographic_confidence = 1.0

# Polarization penalty settings (per-group approval rate variance)
BRIDGING_POLARIZATION_PENALTY_WEIGHT = 1.0  # How strongly polarization penalizes the score (0=ignore, 1=full penalty)
BRIDGING_POLARIZATION_MIN_GROUP_VOTES = 20   # Minimum votes in a group to include it in variance calculation

# WMGA (Weighted Mean Group Approval) settings
BRIDGING_WMGA_PRIOR_STRENGTH = 15  # Bayesian prior strength; higher = more shrinkage toward overall approval


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
# Population baseline for consensus score normalization
# ---------------------------------------------------------------------------

_population_baseline: dict[str, dict[str, float]] = {}
_population_baseline_built = False

_platform_approval_rate: float = 0.5
_platform_approval_rate_built = False


def _build_population_baseline() -> None:
    """Build the global demographic distribution across all voters with known demographics.

    This is the baseline for JSD-based diversity scoring. A diversity score of 1.0
    means the idea's upvote distribution matches the overall voter population.
    """
    global _population_baseline, _population_baseline_built
    if _population_baseline_built:
        return
    _population_baseline_built = True

    _ensure_user_demo_cache()

    reactions_df = store.get("gv_reactions", pd.DataFrame())
    if reactions_df.empty:
        log.warning("No reactions data — population baseline will be empty")
        return

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


def _build_platform_approval_rate() -> None:
    """Compute the platform-wide approval rate across all reactions.

    Used as the Bayesian prior for WMGA so that groups with no votes on
    an idea are shrunk toward the global average rather than toward the
    idea's own (potentially perfect) approval rate.
    """
    global _platform_approval_rate, _platform_approval_rate_built
    if _platform_approval_rate_built:
        return
    _platform_approval_rate_built = True

    reactions_df = store.get("gv_reactions", pd.DataFrame())
    if reactions_df.empty or "mode" not in reactions_df.columns:
        return

    total = len(reactions_df)
    if total == 0:
        return

    upvotes = int((reactions_df["mode"] == "up").sum())
    _platform_approval_rate = upvotes / total
    log.info("Platform-wide approval rate: %.4f", _platform_approval_rate)


def get_population_demographics() -> dict[str, Any]:
    """Return the demographic distribution of all voters on the platform.

    This is the same baseline used for JSD normalization in the consensus score.
    Returns a dict of dimension -> {category: proportion, ...}.
    """
    _build_population_baseline()
    _ensure_user_demo_cache()

    reactions_df = store.get("gv_reactions", pd.DataFrame())
    voter_ids: set[str] = set()
    if not reactions_df.empty and "user_id" in reactions_df.columns:
        voter_ids = set(reactions_df["user_id"].dropna().unique())

    total_voters = len(voter_ids)

    dim_counts: dict[str, dict[str, int]] = {dim: {} for dim in BRIDGING_DIMENSIONS}
    dim_known: dict[str, int] = {dim: 0 for dim in BRIDGING_DIMENSIONS}

    for uid in voter_ids:
        demo = _user_demo_cache.get(uid, {})
        for dim in BRIDGING_DIMENSIONS:
            val = demo.get(dim)
            if val is not None:
                dim_counts[dim][val] = dim_counts[dim].get(val, 0) + 1
                dim_known[dim] += 1

    # Canonical display orderings per dimension (matches IdeaDetail.js)
    _DIM_ORDER: dict[str, list[str]] = {
        "political_lean": [
            "Very Conservative", "Conservative", "Moderate",
            "Liberal", "Very Liberal", "Not sure", "Prefer not to say",
        ],
        "age_bucket": [
            "Under 18", "18-29", "30-39", "40-49", "50-59", "60-69", "65+", "70+",
        ],
    }

    def _sort_cats(dim: str, items: list[tuple[str, int]]) -> list[tuple[str, int]]:
        order = _DIM_ORDER.get(dim)
        if order:
            rank = {v: i for i, v in enumerate(order)}
            return sorted(items, key=lambda x: (rank.get(x[0], 999), x[0]))
        if dim == "race":
            # "Prefer not to say" last, rest alphabetical
            return sorted(items, key=lambda x: (x[0].lower().startswith("prefer"), x[0]))
        # region, urban_rural: alphabetical
        return sorted(items, key=lambda x: x[0])

    dimensions: dict[str, Any] = {}
    for dim in BRIDGING_DIMENSIONS:
        counts = dim_counts[dim]
        known = dim_known[dim]
        sorted_cats = _sort_cats(dim, list(counts.items()))
        dimensions[dim] = {
            "total_known": known,
            "total_unknown": total_voters - known,
            "distribution": {k: round(v / known, 4) if known else 0 for k, v in sorted_cats},
            "counts": dict(sorted_cats),
        }

    return {
        "total_voters": total_voters,
        "dimensions": dimensions,
    }


def invalidate_cache() -> None:
    """Clear cached lookups (call after data refresh)."""
    global _user_demo_cache, _email_demo_cache, _userid_email_map, _ZIPCODE_GEO, _ZIPCODE_GEO_LOADED
    global _population_baseline, _population_baseline_built
    global _platform_approval_rate, _platform_approval_rate_built
    _user_demo_cache = {}
    _email_demo_cache = {}
    _userid_email_map = {}
    _ZIPCODE_GEO = {}
    _ZIPCODE_GEO_LOADED = False
    _population_baseline = {}
    _population_baseline_built = False
    _platform_approval_rate = 0.5
    _platform_approval_rate_built = False


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
# Consensus Score Computation
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


def _compute_consensus_score(
    idea_reactions: pd.DataFrame,
    total_votes: int,
) -> dict[str, Any]:
    """Compute the consensus score for an idea.

    Simplified formula:  Score = Approval × Diversity × Confidence × 100

    Where:
    - Approval  = upvotes / total_votes
    - Diversity = weighted demographic diversity (JSD vs population)
                  × (1 − polarization_penalty_weight × polarization)
    - Confidence = min(1, known_demo_reactions / 30)

    Returns a dict with:
    - consensus_score: float 0-100 or None if insufficient data
    - confidence_level: "low" | "medium" | "high" | None
    - demographic_coverage: float (fraction of reactions with known demos)
    - demographic_confidence: float 0-1
    - approval_ratio: float 0-1
    - per_dimension_scores: dict of dimension → score (0-1)
    - polarization_scores: dict of dimension → score (0-1)
    - polarization_penalty: float 0-1
    """
    _ensure_user_demo_cache()
    _build_population_baseline()

    result: dict[str, Any] = {
        "consensus_score": None,
        "confidence_level": None,
        "demographic_coverage": 0.0,
        "demographic_confidence": 0.0,
        "approval_ratio": 0.0,
        "per_dimension_scores": {},
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

    # Approval: simple ratio of upvotes to total
    approval_ratio = len(up) / total_votes if total_votes > 0 else 0.0
    result["approval_ratio"] = round(approval_ratio, 4)

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

    # Confidence: linear ramp from 0 to 1 as known demos reach threshold
    confidence = min(1.0, total_known / BRIDGING_FULL_CONFIDENCE_REACTIONS)
    result["demographic_confidence"] = round(confidence, 4)

    # Compute coverage per dimension and effective weights
    coverage = _compute_dimension_coverage(up_demos, down_demos)
    effective_weights = _compute_effective_weights(coverage)

    # Per-dimension diversity scores: 1 − JSD(upvote_dist, population_baseline)
    per_dim_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        up_counts = _bucket_counter([v for v in up_demos[dim] if v is not None])
        up_dist = _counts_to_distribution(up_counts)
        pop_dist = _population_baseline.get(dim, {})

        if not up_dist or not pop_dist:
            per_dim_scores[dim] = 0.0
        else:
            jsd = _jsd_between_distributions(up_dist, pop_dist)
            per_dim_scores[dim] = 1.0 - jsd

    result["per_dimension_scores"] = {dim: round(s, 4) for dim, s in per_dim_scores.items()}

    # Per-dimension polarization (approval-rate variance across groups)
    polarization_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        polarization_scores[dim] = _compute_group_approval_variance(up_demos, down_demos, dim)
    result["polarization_scores"] = {dim: round(s, 4) for dim, s in polarization_scores.items()}

    # Weighted polarization penalty
    weighted_polarization = sum(
        effective_weights[dim] * polarization_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    )
    polarization_factor = 1.0 - BRIDGING_POLARIZATION_PENALTY_WEIGHT * weighted_polarization
    result["polarization_penalty"] = round(weighted_polarization, 4)

    # Diversity: weighted average of per-dimension scores × polarization penalty
    diversity = sum(
        effective_weights[dim] * per_dim_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    ) * polarization_factor

    # Final score: Approval × Diversity × Confidence × 100
    consensus_score = approval_ratio * diversity * confidence * 100
    result["consensus_score"] = round(consensus_score, 2)

    # Confidence level
    if total_known >= BRIDGING_FULL_CONFIDENCE_REACTIONS and total_votes >= 20:
        result["confidence_level"] = "high"
    elif total_known >= BRIDGING_MIN_KNOWN_DEMO_REACTIONS * 1.5:
        result["confidence_level"] = "medium"
    else:
        result["confidence_level"] = "low"

    return result


# ---------------------------------------------------------------------------
# WMGA (Weighted Mean Group Approval) consensus score
# ---------------------------------------------------------------------------

def _compute_consensus_score_wmga(
    idea_reactions: pd.DataFrame,
    total_votes: int,
) -> dict[str, Any]:
    """Compute consensus score using Weighted Mean Group Approval (WMGA).

    For each demographic group, computes a Bayesian-smoothed approval rate:
        smoothed = (group_up + prior_strength × overall_approval)
                 / (group_votes + prior_strength)

    The per-dimension score is the population-weighted mean of smoothed group
    approvals.  Final score = weighted-avg of dimensions × confidence × 100.

    Groups with zero votes shrink entirely to overall_approval, so they
    neither help nor hurt.  The prior_strength controls how aggressively
    small samples are pulled toward the global mean.
    """
    _ensure_user_demo_cache()
    _build_population_baseline()
    _build_platform_approval_rate()

    result: dict[str, Any] = {
        "wmga_score": None,
        "wmga_per_dimension": {},
    }

    if idea_reactions.empty or total_votes == 0:
        return result

    up = idea_reactions[idea_reactions["mode"] == "up"] if "mode" in idea_reactions.columns else pd.DataFrame()
    down = idea_reactions[idea_reactions["mode"] == "down"] if "mode" in idea_reactions.columns else pd.DataFrame()

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

    # Count reactions with at least one known demographic value
    def count_known(df: pd.DataFrame) -> int:
        count = 0
        for _, r in df.iterrows():
            uid = _clean_val(r.get("user_id"))
            d = _user_demo_cache.get(uid, {}) if uid else {}
            if any(d.get(dim) is not None for dim in BRIDGING_DIMENSIONS):
                count += 1
        return count

    total_known = count_known(up) + count_known(down)
    if total_known < BRIDGING_MIN_KNOWN_DEMO_REACTIONS:
        return result

    confidence = min(1.0, total_known / BRIDGING_FULL_CONFIDENCE_REACTIONS)

    coverage = _compute_dimension_coverage(up_demos, down_demos)
    effective_weights = _compute_effective_weights(coverage)

    per_dim: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        pop_dist = _population_baseline.get(dim, {})
        if not pop_dist:
            per_dim[dim] = 0.0
            continue

        up_counts = _bucket_counter([v for v in up_demos[dim] if v is not None])
        down_counts = _bucket_counter([v for v in down_demos[dim] if v is not None])

        dim_score = 0.0
        for group, pop_weight in pop_dist.items():
            group_up = up_counts.get(group, 0)
            group_total = group_up + down_counts.get(group, 0)
            smoothed = (
                (group_up + BRIDGING_WMGA_PRIOR_STRENGTH * _platform_approval_rate)
                / (group_total + BRIDGING_WMGA_PRIOR_STRENGTH)
            )
            dim_score += pop_weight * smoothed

        per_dim[dim] = dim_score

    # Per-dimension polarization penalty (same as JSD algorithm)
    polarization_scores: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        polarization_scores[dim] = _compute_group_approval_variance(up_demos, down_demos, dim)

    weighted_polarization = sum(
        effective_weights[dim] * polarization_scores[dim]
        for dim in BRIDGING_DIMENSIONS
    )
    polarization_factor = 1.0 - BRIDGING_POLARIZATION_PENALTY_WEIGHT * weighted_polarization

    # Apply polarization penalty per dimension for the breakdown display
    per_dim_penalized: dict[str, float] = {}
    for dim in BRIDGING_DIMENSIONS:
        penalty = 1.0 - BRIDGING_POLARIZATION_PENALTY_WEIGHT * polarization_scores[dim]
        per_dim_penalized[dim] = per_dim[dim] * penalty

    result["wmga_per_dimension"] = {dim: round(s, 4) for dim, s in per_dim_penalized.items()}
    result["wmga_polarization_scores"] = {dim: round(s, 4) for dim, s in polarization_scores.items()}
    result["wmga_polarization_penalty"] = round(weighted_polarization, 4)

    wmga_score = sum(
        effective_weights[dim] * per_dim[dim] for dim in BRIDGING_DIMENSIONS
    ) * polarization_factor * confidence * 100
    result["wmga_score"] = round(wmga_score, 2)

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

    # Consensus score (both algorithms)
    bridging = _compute_consensus_score(idea_reactions, total)
    wmga = _compute_consensus_score_wmga(idea_reactions, total)
    bridging.update(wmga)

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
