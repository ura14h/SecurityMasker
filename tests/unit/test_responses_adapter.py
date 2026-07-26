"""OpenAI Responses adapterが値だけを変換し、構造を維持することのテスト。"""

from __future__ import annotations

import pytest

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.engine import MaskingEngine
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.protocols import openai_responses as adapter
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.tool_trust import ToolTrustPolicy

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def build_engine(trusted_tools: tuple[str, ...] = ()) -> MaskingEngine:
    detectors = [
        DictionaryDetector([DictionaryEntry(EntityType.PERSON.value, ("山田太郎",), PROSE, LITERAL)]),
        RegexDetector(
            [RegexEntry(r"prod-db01\.internal\.example", EntityType.HOSTNAME.value,
                        ReplacementProfile.HOSTNAME.value, LITERAL, 150)],
            name="host",
        ),
    ]
    return MaskingEngine(detectors, tool_trust=ToolTrustPolicy(frozenset(trusted_tools)))


async def _session():
    return await InMemorySessionStore().get_or_create("s")


@pytest.mark.asyncio
async def test_mask_responses_input_string() -> None:
    eng, s = build_engine(), await _session()
    data = {"model": "gpt-4o", "input": "担当は山田太郎です", "previous_response_id": "resp_123"}
    await adapter.mask_request(eng, s, data)
    assert "山田太郎" not in data["input"]
    assert data["model"] == "gpt-4o"  # structural field untouched
    assert data["previous_response_id"] == "resp_123"  # 構造fieldはマスクしない


@pytest.mark.asyncio
async def test_mask_responses_input_items_only_text() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "山田太郎に接続 prod-db01.internal.example"}]},
        ]
    }
    await adapter.mask_request(eng, s, data)
    part = data["input"][0]["content"][0]
    assert part["type"] == "input_text"  # structural
    assert data["input"][0]["role"] == "user"  # structural
    assert "山田太郎" not in part["text"]
    assert "prod-db01.internal.example" not in part["text"]


@pytest.mark.asyncio
async def test_mask_tool_description_without_changing_schema() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "input": "担当: 山田太郎",
        "tools": [{"type": "function",
                   "function": {"name": "connect_db", "description": "接続先 prod-db01.internal.example",
                                "parameters": {"type": "object", "properties": {"山田太郎_key": {"type": "string"}}}}}],
    }
    await adapter.mask_request(eng, s, data)
    assert "山田太郎" not in data["input"]
    fn = data["tools"][0]["function"]
    assert fn["name"] == "connect_db"  # tool nameは構造fieldなので変更しない
    assert "prod-db01.internal.example" not in fn["description"]
    # 機密値に見える文字列でもJSON Schemaのkeyは変更しない。
    assert "山田太郎_key" in fn["parameters"]["properties"]


@pytest.mark.asyncio
async def test_mask_then_restore_response_roundtrip() -> None:
    eng, s = build_engine(), await _session()
    req = {"input": "山田太郎 と prod-db01.internal.example"}
    await adapter.mask_request(eng, s, req)
    masked_input = req["input"]
    assert "山田太郎" not in masked_input

    # Simulate the model echoing the aliases back in a Responses output.
    resp = {
        "id": "resp_abc", "object": "response", "status": "completed",
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": masked_input}]}],
    }
    adapter.restore_response(eng, s, resp)
    text = resp["output"][0]["content"][0]["text"]
    assert "山田太郎" in text and "prod-db01.internal.example" in text
    assert resp["id"] == "resp_abc" and resp["status"] == "completed"  # structural intact


@pytest.mark.asyncio
async def test_restore_responses_tool_call_arguments_trusted() -> None:
    # allowlist済みlocal toolの引数は元の値へ復元する。
    eng, s = build_engine(trusted_tools=("connect_db",)), await _session()
    await adapter.mask_request(eng, s, {"input": "山田太郎 prod-db01.internal.example"})
    person_alias = next(a for a, o in eng.literal_restorations(s).items() if o == "山田太郎")
    host_alias = next(a for a, o in eng.literal_restorations(s).items() if o == "prod-db01.internal.example")
    resp = {
        "output": [{
            "id": "call_1",
            "type": "function_call",
            "name": "connect_db",
            "arguments": f'{{"host": "{host_alias}", "user": "{person_alias}"}}',
        }],
    }
    adapter.restore_response(eng, s, resp)
    import json
    args = json.loads(resp["output"][0]["arguments"])
    assert args == {"host": "prod-db01.internal.example", "user": "山田太郎"}
    assert resp["output"][0]["name"] == "connect_db"


@pytest.mark.asyncio
async def test_untrusted_tool_call_arguments_not_restored() -> None:
    # 信頼指定のないtool引数はaliasのまま保持する。
    eng, s = build_engine(), await _session()
    await adapter.mask_request(eng, s, {"input": "山田太郎 prod-db01.internal.example"})
    host_alias = next(a for a, o in eng.literal_restorations(s).items() if o == "prod-db01.internal.example")
    resp = {
        "output": [{
            "id": "call_1",
            "type": "function_call",
            "name": "external_mcp_tool",
            "arguments": f'{{"host": "{host_alias}"}}',
        }],
    }
    adapter.restore_response(eng, s, resp)
    args = resp["output"][0]["arguments"]
    assert host_alias in args                          # alias preserved
    assert "prod-db01.internal.example" not in args    # real value NOT leaked
