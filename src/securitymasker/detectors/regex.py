"""User-defined and built-in regex detector (§11 step 3-4).

Matches on normalized text and maps spans back to the original. An entry may point
at a capture group so only the sensitive sub-span is masked (e.g. the password in a
basic-auth URL), rather than the whole match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult

# Bound the scan to avoid pathological regex cost on huge inputs (§32, §33).
_MAX_SCAN_CHARS = 2_000_000


@dataclass(frozen=True)
class RegexEntry:
    pattern: str
    entity_type: str
    replacement_profile: str
    restore_policy: str
    priority: int = 150
    score: float = 0.8
    group: int = 0
    flags: int = 0


class RegexDetector:
    def __init__(self, entries: list[RegexEntry], *, name: str = "regex") -> None:
        self.name = name
        self._compiled = [(re.compile(e.pattern, e.flags), e) for e in entries]

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        if len(text) > _MAX_SCAN_CHARS:
            text = text[:_MAX_SCAN_CHARS]
        results: list[DetectionResult] = []
        for rx, entry in self._compiled:
            for m in rx.finditer(text):
                try:
                    n_start, n_end = m.span(entry.group)
                except IndexError as exc:  # pragma: no cover - config guard
                    raise ValueError(
                        f"regex group {entry.group} out of range for {entry.pattern!r}"
                    ) from exc
                if n_end <= n_start:
                    continue
                o_start, o_end = context.norm.to_original_span(n_start, n_end)
                results.append(
                    DetectionResult(
                        entity_type=entry.entity_type,
                        start=o_start,
                        end=o_end,
                        score=entry.score,
                        detector=self.name,
                        context_kind=context.context_kind,
                        replacement_profile=entry.replacement_profile,
                        restore_policy=entry.restore_policy,
                        original_value=context.norm.original[o_start:o_end],
                        normalized_value=text[n_start:n_end],
                        metadata={"priority": entry.priority},
                    )
                )
        return results
