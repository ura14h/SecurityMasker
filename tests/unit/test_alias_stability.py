"""Audit round-2: issued-alias protection covers EVERY profile (doc/06 P0-7).

Re-audit finding 5: alias protection was driven by a shape regex that only knew
prose/hostname/email/env_reference, so ipv4, ipv6, uuid and numeric aliases were
re-masked on replay. The auditor observed an IPv4 alias drifting across turns:

    198.51.100.83 -> 198.51.100.97 -> 198.51.100.246

Replaying a masked turn must be idempotent for all profiles. Synthetic data only.
"""

from __future__ import annotations

import pytest

from securitymasker.detectors.formats import FormatsDetector
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.engine import MaskingEngine
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore

LITERAL = RestorePolicy.LITERAL.value


def _engine_for(profile: str, pattern: str, entity: str) -> MaskingEngine:
    from securitymasker.detectors.existing_alias import ExistingAliasDetector

    return MaskingEngine([
        ExistingAliasDetector(),
        RegexDetector([RegexEntry(pattern, entity, profile, LITERAL, 150)], name="u"),
    ])


async def _session():
    return await InMemorySessionStore().get_or_create("s")


@pytest.mark.parametrize(
    ("profile", "pattern", "entity", "value"),
    [
        (ReplacementProfile.IPV4.value, r"10\.1\.2\.3", EntityType.IP_ADDRESS.value, "10.1.2.3"),
        (ReplacementProfile.IPV6.value, r"fd00::1", EntityType.IP_ADDRESS.value, "fd00::1"),
        (ReplacementProfile.UUID.value,
         r"123e4567-e89b-12d3-a456-426614174000", EntityType.UUID.value,
         "123e4567-e89b-12d3-a456-426614174000"),
        (ReplacementProfile.NUMERIC.value, r"5550001111", EntityType.PHONE.value, "5550001111"),
        (ReplacementProfile.PROSE_IDENTIFIER.value, r"SecretCorp",
         EntityType.ORGANIZATION.value, "SecretCorp"),
    ],
)
@pytest.mark.asyncio
async def test_alias_is_stable_across_replayed_turns(profile, pattern, entity, value) -> None:
    engine = _engine_for(profile, pattern, entity)
    session = await _session()

    first = (await engine.mask_text(session, f"connect {value} now")).masked_text
    # Replay the MASKED text twice, as a multi-turn client resending history does.
    second = (await engine.mask_text(session, first)).masked_text
    third = (await engine.mask_text(session, second)).masked_text

    assert first == second == third, f"{profile} alias drifted across turns"
    # Exactly one mapping: the replay must not mint new aliases.
    assert len(session.mappings_by_alias) == 1


@pytest.mark.asyncio
async def test_ipv4_alias_replay_does_not_grow_mappings() -> None:
    # The auditor's exact reproduction: an IPv4 alias re-masked each turn.
    engine = MaskingEngine([
        __import__("securitymasker.detectors.existing_alias", fromlist=["x"]).ExistingAliasDetector(),
        FormatsDetector(),
    ])
    session = await _session()
    text = "host 203.0.113.7 responded"
    seen = []
    for _ in range(3):
        text = (await engine.mask_text(session, text)).masked_text
        seen.append(text)
    assert seen[0] == seen[1] == seen[2], seen
    assert len(session.mappings_by_alias) == 1
