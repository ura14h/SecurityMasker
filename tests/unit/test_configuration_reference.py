"""利用者向け設定リファレンスが現行schemaの全fieldを追従することを検証する。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from securitymasker.config import (
    Defaults,
    DetectorsConfig,
    DetectorToggle,
    EntityConfig,
    JapaneseNerConfig,
    JapanesePiiConfig,
    RegexConfig,
    RuntimeConfig,
    SecurityMaskerFileConfig,
    StateConfig,
    ToolTrustConfig,
    UserDictionaryConfig,
)

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "user" / "configuration.md"
CONFIG_TEMPLATE = ROOT / "src" / "securitymasker" / "resources" / "securitymasker.config"
DICTIONARY_TEMPLATE = ROOT / "src" / "securitymasker" / "resources" / "securitymasker.dict"


def test_reference_covers_every_public_config_field() -> None:
    document = REFERENCE.read_text(encoding="utf-8")
    schemas: tuple[type[BaseModel], ...] = (
        SecurityMaskerFileConfig,
        RuntimeConfig,
        StateConfig,
        Defaults,
        DetectorsConfig,
        DetectorToggle,
        JapanesePiiConfig,
        JapaneseNerConfig,
        ToolTrustConfig,
        UserDictionaryConfig,
        EntityConfig,
        RegexConfig,
    )

    fields = {
        field
        for schema in schemas
        for field in schema.model_fields
    }
    missing = sorted(field for field in fields if f"`{field}`" not in document)

    assert not missing, f"設定リファレンスにないfield: {missing}"


def test_generated_templates_expose_nontrivial_user_knobs() -> None:
    config_template = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    dictionary_template = DICTIONARY_TEMPLATE.read_text(encoding="utf-8")

    for field in (
        "detector_timeout_seconds",
        "max_fuzzy_chars",
        "my_number_min_score",
        "corporate_number",
        "skip_code_contexts",
        "trusted_local_tools",
    ):
        assert f"{field}:" in config_template

    for field in ("case_sensitive", "group"):
        assert f"{field}:" in dictionary_template
