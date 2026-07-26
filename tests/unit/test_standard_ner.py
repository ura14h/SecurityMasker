"""ADR-0012標準NER経路を実配布modelで検証する。"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from securitymasker.config import (
    JapaneseNerV2Config,
    SecurityMaskerConfig,
    build_engine,
)
from securitymasker.models_fetch import (
    ADOPTED_MODEL,
    ADOPTED_REVISION,
    cache_directory,
)
from securitymasker.sessions.store import new_session


def _missing_model() -> None:
    message = (
        f"{ADOPTED_MODEL}@{ADOPTED_REVISION} is not cached; "
        "run scripts/setup before the release gate"
    )
    if os.environ.get("SM_REQUIRE_MODEL") == "1":
        pytest.fail(message)
    pytest.skip(message)


def test_v2_standard_ner_is_enabled_and_pinned() -> None:
    configured = JapaneseNerV2Config()
    assert configured.enabled is True
    assert configured.model == ADOPTED_MODEL
    assert configured.revision == ADOPTED_REVISION
    assert configured.local_files_only is True
    assert configured.allow_unverified_model is False


def test_v2_enabled_ner_cannot_silently_drop_or_replace_the_standard_model() -> None:
    with pytest.raises(ValidationError):
        JapaneseNerV2Config.model_validate({"enabled": True, "model": None})
    with pytest.raises(ValidationError):
        JapaneseNerV2Config.model_validate(
            {
                "enabled": True,
                "model": "someone/unverified-model",
                "revision": "floating",
            }
        )
    with pytest.raises(ValidationError):
        JapaneseNerV2Config.model_validate({"allow_unverified_model": True})


@pytest.fixture(scope="module")
def standard_engine():
    if cache_directory(ADOPTED_MODEL, ADOPTED_REVISION) is None:
        _missing_model()
    config = SecurityMaskerConfig.model_validate(
        {
            "version": 1,
            "ner": {
                "model": ADOPTED_MODEL,
                "revision": ADOPTED_REVISION,
                "min_score": 0.7,
                "local_files_only": True,
                "allow_unverified_model": False,
            },
        }
    )
    return build_engine(config)


@pytest.mark.asyncio
async def test_standard_ner_masks_unregistered_person_organization_and_location(
    standard_engine,
) -> None:
    text = "担当者は佐々木健一です。株式会社青空技研は横浜市にあります。"
    result = await standard_engine.mask_text(new_session("standard-ner"), text)

    assert "佐々木健一" not in result.masked_text
    assert "株式会社青空技研" not in result.masked_text
    assert "横浜市" not in result.masked_text
    assert {item.entity_type for item in result.detections} >= {
        "PERSON",
        "ORGANIZATION",
        "LOCATION",
    }
