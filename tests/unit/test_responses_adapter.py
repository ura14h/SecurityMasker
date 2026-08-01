"""OpenAI Responses adapterが値だけを変換し、構造を維持することのテスト。"""

from __future__ import annotations

import pytest

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.engine import MaskingEngine
from securitymasker.errors import UnsupportedAttachmentError
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
async def test_codex_transport_ids_are_marked_opaque() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_4111111111111111",
                "output": "clean",
            }
        ],
        "previous_response_id": "resp_4111111111111111",
        "prompt_cache_key": "cache_4111111111111111",
        "client_metadata": {"turn_id": "turn_4111111111111111"},
    }
    summary = await adapter.mask_request(eng, s, data)
    assert summary.opaque_tokens == {
        "call_4111111111111111",
        "resp_4111111111111111",
        "cache_4111111111111111",
        "turn_4111111111111111",
    }


@pytest.mark.asyncio
async def test_codex_nested_turn_metadata_marks_only_transport_tokens() -> None:
    eng, s = build_engine(), await _session()
    metadata = (
        '{"turn_id":"019f9e64-262a-7ec1-8d0f-e63bb2c3e353",'
        '"turn_started_at_unix_ms":"1785068791342",'
        '"contact":"synthetic@example.com"}'
    )
    summary = await adapter.mask_request(
        eng,
        s,
        {"input": "clean", "client_metadata": {"x-codex-turn-metadata": metadata}},
    )
    assert summary.opaque_tokens == {
        "019f9e64-262a-7ec1-8d0f-e63bb2c3e353",
        "1785068791342",
    }


def test_codex_header_metadata_is_structured_without_hiding_other_values() -> None:
    metadata = (
        '{"turn_id":"019f9e64-262a-7ec1-8d0f-e63bb2c3e353",'
        '"turn_started_at_unix_ms":"1785068791342",'
        '"contact":"synthetic@example.com"}'
    )

    payload, opaque = adapter.prepare_wildcard_headers(
        {"x-codex-turn-metadata": metadata, "x-codex-feature": "clean"}
    )

    assert payload["x-codex-turn-metadata"] == {
        "turn_id": "019f9e64-262a-7ec1-8d0f-e63bb2c3e353",
        "turn_started_at_unix_ms": "1785068791342",
        "contact": "synthetic@example.com",
    }
    assert payload["x-codex-feature"] == "clean"
    assert opaque == {
        "019f9e64-262a-7ec1-8d0f-e63bb2c3e353",
        "1785068791342",
    }


def test_malformed_codex_header_metadata_remains_raw_for_leak_guard() -> None:
    raw = '{"turn_started_at_unix_ms":"4222222222222"'

    payload, opaque = adapter.prepare_wildcard_headers(
        {"x-codex-turn-metadata": raw}
    )

    assert payload == {"x-codex-turn-metadata": raw}
    assert opaque == set()


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
@pytest.mark.parametrize(
    "attachment",
    [
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        {"type": "input_file", "file_data": "c3ludGhldGlj", "filename": "secret.txt"},
        {"type": "input_file", "file_id": "file_synthetic"},
        {"type": "input_audio", "audio_url": "https://files.invalid/audio.wav"},
    ],
)
async def test_protocol_native_attachments_are_blocked(attachment: dict) -> None:
    eng, s = build_engine(), await _session()
    data = {
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "説明"}, attachment],
            }
        ]
    }

    with pytest.raises(UnsupportedAttachmentError, match="cannot be inspected"):
        await adapter.mask_request(eng, s, data)


@pytest.mark.asyncio
async def test_provider_file_search_is_blocked() -> None:
    eng, s = build_engine(), await _session()
    data = {
        "input": "資料を検索",
        "tools": [{"type": "file_search", "vector_store_ids": ["vs_synthetic"]}],
    }

    with pytest.raises(UnsupportedAttachmentError, match="cannot be inspected"):
        await adapter.mask_request(eng, s, data)


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
