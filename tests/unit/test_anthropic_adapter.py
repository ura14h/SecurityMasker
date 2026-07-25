"""Anthropic Messages adapter tests (§16, §23: mask values, keep structure)."""

from __future__ import annotations

import pytest

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.engine import MaskingEngine
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.protocols import anthropic_messages as adapter
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.tool_trust import ToolTrustPolicy

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def build_engine(trusted_tools: tuple[str, ...] = ()) -> MaskingEngine:
    return MaskingEngine([
        DictionaryDetector([DictionaryEntry(EntityType.PERSON.value, ("山田太郎",), PROSE, LITERAL)]),
        RegexDetector(
            [RegexEntry(r"prod-db01\.internal\.example", EntityType.HOSTNAME.value,
                        ReplacementProfile.HOSTNAME.value, LITERAL, 150)],
            name="host",
        ),
    ], tool_trust=ToolTrustPolicy(frozenset(trusted_tools)))


async def _session():
    return await InMemorySessionStore().get_or_create("s")


@pytest.mark.asyncio
async def test_mask_system_string_and_messages() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "model": "claude", "max_tokens": 64,
        "system": "あなたは山田太郎の助手です",
        "messages": [{"role": "user", "content": "接続先 prod-db01.internal.example"}],
    }
    await adapter.mask_request(eng, s, data)
    assert "山田太郎" not in data["system"]
    assert "prod-db01.internal.example" not in data["messages"][0]["content"]
    assert data["model"] == "claude"  # structural


@pytest.mark.asyncio
async def test_mask_content_blocks_and_tool_use_input() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "max_tokens": 10,
        "messages": [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "担当 山田太郎"},
                {"type": "tool_use", "id": "toolu_1", "name": "connect_db",
                 "input": {"host": "prod-db01.internal.example", "note": "ok"}},
            ],
        }],
    }
    await adapter.mask_request(eng, s, data)
    blocks = data["messages"][0]["content"]
    assert "山田太郎" not in blocks[0]["text"]
    assert blocks[1]["id"] == "toolu_1" and blocks[1]["name"] == "connect_db"  # structural
    assert "prod-db01.internal.example" not in blocks[1]["input"]["host"]
    assert blocks[1]["input"]["note"] == "ok"  # non-secret value passes through


@pytest.mark.asyncio
async def test_mask_tool_result_content() -> None:
    eng, s = build_engine(), await _session()
    data = {"max_tokens": 10, "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1",
         "content": [{"type": "text", "text": "結果: prod-db01.internal.example"}]},
    ]}]}
    await adapter.mask_request(eng, s, data)
    block = data["messages"][0]["content"][0]
    assert block["tool_use_id"] == "toolu_1"  # structural
    assert "prod-db01.internal.example" not in block["content"][0]["text"]


@pytest.mark.asyncio
async def test_mask_tool_description_not_schema() -> None:
    eng, s = build_engine(), await _session()
    data = {"max_tokens": 10, "messages": [],
            "tools": [{"name": "connect_db", "description": "接続 prod-db01.internal.example",
                       "input_schema": {"type": "object", "properties": {"山田太郎_k": {"type": "string"}}}}]}
    await adapter.mask_request(eng, s, data)
    tool = data["tools"][0]
    assert tool["name"] == "connect_db"
    assert "prod-db01.internal.example" not in tool["description"]
    assert "山田太郎_k" in tool["input_schema"]["properties"]  # schema keys untouched


@pytest.mark.asyncio
async def test_unknown_block_passes_through() -> None:
    eng, s = build_engine(), await _session()
    data = {"max_tokens": 10, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "data": "山田太郎ish-but-binary"}},
        {"type": "text", "text": "山田太郎"},
    ]}]}
    await adapter.mask_request(eng, s, data)
    blocks = data["messages"][0]["content"]
    assert blocks[0]["type"] == "image"  # unknown block untouched
    assert blocks[0]["source"]["data"] == "山田太郎ish-but-binary"
    assert "山田太郎" not in blocks[1]["text"]


@pytest.mark.asyncio
async def test_mask_then_restore_roundtrip() -> None:
    eng, s = build_engine(), await _session()
    req = {"max_tokens": 10, "messages": [{"role": "user", "content": "山田太郎 と prod-db01.internal.example"}]}
    await adapter.mask_request(eng, s, req)
    masked = req["messages"][0]["content"]
    resp = {
        "id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": masked}],
    }
    adapter.restore_response(eng, s, resp)
    assert "山田太郎" in resp["content"][0]["text"]
    assert "prod-db01.internal.example" in resp["content"][0]["text"]
    assert resp["id"] == "msg_1" and resp["stop_reason"] == "end_turn"  # structural


@pytest.mark.asyncio
async def test_restore_tool_use_input_values_trusted() -> None:
    eng, s = build_engine(trusted_tools=("db",)), await _session()
    await adapter.mask_request(eng, s, {"max_tokens": 10, "messages": [
        {"role": "user", "content": "山田太郎 prod-db01.internal.example"}]})
    host_alias = next(a for a, o in eng.literal_restorations(s).items() if o == "prod-db01.internal.example")
    resp = {"content": [{"type": "tool_use", "id": "t1", "name": "db",
                         "input": {"host": host_alias, "n": 1}}]}
    adapter.restore_response(eng, s, resp)
    assert resp["content"][0]["input"]["host"] == "prod-db01.internal.example"
    assert resp["content"][0]["input"]["n"] == 1


@pytest.mark.asyncio
async def test_restore_tool_use_input_untrusted_keeps_aliases() -> None:
    # Default: the tool is not trusted -> input keeps its aliases (doc/06 P0-8).
    eng, s = build_engine(), await _session()
    await adapter.mask_request(eng, s, {"max_tokens": 10, "messages": [
        {"role": "user", "content": "prod-db01.internal.example"}]})
    host_alias = next(a for a, o in eng.literal_restorations(s).items() if o == "prod-db01.internal.example")
    resp = {"content": [{"type": "tool_use", "id": "t1", "name": "external",
                         "input": {"host": host_alias}}]}
    adapter.restore_response(eng, s, resp)
    assert resp["content"][0]["input"]["host"] == host_alias           # not restored
    assert resp["content"][0]["input"]["host"] != "prod-db01.internal.example"
