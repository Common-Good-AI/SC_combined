"""Typeform Responses API client.

Uses a personal access token for authentication.  Handles pagination via
the ``after`` cursor for forms with > 1 000 responses.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from backend.config import Config

log = logging.getLogger(__name__)

_PAGE_SIZE = 1000  # Typeform max per request


class TypeformClient:
    """Thin wrapper around the Typeform REST API (Responses + Create)."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._token = token or Config.TF_TOKEN
        self._base_url = (base_url or Config.TF_BASE_URL).rstrip("/")

    # ── Generic request helper ───────────────────────────────────────────

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated GET and return the parsed JSON body."""
        url = f"{self._base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    # ── Form definition ──────────────────────────────────────────────────

    def get_form_definition(self, form_id: str) -> dict[str, Any]:
        """Retrieve the form schema (fields, title, etc.) for *form_id*."""
        data = self._request(f"/forms/{form_id}")
        log.info(
            "Typeform: form '%s' (%s) – %d fields",
            data.get("title", "?"),
            form_id,
            len(data.get("fields", [])),
        )
        return data

    # ── Responses (with cursor pagination) ───────────────────────────────

    def get_responses_raw(
        self,
        form_id: str,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the raw ``items`` list for *form_id*, paginated.

        If *since* is given (ISO 8601), only responses submitted after that
        timestamp are returned.
        """
        all_items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"page_size": _PAGE_SIZE}
        if since:
            params["since"] = since

        while True:
            body = self._request(f"/forms/{form_id}/responses", params=params)
            items = body.get("items", [])
            all_items.extend(items)

            # If we got fewer than page_size, we've reached the end
            if len(items) < _PAGE_SIZE:
                break

            # Use the *last* item's token as the ``after`` cursor
            last_token = items[-1].get("token")
            if not last_token:
                break
            params["after"] = last_token

        log.info("Typeform: form %s → %d responses", form_id, len(all_items))
        return all_items

    # ── Flattened responses ──────────────────────────────────────────────

    def get_responses(
        self,
        form_id: str,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch responses for *form_id* and return a list of flat dicts.

        If *since* is given (ISO 8601), only new responses submitted after
        that timestamp are returned.

        Each dict contains:
        - ``response_id``
        - ``submitted_at``
        - ``landed_at``
        - One key per answered field, keyed by **field title** (from the
          form definition).  If the field is an email type, also stored
          under the canonical key ``email``.
        - Any ``hidden`` fields from the URL.
        """
        # 1) Get form definition so we can map field IDs → titles
        definition = self.get_form_definition(form_id)
        field_map = self._build_field_map(definition)

        # 2) Get raw responses
        raw_items = self.get_responses_raw(form_id, since=since)

        # 3) Flatten each response
        flat: list[dict[str, Any]] = []
        for item in raw_items:
            row: dict[str, Any] = {
                "response_id": item.get("response_id") or item.get("token", ""),
                "submitted_at": item.get("submitted_at"),
                "landed_at": item.get("landed_at"),
                "form_id": form_id,
            }

            # Hidden fields (e.g. pre-filled email via URL)
            for hk, hv in (item.get("hidden") or {}).items():
                row[f"hidden_{hk}"] = hv

            # Answers
            for answer in item.get("answers", []):
                field_id = answer.get("field", {}).get("id", "")
                field_info = field_map.get(field_id, {})
                col_name = field_info.get("title", field_id)
                field_type = answer.get("field", {}).get("type", "")
                value = self._extract_answer_value(answer)
                row[col_name] = value

                # If this is an email-type field, also store as canonical "email"
                if field_type == "email" or answer.get("type") == "email":
                    row["email"] = value

            flat.append(row)

        return flat

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_field_map(definition: dict) -> dict[str, dict[str, str]]:
        """Build a mapping of ``{field_id: {title, type, ref}}`` from the form definition."""
        fmap: dict[str, dict[str, str]] = {}
        for field in definition.get("fields", []):
            fid = field.get("id", "")
            fmap[fid] = {
                "title": field.get("title", fid),
                "type": field.get("type", ""),
                "ref": field.get("ref", ""),
            }
            # Also index sub-fields inside groups / statement blocks
            for prop in field.get("properties", {}).get("fields", []):
                sub_id = prop.get("id", "")
                fmap[sub_id] = {
                    "title": prop.get("title", sub_id),
                    "type": prop.get("type", ""),
                    "ref": prop.get("ref", ""),
                }
        return fmap

    @staticmethod
    def _extract_answer_value(answer: dict) -> Any:
        """Pull the scalar value out of a Typeform answer object."""
        atype = answer.get("type", "")
        if atype == "text":
            return answer.get("text")
        if atype == "email":
            return answer.get("email")
        if atype == "number":
            return answer.get("number")
        if atype == "boolean":
            return answer.get("boolean")
        if atype == "date":
            return answer.get("date")
        if atype == "choice":
            return answer.get("choice", {}).get("label")
        if atype == "choices":
            return ", ".join(answer.get("choices", {}).get("labels", []))
        if atype == "file_url":
            return answer.get("file_url")
        if atype == "url":
            return answer.get("url")
        if atype == "payment":
            pay = answer.get("payment", {})
            return f"{pay.get('amount', 0)} {pay.get('currency', '')}"
        # Fallback: return the raw answer dict minus the field key
        return {k: v for k, v in answer.items() if k != "field"}
