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

_PAGE_SIZE = 500  # Typeform returns at most 1000 per request; use 500 to avoid silent truncation


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
        resp = requests.get(url, headers=headers, params=params, timeout=20)
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

    def _paginate_responses(
        self,
        form_id: str,
        params: dict[str, Any],
        seen_tokens: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Paginate through all response pages using cursor + time-window fallback.

        The Typeform API limits cursor-based pagination (``after``) to ~1000
        items per window.  When that limit is hit, we use the ``until``
        parameter (set to the oldest item's submitted_at) to open a new
        window of older responses and continue paginating.

        *seen_tokens* can be passed in to deduplicate across multiple calls
        (e.g. completed + partial fetches).
        """
        all_items: list[dict[str, Any]] = []
        params = {**params, "page_size": _PAGE_SIZE}
        if seen_tokens is None:
            seen_tokens = set()
        reported_total: int = 0
        max_windows = 30  # Safety limit on time-window iterations

        for _ in range(max_windows):
            # Cursor-paginate within the current time window
            window_params = {**params}
            window_params.pop("after", None)  # Reset cursor for each new window
            window_items: list[dict[str, Any]] = []

            while True:
                body = self._request(f"/forms/{form_id}/responses", window_params)
                items = body.get("items", [])

                # Capture total_items from the first request (no 'until' set)
                if not reported_total and "until" not in params:
                    reported_total = body.get("total_items", 0)

                # Deduplicate
                new_in_page = []
                for item in items:
                    token = item.get("token") or item.get("response_id", "")
                    if token and token not in seen_tokens:
                        seen_tokens.add(token)
                        new_in_page.append(item)
                window_items.extend(new_in_page)

                # If fewer than page_size, this window is exhausted
                if len(items) < _PAGE_SIZE:
                    break

                # Use cursor for next page within this window
                last_token = items[-1].get("token")
                if not last_token:
                    break
                window_params["after"] = last_token

            all_items.extend(window_items)

            log.debug(
                "Typeform pagination: form %s window collected %d items "
                "(total so far: %d, reported_total: %d)",
                form_id, len(window_items), len(all_items), reported_total,
            )

            # If we got nothing new in this window, we're done
            if not window_items:
                break

            # NOTE: Typeform may cap total_items at its cursor limit (~1000).
            # Only trust reported_total if it's clearly above that limit.
            if reported_total > 1000 and len(all_items) >= reported_total:
                break

            # Open a new time window: use 'until' = oldest item's submitted_at
            oldest_item = window_items[-1]
            oldest_time = oldest_item.get("submitted_at") or oldest_item.get("landed_at")
            if not oldest_time:
                break

            params = {**params, "until": oldest_time}
            params.pop("after", None)

        log.info(
            "Typeform: form %s pagination complete — %d responses collected "
            "(API reported %d)",
            form_id, len(all_items), reported_total,
        )
        return all_items

    def get_responses_raw(
        self,
        form_id: str,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the raw ``items`` list for *form_id*, paginated.

        Fetches both completed and partial (incomplete) submissions.
        If *since* is given (ISO 8601), only responses submitted after that
        timestamp are returned.
        """
        base_params: dict[str, Any] = {}
        if since:
            base_params["since"] = since

        # Shared seen_tokens across both calls to deduplicate responses
        # that may appear in both completed and partial results
        seen_tokens: set[str] = set()

        # Fetch completed responses
        completed_items = self._paginate_responses(
            form_id, {**base_params, "completed": "true"}, seen_tokens=seen_tokens,
        )
        log.info("Typeform: form %s → %d completed responses", form_id, len(completed_items))

        # Fetch partial (incomplete) responses
        partial_items = self._paginate_responses(
            form_id, {**base_params, "completed": "false"}, seen_tokens=seen_tokens,
        )
        log.info("Typeform: form %s → %d partial responses", form_id, len(partial_items))

        all_items = completed_items + partial_items
        log.info("Typeform: form %s → %d total responses", form_id, len(all_items))
        return all_items

    # ── Form metrics (visits / unique visitors / submissions) ────────────

    def get_form_metrics(self, form_id: str) -> dict[str, Any]:
        """Fetch form-level metrics from the ``/forms/{form_id}/metrics`` endpoint.

        Returns a dict with ``title``, ``visits`` (total form sessions),
        ``unique_visitors``, ``submissions`` (completed responses), and
        ``average_time`` (average completion time in seconds), aggregated
        across all device types.
        """
        # Form title
        title = form_id
        try:
            defn = self.get_form_definition(form_id)
            title = defn.get("title", form_id)
        except Exception:
            pass

        body = self._request(f"/forms/{form_id}/metrics")

        total_visits = 0
        total_unique = 0
        total_responses = 0
        weighted_avg_sum = 0
        for device_data in body.values():
            if not isinstance(device_data, dict):
                continue
            visits = int(device_data.get("visits", 0))
            total_visits += visits
            total_unique += int(device_data.get("unique", 0))
            total_responses += int(device_data.get("responses", 0))
            weighted_avg_sum += int(device_data.get("average", 0)) * visits

        avg_time = round(weighted_avg_sum / total_visits) if total_visits else 0

        return {
            "title": title,
            "visits": total_visits,
            "unique_visitors": total_unique,
            "submissions": total_responses,
            "average_time": avg_time,
        }

    def get_all_form_views(self) -> list[dict[str, Any]]:
        """Return per-form visit counts for every configured Typeform form.

        Returns a list of dicts with keys: ``form_id``, ``title``,
        ``visits``, ``unique_visitors``, ``submissions``, ``average_time``.
        """
        results: list[dict[str, Any]] = []
        for form_id in (Config.TF_FORM_IDS or []):
            try:
                data = self.get_form_metrics(form_id)
                results.append({"form_id": form_id, **data})
            except Exception as exc:
                log.warning("Typeform metrics error (form %s): %s", form_id, exc)
                results.append({
                    "form_id": form_id,
                    "title": form_id,
                    "visits": 0,
                    "unique_visitors": 0,
                    "submissions": 0,
                    "average_time": 0,
                    "error": str(exc),
                })
        return results

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

        # 3) Flatten each response (deduplicate by response_id as safety net)
        flat: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            rid = item.get("response_id") or item.get("token", "")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            row: dict[str, Any] = {
                "response_id": rid,
                "submitted_at": item.get("submitted_at"),
                "landed_at": item.get("landed_at"),
                "response_type": "completed" if item.get("submitted_at") else "partial",
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
