"""HTTPとWebSocketが共有するrequest masking pipeline。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from securitymasker.detectors.existing_alias import contains_alias_shape
from securitymasker.engine import MaskingEngine, iter_strings
from securitymasker.errors import (
    DetectionError,
    DetectorTimeoutError,
    LeakageError,
    MaskingError,
    SecurityMaskerError,
    SessionError,
)
from securitymasker.gateway.headers import scannable_headers, wildcard_headers
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.session import resolve_session
from securitymasker.logging import get_logger
from securitymasker.metrics import BlockReason, Provider, StoreOperation
from securitymasker.models import MaskingSession
from securitymasker.protocols.base import MaskingSummary

_log = get_logger(component="securitymasker.gateway")

MaskRequest = Callable[
    [MaskingEngine, MaskingSession, dict[str, Any]], Awaitable[MaskingSummary]
]


@dataclass(frozen=True)
class PreparedRequest:
    """mask・保存・最終guardが完了したupstream送信可能request。"""

    data: dict[str, Any]
    session: MaskingSession
    store_key: str
    summary: MaskingSummary


class RequestRejected(Exception):
    """原文を含まないclient向け拒否情報。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        reason: BlockReason,
        *,
        reported: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.public_message = message
        self.reason = reason
        self.reported = reported


def _payload_has_alias_shape(data: dict[str, Any]) -> bool:
    return any(contains_alias_shape(value) for value in iter_strings(data))


def _security_reason(exc: SecurityMaskerError) -> BlockReason:
    if isinstance(exc, DetectorTimeoutError):
        return BlockReason.DETECTOR_TIMEOUT
    if isinstance(exc, DetectionError):
        return BlockReason.DETECTOR_FAILURE
    if isinstance(exc, LeakageError):
        return BlockReason.LEAK_GUARD
    if isinstance(exc, MaskingError):
        return BlockReason.MASKING
    return BlockReason.MASKING


async def _resolve_store_key(
    runtime: GatewayRuntime,
    headers: Mapping[str, str],
    data: dict[str, Any],
    *,
    provider: Provider,
    path: str,
    connection_session_id: str | None,
) -> tuple[str, bool]:
    """session keyを解決し、connection境界を越えるbindingを拒否する。"""
    previous = data.get("previous_response_id")
    previous_id = previous if isinstance(previous, str) and previous else None

    if connection_session_id is not None:
        if previous_id is not None:
            try:
                bound = await runtime.store.resolve_response(previous_id)
                if bound is not None and await runtime.store.get(bound) is None:
                    bound = None
            except Exception as exc:
                runtime.telemetry.store_error(provider, StoreOperation.REQUEST)
                runtime.telemetry.blocked(provider, BlockReason.STORE)
                _log.warning("sm_block_store", path=path, reason=type(exc).__name__)
                raise RequestRejected(
                    503,
                    "session_store_unavailable",
                    "Session store unavailable.",
                    BlockReason.STORE,
                    reported=True,
                ) from None
            if bound != connection_session_id:
                runtime.telemetry.blocked(
                    provider,
                    BlockReason.SESSION_UNRESOLVED,
                    session_id=connection_session_id,
                )
                _log.warning("sm_block_unknown_previous_response", path=path)
                raise RequestRejected(
                    409,
                    "session_unresolved",
                    "previous_response_id does not match this WebSocket session.",
                    BlockReason.SESSION_UNRESOLVED,
                    reported=True,
                )
        return connection_session_id, True

    resolved = resolve_session(headers, data)
    store_key = resolved.session_id
    stable = resolved.stable
    if not stable and resolved.previous_response_id:
        try:
            bound = await runtime.store.resolve_response(resolved.previous_response_id)
            if bound is not None and await runtime.store.get(bound) is None:
                bound = None
        except Exception as exc:
            runtime.telemetry.store_error(provider, StoreOperation.REQUEST)
            runtime.telemetry.blocked(provider, BlockReason.STORE)
            _log.warning("sm_block_store", path=path, reason=type(exc).__name__)
            raise RequestRejected(
                503,
                "session_store_unavailable",
                "Session store unavailable.",
                BlockReason.STORE,
                reported=True,
            ) from None
        if bound is None:
            _log.warning("sm_block_unknown_previous_response", path=path)
            runtime.telemetry.blocked(provider, BlockReason.SESSION_UNRESOLVED)
            raise RequestRejected(
                409,
                "session_unresolved",
                "previous_response_id does not match a known session.",
                BlockReason.SESSION_UNRESOLVED,
                reported=True,
            )
        store_key, stable = bound, True
    return store_key, stable


