"""Japanese NER adapter (§14.1) — pluggable model, disabled unless configured.

The model is configurable and never hardcoded (§14.1): pass a Hugging Face
token-classification model id. Import-guarded on ``transformers``; if it or the
model is unavailable, this cleanly no-ops. In code contexts, unregistered-name NER
is conservative by default (§17): callers pass a higher ``min_score`` and may skip
this detector for code (handled by the pipeline builder).

NER alone must not make high-confidence PERSON calls for ambiguous words (さくら/
葵…); context words raise the score (§14.1).
"""

from __future__ import annotations

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.errors import ConfigError, DetectionError
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_LABEL_MAP: dict[str, str] = {
    "PER": EntityType.PERSON.value,
    "PERSON": EntityType.PERSON.value,
    "ORG": EntityType.ORGANIZATION.value,
    "ORGANIZATION": EntityType.ORGANIZATION.value,
    "LOC": EntityType.JP_ADDRESS.value,
    "LOCATION": EntityType.JP_ADDRESS.value,
}
_PERSON_CONTEXT = (
    "氏名", "名前", "契約者", "申込者", "担当者", "代表者", "連絡先", "お客様", "患者", "従業員", "さん", "様",
)


class JapaneseNerDetector:
    name = "jp_ner"

    def __init__(
        self, *, model: str | None = None, min_score: float = 0.85, required: bool = False
    ) -> None:
        self._min_score = min_score
        self._pipeline = None
        self.available = False
        if not model:
            return  # no model configured -> disabled (do not hardcode a model)
        try:
            from transformers import pipeline

            self._pipeline = pipeline("token-classification", model=model, aggregation_strategy="simple")
            self.available = True
        except Exception as exc:  # noqa: BLE001 - optional dependency / model missing
            self._pipeline = None
            self.available = False
            if required:
                # Model requested in config but unusable => fail startup (doc/06 P0-6).
                raise ConfigError(
                    f"ner.model={model!r} is configured but the pipeline could not "
                    f"load ({type(exc).__name__}); install 'transformers' and the "
                    f"model, or clear ner.model."
                ) from exc

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not self.available or self._pipeline is None:
            return []
        text = context.norm.normalized
        try:
            entities = self._pipeline(text)
        except Exception as exc:  # noqa: BLE001
            raise DetectionError(
                f"jp_ner pipeline failed at runtime: {type(exc).__name__}"
            ) from exc
        results: list[DetectionResult] = []
        for ent in entities:
            label = str(ent.get("entity_group") or ent.get("entity") or "").upper()
            etype = _LABEL_MAP.get(label)
            score = float(ent.get("score", 0.0))
            if etype is None or score < self._min_score:
                continue
            start, end = int(ent["start"]), int(ent["end"])
            # Reduce false positives for ambiguous person names without context.
            if etype == EntityType.PERSON.value and not has_context(text, start, end, _PERSON_CONTEXT):
                score *= 0.8
                if score < self._min_score:
                    continue
            o_start, o_end = context.norm.to_original_span(start, end)
            results.append(
                DetectionResult(
                    entity_type=etype,
                    start=o_start,
                    end=o_end,
                    score=score,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=RestorePolicy.LITERAL.value,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=text[start:end],
                    metadata={"priority": 90, "ner_label": label},
                )
            )
        return results
