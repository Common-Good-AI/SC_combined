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

import pandas as pd

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
    "somewhat_liberal_10r": "Somewhat Liberal",
    "moderate_middle_of_the_road_jgq": "Moderate",
    "somewhat_conservative_3bp": "Somewhat Conservative",
    "very_conservative_iwv": "Very Conservative",
    "prefer_not_to_say_dgj": "Prefer not to say",
}

# Typeform political labels → canonical labels (align with GoVocal human-readable)
_TF_POLITICAL_NORMALIZE: dict[str, str] = {
    "Very Liberal": "Very Liberal",
    "Liberal": "Somewhat Liberal",
    "Moderate": "Moderate",
    "Conservative": "Somewhat Conservative",
    "Very Conservative": "Very Conservative",
    "Not sure": "Not sure",
}

# Extra race codes that may appear (not in the main map but valid)
_EXTRA_RACE_LABELS: dict[str, str] = {
    "native_hawaiian_or_pacific_islander_ujy": "Native Hawaiian or Pacific Islander",
}

AGE_BUCKETS = [
    (18, 24, "18-24"),
    (25, 34, "25-34"),
    (35, 44, "35-44"),
    (45, 54, "45-54"),
    (55, 64, "55-64"),
    (65, 200, "65+"),
]

# Zipcode → region / urban_rural lookup
_ZIPCODE_GEO: dict[str, dict[str, str]] = {}


_ZIPCODE_GEO_LOADED = False


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


def invalidate_cache() -> None:
    """Clear cached lookups (call after data refresh)."""
    global _user_demo_cache, _email_demo_cache, _userid_email_map, _ZIPCODE_GEO, _ZIPCODE_GEO_LOADED
    _user_demo_cache = {}
    _email_demo_cache = {}
    _userid_email_map = {}
    _ZIPCODE_GEO = {}
    _ZIPCODE_GEO_LOADED = False


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
    }
