"""SecurityMasker proxy ASGI app（ADR-0006、doc/06-Issue.md P0で堅牢化）。

Routes Codex (OpenAI Responses) and Claude Code (Anthropic Messages) through the
masking core. Security gates (external-send is only allowed after masking):

- explicit route allowlist — unknown routes are refused locally, never forwarded;
- request validation — non-JSON-object, malformed, oversized, or unsupported
  Content-Encoding bodies are refused locally before anything leaves;
- header hygiene — internal `X-SecurityMasker-*` headers are stripped; client auth
  is passed through to the correct upstream, never logged (§25);
- readiness (`/ready`) is distinct from liveness (`/health`).

One handler invocation resolves the session, masks the request, forwards it, and
restores the response — owning both directions.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from securitymasker.detectors.existing_alias import contains_alias_shape
from securitymasker.engine import MaskingEngine, iter_strings
from securitymasker.errors import SecurityMaskerError
from securitymasker.gateway.forwarder import forward_buffered, forward_streaming
from securitymasker.gateway.identity import Identity, IdentityError, resolve_identity
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.session import namespaced_key, resolve_session
from securitymasker.logging import get_logger
from securitymasker.models import MaskingSession
from securitymasker.protocols import anthropic_messages, openai_responses
from securitymasker.streaming.anthropic_messages_stream import AnthropicStreamProcessor
from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor

_log = get_logger(component="securitymasker.gateway")

# マスク前に受理するrequest bodyの最大値（doc/06 P0-5）。testから変更できるmodule値。
# tuned/overridden; a hard cap protects the detectors and the event loop (§32).
MAX_BODY_BYTES = 10 * 1024 * 1024
_INTERNAL_HEADER_PREFIX = "x-securitymasker-"


def _error(status: int, code: str, message: str) -> JSONResponse:
    # errorにはrequest body／値を絶対に再表示しない（§25）。
    return JSONResponse({"error": {"message": message, "type": code, "code": str(status)}},
                        status_code=status)


def _payload_has_alias_shape(data: dict[str, Any]) -> bool:
    return any(contains_alias_shape(s) for s in iter_strings(data))


# 全upstreamへ転送可能なheader。deny-by-defaultで未記載項目を拒否する。
# in the provider set below) is dropped, so a custom header can neither leak a
# secret nor smuggle data past the masker (doc/06 P0-4).
_COMMON_HEADERS = frozenset({
    "accept", "accept-encoding", "accept-language", "content-type", "content-length",
    "user-agent",
})

# provider固有header。Anthropicの`x-api-key`をOpenAIへ送ってはならない。
# `openai-*`/`chatgpt-*` are OpenAI's and must never reach Anthropic (doc/06 P0-3).
# `authorization` is Bearer for BOTH providers, so it is forwarded only to the
# upstream the client's own route selected — never copied across providers.
# 実Codex sessionで確認したCodex固有request header（doc/05-Phase6-Design.md）。
# Codex session). `session-id`/`thread-id` are the correct spellings — Codex sends
# them and the session resolver reads them, so they must reach the upstream too.
_OPENAI_HEADERS = frozenset({
    "authorization", "openai-organization", "openai-project", "openai-beta",
    "chatgpt-account-id", "originator", "session-id", "thread-id",
    "x-openai-internal-codex-responses-lite",
})


def _is_openai_passthrough(name: str) -> bool:
    """Codexが送る``x-codex-*`` header群をまとめて転送する。

    Because the NAME is not known in advance, the VALUE is untrusted free text and
    is scanned with the full deterministic detector set before forwarding — unlike
    the fixed, structural headers above (doc/06 P0-4).
    """
    return name.startswith("x-codex-")
_ANTHROPIC_HEADERS = frozenset({
    "authorization", "x-api-key", "anthropic-version", "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
})

PROVIDER_HEADERS: dict[str, frozenset[str]] = {
    "openai": _OPENAI_HEADERS,
    "anthropic": _ANTHROPIC_HEADERS,
}

# auth headerは対応providerへ透過するが、マスク・保存・ログ記録しない。
# or logged (§25) — and never leak-scanned into an error message.
_AUTH_HEADERS = frozenset({"authorization", "x-api-key"})

# user contentではなくclient／proxy transportが生成するheader。
# legitimately hold IPs and hostnames, so they are not leak-scanned.
_TRANSPORT_HEADERS = frozenset({
    "host", "connection", "content-length", "accept-encoding", "accept",
    "accept-language", "user-agent", "via", "forwarded",
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
})


def _client_headers(request: Request, provider: str) -> dict[str, str]:
    """``provider``ごとのallowlist済みheaderを返し、それ以外は破棄する。"""
    allowed = _COMMON_HEADERS | PROVIDER_HEADERS.get(provider, frozenset())
    return {
        k: v for k, v in request.headers.items()
        if k.lower() in allowed or (provider == "openai" and _is_openai_passthrough(k.lower()))
    }


async def _read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """request bodyを検証してJSON objectへparseし、失敗時はlocal errorを返す。

    Refuses (no forward) on unsupported Content-Encoding/Type, oversized bodies,
    malformed JSON, or non-object JSON (doc/06 P0-2/P0-5).
    """
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding and encoding != "identity":
        return None, _error(415, "unsupported_content_encoding",
                            f"Content-Encoding {encoding!r} is not supported.")
    ctype = request.headers.get("content-type", "")
    if ctype and "json" not in ctype.lower():
        return None, _error(415, "unsupported_media_type",
                            "Only application/json request bodies are supported.")
    # 宣言lengthで先に拒否し、stream受信中にも上限を強制する。
    # oversized (or lying) body is never fully materialised in memory (doc/06 P1-5).
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
    """readiness：masking engineとsession storeの両方が利用可能か示す（doc/06 P0-1）。

    Probes the store for real — a configured-but-unreachable Redis (or a bad master
    key) must not report ready, since every request depends on it (finding 8).
    """
    rt: GatewayRuntime = request.app.state.runtime
    if rt.engine is None:
        return JSONResponse({"ready": False, "reason": "masking engine not configured"},
                            status_code=503)
    try:
        # Redis storeのnamespaceを一致させるため、同じargumentで作成・削除する。
        # tenant, so creating with a tenant and deleting without one wrote one key
        # and removed another, leaking a probe session on every check.
        probe = namespaced_key("_readiness", "__securitymasker_readiness__")
        await rt.store.get_or_create(probe)
        await rt.store.delete(probe)
    except Exception as exc:  # noqa: BLE001 - any store fault means not ready
        _log.warning("sm_not_ready", reason=type(exc).__name__)
        return JSONResponse({"ready": False, "reason": "session store unavailable"},
                            status_code=503)
    return JSONResponse({"ready": True})


async def _handle(
    request: Request,
    *,
    upstream: str,
    path: str,
    provider: str,
    mask: Callable[[MaskingEngine, MaskingSession, dict[str, Any]], Awaitable[None]],
    restore_dict: Callable[[MaskingEngine, MaskingSession, dict[str, Any]], None],
    stream_processor: Callable[..., Any],
) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    url = f"{upstream}{path}"
    headers = _client_headers(request, provider)

    if rt.engine is None:
        # 開発専用のtransparent mode。既定ではconfig必須（runtime参照）。
        return await forward_streaming(request.method, url, headers, await request.body())

    data, err = await _read_json_object(request)
    if err is not None or data is None:
        return err or _error(400, "invalid_body", "Request body must be a JSON object.")

    try:
        # cryptoとheader canonicalizationはhandlerではなくidentity moduleで検証する。
        # parsing stay out of request orchestration (§ architecture).
        identity = resolve_identity(
            rt.mode, request.headers,
            auth_secret=rt.tenant_auth_secret,
            max_skew_seconds=rt.max_clock_skew_seconds,
            require_timestamp=rt.require_assertion_timestamp,
            tenant_header=rt.tenant_header,
        )
    except IdentityError as exc:
        # caller間のalias table共有を防ぐためfail-closedにする。
        # what this boundary exists to prevent (doc/06 P0-9). The message never
        # echoes the presented proof or claimed identity (§25).
        _log.warning("sm_block_identity", path=path, reason=str(exc))
        return _error(403, "identity_required", str(exc))

    resolved = resolve_session(request.headers, data)
    store_key = namespaced_key(identity, resolved.session_id)
    stable = resolved.stable
    if not stable and resolved.previous_response_id:
        # previous_response_id changes every turn, so it is a lookup handle only:
        # continue the session that actually produced that response (doc/06 P1-1).
        # 保存値はすでにtenant namespaceを含む完全なstore key。
        bound = await rt.store.resolve_response(
            namespaced_key(identity, resolved.previous_response_id)
        )
        if bound is not None and await rt.store.get(bound) is None:
            # binding元sessionが期限切れ・revoke・purge済みなら新sessionを作らない。
            # a fresh one here would silently re-mask the client's existing aliases
            # onto a new table, which is exactly the drift we block for (P1-1).
            bound = None
        if bound is None:
            # 対応sessionを特定できないconversation継続は拒否する。
            # another tenant's, or never ours). Refuse unconditionally: the body
            # may carry aliases we can neither recognise nor restore. Shape
            # heuristics are NOT enough here — a numeric or uuid alias is
            # indistinguishable from ordinary data, and such a request would be
            # silently re-masked onto a new table (doc/06 P1-1).
            _log.warning("sm_block_unknown_previous_response", path=path)
            return _error(409, "session_unresolved",
                          "previous_response_id does not match a known session.")
        store_key, stable = bound, True

    if not stable and _payload_has_alias_shape(data):
        # 前turnのaliasがあるのに安定sessionがなければ、新規作成せずblockする。
        # of silently starting a fresh session and corrupting the turn (P1-1).
        _log.warning("sm_block_unresolved_session", path=path)
        return _error(409, "session_unresolved",
                      "Request references prior aliases but no stable session id was provided.")

    try:
        # get／create／mask／saveを一つの排他区間に置くため最初にlockする。
        # workers can't fork the same session's alias table (doc/06 P1-9).
        async with rt.store.lock(store_key) as held:
            session = await rt.store.get_or_create(
                store_key, tenant_id=identity.tenant, user_id=identity.user)
            await mask(rt.engine, session, data)
            # masking中のlock失効に備え、書き込み直前にownershipを再確認する。
            # long enough for a distributed lock to expire and be taken over, and
            # a non-owner write would corrupt the other holder's alias table
            # (doc/06 P1-9). Verifying after the save would be too late.
            await held.verify()
            await rt.store.save(session)
            held.check()
        # マスク済みpayload全体への最終block-only guard（doc/06 P0-4）。
        # registered secret in an unknown/structural field must never be forwarded.
        await rt.engine.assert_no_leak_in_payload(data, session=session, request_id=store_key)
        # allowlist対象だけでなく全incoming non-auth headerにも同じguardを適用する。
        # subset — so a secret placed in a header is refused outright rather than
        # silently dropped (doc/06 P0-4). Provider auth headers are excluded: they
        # are the client's own credential for this upstream and must never be
        # scanned into an error or log (§25). Transport headers are excluded too:
        # they are generated by the client/proxy, not user content.
        scannable = {k: v for k, v in request.headers.items()
                     if k.lower() not in _AUTH_HEADERS and k.lower() not in _TRANSPORT_HEADERS}
        await rt.engine.assert_no_leak_in_headers(
            scannable, session=session, request_id=store_key,
        )
        # wildcard一致する`x-codex-*`は自由text値を持つため個別scanする。
        # the narrow header scan is not enough — run the FULL deterministic set on
        # them, exactly as for a body field (doc/06 P0-4).
        wildcard = {k: v for k, v in scannable.items() if _is_openai_passthrough(k.lower())}
        if wildcard:
            await rt.engine.assert_no_leak_in_payload(
                wildcard, session=session, request_id=store_key,
            )
    except SecurityMaskerError as exc:
        _log.warning("sm_block", path=path)
        return _error(400, "securitymasker_blocked", str(exc))

    masked = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if data.get("stream"):
        proc = stream_processor(rt.engine.literal_restorations(session),
                                rt.engine.make_restorer(session),
                                rt.engine.tool_trust)
        # stream内response IDをこのsessionへbindingし、次turnで再利用する。
        # turn's previous_response_id continues it (doc/06 P1-1).
        bound_identity = identity

        async def _bind_stream(p: Any, key: str = store_key,
                               ident: Identity = bound_identity) -> None:
            for rid in getattr(p, "response_ids", ()):
                await rt.store.bind_response(namespaced_key(ident, rid), key)

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
        # previous_response_id resolves back here (doc/06 P1-1).
        rid = resp.get("id")
        if isinstance(rid, str) and rid:
            await rt.store.bind_response(namespaced_key(identity, rid), store_key)
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
                         stream_processor=ResponsesStreamProcessor)


async def handle_messages(request: Request) -> Response:
    rt: GatewayRuntime = request.app.state.runtime
    return await _handle(request, upstream=rt.anthropic_upstream, path="/v1/messages",
                         provider="anthropic",
                         mask=anthropic_messages.mask_request,
                         restore_dict=anthropic_messages.restore_response,
                         stream_processor=AnthropicStreamProcessor)


async def handle_models(request: Request) -> Response:
    """model listをマスクせず透過する安全なGET経路（doc/06 P0-3）。"""
    rt: GatewayRuntime = request.app.state.runtime
    url = f"{rt.openai_upstream}/models"
    return await forward_streaming(
        request.method, url, _client_headers(request, "openai"), await request.body()
    )


def create_app(runtime: GatewayRuntime | None = None) -> Starlette:
    # 明示allowlistだけを許しcatch-allは置かない。未知routeはlocalで404（P0-3）。
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route("/responses", handle_responses, methods=["POST"]),
        Route("/v1/responses", handle_responses, methods=["POST"]),
        Route("/messages", handle_messages, methods=["POST"]),
        Route("/v1/messages", handle_messages, methods=["POST"]),
        Route("/models", handle_models, methods=["GET"]),
        Route("/v1/models", handle_models, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.runtime = runtime or GatewayRuntime.from_env()
    return app
