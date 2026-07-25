"""Presidio Analyzer adapter — Presidio as a *detector only* (§13).

In-process (ADR-0004), import-guarded: if ``presidio-analyzer`` or the configured
spaCy model is not installed, this detector cleanly no-ops so the rest of the
pipeline still runs. SecurityMasker owns aliasing/restoration; from Presidio we take
only entity type, span, and score (§13).

The NlpEngine is configured for the requested language (default Japanese via
``ja_core_news_md``). NER is the least-trusted signal, so detections get a low
priority (dictionary and deterministic recognizers win overlaps, §40-10) and are
skipped in code-like contexts by default (§17). Install: ``pip install -e
'.[presidio]'`` then ``python -m spacy download ja_core_news_md``.
"""

from __future__ import annotations

from typing import Any

from securitymasker.detectors.base import DetectionContext
from securitymasker.errors import ConfigError, DetectionError
from securitymasker.models import (
    ContextKind,
    DetectionResult,
    EntityType,
    ReplacementProfile,
    RestorePolicy,
)

# Map Presidio entity labels -> (our EntityType, replacement profile, restore policy).
_MAP: dict[str, tuple[str, str, str]] = {
    "PERSON": (EntityType.PERSON.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "LOCATION": (EntityType.JP_ADDRESS.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "ORGANIZATION": (EntityType.ORGANIZATION.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "NRP": (EntityType.ORGANIZATION.value, ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value),
    "EMAIL_ADDRESS": (EntityType.EMAIL.value, ReplacementProfile.EMAIL.value, RestorePolicy.LITERAL.value),
    "PHONE_NUMBER": (EntityType.PHONE.value, ReplacementProfile.NUMERIC.value, RestorePolicy.LITERAL.value),
    "IP_ADDRESS": (EntityType.IP_ADDRESS.value, ReplacementProfile.IPV4.value, RestorePolicy.LITERAL.value),
    "CREDIT_CARD": (EntityType.CREDIT_CARD.value, ReplacementProfile.NUMERIC.value, RestorePolicy.BLOCK.value),
}

# NER is unreliable in code (§17): skip these context kinds by default.
_CODE_CONTEXTS = frozenset({
    ContextKind.SOURCE_CODE.value,
    ContextKind.MARKDOWN_CODE.value,
    ContextKind.SHELL.value,
    ContextKind.JSON_STRING.value,
    ContextKind.YAML_SCALAR.value,
})


class PresidioDetector:
    name = "presidio"

    def __init__(
        self,
        *,
        language: str = "ja",
        model_name: str = "ja_core_news_md",
        min_score: float = 0.5,
        entities: tuple[str, ...] | None = None,
        skip_code_contexts: bool = True,
        required: bool = False,
    ) -> None:
        self._language = language
        self._min_score = min_score
        self._entities = list(entities) if entities else None
        self._skip_code = skip_code_contexts
        self._required = required
        self._analyzer: Any = None
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            nlp_engine = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": language, "model_name": model_name}],
                }
            ).create_engine()
            self._analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine, supported_languages=[language]
            )
            self.available = True
        # spaCy's loader raises SystemExit (via wasabi) on a missing model, so we
        # must catch that too and convert it into a clean fail-closed outcome.
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            self.available = False
            if required:
                # Enabled in config but unusable => fail startup, never silently
                # no-op (doc/06 P0-6). Message names the model, not any input.
                raise ConfigError(
                    f"presidio is enabled but could not load (language={language!r}, "
                    f"model={model_name!r}): {type(exc).__name__}. Install "
                    f"'.[presidio]' and the spaCy model, or disable presidio."
                ) from exc

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not self.available or self._analyzer is None:
            return []
        if self._skip_code and context.context_kind in _CODE_CONTEXTS:
            return []
        text = context.norm.normalized
        try:
            hits = self._analyzer.analyze(
                text=text, language=self._language, entities=self._entities
            )
        except Exception as exc:  # noqa: BLE001
            # A loaded detector that fails at runtime is a fail-closed event: block
            # rather than under-scan and forward (doc/06 P0-6).
            raise DetectionError(
                f"presidio analyzer failed at runtime: {type(exc).__name__}"
            ) from exc
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
                    # Least-trusted signal: lowest priority so dictionary and
                    # deterministic recognizers win overlaps (§40-10).
                    metadata={"priority": 80, "recognizer": h.entity_type},
                )
            )
        return results
