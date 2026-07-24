"""Tool-argument reassembly tests (§30.3: split deltas, special chars, fail-closed)."""

from __future__ import annotations

import json

import pytest

from securitymasker.errors import RestoreError
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler

# Restore map: alias -> original (originals contain JSON-hostile characters).
RESTORE = {
    "SM_HOST": "prod-db01.internal.example",
    "SM_PERSON": "山田太郎",
    "SM_QUOTE": 'he said "hi"\nand left\tnow\\done',
}


def restore(text: str) -> str:
    for alias, original in RESTORE.items():
        text = text.replace(alias, original)
    return text


def test_reassemble_across_deltas() -> None:
    r = ToolArgumentReassembler(restore)
    full = '{"hostname": "SM_HOST", "user": "SM_PERSON"}'
    for i in range(0, len(full), 5):
        r.add_delta("call1", full[i : i + 5])
    out = r.complete("call1")
    obj = json.loads(out)  # must be valid JSON
    assert obj == {"hostname": "prod-db01.internal.example", "user": "山田太郎"}


def test_special_characters_do_not_break_json() -> None:
    r = ToolArgumentReassembler(restore)
    out = r.restore_arguments('{"msg": "SM_QUOTE"}')
    obj = json.loads(out)  # re-serialization keeps it valid despite " \\ \n \t
    assert obj["msg"] == 'he said "hi"\nand left\tnow\\done'


def test_nested_arrays_and_objects() -> None:
    r = ToolArgumentReassembler(restore)
    src = '{"a": ["SM_PERSON", {"b": "SM_HOST"}], "n": 5, "ok": true}'
    obj = json.loads(r.restore_arguments(src))
    assert obj["a"][0] == "山田太郎"
    assert obj["a"][1]["b"] == "prod-db01.internal.example"
    assert obj["n"] == 5 and obj["ok"] is True


def test_keys_are_never_transformed() -> None:
    r = ToolArgumentReassembler(lambda s: s.replace("SM_HOST", "X"))
    out = r.restore_arguments('{"SM_HOST": "SM_HOST"}')
    obj = json.loads(out)
    assert list(obj.keys()) == ["SM_HOST"]  # key unchanged
    assert obj["SM_HOST"] == "X"  # value restored


def test_multiple_concurrent_tool_calls() -> None:
    r = ToolArgumentReassembler(restore)
    r.add_delta("a", '{"h":"SM_HOST"}')
    r.add_delta("b", '{"p":"SM_PERSON"}')
    assert json.loads(r.complete("a"))["h"] == "prod-db01.internal.example"
    assert json.loads(r.complete("b"))["p"] == "山田太郎"


def test_incomplete_json_fails_closed() -> None:
    r = ToolArgumentReassembler(restore)
    r.add_delta("c", '{"h": "SM_HOST"')  # missing closing brace
    with pytest.raises(RestoreError):
        r.complete("c")


def test_empty_arguments_passthrough() -> None:
    r = ToolArgumentReassembler(restore)
    assert r.restore_arguments("") == ""


def test_missing_tool_call_raises() -> None:
    r = ToolArgumentReassembler(restore)
    with pytest.raises(RestoreError):
        r.complete("nope")
