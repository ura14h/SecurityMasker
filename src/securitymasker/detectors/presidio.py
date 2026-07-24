"""Presidio Analyzer adapter — Presidio as a *detector only* (§13).

In-process (ADR-0004), import-guarded: if ``presidio-analyzer`` (and its language
model) is not installed, this detector cleanly no-ops so the rest of the pipeline
still runs. SecurityMasker owns aliasing/restoration; from Presidio we take only
entity type, span, and score (§13). Install with the ``presidio`` extra and a spaCy
model to enable.
"""

from __future__ import annotations

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# Map Presidio entity labels -> (our EntityType, replacement profile, restore policy).
_MAP: dict[str, tuple[str, str, str]] = {
    "PERSON": (EntityType.PERSON.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "LOCATION": (EntityType.JP_ADDRESS.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "ORGANIZATION": (EntityType.ORGANIZATION.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "EMAIL_ADDRESS": (EntityType.EMAIL.value, ReplacementProfile.EMAIL.value, RestorePolicy.LITERAL.value),
    "PHONE_NUMBER": (EntityType.PHONE.value, ReplacementProfile.NUMERIC.value, RestorePolicy.LITERAL.value),
    "IP_ADDRESS": (EntityType.IP_ADDRESS.value, ReplacementProfile.IPV4.value, RestorePolicy.LITERAL.value),
    "CREDIT_CARD": (EntityType.CREDIT_CARD.value, ReplacementProfile.NUMERIC.value, RestorePolicy.BLOCK.value),
}


class PresidioDetector:
    name = "presidio"

    def __init__(
        self,
        *,
        language: str = "ja",
        min_score: float = 0.5,
        entities: tuple[str, ...] | None = None,
    ) -> None:
        self._language = language
        self._min_score = min_score
        self._entities = list(entities) if entities else None
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            self.available = True
        except Exception:  # noqa: BLE001 - optional dependency / model missing
            self._analyzer = None
            self.available = False

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not self.available or self._analyzer is None:
            return []
        text = context.norm.normalized
        try:
            hits = self._analyzer.analyze(
                text=text, language=self._language, entities=self._entities
            )
        except Exception:  # noqa: BLE001 - never fail the pipeline on analyzer error
            return []
        results: list[DetectionResult] = []
        for h in hits:
            if h.score < self._min_score or h.entity_type not in _MAP:
                continue
            etype, profile, policy = _MAP[h.entity_type]
            o_start, o_end = context.norm.to_original_span(h.start, h.end)
            results.append(
                DetectionResult(
                    entity_type=etype,
                    start=o_start,
                    end=o_end,
                    score=float(h.score),
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=profile,
                    restore_policy=policy,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=text[h.start : h.end],
                    metadata={"priority": 120, "recognizer": h.entity_type},
                )
            )
        return results
