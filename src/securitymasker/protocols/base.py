"""protocol adapterが共有するhelper。

adapterはrequest/responseのdictをin-placeで処理するが、文字列値だけを変更し、構造keyは
変更しない。マスクにはengineの非同期変換を、復元には同期restorerを使う。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from securitymasker.errors import UnsupportedAttachmentError
from securitymasker.models import DetectionResult


class MaskTransform(Protocol):
    """非同期masking変換。``kind``で検出contextを選ぶ。"""

    async def __call__(self, text: str, kind: str = ...) -> str: ...


RestoreTransform = Callable[[str], str]


@dataclass
class MaskingSummary:
    """一request内で実際にmask対象として検出したentity件数。"""

    entity_counts: Counter[str] = field(default_factory=Counter)
    opaque_tokens: set[str] = field(default_factory=set)

    def add(self, detections: list[DetectionResult]) -> None:
        self.entity_counts.update(item.entity_type for item in detections)

# OpenAI／Anthropic message構造でuser textを保持するcontent-part key。
TEXT_KEYS = frozenset({"text", "input_text", "output_text"})


def reject_unsupported_attachments(
    node: Any,
    *,
    block_types: frozenset[str],
    reference_fields: frozenset[str],
) -> None:
    """添付content blockと外部file参照を再帰検出してfail-closedにする。

    base64、URL、file IDの内容は通常の文字列detectorでは完全検査できない。既知typeに加えて
    添付固有fieldも見ることで、providerが新しいenvelopeを追加しても黙って透過しない。
    """
    if isinstance(node, list):
        for item in node:
            reject_unsupported_attachments(
                item,
                block_types=block_types,
                reference_fields=reference_fields,
            )
        return
    if not isinstance(node, dict):
        return
    if node.get("type") in block_types or any(
        isinstance(node.get(field), str) for field in reference_fields
    ):
        raise UnsupportedAttachmentError
    for value in node.values():
        reject_unsupported_attachments(
            value,
            block_types=block_types,
            reference_fields=reference_fields,
        )


# modelがplaceholderをverbatimに保持するよう任意で注入する指示。
# restoration fidelity. Contains no secrets.
ALIAS_INSTRUCTION = (
    "Note: tokens such as SM_PERSON_XXXXXX, sm-host-xxxxxx.example.invalid, and "
    "${SECURITYMASKER_SECRET_XXXXXX} are placeholders inserted by a security proxy. "
    "Treat them as opaque identifiers: preserve them exactly and never alter, "
    "translate, decode, or invent such tokens."
)
