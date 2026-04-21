"""Phase 2a analytics — high-level metrics computed from in-memory store.

Every public function reads from ``data_store.store`` (dict[str, DataFrame])
and returns a plain dict ready to be JSON-serialised by Flask.

Participant taxonomy
--------------------
1. **Confirmed users** – have a verified account in GoVocal; their email
   exists in ``gv_users``.
2. **Email-only users** – provided an email on a GoVocal idea (via
   ``custom_field_values.u_email_*``) or on a Typeform response, but that
   email does **not** appear in ``gv_users``.
3. **Anonymous users** – submitted a GoVocal survey or Typeform response
   with **no** email at all.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.api_client.gv_api import GoVocalClient
from backend.config import Config
from backend.data_store import store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Column names that may contain an email directly on a GV idea object
# (populated via custom_field_values during ingestion by pd.json_normalize).
_GV_IDEA_EMAIL_COLS: list[str] = [
    "custom_field_values.u_email_5vp",
    "custom_field_values.u_email_rzm",
]


def _normalise_emails(series: pd.Series) -> pd.Series:
    """Lowercase, strip, and remove blanks / NaN from an email column."""
    return (
        series
        .dropna()
        .astype(str)
        .str.strip()
        .str.lower()
        .loc[lambda s: s.ne("") & s.ne("nan") & s.ne("none")]
    )


def _emails_from_gv_ideas_via_users(df: pd.DataFrame) -> pd.Series:
    """Join gv_ideas (or a subset) to gv_users to resolve author_id → email."""
    if df.empty or "author_id" not in df.columns:
        return pd.Series(dtype=str)
    users = store.get("gv_users")
    if users is None or "email" not in users.columns:
        return pd.Series(dtype=str)
    merged = df[["author_id"]].merge(
        users[["id", "email"]].rename(columns={"id": "author_id"}),
        on="author_id",
        how="left",
    )
    return _normalise_emails(merged["email"])


def _emails_from_gv_idea_custom_fields(df: pd.DataFrame) -> set[str]:
    """Extract emails stored directly on idea objects (custom_field_values)."""
    emails: set[str] = set()
    if df.empty:
        return emails
    for col in _GV_IDEA_EMAIL_COLS:
        if col in df.columns:
            emails.update(_normalise_emails(df[col]).tolist())
    return emails


def _emails_from_gv_reactions(df: pd.DataFrame, idea_ids: set[str] | None = None) -> pd.Series:
    """Resolve reaction user_id → email, optionally filtering by reactable_id."""
    if df.empty:
        return pd.Series(dtype=str)
    if idea_ids is not None and "reactable_id" in df.columns:
        df = df[df["reactable_id"].isin(idea_ids)]
    if df.empty or "user_id" not in df.columns:
        return pd.Series(dtype=str)
    users = store.get("gv_users")
    if users is None or "email" not in users.columns:
        return pd.Series(dtype=str)
    merged = df[["user_id"]].merge(
        users[["id", "email"]].rename(columns={"id": "user_id"}),
        on="user_id",
        how="left",
    )
    return _normalise_emails(merged["email"])


def _tf_emails(df: pd.DataFrame) -> pd.Series:
    """Get normalised emails from a Typeform DataFrame (email or hidden_email)."""
    if df.empty:
        return pd.Series(dtype=str)
    if "email" in df.columns:
        return _normalise_emails(df["email"])
    if "hidden_email" in df.columns:
        return _normalise_emails(df["hidden_email"])
    return pd.Series(dtype=str)


def _all_tf_frames_with_keys() -> list[tuple[str, pd.DataFrame]]:
    """Return (key, DataFrame) pairs for all Typeform DataFrames."""
    return [(key, df) for key, df in store.items() if key.startswith("tf_")]


def _all_tf_frames() -> list[pd.DataFrame]:
    """Return all Typeform DataFrames from the store."""
    return [df for _, df in _all_tf_frames_with_keys()]


def _get_confirmed_emails() -> set[str]:
    """Return the set of confirmed user emails from gv_users."""
    users = store.get("gv_users")
    if users is None or "email" not in users.columns:
        return set()
    return set(_normalise_emails(users["email"]).tolist())


def _row_has_no_email(row: pd.Series) -> bool:
    """Return True when a GV idea row has no email anywhere."""
    for col in _GV_IDEA_EMAIL_COLS:
        if col in row.index:
            val = row[col]
            if pd.notna(val) and str(val).strip().lower() not in ("", "nan", "none"):
                return False
    return True


def _count_gv_ideas_anonymous(df: pd.DataFrame) -> int:
    """Count GV idea rows that have NO author_id AND no email in custom fields."""
    if df.empty or "author_id" not in df.columns:
        return 0
    null_author = df[df["author_id"].isna()]
    if null_author.empty:
        return 0
    return int(null_author.apply(_row_has_no_email, axis=1).sum())


def _count_tf_anonymous(df: pd.DataFrame) -> int:
    """Count Typeform rows with no email at all."""
    if df.empty:
        return 0
    if "email" in df.columns:
        blank = (
            df["email"].isna()
            | df["email"].astype(str).str.strip().eq("")
            | df["email"].astype(str).str.strip().str.lower().eq("nan")
        )
        return int(blank.sum())
    if "hidden_email" in df.columns:
        blank = (
            df["hidden_email"].isna()
            | df["hidden_email"].astype(str).str.strip().eq("")
            | df["hidden_email"].astype(str).str.strip().str.lower().eq("nan")
        )
        return int(blank.sum())
    # No email column → every row is anonymous
    return len(df)


# ---------------------------------------------------------------------------
# 1. Total participants (3-tier model)
# ---------------------------------------------------------------------------

def compute_total_participants() -> dict[str, Any]:
    """Compute participants in three tiers:

    1. **Confirmed users** — unique emails in ``gv_users``.
    2. **Email-only users** — emails found on GV idea custom fields or
       Typeform responses that are **not** in ``gv_users``.
    3. **Anonymous users** — GV survey / Typeform submissions with no email.

    ``total = confirmed + email_only + anonymous``
    """
    # --- Tier 1: Confirmed users ---
    confirmed_emails = _get_confirmed_emails()

    # --- Tier 2: Email-only users ---
    # Collect every email that appears on an action (idea custom fields + TF)
    action_emails: set[str] = set()

    # GV ideas — emails in custom_field_values
    gv_ideas = store.get("gv_ideas", pd.DataFrame())
    if not gv_ideas.empty:
        action_emails.update(_emails_from_gv_idea_custom_fields(gv_ideas))

    # Typeform
    for tf_df in _all_tf_frames():
        action_emails.update(set(_tf_emails(tf_df).tolist()))

    email_only_emails = action_emails - confirmed_emails
    email_only_count = len(email_only_emails)

    # --- Tier 3: Anonymous users ---
    anon_breakdown: dict[str, int] = {}

    # GV surveys — null author_id AND no email in custom fields
    gv_survey = store.get("gv_ideas_survey", pd.DataFrame())
    anon_breakdown["govocal_surveys"] = _count_gv_ideas_anonymous(gv_survey)

    # GV ideation — null author_id AND no email in custom fields
    gv_ideation = store.get("gv_ideas_ideation", pd.DataFrame())
    anon_breakdown["govocal_ideation"] = _count_gv_ideas_anonymous(gv_ideation)

    # Typeform
    for key, tf_df in _all_tf_frames_with_keys():
        anon_breakdown[key] = _count_tf_anonymous(tf_df)

    anonymous_count = sum(anon_breakdown.values())

    confirmed_count = len(confirmed_emails)
    total = confirmed_count + email_only_count + anonymous_count

    return {
        "total": total,
        "confirmed_users": confirmed_count,
        "email_only_users": email_only_count,
        "anonymous_users": anonymous_count,
        "anonymous_breakdown": anon_breakdown,
        "detail": {
            "total_emails_on_ideas": len(
                _emails_from_gv_idea_custom_fields(gv_ideas) if not gv_ideas.empty else set()
            ),
            "total_emails_on_typeform": len(action_emails - _emails_from_gv_idea_custom_fields(gv_ideas) if not gv_ideas.empty else action_emails),
            "overlap_action_emails_and_confirmed": len(action_emails & confirmed_emails),
        },
    }


# ---------------------------------------------------------------------------
# 2. Total actions
# ---------------------------------------------------------------------------

def compute_total_actions() -> dict[str, Any]:
    """An action = survey submit OR idea submit OR GoVocal reaction."""
    # Survey submits: GV survey-type ideas + all Typeform rows
    gv_survey_count = len(store.get("gv_ideas_survey", pd.DataFrame()))
    tf_count = sum(len(df) for df in _all_tf_frames())
    survey_submits = gv_survey_count + tf_count

    # Ideas submitted (ideation type in GV)
    ideas_submitted = len(store.get("gv_ideas_ideation", pd.DataFrame()))

    # Reactions
    reactions = len(store.get("gv_reactions", pd.DataFrame()))

    # Comments
    comments = len(store.get("gv_comments", pd.DataFrame()))

    return {
        "survey_submits": survey_submits,
        "survey_submits_breakdown": {
            "govocal_surveys": gv_survey_count,
            "typeform_surveys": tf_count,
        },
        "ideas_submitted": ideas_submitted,
        "reactions": reactions,
        "comments": comments,
        "total": survey_submits + ideas_submitted + reactions,
    }


# ---------------------------------------------------------------------------
# 2b. Action distributions (per-user percentile curves)
# ---------------------------------------------------------------------------

def _user_action_distribution(df: pd.DataFrame, user_col: str) -> dict:
    """Return percentile-based distribution of per-user action counts."""
    if df.empty or user_col not in df.columns:
        return {"percentiles": [], "counts": [], "total_users": 0,
                "max_actions": 0, "median_actions": 0, "mean_actions": 0}

    per_user = np.array(df.groupby(user_col).size().sort_values().values)
    n = len(per_user)
    percentiles = list(range(0, 101))
    counts = []
    for p in percentiles:
        idx = int(np.clip(np.floor(p / 100 * (n - 1)), 0, n - 1))
        counts.append(int(per_user[idx]))

    return {
        "percentiles": percentiles,
        "counts": counts,
        "total_users": n,
        "max_actions": int(per_user[-1]) if n else 0,
        "median_actions": int(np.median(per_user)) if n else 0,
        "mean_actions": round(float(np.mean(per_user)), 1) if n else 0,
    }


def compute_action_distributions() -> dict[str, Any]:
    """Per-user action count distributions for reactions, ideas, and comments."""
    reactions_df = store.get("gv_reactions", pd.DataFrame())
    ideas_df = store.get("gv_ideas_ideation", pd.DataFrame())
    comments_df = store.get("gv_comments", pd.DataFrame())

    return {
        "reactions": _user_action_distribution(reactions_df, "user_id"),
        "ideas": _user_action_distribution(ideas_df, "author_id"),
        "comments": _user_action_distribution(comments_df, "author_id"),
    }


# ---------------------------------------------------------------------------
# 3. Conversion rates (Typeform + GoVocal, kept separate)
# ---------------------------------------------------------------------------

def compute_conversion_rate() -> dict[str, Any]:
    """Two separate conversion rates:

    **Typeform conversion rate**
        Typeform emails that also exist in gv_users  /  total Typeform submissions
        → "what % of Typeform submissions came from confirmed GoVocal users"

    **GoVocal conversion rate**
        GoVocal survey emails that also exist in gv_users  /  total GoVocal survey submissions
        → "what % of GoVocal survey submissions came from confirmed GoVocal users"
    """
    confirmed_emails = _get_confirmed_emails()

    # --- Typeform ---
    tf_total_submissions = 0
    tf_emails_all: set[str] = set()
    for tf_df in _all_tf_frames():
        tf_total_submissions += len(tf_df)
        tf_emails_all.update(set(_tf_emails(tf_df).tolist()))

    tf_emails_in_gv = tf_emails_all & confirmed_emails
    tf_rate = (
        (len(tf_emails_in_gv) / tf_total_submissions * 100)
        if tf_total_submissions else 0.0
    )

    # --- GoVocal surveys ---
    gv_survey = store.get("gv_ideas_survey", pd.DataFrame())
    gv_survey_total = len(gv_survey)

    # Collect all emails from GoVocal surveys (author_id→users + custom fields)
    gv_survey_emails: set[str] = set()
    if not gv_survey.empty:
        gv_survey_emails.update(
            set(_emails_from_gv_ideas_via_users(gv_survey).tolist())
        )
        gv_survey_emails.update(_emails_from_gv_idea_custom_fields(gv_survey))
    gv_survey_emails.discard("")

    gv_emails_in_users = gv_survey_emails & confirmed_emails
    gv_rate = (
        (len(gv_emails_in_users) / gv_survey_total * 100)
        if gv_survey_total else 0.0
    )

    return {
        "typeform": {
            "total_submissions": tf_total_submissions,
            "emails_in_govocal_users": len(tf_emails_in_gv),
            "conversion_rate_pct": round(tf_rate, 2),
        },
        "govocal": {
            "total_submissions": gv_survey_total,
            "emails_in_govocal_users": len(gv_emails_in_users),
            "conversion_rate_pct": round(gv_rate, 2),
        },
    }


# ---------------------------------------------------------------------------
# 4. Idea selection breakdown (Typeform question + GV initial survey)
# ---------------------------------------------------------------------------

def _find_idea_question_column(df: pd.DataFrame) -> str | None:
    """Find the column whose title matches the idea-selection question."""
    pattern = Config.TF_IDEA_QUESTION_PATTERN
    for col in df.columns:
        if re.search(pattern, col):
            return col
    return None


# Mapping from GV initial-survey coded theme values
# (custom_field_values.your_question_op0) to human-readable labels that align
# with the Typeform theme labels where possible.
_GV_THEME_LABELS: dict[str, str] = {
    "political_reform_t2g": "Political Reform and Governance",
    "healthcare_access_and_affordability_ch0": "Health Care Costs and Access",
    "quality_education_qvy": "Fixing Our Schools",
    "wages_and_job_development_q0c": "Jobs, Wages, and Rising Costs",
    "option1": "Roads, Traffic, and Infrastructure",
    "option2": "Affordable Housing and Community Growth",
}

_GV_INITIAL_SURVEY_PROJECT_ID = "b3808271-ec77-485f-b028-7b9a25cf37ed"
_GV_INITIAL_SURVEY_THEME_COL = "custom_field_values.your_question_op0"


def compute_idea_selection_breakdown() -> dict[str, Any]:
    """Which themes were selected most across all survey responses.

    Combines:
    - Typeform responses (question matching TF_IDEA_QUESTION_PATTERN)
    - GoVocal initial survey (custom_field_values.your_question_op0)
    """
    all_selections: list[str] = []
    matched_column: str | None = None

    # --- Typeform responses ---
    for tf_df in _all_tf_frames():
        if tf_df.empty:
            continue
        col = _find_idea_question_column(tf_df)
        if col is None:
            continue
        matched_column = col
        raw = tf_df[col].dropna().astype(str)
        for val in raw:
            val = val.strip()
            if val:
                all_selections.append(val)

    # --- GoVocal initial survey responses ---
    gv_survey = store.get("gv_ideas_survey")
    if gv_survey is not None and not gv_survey.empty:
        initial = gv_survey[gv_survey["project_id"] == _GV_INITIAL_SURVEY_PROJECT_ID]
        if _GV_INITIAL_SURVEY_THEME_COL in initial.columns:
            raw = initial[_GV_INITIAL_SURVEY_THEME_COL].dropna().astype(str)
            for val in raw:
                val = val.strip()
                if val and val not in ("nan", "NaN", ""):
                    label = _GV_THEME_LABELS.get(val, val)
                    all_selections.append(label)

    if not all_selections:
        return {
            "question_title": matched_column,
            "total_responses": 0,
            "selections": [],
            "note": "No matching question found or no selections recorded.",
        }

    counts = pd.Series(all_selections).value_counts()
    total = int(counts.sum())
    selections = [
        {
            "idea": idea,
            "count": int(cnt),
            "percentage": round(cnt / total * 100, 2),
        }
        for idea, cnt in counts.items()
    ]

    return {
        "question_title": matched_column,
        "total_responses": total,
        "selections": selections,
    }


# ---------------------------------------------------------------------------
# 5. Idea tag breakdown
# ---------------------------------------------------------------------------

def compute_idea_tags_breakdown() -> dict[str, Any]:
    """Count ideas per input topic (tag) using the ideas_input_topics join table.

    Returns a list of {tag, topic_id, count} dicts sorted by count descending,
    plus the total number of distinct tagged ideas and total distinct topics.
    """
    topics_df = store.get("gv_input_topics", pd.DataFrame())
    join_df = store.get("gv_ideas_input_topics", pd.DataFrame())

    if join_df.empty or "input_topic_id" not in join_df.columns:
        return {"total_tagged_ideas": 0, "total_tags": 0, "tags": []}

    idea_col = "idea_id" if "idea_id" in join_df.columns else None

    if idea_col:
        counts = (
            join_df.groupby("input_topic_id")[idea_col]
            .nunique()
            .reset_index()
            .rename(columns={idea_col: "count"})
        )
    else:
        counts = (
            join_df["input_topic_id"]
            .value_counts()
            .reset_index()
            .rename(columns={"index": "input_topic_id", "input_topic_id": "count"})
        )

    name_map: dict = {}
    if not topics_df.empty and "id" in topics_df.columns and "title" in topics_df.columns:
        name_map = topics_df.set_index("id")["title"].to_dict()

    tags = [
        {
            "tag": name_map.get(row["input_topic_id"], row["input_topic_id"]),
            "topic_id": row["input_topic_id"],
            "count": int(row["count"]),
        }
        for _, row in counts.sort_values("count", ascending=False).iterrows()
    ]

    total_tagged = int(join_df[idea_col].nunique()) if idea_col else int(len(join_df))

    return {
        "total_tagged_ideas": total_tagged,
        "total_tags": len(tags),
        "tags": tags,
    }


# ---------------------------------------------------------------------------
# 5b. Votes (upvotes / downvotes) aggregated by tag
# ---------------------------------------------------------------------------

def compute_votes_by_tag() -> dict[str, Any]:
    """Aggregate upvotes and downvotes per input topic (tag).

    Joins ``gv_ideas_input_topics`` with ``gv_reactions`` so that each
    idea's votes count toward every tag assigned to it.

    Returns ``{tags: [{tag, topic_id, upvotes, downvotes, net}]}``
    sorted by *net* descending.
    """
    topics_df = store.get("gv_input_topics", pd.DataFrame())
    join_df = store.get("gv_ideas_input_topics", pd.DataFrame())
    reactions_df = store.get("gv_reactions", pd.DataFrame())

    if join_df.empty or "input_topic_id" not in join_df.columns:
        return {"tags": []}

    idea_col = "idea_id" if "idea_id" in join_df.columns else None
    if idea_col is None:
        return {"tags": []}

    # Build tag name lookup
    name_map: dict = {}
    if not topics_df.empty and "id" in topics_df.columns and "title" in topics_df.columns:
        name_map = topics_df.set_index("id")["title"].to_dict()

    # Map each idea to its tags
    idea_tags = join_df[[idea_col, "input_topic_id"]].copy()

    if reactions_df.empty or "reactable_id" not in reactions_df.columns:
        # No reactions — return zeroed-out entries
        tag_ids = idea_tags["input_topic_id"].unique()
        tags = [
            {
                "tag": name_map.get(tid, tid),
                "topic_id": tid,
                "upvotes": 0,
                "downvotes": 0,
                "net": 0,
            }
            for tid in tag_ids
        ]
        return {"tags": tags}

    # Join reactions to idea-tag associations
    merged = idea_tags.merge(
        reactions_df[["reactable_id", "mode"]],
        left_on=idea_col,
        right_on="reactable_id",
        how="inner",
    )

    if merged.empty:
        tag_ids = idea_tags["input_topic_id"].unique()
        tags = [
            {
                "tag": name_map.get(tid, tid),
                "topic_id": tid,
                "upvotes": 0,
                "downvotes": 0,
                "net": 0,
            }
            for tid in tag_ids
        ]
        return {"tags": tags}

    # Aggregate upvotes / downvotes per tag
    merged["is_up"] = (merged["mode"] == "up").astype(int)
    merged["is_down"] = (merged["mode"] == "down").astype(int)

    agg = (
        merged.groupby("input_topic_id")[["is_up", "is_down"]]
        .sum()
        .reset_index()
        .rename(columns={"is_up": "upvotes", "is_down": "downvotes"})
    )
    agg["net"] = agg["upvotes"] - agg["downvotes"]

    tags = [
        {
            "tag": name_map.get(row["input_topic_id"], row["input_topic_id"]),
            "topic_id": row["input_topic_id"],
            "upvotes": int(row["upvotes"]),
            "downvotes": int(row["downvotes"]),
            "net": int(row["net"]),
        }
        for _, row in agg.sort_values("net", ascending=False).iterrows()
    ]

    return {"tags": tags}


# ---------------------------------------------------------------------------
# 6. Participation breakdown (3-tier model)
# ---------------------------------------------------------------------------

def _source_participation_gv_ideas(label: str, df: pd.DataFrame) -> dict:
    """Per-source participation for a GV ideas DataFrame (surveys or ideation).

    Uses 3-tier model:
    - confirmed:  emails resolved via author_id → gv_users
                  + emails in custom fields that ARE in gv_users
    - email_only: emails in custom fields NOT in gv_users
    - anonymous:  rows with null author_id AND no email in custom fields
    """
    if df.empty:
        return {
            "source": label, "actions": 0,
            "confirmed_users": 0, "email_only_users": 0, "anonymous_users": 0,
        }

    confirmed_emails = _get_confirmed_emails()
    actions = len(df)

    # All emails from this source: author_id→users + custom field emails
    source_emails: set[str] = set()
    source_emails.update(set(_emails_from_gv_ideas_via_users(df).tolist()))
    source_emails.update(_emails_from_gv_idea_custom_fields(df))
    source_emails.discard("")

    confirmed = len(source_emails & confirmed_emails)
    email_only = len(source_emails - confirmed_emails)
    anonymous = _count_gv_ideas_anonymous(df)

    return {
        "source": label,
        "actions": actions,
        "confirmed_users": confirmed,
        "email_only_users": email_only,
        "anonymous_users": anonymous,
    }


def _source_participation_gv_reactions(label: str, df: pd.DataFrame) -> dict:
    """Per-source participation for GV reactions.

    Reactions only have user_id (→ gv_users).  No custom field emails.
    Rows with null user_id are anonymous.
    """
    if df.empty:
        return {
            "source": label, "actions": 0,
            "confirmed_users": 0, "email_only_users": 0, "anonymous_users": 0,
        }

    confirmed_emails = _get_confirmed_emails()
    actions = len(df)

    # All reaction emails come from gv_users → they are confirmed by definition
    reaction_emails = set(_emails_from_gv_reactions(df).tolist())
    reaction_emails.discard("")
    confirmed = len(reaction_emails & confirmed_emails)
    # Reactions don't carry custom-field emails, so email_only = 0
    email_only = 0
    anonymous = int(df["user_id"].isna().sum()) if "user_id" in df.columns else 0

    return {
        "source": label,
        "actions": actions,
        "confirmed_users": confirmed,
        "email_only_users": email_only,
        "anonymous_users": anonymous,
    }


def _source_participation_tf(label: str, df: pd.DataFrame) -> dict:
    """Per-source participation for a single Typeform DataFrame."""
    if df.empty:
        return {
            "source": label, "actions": 0,
            "confirmed_users": 0, "email_only_users": 0, "anonymous_users": 0,
        }

    confirmed_emails = _get_confirmed_emails()
    actions = len(df)

    tf_email_set = set(_tf_emails(df).tolist())
    tf_email_set.discard("")
    confirmed = len(tf_email_set & confirmed_emails)
    email_only = len(tf_email_set - confirmed_emails)
    anonymous = _count_tf_anonymous(df)

    return {
        "source": label,
        "actions": actions,
        "confirmed_users": confirmed,
        "email_only_users": email_only,
        "anonymous_users": anonymous,
    }


def compute_participation_breakdown() -> dict[str, Any]:
    """Per-source and overall participation using the 3-tier model."""

    sources: list[dict] = []

    # GV surveys
    gv_surveys = store.get("gv_ideas_survey", pd.DataFrame())
    sources.append(_source_participation_gv_ideas("govocal_surveys", gv_surveys))

    # GV ideation
    gv_ideation = store.get("gv_ideas_ideation", pd.DataFrame())
    sources.append(_source_participation_gv_ideas("govocal_ideation", gv_ideation))

    # GV reactions
    gv_reactions = store.get("gv_reactions", pd.DataFrame())
    sources.append(_source_participation_gv_reactions("govocal_reactions", gv_reactions))

    # Typeform (per form)
    for key, tf_df in _all_tf_frames_with_keys():
        sources.append(_source_participation_tf(key, tf_df))

    # Overall (use the deduplicated compute_total_participants)
    totals = compute_total_participants()
    total_actions = compute_total_actions()

    return {
        "per_source": sources,
        "overall": {
            "total_actions": total_actions["total"],
            "confirmed_users": totals["confirmed_users"],
            "email_only_users": totals["email_only_users"],
            "anonymous_users": totals["anonymous_users"],
            "total_participants": totals["total"],
        },
    }


# ---------------------------------------------------------------------------
# Participation timeline (daily counts by action type)
# ---------------------------------------------------------------------------

def compute_participation_timeline() -> dict[str, Any]:
    """Aggregate daily participation counts by action type.

    Returns a sorted list of ``{date, surveys, ideas, reactions, total}``
    dictionaries — one per calendar day that has at least one action.
    """
    records: list[dict[str, Any]] = []

    # --- Surveys: GV survey ideas + all Typeform submissions ---
    gv_survey = store.get("gv_ideas_survey", pd.DataFrame())
    if not gv_survey.empty and "created_at" in gv_survey.columns:
        dates = pd.to_datetime(gv_survey["created_at"], errors="coerce", utc=True).dt.date
        for d, cnt in dates.value_counts().items():
            records.append({"date": str(d), "category": "surveys", "count": int(cnt)})

    for tf_df in _all_tf_frames():
        ts_col = "submitted_at" if "submitted_at" in tf_df.columns else "created_at"
        if ts_col not in tf_df.columns or tf_df.empty:
            continue
        dates = pd.to_datetime(tf_df[ts_col], errors="coerce", utc=True).dt.date
        for d, cnt in dates.value_counts().items():
            records.append({"date": str(d), "category": "surveys", "count": int(cnt)})

    # --- Ideas: GV ideation ---
    gv_ideation = store.get("gv_ideas_ideation", pd.DataFrame())
    if not gv_ideation.empty and "created_at" in gv_ideation.columns:
        dates = pd.to_datetime(gv_ideation["created_at"], errors="coerce", utc=True).dt.date
        for d, cnt in dates.value_counts().items():
            records.append({"date": str(d), "category": "ideas", "count": int(cnt)})

    # --- Reactions ---
    gv_reactions = store.get("gv_reactions", pd.DataFrame())
    if not gv_reactions.empty and "created_at" in gv_reactions.columns:
        dates = pd.to_datetime(gv_reactions["created_at"], errors="coerce", utc=True).dt.date
        for d, cnt in dates.value_counts().items():
            records.append({"date": str(d), "category": "reactions", "count": int(cnt)})

    if not records:
        return {"timeline": []}

    df = pd.DataFrame(records)
    pivot = (
        df.groupby(["date", "category"])["count"]
        .sum()
        .unstack(fill_value=0)
        .sort_index()
    )

    for col in ("surveys", "ideas", "reactions"):
        if col not in pivot.columns:
            pivot[col] = 0

    pivot["total"] = pivot["surveys"] + pivot["ideas"] + pivot["reactions"]

    timeline = []
    for date_val, row in pivot.iterrows():
        timeline.append({
            "date": str(date_val),
            "surveys": int(row["surveys"]),
            "ideas": int(row["ideas"]),
            "reactions": int(row["reactions"]),
            "total": int(row["total"]),
        })

    return {"timeline": timeline}


# ---------------------------------------------------------------------------
# Unique participants over time
# ---------------------------------------------------------------------------

def compute_participants_timeline() -> dict[str, Any]:
    """Track when each unique participant first appeared in the system.

    Returns a daily timeline with ``new`` (new unique participants that day)
    for each of three participant tiers: *confirmed*, *email_only*, and
    *anonymous*.  The frontend can compute cumulative totals.
    """
    confirmed_emails = _get_confirmed_emails()

    # ── Collect (identifier, first-seen date) for every participant ──
    # identified participants keyed by normalised email → earliest date
    email_first_seen: dict[str, str] = {}  # email → "YYYY-MM-DD"

    def _record_email(email: str, date_str: str) -> None:
        """Track the earliest date for this email."""
        prev = email_first_seen.get(email)
        if prev is None or date_str < prev:
            email_first_seen[email] = date_str

    # -- Confirmed users: registration date from gv_users --
    gv_users = store.get("gv_users", pd.DataFrame())
    if not gv_users.empty and "email" in gv_users.columns:
        ts_col = (
            "registration_completed_at"
            if "registration_completed_at" in gv_users.columns
            else "created_at"
        )
        if ts_col in gv_users.columns:
            tmp = gv_users[["email", ts_col]].copy()
            tmp["_email"] = _normalise_emails(tmp["email"])
            tmp["_date"] = pd.to_datetime(tmp[ts_col], errors="coerce", utc=True).dt.date
            for _, row in tmp.dropna(subset=["_email", "_date"]).iterrows():
                _record_email(row["_email"], str(row["_date"]))

    # -- GV ideas (survey + ideation): author_id → email + custom fields --
    for key in ("gv_ideas_survey", "gv_ideas_ideation"):
        df = store.get(key, pd.DataFrame())
        if df.empty or "created_at" not in df.columns:
            continue
        df = df.copy()
        df["_date"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True).dt.date

        # via author_id → gv_users join (merge locally to keep date aligned)
        if "author_id" in df.columns:
            users_df = store.get("gv_users")
            if users_df is not None and "email" in users_df.columns:
                tmp = df[["author_id", "_date"]].merge(
                    users_df[["id", "email"]].rename(columns={"id": "author_id"}),
                    on="author_id",
                    how="left",
                )
                for _, row in tmp.dropna(subset=["email", "_date"]).iterrows():
                    email = str(row["email"]).strip().lower()
                    if email and email not in ("nan", "none"):
                        _record_email(email, str(row["_date"]))

        # via custom_field_values email columns (index is preserved — safe to use)
        for col in _GV_IDEA_EMAIL_COLS:
            if col in df.columns:
                normed = _normalise_emails(df[col])
                for idx, email in normed.items():
                    d = df.at[idx, "_date"] if idx in df.index else None
                    if d is not None and pd.notna(d):
                        _record_email(email, str(d))

    # -- GV reactions: user_id → email (merge locally to keep date aligned) --
    gv_reactions = store.get("gv_reactions", pd.DataFrame())
    if not gv_reactions.empty and "created_at" in gv_reactions.columns:
        users_df = store.get("gv_users")
        if users_df is not None and "email" in users_df.columns:
            tmp = gv_reactions[["user_id", "created_at"]].copy()
            tmp["_date"] = pd.to_datetime(tmp["created_at"], errors="coerce", utc=True).dt.date
            merged = tmp.merge(
                users_df[["id", "email"]].rename(columns={"id": "user_id"}),
                on="user_id",
                how="left",
            )
            for _, row in merged.dropna(subset=["email", "_date"]).iterrows():
                email = str(row["email"]).strip().lower()
                if email and email not in ("nan", "none"):
                    _record_email(email, str(row["_date"]))

    # -- Typeform: email column (reset_index to ensure 0-based alignment) --
    for _key, tf_df in _all_tf_frames_with_keys():
        if tf_df.empty:
            continue
        ts_col = "submitted_at" if "submitted_at" in tf_df.columns else "created_at"
        if ts_col not in tf_df.columns:
            continue
        tf_clean = tf_df.reset_index(drop=True)
        dates = pd.to_datetime(tf_clean[ts_col], errors="coerce", utc=True).dt.date
        tf_em = _tf_emails(tf_clean)
        for idx, email in tf_em.items():
            d = dates.get(idx)
            if d is not None and pd.notna(d):
                _record_email(email, str(d))

    # ── Anonymous participants (no email → each row is unique) ──
    anon_dates: list[str] = []

    for key in ("gv_ideas_survey", "gv_ideas_ideation"):
        df = store.get(key, pd.DataFrame())
        if df.empty or "created_at" not in df.columns:
            continue
        dates = pd.to_datetime(df["created_at"], errors="coerce", utc=True).dt.date
        for idx, row in df.iterrows():
            has_author = pd.notna(row.get("author_id"))
            has_email = not _row_has_no_email(row)
            if not has_author and not has_email:
                d = dates.get(idx)
                if d is not None and pd.notna(d):
                    anon_dates.append(str(d))

    for _key, tf_df in _all_tf_frames_with_keys():
        if tf_df.empty:
            continue
        ts_col = "submitted_at" if "submitted_at" in tf_df.columns else "created_at"
        if ts_col not in tf_df.columns:
            continue
        dates = pd.to_datetime(tf_df[ts_col], errors="coerce", utc=True).dt.date
        for idx, _row in tf_df.iterrows():
            email_val = tf_df.at[idx, "email"] if "email" in tf_df.columns else (
                tf_df.at[idx, "hidden_email"] if "hidden_email" in tf_df.columns else None
            )
            is_blank = (
                email_val is None
                or pd.isna(email_val)
                or str(email_val).strip().lower() in ("", "nan", "none")
            )
            if is_blank:
                d = dates.get(idx)
                if d is not None and pd.notna(d):
                    anon_dates.append(str(d))

    # ── Build daily new-participant counts ──
    records: list[dict[str, Any]] = []

    for email, d in email_first_seen.items():
        tier = "confirmed" if email in confirmed_emails else "email_only"
        records.append({"date": d, "tier": tier})

    for d in anon_dates:
        records.append({"date": d, "tier": "anonymous"})

    if not records:
        return {"timeline": []}

    df_rec = pd.DataFrame(records)
    pivot = (
        df_rec.groupby(["date", "tier"])
        .size()
        .unstack(fill_value=0)
        .sort_index()
    )

    for col in ("confirmed", "email_only", "anonymous"):
        if col not in pivot.columns:
            pivot[col] = 0

    pivot["total"] = pivot["confirmed"] + pivot["email_only"] + pivot["anonymous"]

    timeline = []
    for date_val, row in pivot.iterrows():
        timeline.append({
            "date": str(date_val),
            "confirmed": int(row["confirmed"]),
            "email_only": int(row["email_only"]),
            "anonymous": int(row["anonymous"]),
            "total": int(row["total"]),
        })

    return {"timeline": timeline}


# ---------------------------------------------------------------------------
# Visits timeline (GoVocal Insights API)
# ---------------------------------------------------------------------------

def compute_visits_timeline() -> dict[str, Any]:
    """Fetch daily visit counts from the GoVocal Insights Visits API.

    Returns ``{visits: [{date, visitors, visits}, ...]}``.  The API
    response uses ``date_group`` for the date, ``visitors`` for unique
    visitors, and ``visits`` for total page-loads.
    """
    try:
        gv = GoVocalClient()
        raw = gv.get_visits(resolution="day")
    except Exception as exc:
        log.error("Failed to fetch visits: %s", exc)
        return {"visits": [], "error": str(exc)}

    visits: list[dict[str, Any]] = []
    for entry in raw:
        date_val = entry.get("date_group")
        if date_val is None:
            continue
        date_str = str(date_val)[:10]
        visits.append({
            "date": date_str,
            "visitors": int(entry.get("visitors", 0)),
            "visits": int(entry.get("visits", 0)),
        })

    visits.sort(key=lambda v: v["date"])
    return {"visits": visits}


# ---------------------------------------------------------------------------
# GoVocal Contribution Rate (users vs visits)
# ---------------------------------------------------------------------------

def compute_participation_rate() -> dict[str, Any]:
    """Compute GoVocal Contribution Rate over 24 h, 36 h and 7 days.

    Participation rate = (GoVocal registered users / total visits) * 100.

    We count *new* GoVocal users created within each window and total visits
    within the same window.
    """
    now = datetime.now(timezone.utc)
    windows = {
        "24h": now - timedelta(hours=24),
        "72h": now - timedelta(hours=72),
        "7d": now - timedelta(days=7),
    }

    # --- Users created within each window ---
    gv_users = store.get("gv_users", pd.DataFrame())
    user_counts: dict[str, int] = {}
    if not gv_users.empty:
        ts_col = (
            "registration_completed_at"
            if "registration_completed_at" in gv_users.columns
            else "created_at"
        )
        if ts_col in gv_users.columns:
            gv_users = gv_users.copy()
            gv_users["_ts"] = pd.to_datetime(gv_users[ts_col], errors="coerce", utc=True)
            for label, cutoff in windows.items():
                user_counts[label] = int((gv_users["_ts"] >= cutoff).sum())
        else:
            for label in windows:
                user_counts[label] = 0
    else:
        for label in windows:
            user_counts[label] = 0

    # --- Total users (all time) ---
    total_users = len(gv_users)

    # --- Visits within each window ---
    visits_data = compute_visits_timeline()
    visits_list = visits_data.get("visits", [])

    visit_counts: dict[str, int] = {}
    total_visits = sum(v["visitors"] for v in visits_list)
    for label, cutoff in windows.items():
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        visit_counts[label] = sum(
            v["visitors"] for v in visits_list if v["date"] >= cutoff_str
        )

    # --- Rates ---
    rates: dict[str, Any] = {}
    for label in windows:
        users = user_counts[label]
        visits = visit_counts[label]
        rate = (users / visits * 100) if visits else 0.0
        rates[label] = {
            "users": users,
            "visits": visits,
            "rate_pct": round(rate, 2),
        }

    # All-time rate
    all_time_rate = (total_users / total_visits * 100) if total_visits else 0.0

    return {
        "label": "GoVocal Contribution Rate",
        "rates": rates,
        "all_time": {
            "users": total_users,
            "visits": total_visits,
            "rate_pct": round(all_time_rate, 2),
        },
    }


# ---------------------------------------------------------------------------
# Combined views (GoVocal + Typeform)
# ---------------------------------------------------------------------------

def compute_combined_views() -> dict[str, Any]:
    """Aggregate total visits from GoVocal (Insights API) and Typeform (Metrics API).

    Returns per-source breakdowns plus a combined total.
    """
    from backend.api_client.typeform_api import TypeformClient

    # ── GoVocal visits ───────────────────────────────────────────────────
    gv_total_visitors = 0
    gv_total_visits = 0
    try:
        gv = GoVocalClient()
        raw = gv.get_visits(resolution="day")
        for entry in raw:
            gv_total_visitors += int(entry.get("visitors", 0))
            gv_total_visits += int(entry.get("visits", 0))
    except Exception as exc:
        log.error("Failed to fetch GoVocal visits for combined views: %s", exc)

    # ── Typeform visits ──────────────────────────────────────────────────
    tf_forms: list[dict[str, Any]] = []
    tf_total_visits = 0
    tf_total_unique = 0
    tf_total_submissions = 0
    try:
        tf = TypeformClient()
        tf_forms = tf.get_all_form_views()
        for f in tf_forms:
            tf_total_visits += f.get("visits", 0)
            tf_total_unique += f.get("unique_visitors", 0)
            tf_total_submissions += f.get("submissions", 0)
    except Exception as exc:
        log.error("Failed to fetch Typeform metrics for combined views: %s", exc)

    combined_total = gv_total_visitors + tf_total_visits

    return {
        "combined_total_visits": combined_total,
        "govocal": {
            "visitors": gv_total_visitors,
            "page_loads": gv_total_visits,
        },
        "typeform": {
            "total_visits": tf_total_visits,
            "total_unique": tf_total_unique,
            "total_submissions": tf_total_submissions,
            "forms": tf_forms,
        },
    }


# ---------------------------------------------------------------------------
# All-in-one summary
# ---------------------------------------------------------------------------

def compute_all() -> dict[str, Any]:
    """Return every Phase 2a metric in a single payload."""
    return {
        "total_participants": compute_total_participants(),
        "total_actions": compute_total_actions(),
        "conversion_rate": compute_conversion_rate(),
        "idea_selection_breakdown": compute_idea_selection_breakdown(),
        "participation_breakdown": compute_participation_breakdown(),
    }
