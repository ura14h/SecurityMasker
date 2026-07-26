"""全detectorの結果を統合し、重ならない最終spanとrestore policyを決定する。

``EXISTING_ALIAS``と重なる検出は破棄して既存aliasを保護する。それ以外の重なりは、
安全強度、priority、span長、scoreの順で解決する。
"""

from __future__ import annotations

from dataclasses import replace

from securitymasker.models import DetectionResult, EntityType, RestorePolicy

# restore policyを弱い順に並べた安全強度。
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
    # priorityより安全強度を優先し、高priorityのliteralがblock/redactedを弱めるのを防ぐ。
    priority = int(d.metadata.get("priority", 100))
    return (_safety_rank(d), priority, d.length, d.score)


def _overlaps(a: DetectionResult, b: DetectionResult) -> bool:
    return a.start < b.end and b.start < a.end


def _resolve_cluster(cluster: list[DetectionResult]) -> list[DetectionResult]:
    """互いに到達可能な小overlap cluster内でpreference-greedyに選ぶ。

    既存aliasのspanは重なる候補を抑止し、自身は処理対象から除外する。
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
    spans, and run the preference greedy (priority, then length, then score)
    only *within* each cluster. Non-overlapping detections — e.g. the same secret
    repeated thousands of times in a large input — become singleton clusters, so
    there is no all-pairs blowup.
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
    """restore policyが``block``のentity type。requestをfail-closedにする。"""
    return [
        d.entity_type
        for d in resolved
        if d.restore_policy == RestorePolicy.BLOCK.value
    ]
