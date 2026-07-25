"""SecurityMasker proxy ASGI app (ADR-0006).

Routes Codex (OpenAI Responses) and Claude Code (Anthropic Messages) through the
masking core, forwarding everything else transparently. One handler invocation
resolves the session, masks the request, forwards it (client auth passed through,
never logged, §25), and restores the response — owning both directions.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from securitymasker.errors import SecurityMaskerError
from securitymasker.gateway.forwarder import forward_buffered, forward_streaming
from securitymasker.gateway.responses_stream import ResponsesStreamProcessor
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.session import resolve_session_id
from securitymasker.logging import get_logger
from securitymasker.protocols import anthropic_messages, openai_responses
from securitymasker.streaming.anthropic_stream import AnthropicStreamProcessor

_log = get_logger(component="securitymasker.gateway")


def _load_body(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _blocked_response(exc: SecurityMaskerError) -> JSONResponse:
    # Fail-closed: never forward, never leak the original value (§25, §26).
    return JSONResponse(
        {"error": {"message": str(exc), "type": "securitymasker_blocked", "code": "400"}},
        status_code=400,
    )


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def handle_responses(request: Request) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    raw = await request.body()
    data = _load_body(raw)
    url = f"{rt.openai_upstream}/responses"

    if rt.engine is None or data is None:
        return await forward_streaming(request.method, url, request.headers, raw)

    session_id = resolve_session_id(request.headers, data)
    session = await rt.store.get_or_create(session_id)
    try:
        async with rt.store.lock(session_id):
            await openai_responses.mask_request(rt.engine, session, data)
            await rt.store.save(session)
    except SecurityMaskerError as exc:
        _log.warning("sm_block", stage="responses_mask")
        return _blocked_response(exc)

    masked = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if data.get("stream"):
        proc = ResponsesStreamProcessor(
            rt.engine.literal_restorations(session), rt.engine.make_restorer(session)
        )
        return await forward_streaming(request.method, url, request.headers, masked, proc)

    status, headers, content = await forward_buffered(request.method, url, request.headers, masked)
    resp = _load_body(content)
    if resp is not None:
        openai_responses.restore_response(rt.engine, session, resp)
        return Response(json.dumps(resp, ensure_ascii=False), status_code=status,
                        media_type="application/json")
    return Response(content, status_code=status, media_type=headers.get("content-type"))


async def handle_messages(request: Request) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    raw = await request.body()
    data = _load_body(raw)
    url = f"{rt.anthropic_upstream}/v1/messages"

    if rt.engine is None or data is None:
        return await forward_streaming(request.method, url, request.headers, raw)

    session_id = resolve_session_id(request.headers, data)
    session = await rt.store.get_or_create(session_id)
    try:
        async with rt.store.lock(session_id):
            await anthropic_messages.mask_request(rt.engine, session, data)
            await rt.store.save(session)
    except SecurityMaskerError as exc:
        _log.warning("sm_block", stage="messages_mask")
        return _blocked_response(exc)

    masked = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if data.get("stream"):
        proc = AnthropicStreamProcessor(
            rt.engine.literal_restorations(session), rt.engine.make_restorer(session)
        )
        return await forward_streaming(request.method, url, request.headers, masked, proc)

    status, headers, content = await forward_buffered(request.method, url, request.headers, masked)
    resp = _load_body(content)
    if resp is not None:
        anthropic_messages.restore_response(rt.engine, session, resp)
        return Response(json.dumps(resp, ensure_ascii=False), status_code=status,
                        media_type="application/json")
    return Response(content, status_code=status, media_type=headers.get("content-type"))


async def transparent(request: Request) -> Response:
    """Pass-through for non-masked endpoints (e.g. /models), no body transform."""
    rt: GatewayRuntime = request.app.state.runtime
    raw = await request.body()
    # Anything under /messages goes to Anthropic; everything else to the OpenAI base.
    base = rt.anthropic_upstream if "/messages" in request.url.path else rt.openai_upstream
    url = base + request.url.path
    return await forward_streaming(request.method, url, request.headers, raw)


def create_app(runtime: GatewayRuntime | None = None) -> Starlette:
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/responses", handle_responses, methods=["POST"]),
        Route("/v1/responses", handle_responses, methods=["POST"]),
        Route("/messages", handle_messages, methods=["POST"]),
        Route("/v1/messages", handle_messages, methods=["POST"]),
        Route("/{path:path}", transparent, methods=["GET", "POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.runtime = runtime or GatewayRuntime.from_env()
    return app
