"""Mock upstream LLM server for leakage / SSE-structure verification.

A single ASGI app that emulates just enough of the OpenAI (chat/completions +
Responses) and Anthropic (Messages) wire protocols for the proxy to route to it
as a fake provider. Every received request body is appended (as JSON lines) to
the file named by ``$SM_MOCK_RECORD`` so tests can assert what actually left the
gateway (``doc/00-First-Order.md`` §30.5 leakage tests).

Canned responses embed a fixed alias token (``SM_ORG_7F3A91``) so later phases can
verify alias restoration through the proxy. Phase 0 only checks connectivity, SSE
shape, and that no original secret is recorded/leaked.

Run standalone:
    SM_MOCK_RECORD=/tmp/rec.jsonl uvicorn devtools.mock_upstream:app --port 8081
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

ALIAS_IN_RESPONSE = "SM_ORG_7F3A91"


def _record(payload: dict) -> None:
    path = os.environ.get("SM_MOCK_RECORD")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def _read(request: Request) -> dict:
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"_raw": raw.decode("utf-8", "replace")}
    _record(
        {
            "path": request.url.path,
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": body,
        }
    )
    return body


def _sse(lines: list[str]) -> StreamingResponse:
    async def gen() -> AsyncGenerator[bytes, None]:
        for line in lines:
            yield line.encode("utf-8")

    return StreamingResponse(gen(), media_type="text/event-stream")


def _echo_user_text(body: dict) -> str:
    """Extract the user's text so the mock can echo it back (masked) in the reply.

    This lets restoration be tested end-to-end: whatever aliases the gateway put on
    the wire come back in the response and must be restored before the client.
    """
    parts: list[str] = []
    inp = body.get("input")
    if isinstance(inp, str):
        parts.append(inp)
    elif isinstance(inp, list):
        for item in inp:
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    for msg in body.get("messages", []) if isinstance(body.get("messages"), list) else []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    return " ".join(p for p in parts if p)


def _reply_text(body: dict) -> str:
    # Always include the canned alias (keeps Phase 0 fixtures valid) plus an echo of
    # the (possibly masked) user text so Phase 2 restoration can be verified.
    echo = _echo_user_text(body)
    return f"Connected to {ALIAS_IN_RESPONSE}. :: {echo}" if echo else f"Connected to {ALIAS_IN_RESPONSE}."


def _chunk(text: str, size: int = 4) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


# ---- OpenAI chat/completions -------------------------------------------------
async def chat_completions(request: Request):
    body = await _read(request)
    text = _reply_text(body)
    if body.get("stream"):
        # Small chunks deliberately split aliases across SSE boundaries (§20).
        chunks = [
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": tok}, "finish_reason": None}],
            }
            for tok in _chunk(text)
        ]
        lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
        lines.append("data: [DONE]\n\n")
        return _sse(lines)
    return JSONResponse(
        {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model", "gpt-4o"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


# ---- OpenAI Responses API ----------------------------------------------------
def _responses_message(text: str) -> dict:
    return {
        "id": "msg-mock-0",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _responses_object(text: str) -> dict:
    return {
        "id": "resp-mock",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": "gpt-4o",
        "output": [_responses_message(text)],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


async def responses(request: Request):
    body = await _read(request)
    text = _reply_text(body)
    if body.get("stream"):
        obj = _responses_object(text)
        lines = [
            f'event: response.created\ndata: {json.dumps({"type": "response.created", "response": obj})}\n\n',
            f'event: response.output_text.delta\ndata: {json.dumps({"type": "response.output_text.delta", "item_id": "msg-mock-0", "output_index": 0, "content_index": 0, "delta": text})}\n\n',
            f'event: response.completed\ndata: {json.dumps({"type": "response.completed", "response": obj})}\n\n',
        ]
        return _sse(lines)
    return JSONResponse(_responses_object(text))


# ---- Anthropic Messages ------------------------------------------------------
async def messages(request: Request):
    body = await _read(request)
    text = _reply_text(body)
    if body.get("stream"):
        delta_lines = [
            f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": tok}})}\n\n'
            for tok in _chunk(text)  # small chunks split aliases across deltas (§20)
        ]
        lines = [
            f'event: message_start\ndata: {json.dumps({"type": "message_start", "message": {"id": "msg-mock", "role": "assistant", "content": []}})}\n\n',
            f'event: content_block_start\ndata: {json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})}\n\n',
            *delta_lines,
            f'event: content_block_stop\ndata: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n',
            f'event: message_stop\ndata: {json.dumps({"type": "message_stop"})}\n\n',
        ]
        return _sse(lines)
    return JSONResponse(
        {
            "id": "msg-mock",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude"),
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


async def health(request: Request):
    return JSONResponse({"ok": True, "ts": time.time()})


# Accept both /v1/* and /* path variants (the proxy may or may not add the prefix).
routes = [
    Route("/health", health, methods=["GET"]),
    Route("/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/responses", responses, methods=["POST"]),
    Route("/v1/responses", responses, methods=["POST"]),
    Route("/messages", messages, methods=["POST"]),
    Route("/v1/messages", messages, methods=["POST"]),
]

app = Starlette(routes=routes)
