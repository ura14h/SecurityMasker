"""protocol adapterが共有するhelper。

Adapters mutate the request/response dicts in place, touching only text-bearing
value fields and never structural keys (§16). The transform used for masking is the
engine's async ``mask_text``; for restoration it is the engine's sync restorer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from securitymasker.models import DetectionResult


class MaskTransform(Protocol):
    """非同期masking変換。``kind``で検出contextを選ぶ（§17）。"""

    async def __call__(self, text: str, kind: str = ...) -> str: ...


RestoreTransform = Callable[[str], str]


@dataclass
class MaskingSummary:
    """一request内で実際にmask対象として検出したentity件数。"""

    entity_counts: Counter[str] = field(default_factory=Counter)

    def add(self, detections: list[DetectionResult]) -> None:
        self.entity_counts.update(item.entity_type for item in detections)

# OpenAI／Anthropic message構造でuser textを保持するcontent-part key。
TEXT_KEYS = frozenset({"text", "input_text", "output_text"})

# modelがplaceholderをverbatimに保持するよう任意で注入する指示。
# restoration fidelity (doc/06 P1-2 inject_alias_instruction). Contains no secrets.
ALIAS_INSTRUCTION = (
    "Note: tokens such as SM_PERSON_XXXXXX, sm-host-xxxxxx.example.invalid, and "
    "${SECURITYMASKER_SECRET_XXXXXX} are placeholders inserted by a security proxy. "
    "Treat them as opaque identifiers: preserve them exactly and never alter, "
    "translate, decode, or invent such tokens."
)
