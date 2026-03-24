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
- [Consensus Score Algorithms](#consensus-score-algorithms)
  - [Algorithm 1: JSD-Based (Default)](#algorithm-1-jsd-based-default)
  - [Algorithm 2: WMGA (Weighted Mean Group Approval)](#algorithm-2-wmga-weighted-mean-group-approval)
  - [Shared: Polarization Penalty](#shared-polarization-penalty)
  - [Shared: Confidence](#shared-confidence)
  - [Reported Metrics](#reported-metrics)
  - [Design Decisions](#design-decisions)
- [Deployment](#deployment)

---

## Overview

The dashboard pulls data from two external platforms:

| Source | What it provides |
|---|---|
| **GoVocal** (v2 REST API) | Projects, phases, ideas/surveys, users, comments, reactions |
| **Typeform** (Responses API) | Survey responses with hidden fields and answer flattening |

On the first request the app loads cached data from disk (if available), then performs an incremental refresh to pick up new records. Data is persisted to `data_dump/` as JSON files, enabling fast restarts without full API re-fetches.

### Key Capabilities

- **Participant counting** — deduplicated by email across both platforms, with separate anonymous counts.
- **Action totals** — survey submissions, idea submissions, and reactions.
- **Survey → Deliberation conversion** — tracks which survey respondents also participated in the deliberation/ideation project.
- **Idea-selection breakdown** — aggregates Typeform multi-choice answers matching a configurable question pattern.
- **Per-source participation breakdown** — actions and participants broken down by GoVocal surveys, GoVocal ideation, GoVocal reactions, and each Typeform form.
- **Unified demographics** — merges age, zipcode, political lean, and race from GoVocal custom fields (users and idea submissions) and Typeform survey responses into a single table keyed by email with canonical column names (`email`, `age`, `race`, `zipcode`, `political_lean`, `source`).
- **Participation timelines** — daily action counts and new unique participants over time, broken down by tier (confirmed, email-only, anonymous).
- **Interactive frontend** — Vue 3 single-page app with Participation and Ideas tabs, Chart.js visualizations.

---

## Architecture

```
┌────────────┐  JWT auth   ┌──────────────────┐       ┌─────────────┐
│  GoVocal   │◄────────────│                  │       │  Vue 3 SPA  │
│  REST API  │────────────►│   Flask App      │◄─────►│  + Chart.js │
└────────────┘  paginated  │   (app.py)       │       └─────────────┘
                           │                  │──► JSON API
┌────────────┐  Bearer     │  backend/        │
│  Typeform  │◄────────────│  ├ config.py     │
│  API       │────────────►│  ├ data_store.py │
└────────────┘  cursored   │  ├ analytics.py  │
                           │  ├ idea_analytics│
                           │  └ api_client/   │
                           └──────────────────┘
                                    │
                            In-memory pandas   ◄──► data_dump/
                            DataFrames (store)      (JSON cache)
```

**Data flow:**

1. `Config` loads credentials/IDs from environment variables (`.env`).
2. On the first HTTP request, `data_store.load_from_cache()` attempts to restore DataFrames from `data_dump/*.json`.
3. If cache exists, `refresh_incremental()` fetches only new/changed records (count-based detection). Otherwise, `refresh_all()` performs a full fetch.
4. `GoVocalClient` authenticates via JWT and paginates through all endpoints.
5. `TypeformClient` fetches form definitions, then cursor-paginates responses and flattens each answer by field title.
6. Raw JSON is normalised into pandas DataFrames and stored in the module-level `store` dict.
7. A unified demographics DataFrame is built by mapping source-specific column names to canonical names (`age`, `race`, `zipcode`, `political_lean`) and merging across all sources by email.
8. All DataFrames are dumped to `data_dump/` for fast restarts.
9. Analytics functions in `analytics.py` and `idea_analytics.py` read from `store` on every request and return plain dicts.

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
│   ├── data_store.py          # Data ingestion, caching, in-memory store, refresh logic
│   ├── analytics.py           # Participation analytics computations
│   ├── idea_analytics.py      # Per-idea view with reactions, demographics & bridging
│   ├── zipcode_county.json    # Zipcode → region/urban-rural lookup table
│   └── api_client/
│       ├── __init__.py
│       ├── gv_api.py          # GoVocal REST API client (JWT + pagination)
│       └── typeform_api.py    # Typeform Responses API client (cursor pagination)
├── data_dump/                 # Cached JSON files (auto-generated, gitignored)
│   ├── _meta.json             # Refresh timestamps for incremental fetching
│   ├── gv_*.json              # GoVocal data
│   ├── tf_*.json              # Typeform data
│   └── unified_demographics.json
└── frontend/                  # Vue 3 single-page app
    ├── index.html
    ├── css/style.css
    └── js/
        ├── app.js
        └── components/
            ├── ParticipationTab.js
            ├── IdeasTab.js
            └── IdeaDetail.js
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

The server starts on `http://localhost:8080` by default.

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
| `SECRET_KEY` | Yes | Flask session secret — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth 2.0 client ID (see [Google SSO Setup](#google-sso-setup)) |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth 2.0 client secret |
| `ALLOWED_EMAILS` | * | Comma-separated list of email addresses allowed to log in |
| `ALLOWED_DOMAINS` | * | Comma-separated list of email domains allowed to log in (e.g. `example.com`) |
| `FLASK_DEBUG` | No | Set to `1` to enable debug mode |

\* At least one of `ALLOWED_EMAILS` or `ALLOWED_DOMAINS` must be set.

### Google SSO Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or select an existing one).
2. Navigate to **APIs & Services → Credentials**.
3. Click **Create Credentials → OAuth client ID**.
4. Choose **Web application** as the application type.
5. Under **Authorised redirect URIs**, add:
   - `http://localhost:8080/auth/callback` (local development)
   - `https://<your-app>.herokuapp.com/auth/callback` (production)
6. Copy the **Client ID** and **Client secret** into your `.env` file as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
7. Set `ALLOWED_EMAILS` and/or `ALLOWED_DOMAINS` to restrict which Google accounts can access the dashboard.

### Analytics-Specific Config (hardcoded in `config.py`)

| Setting | Description |
|---|---|
| `GV_SURVEY_PROJECT_IDS` | GoVocal project IDs classified as "survey" projects |
| `GV_DELIBERATION_PROJECT_ID` | GoVocal project ID for the deliberation/ideation space |
| `TF_IDEA_QUESTION_PATTERN` | Regex to match the Typeform question about idea selection |

---

## API Reference

Base URL: `http://localhost:8080`

### General

#### `GET /`

Serves the frontend single-page application (Vue 3 + Chart.js dashboard).

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

Refresh data from both APIs. By default performs an **incremental** refresh (only new records based on count comparison). Pass `?full=true` to force a complete re-fetch.

| Query Param | Description |
|---|---|
| `full` | Set to `true` to bypass incremental logic and re-fetch everything |

Returns the same payload as `/api/data/summary`.

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

#### `GET /api/analytics/participation-timeline`

Daily participation counts by action type (surveys, ideas, reactions).

```json
{
  "timeline": [
    { "date": "2026-01-15", "surveys": 12, "ideas": 3, "reactions": 45, "total": 60 },
    { "date": "2026-01-16", "surveys": 8, "ideas": 5, "reactions": 32, "total": 45 }
  ]
}
```

#### `GET /api/analytics/participation-timeline/by-source`

Daily new unique participants, broken down by tier (confirmed account, email-only, anonymous).

```json
{
  "timeline": [
    { "date": "2026-01-15", "confirmed": 5, "email_only": 8, "anonymous": 2, "total": 15 },
    { "date": "2026-01-16", "confirmed": 3, "email_only": 4, "anonymous": 1, "total": 8 }
  ]
}
```

---

### Ideas

#### `GET /api/ideas`

Unified view of every ideation idea, sorted by total reactions (descending). Each entry includes metadata, author demographics, reaction totals, per-demographic breakdowns of upvotes/downvotes, and a consensus score.

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
      "consensus_score": 62.3,
      "confidence_level": "high",
      "demographic_coverage": 0.85,
      "demographic_confidence": 1.0,
      "approval_ratio": 0.9048,
      "per_dimension_scores": {
        "political_lean": 0.78,
        "age_bucket": 0.82,
        "race": 0.88,
        "region": 0.85,
        "urban_rural": 0.91
      },
      "polarization_scores": {
        "political_lean": 0.35,
        "age_bucket": 0.05,
        "race": 0.08,
        "region": 0.02,
        "urban_rural": 0.01
      },
      "polarization_penalty": 0.19,
      "wmga_score": 58.7,
      "wmga_per_dimension": {
        "political_lean": 0.72,
        "age_bucket": 0.80,
        "race": 0.85,
        "region": 0.82,
        "urban_rural": 0.88
      },
      "wmga_polarization_scores": {
        "political_lean": 0.35,
        "age_bucket": 0.05,
        "race": 0.08,
        "region": 0.02,
        "urban_rural": 0.01
      },
      "wmga_polarization_penalty": 0.19
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

Returns ideas sorted by **consensus score** (descending), filtered to only include ideas that have a computable score (≥20 reactions with known demographics).

Useful for surfacing ideas with broad cross-demographic support.

---

## Consensus Score Algorithms

The **consensus score** measures how broadly an idea is supported across demographic groups, inspired by bridging algorithms used in platforms like Pol.is. Unlike Pol.is (which infers groups from voting patterns), this implementation uses direct demographic data.

Two scoring algorithms are available (both are computed for every idea and returned in the API response; the frontend lets users toggle between them):

| Algorithm | Key | Best For |
|---|---|---|
| **JSD-Based** (default) | `consensus_score` | Measuring whether supporters look like the overall population |
| **WMGA** (Weighted Mean Group Approval) | `wmga_score` | Measuring whether every demographic group actually approves |

Both algorithms share the same **polarization penalty** and **confidence** scaling (described below).

---

### Algorithm 1: JSD-Based (Default)

```
consensus_score = Approval × Diversity × Confidence × 100
```

An idea must be **approved**, **demographically diverse**, and **backed by sufficient data** to score well.

#### 1. Approval (0–1)

The straight ratio of upvotes to total votes:

```
approval = upvotes / total_votes
```

| Approval % | Factor |
|------------|--------|
| 95%        | 0.95   |
| 80%        | 0.80   |
| 60%        | 0.60   |
| 50%        | 0.50   |

#### 2. Diversity (0–1)

Measures how well the idea's supporters represent the overall voter population, with a penalty for polarization:

```
diversity = upvote_diversity × polarization_factor
```

**Upvote diversity** is the weighted average of per-dimension diversity scores across five demographic dimensions:

| Dimension      | Base Weight |
|----------------|-------------|
| Political lean | 50%         |
| Urban/Rural    | 20%         |
| Age            | 10%         |
| Race           | 10%         |
| Region         | 10%         |

**Per-dimension score** = `1 - JS_distance(idea_upvotes, population_baseline)`

where **JS distance** is the Jensen-Shannon distance (square root of the JS divergence), computed via `scipy.spatial.distance.jensenshannon`. A score of 1.0 means the idea's upvoters perfectly mirror the overall voter population for that dimension. Using the distance (not squared divergence) provides better sensitivity to shifts among minority subgroups.

**Epsilon smoothing**: Before computing JSD, a small epsilon (`1e-10`) is added to both probability vectors and they are re-normalised. This prevents `log(0)` errors when a category appears in one distribution but not the other.

**Coverage adjustment**: Base weights are scaled by each dimension's data coverage (fraction of reactions with a value for that dimension), then renormalized. This prevents dimensions with sparse data from distorting the score.

#### 3. Polarization & Confidence

See [Polarization Penalty](#shared-polarization-penalty) and [Confidence](#shared-confidence) below — both are shared with WMGA.

---

### Algorithm 2: WMGA (Weighted Mean Group Approval)

```
wmga_score = WeightedDimAvg × Polarization_Factor × Confidence × 100
```

Where JSD asks *"Do the supporters look like the population?"*, WMGA asks *"Does every demographic group actually approve of this idea?"*.

#### Per-Group Smoothed Approval

For each group within a dimension, compute a **Bayesian-smoothed approval rate**:

```
smoothed_approval = (group_upvotes + prior_strength × platform_approval)
                  / (group_votes   + prior_strength)
```

| Parameter | Value | Description |
|---|---|---|
| `prior_strength` | 15 | How aggressively small samples shrink toward the global mean |
| `platform_approval` | *(computed)* | Platform-wide upvote ratio across all reactions |

Groups with zero votes on the idea shrink entirely to `platform_approval` — they neither help nor hurt the score. As a group accumulates votes, its smoothed rate converges to its actual approval rate.

#### Per-Dimension Score

The per-dimension WMGA score is the **population-weighted mean** of smoothed group approvals:

```
dim_score = Σ (population_weight[group] × smoothed_approval[group])
```

where `population_weight[group]` is the group's share in the overall voter population baseline (the same baseline used by the JSD algorithm).

#### Final WMGA Score

Dimension scores are combined using the same coverage-adjusted effective weights, then multiplied by the polarization factor and confidence:

```
weighted_dim_avg = Σ (effective_weight[dim] × dim_score[dim])
wmga_score = weighted_dim_avg × polarization_factor × confidence × 100
```

The frontend also receives **per-dimension penalised scores** (`wmga_per_dimension`), where each dimension's WMGA score is independently multiplied by its own polarization penalty:

```
wmga_per_dimension[dim] = dim_score[dim] × (1 - polarization_weight × polarization[dim])
```

---

### Shared: Polarization Penalty

Both algorithms apply the same polarization penalty.

For each demographic dimension, the algorithm computes the **variance of per-group approval rates** — a direct measure of whether different groups feel differently about the idea. Groups with fewer than **20** votes are excluded to avoid noisy estimates. The variance is normalised to [0, 1] (divided by the theoretical maximum variance of 0.25) and combined using coverage-adjusted weights:

```
per_dim_polarization[dim] = variance(group_approval_rates) / 0.25   # capped at 1.0

weighted_polarization = Σ (effective_weight[dim] × per_dim_polarization[dim])
polarization_factor   = 1 - 1.0 × weighted_polarization
```

The penalty weight is **1.0**, meaning maximum polarization completely zeroes out the diversity/WMGA component:

| Weighted Polarization | Penalty Factor | Score Impact |
|-----------------------|----------------|-------------|
| 0.0                   | 1.00           | No penalty  |
| 0.2                   | 0.80           | −20%        |
| 0.5                   | 0.50           | −50%        |
| 1.0                   | 0.00           | −100%       |

---

### Shared: Confidence

Both algorithms use the same confidence scaling:

```
confidence = min(1, known_demo_reactions / 50)
```

If fewer than **20 reactions** have known demographics, the consensus score is `null` (insufficient data). Full confidence is reached at **50** reactions with known demographics.

---

### Reported Metrics

**JSD algorithm:**

- **`consensus_score`**: Final JSD-based score (0–100), or `null` if insufficient data.
- **`approval_ratio`**: Upvotes / total votes (0–1).
- **`demographic_coverage`**: Fraction of reactions with known demographics.
- **`demographic_confidence`**: The confidence multiplier (0–1).
- **`per_dimension_scores`**: Per-dimension diversity scores (`1 − JS_distance` from population baseline).
- **`polarization_scores`**: Per-dimension polarization values (normalised approval-rate variance). High values indicate the idea is divisive along that demographic axis.
- **`polarization_penalty`**: The weighted polarization value used to compute the penalty factor.
- **`confidence_level`**: `"low"` / `"medium"` / `"high"` based on data availability.

**WMGA algorithm:**

- **`wmga_score`**: Final WMGA-based score (0–100), or `null` if insufficient data.
- **`wmga_per_dimension`**: Per-dimension WMGA scores after applying each dimension's individual polarization penalty.
- **`wmga_polarization_scores`**: Per-dimension polarization values (same variance calculation as JSD).
- **`wmga_polarization_penalty`**: The weighted polarization value used for the WMGA penalty factor.

---

### Design Decisions

1. **Simple multiplicative formula**: `Approval × Diversity × Confidence` (JSD) and `WeightedDimAvg × Polarization × Confidence` (WMGA) are easy to explain to non-technical audiences. Each factor is intuitive: "Do people like it?", "Do all kinds of people like it?", "Do we have enough data to be sure?"

2. **Two complementary algorithms**: JSD measures *representativeness* of supporters (do upvoters look like the population?). WMGA measures *universality* of approval (does every group approve?). An idea can score differently on each — e.g., if a small minority is absent from upvoters but wouldn't downvote, JSD penalises more than WMGA.

3. **Population-relative diversity**: Uses JS distance from the actual voter population rather than theoretical equal distribution. This accounts for structural imbalances (e.g., if 85% of voters are White, a "perfectly bridging" idea doesn't need equal race representation).

4. **Bayesian smoothing (WMGA)**: The prior (`prior_strength = 15`, centred on platform-wide approval) prevents groups with very few votes from dominating the score. A group with 1 upvote and 0 downvotes won't read as 100% approval — it will be pulled toward the global average.

5. **Polarization detection**: Per-group approval-rate variance directly measures whether different demographic subgroups feel differently about an idea. This catches polarization that JSD-based diversity scoring misses — e.g., when a small but cohesive minority group unanimously opposes an idea, the upvote distribution may still look representative, but the approval-rate variance will flag the split.

6. **Sparse data handling**: Only reactions with known demographics are used. Demographics come primarily from Typeform respondents who also have GoVocal accounts — many GoVocal-only users lack demographic data.

7. **All factors are multiplicative**: An idea must pass all gates (approval/WMGA, diversity/polarization, confidence) to achieve a high score. A zero on any factor kills the score.

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
