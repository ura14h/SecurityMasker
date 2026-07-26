"""masking engineのend-to-end受け入れ条件を検証する。"""

from __future__ import annotations

import json

import pytest

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.existing_alias import ExistingAliasDetector
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.models import (
    ContextKind,
    DetectionResult,
    EntityType,
    ReplacementProfile,
    RestorePolicy,
)
from securitymasker.sessions.memory import InMemorySessionStore

ORG = EntityType.ORGANIZATION.value
PERSON = EntityType.PERSON.value
PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def build_engine(**kw) -> MaskingEngine:
    entries = [
        DictionaryEntry(ORG, ("株式会社極秘技研", "極秘技研"), PROSE, LITERAL, 100),
        DictionaryEntry(PERSON, ("山田太郎", "山田 太郎"), PROSE, LITERAL, 100),
    ]
    detectors = [
        ExistingAliasDetector(),
        DictionaryDetector(entries),
        RegexDetector(
            [RegexEntry(r"prod-db01\.internal\.example", EntityType.HOSTNAME.value,
                        ReplacementProfile.HOSTNAME.value, LITERAL, 150)],
            name="host",
        ),
        build_secret_detector(),
    ]
    return MaskingEngine(detectors, **kw)


async def _sess(store: InMemorySessionStore, sid: str):
    return await store.get_or_create(sid)


@pytest.mark.asyncio
async def test_same_secret_same_alias_within_session() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    s = await _sess(store, "A")
    r1 = await eng.mask_text(s, "山田太郎 と 山田太郎")
    assert "山田太郎" not in r1.masked_text
    # Both occurrences map to exactly one, identical alias.
    aliases = {d for d in r1.masked_text.split() if d.startswith("SM_PERSON_")}
    assert len(aliases) == 1
    alias = aliases.pop()
    assert r1.masked_text == f"{alias} と {alias}"
    # A later request in the same session reuses the same alias.
    r2 = await eng.mask_text(s, "山田太郎")
    assert r2.masked_text == alias


@pytest.mark.parametrize("value", ["Acme", "SM"])
@pytest.mark.asyncio
async def test_one_fuzzy_hit_masks_every_exact_repeat_without_remasking_alias(
    value: str,
) -> None:
    class FirstOnlyFuzzyDetector:
        name = "first_only_fuzzy"
        fuzzy = True
        skip_code_contexts = False

        async def detect(self, context):
            start = context.norm.normalized.find(value)
            if start < 0:
                return []
            end = start + len(value)
            original_start, original_end = context.norm.to_original_span(start, end)
            return [
                DetectionResult(
                    entity_type=ORG,
                    start=original_start,
                    end=original_end,
                    score=0.99,
                    detector=self.name,
                    context_kind=ContextKind.PROSE.value,
                    replacement_profile=PROSE,
                    restore_policy=LITERAL,
                    original_value=context.norm.original[original_start:original_end],
                    normalized_value=value,
                )
            ]

    engine = MaskingEngine([FirstOnlyFuzzyDetector()])
    session = await _sess(InMemorySessionStore(), "repeat")
    result = await engine.mask_text(session, f"{value} と {value}")

    if value != "SM":
        assert value not in result.masked_text
    aliases = result.masked_text.split(" と ")
    assert len(aliases) == 2
    assert aliases[0] == aliases[1]
    assert aliases[0].startswith("SM_ORG_")
    assert await engine.unmask_text(session, result.masked_text) == f"{value} と {value}"


@pytest.mark.asyncio
async def test_different_session_different_alias() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    a = await eng.mask_text(await _sess(store, "A"), "山田太郎")
    b = await eng.mask_text(await _sess(store, "B"), "山田太郎")
    assert a.masked_text != b.masked_text
    assert a.masked_text.startswith("SM_PERSON_")
    assert b.masked_text.startswith("SM_PERSON_")


@pytest.mark.asyncio
async def test_restore_roundtrip_literal_and_env_reference() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    s = await _sess(store, "A")
    text = "担当は山田太郎、key=sk-abcdefghijklmnopqrstuvwxyz012345"
    r = await eng.mask_text(s, text)
    assert "山田太郎" not in r.masked_text
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in r.masked_text
    assert "${SECURITYMASKER_SECRET_" in r.masked_text
    restored = await eng.unmask_text(s, r.masked_text)
    assert "山田太郎" in restored
    # env_reference secret is NOT restored to its literal value.
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in restored
    assert "${SECURITYMASKER_SECRET_" in restored


@pytest.mark.asyncio
async def test_longest_match_wins_over_substring() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    r = await eng.mask_text(await _sess(store, "A"), "株式会社極秘技研の件")
    # Must not become '株式会社SM_ORG_...'; the whole org name is one alias.
    assert "株式会社" not in r.masked_text
    assert r.masked_text.startswith("SM_ORG_")


@pytest.mark.asyncio
async def test_existing_alias_not_double_masked() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    s = await _sess(store, "A")
    first = await eng.mask_text(s, "山田太郎")
    alias = first.masked_text
    # Replaying text that already contains the alias leaves it unchanged.
    again = await eng.mask_text(s, f"{alias} は担当です")
    assert alias in again.masked_text
    assert "SM_PERSON_" in again.masked_text and again.masked_text.count("SM_") == 1


@pytest.mark.asyncio
async def test_only_this_session_aliases_restored() -> None:
    eng, store = build_engine(), InMemorySessionStore()
    a = await eng.mask_text(await _sess(store, "A"), "山田太郎")
    sb = await _sess(store, "B")
    # Session B never created A's alias; it must not restore it.
    assert await eng.unmask_text(sb, a.masked_text) == a.masked_text


@pytest.mark.asyncio
async def test_json_string_escaping_is_preserved() -> None:
    """二重引用符を含む元値を復元しても有効なJSONになることを確認する。"""
    entries = [DictionaryEntry(ORG, ('a"b\\c',), PROSE, LITERAL, 100)]
    eng = MaskingEngine([DictionaryDetector(entries)])
    store = InMemorySessionStore()
    s = await _sess(store, "A")
    payload = json.dumps({"name": 'a"b\\c'})
    r = await eng.mask_text(s, payload)
    assert 'a"b\\c' not in r.masked_text
    assert json.loads(r.masked_text)  # still valid JSON after masking
    restored = await eng.unmask_text(s, r.masked_text)
    assert json.loads(restored)["name"] == 'a"b\\c'


@pytest.mark.asyncio
async def test_block_policy_fails_closed() -> None:
    entries = [DictionaryEntry(EntityType.JP_MY_NUMBER.value, ("123456789012",),
                               ReplacementProfile.NUMERIC.value, RestorePolicy.BLOCK.value, 200)]
    eng = MaskingEngine([DictionaryDetector(entries)])
    s = await InMemorySessionStore().get_or_create("A")
    from securitymasker.errors import MaskingError
    with pytest.raises(MaskingError):
        await eng.mask_text(s, "個人番号は123456789012です")


@pytest.mark.asyncio
async def test_redacted_policy_is_irreversible() -> None:
    entries = [DictionaryEntry(ORG, ("極秘",), PROSE, RestorePolicy.REDACTED.value, 100)]
    eng = MaskingEngine([DictionaryDetector(entries)])
    s = await InMemorySessionStore().get_or_create("A")
    r = await eng.mask_text(s, "これは極秘です")
    assert "極秘" not in r.masked_text
    assert "[REDACTED]" in r.masked_text
    assert "極秘" not in await eng.unmask_text(s, r.masked_text)
