"""Configuration + user dictionary loading (§12).

The dictionary YAML declares entities (with one or more surface forms) and optional
user regexes. Plaintext secrets must NOT be committed to YAML — such values come
from the environment via ``value_from_env`` (§12, §25). Loading validates enums and
regex compilation up front so a bad config fails at startup, not mid-request (§12).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from securitymasker.detectors.base import SensitiveDataDetector
from securitymasker.detectors.date_of_birth import DateOfBirthDetector
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.existing_alias import ExistingAliasDetector
from securitymasker.detectors.formats import FormatsDetector
from securitymasker.detectors.japanese_address import CompositeAddressDetector
from securitymasker.detectors.japanese_my_number import JapaneseMyNumberDetector
from securitymasker.detectors.japanese_phone import JapanesePhoneDetector
from securitymasker.detectors.japanese_postal_code import JapanesePostalCodeDetector
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.normalization import NormForm

_VALID_PROFILES = {p.value for p in ReplacementProfile}
_VALID_POLICIES = {p.value for p in RestorePolicy}


class Defaults(BaseModel):
    fail_mode: str = "closed"
    normalization: NormForm = "nfkc"
    merge_surface_forms: bool = False
    preserve_aliases: bool = True
    session_idle_ttl: str = "4h"
    session_absolute_ttl: str = "24h"
    inject_alias_instruction: bool = True

    @field_validator("fail_mode")
    @classmethod
    def _fail_mode(cls, v: str) -> str:
        if v not in {"closed", "open"}:
            raise ValueError("fail_mode must be 'closed' or 'open'")
        return v


class EntityConfig(BaseModel):
    id: str
    type: str
    values: list[str] = Field(default_factory=list)
    value_from_env: str | None = None
    replacement_profile: str
    restore_policy: str = RestorePolicy.LITERAL.value
    priority: int = 100
    case_sensitive: bool = True

    @field_validator("replacement_profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if v not in _VALID_PROFILES:
            raise ValueError(f"unknown replacement_profile: {v}")
        return v

    @field_validator("restore_policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in _VALID_POLICIES:
            raise ValueError(f"unknown restore_policy: {v}")
        return v

    @model_validator(mode="after")
    def _has_values(self) -> EntityConfig:
        if not self.values and not self.value_from_env:
            raise ValueError(f"entity {self.id!r} needs 'values' or 'value_from_env'")
        return self

    def resolved_values(self) -> tuple[str, ...]:
        values = list(self.values)
        if self.value_from_env:
            env = os.environ.get(self.value_from_env)
            if env:
                values.append(env)
        return tuple(values)


class RegexConfig(BaseModel):
    id: str
    pattern: str
    type: str
    replacement_profile: str
    restore_policy: str = RestorePolicy.LITERAL.value
    priority: int = 150
    group: int = 0

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regex {v!r}: {exc}") from exc
        return v


class JapanesePiiConfig(BaseModel):
    enabled: bool = True
    my_number_restore_policy: str = RestorePolicy.BLOCK.value

    @field_validator("my_number_restore_policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in _VALID_POLICIES:
            raise ValueError(f"unknown restore_policy: {v}")
        return v


class PresidioConfig(BaseModel):
    enabled: bool = False
    language: str = "ja"
    model_name: str = "ja_core_news_md"
    min_score: float = 0.5
    skip_code_contexts: bool = True


class NerConfig(BaseModel):
    model: str | None = None  # HF token-classification model id; None disables (§14.1)
    min_score: float = 0.85


class SecurityMaskerConfig(BaseModel):
    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    entities: list[EntityConfig] = Field(default_factory=list)
    patterns: list[RegexConfig] = Field(default_factory=list)
    enable_secret_detector: bool = True
    enable_format_detectors: bool = True
    japanese_pii: JapanesePiiConfig = Field(default_factory=JapanesePiiConfig)
    presidio: PresidioConfig = Field(default_factory=PresidioConfig)
    ner: NerConfig = Field(default_factory=NerConfig)

    @model_validator(mode="after")
    def _unique_ids(self) -> SecurityMaskerConfig:
        ids = [e.id for e in self.entities] + [p.id for p in self.patterns]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate entity/pattern ids: {sorted(dupes)}")
        return self


def load_config(path: str | Path) -> SecurityMaskerConfig:
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")
    try:
        return SecurityMaskerConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def build_detectors(config: SecurityMaskerConfig) -> list[SensitiveDataDetector]:
    """Assemble the detector pipeline in priority order (§11)."""
    norm = config.defaults.normalization
    dict_entries = [
        DictionaryEntry(
            entity_type=e.type,
            values=e.resolved_values(),
            replacement_profile=e.replacement_profile,
            restore_policy=e.restore_policy,
            priority=e.priority,
            case_sensitive=e.case_sensitive,
        )
        for e in config.entities
    ]
    regex_entries = [
        RegexEntry(
            pattern=p.pattern,
            entity_type=p.type,
            replacement_profile=p.replacement_profile,
            restore_policy=p.restore_policy,
            priority=p.priority,
            group=p.group,
        )
        for p in config.patterns
    ]

    detectors: list[SensitiveDataDetector] = [ExistingAliasDetector()]
    if dict_entries:
        detectors.append(DictionaryDetector(dict_entries, normalization=norm))
    if regex_entries:
        detectors.append(RegexDetector(regex_entries, name="user_regex"))
    if config.enable_secret_detector:
        detectors.append(build_secret_detector())
    if config.enable_format_detectors:
        detectors.append(FormatsDetector())
    if config.japanese_pii.enabled:
        detectors.extend([
            JapaneseMyNumberDetector(restore_policy=config.japanese_pii.my_number_restore_policy),
            JapanesePhoneDetector(),
            JapanesePostalCodeDetector(),
            CompositeAddressDetector(),
            DateOfBirthDetector(),
        ])
    if config.presidio.enabled:
        from securitymasker.detectors.presidio import PresidioDetector

        detectors.append(
            PresidioDetector(
                language=config.presidio.language,
                model_name=config.presidio.model_name,
                min_score=config.presidio.min_score,
                skip_code_contexts=config.presidio.skip_code_contexts,
            )
        )
    if config.ner.model:
        from securitymasker.detectors.japanese_ner import JapaneseNerDetector

        detectors.append(
            JapaneseNerDetector(model=config.ner.model, min_score=config.ner.min_score)
        )
    return detectors


def build_leak_scanners(config: SecurityMaskerConfig) -> list[SensitiveDataDetector]:
    """High-precision, deterministic detectors for the final-payload block guard.

    Kept to low-false-positive detectors only (registered secrets, secret patterns,
    My Number's checksum): they scan structural/unknown fields too, so a fuzzy
    detector here would over-block legitimate config values (doc/06 P0-4).
    """
    scanners: list[SensitiveDataDetector] = []
    if config.enable_secret_detector:
        scanners.append(build_secret_detector())
    if config.japanese_pii.enabled:
        scanners.append(
            JapaneseMyNumberDetector(restore_policy=config.japanese_pii.my_number_restore_policy)
        )
    return scanners


def build_engine(config: SecurityMaskerConfig) -> MaskingEngine:
    registered_literals = tuple(
        value for entity in config.entities for value in entity.resolved_values()
    )
    return MaskingEngine(
        build_detectors(config),
        normalization=config.defaults.normalization,
        merge_surface_forms=config.defaults.merge_surface_forms,
        registered_literals=registered_literals,
        leak_scanners=build_leak_scanners(config),
    )
