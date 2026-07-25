"""Japanese NER via a pinned Hugging Face token-classification model (§14.1, ADR-0009).

Optional and OFF by default. The dictionary and the deterministic recognizers are
the trusted layer; this exists only to catch what is not registered — an
unfamiliar person, company or place name — and it never gets to be the reason we
call something safe (invariant 9).

Safety properties that are not negotiable here, because a NER backend fails in
ways that are silent:

- **Label schema is validated at load.** Models disagree wildly: this one emits
  ``PER``/``ORG``/``LOC``, another emits ``人名``/``法人名``/``地名``. During the
  ADR-0009 comparison a model whose labels we did not know produced *zero*
  detections and looked like a clean run — under-detection that reads as success
  is exactly how a leak hides. So the mapping is checked against the model's own
  ``id2label`` at construction, and a model with no mappable label is refused.
- **Unknown labels are surfaced, not ignored.** Labels we cannot map are recorded
  and reported once, rather than silently dropped per-token.
- **Pinned.** The model id, its commit revision, and the expected file digests are
  configuration, and the runtime loads ``local_files_only`` so a request never
  triggers a download and user text never reaches the Hub.
- **No remote code.** ``trust_remote_code`` is never set; only safetensors
  weights are accepted.
- **Off the event loop.** Inference is synchronous and CPU-bound, so it runs in a
  worker thread; the engine's per-detector timeout then actually bounds it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.errors import ConfigError, DetectionError
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# Coarse labels across the schemas we support. Kept explicit rather than fuzzy
# matched: guessing what an unknown label means is how a model silently mislabels
# a person as a product (ADR-0009).
_LABEL_MAP: dict[str, str] = {
    # English/ISO style (tsmatz/xlm-roberta-ner-japanese and most CoNLL models)
    "PER": EntityType.PERSON.value,
    "PERSON": EntityType.PERSON.value,
    "ORG": EntityType.ORGANIZATION.value,
    "ORG-P": EntityType.ORGANIZATION.value,      # political organisation
    "ORG-O": EntityType.ORGANIZATION.value,      # other organisation
    "ORGANIZATION": EntityType.ORGANIZATION.value,
    "INS": EntityType.ORGANIZATION.value,        # institution
    "LOC": EntityType.LOCATION.value,
    "LOCATION": EntityType.LOCATION.value,
    "GPE": EntityType.LOCATION.value,
    # Japanese label sets (LUKE-style 拡張固有表現)
    "人名": EntityType.PERSON.value,
    "法人名": EntityType.ORGANIZATION.value,
    "政治的組織名": EntityType.ORGANIZATION.value,
    "その他の組織名": EntityType.ORGANIZATION.value,
    "施設名": EntityType.LOCATION.value,
    "地名": EntityType.LOCATION.value,
}

# Labels we deliberately do NOT treat as personal data: an event, a product, or a
# generic "outside" tag. Listed so they count as *known* and do not trip the
# "unmappable schema" guard.
_IGNORED_LABELS = frozenset({"O", "EVT", "PRD", "イベント名", "製品名", "その他"})

_PERSON_CONTEXT = (
    "氏名", "名前", "契約者", "申込者", "担当者", "代表者", "連絡先", "お客様", "患者", "従業員", "さん", "様",
)


def _coarse(label: str) -> str:
    """Strip a BIO prefix and normalise case for lookup."""
    text = label.strip()
    if len(text) > 2 and text[1] == "-" and text[0] in "BILUES":
        text = text[2:]
    return text if any(ord(c) > 127 for c in text) else text.upper()


class UnsupportedLabelSchemaError(ConfigError):
    """The model's labels cannot be mapped, so its output would be meaningless."""


