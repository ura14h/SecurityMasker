"""OpenAI Responses APIのrequestをマスクし、responseを復元するadapter。

利用者の文字列を保持する``instructions``、``input``、toolの``description``、
function callの``arguments``だけを変換する。``model``、各種ID、``type``、``role``、
``status``、toolの``name``、JSON Schemaのkeyと型、``usage``などの構造fieldは変更しない。
未知fieldもそのまま保持する。
"""

from __future__ import annotations

import json
import re
from typing import Any

from securitymasker.engine import MaskingEngine
from securitymasker.models import ContextKind, MaskingSession
from securitymasker.protocols.base import (
    ALIAS_INSTRUCTION,
    TEXT_KEYS,
    MaskingSummary,
    MaskTransform,
    RestoreTransform,
    reject_unsupported_attachments,
)
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler

_OPAQUE_ID = re.compile(
    r"(?i)^(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?::\d+)?|"
    r"[a-z][a-z0-9]{1,15}_[a-z0-9_-]{6,})$"
)
_OPAQUE_TIMESTAMP_KEYS = frozenset(
    {
        "turn_started_at_unix_ms",
        "x-codex-ws-stream-request-start-ms",
    }
)

# --------------------------------------------------------------------------- mask


async def mask_request(
    engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]
) -> MaskingSummary:
    """Responses requestを構造を保ったままin-placeでマスクする。"""
    reject_unsupported_attachments(
        data,
        block_types=frozenset(
            {
                "input_image",
                "input_file",
                "input_audio",
                "computer_screenshot",
                "computer_call_output",
                "file_search",
            }
        ),
        reference_fields=frozenset(
            {"file_data", "file_id", "file_url", "image_url", "audio_url"}
        ),
    )
    summary = MaskingSummary()
    summary.opaque_tokens.update(_opaque_transport_tokens(data))

    async def mask(text: str, kind: str = ContextKind.PROSE.value) -> str:
        result = await engine.mask_text(session, text, context_kind=kind)
        summary.add(result.detections)
        return result.masked_text

    if isinstance(data.get("instructions"), str):
        data["instructions"] = await mask(data["instructions"])

    if "input" in data:
        data["input"] = await _mask_input(data["input"], mask)

    if isinstance(data.get("tools"), list):
        for tool in data["tools"]:
            await _mask_tool_definition(tool, mask)

    # 一つ以上のaliasを生成した場合、modelへ保持指示を任意で追加する。
    # 指示文はplaceholderを改変せずそのまま返すようmodelへ依頼する。
    if engine.inject_alias_instruction and session.mappings_by_alias:
        _prepend_instruction(data)
    return summary


