"""Auth-passthrough probe for the Phase 6 spike (§25-safe).

Captures ONLY a redacted summary of what a client (Codex) sends — whether an
Authorization header is present, its scheme, token length, and a short prefix
(enough to tell an OAuth JWT from a dummy key) — never the token value. Returns a
minimal valid Responses SSE so the client completes. Used to verify whether Codex,
under ChatGPT auth, forwards its OAuth token to a custom base_url.

Run: uvicorn tests.integration.auth_probe:app --port 8090
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import AsyncGenerator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


def _probe(path: str, headers: dict[str, str]) -> None:
    auth = headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    summary = {
        "path": path,
        "has_authorization": bool(auth),
        "scheme": scheme or None,
        "token_len": len(token),
        "token_prefix": token[:8] if token else None,   # 'eyJ...' => OAuth JWT
        "looks_like_jwt": token.startswith("eyJ"),
        "chatgpt_account_id": bool(headers.get("chatgpt-account-id")),
        "session_header": headers.get("x-securitymasker-session-id"),
        "other_headers": sorted(k for k in headers if k not in {"authorization"}),
    }
    print("AUTH_PROBE " + json.dumps(summary, ensure_ascii=False), file=sys.stderr, flush=True)


def _sse(events: list[dict]) -> StreamingResponse:
    async def gen() -> AsyncGenerator[bytes, None]:
        for ev in events:
            line = f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
            yield line.encode("utf-8")

    return StreamingResponse(gen(), media_type="text/event-stream")


def _min_response(text: str = "ok") -> dict:
    return {
        "id": "resp-probe", "object": "response", "created_at": int(time.time()),
        "status": "completed", "model": "probe",
        "output": [{"id": "msg-0", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }


async def responses(request: Request):
    body = await request.body()
    _probe(request.url.path, {k.lower(): v for k, v in request.headers.items()})
    resp = _min_response()
    if b'"stream": true' in body or b'"stream":true' in body:
        events = [
            {"type": "response.created", "response": resp},
            {"type": "response.output_text.delta", "item_id": "msg-0", "output_index": 0,
             "content_index": 0, "delta": "ok"},
            {"type": "response.output_text.done", "item_id": "msg-0", "output_index": 0,
             "content_index": 0, "text": "ok"},
            {"type": "response.completed", "response": resp},
        ]
        return _sse(events)
    return JSONResponse(resp)


async def catchall(request: Request):
    _probe(request.url.path, {k.lower(): v for k, v in request.headers.items()})
    return JSONResponse({"ok": True})


routes = [
    Route("/responses", responses, methods=["POST"]),
    Route("/v1/responses", responses, methods=["POST"]),
    Route("/{path:path}", catchall, methods=["POST", "GET"]),
]
app = Starlette(routes=routes)