class JapaneseNerDetector:
    name = "jp_ner"

    def __init__(
        self,
        *,
        model: str | None = None,
        revision: str | None = None,
        min_score: float = 0.85,
        required: bool = False,
        skip_code_contexts: bool = True,
        local_files_only: bool = True,
    ) -> None:
        # Fuzzy NER opts out of code-like spans (§17); the dictionary and the
        # deterministic detectors keep running there.
        self.skip_code_contexts = skip_code_contexts
        self._min_score = min_score
        self._pipeline: Any = None
        self.available = False
        self.unmapped_labels: tuple[str, ...] = ()
        if not model:
            return  # not configured -> disabled (a model is never hardcoded)

        try:
            from transformers import (
                AutoModelForTokenClassification,
                AutoTokenizer,
                pipeline,
            )
        except ImportError as exc:
            if required:
                raise ConfigError(
                    f"ner.model={model!r} is configured but 'transformers' is not "
                    "installed (pip install -e '.[ner]')."
                ) from exc
            return

        try:
            # Load the weights and tokenizer EXPLICITLY rather than letting
            # `pipeline()` resolve them: this is where `revision` pins the exact
            # commit and `local_files_only` guarantees no network access at request
            # time. `pipeline()` itself does not accept local_files_only.
            load_kwargs: dict[str, Any] = {
                "revision": revision,
                "local_files_only": local_files_only,
                # Never execute model-supplied code (§ supply chain).
                "trust_remote_code": False,
            }
            # transformers ships no stubs for this factory; the kwargs are checked
            # by the explicit `load_kwargs` dict above.
            tokenizer = AutoTokenizer.from_pretrained(model, **load_kwargs)  # type: ignore[no-untyped-call]
            weights = AutoModelForTokenClassification.from_pretrained(model, **load_kwargs)
            self._pipeline = pipeline(
                "token-classification",
                model=weights,
                tokenizer=tokenizer,
                aggregation_strategy="simple",
            )
        except Exception as exc:  # noqa: BLE001 - model missing/corrupt/incompatible
            self._pipeline = None
            if required:
                raise ConfigError(
                    f"ner.model={model!r} (revision={revision or 'unpinned'}) could not "
                    f"be loaded ({type(exc).__name__}). Fetch it first with "
                    "'securitymasker models fetch', or clear ner.model."
                ) from exc
            return

        self._validate_label_schema(model, required)
        self._validate_offsets(model, required)
        self.available = self._pipeline is not None

    def _validate_label_schema(self, model: str, required: bool) -> None:
        """Refuse a model whose labels we cannot interpret (ADR-0009).

        A model with an unknown schema returns detections we drop on the floor,
        which looks exactly like a clean run. For a masking proxy that is a leak
        wearing a green tick, so it fails at startup instead.
        """
        labels = {
            _coarse(label)
            for label in getattr(self._pipeline.model.config, "id2label", {}).values()
        }
        mappable = {label for label in labels if label in _LABEL_MAP}
        unknown = labels - mappable - _IGNORED_LABELS
        self.unmapped_labels = tuple(sorted(unknown))
        if not mappable:
            self._pipeline = None
            message = (
                f"ner.model={model!r} exposes no label this build can map "
                f"(labels: {sorted(labels)[:8]}). Its output would be discarded "
                "silently, so it is refused."
            )
            if required:
                raise UnsupportedLabelSchemaError(message)

    def _validate_offsets(self, model: str, required: bool) -> None:
        """Refuse a model whose tokenizer cannot report character offsets.

        Masking needs a span, not just a label: without ``start``/``end`` we cannot
        say WHICH characters to replace. Some tokenizers (LUKE's, for one) return
        ``None`` offsets, which produced a crash mid-request rather than a clean
        refusal. A one-off synthetic probe at load turns that into a startup
        failure (ADR-0009). The probe text contains no real data (§30).
        """
        if self._pipeline is None:
            return
        try:
            probe = self._pipeline("担当者は佐々木健一です。")
        except Exception:  # noqa: BLE001 - treated the same as unusable output
            probe = []
        usable = any(
            ent.get("start") is not None and ent.get("end") is not None for ent in probe
        )
        if usable:
            return
        self._pipeline = None
        message = (
            f"ner.model={model!r} does not report character offsets, so its "
            "detections cannot be mapped to text spans and nothing could be "
            "masked from them. Use a model with a fast/offset-capable tokenizer."
        )
        if required:
            raise UnsupportedLabelSchemaError(message)

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not self.available or self._pipeline is None:
            return []
        if self.skip_code_contexts and _is_code_like(context.context_kind):
            return []
        text = context.norm.normalized
        try:
            # Synchronous, CPU-bound inference: run it off the event loop so one
            # request cannot stall every other connection (doc/06 P1-5).
            entities = await asyncio.to_thread(self._pipeline, text)
        except Exception as exc:  # noqa: BLE001
            raise DetectionError(
                f"jp_ner pipeline failed at runtime: {type(exc).__name__}"
            ) from exc

        results: list[DetectionResult] = []
        for ent in entities:
            label = _coarse(str(ent.get("entity_group") or ent.get("entity") or ""))
            etype = _LABEL_MAP.get(label)
            score = float(ent.get("score", 0.0))
            if etype is None or score < self._min_score:
                continue
            start, end = int(ent["start"]), int(ent["end"])
            # Ambiguous given names (さくら/葵/ひかり) need context to be credible.
            if etype == EntityType.PERSON.value and not has_context(
                text, start, end, _PERSON_CONTEXT
            ):
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
                    # Least-trusted signal: the dictionary and deterministic
                    # detectors win every overlap (§40-10, invariant 8).
                    metadata={"priority": 90, "ner_label": label},
                )
            )
        return results


def _is_code_like(kind: str) -> bool:
    # Imported lazily to keep the detector free of a package-level cycle.
    from securitymasker.context import is_code_like

    return is_code_like(kind)
