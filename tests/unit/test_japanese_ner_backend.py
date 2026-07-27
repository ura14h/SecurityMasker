"""日本語NER backendのschema検証と安全な無効化を検証する。

The model itself is optional and not present in CI, so these tests drive the
detector with stub pipelines. That is the point: what must be verified is not the
model's accuracy (measured separately in tests/evaluation/ner_benchmark.py) but
the ways a NER backend fails SILENTLY — an unknown label schema, missing offsets,
a runtime fault — each of which would otherwise look like a clean, empty run.
"""

from __future__ import annotations

import logging

import pytest

from securitymasker.detectors import japanese_ner as mod
from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.japanese_ner import (
    JapaneseNerDetector,
    UnsupportedLabelSchemaError,
    _coarse,
    _CpuDeviceNoticeFilter,
    _suppress_cpu_device_notice,
)
from securitymasker.errors import DetectionError
from securitymasker.models import ContextKind, EntityType
from securitymasker.normalization import normalize


def ctx(text: str, kind: str = ContextKind.PROSE.value) -> DetectionContext:
    return DetectionContext(norm=normalize(text, "nfkc"), context_kind=kind)


def test_only_redundant_cpu_device_notice_is_suppressed() -> None:
    logger = logging.getLogger("transformers.pipelines.base")
    existing_filters = tuple(logger.filters)
    notice = logging.LogRecord(
        logger.name,
        logging.WARNING,
        __file__,
        1,
        "Device set to use cpu",
        (),
        None,
    )
    important = logging.LogRecord(
        logger.name,
        logging.WARNING,
        __file__,
        1,
        "model artifact is incomplete",
        (),
        None,
    )

    with _suppress_cpu_device_notice():
        installed = [
            item for item in logger.filters if isinstance(item, _CpuDeviceNoticeFilter)
        ]
        assert len(installed) == 1
        assert installed[0].filter(notice) is False
        assert installed[0].filter(important) is True

    assert tuple(logger.filters) == existing_filters


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
    # inherit an address's sensitivity.
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


# --- long input must not lose its tail -------------------------------------------
#
# A 512-token model does not raise when given more: the pipeline classifies the
# prefix and returns nothing for the rest, which is indistinguishable from a clean
# scan. These pin the windowing that prevents it. The offline tests use a stub, so
# they run anywhere; the real-model test skips when the pinned snapshot is absent.


class _WindowStub:
    """Reports one entity per occurrence, so a dropped window is visible."""

    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.calls: list[str] = []

    def __call__(self, text: str):
        self.calls.append(text)
        return [{"entity_group": "人名", "score": 0.99, "word": self.needle,
                 "start": at, "end": at + len(self.needle)}
                for at in _occurrences(text, self.needle)]


def _occurrences(text: str, needle: str) -> list[int]:
    out, at = [], text.find(needle)
    while at != -1:
        out.append(at)
        at = text.find(needle, at + 1)
    return out


def _detector_with(pipeline) -> mod.JapaneseNerDetector:
    detector = mod.JapaneseNerDetector()
    detector._pipeline = pipeline
    detector.available = True
    detector._min_score = 0.5
    return detector


def test_character_windows_cover_every_character() -> None:
    """No gap between windows: a name in one would be seen by nobody."""
    detector = _detector_with(_WindowStub("X"))
    text = "あ" * 5000
    covered: set[int] = set()
    for offset, chunk in detector._windows(text):
        covered |= set(range(offset, offset + len(chunk)))
    assert covered == set(range(len(text)))


def test_windows_overlap_so_a_boundary_name_is_not_split() -> None:
    detector = _detector_with(_WindowStub("X"))
    windows = detector._windows("あ" * 5000)
    assert len(windows) > 1
    for (start_a, chunk_a), (start_b, _) in zip(windows, windows[1:], strict=False):
        assert start_b < start_a + len(chunk_a), "consecutive windows do not overlap"


def test_duplicate_entities_from_the_overlap_are_collapsed() -> None:
    entities = [{"entity_group": "人名", "score": 0.8, "start": 3, "end": 8},
                {"entity_group": "人名", "score": 0.95, "start": 3, "end": 8},
                {"entity_group": "人名", "score": 0.9, "start": 20, "end": 25}]
    deduped = mod._dedupe(entities)
    assert len(deduped) == 2
    assert deduped[0]["score"] == 0.95, "the more confident window should win"


@pytest.mark.parametrize("position", ["head", "middle", "tail"])
def test_the_real_model_finds_a_name_anywhere_in_a_long_document(position) -> None:
    """The measured behaviour, pinned. Before windowing, 'tail' found nothing."""
    pytest.importorskip("transformers")
    import asyncio

    from securitymasker.models_fetch import cache_directory

    model = "tsmatz/xlm-roberta-ner-japanese"
    revision = "aba094e118d5ffc622e9b25e07edc49f9dd85feb"
    if cache_directory(model, revision) is None:
        pytest.skip("pinned model not cached (run: securitymasker model-load)")

    detector = mod.JapaneseNerDetector(model=model, revision=revision, required=True)
    filler, sentence = "これはテスト文章です。" * 150, "担当者は佐々木健一です。"
    text = {"head": sentence + filler + filler,
            "middle": filler + sentence + filler,
            "tail": filler + filler + sentence}[position]

    norm = normalize(text, "nfkc")
    results = asyncio.run(detector.detect(DetectionContext(norm=norm)))
    assert any(r.original_value == "佐々木健一" for r in results), (
        f"the name was lost when placed at the {position} of a "
        f"{len(text)}-character document"
    )
