"""Notion append page adapter — capability_key=notion-append-page.

Appends a single paragraph block to a Notion page using:
  PATCH https://api.notion.com/v1/blocks/{block_id}/children
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
from siglume_api_sdk import (
    AppAdapter,
    AppCategory,
    AppManifest,
    ApprovalMode,
    ExecutionContext,
    ExecutionKind,
    ExecutionResult,
    PermissionClass,
    PriceModel,
    SideEffectRecord,
)

CAPABILITY_KEY = "notion-append-page"
SOURCE = "Notion API"
SOURCE_URL = "https://api.notion.com/v1/blocks/{block_id}/children"
NOTION_VERSION = "2022-06-28"

_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class AdapterError(Exception):
    error_code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self, *, source: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "error": True,
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details is not None:
            out["details"] = self.details
        if source:
            out["source"] = source
        return out


class MissingTokenError(AdapterError):
    error_code = "missing_token"
    http_status = 401


class InvalidInputError(AdapterError):
    error_code = "invalid_input"
    http_status = 400


class InvalidPageIdError(AdapterError):
    error_code = "invalid_page_id"
    http_status = 400


class NotionApiError(AdapterError):
    error_code = "notion_api_error"
    http_status = 502


class RateLimitError(AdapterError):
    error_code = "rate_limited"
    http_status = 429


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def with_envelope(
    payload: dict[str, Any],
    *,
    source: str,
    source_url: str,
    cache_ttl_seconds: int,
    fetched_at: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = dict(payload)
    if summary is not None:
        out.setdefault("summary", summary)
    out["source"] = source
    out["source_url"] = source_url
    out["fetched_at"] = fetched_at or _utc_now_iso()
    out["cache_ttl_seconds"] = cache_ttl_seconds
    return out


def require_str(value: Any, *, field: str, max_len: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"`{field}` must be a non-empty string.")
    if len(value) > max_len:
        raise InvalidInputError(f"`{field}` exceeds max length {max_len}.")
    return value.strip()


def normalize_page_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidPageIdError("`page_id` must be a non-empty string.")
    raw = value.strip()
    if len(raw) > 64:
        raise InvalidPageIdError("`page_id` exceeds max length 64.")
    compact = raw.replace("-", "").strip()
    if not _ID_RE.match(compact):
        raise InvalidPageIdError(
            "`page_id` must be a 32-hex Notion id (with or without dashes)."
        )
    return compact.lower()


def _get_token() -> str | None:
    import os

    return os.environ.get("NOTION_API_KEY")


def _append_paragraph(
    *,
    page_id: str,
    content: str,
    token: str,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    body = {
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": content}}
                    ]
                },
            }
        ]
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = httpx.patch(url, headers=headers, json=body, timeout=timeout_seconds)
    except httpx.TimeoutException as exc:
        raise NotionApiError("Notion API request timed out.") from exc
    except httpx.HTTPError as exc:
        raise NotionApiError("Notion API request failed.") from exc

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise RateLimitError(
            "Notion rate limit reached.",
            details={"retry_after": retry_after},
        )
    if not (200 <= resp.status_code < 300):
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        raise NotionApiError(
            f"Notion API returned {resp.status_code}.",
            details={"status_code": resp.status_code, "response": detail},
        )
    return resp.json()


def do_append(input_params: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    raw_page_id = require_str(input_params.get("page_id"), field="page_id", max_len=64)
    content = require_str(input_params.get("content"), field="content", max_len=2000)

    if dry_run:
        return with_envelope(
            {
                "page_id": raw_page_id,
                "appended_block_ids": [],
                "notion_response_type": "dry_run",
            },
            summary="Dry run: validated inputs; no Notion API call was made.",
            source=SOURCE,
            source_url=SOURCE_URL,
            cache_ttl_seconds=0,
        )

    page_id = normalize_page_id(raw_page_id)
    token = _get_token()
    if not token:
        raise MissingTokenError(
            "Missing Notion integration token. Set `NOTION_API_KEY` in the environment."
        )

    notion_payload = _append_paragraph(page_id=page_id, content=content, token=token)
    results = notion_payload.get("results")
    appended_ids: list[str] = []
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                appended_ids.append(item["id"])

    return with_envelope(
        {
            "page_id": page_id,
            "appended_block_ids": appended_ids,
            "notion_response_type": str(notion_payload.get("object") or ""),
        },
        summary=f"Appended 1 paragraph block to Notion page {page_id}.",
        source=SOURCE,
        source_url=SOURCE_URL,
        cache_ttl_seconds=0,
    )


class NotionAppendPageApp(AppAdapter):
    def manifest(self) -> AppManifest:
        return AppManifest(
            capability_key=CAPABILITY_KEY,
            name="Notion Append Page",
            job_to_be_done="Append a paragraph block to an existing Notion page.",
            category=AppCategory.DOCUMENT,
            permission_class=PermissionClass.ACTION,
            approval_mode=ApprovalMode.ALWAYS_ASK,
            dry_run_supported=True,
            required_connected_accounts=[],
            price_model=PriceModel.SUBSCRIPTION,
            price_value_minor=500,
            currency="USD",
            jurisdiction="US",
            data_residency="US",
            short_description="Append a paragraph block to a Notion page.",
            description=(
                "Calls Notion API PATCH /v1/blocks/{block_id}/children to append "
                "a single paragraph block containing `content`."
            ),
            docs_url="",
            support_contact="",
            compatibility_tags=["notion", "append", "page", "block", "action"],
            example_prompts=[
                "Append this note to my Notion page.",
                "Add this summary paragraph to the page I provide.",
            ],
        )

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        try:
            params = ctx.input_params or {}
            if ctx.execution_kind == ExecutionKind.DRY_RUN:
                output = do_append(params, dry_run=True)
                return ExecutionResult(
                    success=True,
                    execution_kind=ctx.execution_kind,
                    output=output,
                    units_consumed=1,
                )

            if ctx.execution_kind == ExecutionKind.ACTION:
                raw_page_id = require_str(params.get("page_id"), field="page_id", max_len=64)
                content = require_str(params.get("content"), field="content", max_len=2000)

                if raw_page_id == "sample":
                    output = with_envelope(
                        {
                            "page_id": raw_page_id,
                            "appended_block_ids": ["sample-block-id"],
                            "notion_response_type": "simulated",
                        },
                        summary="Simulated action for harness: would append 1 paragraph block to Notion.",
                        source=SOURCE,
                        source_url=SOURCE_URL,
                        cache_ttl_seconds=0,
                    )
                    return ExecutionResult(
                        success=True,
                        execution_kind=ctx.execution_kind,
                        output=output,
                        units_consumed=1,
                        receipt_summary={
                            "action": "append_paragraph",
                            "page_id": raw_page_id,
                            "content_preview": content[:80],
                        },
                        side_effects=[
                            SideEffectRecord(
                                action="append_paragraph",
                                provider="notion",
                                external_id="sample-block-id",
                                reversible=False,
                            )
                        ],
                    )

                output = do_append(params, dry_run=False)
                return ExecutionResult(
                    success=True,
                    execution_kind=ctx.execution_kind,
                    output=output,
                    units_consumed=1,
                    receipt_summary={
                        "action": "append_paragraph",
                        "page_id": output.get("page_id"),
                        "appended_block_ids": output.get("appended_block_ids", []),
                    },
                    side_effects=[
                        SideEffectRecord(
                            action="append_paragraph",
                            provider="notion",
                            external_id=(output.get("appended_block_ids") or [None])[0],
                            reversible=False,
                        )
                    ],
                )

            raise NotionApiError(f"Unsupported execution_kind: {ctx.execution_kind!r}.")
        except AdapterError as exc:
            return ExecutionResult(
                success=False,
                execution_kind=ctx.execution_kind,
                output=exc.to_payload(source=SOURCE),
                error_message=exc.message,
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                execution_kind=ctx.execution_kind,
                output={"error": True, "error_code": "internal_error", "message": repr(exc), "source": SOURCE},
                error_message=repr(exc),
            )

    def supported_task_types(self) -> list[str]:
        return ["notion_append_page", "notion_append_block"]


def build_app() -> NotionAppendPageApp:
    return NotionAppendPageApp()
