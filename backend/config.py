"""Centralized configuration loaded from environment variables / .env file."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- GoVocal ---
    GV_BASE_URL: str = os.getenv("GV_BASE_URL", "").rstrip("/")
    GV_CLIENT_ID: str = os.getenv("GV_CLIENT_ID", "")
    GV_CLIENT_SECRET: str = os.getenv("GV_CLIENT_SECRET", "")
    GV_PROJECT_IDS: list[str] = [
        pid.strip()
        for pid in os.getenv("GV_PROJECT_IDS", "").split(",")
        if pid.strip()
    ]

    # --- Typeform ---
    TF_TOKEN: str = os.getenv("TF_TOKEN", "")
    TF_BASE_URL: str = os.getenv("TF_BASE_URL", "https://api.typeform.com")
    TF_FORM_IDS: list[str] = [
        fid.strip()
        for fid in os.getenv("TF_FORM_IDS", "").split(",")
        if fid.strip()
    ]

    # --- App ---
    DEBUG: bool = os.getenv("FLASK_DEBUG", "0") == "1"
    REFRESH_INTERVAL_HOURS: float = float(os.getenv("REFRESH_INTERVAL_HOURS", "1"))

    # --- Auth (Google SSO) ---
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    ALLOWED_EMAILS: list[str] = [
        e.strip().lower()
        for e in os.getenv("ALLOWED_EMAILS", "").split(",")
        if e.strip()
    ]
    ALLOWED_DOMAINS: list[str] = [
        d.strip().lower()
        for d in os.getenv("ALLOWED_DOMAINS", "").split(",")
        if d.strip()
    ]

    # --- Analytics (Phase 2a) ---
    # GoVocal project IDs that count as "survey" projects
    GV_SURVEY_PROJECT_IDS: list[str] = [
        "b3808271-ec77-485f-b028-7b9a25cf37ed",
        "be48e68c-20d9-478d-9ceb-e48bbf3cd6b7",
    ]
    # GoVocal project ID for the deliberation / ideation space
    GV_DELIBERATION_PROJECT_ID: str = "ee66d45a-b2db-42bc-9015-e8dd0bf06714"
    # Regex pattern to match the Typeform question title that asks about idea selection
    TF_IDEA_QUESTION_PATTERN: str = r"(?i)what.*issue.*most important"

    @classmethod
    def validate(cls) -> list[str]:
        """Return a list of missing-but-required config keys."""
        problems: list[str] = []
        if not cls.GV_BASE_URL:
            problems.append("GV_BASE_URL is not set")
        if not cls.GV_CLIENT_ID:
            problems.append("GV_CLIENT_ID is not set")
        if not cls.GV_CLIENT_SECRET:
            problems.append("GV_CLIENT_SECRET is not set")
        if not cls.GV_PROJECT_IDS:
            problems.append("GV_PROJECT_IDS is not set (comma-separated list)")
        if not cls.TF_TOKEN:
            problems.append("TF_TOKEN is not set")
        if not cls.TF_FORM_IDS:
            problems.append("TF_FORM_IDS is not set (comma-separated list)")
        if not cls.SECRET_KEY:
            problems.append("SECRET_KEY is not set")
        if not cls.GOOGLE_CLIENT_ID:
            problems.append("GOOGLE_CLIENT_ID is not set")
        if not cls.GOOGLE_CLIENT_SECRET:
            problems.append("GOOGLE_CLIENT_SECRET is not set")
        if not cls.ALLOWED_EMAILS and not cls.ALLOWED_DOMAINS:
            problems.append("At least one of ALLOWED_EMAILS or ALLOWED_DOMAINS must be set")
        return problems
