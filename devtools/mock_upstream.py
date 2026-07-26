"""漏洩防止とSSE構造を検証するための合成upstream LLM server。

OpenAI ResponsesとAnthropic Messagesの必要最小限のwire protocolを模倣する。
受信したrequest bodyは``$SM_MOCK_RECORD``が示すJSON Lines fileへ記録し、
Gatewayから外へ出た最終payloadに元の機密値がないことをテストから確認できるようにする。

固定alias ``SM_ORG_7F3A91``と受信したマスク済み文字列を応答へ含め、Gatewayを通した
復元、SSEの分割処理、漏洩防止をend-to-endで検証する。

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
    """利用者のマスク済み文字列を抽出し、合成応答へそのまま含める。

    Gatewayが送信payloadへ置いたaliasを応答経由で返すことで、clientへ届く前に
    元の値へ復元されることをend-to-endで確認できる。
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
    # 固定aliasと受信したマスク済み文字列を含め、両方の復元を確認できるようにする。
    echo = _echo_user_text(body)
    return f"Connected to {ALIAS_IN_RESPONSE}. :: {echo}" if echo else f"Connected to {ALIAS_IN_RESPONSE}."


def _chunk(text: str, size: int = 4) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


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
        # The FULL documented item lifecycle, not just created/delta/completed.
        # A real client tracks the active item, so a bare delta is discarded
        # ("OutputTextDelta without active item") and the reply never renders —
        # which meant an end-to-end test could not observe restoration at all.
        item = {"id": "msg-mock-0", "type": "message", "role": "assistant",
                "status": "in_progress", "content": []}
        part = {"type": "output_text", "text": "", "annotations": []}
        common = {"item_id": "msg-mock-0", "output_index": 0, "content_index": 0}

        def ev(name: str, payload: dict) -> str:
            return f"event: {name}\ndata: {json.dumps({'type': name, **payload})}\n\n"

        lines = [
            ev("response.created", {"response": obj}),
            ev("response.in_progress", {"response": obj}),
            ev("response.output_item.added", {"output_index": 0, "item": item}),
            ev("response.content_part.added", {**common, "part": part}),
            # aliasが複数deltaに分割されるよう、小さなchunkで返す。
            *(ev("response.output_text.delta", {**common, "delta": tok})
              for tok in _chunk(text)),
            ev("response.output_text.done", {**common, "text": text}),
            ev("response.content_part.done",
               {**common, "part": {**part, "text": text}}),
            ev("response.output_item.done",
               {"output_index": 0,
                "item": {**item, "status": "completed",
                         "content": [{**part, "text": text}]}}),
            ev("response.completed", {"response": obj}),
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
            for tok in _chunk(text)  # aliasを複数deltaに分割する小さなchunk
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


async def count_tokens(request: Request):
    body = await _read(request)
    # 正確なtokenizer emulationではなく、Gatewayがmask済みpayloadをこのendpointへ
    # 転送したことをprotocol E2Eで確認するための決定論的な合成値。
    text = _echo_user_text(body)
    return JSONResponse({"input_tokens": max(1, len(text) // 4)})


async def models(request: Request):
    await _read(request)
    return JSONResponse(
        {
            "data": [
                {
                    "id": "claude-synthetic",
                    "type": "model",
                    "display_name": "Synthetic Claude",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "has_more": False,
            "first_id": "claude-synthetic",
            "last_id": "claude-synthetic",
        }
    )


async def health(request: Request):
    return JSONResponse({"ok": True, "ts": time.time()})


# Accept both /v1/* and /* path variants (the proxy may or may not add the prefix).
routes = [
    Route("/health", health, methods=["GET"]),
    Route("/responses", responses, methods=["POST"]),
    Route("/v1/responses", responses, methods=["POST"]),
    Route("/messages", messages, methods=["POST"]),
    Route("/v1/messages", messages, methods=["POST"]),
    Route("/v1/messages/count_tokens", count_tokens, methods=["POST"]),
    Route("/v1/models", models, methods=["GET"]),
]

app = Starlette(routes=routes)
