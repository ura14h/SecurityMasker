"""Gateway実経路でmetrics／auditが欠落せず、秘密値を保持しないことを検証する。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.responses import StreamingResponse

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.engine import MaskingEngine
from securitymasker.errors import SessionError
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.metrics import (
    AuditEvent,
    AuditRecord,
    BlockReason,
    GatewayTelemetry,
    Metrics,
    StoreOperation,
    StreamErrorReason,
)
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import LockHandle, SessionStore

PERSON = "監査用合成人物"


def _telemetry() -> tuple[GatewayTelemetry, Metrics, list[AuditRecord]]:
    metrics = Metrics()
    records: list[AuditRecord] = []
    return GatewayTelemetry(metrics=metrics, audit_sink=records.append), metrics, records


def _runtime(
    telemetry: GatewayTelemetry,
    *,
    engine: MaskingEngine | None = None,
    store: SessionStore | None = None,
) -> GatewayRuntime:
    if engine is None:
        engine = MaskingEngine(
            [
                DictionaryDetector(
                    [
                        DictionaryEntry(
                            EntityType.PERSON.value,
                            (PERSON,),
                            ReplacementProfile.PROSE_IDENTIFIER.value,
                            RestorePolicy.LITERAL.value,
                        )
                    ]
                )
            ]
        )
    return GatewayRuntime(
        engine,
        store or InMemorySessionStore(),
        openai_upstream="http://up.test",
        anthropic_upstream="http://up.test",
        product_mode="chatgpt",
        telemetry=telemetry,
    )


async def _buffered_echo(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, dict[str, str], bytes]:
    del method, url, headers
    text = json.loads(body)["input"]
    response = {
        "id": "response-1",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }
    return 200, {"content-type": "application/json"}, json.dumps(response).encode()


@pytest.mark.asyncio
async def test_success_path_records_count_latency_masked_entity_and_safe_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry, metrics, records = _telemetry()
    monkeypatch.setattr(gwapp, "forward_buffered", _buffered_echo)
    app = gwapp.create_app(_runtime(telemetry))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        response = await client.post(
            "/responses",
            headers={"X-SecurityMasker-Session-ID": "raw-session-id"},
            json={"model": "m", "input": PERSON},
        )

    assert response.status_code == 200
    snapshot = metrics.snapshot()
    assert snapshot["gateway_requests_total{provider=openai}"] == 1
    assert (
        snapshot["gateway_responses_total{outcome=success,provider=openai}"] == 1
    )
    assert (
        snapshot[
            "gateway_request_duration_count{outcome=success,provider=openai}"
        ]
        == 1
    )
    assert (
        snapshot["gateway_masked_entities_total{entity=PERSON,provider=openai}"]
        == 1
    )
    masked = next(record for record in records if record.event is AuditEvent.REQUEST_MASKED)
    assert masked.entity_count == 1
    assert masked.session_fp is not None
    assert PERSON not in repr(records)
    assert "raw-session-id" not in repr(records)


@pytest.mark.asyncio
async def test_invalid_json_records_fixed_block_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry, metrics, records = _telemetry()
    monkeypatch.setattr(gwapp, "forward_buffered", _buffered_echo)
    app = gwapp.create_app(_runtime(telemetry))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        response = await client.post(
            "/responses",
            content=b"{",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert (
        metrics.snapshot()[
            "gateway_blocks_total{provider=openai,reason=request_format}"
        ]
        == 1
    )
    assert any(
        record.event is AuditEvent.REQUEST_BLOCKED
        and record.reason is BlockReason.REQUEST_FORMAT
        for record in records
    )


class _SlowDetector:
    name = "slow"

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        del context
        await asyncio.sleep(0.05)
        return []


@pytest.mark.asyncio
async def test_detector_timeout_records_dedicated_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry, metrics, records = _telemetry()
    monkeypatch.setattr(gwapp, "forward_buffered", _buffered_echo)
    engine = MaskingEngine([_SlowDetector()], detector_timeout=0.001)
    app = gwapp.create_app(_runtime(telemetry, engine=engine))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        response = await client.post(
            "/responses",
            headers={"X-SecurityMasker-Session-ID": "timeout-session"},
            json={"model": "m", "input": "timeout fixture"},
        )

    assert response.status_code == 400
    snapshot = metrics.snapshot()
    assert snapshot["gateway_detector_timeouts_total{provider=openai}"] == 1
    assert (
        snapshot["gateway_blocks_total{provider=openai,reason=detector_timeout}"]
        == 1
    )
    assert any(
        record.reason is BlockReason.DETECTOR_TIMEOUT for record in records
    )


class _BrokenReadinessStore(InMemorySessionStore):
    async def get_or_create(
        self,
        session_id: str,
        *,
        client_type: str = "unknown",
        lock: Any = None,
    ) -> Any:
        del session_id, client_type, lock
        raise SessionError("synthetic store failure")


@pytest.mark.asyncio
async def test_readiness_store_failure_records_store_metric() -> None:
    telemetry, metrics, records = _telemetry()
    app = gwapp.create_app(
        _runtime(telemetry, store=_BrokenReadinessStore())
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert (
        metrics.snapshot()[
            "gateway_store_errors_total{operation=readiness,provider=admin}"
        ]
        == 1
    )
    assert any(
        record.event is AuditEvent.STORE_ERROR
        and record.reason is StoreOperation.READINESS
        for record in records
    )


class _BrokenRequestStore(InMemorySessionStore):
    @asynccontextmanager
    async def lock(self, session_id: str) -> Any:
        del session_id
        raise SessionError("synthetic SQLite failure")
        yield LockHandle()  # pragma: no cover - async context managerの型を固定する


@pytest.mark.asyncio
async def test_request_store_failure_is_safe_503_and_store_metric() -> None:
    telemetry, metrics, records = _telemetry()
    app = gwapp.create_app(_runtime(telemetry, store=_BrokenRequestStore()))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        response = await client.post(
            "/responses",
            headers={"X-SecurityMasker-Session-ID": "store-session"},
            json={"model": "m", "input": PERSON},
        )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Session store unavailable."
    snapshot = metrics.snapshot()
    assert (
        snapshot["gateway_store_errors_total{operation=request,provider=openai}"]
        == 1
    )
    assert snapshot["gateway_blocks_total{provider=openai,reason=store}"] == 1
    assert "synthetic SQLite failure" not in response.text
    assert any(
        record.event is AuditEvent.STORE_ERROR
        and record.reason is StoreOperation.REQUEST
        for record in records
    )


@pytest.mark.asyncio
async def test_response_started_stream_failure_records_stream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry, metrics, records = _telemetry()

    async def broken_streaming(*args: Any, **kwargs: Any) -> StreamingResponse:
        del args, kwargs

        async def body() -> Any:
            yield b"data: partial\n\n"
            raise RuntimeError("synthetic stream failure")

        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_streaming", broken_streaming)
    app = gwapp.create_app(_runtime(telemetry))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        with pytest.raises(Exception, match="synthetic stream failure"):
            await client.post(
                "/responses",
                headers={"X-SecurityMasker-Session-ID": "stream-session"},
                json={"model": "m", "stream": True, "input": PERSON},
            )

    snapshot = metrics.snapshot()
    assert (
        snapshot["gateway_stream_errors_total{provider=openai,reason=processing}"]
        == 1
    )
    assert (
        snapshot["gateway_responses_total{outcome=server_error,provider=openai}"]
        == 1
    )
    assert any(
        record.event is AuditEvent.STREAM_ERROR
        and record.reason is StreamErrorReason.PROCESSING
        for record in records
    )


class _BrokenBindingStore(InMemorySessionStore):
    async def bind_response(self, response_id: str, session_key: str) -> None:
        del response_id, session_key
        raise SessionError("synthetic response binding failure")


@pytest.mark.asyncio
async def test_stream_binding_failure_has_one_specific_stream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry, metrics, records = _telemetry()

    async def binding_streaming(
        *args: Any, on_complete: Any = None, **kwargs: Any
    ) -> StreamingResponse:
        del args, kwargs

        async def body() -> Any:
            yield b"data: complete\n\n"
            assert on_complete is not None
            await on_complete(SimpleNamespace(response_ids=("response-1",)))

        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_streaming", binding_streaming)
    app = gwapp.create_app(
        _runtime(telemetry, store=_BrokenBindingStore())
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        with pytest.raises(Exception, match="stream response binding failed"):
            await client.post(
                "/responses",
                headers={"X-SecurityMasker-Session-ID": "binding-session"},
                json={"model": "m", "stream": True, "input": PERSON},
            )

    snapshot = metrics.snapshot()
    assert (
        snapshot[
            "gateway_stream_errors_total{provider=openai,reason=response_binding}"
        ]
        == 1
    )
    assert (
        "gateway_stream_errors_total{provider=openai,reason=processing}"
        not in snapshot
    )
    assert (
        snapshot[
            "gateway_store_errors_total{operation=response_binding,provider=openai}"
        ]
        == 1
    )
    assert sum(
        record.event is AuditEvent.STREAM_ERROR for record in records
    ) == 1
