"""Transparent recording tap for the ChatGPT backend (leakage verification).

Sits at ``CHATGPT_API_BASE`` between LiteLLM's ``chatgpt`` provider and the real
``https://chatgpt.com/backend-api/codex``. It records the request body that is
actually put on the wire (so we can assert no original secret leaks, §30.5) and
forwards everything else verbatim, streaming the SSE response back.

Auth is forwarded but never recorded: ``Authorization``/``Cookie`` values are
redacted in the record (§25). Egress is limited to ``CHATGPT_REAL_BASE``.

Run:  SM_TAP_RECORD=/tmp/tap.jsonl uvicorn tests.integration.chatgpt_tap:app --port 8099
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

REAL_BASE = os.environ.get("CHATGPT_REAL_BASE", "https://chatgpt.com/backend-api/codex").rstrip("/")
RECORD = os.environ.get("SM_TAP_RECORD")
_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
_REDACT = {"authorization", "cookie", "proxy-authorization"}
_RESP_STRIP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def _record(path: str, headers: dict[str, str], body: bytes) -> None:
    if not RECORD:
        return
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = {"_raw_len": len(body)}
    safe = {k: ("<redacted>" if k.lower() in _REDACT else v) for k, v in headers.items()}
    with open(RECORD, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"path": path, "headers": safe, "body": parsed}, ensure_ascii=False) + "\n")


async def forward(request: Request):
    body = await request.body()
    _record(request.url.path, dict(request.headers), body)

    url = REAL_BASE + request.url.path
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    upstream = await client.send(
        client.build_request(
            request.method, url, headers=fwd_headers, content=body, params=request.query_params
        ),
        stream=True,
    )

    async def relay() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _RESP_STRIP}
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


app = Starlette(routes=[Route("/{path:path}", forward, methods=["POST", "GET"])])
