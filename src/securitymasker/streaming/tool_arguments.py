"""Tool-call argument reassembly for restoration (§21).

Function/tool argument JSON arrives split across deltas. Restoring aliases by naive
string replacement can corrupt JSON when an original value contains ``"``/``\\``/
newlines. So we buffer deltas per tool-call id, wait for completion, ``json.loads``,
restore string *values* recursively, and ``json.dumps`` again — re-serialization
guarantees valid escaping (§21, §30.3).

Fail-closed (§24, §26): if the reassembled buffer is not valid JSON, we raise rather
than emit a broken/partial tool call. Approximate restoration is never done inside
tool arguments.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from securitymasker.errors import RestoreError
from securitymasker.protocols.structured_walker import transform_all_string_values_sync


class ToolArgumentReassembler:
    """Accumulates argument deltas per tool-call id and restores on completion."""

    def __init__(self, restore: Callable[[str], str]) -> None:
        self._restore = restore
        self._buffers: dict[str, list[str]] = {}

    def add_delta(self, tool_call_id: str, delta: str) -> None:
        self._buffers.setdefault(tool_call_id, []).append(delta)

    def has(self, tool_call_id: str) -> bool:
        return tool_call_id in self._buffers

    def complete(self, tool_call_id: str) -> str:
        """Return the fully-restored, re-serialized argument JSON for one tool call."""
        parts = self._buffers.pop(tool_call_id, None)
        if parts is None:
            raise RestoreError(f"no buffered arguments for tool call {tool_call_id}")
        return self.restore_arguments("".join(parts))

    def restore_arguments(self, raw: str) -> str:
        """Restore a complete argument JSON string (parse → restore values → dump)."""
        text = raw.strip()
        if text == "":
            return raw  # nothing to restore (e.g. no-arg tool call)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RestoreError(
                "tool-call arguments were not valid JSON; refusing to emit a "
                "partially-restored tool call (fail-closed)"
            ) from exc
        restored = transform_all_string_values_sync(parsed, self._restore)
        return json.dumps(restored, ensure_ascii=False)

    def pending_ids(self) -> list[str]:
        return list(self._buffers)
