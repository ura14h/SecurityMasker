"""Segmentation must not become a way to hide text from the model detectors.

Two separate blind spots lived here, and both looked identical from outside — a
clean scan that found nothing:

1. The engine ran the full detector set on the first N spans only, so padding a
   request with inline-code spans pushed later prose out of NER's reach.
2. The NER detector handed its whole input to a 512-token model, which does not
   raise on overflow — it classifies the prefix and silently returns nothing for
   the rest.

Both are deterministic, so both were reachable on purpose. Synthetic data only:
the "name" is invented and already used elsewhere in the suite.
"""

from __future__ import annotations

import pytest

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.errors import DetectionError

NAME = "佐々木健一"


class _RecordingFuzzy:
    """Stands in for a model detector: same scheduling, records what it is shown."""

    name = "recording_fuzzy"
    fuzzy = True
    skip_code_contexts = True
    critical = False

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def detect(self, context):
        self.seen.append(context.norm.normalized)
        return []


def _engine_with_fuzzy(**defaults):
    engine = build_engine(
        SecurityMaskerConfig.model_validate({"version": 1, "defaults": defaults}))
    recorder = _RecordingFuzzy()
    engine._detectors = [*engine._detectors, recorder]
    return engine, recorder


def _padded(spans: int) -> str:
    """Prose split by inline code, with the name at the very end."""
    return " ".join(f"`tok{i}` prose{i}" for i in range(spans)) + f" 担当者は{NAME}です。"


# --- the engine must not stop scanning after a span budget -------------------------


@pytest.mark.parametrize("spans", [1, 10, 40, 100, 300])
@pytest.mark.asyncio
async def test_trailing_prose_reaches_the_fuzzy_pass_at_any_span_count(spans) -> None:
    engine, recorder = _engine_with_fuzzy()
    await engine.detect(_padded(spans))
    assert any(NAME in seen for seen in recorder.seen), (
        f"with {spans} padding spans the trailing name never reached the model "
        "detectors — segmentation hid it"
    )


@pytest.mark.asyncio
async def test_fuzzy_cost_does_not_grow_with_span_count() -> None:
    """One request-wide pass, however finely the text is chopped."""
    engine_few, few = _engine_with_fuzzy()
    engine_many, many = _engine_with_fuzzy()
    await engine_few.detect(_padded(5))
    await engine_many.detect(_padded(300))
    assert len(few.seen) == len(many.seen) == 1


@pytest.mark.asyncio
async def test_code_spans_are_still_withheld_from_the_fuzzy_pass() -> None:
    engine, recorder = _engine_with_fuzzy()
    await engine.detect(_padded(40))
    joined = "\n".join(recorder.seen)
    assert "tok0" not in joined and "tok39" not in joined


@pytest.mark.asyncio
async def test_over_the_limit_fails_closed_instead_of_scanning_a_prefix() -> None:
    # Refusing is the point: scanning only part of the text and reporting success
    # is what the old budget did, and the caller cannot tell that from a clean run.
    engine, recorder = _engine_with_fuzzy(max_fuzzy_chars=1000)
    with pytest.raises(DetectionError) as exc:
        await engine.detect("これはテスト文章です。" * 200 + f"担当者は{NAME}です。")
    assert "Refusing" in str(exc.value)
    assert NAME not in str(exc.value), "the error message quoted the text"


@pytest.mark.asyncio
async def test_detections_come_back_in_original_coordinates() -> None:
    """Offsets must survive the join, or the wrong characters get masked."""

    class _FindsName:
        name = "finds_name"
        fuzzy = True
        skip_code_contexts = True
        critical = False

        async def detect(self, context):
            from securitymasker.aliases.profiles import ReplacementProfile
            from securitymasker.models import DetectionResult, EntityType, RestorePolicy

            text = context.norm.normalized
            at = text.index(NAME)
            start, end = context.norm.to_original_span(at, at + len(NAME))
            return [DetectionResult(
                entity_type=EntityType.PERSON.value, start=start, end=end, score=0.99,
                detector=self.name, context_kind=context.context_kind,
                replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                restore_policy=RestorePolicy.LITERAL.value,
                original_value=text[at:at + len(NAME)],
                normalized_value=text[at:at + len(NAME)],
            )]

    engine = build_engine(SecurityMaskerConfig.model_validate({"version": 1}))
    engine._detectors = [*engine._detectors, _FindsName()]
    body = _padded(40)
    results = [r for r in await engine.detect(body) if r.detector == "finds_name"]
    assert results, "the fuzzy detection was lost on the way back"
    hit = results[0]
    assert body[hit.start:hit.end] == NAME, (
        f"offset mapping is wrong: got {body[hit.start:hit.end]!r}"
    )


# --- the detector's own skip_code_contexts setting must be honoured ----------------


class _RecordingFuzzyInCode(_RecordingFuzzy):
    """A model detector the operator has asked to scan code contexts too."""

    name = "recording_fuzzy_in_code"
    skip_code_contexts = False


def _engine_with(*detectors, **defaults):
    engine = build_engine(
        SecurityMaskerConfig.model_validate({"version": 1, "defaults": defaults}))
    engine._detectors = [*engine._detectors, *detectors]
    return engine


@pytest.mark.asyncio
async def test_skip_code_contexts_false_actually_sees_code() -> None:
    # The engine used to filter code-like spans out before consulting this flag,
    # so turning it off changed nothing — and it failed in the direction that
    # scans LESS than the operator asked for.
    recorder = _RecordingFuzzyInCode()
    await _engine_with(recorder).detect(_padded(5))
    joined = "\n".join(recorder.seen)
    assert "tok0" in joined, "a detector with skip_code_contexts=False never saw code"


@pytest.mark.asyncio
async def test_skip_code_contexts_true_still_withholds_code() -> None:
    recorder = _RecordingFuzzy()
    await _engine_with(recorder).detect(_padded(5))
    joined = "\n".join(recorder.seen)
    assert "tok0" not in joined


@pytest.mark.asyncio
async def test_both_settings_coexist_in_one_request() -> None:
    """Two groups, two passes — not one pass with the stricter policy applied."""
    skips, scans = _RecordingFuzzy(), _RecordingFuzzyInCode()
    await _engine_with(skips, scans).detect(_padded(5))
    assert len(skips.seen) == 1 and len(scans.seen) == 1
    assert "tok0" not in "\n".join(skips.seen)
    assert "tok0" in "\n".join(scans.seen)


# --- the fuzzy ceiling must not apply when nothing fuzzy is enabled ----------------


@pytest.mark.asyncio
async def test_no_fuzzy_detector_means_no_fuzzy_limit() -> None:
    """v1互換設定ではNERが無効なので既定buildにfuzzy detectorはない。

    Rejecting a large request for exceeding a budget that governs work nobody
    asked for would fail closed against a threat that is not present.
    """
    engine = build_engine(SecurityMaskerConfig.model_validate(
        {"version": 1, "defaults": {"max_fuzzy_chars": 1000}}))
    assert not [d for d in engine.detectors if getattr(d, "fuzzy", False)]
    results = await engine.detect("これはテスト文章です。" * 200 + f"担当は{NAME}です。")
    assert isinstance(results, list)     # completed rather than raising


@pytest.mark.asyncio
async def test_the_limit_still_applies_once_a_fuzzy_detector_exists() -> None:
    engine = _engine_with(_RecordingFuzzy(), max_fuzzy_chars=1000)
    with pytest.raises(DetectionError):
        await engine.detect("これはテスト文章です。" * 200 + f"担当は{NAME}です。")
