"""LiteLLM integration adapter (thin, version-pinned).

This is the *only* module that imports LiteLLM. It maps LiteLLM's callback /
guardrail hooks onto the SecurityMasker core. Keeping the coupling here means a
LiteLLM hook rename or signature change is a one-file fix (AGENTS.md §2 rule 5).

Verified against **litellm==1.93.0**. Hook signatures are pinned by
``tests/unit/test_litellm_hook_contract.py``.

Behavior (Phase 2, OpenAI Responses/Chat):
- ``async_pre_call_hook``: mask the outbound request (fail-closed on error, §26).
- ``async_post_call_success_hook``: restore a non-streaming response.
- ``async_post_call_streaming_iterator_hook``: restore streamed text with a
  carry buffer (§20). Unknown chunks pass through unchanged.

If ``SECURITYMASKER_CONFIG`` is unset the callback is a transparent no-op, so the
proxy behaves as vanilla LiteLLM (§38-17).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail

from securitymasker.engine import MaskingEngine
from securitymasker.errors import SecurityMaskerError
from securitymasker.integrations.runtime import Runtime, resolve_session_id
from securitymasker.logging import get_logger
from securitymasker.models import MaskingSession
from securitymasker.protocols import anthropic_messages, openai_responses
from securitymasker.protocols.base import TEXT_KEYS, RestoreTransform
from securitymasker.streaming.anthropic_stream import AnthropicStreamProcessor
from securitymasker.streaming.text_replacer import StreamingRestorer
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler

REQUIRED_HOOKS: tuple[str, ...] = (
    "async_pre_call_hook",
    "async_post_call_success_hook",
    "async_post_call_streaming_iterator_hook",
    "async_post_call_failure_hook",
)

_log = get_logger(component="securitymasker.litellm")


class SecurityMaskerCallback(CustomGuardrail):
    """LiteLLM guardrail entry point for SecurityMasker."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._runtime = Runtime.from_env()

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: Any,
    ) -> Exception | str | dict[str, Any] | None:
        if self._runtime is None:
            return None
        try:
            session_id = resolve_session_id(data)
            session = await self._runtime.store.get_or_create(session_id)
            async with self._runtime.store.lock(session_id):
                if _is_anthropic(call_type, data):
                    await anthropic_messages.mask_request(self._runtime.engine, session, data)
                else:
                    await openai_responses.mask_request(self._runtime.engine, session, data)
                await self._runtime.store.save(session)
        except SecurityMaskerError:
            # Fail closed: block the request rather than forward original data (§26).
            _log.warning("securitymasker_block", stage="pre_call")
            raise
        return None

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> Any:
        if self._runtime is None:
            return response
        session_id = resolve_session_id(data)
        session = await self._runtime.store.get(session_id)
        if session is None:
            return response
        engine = self._runtime.engine
        if isinstance(response, dict):
            # Apply both restorers: they touch disjoint fields (choices/output vs
            # content), so this is protocol-agnostic and never double-restores.
            openai_responses.restore_response(engine, session, response)
            anthropic_messages.restore_response(engine, session, response)
        else:
            _restore_response_object(engine, session, response)
            anthropic_messages.restore_response_object(engine, session, response)
        return response

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: Any,
        request_data: dict[str, Any],
    ) -> AsyncGenerator[Any, None]:
        if self._runtime is None:
            async for chunk in response:
                yield chunk
            return

        session_id = resolve_session_id(request_data)
        session = await self._runtime.store.get(session_id)
        if session is None:
            async for chunk in response:
                yield chunk
            return

        engine = self._runtime.engine
        restorer = StreamingRestorer(engine.literal_restorations(session))
        plain_restore = engine.make_restorer(session)
        reasm = ToolArgumentReassembler(plain_restore)
        anthropic_proc: AnthropicStreamProcessor | None = None
        last_chunk: Any = None
        async for chunk in response:
            last_chunk = chunk
            if isinstance(chunk, bytes | bytearray):
                # Anthropic /v1/messages passthrough streams raw SSE bytes.
                if anthropic_proc is None:
                    anthropic_proc = AnthropicStreamProcessor(
                        engine.literal_restorations(session), plain_restore
                    )
                out = anthropic_proc.feed(bytes(chunk))
                if out:
                    yield out
            else:
                _restore_chunk_text(chunk, restorer, plain_restore, reasm)
                yield chunk
        if anthropic_proc is not None:
            tail_bytes = anthropic_proc.flush()
            if tail_bytes:
                yield tail_bytes
        else:
            tail = restorer.flush()
            if tail and (last := _make_text_chunk(last_chunk, tail)) is not None:
                yield last

    async def async_post_call_streaming_deployment_hook(
        self,
        request_data: dict[str, Any],
        response_chunk: Any,
        call_type: Any = None,
    ) -> Any:
        """Restore OpenAI Responses streaming chunks (§19).

        LiteLLM invokes this per-chunk *inside* the Responses streaming iterator
        (``ResponsesAPIStreamingIterator.__anext__``), passing ``request_data`` so
        the session is resolvable, and swaps in the returned chunk. This is the
        semantically-correct, most-upstream restore point and it works for any
        provider/path that serializes the client SSE from the (mutated) event.

        NOTE (litellm==1.93.0, verified by the real Codex E2E, docs/compatibility.md):
        for the OpenAI Responses **HTTP streaming** path, litellm emits the raw
        upstream SSE to the client and uses these parsed events only for
        logging/guardrails, so this restoration does not reach the client there —
        a fundamental litellm limitation, not a SecurityMasker defect. Masking
        (the security boundary) is unaffected and fully enforced.
        """
        if self._runtime is None:
            return response_chunk
        session_id = resolve_session_id(request_data)
        session = await self._runtime.store.get(session_id)
        if session is None:
            return response_chunk
        restore = self._runtime.engine.make_restorer(session)
        _restore_chunk_plain(response_chunk, restore, ToolArgumentReassembler(restore))
        return response_chunk

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        # Never leak original secrets on failure (§25, §26). Nothing to restore.
        return None


