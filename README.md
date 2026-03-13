# GoVocal + Typeform Admin Dashboard

A Flask-based backend that aggregates civic-engagement data from **GoVocal** and **Typeform** into a single analytics API. All data is held in-memory as pandas DataFrames — no database required.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Setup](#setup)
- [Configuration](#configuration)
- [API Reference](#api-reference)
  - [General](#general)
  - [Data](#data)
  - [Analytics](#analytics)
  - [Ideas](#ideas)
- [Bridging Score Algorithm](#bridging-score-algorithm)
- [Deployment](#deployment)

---

## Overview

The dashboard pulls data from two external platforms:

| Source | What it provides |
|---|---|
| **GoVocal** (v2 REST API) | Projects, phases, ideas/surveys, users, comments, reactions |
| **Typeform** (Responses API) | Survey responses with hidden fields and answer flattening |

On the first request the app fetches all configured projects/forms, normalises everything into DataFrames, and exposes analytics through a JSON API.

### Key Capabilities

- **Participant counting** — deduplicated by email across both platforms, with separate anonymous counts.
- **Action totals** — survey submissions, idea submissions, and reactions.
- **Survey → Deliberation conversion** — tracks which survey respondents also participated in the deliberation/ideation project.
- **Idea-selection breakdown** — aggregates Typeform multi-choice answers matching a configurable question pattern.
- **Per-source participation breakdown** — actions and participants broken down by GoVocal surveys, GoVocal ideation, GoVocal reactions, and each Typeform form.
- **Unified demographics** — merges age, zipcode, political lean, and race from GoVocal custom fields (users and idea submissions) and Typeform survey responses into a single table keyed by email with canonical column names (`email`, `age`, `race`, `zipcode`, `political_lean`, `source`).

---

## Architecture

```
┌────────────┐  JWT auth   ┌──────────────────┐
│  GoVocal   │◄────────────│                  │
│  REST API  │────────────►│                  │
└────────────┘  paginated  │   Flask App      │
                           │   (app.py)       │──► JSON API
┌────────────┐  Bearer     │                  │
│  Typeform  │◄────────────│  backend/        │
│  API       │────────────►│  ├ config.py     │
└────────────┘  cursored   │  ├ data_store.py │
                           │  ├ analytics.py  │
                           │  └ api_client/   │
                           └──────────────────┘
                                    │
                            In-memory pandas
                            DataFrames (store)
```

**Data flow:**

1. `Config` loads credentials/IDs from environment variables (`.env`).
2. On the first HTTP request, `data_store.refresh_all()` is called.
3. `GoVocalClient` authenticates via JWT and paginates through all endpoints.
4. `TypeformClient` fetches form definitions, then cursor-paginates responses and flattens each answer by field title.
5. Raw JSON is normalised into pandas DataFrames and stored in the module-level `store` dict.
6. A unified demographics DataFrame is built by mapping source-specific column names to canonical names (`age`, `race`, `zipcode`, `political_lean`) and merging across all sources by email.
7. Analytics functions in `analytics.py` read from `store` on every request and return plain dicts.

---

## Project Structure

```
├── app.py                     # Flask app, routes, startup hook
├── Procfile                   # Gunicorn entry point for deployment
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables (not committed)
├── backend/
│   ├── __init__.py
│   ├── config.py              # Centralised config from env vars
│   ├── data_store.py          # Data ingestion, in-memory store, refresh logic
│   ├── analytics.py           # Phase 2a analytics computations
│   ├── idea_analytics.py      # Per-idea view with reactions & demographics
│   └── api_client/
│       ├── __init__.py
│       ├── gv_api.py          # GoVocal REST API client (JWT + pagination)
│       └── typeform_api.py    # Typeform Responses API client (cursor pagination)
└── frontend/                  # (Planned) frontend assets
```

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd GoVocal-Admin-panel-

# 2. Create & activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create a .env file (see Configuration below)
cp .env.example .env   # or create manually

# 5. Run the development server
python app.py
```

The server starts on `http://localhost:5000` by default.

---

## Configuration

All settings are read from environment variables (loaded from `.env` via `python-dotenv`).

| Variable | Required | Description |
|---|---|---|
| `GV_BASE_URL` | Yes | GoVocal instance URL (e.g. `https://yourorg.govocal.com`) |
| `GV_CLIENT_ID` | Yes | GoVocal API client ID |
| `GV_CLIENT_SECRET` | Yes | GoVocal API client secret |
| `GV_PROJECT_IDS` | Yes | Comma-separated GoVocal project UUIDs to ingest |
| `TF_TOKEN` | Yes | Typeform personal access token |
| `TF_FORM_IDS` | Yes | Comma-separated Typeform form IDs to ingest |
| `TF_BASE_URL` | No | Typeform API base URL (default: `https://api.typeform.com`) |
| `FLASK_DEBUG` | No | Set to `1` to enable debug mode |

### Analytics-Specific Config (hardcoded in `config.py`)

| Setting | Description |
|---|---|
| `GV_SURVEY_PROJECT_IDS` | GoVocal project IDs classified as "survey" projects |
| `GV_DELIBERATION_PROJECT_ID` | GoVocal project ID for the deliberation/ideation space |
| `TF_IDEA_QUESTION_PATTERN` | Regex to match the Typeform question about idea selection |

---

## API Reference

Base URL: `http://localhost:5000`

### General

#### `GET /`

Returns app info and status.

```json
{
  "app": "GoVocal + Typeform Admin Dashboard",
  "phase": "2a",
  "status": "loaded",
  "hint": "Try /api/health, /api/data/summary, or /api/analytics/summary"
}
```

#### `GET /api/health`

Health check — config validity, data load status, loaded tables.

```json
{
  "config_ok": true,
  "config_problems": [],
  "data_status": "loaded",
  "last_refresh": "2026-03-10T12:00:00+00:00",
  "errors": [],
  "dataframes_loaded": ["gv_projects", "gv_phases", "gv_ideas", "..."]
}
```

---

### Data

#### `GET /api/data/summary`

Row counts per DataFrame, unique emails per source, cross-source email overlap, and refresh metadata.

```json
{
  "row_counts": { "gv_projects": 3, "gv_ideas": 142, "tf_abc123": 87, "..." : "..." },
  "unique_emails_per_source": { "gv_users": 95, "tf_abc123": 80 },
  "total_unique_emails": 120,
  "email_overlap": { "gv_users ∩ tf_abc123": 55 },
  "last_refresh": "2026-03-10T12:00:00+00:00",
  "status": "loaded",
  "errors": []
}
```

#### `POST /api/data/refresh`

Re-fetches all data from both APIs and rebuilds the in-memory store. Returns the same payload as `/api/data/summary`.

#### `GET /api/data/tables`

Lists every loaded DataFrame with row count, column names, and 3 sample rows.

```json
{
  "gv_ideas": {
    "rows": 142,
    "columns": ["id", "project_id", "type", "author_id", "..."],
    "sample": [ { "id": "...", "..." : "..." } ]
  }
}
```

#### `GET /api/data/table/<name>`

Returns the full contents of a single DataFrame.

| Parameter | Location | Description |
|---|---|---|
| `name` | URL path | DataFrame key (e.g. `gv_ideas`, `tf_abc123`, `unified_demographics`) |

Returns `404` if the table name is not found.

---

### Analytics

#### `GET /api/analytics/summary`

All analytics metrics in a single payload (combines every endpoint below).

#### `GET /api/analytics/participants`

Total identified (deduplicated by email) and anonymous participants.

```json
{
  "identified_participants": 120,
  "anonymous_participants": 15,
  "total": 135
}
```

#### `GET /api/analytics/actions`

Total actions broken down by type.

```json
{
  "survey_submits": 200,
  "survey_submits_breakdown": {
    "govocal_surveys": 50,
    "typeform_surveys": 150
  },
  "ideas_submitted": 30,
  "reactions": 85,
  "total": 315
}
```

#### `GET /api/analytics/conversion`

Survey-to-deliberation conversion rate.

```json
{
  "survey_participants": 180,
  "deliberation_participants_from_surveys": 45,
  "conversion_rate_pct": 25.0
}
```

#### `GET /api/analytics/idea-selections`

Breakdown of idea selections from the matching Typeform question.

```json
{
  "question_title": "What issue is most important to you?",
  "total_responses": 150,
  "selections": [
    { "idea": "Public Transit", "count": 60, "percentage": 40.0 },
    { "idea": "Housing", "count": 50, "percentage": 33.33 },
    { "idea": "Green Space", "count": 40, "percentage": 26.67 }
  ]
}
```

#### `GET /api/analytics/participation-breakdown`

Per-source breakdown of actions and participants, plus overall totals.

```json
{
  "per_source": [
    {
      "source": "govocal_surveys",
      "actions": 50,
      "identified_participants": 45,
      "anonymous_participants": 5
    },
    {
      "source": "govocal_ideation",
      "actions": 30,
      "identified_participants": 28,
      "anonymous_participants": 2
    },
    { "source": "govocal_reactions", "..." : "..." },
    { "source": "typeform_abc123", "..." : "..." }
  ],
  "overall": {
    "total_actions": 315,
    "identified_participants": 120,
    "anonymous_participants": 15,
    "total_participants": 135
  }
}
```

---

### Ideas

#### `GET /api/ideas`

Unified view of every ideation idea, sorted by total reactions (descending). Each entry includes metadata, author demographics, reaction totals, per-demographic breakdowns of upvotes/downvotes, and a bridging score.

```json
[
  {
    "idea_id": "abc-123",
    "title": "Expand public transit",
    "body": "Plain-text body of the idea…",
    "project_id": "ee66d45a-…",
    "created_at": "2026-01-15T08:30:00Z",
    "author_demographics": {
      "age_bucket": "35-44",
      "race": "White",
      "region": "Midlands",
      "urban_rural": "Urban",
      "political_lean": "Somewhat Liberal"
    },
    "reactions": {
      "total": 42,
      "upvotes": 38,
      "downvotes": 4,
      "demographic_breakdown": {
        "age_bucket": {
          "upvotes": { "25-34": 12, "35-44": 10, "18-24": 8, "…": "…" },
          "downvotes": { "55-64": 2, "45-54": 2 }
        },
        "race": { "upvotes": { "…": "…" }, "downvotes": { "…": "…" } },
        "political_lean": { "upvotes": { "…": "…" }, "downvotes": { "…": "…" } },
        "region": { "upvotes": { "…": "…" }, "downvotes": { "…": "…" } },
        "urban_rural": { "upvotes": { "…": "…" }, "downvotes": { "…": "…" } }
      }
    },
    "bridging": {
      "bridging_score": 78.5,
      "confidence_level": "high",
      "demographic_coverage": 0.85,
      "engagement_confidence": 0.98,
      "demographic_confidence": 1.0,
      "approval_factor": 0.82,
      "engagement_factor": 0.97,
      "per_dimension_scores": {
        "political_lean": 0.92,
        "age_bucket": 0.88,
        "race": 0.95,
        "region": 0.91,
        "urban_rural": 0.97
      },
      "downvote_diversity": 0.72,
      "cross_coalition_used": true
    }
  }
]
```

#### `GET /api/ideas/<idea_id>`

Returns a single idea with the same shape as above. Returns `404` if the idea is not found.

| Parameter | Location | Description |
|---|---|---|
| `idea_id` | URL path | GoVocal idea UUID |

#### `GET /api/ideas/bridging`

Returns ideas sorted by **bridging score** (descending), filtered to only include ideas that have a computable score (≥8 reactions with known demographics).

Useful for surfacing ideas with broad cross-demographic support.

---

## Bridging Score Algorithm

The **bridging score** measures how broadly an idea is supported across demographic groups, inspired by bridging algorithms used in platforms like Pol.is. Unlike Pol.is (which infers groups from voting patterns), this implementation uses direct demographic data.

### Score Components

The final bridging score (0–100) is computed as:

```
bridging_score = engagement_confidence × demographic_confidence × approval_factor × engagement_factor × diversity_composite × 100
```

#### 1. Engagement Confidence (0–1)

A sigmoid function of total votes that prevents low-vote ideas from scoring high:

```
engagement_confidence = 1 - exp(-total_votes / 10)
```

| Total Votes | Confidence |
|-------------|------------|
| 10          | 63%        |
| 20          | 86%        |
| 30          | 95%        |

#### 2. Demographic Confidence (0–1)

Scales by how many reactions have known demographics:

```
demographic_confidence = min(1, known_demo_reactions / 30)
```

If fewer than **15 reactions** have known demographics, the bridging score is `null` (insufficient data).

#### 3. Approval Factor (0–1)

Prioritises ideas with a high like-to-dislike ratio. The approval ratio (upvotes / total votes) is raised to an exponent to produce a moderate penalty curve:

```
approval_factor = (upvotes / total_votes) ^ 1.5
```

| Approval % | Factor | Penalty |
|------------|--------|--------|
| 95%        | 0.93   | ~7%    |
| 90%        | 0.85   | ~15%   |
| 85%        | 0.78   | ~22%   |
| 75%        | 0.65   | ~35%   |
| 50%        | 0.35   | ~65%   |

This ensures that an idea with broad demographic support but significant opposition is scored lower than a similarly diverse idea with near-unanimous approval.

#### 4. Engagement Factor (0–1)

A logarithmic curve that rewards ideas with higher total participation. While engagement confidence (above) gates out very-low-vote ideas, this factor provides meaningful differentiation between moderately-voted and highly-voted ideas:

```
engagement_factor = min(1.0, log(1 + total_votes) / log(1 + 150))
```

| Total Votes | Factor | Penalty |
|-------------|--------|--------|
| 20          | 0.60   | ~40%   |
| 40          | 0.74   | ~26%   |
| 80          | 0.86   | ~14%   |
| 150         | 1.00   | 0%     |
| 300+        | 1.00   | 0%     |

This means an idea with 40 likes and 0 dislikes (engagement factor ~0.74) will score lower than an idea with 300 likes and 7 dislikes (engagement factor 1.0, approval factor ~0.97), all else being equal.

#### 5. Diversity Composite (0–1)

Measures how well the idea's upvote distribution matches the overall voter population across five demographic dimensions:

| Dimension      | Base Weight |
|----------------|-------------|
| Political lean | 50%         |
| Urban/Rural    | 20%         |
| Age            | 10%         |
| Race           | 10%         |
| Region         | 10%         |

**Per-dimension score** = `1 - JSD(idea_upvotes, population_baseline)`

where JSD is Jensen-Shannon divergence. A score of 1.0 means the idea's upvoters perfectly mirror the overall voter population for that dimension.

**Coverage adjustment**: Base weights are scaled by each dimension's data coverage (fraction of reactions with a value for that dimension), then renormalized. This prevents dimensions with sparse data from distorting the score.

#### 6. Cross-Coalition Signal (when available)

When an idea has **≥5 downvotes with known demographics**, the algorithm also computes a cross-coalition signal:

```
cross_coalition = 1 - JSD(upvote_distribution, downvote_distribution)
```

If upvoters and downvoters look demographically similar (high cross-coalition score), the idea genuinely cuts across group lines rather than splitting along demographic fault lines.

When cross-coalition is available, the diversity composite becomes:

```
diversity_composite = 0.8 × upvote_diversity + 0.2 × cross_coalition
```

### Separate Metrics

The following are reported separately (not folded into the main score):

- **`approval_factor`**: The approval-ratio multiplier applied to the score.
- **`engagement_factor`**: The participation-volume multiplier (log curve, 1.0 at 150+ votes).
- **`downvote_diversity`**: Same calculation as upvote diversity, but on downvotes. Useful for identifying ideas where opposition is non-traditional (spread across demographics rather than concentrated).
- **`demographic_coverage`**: Fraction of reactions with known demographics.
- **`per_dimension_scores`**: Individual dimension scores for transparency.
- **`confidence_level`**: "low" / "medium" / "high" based on data availability.

### Design Decisions

1. **Approval-weighted scoring**: The squared approval ratio keeps the score multiplicative — an idea must be both broadly liked (high approval) and broadly diverse (wide demographic support) to score well. Ideas with ~85% approval see a ~28% penalty; ideas with 95%+ approval are barely affected.

2. **Population-relative diversity**: Uses JSD from the actual voter population rather than theoretical equal distribution. This accounts for structural imbalances (e.g., if 85% of voters are White, a "perfectly bridging" idea doesn't need equal race representation).

3. **Sparse data handling**: Only reactions with known demographics are used. Demographics come primarily from Typeform respondents who also have GoVocal accounts — many GoVocal-only users lack demographic data.

4. **Multiplicative gating**: Engagement confidence, demographic confidence, approval factor, and engagement factor are all multiplied (not added), so an idea must pass all four gates to achieve a high score.

---

## Deployment

The app is production-ready with **Gunicorn** via the included `Procfile`:

```
web: gunicorn app:app
```

Deploy to any platform that supports Python buildpacks (Heroku, Render, Railway, etc.). Ensure all required environment variables are set in the deployment environment.

### Dependencies

- Python 3.10+
- Flask 3.1
- pandas 2.2
- numpy 1.26+
- scipy 1.11+
- requests 2.32
- python-dotenv 1.1
- gunicorn 23.0
