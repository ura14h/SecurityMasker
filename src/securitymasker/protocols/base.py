"""Shared helpers for protocol adapters.

Adapters mutate the request/response dicts in place, touching only text-bearing
value fields and never structural keys (§16). The transform used for masking is the
engine's async ``mask_text``; for restoration it is the engine's sync restorer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class MaskTransform(Protocol):
    """Async masking transform; ``kind`` selects the detection context (§17)."""

    async def __call__(self, text: str, kind: str = ...) -> str: ...


RestoreTransform = Callable[[str], str]

# Content-part keys that carry user text in OpenAI/Anthropic message structures.
TEXT_KEYS = frozenset({"text", "input_text", "output_text"})
