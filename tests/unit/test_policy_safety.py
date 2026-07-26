"""重大secretに対する最低安全policyを検証する。

A high-priority weak dictionary/regex must not be able to weaken a critical
secret's restore policy (env_reference/block) down to literal via overlap or
priority. The safety floor wins regardless of priority.
"""

from __future__ import annotations

from securitymasker import policy
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
ENV = ReplacementProfile.ENVIRONMENT_REFERENCE.value


def _det(entity_type, start, end, restore, *, priority=100, profile=PROSE, score=0.9):
    return DetectionResult(
        entity_type=entity_type, start=start, end=end, score=score, detector="t",
        context_kind="prose", replacement_profile=profile, restore_policy=restore,
        original_value="x" * (end - start), normalized_value="x" * (end - start),
        metadata={"priority": priority})


def test_high_priority_literal_dict_cannot_weaken_api_key() -> None:
    api = _det(EntityType.API_KEY.value, 0, 20, RestorePolicy.ENV_REFERENCE.value,
               priority=200, profile=ENV)
    weak = _det(EntityType.PERSON.value, 0, 20, RestorePolicy.LITERAL.value, priority=999)
    resolved = policy.resolve([api, weak])
    assert len(resolved) == 1
    # The API key wins the overlap and keeps env_reference (not literal).
    assert resolved[0].entity_type == EntityType.API_KEY.value
    assert resolved[0].restore_policy == RestorePolicy.ENV_REFERENCE.value


def test_my_number_block_cannot_be_overridden() -> None:
    mynum = _det(EntityType.JP_MY_NUMBER.value, 0, 12, RestorePolicy.BLOCK.value, priority=210)
    weak = _det(EntityType.PERSON.value, 0, 12, RestorePolicy.LITERAL.value, priority=999)
    resolved = policy.resolve([mynum, weak])
    assert len(resolved) == 1
    assert resolved[0].entity_type == EntityType.JP_MY_NUMBER.value
    assert resolved[0].restore_policy == RestorePolicy.BLOCK.value


def test_secret_literal_is_clamped_up_to_floor() -> None:
    # Even a lone misconfigured API-key detection with literal is clamped up.
    api = _det(EntityType.API_KEY.value, 0, 20, RestorePolicy.LITERAL.value, profile=ENV)
    resolved = policy.resolve([api])
    assert resolved[0].restore_policy == RestorePolicy.ENV_REFERENCE.value


def test_low_priority_block_beats_high_priority_literal() -> None:
    # Re-audit "その他": ranking on the entity-type floor alone let a high-priority
    # `literal` win an overlap against a lower-priority `block` and weaken it.
    blocked = _det(EntityType.CUSTOMER_ID.value, 0, 12, RestorePolicy.BLOCK.value, priority=10)
    weak = _det(EntityType.PERSON.value, 0, 12, RestorePolicy.LITERAL.value, priority=999)
    resolved = policy.resolve([blocked, weak])
    assert len(resolved) == 1
    assert resolved[0].restore_policy == RestorePolicy.BLOCK.value


def test_low_priority_redacted_beats_high_priority_literal() -> None:
    redacted = _det(EntityType.EMPLOYEE_ID.value, 0, 12, RestorePolicy.REDACTED.value, priority=1)
    weak = _det(EntityType.PERSON.value, 0, 12, RestorePolicy.LITERAL.value, priority=900)
    resolved = policy.resolve([redacted, weak])
    assert resolved[0].restore_policy == RestorePolicy.REDACTED.value


def test_stricter_policy_can_still_be_applied() -> None:
    # A stricter-than-floor policy (block) is preserved for a critical secret.
    api = _det(EntityType.API_KEY.value, 0, 20, RestorePolicy.BLOCK.value, profile=ENV)
    resolved = policy.resolve([api])
    assert resolved[0].restore_policy == RestorePolicy.BLOCK.value
