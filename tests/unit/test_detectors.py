"""Detector tests (§30.1: secrets, dictionary width/space, existing alias)."""

from __future__ import annotations

import pytest

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.existing_alias import ExistingAliasDetector
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.normalization import normalize

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def ctx(text: str) -> DetectionContext:
    return DetectionContext(norm=normalize(text, "nfkc"))


@pytest.mark.asyncio
async def test_dictionary_matches_fullwidth_variant() -> None:
    # Register the spaced surface form (§12 registers multiple variants). NFKC folds
    # the ideographic space U+3000 to a normal space so the input matches, and the
    # ORIGINAL surface (with ideographic space) is what we recover for restoration.
    det = DictionaryDetector([DictionaryEntry(EntityType.PERSON.value, ("山田 太郎",), PROSE, LITERAL)])
    res = await det.detect(ctx("担当は山田　太郎です"))
    assert len(res) == 1
    assert res[0].entity_type == EntityType.PERSON.value
    assert res[0].original_value == "山田　太郎"


@pytest.mark.asyncio
async def test_dictionary_reports_all_occurrences() -> None:
    det = DictionaryDetector([DictionaryEntry(EntityType.ORGANIZATION.value, ("極秘技研",), PROSE, LITERAL)])
    res = await det.detect(ctx("極秘技研と極秘技研"))
    assert len(res) == 2


@pytest.mark.asyncio
async def test_secret_detector_openai_and_anthropic_keys() -> None:
    det = build_secret_detector()
    res = await det.detect(ctx("k1=sk-ant-abcdefghijklmnopqrstuvwxyz k2=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
    types = {r.entity_type for r in res}
    assert EntityType.API_KEY.value in types
    assert EntityType.OAUTH_TOKEN.value in types
    assert all(r.restore_policy == RestorePolicy.ENV_REFERENCE.value for r in res)


@pytest.mark.asyncio
async def test_secret_detector_jwt_and_pem() -> None:
    det = build_secret_detector()
    jwt = "eyJhbGciOi.eyJzdWIiOm.SflKxwRJSM"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIBVAIBADANBg\n-----END PRIVATE KEY-----"
    res = await det.detect(ctx(f"token {jwt} and {pem}"))
    types = {r.entity_type for r in res}
    assert EntityType.JWT.value in types
    assert EntityType.PRIVATE_KEY.value in types


@pytest.mark.asyncio
async def test_basic_auth_url_masks_only_credentials() -> None:
    det = build_secret_detector()
    res = await det.detect(ctx("clone https://user:pass@example.com/repo.git"))
    creds = [r for r in res if r.entity_type == EntityType.PASSWORD.value]
    assert len(creds) == 1
    assert creds[0].original_value == "user:pass"


@pytest.mark.asyncio
async def test_existing_alias_detector_recognizes_all_forms() -> None:
    det = ExistingAliasDetector()
    text = "SM_ORG_7F3A91 sm-host-9c885f.example.invalid sm-user-2b891c@example.invalid ${SECURITYMASKER_SECRET_30A958}"
    res = await det.detect(ctx(text))
    assert len(res) == 4
    assert all(r.entity_type == EntityType.EXISTING_ALIAS.value for r in res)
