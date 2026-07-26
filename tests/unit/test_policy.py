"""重複解決・最長一致・保護を含むpolicy決定を検証する。"""

from __future__ import annotations

from securitymasker import policy
from securitymasker.models import DetectionResult, EntityType, RestorePolicy


def det(start, end, *, etype="ORGANIZATION", priority=100, score=0.9, restore="literal") -> DetectionResult:
    return DetectionResult(
        entity_type=etype, start=start, end=end, score=score, detector="t",
        context_kind="prose", replacement_profile="prose_identifier",
        restore_policy=restore, original_value="x" * (end - start),
        metadata={"priority": priority},
    )


def test_longer_span_wins_over_shorter_overlap() -> None:
    short = det(4, 8)   # 極秘技研
    long = det(0, 8)    # 株式会社極秘技研
    resolved = policy.resolve([short, long])
    assert resolved == [long]


def test_higher_priority_wins_on_equal_length() -> None:
    a = det(0, 5, priority=100)
    b = det(0, 5, priority=200)
    assert policy.resolve([a, b]) == [b]


def test_non_overlapping_all_kept_and_sorted() -> None:
    a = det(10, 15)
    b = det(0, 5)
    resolved = policy.resolve([a, b])
    assert [d.start for d in resolved] == [0, 10]


def test_existing_alias_protects_overlapping_detection() -> None:
    alias = det(0, 13, etype=EntityType.EXISTING_ALIAS.value, priority=300)
    overlap = det(0, 6, priority=100)
    resolved = policy.resolve([alias, overlap])
    # The alias is protected and not returned for replacement; the overlap is dropped.
    assert resolved == []


def test_blocking_entities_detected() -> None:
    d = det(0, 12, restore=RestorePolicy.BLOCK.value)
    assert policy.blocking_entities([d]) == [d.entity_type]
