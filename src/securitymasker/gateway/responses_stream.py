"""OpenAI Responses SSE restorer (§19, §20, §21).

The proxy owns the response stream, so — unlike the LiteLLM callback path — this
restoration actually reaches the client. Decodes SSE bytes (UTF-8 safe across
chunks), parses events, and restores aliases:

- ``response.output_text.delta``: per-block carry buffer (aliases split across
  deltas, §20); the block flushes at its ``output_text.done``.
- ``response.output_text.done`` / ``content_part.*`` / ``output_item.*`` /
  ``response.created|in_progress|completed``: full text — restored in place.
- ``response.function_call_arguments.delta``: buffered per item and re-emitted as
  one restored ``.done`` argument JSON (§21); invalid JSON is left as-is (§24).

Unknown events pass through unchanged (§22).
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Callable, Mapping
from typing import Any

from securitymasker.protocols.base import TEXT_KEYS
from securitymasker.protocols.sse import SSEEvent, SSEParser, serialize_event
from securitymasker.streaming.text_replacer import StreamingRestorer
from securitymasker.streaming.tool_arguments import ToolArgumentReassembler
from securitymasker.tool_trust import ToolTrustPolicy

# Cap on buffered tool-argument bytes per call (doc/06 P1-10): beyond this we stop
# accumulating and emit the raw (aliased) buffer without restoration, so a runaway
# tool-call stream can't grow memory without bound. Never a partial literal restore.
_MAX_ARG_BUFFER_BYTES = 1_000_000


def _restore_content_parts(content: Any, restore: Callable[[str], str]) -> None:
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                for key in TEXT_KEYS:
                    if isinstance(part.get(key), str):
                        part[key] = restore(part[key])


def _restore_response_dict(response: Any, restore: Callable[[str], str]) -> None:
    if not isinstance(response, dict):
        return
    for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
        if isinstance(item, dict):
            _restore_content_parts(item.get("content"), restore)


class ResponsesStreamProcessor:
    def __init__(
        self,
        replacements: Mapping[str, str],
        restore: Callable[[str], str],
        tool_trust: ToolTrustPolicy | None = None,
    ) -> None:
        self._replacements = dict(replacements)
        self._restore = restore
        self._trust = tool_trust if tool_trust is not None else ToolTrustPolicy()
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._parser = SSEParser()
        self._text_restorers: dict[tuple[int, int], StreamingRestorer] = {}
        self._arg_buffers: dict[str, list[str]] = {}
        self._arg_sizes: dict[str, int] = {}
        self._arg_overflow: set[str] = set()
        self._item_names: dict[str, str] = {}  # item_id -> tool name (for trust, P0-8)
        # Response ids seen in the stream; the gateway binds them to this session
        # so the next turn's previous_response_id continues it (doc/06 P1-1).
        self.response_ids: set[str] = set()
        self._reasm = ToolArgumentReassembler(restore)

    def feed(self, data: bytes) -> bytes:
        text = self._decoder.decode(data)
        out: list[SSEEvent] = []
        for ev in self._parser.feed(text):
            out.extend(self._handle(ev))
        return _serialize(out)

    def flush(self) -> bytes:
        events = self._parser.feed(self._decoder.decode(b"", final=True)) + self._parser.flush()
        out: list[SSEEvent] = []
        for ev in events:
            out.extend(self._handle(ev))
        for key, restorer in list(self._text_restorers.items()):
            leftover = restorer.flush()
            if leftover:
                out.append(_text_delta_event(key[0], key[1], leftover))
        self._text_restorers.clear()
        return _serialize(out)

    def _handle(self, ev: SSEEvent) -> list[SSEEvent]:
        payload = _payload(ev)
        if payload is None:
            return [ev]
        etype = payload.get("type", "")
        if etype == "response.output_text.delta":
            return self._on_text_delta(ev, payload)
        if etype == "response.output_text.done":
            return self._on_text_done(ev, payload)
        if etype == "response.function_call_arguments.delta":
            return self._on_args_delta(payload)
        if etype == "response.function_call_arguments.done":
            return self._on_args_done(ev, payload)
        if etype in ("response.content_part.added", "response.content_part.done"):
            part = payload.get("part")
            if isinstance(part, dict):
                for key in TEXT_KEYS:
                    if isinstance(part.get(key), str):
                        part[key] = self._restore(part[key])
            return [_reserialize(ev, payload)]
        if etype in ("response.output_item.added", "response.output_item.done"):
            item = payload.get("item")
            if isinstance(item, dict):
                # Remember item_id -> tool name so argument deltas can be trusted.
                if isinstance(item.get("id"), str) and isinstance(item.get("name"), str):
                    self._item_names[item["id"]] = item["name"]
                _restore_content_parts(item.get("content"), self._restore)
                if isinstance(item.get("arguments"), str) and self._trust.restores_arguments(
                    item.get("name")
                ):
                    item["arguments"] = self._safe_args(item["arguments"])
            return [_reserialize(ev, payload)]
        if "response" in payload and isinstance(payload["response"], dict):
            rid = payload["response"].get("id")
            if isinstance(rid, str) and rid:
                self.response_ids.add(rid)
            _restore_response_dict(payload["response"], self._restore)
            return [_reserialize(ev, payload)]
        return [ev]

    def _on_text_delta(self, ev: SSEEvent, payload: dict[str, Any]) -> list[SSEEvent]:
        key = (int(payload.get("output_index", 0)), int(payload.get("content_index", 0)))
        restorer = self._text_restorers.setdefault(key, StreamingRestorer(self._replacements))
        payload["delta"] = restorer.feed(str(payload.get("delta", "")))
        return [_reserialize(ev, payload)]

    def _on_text_done(self, ev: SSEEvent, payload: dict[str, Any]) -> list[SSEEvent]:
        key = (int(payload.get("output_index", 0)), int(payload.get("content_index", 0)))
        extra: list[SSEEvent] = []
        restorer = self._text_restorers.pop(key, None)
        if restorer is not None:
            leftover = restorer.flush()
            if leftover:
                extra.append(_text_delta_event(key[0], key[1], leftover))
        if isinstance(payload.get("text"), str):
            payload["text"] = self._restore(payload["text"])
        return [*extra, _reserialize(ev, payload)]

    def _on_args_delta(self, payload: dict[str, Any]) -> list[SSEEvent]:
        item_id = str(payload.get("item_id", ""))
        delta = str(payload.get("delta", ""))
        size = self._arg_sizes.get(item_id, 0) + len(delta)
        if size > _MAX_ARG_BUFFER_BYTES:
            self._arg_overflow.add(item_id)  # stop restoring; emit raw at done (P1-10)
        self._arg_buffers.setdefault(item_id, []).append(delta)
        self._arg_sizes[item_id] = size
        return []  # suppress until done (§21)

    def _on_args_done(self, ev: SSEEvent, payload: dict[str, Any]) -> list[SSEEvent]:
        item_id = str(payload.get("item_id", ""))
        raw = "".join(self._arg_buffers.pop(item_id, [])) or str(payload.get("arguments", ""))
        self._arg_sizes.pop(item_id, None)
        overflowed = item_id in self._arg_overflow
        self._arg_overflow.discard(item_id)
        # Restore to real values only for a trusted local tool (doc/06 P0-8) and only
        # when the buffer stayed within the cap (P1-10); otherwise emit raw (aliased).
        if not overflowed and self._trust.restores_arguments(self._item_names.get(item_id)):
            restored = self._safe_args(raw)
        else:
            restored = raw
        payload["arguments"] = restored
        emit_delta = _args_delta_event(payload, restored)
        return [emit_delta, _reserialize(ev, payload)]

    def _safe_args(self, raw: str) -> str:
        try:
            return self._reasm.restore_arguments(raw)
        except Exception:  # noqa: BLE001 - leave aliases, never approximate (§24)
            return raw


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


def _text_delta_event(output_index: int, content_index: int, text: str) -> SSEEvent:
    payload = {"type": "response.output_text.delta", "output_index": output_index,
               "content_index": content_index, "delta": text}
    return SSEEvent(event="response.output_text.delta",
                    data=[json.dumps(payload, ensure_ascii=False)])


def _args_delta_event(done_payload: dict[str, Any], arguments: str) -> SSEEvent:
    payload = {"type": "response.function_call_arguments.delta",
               "item_id": done_payload.get("item_id"),
               "output_index": done_payload.get("output_index"),
               "delta": arguments}
    return SSEEvent(event="response.function_call_arguments.delta",
                    data=[json.dumps(payload, ensure_ascii=False)])


def _serialize(events: list[SSEEvent]) -> bytes:
    return "".join(serialize_event(e) for e in events).encode("utf-8")