# --------------------------------------------------------------------------- utils


def _is_anthropic(call_type: Any, data: dict[str, Any]) -> bool:
    """Route masking by LiteLLM call_type (authoritative), falling back to shape."""
    if "anthropic" in str(call_type).lower():
        return True
    return anthropic_messages.is_anthropic_request(data) and "input" not in data


def _restore_content_part(part: Any, restore: RestoreTransform) -> None:
    for key in TEXT_KEYS:
        if isinstance(getattr(part, key, None), str):
            setattr(part, key, restore(getattr(part, key)))


def _restore_output_item(
    item: Any, restore: RestoreTransform, reasm: ToolArgumentReassembler
) -> None:
    for part in getattr(item, "content", None) or []:
        _restore_content_part(part, restore)
    if isinstance(getattr(item, "arguments", None), str):
        item.arguments = reasm.restore_arguments(item.arguments)


def _restore_response_fields(
    response: Any, restore: RestoreTransform, reasm: ToolArgumentReassembler
) -> None:
    """Restore text/tool-args in a Chat or Responses object (attribute access)."""
    for choice in getattr(response, "choices", None) or []:
        msg = getattr(choice, "message", None)
        if msg is None:
            continue
        if isinstance(getattr(msg, "content", None), str):
            msg.content = restore(msg.content)
        for call in getattr(msg, "tool_calls", None) or []:
            fn = getattr(call, "function", None)
            if fn is not None and isinstance(getattr(fn, "arguments", None), str):
                fn.arguments = reasm.restore_arguments(fn.arguments)

    for item in getattr(response, "output", None) or []:
        _restore_output_item(item, restore, reasm)


def _restore_response_object(
    engine: MaskingEngine, session: MaskingSession, response: Any
) -> None:
    """Restore aliases in a live non-streaming response object in place (§19)."""
    restore = engine.make_restorer(session)
    _restore_response_fields(response, restore, ToolArgumentReassembler(restore))


