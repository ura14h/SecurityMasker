"""Codex Responses WebSocket transport adapter。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from securitymasker.gateway.headers import websocket_headers
from securitymasker.gateway.request_pipeline import (
    RequestRejected,
    guard_unknown_event,
    prepare_connection_session,
    prepare_request,
)
from securitymasker.logging import get_logger, safe_fingerprint
from securitymasker.metrics import (
    BlockReason,
    Provider,
    StoreOperation,
    StreamErrorReason,
)
from securitymasker.models import MaskingSession
from securitymasker.protocols import openai_responses
from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor

MAX_MESSAGE_BYTES = 10 * 1024 * 1024
_TERMINAL_EVENTS = frozenset(
    {
        "response.completed",
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "error",
    }
)
_CONNECTION_EVENTS = frozenset({"responsesapi.websocket_timing"})
_log = get_logger(component="securitymasker.gateway.websocket")


class UpstreamConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self, decode: bool | None = None) -> str | bytes: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass
class _ActiveResponse:
    processor: ResponsesStreamProcessor
    started_at: float
    bound_ids: set[str]


def upstream_websocket_url(base_url: str) -> str:
    """検証済みHTTP upstream URLをResponses WebSocket URLへ変換する。"""
    parts = urlsplit(base_url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme)
    if scheme is None:
        raise ValueError("upstream scheme must be http or https")
    path = parts.path.rstrip("/") + "/responses"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


@asynccontextmanager
async def open_upstream(
    url: str,
    headers: Mapping[str, str],
    user_agent: str | None,
) -> AsyncIterator[ClientConnection]:
    """自動再接続しない一つの上流WebSocketを開く。"""
    async with connect(
        url,
        additional_headers=headers,
        user_agent_header=user_agent,
        compression=None,
        open_timeout=15,
        close_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_size=MAX_MESSAGE_BYTES,
        max_queue=16,
    ) as connection:
        yield connection


def _error_event(status: int, code: str, message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "status": status,
        "error": {
            "type": "securitymasker_websocket_error",
            "code": code,
            "message": message,
        },
    }


async def _send_json(websocket: WebSocket, event: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(event, ensure_ascii=False))


async def _close_with_error(
    websocket: WebSocket,
    *,
    status: int,
    code: str,
    message: str,
    close_code: int,
) -> None:
    if websocket.application_state is WebSocketState.CONNECTED:
        await _send_json(websocket, _error_event(status, code, message))
    if websocket.application_state is not WebSocketState.DISCONNECTED:
        await websocket.close(code=close_code, reason=code[:120])


def _parse_text_message(message: str) -> dict[str, Any]:
    if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise RequestRejected(
            413,
            "message_too_large",
            f"WebSocket message exceeds the {MAX_MESSAGE_BYTES}-byte limit.",
            BlockReason.REQUEST_FORMAT,
        )
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        raise RequestRejected(
            400,
            "invalid_json",
            "WebSocket message is not valid JSON.",
            BlockReason.REQUEST_FORMAT,
        ) from None
    if not isinstance(data, dict):
        raise RequestRejected(
            400,
            "invalid_body",
            "WebSocket message must be a JSON object.",
            BlockReason.REQUEST_FORMAT,
        )
    return data


def _validate_response_create(data: dict[str, Any]) -> None:
    if data.get("stream") is True:
        # Codex 0.145.0はWebSocket transportでもHTTP互換の``stream: true``を
        # 付与する。上流WebSocket APIではstreamingが暗黙なのでadapter境界で除く。
        data.pop("stream")
    elif "stream" in data:
        raise RequestRejected(
            400,
            "invalid_websocket_field",
            "stream must be true or omitted in WebSocket mode.",
            BlockReason.REQUEST_FORMAT,
        )
    if "background" in data:
        raise RequestRejected(
            400,
            "invalid_websocket_field",
            "background is not supported in WebSocket mode.",
            BlockReason.REQUEST_FORMAT,
        )


async def _relay(
    downstream: WebSocket,
    upstream: UpstreamConnection,
    *,
    session_id: str,
    initial_session: MaskingSession,
) -> None:
    runtime = downstream.app.state.runtime
    active: _ActiveResponse | None = None
    session = initial_session
    state_lock = asyncio.Lock()

    async def client_to_upstream() -> None:
        nonlocal active, session
        while True:
            message = await downstream.receive()
            if message["type"] == "websocket.disconnect":
                return
            binary = message.get("bytes")
            text = message.get("text")
            if binary is not None or not isinstance(text, str):
                await _close_with_error(
                    downstream,
                    status=400,
                    code="binary_message_not_supported",
                    message="Only UTF-8 JSON text messages are supported.",
                    close_code=1003,
                )
                return
            request_started_at: float | None = None
            try:
                data = _parse_text_message(text)
                event_type = data.get("type")
                if event_type == "response.create":
                    _validate_response_create(data)
                    async with state_lock:
                        if active is not None:
                            raise RequestRejected(
                                409,
                                "response_in_progress",
                                "Only one response may be in flight on a connection.",
                                BlockReason.REQUEST_FORMAT,
                            )
                    runtime.telemetry.request_started(Provider.OPENAI)
                    started_at = time.perf_counter()
                    request_started_at = started_at
                    prepared = await prepare_request(
                        runtime,
                        downstream.headers,
                        data,
                        provider_name="openai",
                        path="/responses",
                        mask=openai_responses.mask_request,
                        connection_session_id=session_id,
                    )
                    session = prepared.session
                    processor = ResponsesStreamProcessor(
                        runtime.engine.literal_restorations(session),
                        runtime.engine.make_restorer(session),
                        runtime.engine.tool_trust,
                    )
                    async with state_lock:
                        active = _ActiveResponse(processor, started_at, set())
                else:
                    await guard_unknown_event(
                        runtime,
                        data,
                        session=session,
                        store_key=session_id,
                    )
                outbound = json.dumps(data, ensure_ascii=False)
                if len(outbound.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    raise RequestRejected(
                        413,
                        "message_too_large",
                        f"Masked WebSocket message exceeds the {MAX_MESSAGE_BYTES}-byte limit.",
                        BlockReason.REQUEST_FORMAT,
                    )
                await upstream.send(outbound)
            except RequestRejected as exc:
                if not exc.reported:
                    runtime.telemetry.blocked(
                        Provider.OPENAI, exc.reason, session_id=session_id
                    )
                if request_started_at is not None:
                    runtime.telemetry.request_completed(
                        Provider.OPENAI,
                        status_code=exc.status,
                        duration_ms=(time.perf_counter() - request_started_at) * 1000.0,
                    )
                await _close_with_error(
                    downstream,
                    status=exc.status,
                    code=exc.code,
                    message=exc.public_message,
                    close_code=1009 if exc.status == 413 else 1008,
                )
                return

    async def upstream_to_client() -> None:
        nonlocal active
        while True:
            message = await upstream.recv()
            if not isinstance(message, str):
                runtime.telemetry.stream_error(
                    Provider.OPENAI, StreamErrorReason.PROCESSING
                )
                await _close_with_error(
                    downstream,
                    status=502,
                    code="invalid_upstream_message",
                    message="Upstream sent a non-text WebSocket message.",
                    close_code=1011,
                )
                return
            if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                runtime.telemetry.stream_error(
                    Provider.OPENAI, StreamErrorReason.PROCESSING
                )
                await _close_with_error(
                    downstream,
                    status=502,
                    code="upstream_message_too_large",
                    message="Upstream WebSocket message exceeded the safety limit.",
                    close_code=1009,
                )
                return
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                event = None
            if not isinstance(event, dict):
                runtime.telemetry.stream_error(
                    Provider.OPENAI, StreamErrorReason.PROCESSING
                )
                await _close_with_error(
                    downstream,
                    status=502,
                    code="invalid_upstream_json",
                    message="Upstream WebSocket message was not a JSON object.",
                    close_code=1011,
                )
                return
            async with state_lock:
                current = active
            if current is None:
                if event.get("type") in _CONNECTION_EVENTS:
                    try:
                        await guard_unknown_event(
                            runtime,
                            event,
                            session=session,
                            store_key=session_id,
                        )
                    except RequestRejected as exc:
                        await _close_with_error(
                            downstream,
                            status=exc.status,
                            code=exc.code,
                            message=exc.public_message,
                            close_code=1008,
                        )
                        return
                    await _send_json(downstream, event)
                    continue
                _log.debug(
                    "sm_websocket_unexpected_upstream_event",
                    event_type=event.get("type"),
                )
                runtime.telemetry.stream_error(
                    Provider.OPENAI, StreamErrorReason.PROCESSING
                )
                await _close_with_error(
                    downstream,
                    status=502,
                    code="unexpected_upstream_event",
                    message="Upstream sent an event without an active response.",
                    close_code=1011,
                )
                return
            outputs = current.processor.process_event(event)
            try:
                for response_id in current.processor.response_ids - current.bound_ids:
                    await runtime.store.bind_response(response_id, session_id)
                    current.bound_ids.add(response_id)
            except Exception:
                runtime.telemetry.store_error(
                    Provider.OPENAI, StoreOperation.RESPONSE_BINDING
                )
                runtime.telemetry.stream_error(
                    Provider.OPENAI, StreamErrorReason.RESPONSE_BINDING
                )
                await _close_with_error(
                    downstream,
                    status=503,
                    code="response_binding_failed",
                    message="Session store unavailable while binding the response.",
                    close_code=1011,
                )
                return

            terminal = event.get("type") in _TERMINAL_EVENTS
            if terminal:
                outputs.extend(current.processor.finish_events())
            for output in outputs:
                await _send_json(downstream, output)
                if output.get("type") == "error" and event.get("type") != "error":
                    runtime.telemetry.stream_error(
                        Provider.OPENAI, StreamErrorReason.PROCESSING
                    )
                    await downstream.close(
                        code=1008, reason="response_processing_failed"
                    )
                    return
            if terminal:
                status = event.get("status")
                if not isinstance(status, int):
                    status = (
                        400
                        if event.get("type")
                        in {"error", "response.failed", "response.incomplete"}
                        else 200
                    )
                duration_ms = (time.perf_counter() - current.started_at) * 1000.0
                runtime.telemetry.request_completed(
                    Provider.OPENAI,
                    status_code=status,
                    duration_ms=duration_ms,
                )
                _log.debug(
                    "sm_websocket_turn_completed",
                    session_fp=safe_fingerprint(session_id),
                    outcome="success" if status < 400 else "error",
                    duration_ms=round(duration_ms, 1),
                )
                async with state_lock:
                    active = None

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        if not task.cancelled():
            task.result()


async def handle_responses_websocket(websocket: WebSocket) -> None:
    """downstream Codexとupstream Responses WebSocketを安全に中継する。"""
    runtime = websocket.app.state.runtime
    try:
        session_id, session = await prepare_connection_session(
            runtime,
            websocket.headers,
            provider_name="openai",
            path="/responses",
        )
    except RequestRejected as exc:
        await websocket.close(code=1008, reason=exc.code[:120])
        return

    headers, user_agent = websocket_headers(websocket.headers)
    url = upstream_websocket_url(runtime.openai_upstream)
    try:
        async with open_upstream(url, headers, user_agent) as upstream:
            await websocket.accept()
            _log.debug(
                "sm_websocket_connected",
                session_fp=safe_fingerprint(session_id),
            )
            await _relay(
                websocket,
                upstream,
                session_id=session_id,
                initial_session=session,
            )
    except asyncio.CancelledError:
        _log.debug("sm_websocket_disconnected", reason="cancelled")
        return
    except (ConnectionClosed, WebSocketDisconnect) as exc:
        code = getattr(exc, "code", None)
        if code not in {1000, 1001}:
            runtime.telemetry.stream_error(
                Provider.OPENAI, StreamErrorReason.NETWORK
            )
        _log.debug("sm_websocket_disconnected", close_code=code)
        return
    except Exception as exc:  # noqa: BLE001 - transport detailはclient/logへ出さない
        runtime.telemetry.stream_error(
            Provider.OPENAI, StreamErrorReason.PROCESSING
        )
        _log.debug("sm_websocket_disconnected", reason=type(exc).__name__)
        await _close_with_error(
            websocket,
            status=502,
            code="upstream_websocket_failed",
            message="Upstream WebSocket connection failed.",
            close_code=1011,
        )
