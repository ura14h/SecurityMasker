"""Milestone E limit tests (doc/06 P1-5, P1-10): bounded resources, fail-closed.

Session mapping tables and streaming tool-argument buffers are bounded so a
runaway or hostile stream cannot exhaust memory, and overflow never produces a
partial literal restore.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from securitymasker.aliases import factory
from securitymasker.errors import MaskingError
from securitymasker.gateway import responses_stream as rs
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.tool_trust import ToolTrustPolicy


@pytest.mark.asyncio
async def test_session_mapping_cap_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(factory, "MAX_MAPPINGS_PER_SESSION", 3)
    session = await InMemorySessionStore().get_or_create("s")

    def alloc(value: str):
        return factory.get_or_create_alias(
            session, original_value=value, fingerprint_value=value,
            entity_type="PERSON", replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
            restore_policy=RestorePolicy.LITERAL.value)

    for i in range(3):
        alloc(f"secret-{i}")
    with pytest.raises(MaskingError):
        alloc("secret-overflow")  # 4th distinct secret -> fail closed


def _ev(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _run(sse: str, *, trusted=("connect_db",)) -> list[dict]:
    proc = rs.ResponsesStreamProcessor({}, lambda t: t, ToolTrustPolicy(frozenset(trusted)))
    out = proc.feed(sse.encode()) + proc.flush()
    events = []
    for line in out.decode().splitlines():
        if line.startswith("data: "):
            with contextlib.suppress(json.JSONDecodeError):
                events.append(json.loads(line[6:]))
    return events


def test_arg_buffer_overflow_emits_raw_not_partial(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_MAX_ARG_BUFFER_BYTES", 50)
    added = _ev({"type": "response.output_item.added", "output_index": 0,
                 "item": {"id": "fc_1", "type": "function_call", "name": "connect_db"}})
    big = '{"x": "' + "A" * 200 + '"}'
    deltas = "".join(
        _ev({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": big[i:i + 20]})
        for i in range(0, len(big), 20))
    done = _ev({"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": big})
    events = _run(added + deltas + done)
    done_ev = [e for e in events if e.get("type") == "response.function_call_arguments.done"][0]
    # Overflow -> raw buffered content, never a corrupted partial restore.
    assert done_ev["arguments"] == big


def test_missing_done_drops_tool_buffer(monkeypatch) -> None:
    # Args stream that never gets a `.done`: buffered content must not be emitted
    # as an executable (or partially restored) tool call.
    added = _ev({"type": "response.output_item.added", "output_index": 0,
                 "item": {"id": "fc_1", "type": "function_call", "name": "connect_db"}})
    deltas = _ev({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                  "delta": '{"host": "x"}'})
    events = _run(added + deltas)
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.done"]
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.delta"]
