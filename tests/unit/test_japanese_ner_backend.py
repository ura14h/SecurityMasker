"""Japanese NER backend safety (ADR-0009, doc/06 §5.3).

The model itself is optional and not present in CI, so these tests drive the
detector with stub pipelines. That is the point: what must be verified is not the
model's accuracy (measured separately in tests/evaluation/ner_benchmark.py) but
the ways a NER backend fails SILENTLY — an unknown label schema, missing offsets,
a runtime fault — each of which would otherwise look like a clean, empty run.
"""

from __future__ import annotations

import pytest

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.japanese_ner import (
    JapaneseNerDetector,
    UnsupportedLabelSchemaError,
    _coarse,
)
from securitymasker.errors import DetectionError
from securitymasker.models import ContextKind, EntityType
from securitymasker.normalization import normalize


def ctx(text: str, kind: str = ContextKind.PROSE.value) -> DetectionContext:
    return DetectionContext(norm=normalize(text, "nfkc"), context_kind=kind)


class _StubPipeline:
    """Minimal stand-in for a transformers token-classification pipeline."""

    def __init__(self, labels, entities):
        self.model = type("M", (), {"config": type("C", (), {"id2label": dict(enumerate(labels))})()})()
        self._entities = entities

    def __call__(self, text):
        return list(self._entities)


def _detector(labels, entities, **kw) -> JapaneseNerDetector:
    det = JapaneseNerDetector(**kw)
    det._pipeline = _StubPipeline(labels, entities)
    return det


# --- label normalisation -----------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ("PER", "PER"), ("B-PER", "PER"), ("I-ORG", "ORG"), ("per", "PER"),
    ("人名", "人名"), ("B-人名", "人名"), ("ORG-P", "ORG-P"),
])
def test_coarse_strips_bio_prefix_and_normalises_case(raw, expected) -> None:
    assert _coarse(raw) == expected


# --- schema validation: the silent-failure guard ------------------------------------


def test_unmappable_label_schema_is_refused() -> None:
    # A model whose labels we cannot map returns detections we would discard —
    # under-detection that reads as a clean run, i.e. a leak wearing a green tick.
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(["B-完全に不明なラベル", "O"], [])
    with pytest.raises(UnsupportedLabelSchemaError):
        det._validate_label_schema("stub/model", required=True)


def test_unmappable_schema_disables_detector_when_not_required() -> None:
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(["B-謎", "O"], [])
    det._validate_label_schema("stub/model", required=False)
    assert det._pipeline is None       # disabled rather than silently useless


def test_japanese_label_schema_is_supported() -> None:
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(["B-人名", "B-法人名", "B-地名", "O"], [])
    det._validate_label_schema("stub/luke-like", required=True)   # no raise
    assert det._pipeline is not None


def test_english_label_schema_is_supported() -> None:
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(["PER", "ORG", "ORG-P", "LOC", "EVT", "PRD", "O"], [])
    det._validate_label_schema("stub/xlmr-like", required=True)
    assert det.unmapped_labels == ()   # EVT/PRD are known-and-ignored, not unknown


def test_unknown_labels_are_recorded_not_silently_dropped() -> None:
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(["PER", "B-謎ラベル", "O"], [])
    det._validate_label_schema("stub/model", required=True)
    assert "謎ラベル" in det.unmapped_labels


# --- offsets: masking needs spans, not just labels -----------------------------------


def test_model_without_character_offsets_is_refused() -> None:
    # Some tokenizers report start/end as None; without a span we cannot say WHICH
    # characters to replace, and the old code crashed mid-request instead.
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(
        ["PER", "O"], [{"entity_group": "PER", "word": "x", "score": 0.99,
                        "start": None, "end": None}])
    with pytest.raises(UnsupportedLabelSchemaError):
        det._validate_offsets("stub/no-offsets", required=True)
    assert det._pipeline is None


def test_model_with_offsets_passes_validation() -> None:
    det = JapaneseNerDetector()
    det._pipeline = _StubPipeline(
        ["PER", "O"], [{"entity_group": "PER", "word": "佐々木健一", "score": 0.99,
                        "start": 4, "end": 9}])
    det._validate_offsets("stub/ok", required=True)
    assert det._pipeline is not None


# --- detection behaviour --------------------------------------------------------------


