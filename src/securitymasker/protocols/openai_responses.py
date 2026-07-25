"""OpenAI Responses + Chat Completions adapter (§22, §16).

Masks only user-text-bearing fields on the way out and restores them on the way
back, never altering structural fields (§16): ``model``, ``*_id``, ``type``,
``role``, ``status``, ``previous_response_id``, tool ``name``, JSON Schema keys/
types, or ``usage``. Unknown fields pass through untouched.

Maskable request fields (§22): ``instructions``; ``input`` (string, or a list of
message items whose content parts carry ``text``); chat ``messages`` content; tool
``description``; function-call ``arguments`` (string values only). Response
restoration reverses the text fields and re-serializes tool-call ``arguments``.
"""

from __future__ import annotations

import json
from typing import Any

from securitymasker.engine import MaskingEngine
from securitymasker.models import ContextKind, MaskingSession
from securitymasker.protocols.base import TEXT_KEYS, MaskTransform, RestoreTransform
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler


def is_responses_request(data: dict[str, Any]) -> bool:
    return "input" in data or "previous_response_id" in data


# --------------------------------------------------------------------------- mask


async def mask_request(
    engine: MaskingEngine, session: MaskingSession, data: dict[str, Any]
) -> None:
    """Mask a Responses or Chat Completions request in place."""

    async def mask(text: str, kind: str = ContextKind.PROSE.value) -> str:
        return (await engine.mask_text(session, text, context_kind=kind)).masked_text

    if isinstance(data.get("instructions"), str):
        data["instructions"] = await mask(data["instructions"])

    if "input" in data:
        data["input"] = await _mask_input(data["input"], mask)

    if isinstance(data.get("messages"), list):
        for message in data["messages"]:
            if isinstance(message, dict):
                await _mask_message_content(message, mask)

    if isinstance(data.get("tools"), list):
        for tool in data["tools"]:
            await _mask_tool_definition(tool, mask)


async def _mask_input(node: Any, mask: MaskTransform) -> Any:
    if isinstance(node, str):
        return await mask(node)
    if isinstance(node, list):
        return [await _mask_input_item(item, mask) for item in node]
    return node


async def _mask_input_item(item: Any, mask: MaskTransform) -> Any:
    if not isinstance(item, dict):
        return item
    # A message item with content parts, or a bare content part with `text`.
    if "content" in item:
        await _mask_message_content(item, mask)
    for key in TEXT_KEYS:
        if isinstance(item.get(key), str):
            item[key] = await mask(item[key])
    # Function-call output being resent as input, and its arguments.
    if isinstance(item.get("output"), str):
        item["output"] = await mask(item["output"], ContextKind.TOOL_RESULT.value)
    if isinstance(item.get("arguments"), str):
        item["arguments"] = await _mask_arguments(item["arguments"], mask)
    return item


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
    # Only the human-readable description is masked; name and JSON Schema stay (§16).
    if not isinstance(tool, dict):
        return
    if isinstance(tool.get("description"), str):
        tool["description"] = await mask(tool["description"])
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("description"), str):
        fn["description"] = await mask(fn["description"])


async def _mask_arguments(raw: str, mask: MaskTransform) -> str:
    """Mask string values inside a function-call argument JSON, preserving structure."""
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
    """Restore a non-streaming Responses/Chat response in place (§19).

    Response text is restored for display; tool-call ``arguments`` are restored to
    real values only for allowlisted trusted-local tools (doc/06 P0-8).
    """
    restore = engine.make_restorer(session)
    reasm = ToolArgumentReassembler(restore)
    trust = engine.tool_trust

    # Responses API: output[].content[].text  and function_call arguments.
    if isinstance(data.get("output"), list):
        for item in data["output"]:
            _restore_output_item(item, restore, reasm, trust)

    # Chat Completions: choices[].message.{content, tool_calls[].function.arguments}
    if isinstance(data.get("choices"), list):
        for choice in data["choices"]:
            msg = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(msg, dict):
                _restore_message(msg, restore, reasm, trust)


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
    # Tool arguments are execution-bound: restore only for a trusted local tool,
    # otherwise leave the aliases in place (doc/06 P0-8).
    if isinstance(item.get("arguments"), str) and trust.restores_arguments(item.get("name")):
        item["arguments"] = reasm.restore_arguments(item["arguments"])


def _restore_message(
    msg: dict[str, Any], restore: RestoreTransform, reasm: ToolArgumentReassembler, trust: Any
) -> None:
    if isinstance(msg.get("content"), str):
        msg["content"] = restore(msg["content"])
    elif isinstance(msg.get("content"), list):
        for part in msg["content"]:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = restore(part["text"])
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            fn = call.get("function") if isinstance(call, dict) else None
            if (isinstance(fn, dict) and isinstance(fn.get("arguments"), str)
                    and trust.restores_arguments(fn.get("name"))):
                fn["arguments"] = reasm.restore_arguments(fn["arguments"])
