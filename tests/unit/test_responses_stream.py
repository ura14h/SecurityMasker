"""ResponsesStreamProcessor tests (§20, §21): delta/done/completed + tool args."""

from __future__ import annotations

import contextlib
import json

from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor
from securitymasker.tool_trust import ToolTrustPolicy

REPL = {"SM_ORG_7F3A91": "株式会社極秘技研", "SM_HOST": "prod-db01.internal.example"}


def restore(text: str) -> str:
    for alias, original in REPL.items():
        text = text.replace(alias, original)
    return text


def _run(sse: str, chunk: int | None = None, trusted_tools: tuple[str, ...] = ()) -> list[dict]:
    proc = ResponsesStreamProcessor(REPL, restore, ToolTrustPolicy(frozenset(trusted_tools)))
    raw = sse.encode("utf-8")
    out = bytearray()
    if chunk is None:
        out += proc.feed(raw)
    else:
        for i in range(0, len(raw), chunk):
            out += proc.feed(raw[i : i + chunk])
    out += proc.flush()
    events = []
    for block in out.decode("utf-8").split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line[6:]))
    return events


def _ev(payload: dict) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _text(events: list[dict]) -> str:
    return "".join(
        e["delta"] for e in events
        if e.get("type") == "response.output_text.delta"
    )


def test_text_delta_restored_across_split_deltas() -> None:
    full = "接続先 SM_HOST 会社 SM_ORG_7F3A91"
    deltas = "".join(
        _ev({"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
             "delta": full[i : i + 3]})
        for i in range(0, len(full), 3)
    )
    sse = deltas + _ev({"type": "response.output_text.done", "output_index": 0,
                        "content_index": 0, "text": full})
    events = _run(sse, chunk=5)
    assert _text(events) == "接続先 prod-db01.internal.example 会社 株式会社極秘技研"
    done = [e for e in events if e.get("type") == "response.output_text.done"][0]
    assert done["text"] == "接続先 prod-db01.internal.example 会社 株式会社極秘技研"


def test_completed_event_response_restored() -> None:
    resp = {"id": "r", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "会社 SM_ORG_7F3A91"}]}]}
    sse = _ev({"type": "response.completed", "response": resp})
    events = _run(sse)
    completed = [e for e in events if e.get("type") == "response.completed"][0]
    assert completed["response"]["output"][0]["content"][0]["text"] == "会社 株式会社極秘技研"


def _fc_stream(full: str) -> str:
    # output_item.added registers the tool name for fc_1, then buffered arg deltas.
    added = _ev({"type": "response.output_item.added", "output_index": 0,
                 "item": {"id": "fc_1", "type": "function_call", "name": "connect_db",
                          "arguments": ""}})
    deltas = "".join(
        _ev({"type": "response.function_call_arguments.delta", "item_id": "fc_1",
             "output_index": 0, "delta": full[i : i + 4]})
        for i in range(0, len(full), 4)
    )
    done = _ev({"type": "response.function_call_arguments.done", "item_id": "fc_1",
                "output_index": 0, "arguments": full})
    return added + deltas + done


def test_function_call_arguments_restored_for_trusted_tool() -> None:
    events = _run(_fc_stream('{"host": "SM_HOST"}'), trusted_tools=("connect_db",))
    # Deltas are suppressed; exactly one restored delta is re-emitted, plus done.
    arg_deltas = [e for e in events if e.get("type") == "response.function_call_arguments.delta"]
    assert len(arg_deltas) == 1
    assert json.loads(arg_deltas[0]["delta"]) == {"host": "prod-db01.internal.example"}
    done = [e for e in events if e.get("type") == "response.function_call_arguments.done"][0]
    assert json.loads(done["arguments"]) == {"host": "prod-db01.internal.example"}


def test_function_call_arguments_not_restored_for_untrusted_tool() -> None:
    # Default: connect_db is not on the trusted allowlist -> args keep aliases.
    events = _run(_fc_stream('{"host": "SM_HOST"}'))
    done = [e for e in events if e.get("type") == "response.function_call_arguments.done"][0]
    assert json.loads(done["arguments"]) == {"host": "SM_HOST"}  # not restored


def test_unknown_event_passthrough() -> None:
    events = _run(_ev({"type": "response.in_progress", "sequence_number": 1}))
    assert events == [{"type": "response.in_progress", "sequence_number": 1}]
