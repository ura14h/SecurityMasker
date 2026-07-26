"""detectionを統合・解決し、spanごとの最終処理を決定する（§11 step 8-9）。

Input: every detector's raw hits (original coordinates). Output: a set of
non-overlapping spans to act on, plus their restore policy. Rules:

- ``EXISTING_ALIAS`` hits are protected regions: any overlapping hit is dropped and
  the alias itself is left untouched (idempotency, §11).
- Remaining overlaps resolve by preference: higher priority, then longer span, then
  higher score — so ``株式会社極秘技研`` wins over the shorter ``極秘技研`` (§11).
"""

from __future__ import annotations

from dataclasses import replace

from securitymasker.models import DetectionResult, EntityType, RestorePolicy

# Safety lattice (doc/06 P1-3): strictly-ordered restore policies, strongest last.
_POLICY_STRENGTH: dict[str, int] = {
    RestorePolicy.LITERAL.value: 0,
    RestorePolicy.ENV_REFERENCE.value: 1,
    RestorePolicy.REDACTED.value: 2,
    RestorePolicy.BLOCK.value: 3,
}

# Minimum (safest) restore policy per entity type — a high-priority weak dictionary
# or regex must NOT be able to weaken these below their floor. Critical developer
# secrets are at least env_reference (real value never returned); My Number and card
# numbers are at least block. Any priority is powerless against this floor.
_SAFETY_FLOOR: dict[str, str] = {
    EntityType.API_KEY.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.OAUTH_TOKEN.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.JWT.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.PRIVATE_KEY.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.PASSWORD.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.DB_CONNECTION_STRING.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.GENERIC_SECRET.value: RestorePolicy.ENV_REFERENCE.value,
    EntityType.JP_MY_NUMBER.value: RestorePolicy.BLOCK.value,
    EntityType.CREDIT_CARD.value: RestorePolicy.BLOCK.value,
}


def _floor_rank(entity_type: str) -> int:
    return _POLICY_STRENGTH.get(_SAFETY_FLOOR.get(entity_type, RestorePolicy.LITERAL.value), 0)


def _clamp_policy(d: DetectionResult) -> DetectionResult:
    """detectionのrestore policyをentity typeの最低強度以上へ引き上げる。"""
    floor = _SAFETY_FLOOR.get(d.entity_type)
    if floor is None:
        return d
    if _POLICY_STRENGTH.get(d.restore_policy, 0) >= _POLICY_STRENGTH[floor]:
        return d
    return replace(d, restore_policy=floor)


def _safety_rank(d: DetectionResult) -> int:
    """固有restore policyとentity type最低強度のうち厳しい方を返す。"""
    return max(_floor_rank(d.entity_type), _POLICY_STRENGTH.get(d.restore_policy, 0))


def _preference(d: DetectionResult) -> tuple[int, int, int, float]:
    # Safety dominates priority (P1-3). Ranking on the *effective* strictness — not
    # just the entity-type floor — is what stops a high-priority `literal` from
    # winning an overlap against a lower-priority `block`/`redacted` and quietly
    # weakening it.
    priority = int(d.metadata.get("priority", 100))
    return (_safety_rank(d), priority, d.length, d.score)


def _overlaps(a: DetectionResult, b: DetectionResult) -> bool:
    return a.start < b.end and b.start < a.end


def _resolve_cluster(cluster: list[DetectionResult]) -> list[DetectionResult]:
    """互いに到達可能な小overlap cluster内でpreference-greedyに選ぶ。

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
        accepted.append(_clamp_policy(cand))
    return accepted


def resolve(detections: list[DetectionResult]) -> list[DetectionResult]:
    """処理対象の重ならないdetectionをstart位置順で返す。

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
    """restore policyが``block``のentity type。requestをfail-closedにする（§10）。"""
    return [
        d.entity_type
        for d in resolved
        if d.restore_policy == RestorePolicy.BLOCK.value
    ]
