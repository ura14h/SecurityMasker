"""Detector protocol and shared context (§15).

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


@runtime_checkable
class SensitiveDataDetector(Protocol):
    name: str

    async def detect(self, context: DetectionContext) -> list[DetectionResult]: ...
