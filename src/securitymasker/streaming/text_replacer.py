"""Carry-buffer streaming alias→original replacer (§20).

Aliases can be split across SSE chunks (``"…sm-host-7f" | "3a91.example.invalid…"``),
so a naive per-chunk ``replace()`` would miss them. This holds back only the
smallest suffix of the buffer that could still be the start of an alias, emits
everything before it (with complete aliases replaced), and carries the rest to the
next chunk. ``flush()`` drains the tail at end-of-stream.

Operates on ``str`` (already-decoded text), so UTF-8 multibyte characters are never
split (§20). It replaces a fixed alias vocabulary; unknown text passes through
untouched, preserving order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


class StreamingRestorer:
    def __init__(self, replacements: Mapping[str, str]) -> None:
        self._replacements = dict(replacements)
        self._buffer = ""
        self._max_alias_len = max((len(a) for a in self._replacements), default=0)
        # Longest alias first so a short alias that prefixes a longer one (from
        # collision-lengthened tokens) never matches greedily-early.
        aliases: Sequence[str] = sorted(self._replacements, key=len, reverse=True)
        self._rx = re.compile("|".join(re.escape(a) for a in aliases)) if aliases else None

    def feed(self, chunk: str) -> str:
        """Consume a chunk; return the text that is now safe to emit."""
        if self._rx is None:
            return chunk  # empty vocabulary: pure pass-through
        if not chunk:
            return ""
        self._buffer += chunk
        keep = self._suffix_prefix_len(self._buffer)
        cut = len(self._buffer) - keep
        safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._rx.sub(self._sub, safe)

    def flush(self) -> str:
        """Emit any remaining buffered text at end-of-stream."""
        if self._rx is None:
            out, self._buffer = self._buffer, ""
            return out
        out = self._rx.sub(self._sub, self._buffer)
        self._buffer = ""
        return out

    def _sub(self, match: re.Match[str]) -> str:
        return self._replacements[match.group(0)]

    def _suffix_prefix_len(self, text: str) -> int:
        """Length of the longest suffix of ``text`` that is a prefix of some alias.

        That suffix might grow into a full alias with the next chunk, so it must be
        held back. Bounded by ``max_alias_len - 1``.
        """
        max_k = min(self._max_alias_len - 1, len(text))
        for k in range(max_k, 0, -1):
            suffix = text[-k:]
            for alias in self._replacements:
                if alias.startswith(suffix):
                    return k
        return 0
