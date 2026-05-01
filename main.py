from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from siglume_api_sdk import ExecutionContext, ExecutionKind

from adapter import build_app


app = FastAPI(title="notion-append-page", version="0.1.0")
_ADAPTER = build_app()


@app.get("/health")
def health() -> dict[str, Any]:
    manifest = _ADAPTER.manifest()
    return {"ok": True, "service": "notion-append-page", "capability_key": manifest.capability_key}


@app.post("/invoke")
async def invoke(
    request: Request,
    x_siglume_review_key: str | None = Header(default=None, alias="X-Siglume-Review-Key"),
) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    ctx = ExecutionContext(
        agent_id="vercel",
        owner_user_id="siglume",
        task_type="notion_append_page",
        input_params=body,
        execution_kind=ExecutionKind.ACTION,
    )
    result = await _ADAPTER.execute(ctx)
    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=getattr(result, "error_message", None) or "Execution failed",
        )
    return result.output

