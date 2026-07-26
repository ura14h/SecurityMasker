"""alias説明文を任意で注入する設定と配線を検証する。

When enabled, the adapters prepend a placeholder-preservation note to the
system/instructions field once masking has produced an alias; when disabled (the
engine default), nothing is added. The note carries no secret values.
"""

from __future__ import annotations

import pytest

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.engine import MaskingEngine
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.protocols import anthropic_messages, openai_responses
from securitymasker.protocols.base import ALIAS_INSTRUCTION
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"


def _engine(inject: bool) -> MaskingEngine:
    return MaskingEngine(
        [DictionaryDetector([DictionaryEntry(
            EntityType.PERSON.value, (PERSON,),
            ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])],
        inject_alias_instruction=inject)


async def _session():
    return await InMemorySessionStore().get_or_create("s")


@pytest.mark.asyncio
async def test_responses_injects_instruction_when_enabled() -> None:
    eng, s = _engine(True), await _session()
    data = {"instructions": "Be brief.", "input": f"担当は{PERSON}"}
    await openai_responses.mask_request(eng, s, data)
    assert data["instructions"].startswith(ALIAS_INSTRUCTION)
    assert "Be brief." in data["instructions"]
    assert PERSON not in data["instructions"]  # no secret leaked into the note


@pytest.mark.asyncio
async def test_responses_no_instruction_when_disabled() -> None:
    eng, s = _engine(False), await _session()
    data = {"input": f"担当は{PERSON}"}
    await openai_responses.mask_request(eng, s, data)
    assert "instructions" not in data


@pytest.mark.asyncio
async def test_responses_no_instruction_when_nothing_masked() -> None:
    eng, s = _engine(True), await _session()
    data = {"input": "nothing sensitive here"}
    await openai_responses.mask_request(eng, s, data)
    assert "instructions" not in data  # no alias -> no note


@pytest.mark.asyncio
async def test_anthropic_injects_into_system() -> None:
    eng, s = _engine(True), await _session()
    data = {"max_tokens": 10, "system": "Answer in JSON.",
            "messages": [{"role": "user", "content": f"担当は{PERSON}"}]}
    await anthropic_messages.mask_request(eng, s, data)
    assert data["system"].startswith(ALIAS_INSTRUCTION)
    assert "Answer in JSON." in data["system"]
