"""設定とdetectorのfail-closed動作を検証する。

A misconfiguration or a failed/unavailable detector must fail at startup or block
the request — never silently become a normal, forwarding gateway. Reproduce each
unsafe config first, then assert it is rejected (ConfigError) or blocks.
"""

from __future__ import annotations

import pytest

from securitymasker.bootstrap import initialize_layout
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

# --- strict schema: unknown fields / version / durations / regex -------------


def _load_raw(tmp_path, text: str):
    layout = initialize_layout(tmp_path, mode="chatgpt", port=49161)
    layout.dictionary.write_text(text, encoding="utf-8")
    return load_config(layout.config)


def _load_modified_config(tmp_path, old: str, new: str):
    layout = initialize_layout(tmp_path, mode="chatgpt", port=49161)
    text = layout.config.read_text(encoding="utf-8").replace(old, new)
    layout.config.write_text(text, encoding="utf-8")
    return load_config(layout.config)


def test_unknown_top_level_field_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_modified_config(tmp_path, "version: 1", "version: 1\nentitiez: []")


def test_unknown_defaults_field_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_modified_config(tmp_path, "fail_mode: closed", "fail_mdoe: closed")


def test_unsupported_version_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_modified_config(tmp_path, "version: 1", "version: 999")


def test_invalid_duration_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_modified_config(
            tmp_path,
            "session_idle_ttl: 4h",
            "session_idle_ttl: 4 fortnights",
        )


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


def test_empty_entity_value_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            "  - id: e1\n    type: PERSON\n    values: ['  ']\n"
            "    replacement_profile: prose_identifier\n"
        ))


def test_duplicate_entity_values_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            "  - id: e1\n    type: PERSON\n    values: ['A', 'A']\n"
            "    replacement_profile: prose_identifier\n"
        ))


def test_out_of_range_priority_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            "  - id: e1\n    type: PERSON\n    values: ['A']\n"
            "    replacement_profile: prose_identifier\n    priority: -5\n"
        ))


def test_out_of_range_min_score_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        _load_modified_config(tmp_path, "min_score: 0.7", "min_score: 4.2")


def test_parse_duration_units() -> None:
    assert parse_duration("90s").total_seconds() == 90
    assert parse_duration("30m").total_seconds() == 1800
    assert parse_duration("4h").total_seconds() == 14400
    assert parse_duration("1d").total_seconds() == 86400
    for bad in ["0h", "-1m", "h", "10", ""]:
        with pytest.raises(ValueError):
            parse_duration(bad)


# --- value_from_env must resolve at startup ---------------------------------


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


# --- preserve_aliases actually toggles the existing-alias detector -----------


def test_preserve_aliases_toggles_detector() -> None:
    on = SecurityMaskerConfig.model_validate({"version": 1})
    off = SecurityMaskerConfig.model_validate(
        {"version": 1, "defaults": {"preserve_aliases": False}})
    names_on = {getattr(d, "name", "") for d in build_detectors(on)}
    names_off = {getattr(d, "name", "") for d in build_detectors(off)}
    assert "existing_alias" in names_on
    assert "existing_alias" not in names_off


# --- required detector that cannot load fails startup ------------------------


def test_ner_required_missing_dependency_fails_startup() -> None:
    from securitymasker.detectors.japanese_ner import JapaneseNerDetector

    # transformers is not a runtime dependency; requiring it must fail at startup,
    # not silently disable NER.
    with pytest.raises(ConfigError):
        JapaneseNerDetector(model="does-not-exist/model", required=True)


# --- runtime detector fault: closed blocks; open may skip fuzzy detector ------


class _Boom:
    name = "jp_ner"  # a fail-open-eligible (fuzzy) detector name

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


# --- config errors must never echo the value that caused them --------------------
#
# A config file IS the secret inventory: entity values, and regexes that usually
# embed the very string they match. Every route out of the loader — duplicate
# detection, Pydantic type errors, YAML syntax errors, regex compilation, capture
# group checks — is a place the offending text can end up in an exception that is
# then logged. Each is pinned here separately because they fail through different
# libraries and one fix does not cover the others.

SECRET_VALUE = "Zettai-Himitsu-Corp-9876"


def test_duplicate_value_error_does_not_leak_the_value(tmp_path) -> None:
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            f"  - id: e1\n    type: PERSON\n    values: ['{SECRET_VALUE}', '{SECRET_VALUE}']\n"
            "    replacement_profile: prose_identifier\n"
        ))
    assert SECRET_VALUE not in str(exc.value)


def test_type_error_does_not_leak_the_value(tmp_path) -> None:
    # A wrong-typed field makes Pydantic report the rejected INPUT by default.
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            f"  - id: e1\n    type: PERSON\n    values: '{SECRET_VALUE}'\n"
            "    replacement_profile: prose_identifier\n    priority: not-a-number\n"
        ))
    assert SECRET_VALUE not in str(exc.value)


def test_empty_value_error_does_not_leak_siblings(tmp_path) -> None:
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            f"  - id: e1\n    type: PERSON\n    values: ['{SECRET_VALUE}', '  ']\n"
            "    replacement_profile: prose_identifier\n"
        ))
    assert SECRET_VALUE not in str(exc.value)


def test_invalid_regex_error_does_not_leak_the_pattern(tmp_path) -> None:
    # re.error quotes the pattern, and a user pattern usually embeds its secret.
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "patterns:\n"
            f"  - id: p1\n    pattern: '{SECRET_VALUE}('\n    type: HOSTNAME\n"
            "    replacement_profile: hostname\n"
        ))
    assert SECRET_VALUE not in str(exc.value)


def test_yaml_parse_error_does_not_leak_the_line(tmp_path) -> None:
    # Unclosed bracket: PyYAML quotes the offending source line in its message.
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "entities:\n"
            f"  - id: e1\n    values: ['{SECRET_VALUE}'\n"
        ))
    assert SECRET_VALUE not in str(exc.value)


def test_capture_group_error_does_not_leak_the_pattern(tmp_path) -> None:
    with pytest.raises(ConfigError) as exc:
        _load_raw(tmp_path, (
            "version: 1\n"
            "patterns:\n"
            f"  - id: p1\n    pattern: '{SECRET_VALUE}'\n    type: HOSTNAME\n"
            "    replacement_profile: hostname\n    group: 4\n"
        ))
    assert SECRET_VALUE not in str(exc.value)
