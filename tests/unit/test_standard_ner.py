"""標準NER経路の固定model設定と実配布modelでの検出を検証する。"""

from __future__ import annotations

import os
import socket

import pytest
from pydantic import ValidationError

from securitymasker.config import (
    JapaneseNerConfig,
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


def test_standard_ner_is_enabled_and_pinned() -> None:
    configured = JapaneseNerConfig()
    assert configured.enabled is True
    assert configured.model == ADOPTED_MODEL
    assert configured.revision == ADOPTED_REVISION
    assert configured.local_files_only is True
    assert configured.allow_unverified_model is False


def test_enabled_ner_cannot_silently_drop_or_replace_the_standard_model() -> None:
    with pytest.raises(ValidationError):
        JapaneseNerConfig.model_validate({"enabled": True, "model": None})
    with pytest.raises(ValidationError):
        JapaneseNerConfig.model_validate(
            {
                "enabled": True,
                "model": "someone/unverified-model",
                "revision": "floating",
            }
        )
    with pytest.raises(ValidationError):
        JapaneseNerConfig.model_validate({"allow_unverified_model": True})


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


@pytest.mark.skipif(os.name != "nt", reason="Windows native offline model gate")
def test_windows_standard_model_load_and_inference_open_no_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if cache_directory(ADOPTED_MODEL, ADOPTED_REVISION) is None:
        _missing_model()
    attempts: list[object] = []

    def refuse_connection(_socket: socket.socket, address: object) -> None:
        attempts.append(address)
        raise AssertionError("offline NER attempted a socket connection")

    def refuse_connection_ex(_socket: socket.socket, address: object) -> int:
        refuse_connection(_socket, address)
        return 1

    monkeypatch.setattr(socket.socket, "connect", refuse_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_connection_ex)
    from securitymasker.detectors.japanese_ner import JapaneseNerDetector

    detector = JapaneseNerDetector(
        model=ADOPTED_MODEL,
        revision=ADOPTED_REVISION,
        min_score=0.7,
        required=True,
        local_files_only=True,
        allow_unverified_model=False,
    )
    text = "担当者は佐々木健一です。"
    assert detector._pipeline is not None
    entities = detector._pipeline(text)

    assert any(text[item["start"] : item["end"]] == "佐々木健一" for item in entities)
    assert attempts == []


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
