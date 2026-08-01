"""SecurityMasker proxyのASGI application。

CodexのOpenAI ResponsesとClaude CodeのAnthropic Messagesをmasking coreへ
接続する。未知route、不正・過大なJSON、未対応Content-Encodingはlocalで拒否する。
内部headerは除去し、client認証情報は対応providerへだけ透過してログへ残さない。
一つのhandlerがsession解決、requestのマスク、転送、responseの復元までを所有する。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route, WebSocketRoute

from securitymasker.engine import MaskingEngine
from securitymasker.gateway.forwarder import forward_buffered, forward_streaming
from securitymasker.gateway.headers import client_headers
from securitymasker.gateway.request_pipeline import RequestRejected, prepare_request
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.telemetry import (
    GatewayTelemetryMiddleware,
    ResponseBindingStreamError,
)
from securitymasker.gateway.websocket import handle_responses_websocket
from securitymasker.logging import get_logger
from securitymasker.metrics import (
    BlockReason,
    Provider,
    StoreOperation,
)
from securitymasker.models import MaskingSession
from securitymasker.protocols import anthropic_messages, openai_responses
from securitymasker.protocols.base import MaskingSummary
from securitymasker.streaming.anthropic_messages_stream import AnthropicStreamProcessor
from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor

_log = get_logger(component="securitymasker.gateway")

# マスク前に受理するrequest bodyの最大値。testから変更できるmodule値。
# detectorとevent loopを過大入力から保護するhard limitである。
MAX_BODY_BYTES = 10 * 1024 * 1024
_INTERNAL_HEADER_PREFIX = "x-securitymasker-"


def _error(status: int, code: str, message: str) -> JSONResponse:
    # errorにはrequest bodyや機密値を絶対に再表示しない。
    return JSONResponse({"error": {"message": message, "type": code, "code": str(status)}},
                        status_code=status)


async def _read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """request bodyを検証してJSON objectへparseし、失敗時はlocal errorを返す。

    未対応のContent-Encoding／Content-Type、過大body、不正JSON、object以外のJSONは
    upstreamへ転送せず拒否する。
    """
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding and encoding != "identity":
        return None, _error(415, "unsupported_content_encoding",
                            f"Content-Encoding {encoding!r} is not supported.")
    ctype = request.headers.get("content-type", "")
    if ctype and "json" not in ctype.lower():
        return None, _error(415, "unsupported_media_type",
                            "Only application/json request bodies are supported.")
    # 宣言lengthで先に拒否し、stream受信中にも上限を強制する。虚偽のlengthを持つ
    # 過大bodyもmemoryへ全展開しない。
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None, _error(413, "payload_too_large",
                            f"Request body exceeds the {MAX_BODY_BYTES}-byte limit.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None, _error(413, "payload_too_large",
                                f"Request body exceeds the {MAX_BODY_BYTES}-byte limit.")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _error(400, "invalid_json", "Request body is not valid JSON.")
    if not isinstance(data, dict):
        return None, _error(400, "invalid_body", "Request body must be a JSON object.")
    return data, None


async def health(request: Request) -> JSONResponse:
    """liveness：processが起動していることを示す。"""
    return JSONResponse({"ok": True})


async def ready(request: Request) -> JSONResponse:
    """readiness：masking engineとSQLiteの両方が利用可能か実probeする。"""
    rt: GatewayRuntime = request.app.state.runtime
    try:
        probe = "__securitymasker_readiness__"
        await rt.store.get_or_create(probe)
        await rt.store.delete(probe)
    except Exception as exc:  # noqa: BLE001 - any store fault means not ready
        rt.telemetry.store_error(Provider.ADMIN, StoreOperation.READINESS)
        _log.debug("sm_not_ready", reason=type(exc).__name__)
        return JSONResponse({"ready": False, "reason": "session store unavailable"},
                            status_code=503)
    return JSONResponse({"ready": True})


async def _handle(
    request: Request,
    *,
    upstream: str,
    path: str,
    provider: str,
    mask: Callable[
        [MaskingEngine, MaskingSession, dict[str, Any]], Awaitable[MaskingSummary]
    ],
    restore_dict: Callable[[MaskingEngine, MaskingSession, dict[str, Any]], None],
    stream_processor: Callable[..., Any],
    prepare_wildcard_headers: Callable[
        [Mapping[str, str]], tuple[dict[str, Any], set[str]]
    ]
    | None = None,
    streaming_allowed: bool = True,
) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    telemetry = rt.telemetry
    provider_name = Provider(provider)
    url = f"{upstream}{path}"
    headers = client_headers(request.headers, provider)

    data, err = await _read_json_object(request)
    if err is not None or data is None:
        telemetry.blocked(provider_name, BlockReason.REQUEST_FORMAT)
        return err or _error(400, "invalid_body", "Request body must be a JSON object.")

    try:
        prepared = await prepare_request(
            rt,
            request.headers,
            data,
            provider_name=provider,
            path=path,
            mask=mask,
            prepare_wildcard_headers=prepare_wildcard_headers,
        )
    except RequestRejected as exc:
        return _error(exc.status, exc.code, exc.public_message)
    session = prepared.session
    store_key = prepared.store_key
    masked = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if streaming_allowed and data.get("stream"):
        proc = stream_processor(rt.engine.literal_restorations(session),
                                rt.engine.make_restorer(session),
                                rt.engine.tool_trust)
        # stream内response IDをこのsessionへbindingし、次turnで再利用する。
        # turn's previous_response_id continues it.
        async def _bind_stream(p: Any, key: str = store_key) -> None:
            try:
                for rid in getattr(p, "response_ids", ()):
                    await rt.store.bind_response(rid, key)
            except Exception:
                telemetry.store_error(provider_name, StoreOperation.RESPONSE_BINDING)
                # middlewareがresponse開始後の例外を一度だけstream errorとして数える。
                raise ResponseBindingStreamError(
                    "stream response binding failed"
                ) from None

        return await forward_streaming(request.method, url, headers, masked, proc,
                                       on_complete=_bind_stream)

    status, resp_headers, content = await forward_buffered(request.method, url, headers, masked)
    resp = None
    try:
        parsed = json.loads(content) if content else None
        resp = parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        resp = None
    if resp is not None:
        # 次turnがsessionを解決できるよう、復元前にresponse IDをbindingする。
        # previous_response_id resolves back here.
        rid = resp.get("id")
        if isinstance(rid, str) and rid:
            try:
                await rt.store.bind_response(rid, store_key)
            except Exception:
                telemetry.store_error(provider_name, StoreOperation.RESPONSE_BINDING)
                raise
        restore_dict(rt.engine, session, resp)
        return Response(json.dumps(resp, ensure_ascii=False), status_code=status,
                        media_type="application/json")
    return Response(content, status_code=status, media_type=resp_headers.get("content-type"))


async def handle_responses(request: Request) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    return await _handle(request, upstream=rt.openai_upstream, path="/responses",
                         provider="openai",
                         mask=openai_responses.mask_request,
                         restore_dict=openai_responses.restore_response,
                         stream_processor=ResponsesStreamProcessor,
                         prepare_wildcard_headers=openai_responses.prepare_wildcard_headers)


async def handle_messages(request: Request) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    return await _handle(request, upstream=rt.anthropic_upstream, path="/v1/messages",
                         provider="anthropic",
                         mask=anthropic_messages.mask_request,
                         restore_dict=anthropic_messages.restore_response,
                         stream_processor=AnthropicStreamProcessor)


def _restore_nothing(
    engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]
) -> None:
    """token count responseには復元対象のuser textがない。"""


async def handle_count_tokens(request: Request) -> Response:
    """実送信と同じmask済みMessages payloadをAnthropicに数えさせる。"""
    rt: GatewayRuntime = request.app.state.runtime
    return await _handle(
        request,
        upstream=rt.anthropic_upstream,
        path="/v1/messages/count_tokens",
        provider="anthropic",
        mask=anthropic_messages.mask_request,
        restore_dict=_restore_nothing,
        stream_processor=AnthropicStreamProcessor,
        streaming_allowed=False,
    )


async def handle_openai_models(request: Request) -> Response:
    """OpenAI model listをマスクせず透過する安全なGET経路。"""
    rt: GatewayRuntime = request.app.state.runtime
    url = f"{rt.openai_upstream}/models"
    return await forward_streaming(
        request.method, url, client_headers(request.headers, "openai"), await request.body()
    )


async def handle_anthropic_models(request: Request) -> Response:
    """Anthropic model listを対応するupstreamだけへ透過する。"""
    rt: GatewayRuntime = request.app.state.runtime
    url = f"{rt.anthropic_upstream}/v1/models"
    return await forward_streaming(
        request.method, url, client_headers(request.headers, "anthropic"), await request.body()
    )


async def handle_head_root(request: Request) -> Response:
    """Claude CodeのGateway到達確認へbody無しで応答する。"""
    return Response(status_code=200)


def create_app(runtime: GatewayRuntime | None = None) -> Starlette:
    # 明示allowlistだけを許しcatch-allは置かない。未知routeはlocalで404にする。
    routes: list[BaseRoute] = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
    ]
    resolved_runtime = runtime or GatewayRuntime.from_env()
    if resolved_runtime.product_mode == "chatgpt":
        routes.extend([
            Route("/responses", handle_responses, methods=["POST"]),
            Route("/v1/responses", handle_responses, methods=["POST"]),
            WebSocketRoute("/responses", handle_responses_websocket),
            WebSocketRoute("/v1/responses", handle_responses_websocket),
            Route("/models", handle_openai_models, methods=["GET"]),
            Route("/v1/models", handle_openai_models, methods=["GET"]),
        ])
    if resolved_runtime.product_mode == "claude":
        routes.extend([
            Route("/messages", handle_messages, methods=["POST"]),
            Route("/v1/messages", handle_messages, methods=["POST"]),
            Route(
                "/v1/messages/count_tokens",
                handle_count_tokens,
                methods=["POST"],
            ),
        ])
    if resolved_runtime.product_mode == "claude":
        routes.extend([
            Route("/v1/models", handle_anthropic_models, methods=["GET"]),
            Route("/", handle_head_root, methods=["HEAD"]),
        ])
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(
                GatewayTelemetryMiddleware,
                telemetry=resolved_runtime.telemetry,
            )
        ],
    )
    app.state.runtime = resolved_runtime
    return app
