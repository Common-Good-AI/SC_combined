"""GoVocal public REST API client.

Handles JWT authentication, automatic token refresh, and full pagination
for all list endpoints.  All data stays in-memory (no DB).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from backend.config import Config

log = logging.getLogger(__name__)

# GoVocal JWT tokens expire after 24 h.  We refresh a bit early.
_JWT_TTL_SECONDS = 23 * 60 * 60  # 23 hours


class GoVocalClient:
    """Thin wrapper around the GoVocal v2 REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.base_url = (base_url or Config.GV_BASE_URL).rstrip("/")
        self._client_id = client_id or Config.GV_CLIENT_ID
        self._client_secret = client_secret or Config.GV_CLIENT_SECRET
        self._jwt: str | None = None
        self._jwt_expires_at: float = 0.0

    # ── Authentication ───────────────────────────────────────────────────

    def authenticate(self) -> None:
        """Obtain a fresh JWT from the GoVocal /authenticate endpoint."""
        url = f"{self.base_url}/api/v2/authenticate"
        payload = {
            "auth": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        self._jwt = resp.json()["jwt"]
        self._jwt_expires_at = time.time() + _JWT_TTL_SECONDS
        log.info("GoVocal: authenticated successfully")

    def _ensure_auth(self) -> None:
        if self._jwt is None or time.time() >= self._jwt_expires_at:
            self.authenticate()

    # ── Generic request helper (with pagination) ─────────────────────────

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        paginate: bool = True,
    ) -> list[dict[str, Any]]:
        """GET *endpoint* with automatic auth and full pagination.

        Returns the aggregated list of items across all pages.
        The GoVocal API nests the list under a key that matches the resource
        name (e.g. ``"users"``, ``"ideas"``).  We detect that key automatically.
        """
        self._ensure_auth()
        url = f"{self.base_url}{endpoint}"
        params = dict(params or {})
        params.setdefault("page_size", 24)  # API max
        params.setdefault("page_number", 1)

        headers = {"Authorization": f"Bearer {self._jwt}"}
        all_items: list[dict[str, Any]] = []
        total_pages = 1  # will be updated from first response

        while params["page_number"] <= total_pages:
            log.debug("GoVocal GET %s  page %s/%s", endpoint, params["page_number"], total_pages)
            resp = requests.get(url, headers=headers, params=params, timeout=60)

            # If 401, re-auth once and retry this page
            if resp.status_code == 401:
                log.warning("GoVocal: 401 on %s – re-authenticating", endpoint)
                self.authenticate()
                headers = {"Authorization": f"Bearer {self._jwt}"}
                resp = requests.get(url, headers=headers, params=params, timeout=60)

            resp.raise_for_status()
            body = resp.json()

            # Detect the data key (first key that is not "meta")
            data_key = next((k for k in body if k != "meta"), None)
            if data_key is None:
                break

            items = body[data_key]
            all_items.extend(items)

            meta = body.get("meta", {})
            total_pages = meta.get("total_pages", 1)

            if not paginate:
                break
            params["page_number"] += 1

        log.info("GoVocal: %s → %d items", endpoint, len(all_items))
        return all_items

    # ── Resource count (for deletion detection) ─────────────────────────

    def get_resource_count(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> int:
        """Return the total number of items for *endpoint* without fetching all pages."""
        self._ensure_auth()
        url = f"{self.base_url}{endpoint}"
        p = dict(params or {})
        p["page_size"] = 1
        p["page_number"] = 1
        headers = {"Authorization": f"Bearer {self._jwt}"}
        resp = requests.get(url, headers=headers, params=p, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        return body.get("meta", {}).get("total", 0)

    def get_idea_count(self, project_id: str | None = None) -> int:
        params: dict[str, Any] = {}
        if project_id:
            params["project_id"] = project_id
        return self.get_resource_count("/api/v2/ideas/", params)

    def get_user_count(self) -> int:
        return self.get_resource_count("/api/v2/users/")

    def get_comment_count(self) -> int:
        return self.get_resource_count("/api/v2/comments/")

    def get_reaction_count(self) -> int:
        return self.get_resource_count("/api/v2/reactions")

    # ── High-level data fetchers ─────────────────────────────────────────

    def get_projects(self, project_ids: list[str] | None = None) -> list[dict]:
        """Fetch projects.  If *project_ids* given, fetch each individually."""
        ids = project_ids or Config.GV_PROJECT_IDS
        if ids:
            projects = []
            for pid in ids:
                self._ensure_auth()
                url = f"{self.base_url}/api/v2/projects/{pid}"
                headers = {"Authorization": f"Bearer {self._jwt}"}
                resp = requests.get(url, headers=headers, params={"locale": "en"}, timeout=30)
                resp.raise_for_status()
                projects.append(resp.json()["project"])
            log.info("GoVocal: fetched %d projects by ID", len(projects))
            return projects
        return self._request("/api/v2/projects/")

    def get_phases(self, project_id: str) -> list[dict]:
        return self._request(f"/api/v2/projects/{project_id}/phases")

    def get_ideas(
        self,
        project_id: str | None = None,
        idea_type: str | None = None,
        updated_after: str | None = None,
    ) -> list[dict]:
        """Fetch ideas, optionally filtered by project, type, or update time."""
        params: dict[str, Any] = {}
        if project_id:
            params["project_id"] = project_id
        if idea_type:
            params["type"] = idea_type
        if updated_after:
            params["updated_after"] = updated_after
        return self._request("/api/v2/ideas/", params=params)

    def get_users(self, updated_after: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if updated_after:
            params["updated_after"] = updated_after
        return self._request("/api/v2/users/", params=params)

    def get_comments(
        self,
        idea_id: str | None = None,
        updated_after: str | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if idea_id:
            params["idea_id"] = idea_id
        if updated_after:
            params["updated_after"] = updated_after
        return self._request("/api/v2/comments/", params=params)

    def get_reactions(self, updated_after: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if updated_after:
            params["updated_after"] = updated_after
        return self._request("/api/v2/reactions", params=params)
