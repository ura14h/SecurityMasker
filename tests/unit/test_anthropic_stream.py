"""AnthropicStreamProcessor tests (§20, §21, §23: text/json deltas, passthrough)."""

from __future__ import annotations

import contextlib
import json

from securitymasker.streaming.anthropic_stream import AnthropicStreamProcessor

REPL = {"SM_PERSON_2B891C": "山田太郎", "sm-host-7f3a91.example.invalid": "prod-db01.internal.example"}


def restore(text: str) -> str:
    for alias, original in REPL.items():
        text = text.replace(alias, original)
    return text


def _run(events_sse: str, chunk_size: int | None = None) -> list[dict]:
    proc = AnthropicStreamProcessor(REPL, restore)
    raw = events_sse.encode("utf-8")
    out = bytearray()
    if chunk_size is None:
        out += proc.feed(raw)
    else:
        for i in range(0, len(raw), chunk_size):
            out += proc.feed(raw[i : i + chunk_size])
    out += proc.flush()
    events = []
    for block in out.decode("utf-8").split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line[6:]))
    return events


def _text_stream(text: str, size: int = 4) -> str:
    deltas = "".join(
        "event: content_block_delta\n"
        + f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[i:i + size]}})}\n\n"
        for i in range(0, len(text), size)
    )
    return (
        'event: content_block_start\n'
        f'data: {json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})}\n\n'
        + deltas
        + 'event: content_block_stop\n'
        f'data: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n'
    )


def _assemble_text(events: list[dict]) -> str:
    return "".join(
        e["delta"]["text"]
        for e in events
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta"
    )


def test_text_delta_restored_across_split_deltas() -> None:
    sse = _text_stream("担当 SM_PERSON_2B891C at sm-host-7f3a91.example.invalid")
    events = _run(sse)
    assert _assemble_text(events) == "担当 山田太郎 at prod-db01.internal.example"


def test_text_delta_restored_with_tiny_byte_chunks() -> None:
    sse = _text_stream("x SM_PERSON_2B891C y")
    events = _run(sse, chunk_size=1)  # split at every byte, incl. multibyte UTF-8
    assert _assemble_text(events) == "x 山田太郎 y"


def test_input_json_delta_buffered_and_restored() -> None:
    full = '{"host": "sm-host-7f3a91.example.invalid", "user": "SM_PERSON_2B891C"}'
    deltas = "".join(
        "event: content_block_delta\n"
        + f"data: {json.dumps({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': full[i:i + 5]}})}\n\n"
        for i in range(0, len(full), 5)
    )
    sse = (
        'event: content_block_start\n'
        f'data: {json.dumps({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "db", "input": {}}})}\n\n'
        + deltas
        + 'event: content_block_stop\n'
        f'data: {json.dumps({"type": "content_block_stop", "index": 1})}\n\n'
    )
    events = _run(sse)
    # Exactly one input_json_delta is emitted (buffered), and it is valid restored JSON.
    json_deltas = [
        e for e in events
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
    ]
    assert len(json_deltas) == 1
    obj = json.loads(json_deltas[0]["delta"]["partial_json"])
    assert obj == {"host": "prod-db01.internal.example", "user": "山田太郎"}


def test_message_events_pass_through_in_order() -> None:
    sse = (
        'event: message_start\ndata: {"type": "message_start", "message": {"id": "m"}}\n\n'
        + _text_stream("SM_PERSON_2B891C")
        + 'event: message_stop\ndata: {"type": "message_stop"}\n\n'
    )
    events = _run(sse)
    types = [e["type"] for e in events]
    assert types[0] == "message_start" and types[-1] == "message_stop"
    assert _assemble_text(events) == "山田太郎"


def test_unknown_event_passthrough() -> None:
    sse = 'event: ping\ndata: {"type": "ping"}\n\n'
    events = _run(sse)
    assert events == [{"type": "ping"}]


def test_invalid_tool_json_left_unrestored_not_crash() -> None:
    # Missing closing brace: fail-closed means leave aliases in place, never crash.
    bad = '{"host": "SM_PERSON_2B891C"'
    sse = (
        'event: content_block_start\n'
        f'data: {json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "d", "input": {}}})}\n\n'
        'event: content_block_delta\n'
        f'data: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": bad}})}\n\n'
        'event: content_block_stop\n'
        f'data: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n'
    )
    events = _run(sse)
    json_deltas = [
        e for e in events
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
    ]
    assert len(json_deltas) == 1
    assert json_deltas[0]["delta"]["partial_json"] == bad  # unchanged, no crash