def _opaque_transport_tokens(data: dict[str, Any]) -> set[str]:
    """Codex生成の既知IDと時刻だけを一般PII format scanから分離する。"""
    values: set[str] = set()

    def add_value(key: str, value: Any) -> None:
        if isinstance(value, str):
            if key == "x-codex-turn-metadata":
                try:
                    nested = json.loads(value)
                except json.JSONDecodeError:
                    return
                add_node(nested)
            if _OPAQUE_ID.fullmatch(value) or (
                key in _OPAQUE_TIMESTAMP_KEYS
                and value.isascii()
                and value.isdigit()
                and 12 <= len(value) <= 14
            ):
                values.add(value)
        elif isinstance(value, list | dict):
            add_node(value)

    def add_node(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                add_node(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                add_value(str(key), value)

    for key in ("previous_response_id", "prompt_cache_key"):
        add_value(key, data.get(key))
    metadata = data.get("client_metadata")
    if isinstance(metadata, dict):
        add_node(metadata)
    input_node = data.get("input")
    if isinstance(input_node, list):
        for item in input_node:
            if not isinstance(item, dict):
                continue
            for key in ("id", "call_id"):
                add_value(key, item.get(key))
    return values


def _prepend_instruction(data: dict[str, Any]) -> None:
    existing = data.get("instructions")
    if isinstance(existing, str) and existing:
        data["instructions"] = f"{ALIAS_INSTRUCTION}\n\n{existing}"
    elif existing is None or existing == "":
        data["instructions"] = ALIAS_INSTRUCTION


async def _mask_input(node: Any, mask: MaskTransform) -> Any:
    if isinstance(node, str):
        return await mask(node)
    if isinstance(node, list):
        return [await _mask_input_item(item, mask) for item in node]
    return node


async def _mask_input_item(item: Any, mask: MaskTransform) -> Any:
    if not isinstance(item, dict):
        return item
    # contentを持つmessage itemと、textを直接持つcontent partの両方を扱う。
    if "content" in item:
        await _mask_message_content(item, mask)
    for key in TEXT_KEYS:
        if isinstance(item.get(key), str):
            item[key] = await mask(item[key])
    # inputとして再送されたfunction-call outputとargument。
    output = item.get("output")
    if isinstance(output, str | list | dict):
        item["output"] = await _mask_tool_output(output, mask)
    if isinstance(item.get("arguments"), str):
        item["arguments"] = await _mask_arguments(item["arguments"], mask)
    return item


async def _mask_tool_output(node: Any, mask: MaskTransform) -> Any:
    """tool output配下の任意深さの文字列値を、keyを変えずにマスクする。"""
    if isinstance(node, str):
        return await mask(node, ContextKind.TOOL_RESULT.value)
    if isinstance(node, list):
        return [await _mask_tool_output(value, mask) for value in node]
    if isinstance(node, dict):
        return {key: await _mask_tool_output(value, mask) for key, value in node.items()}
    return node


async def _mask_message_content(message: dict[str, Any], mask: MaskTransform) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = await mask(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                for key in TEXT_KEYS:
                    if isinstance(part.get(key), str):
                        part[key] = await mask(part[key])


async def _mask_tool_definition(tool: Any, mask: MaskTransform) -> None:
    # 人間向けdescriptionだけをマスクし、nameとJSON Schemaは維持する。
    if not isinstance(tool, dict):
        return
    if isinstance(tool.get("description"), str):
        tool["description"] = await mask(tool["description"])
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("description"), str):
        fn["description"] = await mask(fn["description"])


async def _mask_arguments(raw: str, mask: MaskTransform) -> str:
    """構造を保ったままfunction-call argument JSON内の文字列値をマスクする。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return await mask(raw, ContextKind.TOOL_ARGUMENT.value)

    async def walk(node: Any) -> Any:
        if isinstance(node, str):
            return await mask(node, ContextKind.TOOL_ARGUMENT.value)
        if isinstance(node, list):
            return [await walk(v) for v in node]
        if isinstance(node, dict):
            return {k: await walk(v) for k, v in node.items()}
        return node

    return json.dumps(await walk(parsed), ensure_ascii=False)


# ------------------------------------------------------------------------ restore


def restore_response(engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]) -> None:
    """非streamingのResponses responseをin-placeで復元する。

    表示用textは元の値へ復元する。tool callの``arguments``は、明示的に信頼した
    local toolに限って実値へ復元する。
    """
    restore = engine.make_restorer(session)
    reasm = ToolArgumentReassembler(restore)
    trust = engine.tool_trust

    # Responses API：`output[].content[].text`とfunction_call argument。
    if isinstance(data.get("output"), list):
        for item in data["output"]:
            _restore_output_item(item, restore, reasm, trust)

def _restore_output_item(
    item: Any, restore: RestoreTransform, reasm: ToolArgumentReassembler, trust: Any
) -> None:
    if not isinstance(item, dict):
        return
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                for key in TEXT_KEYS:
                    if isinstance(part.get(key), str):
                        part[key] = restore(part[key])
    # tool argumentは実行対象なので、信頼済みlocal toolだけ実値へ復元する。
    # それ以外はaliasのまま保持し、外部toolへ元の機密値を渡さない。
    if isinstance(item.get("arguments"), str) and trust.restores_arguments(item.get("name")):
        item["arguments"] = reasm.restore_arguments(item["arguments"])