def _restore_chunk_plain(
    chunk: Any, restore: RestoreTransform, reasm: ToolArgumentReassembler
) -> None:
    """Restore a streaming chunk statelessly (no cross-chunk carry buffer).

    Used by the Responses-streaming deployment hook, which fires per chunk without
    per-stream state. Full-text "done"/"completed" events are authoritative for the
    client's final render, so plain restoration of every text field is sufficient;
    an alias split across raw ``output_text.delta`` events is corrected by the
    subsequent ``output_text.done`` / ``response.completed`` full-text event.
    """
    raw_type = getattr(chunk, "type", None)
    ctype = getattr(raw_type, "value", raw_type)
    if isinstance(ctype, str) and ctype.endswith("output_text.delta"):
        if isinstance(getattr(chunk, "delta", None), str):
            chunk.delta = restore(chunk.delta)
        return
    if isinstance(getattr(chunk, "text", None), str):
        chunk.text = restore(chunk.text)
    part = getattr(chunk, "part", None)
    if part is not None:
        _restore_content_part(part, restore)
    item = getattr(chunk, "item", None)
    if item is not None:
        _restore_output_item(item, restore, reasm)
    embedded = getattr(chunk, "response", None)
    if embedded is not None:
        _restore_response_fields(embedded, restore, reasm)
    for choice in getattr(chunk, "choices", None) or []:
        delta = getattr(choice, "delta", None)
        if delta is not None and isinstance(getattr(delta, "content", None), str):
            delta.content = restore(delta.content)


def _restore_chunk_text(
    chunk: Any,
    restorer: StreamingRestorer,
    restore: RestoreTransform,
    reasm: ToolArgumentReassembler,
) -> None:
    """Restore text in a streaming chunk, in place.

    Covers every text-bearing Responses streaming event, since a client may render
    from the incremental delta OR from a "done"/completed event that carries the
    full text (this bit the real Codex E2E):

    - Chat: ``choices[].delta.content`` (carry buffer for aliases split across chunks)
    - Responses ``OutputTextDeltaEvent.delta`` (carry buffer)
    - Responses ``OutputTextDoneEvent.text`` / ``ContentPart*Event.part`` /
      ``OutputItem*Event.item`` / ``Response*Event.response`` (full text — restored
      with a plain, idempotent restorer)

    Unknown chunks pass through unchanged (§22).
    """
    choices = getattr(chunk, "choices", None) or (
        chunk.get("choices") if isinstance(chunk, dict) else None
    )
    if choices:
        for choice in choices:
            delta = getattr(choice, "delta", None) or (
                choice.get("delta") if isinstance(choice, dict) else None
            )
            if delta is None:
                continue
            content = (
                delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
            )
            if isinstance(content, str) and content:
                restored = restorer.feed(content)
                if isinstance(delta, dict):
                    delta["content"] = restored
                else:
                    delta.content = restored
        return

    raw_type = getattr(chunk, "type", None)
    ctype = getattr(raw_type, "value", raw_type)  # str-enum members -> their value
    if isinstance(ctype, str) and ctype.endswith("output_text.delta"):
        delta_text = getattr(chunk, "delta", None)
        if isinstance(delta_text, str) and delta_text:
            chunk.delta = restorer.feed(delta_text)
        return

    # Full-text "done"/lifecycle events: restore in place with the plain restorer.
    if isinstance(getattr(chunk, "text", None), str):
        chunk.text = restore(chunk.text)
    part = getattr(chunk, "part", None)
    if part is not None:
        _restore_content_part(part, restore)
    item = getattr(chunk, "item", None)
    if item is not None:
        _restore_output_item(item, restore, reasm)
    embedded = getattr(chunk, "response", None)
    if embedded is not None:
        _restore_response_fields(embedded, restore, reasm)


def _make_text_chunk(template: Any, text: str) -> Any | None:
    """Clone the last chunk shape to carry a flushed text tail, if we can."""
    try:
        clone = template.model_copy(deep=True) if hasattr(template, "model_copy") else None
    except Exception:  # noqa: BLE001
        clone = None
    if clone is None:
        return None
    choices = getattr(clone, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None
    delta.content = text
    return clone
