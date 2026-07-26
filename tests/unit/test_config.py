"""設定の読み込み、起動時検証、環境変数参照を検証する。"""

from __future__ import annotations

from pathlib import Path

import pytest

from securitymasker.config import build_engine, load_config
from securitymasker.errors import ConfigError

VALID = """
version: 1
defaults:
  normalization: nfkc
entities:
  - id: org
    type: ORGANIZATION
    values: ["極秘技研"]
    replacement_profile: prose_identifier
    restore_policy: literal
patterns:
  - id: ticket
    pattern: 'INC-[0-9]{6}'
    type: CUSTOMER_ID
    replacement_profile: numeric
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_valid_config(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID))
    assert len(config.entities) == 1
    assert len(config.patterns) == 1
    assert config.defaults.normalization == "nfkc"


def test_invalid_profile_raises(tmp_path: Path) -> None:
    bad = VALID.replace("prose_identifier", "not_a_profile")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_invalid_regex_raises(tmp_path: Path) -> None:
    bad = VALID.replace("INC-[0-9]{6}", "INC-[0-9")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_duplicate_ids_raise(tmp_path: Path) -> None:
    dup = VALID + """
  - id: ticket
    pattern: 'X-[0-9]{2}'
    type: CUSTOMER_ID
    replacement_profile: numeric
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, dup))


def test_entity_requires_values_or_env(tmp_path: Path) -> None:
    bad = """
version: 1
entities:
  - id: x
    type: PERSON
    replacement_profile: prose_identifier
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_value_from_env_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROD_DB_HOST", "prod-db01.internal.example")
    text = """
version: 1
entities:
  - id: host
    type: HOSTNAME
    value_from_env: PROD_DB_HOST
    replacement_profile: hostname
    restore_policy: literal
"""
    config = load_config(_write(tmp_path, text))
    assert config.entities[0].resolved_values() == ("prod-db01.internal.example",)


@pytest.mark.asyncio
async def test_build_engine_from_config_masks(tmp_path: Path) -> None:
    from securitymasker.sessions.memory import InMemorySessionStore

    engine = build_engine(load_config(_write(tmp_path, VALID)))
    session = await InMemorySessionStore().get_or_create("s")
    result = await engine.mask_text(session, "極秘技研の件 INC-123456")
    assert "極秘技研" not in result.masked_text
    assert "INC-123456" not in result.masked_text


def test_detectors_are_built_once() -> None:
    """Building an engine must not build the detector pipeline twice.

    Detector construction loads models, so a duplicate build is not merely slow —
    it doubles resident memory for every process that constructs an engine.
    """
    from unittest.mock import patch

    from securitymasker import config as cfgmod
    from securitymasker.config import SecurityMaskerConfig

    with patch.object(cfgmod, "build_detectors",
                      wraps=cfgmod.build_detectors) as counting:
        cfgmod.build_engine(SecurityMaskerConfig.model_validate({"version": 1}))
    assert counting.call_count == 1, "detector pipeline (and its models) built twice"
