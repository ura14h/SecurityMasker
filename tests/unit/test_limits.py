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


def _proc(trusted=("connect_db",)) -> rs.ResponsesStreamProcessor:
    return rs.ResponsesStreamProcessor({}, lambda t: t, ToolTrustPolicy(frozenset(trusted)))


def test_arg_buffer_cap_actually_bounds_memory(monkeypatch) -> None:
    # Re-audit finding 6: the cap only set a flag while the buffer kept growing,
    # so 100 chars x 100 deltas retained 10,000 chars under a 50-char cap.
    monkeypatch.setattr(rs, "_MAX_ARG_BUFFER_BYTES", 50)
    proc = _proc()
    proc.feed(_ev({"type": "response.output_item.added", "output_index": 0,
                   "item": {"id": "fc_1", "type": "function_call", "name": "connect_db"}}).encode())
    for _ in range(100):
        proc.feed(_ev({"type": "response.function_call_arguments.delta",
                       "item_id": "fc_1", "delta": "A" * 100}).encode())
    retained = sum(len(p) for parts in proc._arg_buffers.values() for p in parts)
    assert retained <= 50, f"buffer kept growing past the cap: {retained} chars"


def test_arg_buffer_overflow_fails_closed_with_error_event(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_MAX_ARG_BUFFER_BYTES", 50)
    added = _ev({"type": "response.output_item.added", "output_index": 0,
                 "item": {"id": "fc_1", "type": "function_call", "name": "connect_db"}})
    big = '{"x": "' + "A" * 200 + '"}'
    deltas = "".join(
        _ev({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": big[i:i + 20]})
        for i in range(0, len(big), 20))
    done = _ev({"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": big})
    events = _run(added + deltas + done)
    # No tool-call payload is emitted, and the client is told why (never silent).
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.done"]
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "buffer limit" in errors[0]["error"]["message"]


def test_missing_done_reports_incomplete_tool_call() -> None:
    # A stream that ends before `.done`: the incomplete JSON must never be emitted
    # as an executable call, but the client must be told rather than silently
    # losing the call (doc/06 P1-10).
    added = _ev({"type": "response.output_item.added", "output_index": 0,
                 "item": {"id": "fc_1", "type": "function_call", "name": "connect_db"}})
    deltas = _ev({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                  "delta": '{"host": "x"}'})
    events = _run(added + deltas)
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.done"]
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.delta"]
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "incomplete" in errors[0]["error"]["message"]