@pytest.mark.asyncio
async def test_detects_person_with_context_word() -> None:
    det = _detector(["PER", "O"],
                    [{"entity_group": "PER", "word": "佐々木健一", "score": 0.95,
                      "start": 4, "end": 9}])
    det.available = True
    hits = await det.detect(ctx("担当者は佐々木健一です。"))
    assert len(hits) == 1
    assert hits[0].entity_type == EntityType.PERSON.value
    assert hits[0].original_value == "佐々木健一"


@pytest.mark.asyncio
async def test_japanese_labels_map_to_our_entity_types() -> None:
    det = _detector(["B-法人名", "O"],
                    [{"entity_group": "法人名", "word": "株式会社桜庭電機", "score": 0.95,
                      "start": 0, "end": 8}])
    det.available = True
    hits = await det.detect(ctx("株式会社桜庭電機と契約しました。"))
    assert hits and hits[0].entity_type == EntityType.ORGANIZATION.value


@pytest.mark.asyncio
async def test_location_is_not_treated_as_a_personal_address() -> None:
    # Conflating a city with somebody's home address would let a coarse hit
    # inherit an address's sensitivity (ADR-0009).
    det = _detector(["LOC", "O"],
                    [{"entity_group": "LOC", "word": "神奈川県横浜市", "score": 0.99,
                      "start": 3, "end": 10}])
    det.available = True
    hits = await det.detect(ctx("会場は神奈川県横浜市です。"))
    assert hits[0].entity_type == EntityType.LOCATION.value
    assert hits[0].entity_type != EntityType.JP_ADDRESS.value


@pytest.mark.asyncio
async def test_ambiguous_person_without_context_is_dropped() -> None:
    det = _detector(["PER", "O"],
                    [{"entity_group": "PER", "word": "さくら", "score": 0.80,
                      "start": 0, "end": 3}])
    det.available = True
    # 0.80 * 0.8 (no context word) = 0.64 < min_score 0.7 -> dropped.
    assert await det.detect(ctx("さくらの開花予想")) == []


@pytest.mark.asyncio
async def test_below_threshold_hits_are_dropped() -> None:
    det = _detector(["PER", "O"],
                    [{"entity_group": "PER", "word": "佐々木健一", "score": 0.40,
                      "start": 4, "end": 9}])
    det.available = True
    assert await det.detect(ctx("担当者は佐々木健一です。")) == []


@pytest.mark.asyncio
async def test_skips_code_contexts_by_default() -> None:
    det = _detector(["PER", "O"],
                    [{"entity_group": "PER", "word": "Sakura", "score": 0.99,
                      "start": 6, "end": 12}])
    det.available = True
    assert await det.detect(ctx("class SakuraService:", ContextKind.SOURCE_CODE.value)) == []


@pytest.mark.asyncio
async def test_runtime_failure_fails_closed() -> None:
    class _Boom(_StubPipeline):
        def __call__(self, text):
            raise RuntimeError("inference exploded")

    det = JapaneseNerDetector()
    det._pipeline = _Boom(["PER", "O"], [])
    det.available = True
    with pytest.raises(DetectionError):
        await det.detect(ctx("担当者は佐々木健一です。"))


@pytest.mark.asyncio
async def test_unconfigured_detector_is_disabled_not_broken() -> None:
    det = JapaneseNerDetector(model=None)
    assert det.available is False
    assert await det.detect(ctx("担当者は佐々木健一です。")) == []


# --- configuration pinning ---------------------------------------------------------


def test_model_without_revision_is_rejected_by_config() -> None:
    from pydantic import ValidationError

    from securitymasker.config import SecurityMaskerConfig
    from securitymasker.errors import ConfigError

    with pytest.raises((ValidationError, ConfigError)):
        SecurityMaskerConfig.model_validate(
            {"version": 1, "ner": {"model": "some/model"}})


def test_model_with_revision_is_accepted() -> None:
    from securitymasker.config import SecurityMaskerConfig

    config = SecurityMaskerConfig.model_validate(
        {"version": 1, "ner": {"model": "some/model", "revision": "abc123"}})
    assert config.ner.revision == "abc123"
    assert config.ner.local_files_only is True     # never fetches at request time


def test_ner_is_off_by_default() -> None:
    from securitymasker.config import SecurityMaskerConfig

    config = SecurityMaskerConfig.model_validate({"version": 1})
    assert config.ner.model is None
