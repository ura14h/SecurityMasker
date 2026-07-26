"""Responses streamのdelta・完了event・tool引数復元を検証する。"""

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


# --- malformed tool arguments are refused, never passed through --------------------
#
# Restoring aliases inside function-call arguments means editing a JSON string. If
# what arrives is not valid JSON we cannot edit it safely, and forwarding it raw
# would hand the client a broken tool call built from model-controlled text. Both
# trust levels fail closed on validity; they differ only in whether a VALID payload
# gets its aliases restored (trusted) or kept as aliases (untrusted).


def _tool_stream(name: str, arguments: str) -> str:
    added = ("data: " + json.dumps({
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": "fc_1", "type": "function_call", "name": name}}) + "\n\n")
    done = ("data: " + json.dumps({
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1", "arguments": arguments}) + "\n\n")
    return added + done


def _events(out: str) -> list[dict]:
    return [json.loads(line[6:]) for line in out.splitlines() if line.startswith("data: ")]


def test_invalid_tool_json_becomes_an_error_event() -> None:
    proc = ResponsesStreamProcessor({"SM_X": "real"}, lambda t: t,
                                    ToolTrustPolicy(frozenset({"tool_a"})))
    broken = '{"host": "SM_X", '  # truncated => not valid JSON
    out = (proc.feed(_tool_stream("tool_a", broken).encode()) + proc.flush()).decode()

    events = _events(out)
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.done"]
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "not valid JSON" in errors[0]["error"]["message"]
    assert broken not in out  # the malformed JSON is not re-sent


def test_untrusted_tool_invalid_json_is_not_resent() -> None:
    # No trusted tools at all: validity must still be enforced.
    proc = ResponsesStreamProcessor({}, lambda t: t, ToolTrustPolicy(frozenset()))
    broken = '{"host": "SM_X", '
    out = (proc.feed(_tool_stream("untrusted_tool", broken).encode()) + proc.flush()).decode()

    assert broken not in out, "malformed JSON was re-sent for an untrusted tool"
    errors = [e for e in _events(out) if e.get("type") == "error"]
    assert len(errors) == 1 and "not valid JSON" in errors[0]["error"]["message"]


def test_untrusted_tool_valid_json_keeps_aliases() -> None:
    proc = ResponsesStreamProcessor({"SM_X": "real-value"},
                                    lambda t: t.replace("SM_X", "real-value"),
                                    ToolTrustPolicy(frozenset()))
    out = (proc.feed(_tool_stream("untrusted_tool", '{"host": "SM_X"}').encode())
           + proc.flush()).decode()
    assert "SM_X" in out                 # alias preserved for the untrusted tool
    assert "real-value" not in out       # real value never handed over
