"""Milestone B tests (doc/06 P0-6 + P1-2): config + detector fail-closed.

A misconfiguration or a failed/unavailable detector must fail at startup or block
the request — never silently become a normal, forwarding gateway. Reproduce each
unsafe config first, then assert it is rejected (ConfigError) or blocks.
"""

from __future__ import annotations

import pytest

from securitymasker.config import (
    SecurityMaskerConfig,
    build_detectors,
    build_engine,
    load_config,
    parse_duration,
)
from securitymasker.detectors.base import DetectionContext
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError, DetectionError

# --- P1-2: strict schema — unknown fields / version / durations / regex ---------


def _load_raw(tmp_path, text: str):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return load_config(p)


def test_unknown_top_level_field_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, "version: 1\nentitiez: []\n")


def test_unknown_defaults_field_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, "version: 1\ndefaults:\n  fail_mdoe: closed\n")


def test_unsupported_version_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, "version: 999\n")


def test_invalid_duration_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, "version: 1\ndefaults:\n  session_idle_ttl: 4 fortnights\n")


def test_regex_group_out_of_range_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, (
            "version: 1\n"
            "patterns:\n"
            "  - id: p1\n    pattern: 'abc'\n    type: HOSTNAME\n"
            "    replacement_profile: hostname\n    group: 3\n"
        ))


def test_regex_invalid_profile_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, (
            "version: 1\n"
            "patterns:\n"
            "  - id: p1\n    pattern: 'abc'\n    type: HOSTNAME\n"
            "    replacement_profile: not_a_profile\n"
        ))


def test_parse_duration_units() -> None:
    assert parse_duration("90s").total_seconds() == 90
    assert parse_duration("30m").total_seconds() == 1800
    assert parse_duration("4h").total_seconds() == 14400
    assert parse_duration("1d").total_seconds() == 86400
    for bad in ["0h", "-1m", "h", "10", ""]:
        with pytest.raises(ValueError):
            parse_duration(bad)


# --- P0-6: value_from_env must resolve at startup -------------------------------


def _env_config() -> SecurityMaskerConfig:
    return SecurityMaskerConfig.model_validate({
        "version": 1,
        "entities": [{
            "id": "api", "type": "API_KEY", "value_from_env": "SM_TEST_SECRET",
            "replacement_profile": "environment_reference", "restore_policy": "env_reference",
        }],
    })


def test_value_from_env_missing_fails_startup(monkeypatch) -> None:
    monkeypatch.delenv("SM_TEST_SECRET", raising=False)
    with pytest.raises(ConfigError):
        build_engine(_env_config())


def test_value_from_env_empty_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("SM_TEST_SECRET", "")
    with pytest.raises(ConfigError):
        build_engine(_env_config())


def test_value_from_env_present_ok(monkeypatch) -> None:
    monkeypatch.setenv("SM_TEST_SECRET", "sk-ant-" + "z" * 30)
    engine = build_engine(_env_config())  # no raise
    assert isinstance(engine, MaskingEngine)


# --- P1-2: preserve_aliases actually toggles the existing-alias detector ---------


def test_preserve_aliases_toggles_detector() -> None:
    on = SecurityMaskerConfig.model_validate({"version": 1})
    off = SecurityMaskerConfig.model_validate(
        {"version": 1, "defaults": {"preserve_aliases": False}})
    names_on = {getattr(d, "name", "") for d in build_detectors(on)}
    names_off = {getattr(d, "name", "") for d in build_detectors(off)}
    assert "existing_alias" in names_on
    assert "existing_alias" not in names_off


# --- P0-6: required detector that cannot load fails startup ----------------------


def test_ner_required_missing_dependency_fails_startup() -> None:
    from securitymasker.detectors.japanese_ner import JapaneseNerDetector

    # transformers is not a runtime dependency; requiring it must fail at startup,
    # not silently disable NER.
    with pytest.raises(ConfigError):
        JapaneseNerDetector(model="does-not-exist/model", required=True)


def test_presidio_required_bad_model_fails_startup() -> None:
    from securitymasker.detectors.presidio import PresidioDetector

    with pytest.raises(ConfigError):
        PresidioDetector(model_name="__no_such_spacy_model__", required=True)


# --- P0-6: a runtime detector fault blocks (closed) / may skip fuzzy (open) ------


class _Boom:
    name = "presidio"  # a fail-open-eligible (fuzzy) detector name

    async def detect(self, context: DetectionContext) -> list:
        raise DetectionError("simulated runtime fault")


class _BoomCritical:
    name = "secret_patterns"  # critical: never fail-open

    async def detect(self, context: DetectionContext) -> list:
        raise DetectionError("simulated runtime fault")


@pytest.mark.asyncio
async def test_detector_fault_blocks_in_closed_mode() -> None:
    engine = MaskingEngine([_Boom()], fail_mode="closed")
    with pytest.raises(DetectionError):
        await engine.detect("hello")


@pytest.mark.asyncio
async def test_fuzzy_detector_fault_skipped_in_open_mode() -> None:
    engine = MaskingEngine([_Boom()], fail_mode="open")
    assert await engine.detect("hello") == []  # skipped, no raise


@pytest.mark.asyncio
async def test_critical_detector_fault_blocks_even_in_open_mode() -> None:
    engine = MaskingEngine([_BoomCritical()], fail_mode="open")
    with pytest.raises(DetectionError):
        await engine.detect("hello")
