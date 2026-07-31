"""Upstream転送のprotocol境界と安全な通信log levelを検証する。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from securitymasker.gateway import forwarder
from securitymasker.gateway.forwarder import response_media_type


class _RecordingLogger:
    def __init__(self) -> None:
        self.debug_events: list[tuple[str, dict[str, Any]]] = []
        self.warning_events: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **fields: Any) -> None:
        self.debug_events.append((event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.warning_events.append((event, fields))


def test_missing_success_content_type_is_treated_as_event_stream() -> None:
    assert (
        response_media_type(None, status_code=200, has_processor=True)
        == "text/event-stream"
    )


def test_explicit_content_type_is_preserved() -> None:
    assert (
        response_media_type(
            "application/json",
            status_code=200,
            has_processor=True,
        )
        == "application/json"
    )


def test_missing_error_content_type_is_not_treated_as_event_stream() -> None:
    assert response_media_type(None, status_code=400, has_processor=True) is None


def test_unprocessed_stream_does_not_invent_content_type() -> None:
    assert response_media_type(None, status_code=200, has_processor=False) is None


@pytest.mark.asyncio
async def test_buffered_upstream_network_failure_is_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _RecordingLogger()

    class FailingClient:
        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, *args: object, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", "https://example.invalid/responses")
            raise httpx.ConnectError("synthetic connection failure", request=request)

    monkeypatch.setattr(forwarder, "_log", logger)
    monkeypatch.setattr(forwarder.httpx, "AsyncClient", lambda **kwargs: FailingClient())

    with pytest.raises(httpx.ConnectError):
        await forwarder.forward_buffered(
            "POST", "https://example.invalid/responses", {}, b"{}"
        )

    assert logger.warning_events == [
        ("sm_upstream_network_error", {"reason": "ConnectError"})
    ]


@pytest.mark.asyncio
async def test_buffered_upstream_status_is_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _RecordingLogger()

    class SuccessfulClient:
        async def __aenter__(self) -> SuccessfulClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, *args: object, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", "https://example.invalid/responses")
            return httpx.Response(202, content=b"{}", request=request)

    monkeypatch.setattr(forwarder, "_log", logger)
    monkeypatch.setattr(
        forwarder.httpx, "AsyncClient", lambda **kwargs: SuccessfulClient()
    )

    status, _, _ = await forwarder.forward_buffered(
        "POST", "https://example.invalid/responses", {}, b"{}"
    )

    assert status == 202
    assert logger.debug_events == [
        ("sm_upstream_response_completed", {"status_code": 202})
    ]
