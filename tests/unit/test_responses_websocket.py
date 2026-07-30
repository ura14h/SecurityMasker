"""Responses WebSocketのmask・復元・session境界を検証する。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.engine import MaskingEngine
from securitymasker.gateway import app as gateway_app
from securitymasker.gateway import websocket as gateway_ws
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor
from securitymasker.tool_trust import ToolTrustPolicy

PERSON = "山田太郎"
PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def _runtime() -> GatewayRuntime:
    engine = MaskingEngine(
        [
            DictionaryDetector(
                [
                    DictionaryEntry(
                        EntityType.PERSON.value,
                        (PERSON,),
                        PROSE,
                        LITERAL,
                    )
                ]
            )
        ],
        registered_literals=(PERSON,),
    )
    return GatewayRuntime(
        engine,
        InMemorySessionStore(),
        openai_upstream="http://127.0.0.1:48001",
        anthropic_upstream="http://127.0.0.1:48002",
        product_mode="chatgpt",
    )


class FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._events: asyncio.Queue[str] = asyncio.Queue()
        self._response_number = 0

    async def send(self, message: str) -> None:
        event = json.loads(message)
        self.sent.append(event)
        if event.get("type") != "response.create":
            return
        self._response_number += 1
        response_id = f"resp-ws-{self._response_number}"
        text = event.get("input", "")
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        response = {
            "id": response_id,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        }
        common = {"output_index": 0, "content_index": 0}
        events = [
            {"type": "response.created", "response": {"id": response_id, "output": []}},
            *[
                {"type": "response.output_text.delta", **common, "delta": text[index : index + 3]}
                for index in range(0, len(text), 3)
            ],
            {"type": "response.output_text.done", **common, "text": text},
            {"type": "response.completed", "response": response},
        ]
        for output in events:
            await self._events.put(json.dumps(output, ensure_ascii=False))

    async def recv(self, decode: bool | None = None) -> str | bytes:
        return await self._events.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


def _patch_upstream(
    monkeypatch: pytest.MonkeyPatch, upstreams: list[FakeUpstream]
) -> None:
    @asynccontextmanager
    async def fake_open(url: str, headers: dict[str, str], user_agent: str | None):
        upstream = FakeUpstream()
        upstreams.append(upstream)
        yield upstream

    monkeypatch.setattr(gateway_ws, "open_upstream", fake_open)


def _read_response(websocket: Any) -> tuple[str, str]:
    deltas: list[str] = []
    response_id = ""
    while True:
        event = websocket.receive_json()
        if event["type"] == "response.output_text.delta":
            deltas.append(event["delta"])
        if event["type"] == "response.created":
            response_id = event["response"]["id"]
        if event["type"] == "response.completed":
            return "".join(deltas), response_id


def test_websocket_masks_request_restores_response_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams: list[FakeUpstream] = []
    _patch_upstream(monkeypatch, upstreams)

    with (
        TestClient(gateway_app.create_app(_runtime())) as client,
        client.websocket_connect(
            "/responses", headers={"x-securitymasker-session-id": "ws-session"}
        ) as websocket,
    ):
        websocket.send_json(
            {"type": "response.create", "model": "m", "input": f"担当は{PERSON}"}
        )
        restored, response_id = _read_response(websocket)
        assert PERSON in restored

        websocket.send_json(
            {
                "type": "response.create",
                "model": "m",
                "previous_response_id": response_id,
                "input": f"再び{PERSON}",
            }
        )
        restored_second, _ = _read_response(websocket)
        assert PERSON in restored_second

    assert len(upstreams) == 1
    assert len(upstreams[0].sent) == 2
    outbound = json.dumps(upstreams[0].sent, ensure_ascii=False)
    assert PERSON not in outbound
    assert "SM_PERSON_" in outbound


def test_websocket_rejects_transport_fields_before_upstream_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams: list[FakeUpstream] = []
    _patch_upstream(monkeypatch, upstreams)

    with (
        TestClient(gateway_app.create_app(_runtime())) as client,
        client.websocket_connect("/v1/responses") as websocket,
    ):
        websocket.send_json(
            {
                "type": "response.create",
                "model": "m",
                "stream": True,
                "input": PERSON,
            }
        )
        event = websocket.receive_json()

    assert event["type"] == "error"
    assert event["error"]["code"] == "invalid_websocket_field"
    assert upstreams[0].sent == []
    assert PERSON not in json.dumps(event, ensure_ascii=False)


def test_unknown_clean_event_passes_but_registered_value_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams: list[FakeUpstream] = []
    _patch_upstream(monkeypatch, upstreams)

    with (
        TestClient(gateway_app.create_app(_runtime())) as client,
        client.websocket_connect("/responses") as websocket,
    ):
        clean = {"type": "future.clean", "sequence_number": 7}
        websocket.send_json(clean)
        websocket.send_json({"type": "future.secret", "value": PERSON})
        event = websocket.receive_json()

    assert upstreams[0].sent == [clean]
    assert event["type"] == "error"
    assert event["error"]["code"] == "securitymasker_blocked"
    assert PERSON not in json.dumps(event, ensure_ascii=False)


def test_previous_response_from_another_connection_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams: list[FakeUpstream] = []
    _patch_upstream(monkeypatch, upstreams)
    app = gateway_app.create_app(_runtime())

    with TestClient(app) as client:
        with client.websocket_connect(
            "/responses", headers={"x-securitymasker-session-id": "session-a"}
        ) as first:
            first.send_json(
                {"type": "response.create", "model": "m", "input": PERSON}
            )
            _, response_id = _read_response(first)

        with client.websocket_connect(
            "/responses", headers={"x-securitymasker-session-id": "session-b"}
        ) as second:
            second.send_json(
                {
                    "type": "response.create",
                    "model": "m",
                    "previous_response_id": response_id,
                    "input": "clean",
                }
            )
            event = second.receive_json()

    assert event["type"] == "error"
    assert event["error"]["code"] == "session_unresolved"
    assert upstreams[1].sent == []


def test_binary_message_is_not_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams: list[FakeUpstream] = []
    _patch_upstream(monkeypatch, upstreams)

    with (
        TestClient(gateway_app.create_app(_runtime())) as client,
        client.websocket_connect("/responses") as websocket,
    ):
        websocket.send_bytes(PERSON.encode())
        event = websocket.receive_json()

    assert event["error"]["code"] == "binary_message_not_supported"
    assert upstreams[0].sent == []


@pytest.mark.parametrize("cut", range(1, len("SM_PERSON_7F3A91")))
def test_websocket_event_processor_restores_every_alias_split(cut: int) -> None:
    alias = "SM_PERSON_7F3A91"
    processor = ResponsesStreamProcessor(
        {alias: PERSON},
        lambda text: text.replace(alias, PERSON),
    )
    events: list[dict[str, Any]] = []
    for delta in (alias[:cut], alias[cut:]):
        events.extend(
            processor.process_event(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": delta,
                }
            )
        )
    events.extend(
        processor.process_event(
            {
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": alias,
            }
        )
    )

    restored = "".join(
        event["delta"]
        for event in events
        if event.get("type") == "response.output_text.delta"
    )
    assert restored == PERSON


def test_websocket_event_processor_restores_trusted_tool_arguments() -> None:
    alias = "SM_PERSON_7F3A91"
    processor = ResponsesStreamProcessor(
        {alias: PERSON},
        lambda text: text.replace(alias, PERSON),
        ToolTrustPolicy(frozenset({"local_tool"})),
    )
    processor.process_event(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "fc-1",
                "type": "function_call",
                "name": "local_tool",
                "arguments": "",
            },
        }
    )
    raw = json.dumps({"person": alias})
    for chunk in (raw[:5], raw[5:11], raw[11:]):
        assert processor.process_event(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc-1",
                "output_index": 0,
                "delta": chunk,
            }
        ) == []
    events = processor.process_event(
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc-1",
            "output_index": 0,
            "arguments": raw,
        }
    )

    done = next(
        event
        for event in events
        if event["type"] == "response.function_call_arguments.done"
    )
    assert json.loads(done["arguments"]) == {"person": PERSON}
