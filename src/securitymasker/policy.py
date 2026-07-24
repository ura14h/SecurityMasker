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


def _preference(d: DetectionResult) -> tuple[int, int, float]:
    priority = int(d.metadata.get("priority", 100))
    return (priority, d.length, d.score)


def _overlaps(a: DetectionResult, b: DetectionResult) -> bool:
    return a.start < b.end and b.start < a.end


def _resolve_cluster(cluster: list[DetectionResult]) -> list[DetectionResult]:
    """Preference-greedy within a small cluster of mutually-reachable overlaps.

    Existing-alias spans are protected: they suppress overlapping candidates and are
    themselves excluded from the result (idempotency, §11).
    """
    protected = [d for d in cluster if d.entity_type == EntityType.EXISTING_ALIAS.value]
    candidates = [d for d in cluster if d.entity_type != EntityType.EXISTING_ALIAS.value]
    accepted: list[DetectionResult] = []
    for cand in sorted(candidates, key=_preference, reverse=True):
        if any(_overlaps(cand, p) for p in protected):
            continue
        if any(_overlaps(cand, a) for a in accepted):
            continue
        accepted.append(cand)
    return accepted


def resolve(detections: list[DetectionResult]) -> list[DetectionResult]:
    """Return non-overlapping detections to act on, ordered by start position.

    Near-linear: sort by start, sweep into clusters of transitively-overlapping
    spans, and run the preference greedy (priority, then length, then score, §11)
    only *within* each cluster. Non-overlapping detections — e.g. the same secret
    repeated thousands of times in a large input — become singleton clusters, so
    there is no all-pairs blowup (§32).
    """
    if not detections:
        return []
    ordered = sorted(detections, key=lambda d: (d.start, d.end))

    result: list[DetectionResult] = []
    cluster: list[DetectionResult] = [ordered[0]]
    cluster_end = ordered[0].end
    for det in ordered[1:]:
        if det.start < cluster_end:  # overlaps the running cluster
            cluster.append(det)
            cluster_end = max(cluster_end, det.end)
        else:
            result.extend(_resolve_cluster(cluster))
            cluster = [det]
            cluster_end = det.end
    result.extend(_resolve_cluster(cluster))

    result.sort(key=lambda d: d.start)
    return result


def blocking_entities(resolved: list[DetectionResult]) -> list[str]:
    """Entity types whose restore policy is ``block`` (request must fail closed, §10)."""
    return [
        d.entity_type
        for d in resolved
        if d.restore_policy == RestorePolicy.BLOCK.value
    ]
