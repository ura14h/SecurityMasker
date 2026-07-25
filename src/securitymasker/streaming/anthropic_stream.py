"""Anthropic Messages SSE stream restorer (§20, §21, §23).

LiteLLM streams the Anthropic ``/v1/messages`` passthrough as raw SSE **bytes** at
the iterator hook (not typed objects). This processor decodes bytes (UTF-8 safe
across chunk boundaries), parses SSE events, and:

- restores ``text_delta`` text with a per-block carry buffer so aliases split
  across ``content_block_delta`` events are recovered (§20); the block's tail is
  flushed as an extra delta at ``content_block_stop``;
- buffers ``input_json_delta`` ``partial_json`` per block, suppressing the deltas
  until ``content_block_stop``, then emits one restored ``input_json_delta`` (§21).
  If the buffered JSON is invalid it is emitted unchanged (aliases left in place —
  safe, never an approximate restore, §24).

All other events (``message_start``/``message_delta``/``message_stop``/``ping``/
unknown) pass through unchanged, preserving event order and ``usage`` (§20).
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Callable, Mapping
from typing import Any

from securitymasker.protocols.sse import SSEEvent, SSEParser, serialize_event
from securitymasker.streaming.text_replacer import StreamingRestorer
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler
from securitymasker.tool_trust import ToolTrustPolicy

# Cap on buffered tool-input bytes per block (doc/06 P1-10): beyond this we emit the
# raw (aliased) buffer without restoration so a runaway stream can't grow memory
# without bound. Never a partial literal restore.
_MAX_JSON_BUFFER_BYTES = 1_000_000


class AnthropicStreamProcessor:
    def __init__(
        self,
        replacements: Mapping[str, str],
        restore: Callable[[str], str],
        tool_trust: ToolTrustPolicy | None = None,
    ) -> None:
        self._replacements = dict(replacements)
        self._trust = tool_trust if tool_trust is not None else ToolTrustPolicy()
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._parser = SSEParser()
        self._text_restorers: dict[int, StreamingRestorer] = {}
        self._json_blocks: set[int] = set()
        self._json_buffers: dict[int, list[str]] = {}
        self._json_sizes: dict[int, int] = {}
        self._json_overflow: set[int] = set()
        self._block_names: dict[int, str] = {}  # block index -> tool name (P0-8)
        self._reasm = ToolArgumentReassembler(restore)

    def feed(self, data: bytes) -> bytes:
        text = self._decoder.decode(data)
        return self._emit(ev for parsed in [self._parser.feed(text)] for ev in parsed)

    def flush(self) -> bytes:
        tail_text = self._decoder.decode(b"", final=True)
        events = self._parser.feed(tail_text) + self._parser.flush()
        out: list[SSEEvent] = []
        for ev in events:
            out.extend(self._handle(ev))
        # Flush any remaining per-block text carry buffers.
        for idx, restorer in list(self._text_restorers.items()):
            leftover = restorer.flush()
            if leftover:
                out.append(_text_delta_event(idx, leftover))
        self._text_restorers.clear()
        # Stream ended before these tool blocks stopped: never emit incomplete JSON
        # as an executable call, but report it rather than dropping it (P1-10).
        pending = sorted(set(self._json_buffers) | self._json_overflow)
        if pending:
            out.append(_error_event(
                "securitymasker: the response stream ended before "
                f"{len(pending)} tool call(s) completed; their inputs were "
                "incomplete and were not forwarded."))
        self._json_buffers.clear()
        self._json_sizes.clear()
        self._json_overflow.clear()
        return _serialize(out)

    def _emit(self, events: Any) -> bytes:
        out: list[SSEEvent] = []
        for ev in events:
            out.extend(self._handle(ev))
        return _serialize(out)

    def _handle(self, ev: SSEEvent) -> list[SSEEvent]:
        payload = _payload(ev)
        if payload is None:
            return [ev]
        etype = payload.get("type")
        if etype == "content_block_start":
            self._on_block_start(payload)
            return [ev]
        if etype == "content_block_delta":
            return self._on_block_delta(ev, payload)
        if etype == "content_block_stop":
            return self._on_block_stop(ev, payload)
        return [ev]

    def _on_block_start(self, payload: dict[str, Any]) -> None:
        idx = payload.get("index")
        block = payload.get("content_block") or {}
        if not isinstance(idx, int):
            return
        if block.get("type") == "text":
            self._text_restorers[idx] = StreamingRestorer(self._replacements)
        elif block.get("type") == "tool_use":
            self._json_blocks.add(idx)
            self._json_buffers[idx] = []
            if isinstance(block.get("name"), str):
                self._block_names[idx] = block["name"]

    def _on_block_delta(self, ev: SSEEvent, payload: dict[str, Any]) -> list[SSEEvent]:
        idx = payload.get("index")
        delta = payload.get("delta") or {}
        if delta.get("type") == "text_delta" and isinstance(idx, int):
            restorer = self._text_restorers.setdefault(idx, StreamingRestorer(self._replacements))
            delta["text"] = restorer.feed(str(delta.get("text", "")))
            return [_reserialize(ev, payload)]
        if delta.get("type") == "input_json_delta" and isinstance(idx, int):
            if idx in self._json_overflow:
                return []  # already over the cap: discard, never keep growing
            partial = str(delta.get("partial_json", ""))
            size = self._json_sizes.get(idx, 0) + len(partial)
            if size > _MAX_JSON_BUFFER_BYTES:
                # Stop accumulating and drop the buffer: keeping it would defeat
                # the cap. The block is failed closed at content_block_stop.
                self._json_overflow.add(idx)
                self._json_buffers.pop(idx, None)
                self._json_sizes[idx] = size
                return []
            self._json_buffers.setdefault(idx, []).append(partial)
            self._json_sizes[idx] = size
            return []  # suppress until the block completes (§21)
        return [ev]

    def _on_block_stop(self, ev: SSEEvent, payload: dict[str, Any]) -> list[SSEEvent]:
        idx = payload.get("index")
        extra: list[SSEEvent] = []
        if isinstance(idx, int) and idx in self._text_restorers:
            leftover = self._text_restorers.pop(idx).flush()
            if leftover:
                extra.append(_text_delta_event(idx, leftover))
        if isinstance(idx, int) and idx in self._json_blocks:
            self._json_blocks.discard(idx)
            raw = "".join(self._json_buffers.pop(idx, []))
            self._json_sizes.pop(idx, None)
            overflowed = idx in self._json_overflow
            self._json_overflow.discard(idx)
            name = self._block_names.pop(idx, None)
            if overflowed:
                # Fail closed and visibly: the input exceeded the cap so we no
                # longer hold it; emitting the remainder would hand the client a
                # tool call it must not execute (doc/06 P1-10).
                extra.append(_error_event(
                    "securitymasker: tool input exceeded the "
                    f"{_MAX_JSON_BUFFER_BYTES}-byte buffer limit; the call was not "
                    "forwarded to the client."))
            elif raw:
                # Restore real values only for a trusted local tool (doc/06 P0-8);
                # otherwise re-emit the buffered aliased JSON unchanged.
                partial: str | None = raw
                if self._trust.restores_arguments(name):
                    partial = self._safe_restore_json(raw)
                if partial is None:
                    # Unparseable input cannot be restored; emitting it would hand
                    # the client an unexecutable tool call (doc/06 P1-10).
                    extra.append(_error_event(
                        "securitymasker: tool input was not valid JSON and could not "
                        "be restored; the call was not forwarded to the client."))
                else:
                    extra.append(_json_delta_event(idx, partial))
        return [*extra, ev]

    def _safe_restore_json(self, raw: str) -> str | None:
        """Restored tool input, or ``None`` when it cannot be restored safely."""
        try:
            return self._reasm.restore_arguments(raw)
        except Exception:  # noqa: BLE001 - never approximate a restore (§24)
            return None


# --------------------------------------------------------------------------- helpers


def _payload(ev: SSEEvent) -> dict[str, Any] | None:
    text = ev.data_text
    if not text or text == "[DONE]":
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _reserialize(ev: SSEEvent, payload: dict[str, Any]) -> SSEEvent:
    return SSEEvent(event=ev.event, data=[json.dumps(payload, ensure_ascii=False)], id=ev.id)


def _text_delta_event(idx: int, text: str) -> SSEEvent:
    payload = {"type": "content_block_delta", "index": idx,
               "delta": {"type": "text_delta", "text": text}}
    return SSEEvent(event="content_block_delta", data=[json.dumps(payload, ensure_ascii=False)])


def _error_event(message: str) -> SSEEvent:
    """Anthropic-compatible error event (doc/06 P1-10).

    An SSE stream's HTTP status is fixed once it starts, so a mid-stream
    fail-closed outcome is reported as an ``error`` event. Carries no user content.
    """
    payload = {"type": "error", "error": {"type": "securitymasker_stream_error",
                                          "message": message}}
    return SSEEvent(event="error", data=[json.dumps(payload, ensure_ascii=False)])


def _json_delta_event(idx: int, partial_json: str) -> SSEEvent:
    payload = {"type": "content_block_delta", "index": idx,
               "delta": {"type": "input_json_delta", "partial_json": partial_json}}
    return SSEEvent(event="content_block_delta", data=[json.dumps(payload, ensure_ascii=False)])


def _serialize(events: list[SSEEvent]) -> bytes:
    return "".join(serialize_event(e) for e in events).encode("utf-8")
