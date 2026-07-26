"""Anthropic Messages APIのrequestをマスクし、responseを復元するadapter。

``system``、``messages``内のtextとtool入出力、toolの``description``だけを変換する。
``type``、``role``、各種ID、``name``、``input_schema``、``stop_reason``、
``usage``などの構造fieldと未知blockは変更しない。
"""

from __future__ import annotations

from typing import Any

from securitymasker.engine import MaskingEngine
from securitymasker.models import ContextKind, MaskingSession
from securitymasker.protocols.base import ALIAS_INSTRUCTION, MaskingSummary, MaskTransform
from securitymasker.protocols.structured_walker import (
    transform_all_string_values,
    transform_all_string_values_sync,
)


def is_anthropic_request(data: dict[str, Any]) -> bool:
    # Anthropic requestは`messages`と同階層に`system`／`max_tokens`を持つ。
    # authoritative signal is the route the client used, checked by the caller.
    return "messages" in data and ("system" in data or "max_tokens" in data)


# --------------------------------------------------------------------------- mask


async def mask_request(
    engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]
) -> MaskingSummary:
    """Anthropic Messages requestをin-placeでマスクする。"""
    summary = MaskingSummary()

    async def mask(text: str, kind: str = ContextKind.PROSE.value) -> str:
        result = await engine.mask_text(session, text, context_kind=kind)
        summary.add(result.detections)
        return result.masked_text

    await _mask_system(data, mask)

    if isinstance(data.get("messages"), list):
        for message in data["messages"]:
            if isinstance(message, dict):
                message["content"] = await _mask_content(message.get("content"), mask)

    if isinstance(data.get("tools"), list):
        for tool in data["tools"]:
            if isinstance(tool, dict) and isinstance(tool.get("description"), str):
                tool["description"] = await mask(tool["description"])

    if engine.inject_alias_instruction and session.mappings_by_alias:
        _prepend_instruction(data)
    return summary


def _prepend_instruction(data: dict[str, Any]) -> None:
    system = data.get("system")
    if isinstance(system, str) and system:
        data["system"] = f"{ALIAS_INSTRUCTION}\n\n{system}"
    elif isinstance(system, list):
        system.insert(0, {"type": "text", "text": ALIAS_INSTRUCTION})
    else:
        data["system"] = ALIAS_INSTRUCTION


async def _mask_system(data: dict[str, Any], mask: MaskTransform) -> None:
    system = data.get("system")
    if isinstance(system, str):
        data["system"] = await mask(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = await mask(block["text"])


async def _mask_content(content: Any, mask: MaskTransform) -> Any:
    if isinstance(content, str):
        return await mask(content)
    if not isinstance(content, list):
        return content
    for block in content:
        if isinstance(block, dict):
            await _mask_block(block, mask)
    return content


async def _mask_block(block: dict[str, Any], mask: MaskTransform) -> None:
    btype = block.get("type")
    if btype == "text" and isinstance(block.get("text"), str):
        block["text"] = await mask(block["text"])
    elif btype == "tool_use" and isinstance(block.get("input"), dict | list):
        block["input"] = await transform_all_string_values(
            block["input"], lambda s: mask(s, ContextKind.TOOL_ARGUMENT.value)
        )
    elif btype == "tool_result":
        block["content"] = await _mask_content(block.get("content"), mask)
    # 未知block type（image、textなしthinking、citationなど）は透過する。


# ------------------------------------------------------------------------ restore


def restore_response(
    engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]
) -> None:
    """非streamingのAnthropic responseをin-placeで復元する。

    text blockは表示用に復元する。``tool_use.input``はallowlist済みlocal toolだけ
    復元し、それ以外はaliasのまま保持する。
    """
    restore = engine.make_restorer(session)
    trust = engine.tool_trust
    if isinstance(data.get("content"), list):
        for block in data["content"]:
            _restore_block(block, restore, trust)


def _restore_block(block: Any, restore: Any, trust: Any) -> None:
    if not isinstance(block, dict):
        return
    if block.get("type") == "text" and isinstance(block.get("text"), str):
        block["text"] = restore(block["text"])
    elif (block.get("type") == "tool_use" and isinstance(block.get("input"), dict | list)
          and trust.restores_arguments(block.get("name"))):
        block["input"] = transform_all_string_values_sync(block["input"], restore)


def restore_response_object(engine: MaskingEngine, session: MaskingSession, response: Any) -> None:
    """live Anthropic response objectをattribute accessで復元する。"""
    restore = engine.make_restorer(session)
    trust = engine.tool_trust
    for block in getattr(response, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "text" and isinstance(getattr(block, "text", None), str):
            block.text = restore(block.text)
        elif (btype == "tool_use" and isinstance(getattr(block, "input", None), dict | list)
              and trust.restores_arguments(getattr(block, "name", None))):
            block.input = transform_all_string_values_sync(block.input, restore)
