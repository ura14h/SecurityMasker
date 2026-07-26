"""ASGI response全体を観測するGateway telemetry middleware。"""

from __future__ import annotations

import asyncio
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from securitymasker.metrics import (
    GatewayTelemetry,
    StreamErrorReason,
    provider_for_path,
)


class ResponseBindingStreamError(RuntimeError):
    """stream完了時のresponse bindingだけを安全に識別する内部例外。"""


class GatewayTelemetryMiddleware:
    """最終stream chunkまで含むrequest数・status・latency・stream errorを記録する。"""

    def __init__(self, app: ASGIApp, *, telemetry: GatewayTelemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        provider = provider_for_path(str(scope.get("path", "")))
        self.telemetry.request_started(provider)
        started_at = time.perf_counter()
        status_code = 500
        response_started = False
        completed = False

        def complete(status: int) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            self.telemetry.request_completed(
                provider,
                status_code=status,
                duration_ms=(time.perf_counter() - started_at) * 1000.0,
            )

        async def observed_send(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not bool(message.get("more_body", False))
            ):
                complete(status_code)

        try:
            await self.app(scope, receive, observed_send)
            complete(status_code)
        except BaseException as exc:
            if response_started:
                if isinstance(exc, ResponseBindingStreamError):
                    reason = StreamErrorReason.RESPONSE_BINDING
                elif isinstance(exc, asyncio.CancelledError):
                    reason = StreamErrorReason.CANCELLED
                else:
                    reason = StreamErrorReason.PROCESSING
                self.telemetry.stream_error(provider, reason)
            complete(500)
            raise
