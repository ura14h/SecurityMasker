"""Structure-preserving JSON walkers (§16).

Never stringify a whole payload and blanket-replace. Instead walk the structure and
transform only *string values*, never dict keys, ids, or type/role tags. Two tools:

- ``transform_all_string_values``: for free-form user JSON (tool arguments) — every
  string value is run through the transform; keys are untouched (§16 tool example).
  This is safe because the transform (detection) only replaces detected secrets, so
  non-sensitive strings pass through unchanged.
- ``transform_field`` / ``transform_text_fields``: for known envelopes where only
  designated text keys (``text``, ``input``, ``instructions``, …) may be masked and
  structural keys (``type``, ``role``, ``status``, ``id``) must not be.

Transforms are async so the masking engine can be awaited inline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Transform = Callable[[str], Awaitable[str]]


async def transform_all_string_values(node: Any, transform: Transform) -> Any:
    """Recursively transform every string *value*; leave dict keys and non-str alone."""
    if isinstance(node, str):
        return await transform(node)
    if isinstance(node, list):
        return [await transform_all_string_values(item, transform) for item in node]
    if isinstance(node, dict):
        return {key: await transform_all_string_values(val, transform) for key, val in node.items()}
    return node


def transform_all_string_values_sync(node: Any, transform: Callable[[str], str]) -> Any:
    """Sync variant of ``transform_all_string_values`` (for tool-argument restore)."""
    if isinstance(node, str):
        return transform(node)
    if isinstance(node, list):
        return [transform_all_string_values_sync(item, transform) for item in node]
    if isinstance(node, dict):
        return {key: transform_all_string_values_sync(val, transform) for key, val in node.items()}
    return node


async def transform_field(obj: Any, key: str, transform: Transform) -> None:
    """In-place: if ``obj[key]`` is a string, replace it with its transform."""
    if isinstance(obj, dict) and isinstance(obj.get(key), str):
        obj[key] = await transform(obj[key])


async def transform_text_fields(obj: Any, keys: frozenset[str], transform: Transform) -> None:
    """In-place: transform each string value stored under any key in ``keys``."""
    if isinstance(obj, dict):
        for key in keys:
            await transform_field(obj, key, transform)
