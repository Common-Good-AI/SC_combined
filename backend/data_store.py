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

def _build_unified_demographics() -> pd.DataFrame:
    """Merge demographics from all loaded sources, keyed by email."""
    demo_cols = ["email", "age", "zipcode", "political_lean", "race", "source"]
    parts: list[pd.DataFrame] = []

    # GoVocal users
    if "gv_users" in store and "email" in store["gv_users"].columns:
        df = store["gv_users"].copy()
        df["source"] = "govocal_user"
        cols = [c for c in demo_cols if c in df.columns]
        parts.append(df[cols])

    # GoVocal ideas (author demographics baked into custom_field_values)
    if "gv_ideas" in store:
        df = store["gv_ideas"].copy()
        if "author_id" in df.columns and "gv_users" in store:
            # Join email from users
            users = store["gv_users"][["id", "email"]].rename(columns={"id": "author_id"})
            df = df.merge(users, on="author_id", how="left")
        df["source"] = "govocal_idea"
        cols = [c for c in demo_cols if c in df.columns]
        if "email" in cols:
            parts.append(df[cols])

    # Typeform responses
    for key, df in store.items():
        if key.startswith("tf_") and "email" in df.columns:
            tmp = df.copy()
            tmp["source"] = f"typeform_{key[3:]}"
            cols = [c for c in demo_cols if c in tmp.columns]
            parts.append(tmp[cols])

    if not parts:
        return pd.DataFrame(columns=demo_cols)

    unified = pd.concat(parts, ignore_index=True)
    # Normalise emails for matching
    if "email" in unified.columns:
        unified["email"] = unified["email"].astype(str).str.strip().str.lower()
        unified = unified[unified["email"].ne("") & unified["email"].ne("nan")]

    log.info("Unified demographics: %d rows", len(unified))
    return unified


# ── Public API ───────────────────────────────────────────────────────────

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

    # Ingest
    store.update(_ingest_govocal(gv))
    store.update(_ingest_typeform(tf))

    # Build unified demographics table
    store["unified_demographics"] = _build_unified_demographics()

    meta["last_refresh"] = datetime.now(timezone.utc).isoformat()
    meta["status"] = "loaded" if not meta["errors"] else "loaded_with_errors"

    # Dump all DataFrames to JSON for inspection
    dump_store_to_json()

    summary = get_summary()
    log.info("Data store refreshed: %s", summary)
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

    # Also dump metadata
    with open(out / "_meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)

    log.info("All DataFrames dumped to %s/", output_dir)


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
