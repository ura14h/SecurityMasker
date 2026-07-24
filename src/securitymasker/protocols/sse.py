"""Minimal SSE parser/serializer (§20, §22, §23).

Preserves event structure exactly: ``event:``/``id:``/``retry:``/comment (``:``)
lines and multi-line ``data:`` payloads. Unknown fields and events pass through
untouched (§22). ``data: [DONE]`` is just a data payload and is preserved verbatim.
The incremental ``SSEParser`` buffers partial lines/events across chunks.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field


@dataclass
class SSEEvent:
    event: str | None = None
    data: list[str] = field(default_factory=list)
    id: str | None = None
    retry: int | None = None
    comments: list[str] = field(default_factory=list)

    @property
    def data_text(self) -> str:
        """Concatenated data payload (SSE joins multiple ``data:`` lines with \\n)."""
        return "\n".join(self.data)

    def is_empty(self) -> bool:
        return not (self.event or self.data or self.id or self.retry or self.comments)


def serialize_event(ev: SSEEvent) -> str:
    lines: list[str] = []
    for c in ev.comments:
        lines.append(f":{c}")
    if ev.event is not None:
        lines.append(f"event: {ev.event}")
    for d in ev.data:
        lines.append(f"data: {d}")
    if ev.id is not None:
        lines.append(f"id: {ev.id}")
    if ev.retry is not None:
        lines.append(f"retry: {ev.retry}")
    return "\n".join(lines) + "\n\n"


def _field(line: str) -> tuple[str, str]:
    # "field: value" — a single leading space after the colon is stripped (SSE spec).
    if ":" not in line:
        return line, ""
    name, _, value = line.partition(":")
    if value.startswith(" "):
        value = value[1:]
    return name, value


def _event_from_lines(lines: list[str]) -> SSEEvent:
    ev = SSEEvent()
    for raw in lines:
        if raw == "":
            continue
        if raw.startswith(":"):
            ev.comments.append(raw[1:])
            continue
        name, value = _field(raw)
        if name == "event":
            ev.event = value
        elif name == "data":
            ev.data.append(value)
        elif name == "id":
            ev.id = value
        elif name == "retry":
            with contextlib.suppress(ValueError):  # ignore malformed retry (spec)
                ev.retry = int(value)
    return ev


def parse_sse(text: str) -> list[SSEEvent]:
    parser = SSEParser()
    events = parser.feed(text)
    events.extend(parser.flush())
    return events


class SSEParser:
    def __init__(self) -> None:
        self._pending = ""  # incomplete trailing line data
        self._lines: list[str] = []  # lines of the event currently being built

    def feed(self, chunk: str) -> list[SSEEvent]:
        self._pending += chunk.replace("\r\n", "\n").replace("\r", "\n")
        events: list[SSEEvent] = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line == "":  # blank line dispatches the event
                if self._lines:
                    events.append(_event_from_lines(self._lines))
                    self._lines = []
            else:
                self._lines.append(line)
        return events

    def flush(self) -> list[SSEEvent]:
        if self._pending:
            self._lines.append(self._pending)
            self._pending = ""
        if self._lines:
            ev = _event_from_lines(self._lines)
            self._lines = []
            return [ev] if not ev.is_empty() else []
        return []
