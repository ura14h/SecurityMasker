"""Merge/resolve detections and decide the final action per span (§11 steps 8-9).

Input: every detector's raw hits (original coordinates). Output: a set of
non-overlapping spans to act on, plus their restore policy. Rules:

- ``EXISTING_ALIAS`` hits are protected regions: any overlapping hit is dropped and
  the alias itself is left untouched (idempotency, §11).
- Remaining overlaps resolve by preference: higher priority, then longer span, then
  higher score — so ``株式会社極秘技研`` wins over the shorter ``極秘技研`` (§11).
"""

from __future__ import annotations

from securitymasker.models import DetectionResult, EntityType, RestorePolicy


def _overlaps(a: DetectionResult, b: DetectionResult) -> bool:
    return a.start < b.end and b.start < a.end


def _preference(d: DetectionResult) -> tuple[int, int, float]:
    priority = int(d.metadata.get("priority", 100))
    return (priority, d.length, d.score)


def resolve(detections: list[DetectionResult]) -> list[DetectionResult]:
    """Return non-overlapping detections to act on, ordered by start position."""
    protected = [d for d in detections if d.entity_type == EntityType.EXISTING_ALIAS.value]
    candidates = [d for d in detections if d.entity_type != EntityType.EXISTING_ALIAS.value]

    # Drop anything overlapping a protected alias region.
    candidates = [c for c in candidates if not any(_overlaps(c, p) for p in protected)]

    # Greedily accept the most-preferred non-overlapping candidates.
    candidates.sort(key=_preference, reverse=True)
    accepted: list[DetectionResult] = []
    for cand in candidates:
        if any(_overlaps(cand, a) for a in accepted):
            continue
        accepted.append(cand)

    accepted.sort(key=lambda d: d.start)
    return accepted


def blocking_entities(resolved: list[DetectionResult]) -> list[str]:
    """Entity types whose restore policy is ``block`` (request must fail closed, §10)."""
    return [
        d.entity_type
        for d in resolved
        if d.restore_policy == RestorePolicy.BLOCK.value
    ]
