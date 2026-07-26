"""detector Protocolと共有context（§15）。

Detectors run over the NFKC/NFC-normalized text and return ``DetectionResult``s in
**original** coordinates (they map spans back via ``context.norm``). This keeps
detection robust to width/compatibility variants while replacement stays on the
original surface form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from securitymasker.models import ContextKind, DetectionResult
from securitymasker.normalization import NormalizedText


@dataclass(frozen=True)
class DetectionContext:
    norm: NormalizedText
    context_kind: str = ContextKind.PROSE.value
    request_id: str | None = None
    # 現在のsessionで実際に発行したalias。既存alias detectorが利用する。
    # only protects a token if it is in this set, so an alias-shaped string that
    # was never issued here (another session's, an expired one, or a secret that
    # merely looks like an alias) is not auto-protected (doc/06 P0-7).
    issued_aliases: frozenset[str] = frozenset()


@runtime_checkable
class SensitiveDataDetector(Protocol):
    name: str

    async def detect(self, context: DetectionContext) -> list[DetectionResult]: ...
