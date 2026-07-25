"""Configuration + user dictionary loading (§12).

The dictionary YAML declares entities (with one or more surface forms) and optional
user regexes. Plaintext secrets must NOT be committed to YAML — such values come
from the environment via ``value_from_env`` (§12, §25). Loading validates enums and
regex compilation up front so a bad config fails at startup, not mid-request (§12).
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from securitymasker.detectors.base import SensitiveDataDetector
from securitymasker.detectors.date_of_birth import DateOfBirthDetector
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.existing_alias import ExistingAliasDetector
from securitymasker.detectors.formats import FormatsDetector
from securitymasker.detectors.japanese_address import CompositeAddressDetector
from securitymasker.detectors.japanese_corporate_number import JapaneseCorporateNumberDetector
from securitymasker.detectors.japanese_my_number import JapaneseMyNumberDetector
from securitymasker.detectors.japanese_phone import JapanesePhoneDetector
from securitymasker.detectors.japanese_postal_code import JapanesePostalCodeDetector
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.normalization import NormForm
from securitymasker.tool_trust import ToolTrustPolicy

_VALID_PROFILES = {p.value for p in ReplacementProfile}
_VALID_POLICIES = {p.value for p in RestorePolicy}
_SCHEMA_VERSION = 1

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(value: str) -> timedelta:
    """Parse a ``\\d+[smhd]`` duration (e.g. ``4h``, ``30m``) to a ``timedelta``."""
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"invalid duration {value!r} (expected e.g. '4h', '30m', '90s', '1d')")
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise ValueError(f"duration {value!r} must be positive")
    return timedelta(**{_DURATION_UNITS[unit]: amount})


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    @field_validator("session_idle_ttl", "session_absolute_ttl")
    @classmethod
    def _durations(cls, v: str) -> str:
        parse_duration(v)  # validate at load time; conversion happens in the runtime
        return v


class EntityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    id: str
    pattern: str
    type: str
    replacement_profile: str
    restore_policy: str = RestorePolicy.LITERAL.value
    priority: int = 150
    group: int = 0

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
    def _compiles(self) -> RegexConfig:
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex {self.pattern!r}: {exc}") from exc
        if self.group < 0 or self.group > compiled.groups:
            raise ValueError(
                f"capture group {self.group} out of range for {self.pattern!r} "
                f"(has {compiled.groups} group(s))"
            )
        return self


class JapanesePiiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    my_number_restore_policy: str = RestorePolicy.BLOCK.value
    # Confidence gate for My Number (§5.6). 0.0 = catch any valid checksum
    # (fail-closed); ~0.6 = require My Number context words, avoiding false blocks
    # of unrelated checksum-valid 12-digit business ids.
    my_number_min_score: float = 0.0
    # 法人番号 (corporate number) is public info; masking is opt-in (doc/06 §5.7).
    corporate_number: bool = False

    @field_validator("my_number_restore_policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in _VALID_POLICIES:
            raise ValueError(f"unknown restore_policy: {v}")
        return v


class PresidioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    language: str = "ja"
    model_name: str = "ja_core_news_md"
    min_score: float = 0.5
    skip_code_contexts: bool = True


class NerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None  # HF token-classification model id; None disables (§14.1)
    min_score: float = 0.85


class ToolTrustConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Default empty => no tool's arguments are restored to real values (P0-8).
    trusted_local_tools: list[str] = Field(default_factory=list)


class SecurityMaskerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    entities: list[EntityConfig] = Field(default_factory=list)
    patterns: list[RegexConfig] = Field(default_factory=list)
    enable_secret_detector: bool = True
    enable_format_detectors: bool = True
    japanese_pii: JapanesePiiConfig = Field(default_factory=JapanesePiiConfig)
    presidio: PresidioConfig = Field(default_factory=PresidioConfig)
    ner: NerConfig = Field(default_factory=NerConfig)
    tool_trust: ToolTrustConfig = Field(default_factory=ToolTrustConfig)

    @field_validator("version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != _SCHEMA_VERSION:
            raise ValueError(
                f"unsupported config version {v}; this build understands version "
                f"{_SCHEMA_VERSION} only"
            )
        return v

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


def _require_env_values(config: SecurityMaskerConfig) -> None:
    """Fail startup if any ``value_from_env`` is unset/empty (doc/06 P0-6).

    A declared env-backed secret that resolves to nothing would otherwise silently
    disable that entity's masking — a leak. The error names only the env var and
    entity id, never a value (§25).
    """
    for entity in config.entities:
        if entity.value_from_env is None:
            continue
        value = os.environ.get(entity.value_from_env)
        if not value:
            raise ConfigError(
                f"entity {entity.id!r} requires environment variable "
                f"{entity.value_from_env!r}, which is unset or empty"
            )


def build_detectors(config: SecurityMaskerConfig) -> list[SensitiveDataDetector]:
    """Assemble the detector pipeline in priority order (§11)."""
    _require_env_values(config)
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

    detectors: list[SensitiveDataDetector] = []
    if config.defaults.preserve_aliases:
        detectors.append(ExistingAliasDetector())
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
            JapaneseMyNumberDetector(
                restore_policy=config.japanese_pii.my_number_restore_policy,
                min_score=config.japanese_pii.my_number_min_score,
            ),
            JapanesePhoneDetector(),
            JapanesePostalCodeDetector(),
            CompositeAddressDetector(),
            DateOfBirthDetector(),
        ])
        if config.japanese_pii.corporate_number:
            detectors.append(JapaneseCorporateNumberDetector())
    if config.presidio.enabled:
        from securitymasker.detectors.presidio import PresidioDetector

        # Enabled in config => required: a failed load must fail startup, not
        # silently no-op (doc/06 P0-6).
        detectors.append(
            PresidioDetector(
                language=config.presidio.language,
                model_name=config.presidio.model_name,
                min_score=config.presidio.min_score,
                skip_code_contexts=config.presidio.skip_code_contexts,
                required=True,
            )
        )
    if config.ner.model:
        from securitymasker.detectors.japanese_ner import JapaneseNerDetector

        detectors.append(
            JapaneseNerDetector(
                model=config.ner.model, min_score=config.ner.min_score, required=True
            )
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
        fail_mode=config.defaults.fail_mode,
        tool_trust=ToolTrustPolicy(frozenset(config.tool_trust.trusted_local_tools)),
        inject_alias_instruction=config.defaults.inject_alias_instruction,
    )
