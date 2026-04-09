"""In-memory data store.

Fetches data from GoVocal and Typeform, normalises it into pandas
DataFrames, and exposes them through a simple module-level dict.
All data lives in RAM — no database required.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.api_client.gv_api import GoVocalClient
from backend.api_client.typeform_api import TypeformClient
from backend.config import Config

log = logging.getLogger(__name__)

# ── Module-level store ───────────────────────────────────────────────────
# Keys are descriptive names; values are DataFrames.
store: dict[str, pd.DataFrame] = {}

# Metadata about the last refresh
meta: dict[str, Any] = {
    "last_refresh": None,
    "errors": [],
    "status": "not_loaded",
}

# Per-resource timestamps for incremental fetching (ISO 8601 strings)
_last_fetch: dict[str, str] = {}

# ── Demographic field helpers ────────────────────────────────────────────
# These are the demographic fields we want to extract from GoVocal's
# custom_field_values dicts.  Map from *possible* API key variants to our
# canonical column name.
_DEMO_ALIASES: dict[str, list[str]] = {
    "age": ["age", "Age", "age_group", "age_range"],
    "zipcode": ["zipcode", "zip_code", "zip", "postal_code", "Zipcode", "ZipCode"],
    "political_lean": ["political_lean", "political_leaning", "politics", "Political Lean"],
    "race": ["race", "ethnicity", "Race", "Ethnicity", "race_ethnicity"],
}


def _extract_demographics(custom: dict | None) -> dict[str, Any]:
    """Pull canonical demographic columns from a GoVocal custom_field_values dict."""
    if not custom:
        return {}
    out: dict[str, Any] = {}
    for canon, aliases in _DEMO_ALIASES.items():
        for alias in aliases:
            if alias in custom:
                out[canon] = custom[alias]
                break
    return out


# ── GoVocal ingestion ────────────────────────────────────────────────────

def _ingest_govocal(gv: GoVocalClient) -> dict[str, pd.DataFrame]:
    """Fetch data from GoVocal and return a dict of DataFrames."""
    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []

    # --- Projects ---
    try:
        projects_raw = gv.get_projects()
        frames["gv_projects"] = pd.json_normalize(projects_raw)
        log.info("GoVocal: %d projects loaded", len(projects_raw))
    except Exception as exc:
        msg = f"GoVocal projects error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Phases (per project) ---
    all_phases: list[dict] = []
    for pid in Config.GV_PROJECT_IDS:
        try:
            all_phases.extend(gv.get_phases(pid))
        except Exception as exc:
            msg = f"GoVocal phases error (project {pid}): {exc}"
            log.error(msg)
            errors.append(msg)
    if all_phases:
        frames["gv_phases"] = pd.json_normalize(all_phases)

    # --- Ideas (all projects, both types) ---
    all_ideas: list[dict] = []
    for pid in Config.GV_PROJECT_IDS:
        try:
            project_ideas = gv.get_ideas(project_id=pid)
            # Stamp project_id to guarantee the column exists
            for idea in project_ideas:
                idea.setdefault("project_id", pid)
            all_ideas.extend(project_ideas)
        except Exception as exc:
            msg = f"GoVocal ideas error (project {pid}): {exc}"
            log.error(msg)
            errors.append(msg)

    if all_ideas:
        # Flatten custom_field_values demographics into top-level columns
        for idea in all_ideas:
            idea.update(_extract_demographics(idea.get("custom_field_values")))
        frames["gv_ideas"] = pd.json_normalize(all_ideas)
        # Convenience: separate ideation vs survey rows
        df = frames["gv_ideas"]
        if "type" in df.columns:
            frames["gv_ideas_ideation"] = df[df["type"] == "idea"].copy()
            frames["gv_ideas_survey"] = df[df["type"] == "survey"].copy()
        log.info("GoVocal: %d ideas/surveys loaded", len(all_ideas))

    # --- Users ---
    try:
        users_raw = gv.get_users()
        for user in users_raw:
            user.update(_extract_demographics(user.get("custom_field_values")))
        frames["gv_users"] = pd.json_normalize(users_raw)
        log.info("GoVocal: %d users loaded", len(users_raw))
    except Exception as exc:
        msg = f"GoVocal users error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Comments ---
    try:
        comments_raw = gv.get_comments()
        if comments_raw:
            frames["gv_comments"] = pd.json_normalize(comments_raw)
            log.info("GoVocal: %d comments loaded", len(comments_raw))
    except Exception as exc:
        msg = f"GoVocal comments error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Reactions ---
    try:
        reactions_raw = gv.get_reactions()
        if reactions_raw:
            frames["gv_reactions"] = pd.json_normalize(reactions_raw)
            log.info("GoVocal: %d reactions loaded", len(reactions_raw))
        else:
            frames["gv_reactions"] = pd.DataFrame()
    except Exception as exc:
        msg = f"GoVocal reactions error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Input topics (idea tags) ---
    try:
        input_topics_raw = gv.get_input_topics()
        if input_topics_raw:
            frames["gv_input_topics"] = pd.json_normalize(input_topics_raw)
            log.info("GoVocal: %d input topics loaded", len(input_topics_raw))
        else:
            frames["gv_input_topics"] = pd.DataFrame()
    except Exception as exc:
        msg = f"GoVocal input topics error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Idea ↔ input topic associations ---
    try:
        ideas_topics_raw = gv.get_ideas_input_topics()
        if ideas_topics_raw:
            frames["gv_ideas_input_topics"] = pd.json_normalize(ideas_topics_raw)
            log.info("GoVocal: %d idea-topic associations loaded", len(ideas_topics_raw))
        else:
            frames["gv_ideas_input_topics"] = pd.DataFrame()
    except Exception as exc:
        msg = f"GoVocal idea-topic associations error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Baskets (voting) ---
    try:
        baskets_raw = gv.get_baskets()
        if baskets_raw:
            frames["gv_baskets"] = pd.json_normalize(baskets_raw)
            log.info("GoVocal: %d baskets loaded", len(baskets_raw))
        else:
            frames["gv_baskets"] = pd.DataFrame()
    except Exception as exc:
        msg = f"GoVocal baskets error: {exc}"
        log.error(msg)
        errors.append(msg)

    # --- Basket ↔ idea associations (votes) ---
    try:
        basket_ideas_raw = gv.get_basket_ideas()
        if basket_ideas_raw:
            frames["gv_basket_ideas"] = pd.json_normalize(basket_ideas_raw)
            log.info("GoVocal: %d basket-idea associations loaded", len(basket_ideas_raw))
        else:
            frames["gv_basket_ideas"] = pd.DataFrame()
    except Exception as exc:
        msg = f"GoVocal basket-idea associations error: {exc}"
        log.error(msg)
        errors.append(msg)

    if errors:
        meta["errors"].extend(errors)

    return frames


# ── Typeform ingestion ───────────────────────────────────────────────────

def _ingest_typeform(tf: TypeformClient) -> dict[str, pd.DataFrame]:
    """Fetch data from Typeform and return a dict of DataFrames."""
    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []

    for form_id in Config.TF_FORM_IDS:
        try:
            flat_responses = tf.get_responses(form_id)
            key = f"tf_{form_id}"
            frames[key] = pd.DataFrame(flat_responses)
            log.info("Typeform: form %s → %d responses", form_id, len(flat_responses))
        except Exception as exc:
            msg = f"Typeform error (form {form_id}): {exc}"
            log.error(msg)
            errors.append(msg)

    if errors:
        meta["errors"].extend(errors)

    return frames


# ── Unified demographics ─────────────────────────────────────────────────

# Column mappings: source-specific column → canonical name
_GV_USER_DEMO_MAP: dict[str, str] = {
    "custom_field_values.age_peq": "age",
    "custom_field_values.race_2sy": "race",
    "custom_field_values.zipcode_ny9": "zipcode",
    "custom_field_values.political_lean_l7k": "political_lean",
}

_GV_IDEA_DEMO_MAP: dict[str, str] = {
    "custom_field_values.u_email_5vp": "email",
    "custom_field_values.u_email_rzm": "email_alt",
    "custom_field_values.u_age_peq": "age",
    "custom_field_values.u_race_2sy": "race",
    "custom_field_values.u_zipcode_ny9": "zipcode",
    "custom_field_values.u_political_lean_l7k": "political_lean",
}

_TF_DEMO_MAP: dict[str, str] = {
    "First of all, how old are you?": "age",
    "What is your race?": "race",
    "What's your *zipcode*?": "zipcode",
    "How would you describe your political views?": "political_lean",
}

_CANONICAL_COLS = ["email", "age", "zipcode", "political_lean", "race", "source"]


def _rename_and_select(df: pd.DataFrame, col_map: dict[str, str],
                       source_label: str) -> pd.DataFrame:
    """Rename source-specific columns to canonical names and select only those."""
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    tmp = df.rename(columns=rename).copy()
    tmp["source"] = source_label
    cols = [c for c in _CANONICAL_COLS if c in tmp.columns]
    return tmp[cols]


def _build_unified_demographics() -> pd.DataFrame:
    """Merge demographics from all loaded sources, keyed by email.

    Maps source-specific column names to canonical names:
      - gv_users:  custom_field_values.age_peq → age, etc.
      - gv_ideas:  custom_field_values.u_email_5vp → email, u_age_peq → age, etc.
      - typeform:  "First of all, how old are you?" → age, etc.
    """
    parts: list[pd.DataFrame] = []

    # ── GoVocal users ────────────────────────────────────────────────────
    if "gv_users" in store and "email" in store["gv_users"].columns:
        df = store["gv_users"].copy()
        part = _rename_and_select(df, _GV_USER_DEMO_MAP, "govocal_user")
        parts.append(part)

    # ── GoVocal ideas (custom fields carry demographics + email) ─────────
    if "gv_ideas" in store:
        df = store["gv_ideas"].copy()
        # Rename custom field columns to canonical names
        rename = {k: v for k, v in _GV_IDEA_DEMO_MAP.items() if k in df.columns}
        df = df.rename(columns=rename)

        # If author_id present, join the registered user's email
        if "author_id" in df.columns and "gv_users" in store:
            users = store["gv_users"][["id", "email"]].rename(
                columns={"id": "author_id", "email": "user_email"},
            )
            df = df.merge(users, on="author_id", how="left")
        else:
            df["user_email"] = pd.NA

        # Build a unified email: prefer user_email (registered), then
        # custom field email, then email_alt
        df["email"] = (
            df.get("user_email", pd.Series(dtype="object"))
            .combine_first(df.get("email", pd.Series(dtype="object")))
            .combine_first(df.get("email_alt", pd.Series(dtype="object")))
        )

        df["source"] = "govocal_idea"
        cols = [c for c in _CANONICAL_COLS if c in df.columns]
        if "email" in cols:
            parts.append(df[cols])

    # ── Typeform responses ───────────────────────────────────────────────
    for key, frame in store.items():
        if key.startswith("tf_") and "email" in frame.columns:
            part = _rename_and_select(frame, _TF_DEMO_MAP, f"typeform_{key[3:]}")
            parts.append(part)

    if not parts:
        return pd.DataFrame(columns=_CANONICAL_COLS)

    unified = pd.concat(parts, ignore_index=True)

    # Normalise emails for deduplication
    if "email" in unified.columns:
        unified["email"] = unified["email"].astype(str).str.strip().str.lower()
        unified = unified[unified["email"].ne("") & unified["email"].ne("nan")]

    log.info("Unified demographics: %d rows, %d unique emails",
             len(unified),
             unified["email"].nunique() if "email" in unified.columns else 0)
    return unified


# ── Public API ───────────────────────────────────────────────────────────


def _upsert_df(
    existing: pd.DataFrame,
    new: pd.DataFrame,
    id_col: str = "id",
) -> pd.DataFrame:
    """Merge *new* rows into *existing* by *id_col*.

    - Rows in *new* whose id already exists in *existing* replace the old row.
    - Rows in *new* with a brand-new id are appended.
    """
    if new.empty:
        return existing
    if existing.empty:
        return new
    # Drop rows from existing that are being updated
    mask = existing[id_col].isin(new[id_col])
    kept = existing[~mask]
    return pd.concat([kept, new], ignore_index=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def refresh_all() -> dict[str, Any]:
    """Fetch everything from both APIs and rebuild the in-memory store.

    Returns a summary dict with row counts per DataFrame.
    """
    meta["errors"] = []
    meta["status"] = "loading"

    gv = GoVocalClient()
    tf = TypeformClient()

    # Clear old data
    store.clear()
    _last_fetch.clear()

    # Ingest
    store.update(_ingest_govocal(gv))
    store.update(_ingest_typeform(tf))

    # Build unified demographics table
    store["unified_demographics"] = _build_unified_demographics()

    now = _now_iso()
    meta["last_refresh"] = now
    meta["status"] = "loaded" if not meta["errors"] else "loaded_with_errors"

    # Record per-resource timestamps so incremental knows when to start
    for key in store:
        _last_fetch[key] = now

    # Dump all DataFrames to JSON for inspection
    dump_store_to_json()

    summary = get_summary()
    log.info("Data store refreshed (full): %s", summary)
    return summary


# ── Incremental refresh ─────────────────────────────────────────────────


def _ingest_govocal_incremental(gv: GoVocalClient) -> None:
    """Incrementally update GoVocal data already in the store.

    For each resource we compare the API total count with the cached row
    count:
      - Equal  → data is unchanged (survey data is append-only) → **skip**.
      - Greater → new records added → full-fetch that resource and upsert.
      - Less   → records were deleted → full-fetch that resource.
    """
    errors: list[str] = []
    now = _now_iso()

    # --- Projects & Phases: always full (tiny datasets) ---
    try:
        projects_raw = gv.get_projects()
        store["gv_projects"] = pd.json_normalize(projects_raw)
    except Exception as exc:
        errors.append(f"GoVocal projects error: {exc}")

    all_phases: list[dict] = []
    for pid in Config.GV_PROJECT_IDS:
        try:
            all_phases.extend(gv.get_phases(pid))
        except Exception as exc:
            errors.append(f"GoVocal phases error (project {pid}): {exc}")
    if all_phases:
        store["gv_phases"] = pd.json_normalize(all_phases)

    # --- Ideas ---
    try:
        api_idea_count = sum(
            gv.get_idea_count(project_id=pid) for pid in Config.GV_PROJECT_IDS
        )
        cached_count = len(store.get("gv_ideas", pd.DataFrame()))
        since = _last_fetch.get("gv_ideas")

        if not since or api_idea_count < cached_count:
            # No prior data or deletions detected → full fetch
            if since:
                log.info("GoVocal ideas: deletion detected (API %d vs cached %d) – full fetch",
                         api_idea_count, cached_count)
            all_ideas: list[dict] = []
            for pid in Config.GV_PROJECT_IDS:
                project_ideas = gv.get_ideas(project_id=pid)
                for idea in project_ideas:
                    idea.setdefault("project_id", pid)
                all_ideas.extend(project_ideas)
            if all_ideas:
                for idea in all_ideas:
                    idea.update(_extract_demographics(idea.get("custom_field_values")))
                store["gv_ideas"] = pd.json_normalize(all_ideas)
        elif api_idea_count == cached_count:
            log.info("GoVocal ideas: count unchanged (%d) – skipping", cached_count)
        else:
            # api_idea_count > cached_count: new records added → full-fetch
            # and upsert (API does not support reliable updated_after filtering)
            log.info("GoVocal ideas: %d new (API %d vs cached %d) – fetching",
                     api_idea_count - cached_count, api_idea_count, cached_count)
            all_ideas = []
            for pid in Config.GV_PROJECT_IDS:
                project_ideas = gv.get_ideas(project_id=pid)
                for idea in project_ideas:
                    idea.setdefault("project_id", pid)
                all_ideas.extend(project_ideas)
            if all_ideas:
                for idea in all_ideas:
                    idea.update(_extract_demographics(idea.get("custom_field_values")))
                new_df = pd.json_normalize(all_ideas)
                store["gv_ideas"] = _upsert_df(store["gv_ideas"], new_df, "id")

        # Rebuild convenience subsets
        if "gv_ideas" in store and "type" in store["gv_ideas"].columns:
            df = store["gv_ideas"]
            store["gv_ideas_ideation"] = df[df["type"] == "idea"].copy()
            store["gv_ideas_survey"] = df[df["type"] == "survey"].copy()
        _last_fetch["gv_ideas"] = now
    except Exception as exc:
        errors.append(f"GoVocal ideas incremental error: {exc}")
        log.error("GoVocal ideas incremental error: %s", exc)

    # --- Users ---
    try:
        api_user_count = gv.get_user_count()
        cached_count = len(store.get("gv_users", pd.DataFrame()))
        since = _last_fetch.get("gv_users")

        if not since or api_user_count < cached_count:
            if since:
                log.info("GoVocal users: deletion detected (API %d vs cached %d) – full fetch",
                         api_user_count, cached_count)
            users_raw = gv.get_users()
            for user in users_raw:
                user.update(_extract_demographics(user.get("custom_field_values")))
            store["gv_users"] = pd.json_normalize(users_raw)
        elif api_user_count == cached_count:
            log.info("GoVocal users: count unchanged (%d) – skipping", cached_count)
        else:
            log.info("GoVocal users: %d new (API %d vs cached %d) – fetching",
                     api_user_count - cached_count, api_user_count, cached_count)
            users_raw = gv.get_users()
            for user in users_raw:
                user.update(_extract_demographics(user.get("custom_field_values")))
            store["gv_users"] = _upsert_df(store["gv_users"], pd.json_normalize(users_raw), "id")
        _last_fetch["gv_users"] = now
    except Exception as exc:
        errors.append(f"GoVocal users incremental error: {exc}")
        log.error("GoVocal users incremental error: %s", exc)

    # --- Comments ---
    try:
        api_count = gv.get_comment_count()
        cached_count = len(store.get("gv_comments", pd.DataFrame()))
        since = _last_fetch.get("gv_comments")

        if not since or api_count < cached_count:
            if since:
                log.info("GoVocal comments: deletion detected (API %d vs cached %d) – full fetch",
                         api_count, cached_count)
            comments_raw = gv.get_comments()
            if comments_raw:
                store["gv_comments"] = pd.json_normalize(comments_raw)
        elif api_count == cached_count:
            log.info("GoVocal comments: count unchanged (%d) – skipping", cached_count)
        else:
            log.info("GoVocal comments: %d new (API %d vs cached %d) – fetching",
                     api_count - cached_count, api_count, cached_count)
            comments_raw = gv.get_comments()
            if comments_raw:
                store["gv_comments"] = _upsert_df(
                    store.get("gv_comments", pd.DataFrame()), pd.json_normalize(comments_raw), "id"
                )
        _last_fetch["gv_comments"] = now
    except Exception as exc:
        errors.append(f"GoVocal comments incremental error: {exc}")
        log.error("GoVocal comments incremental error: %s", exc)

    # --- Reactions ---
    try:
        api_count = gv.get_reaction_count()
        cached_count = len(store.get("gv_reactions", pd.DataFrame()))
        since = _last_fetch.get("gv_reactions")

        if not since or api_count < cached_count:
            if since:
                log.info("GoVocal reactions: deletion detected (API %d vs cached %d) – full fetch",
                         api_count, cached_count)
            reactions_raw = gv.get_reactions()
            store["gv_reactions"] = pd.json_normalize(reactions_raw) if reactions_raw else pd.DataFrame()
        elif api_count == cached_count:
            log.info("GoVocal reactions: count unchanged (%d) – skipping", cached_count)
        else:
            log.info("GoVocal reactions: %d new (API %d vs cached %d) – fetching",
                     api_count - cached_count, api_count, cached_count)
            reactions_raw = gv.get_reactions()
            if reactions_raw:
                store["gv_reactions"] = _upsert_df(
                    store.get("gv_reactions", pd.DataFrame()), pd.json_normalize(reactions_raw), "id"
                )
        _last_fetch["gv_reactions"] = now
    except Exception as exc:
        errors.append(f"GoVocal reactions incremental error: {exc}")
        log.error("GoVocal reactions incremental error: %s", exc)

    # --- Input topics & associations: always full (static, tiny datasets) ---
    try:
        input_topics_raw = gv.get_input_topics()
        store["gv_input_topics"] = pd.json_normalize(input_topics_raw) if input_topics_raw else pd.DataFrame()
        log.info("GoVocal input topics: %d loaded", len(input_topics_raw or []))
    except Exception as exc:
        errors.append(f"GoVocal input topics error: {exc}")
        log.error("GoVocal input topics error: %s", exc)

    try:
        ideas_topics_raw = gv.get_ideas_input_topics()
        store["gv_ideas_input_topics"] = pd.json_normalize(ideas_topics_raw) if ideas_topics_raw else pd.DataFrame()
        log.info("GoVocal idea-topic associations: %d loaded", len(ideas_topics_raw or []))
    except Exception as exc:
        errors.append(f"GoVocal idea-topic associations error: {exc}")
        log.error("GoVocal idea-topic associations error: %s", exc)

    # --- Baskets (voting) ---
    try:
        api_count = gv.get_basket_count()
        cached_count = len(store.get("gv_baskets", pd.DataFrame()))
        if not _last_fetch.get("gv_baskets") or api_count != cached_count:
            log.info("GoVocal baskets: API %d vs cached %d – fetching", api_count, cached_count)
            baskets_raw = gv.get_baskets()
            store["gv_baskets"] = pd.json_normalize(baskets_raw) if baskets_raw else pd.DataFrame()
        else:
            log.info("GoVocal baskets: count unchanged (%d) – skipping", cached_count)
        _last_fetch["gv_baskets"] = now
    except Exception as exc:
        errors.append(f"GoVocal baskets incremental error: {exc}")
        log.error("GoVocal baskets incremental error: %s", exc)

    # --- Basket ↔ idea associations ---
    try:
        api_count = gv.get_basket_idea_count()
        cached_count = len(store.get("gv_basket_ideas", pd.DataFrame()))
        if not _last_fetch.get("gv_basket_ideas") or api_count != cached_count:
            log.info("GoVocal basket-ideas: API %d vs cached %d – fetching", api_count, cached_count)
            basket_ideas_raw = gv.get_basket_ideas()
            store["gv_basket_ideas"] = pd.json_normalize(basket_ideas_raw) if basket_ideas_raw else pd.DataFrame()
        else:
            log.info("GoVocal basket-ideas: count unchanged (%d) – skipping", cached_count)
        _last_fetch["gv_basket_ideas"] = now
    except Exception as exc:
        errors.append(f"GoVocal basket-ideas incremental error: {exc}")
        log.error("GoVocal basket-ideas incremental error: %s", exc)

    if errors:
        meta["errors"].extend(errors)


def _ingest_typeform_incremental(tf: TypeformClient) -> None:
    """Incrementally fetch new Typeform responses and append to the store."""
    errors: list[str] = []
    now = _now_iso()

    for form_id in Config.TF_FORM_IDS:
        key = f"tf_{form_id}"
        try:
            since = _last_fetch.get(key)
            if not since or key not in store:
                # No prior data — full fetch
                flat_responses = tf.get_responses(form_id)
                store[key] = pd.DataFrame(flat_responses)
                log.info("Typeform: form %s → %d responses (full)", form_id, len(flat_responses))
            else:
                # Typeform requires ISO 8601 without microseconds, with Z suffix
                tf_since = (
                    datetime.fromisoformat(since)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                )
                flat_responses = tf.get_responses(form_id, since=tf_since)
                log.info("Typeform: form %s → %d new responses since %s",
                         form_id, len(flat_responses), tf_since)
                if flat_responses:
                    new_df = pd.DataFrame(flat_responses)
                    store[key] = _upsert_df(store[key], new_df, "response_id")
            _last_fetch[key] = now
        except Exception as exc:
            msg = f"Typeform incremental error (form {form_id}): {exc}"
            log.error(msg)
            errors.append(msg)

    if errors:
        meta["errors"].extend(errors)


def refresh_incremental() -> dict[str, Any]:
    """Fetch only new/updated records from both APIs.

    Falls back to :func:`refresh_all` if the store is empty (first load).
    Returns a summary dict with row counts per DataFrame.
    """
    if not store or meta["status"] == "not_loaded":
        log.info("Store is empty – falling back to full refresh")
        return refresh_all()

    meta["errors"] = []
    meta["status"] = "loading"

    gv = GoVocalClient()
    tf = TypeformClient()

    _ingest_govocal_incremental(gv)
    _ingest_typeform_incremental(tf)

    # Always rebuild unified demographics (cross-source dependency)
    store["unified_demographics"] = _build_unified_demographics()

    meta["last_refresh"] = _now_iso()
    meta["status"] = "loaded" if not meta["errors"] else "loaded_with_errors"

    dump_store_to_json()

    summary = get_summary()
    log.info("Data store refreshed (incremental): %s", summary)
    return summary


# ── JSON dump for debugging ──────────────────────────────────────────────

def dump_store_to_json(output_dir: str = "data_dump") -> None:
    """Write every DataFrame in the store to a JSON file for inspection."""
    out = Path(output_dir)
    out.mkdir(exist_ok=True)

    for name, df in store.items():
        path = out / f"{name}.json"
        # Use default handler for types pandas can't serialise natively
        records = df.to_dict(orient="records")
        with open(path, "w") as f:
            json.dump(records, f, indent=2, default=str)
        log.info("Dumped %s → %s  (%d rows)", name, path, len(df))

    # Also dump metadata + per-resource timestamps so we can restore them
    meta_dump = {
        **meta,
        "_last_fetch": dict(_last_fetch),
    }
    with open(out / "_meta.json", "w") as f:
        json.dump(meta_dump, f, indent=2, default=str)

    log.info("All DataFrames dumped to %s/", output_dir)


# ── Load cached data from disk ───────────────────────────────────────────

def load_from_cache(input_dir: str = "data_dump") -> bool:
    """Restore the in-memory store from previously-dumped JSON files.

    Returns True if cache was loaded successfully (at least one DataFrame),
    False otherwise.
    """
    cache = Path(input_dir)
    meta_path = cache / "_meta.json"

    if not meta_path.exists():
        log.info("No cache found at %s – will do full fetch", input_dir)
        return False

    try:
        with open(meta_path) as f:
            saved_meta = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read cache metadata: %s", exc)
        return False

    loaded_count = 0
    for json_file in cache.glob("*.json"):
        if json_file.name == "_meta.json":
            continue
        key = json_file.stem  # e.g. "gv_ideas"
        try:
            with open(json_file) as f:
                records = json.load(f)
            store[key] = pd.DataFrame(records)
            loaded_count += 1
            log.info("Cache: loaded %s (%d rows)", key, len(records))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cache: skipping %s – %s", json_file.name, exc)

    if loaded_count == 0:
        return False

    # Restore metadata
    meta["last_refresh"] = saved_meta.get("last_refresh")
    meta["errors"] = saved_meta.get("errors", [])
    meta["status"] = saved_meta.get("status", "loaded")

    # Restore per-resource timestamps so incremental fetching works.
    # Fall back to last_refresh for all keys if _last_fetch wasn't persisted.
    saved_timestamps = saved_meta.get("_last_fetch", {})
    if saved_timestamps:
        _last_fetch.update(saved_timestamps)
    elif meta["last_refresh"]:
        for key in store:
            _last_fetch[key] = meta["last_refresh"]

    log.info("Cache restored: %d DataFrames, last_refresh=%s",
             loaded_count, meta['last_refresh'])
    return True


def get_summary() -> dict[str, Any]:
    """Return row counts, unique emails per source, and overlap stats."""
    counts = {name: len(df) for name, df in store.items()}

    # Email overlap analysis
    email_sets: dict[str, set[str]] = {}
    for name, df in store.items():
        if "email" in df.columns:
            emails = set(
                df["email"]
                .dropna()
                .astype(str)
                .str.strip()
                .str.lower()
            ) - {"", "nan"}
            if emails:
                email_sets[name] = emails

    unique_per_source = {k: len(v) for k, v in email_sets.items()}

    # Pairwise overlap
    all_emails = set().union(*email_sets.values()) if email_sets else set()
    overlap: dict[str, int] = {}
    sources = list(email_sets.keys())
    for i, s1 in enumerate(sources):
        for s2 in sources[i + 1 :]:
            common = email_sets[s1] & email_sets[s2]
            if common:
                overlap[f"{s1} ∩ {s2}"] = len(common)

    return {
        "row_counts": counts,
        "unique_emails_per_source": unique_per_source,
        "total_unique_emails": len(all_emails),
        "email_overlap": overlap,
        "last_refresh": meta["last_refresh"],
        "status": meta["status"],
        "errors": meta["errors"],
    }