async def prepare_request(
    runtime: GatewayRuntime,
    headers: Mapping[str, str],
    data: dict[str, Any],
    *,
    provider_name: str,
    path: str,
    mask: MaskRequest,
    connection_session_id: str | None = None,
) -> PreparedRequest:
    """requestをmask・保存・leak scanし、upstream送信可能な状態へする。"""
    provider = Provider(provider_name)
    store_key, stable = await _resolve_store_key(
        runtime,
        headers,
        data,
        provider=provider,
        path=path,
        connection_session_id=connection_session_id,
    )

    if not stable and _payload_has_alias_shape(data):
        _log.warning("sm_block_unresolved_session", path=path)
        runtime.telemetry.blocked(provider, BlockReason.SESSION_UNRESOLVED)
        raise RequestRejected(
            409,
            "session_unresolved",
            "Request references prior aliases but no stable session id was provided.",
            BlockReason.SESSION_UNRESOLVED,
            reported=True,
        )

    try:
        async with runtime.store.lock(store_key) as held:
            session = await runtime.store.get_or_create(store_key, lock=held)
            summary = await mask(runtime.engine, session, data)
            await held.verify()
            await runtime.store.save(session, lock=held)
            held.check()
        await runtime.engine.assert_no_leak_in_payload(
            data, session=session, request_id=store_key
        )
        scannable = scannable_headers(headers)
        await runtime.engine.assert_no_leak_in_headers(
            scannable, session=session, request_id=store_key
        )
        wildcard = wildcard_headers(headers, provider_name)
        if wildcard:
            await runtime.engine.assert_no_leak_in_payload(
                wildcard, session=session, request_id=store_key
            )
    except SessionError as exc:
        runtime.telemetry.store_error(provider, StoreOperation.REQUEST)
        runtime.telemetry.blocked(provider, BlockReason.STORE, session_id=store_key)
        _log.warning("sm_block_store", path=path, reason=type(exc).__name__)
        raise RequestRejected(
            503,
            "session_store_unavailable",
            "Session store unavailable.",
            BlockReason.STORE,
            reported=True,
        ) from None
    except SecurityMaskerError as exc:
        reason = _security_reason(exc)
        runtime.telemetry.blocked(provider, reason, session_id=store_key)
        _log.warning("sm_block", path=path)
        raise RequestRejected(
            400,
            "securitymasker_blocked",
            str(exc),
            reason,
            reported=True,
        ) from None

    runtime.telemetry.masked(
        provider, summary.entity_counts, session_id=store_key
    )
    return PreparedRequest(data, session, store_key, summary)


async def prepare_connection_session(
    runtime: GatewayRuntime,
    headers: Mapping[str, str],
    *,
    provider_name: str,
    path: str,
) -> tuple[str, MaskingSession]:
    """WebSocket接続固定sessionを作り、handshake headerを送信前検査する。"""
    provider = Provider(provider_name)
    resolved = resolve_session(headers)
    store_key = resolved.session_id
    try:
        async with runtime.store.lock(store_key) as held:
            session = await runtime.store.get_or_create(store_key, lock=held)
            await held.verify()
            await runtime.store.save(session, lock=held)
            held.check()
        scannable = scannable_headers(headers)
        await runtime.engine.assert_no_leak_in_headers(
            scannable, session=session, request_id=store_key
        )
        wildcard = wildcard_headers(headers, provider_name)
        if wildcard:
            await runtime.engine.assert_no_leak_in_payload(
                wildcard, session=session, request_id=store_key
            )
    except SessionError as exc:
        runtime.telemetry.store_error(provider, StoreOperation.REQUEST)
        runtime.telemetry.blocked(provider, BlockReason.STORE, session_id=store_key)
        _log.warning("sm_block_store", path=path, reason=type(exc).__name__)
        raise RequestRejected(
            503,
            "session_store_unavailable",
            "Session store unavailable.",
            BlockReason.STORE,
            reported=True,
        ) from None
    except SecurityMaskerError as exc:
        reason = _security_reason(exc)
        runtime.telemetry.blocked(provider, reason, session_id=store_key)
        _log.warning("sm_block", path=path)
        raise RequestRejected(
            400,
            "securitymasker_blocked",
            str(exc),
            reason,
            reported=True,
        ) from None
    return store_key, session


async def guard_unknown_event(
    runtime: GatewayRuntime,
    event: dict[str, Any],
    *,
    session: MaskingSession,
    store_key: str,
) -> None:
    """未知client eventは変更せず、全体guardを通るclean eventだけ許可する。"""
    try:
        await runtime.engine.assert_no_leak_in_payload(
            event, session=session, request_id=store_key
        )
    except SecurityMaskerError as exc:
        reason = _security_reason(exc)
        runtime.telemetry.blocked(
            Provider.OPENAI, reason, session_id=store_key
        )
        _log.warning("sm_block", path="/responses")
        raise RequestRejected(
            400,
            "securitymasker_blocked",
            str(exc),
            reason,
            reported=True,
        ) from None
