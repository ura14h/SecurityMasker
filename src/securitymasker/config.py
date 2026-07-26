"""設定とユーザーdictionaryの読み込み（§12）。

The dictionary YAML declares entities (with one or more surface forms) and optional
user regexes. Plaintext secrets must NOT be committed to YAML — such values come
from the environment via ``value_from_env`` (§12, §25). Loading validates enums and
regex compilation up front so a bad config fails at startup, not mid-request (§12).
"""

from __future__ import annotations

import os
import re
import stat
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from securitymasker.detectors.base import SensitiveDataDetector
from securitymasker.detectors.date_of_birth import DateOfBirthDetector
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.existing_alias import ExistingAliasDetector
from securitymasker.detectors.formats import FormatsDetector
from securitymasker.detectors.japanese_address import CompositeAddressDetector
from securitymasker.detectors.japanese_corporate_number import JapaneseCorporateNumberDetector
from securitymasker.detectors.japanese_identifiers import JapaneseIdentifierDetector
from securitymasker.detectors.japanese_my_number import JapaneseMyNumberDetector
from securitymasker.detectors.japanese_phone import JapanesePhoneDetector
from securitymasker.detectors.japanese_postal_code import JapanesePostalCodeDetector
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.detectors.safety import check_regex_safety
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.models_fetch import ADOPTED_MODEL, ADOPTED_REVISION
from securitymasker.normalization import NormForm
from securitymasker.tool_trust import ToolTrustPolicy

_VALID_PROFILES = {p.value for p in ReplacementProfile}
_VALID_POLICIES = {p.value for p in RestorePolicy}
_LEGACY_SCHEMA_VERSION = 1
_CURRENT_SCHEMA_VERSION = 2
_PRIVATE_FILE_BITS = stat.S_IRWXG | stat.S_IRWXO

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(value: str) -> timedelta:
    """``\\d+[smhd]``形式（例：``4h``、``30m``）を``timedelta``へ変換する。"""
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
    # detectorごとのwall-clock budget（doc/06 P1-5）。0で無効。
    detector_timeout_seconds: float = Field(default=10.0, ge=0.0, le=300.0)
    # 一requestがmodel-backed detectorへ渡せるtext量の上限。
    # detectors. Over it the request is REFUSED, because the alternative —
    # scanning a prefix and reporting success — is a silent blind spot
    # (ADR-0011). Sized for a very large prompt; raise it only with the
    # inference cost in mind.
    max_fuzzy_chars: int = Field(default=200_000, ge=1_000, le=10_000_000)

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
    priority: int = Field(default=100, ge=0, le=1000)
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
        # 空またはwhitespaceだけの値は全位置に一致し得るため拒否する。
        # always a config mistake; duplicates silently double the work. Both are
        # rejected at load rather than tolerated (doc/06 P1-2).
        # 問題のVALUEは登録済みsecretなのでerrorに含めない。
        # validation error surfaces in startup logs, the CLI and tracebacks (§25).
        # 修正に十分でlogにも安全な位置だけを報告する。
        for index, value in enumerate(self.values):
            if not value.strip():
                raise ValueError(f"entity {self.id!r}: values[{index}] is empty")
        seen: dict[str, int] = {}
        for index, value in enumerate(self.values):
            if value in seen:
                raise ValueError(
                    f"entity {self.id!r}: values[{index}] duplicates values[{seen[value]}]"
                )
            seen[value] = index
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
    priority: int = Field(default=150, ge=0, le=1000)
    group: int = Field(default=0, ge=0)

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
        # user regexにはsecret literalが含まれ得るためPATTERNを再表示しない。
        # secret it is meant to match, and this message reaches startup logs and
        # tracebacks (§25). The rule id is enough to locate it.
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(
                f"pattern {self.id!r} is not a valid regular expression "
                f"({type(exc).__name__})"
            ) from None
        if self.group < 0 or self.group > compiled.groups:
            raise ValueError(
                f"pattern {self.id!r}: capture group {self.group} is out of range "
                f"(the expression has {compiled.groups} group(s))"
            )
        # `re`は実行中に割り込めないため既知のcatastrophic-backtracking形式をload時に拒否する。
        # interrupted mid-match, so a bad user pattern is a DoS (doc/06 P1-5).
        check_regex_safety(self.pattern, rule_id=self.id)
        return self


class JapanesePiiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    my_number_restore_policy: str = RestorePolicy.BLOCK.value
    # My Numberのconfidence gate（§5.6）。0.0なら有効checksumをすべて対象にする。
    # (fail-closed); ~0.6 = require My Number context words, avoiding false blocks
    # of unrelated checksum-valid 12-digit business ids.
    my_number_min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # 法人番号 (corporate number) is public info; masking is opt-in (doc/06 §5.7).
    corporate_number: bool = False

    @field_validator("my_number_restore_policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in _VALID_POLICIES:
            raise ValueError(f"unknown restore_policy: {v}")
        return v


class NerConfig(BaseModel):
    """HF日本語NER設定。v1互換設定では``model``未指定時に無効。"""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None  # HF token-classification model id; None disables (§14.1)
    # modelのcommit revision。未固定modelの変化を防ぐためmodel指定時は必須。
    # id silently follows `main`, so the weights doing the detecting could change
    # under us between deploys (ADR-0009).
    revision: str | None = None
    # 0.7 is the measured optimum for the adopted model on tests/evaluation:
    # below it prose false positives appear, above it PERSON recall falls off.
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)
    # request処理中のloadではnetworkへ到達させず、modelは明示stepで取得する。
    # an explicit preparation step or baked into the image.
    local_files_only: bool = True
    skip_code_contexts: bool = True
    # A model with no artifact manifest cannot be verified. Accepting one is an
    # explicit, argued-for choice, never a default (ADR-0010).
    allow_unverified_model: bool = False

    @model_validator(mode="after")
    def _pinned(self) -> NerConfig:
        if self.model and not self.revision:
            raise ValueError(
                "ner.revision is required when ner.model is set: pin the model's "
                "commit revision so the weights cannot change between deploys"
            )
        return self


class ToolTrustConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 既定は空で、どのtool argumentも実値へ復元しない（P0-8）。
    trusted_local_tools: list[str] = Field(default_factory=list)


class RuntimeConfig(BaseModel):
    """単一processの利用者向けruntime設定（ADR-0012）。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["chatgpt", "claude"]
    host: Literal["127.0.0.1", "::1", "localhost"] = "127.0.0.1"
    port: int = Field(default=4000, ge=1, le=65535)


class StateConfig(BaseModel):
    """SQLiteとmaster keyの明示path。pathはconfig基準で絶対化して保持する。"""

    model_config = ConfigDict(extra="forbid")

    database: Path
    key: Path


class DetectorToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class JapaneseNerV2Config(NerConfig):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: Literal["tsmatz/xlm-roberta-ner-japanese"] | None = ADOPTED_MODEL
    revision: Literal["aba094e118d5ffc622e9b25e07edc49f9dd85feb"] | None = (
        ADOPTED_REVISION
    )
    local_files_only: Literal[True] = True
    allow_unverified_model: Literal[False] = False

    @model_validator(mode="after")
    def _enabled_requires_standard_model(self) -> JapaneseNerV2Config:
        if self.enabled and (self.model is None or self.revision is None):
            raise ValueError(
                "enabled japanese_ner requires the pinned standard model and revision"
            )
        return self


class DetectorsV2Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secrets: DetectorToggle = Field(default_factory=DetectorToggle)
    formats: DetectorToggle = Field(default_factory=DetectorToggle)
    japanese_pii: JapanesePiiConfig = Field(default_factory=JapanesePiiConfig)
    japanese_ner: JapaneseNerV2Config = Field(default_factory=JapaneseNerV2Config)


class UserDictionaryConfig(BaseModel):
    """単一の``securitymasker.dict`` schema。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    entities: list[EntityConfig] = Field(default_factory=list)
    patterns: list[RegexConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> UserDictionaryConfig:
        ids = [entry.id for entry in self.entities] + [pattern.id for pattern in self.patterns]
        duplicates = {entry_id for entry_id in ids if ids.count(entry_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate entity/pattern ids: {sorted(duplicates)}")
        return self


class SecurityMaskerConfigV2(BaseModel):
    """利用者向け``securitymasker.config`` v2 schema。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    runtime: RuntimeConfig
    state: StateConfig
    dictionary: str
    defaults: Defaults = Field(default_factory=Defaults)
    detectors: DetectorsV2Config = Field(default_factory=DetectorsV2Config)
    tool_trust: ToolTrustConfig = Field(default_factory=ToolTrustConfig)

    @field_validator("dictionary")
    @classmethod
    def _dictionary_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dictionary path must not be empty")
        return value


class SecurityMaskerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    entities: list[EntityConfig] = Field(default_factory=list)
    patterns: list[RegexConfig] = Field(default_factory=list)
    enable_secret_detector: bool = True
    enable_format_detectors: bool = True
    japanese_pii: JapanesePiiConfig = Field(default_factory=JapanesePiiConfig)
    ner: NerConfig = Field(default_factory=NerConfig)
    tool_trust: ToolTrustConfig = Field(default_factory=ToolTrustConfig)
    # v2だけが持つ利用者向けruntime/state metadata。engine側は従来fieldを使う。
    runtime: RuntimeConfig | None = None
    state: StateConfig | None = None
    dictionary: Path | None = None
    config_path: Path | None = Field(default=None, exclude=True, repr=False)

    @field_validator("version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v not in {_LEGACY_SCHEMA_VERSION, _CURRENT_SCHEMA_VERSION}:
            raise ValueError(
                f"unsupported config version {v}; this build understands versions "
                f"{_LEGACY_SCHEMA_VERSION} and {_CURRENT_SCHEMA_VERSION}"
            )
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> SecurityMaskerConfig:
        ids = [e.id for e in self.entities] + [p.id for p in self.patterns]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate entity/pattern ids: {sorted(dupes)}")
        return self


def _safe_validation_message(exc: ValidationError) -> str:
    """問題の入力値を含めずにValidationErrorを表示する（§25、doc/06 P0-4）。

    Pydantic's default rendering embeds the rejected value, and in this config the
    rejected value is often a registered secret. We emit only the field LOCATION
    and the stable error CODE (``type``) — never ``input``, and never ``msg``,
    because a message can quote the input (a custom validator, a future Pydantic
    version, or a nested exception's text). Location + code is enough to fix the
    config and is safe in a startup log, a CLI message, or a traceback.

    ``from exc`` is deliberately NOT used at the call site: the chained traceback
    would carry the original exception, which still holds the input values.
    """
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('type', 'invalid')}")
    return "; ".join(parts) or "invalid configuration"


def adjacent_config_directory() -> Path:
    """binaryまたはroot scriptのdirectoryを返す。

    PyInstaller one-fileの``sys._MEIPASS``は一時展開先なので使用しない。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def resolve_config_path(path: str | Path | None = None) -> Path:
    """CLI、環境変数、隣接fileの順でconfigを解決する（ADR-0012）。"""
    if path is not None and str(path).strip():
        return Path(path).expanduser().resolve()
    environment_path = os.environ.get("SECURITYMASKER_CONFIG")
    if environment_path:
        return Path(environment_path).expanduser().resolve()
    adjacent = adjacent_config_directory() / "securitymasker.config"
    if adjacent.is_file():
        return adjacent.resolve()
    raise ConfigError(
        "securitymasker.config was not found; use --config, "
        "SECURITYMASKER_CONFIG, or place it beside the executable"
    )


def _read_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        # PyYAML errorはsecretを含み得る問題行を引用するため、そのまま返さない。
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError(f"{path.name} is not valid YAML{where}") from None
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot read {label} {path.name}: {detail}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} root must be a mapping, got {type(raw).__name__}")
    return raw


def _require_private_file(path: Path, *, label: str) -> None:
    """user以外が読めるv2機密fileを拒否する。"""
    try:
        file_stat = path.stat()
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot inspect {label} {path.name}: {detail}") from None
    if not stat.S_ISREG(file_stat.st_mode):
        raise ConfigError(f"{label} {path.name} must be a regular file")
    if os.name == "posix":
        if file_stat.st_uid != os.getuid():
            raise ConfigError(f"{label} {path.name} must be owned by the current user")
        if stat.S_IMODE(file_stat.st_mode) & _PRIVATE_FILE_BITS:
            raise ConfigError(
                f"{label} {path.name} has unsafe permissions; use chmod 600"
            )


def _require_private_directory(path: Path, *, label: str) -> None:
    try:
        directory_stat = path.stat()
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot inspect {label} {path.name}: {detail}") from None
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ConfigError(f"{label} {path.name} must be a directory")
    if os.name == "posix":
        if directory_stat.st_uid != os.getuid():
            raise ConfigError(f"{label} {path.name} must be owned by the current user")
        if stat.S_IMODE(directory_stat.st_mode) & _PRIVATE_FILE_BITS:
            raise ConfigError(
                f"{label} {path.name} has unsafe permissions; use chmod 700"
            )


def _resolve_from_config(config_path: Path, configured_path: str | Path) -> Path:
    candidate = Path(configured_path).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _load_v2(config_path: Path, raw: dict[str, Any]) -> SecurityMaskerConfig:
    _require_private_file(config_path, label="config")
    try:
        v2 = SecurityMaskerConfigV2.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid config {config_path.name}: {_safe_validation_message(exc)}"
        ) from None

    dictionary_path = _resolve_from_config(config_path, v2.dictionary)
    _require_private_file(dictionary_path, label="dictionary")
    dictionary_raw = _read_yaml_mapping(dictionary_path, label="dictionary")
    try:
        dictionary = UserDictionaryConfig.model_validate(dictionary_raw)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid dictionary {dictionary_path.name}: {_safe_validation_message(exc)}"
        ) from None

    state = StateConfig(
        database=_resolve_from_config(config_path, v2.state.database),
        key=_resolve_from_config(config_path, v2.state.key),
    )
    _require_private_directory(state.key.parent, label="state directory")
    if state.database.parent != state.key.parent:
        _require_private_directory(state.database.parent, label="state database directory")
    _require_private_file(state.key, label="master key")
    try:
        key_size = state.key.stat().st_size
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot inspect master key {state.key.name}: {detail}") from None
    if key_size != 32:
        raise ConfigError("master key must contain exactly 32 bytes")
    if state.database.exists():
        _require_private_file(state.database, label="state database")

    ner = v2.detectors.japanese_ner.model_copy(
        update={
            "model": (
                v2.detectors.japanese_ner.model
                if v2.detectors.japanese_ner.enabled
                else None
            )
        }
    )
    return SecurityMaskerConfig(
        version=2,
        defaults=v2.defaults,
        entities=dictionary.entities,
        patterns=dictionary.patterns,
        enable_secret_detector=v2.detectors.secrets.enabled,
        enable_format_detectors=v2.detectors.formats.enabled,
        japanese_pii=v2.detectors.japanese_pii,
        ner=NerConfig.model_validate(ner.model_dump(exclude={"enabled"})),
        tool_trust=v2.tool_trust,
        runtime=v2.runtime,
        state=state,
        dictionary=dictionary_path,
        config_path=config_path,
    )


def load_config(path: str | Path) -> SecurityMaskerConfig:
    config_path = Path(path).expanduser().resolve()
    raw = _read_yaml_mapping(config_path, label="config")
    if raw.get("version") == _CURRENT_SCHEMA_VERSION:
        return _load_v2(config_path, raw)
    try:
        return SecurityMaskerConfig.model_validate(raw)
    except ValidationError as exc:
        # chained tracebackで入力値を再露出させないため`from exc`を使わない。
        raise ConfigError(
            f"invalid config {Path(path).name}: {_safe_validation_message(exc)}"
        ) from None


def _require_env_values(config: SecurityMaskerConfig) -> None:
    """``value_from_env``が未設定または空なら起動を失敗させる（doc/06 P0-6）。

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
    """priority順にdetector pipelineを構築する（§11）。"""
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
        detectors.append(JapaneseIdentifierDetector())
        if config.japanese_pii.corporate_number:
            detectors.append(JapaneseCorporateNumberDetector())
    if config.ner.model:
        from securitymasker.detectors.japanese_ner import JapaneseNerDetector

        detectors.append(
            JapaneseNerDetector(
                model=config.ner.model,
                revision=config.ner.revision,
                min_score=config.ner.min_score,
                local_files_only=config.ner.local_files_only,
                skip_code_contexts=config.ner.skip_code_contexts,
                allow_unverified_model=config.ner.allow_unverified_model,
                required=True,
            )
        )
    return detectors


# fuzzyなmodel-backed detectorは誤検出で全requestをblockし得るため最終guardから除く。
# scan structural/unknown fields too, where an NER false positive would block a
# legitimate request. Every DETERMINISTIC detector is included (doc/06 P0-4).
_FUZZY_DETECTOR_NAMES = frozenset({"jp_ner", "existing_alias"})


def build_leak_scanners(
    config: SecurityMaskerConfig,
    detectors: list[SensitiveDataDetector] | None = None,
) -> list[SensitiveDataDetector]:
    """最終payloadのblock-only guardで使うdeterministic detector（doc/06 P0-4）。

    Invariant 1 (never send original secrets) outranks "unknown fields pass
    through": anything a deterministic detector would have masked in text must not
    reach the upstream through an unknown, structural, or schema field either. So
    this mirrors the full masking pipeline minus the fuzzy NER detectors — the
    dictionary detector is included precisely so ``case_sensitive: false`` entries
    are matched the same way here as during masking.

    Aliases this session already issued are skipped by the caller, so our own
    replacements (an email-shaped alias, a doc-range IPv4, ...) never self-trigger.

    Pass ``detectors`` to REUSE an already-built pipeline. Building a second one
    would load the HF model a second time, roughly doubling startup time and
    resident memory; the deterministic detectors here are stateless, so sharing
    the same instances is safe.
    """
    pipeline = detectors if detectors is not None else build_detectors(config)
    return [d for d in pipeline if getattr(d, "name", "") not in _FUZZY_DETECTOR_NAMES]


def build_engine(config: SecurityMaskerConfig) -> MaskingEngine:
    registered_literals = tuple(
        value for entity in config.entities for value in entity.resolved_values()
    )
    # pipelineは一度だけbuildしてleak scannerと共有し、modelの二重loadを防ぐ。
    # build_detectors() would load the HF model again (doc/06 P2 review).
    detectors = build_detectors(config)
    return MaskingEngine(
        detectors,
        normalization=config.defaults.normalization,
        merge_surface_forms=config.defaults.merge_surface_forms,
        registered_literals=registered_literals,
        leak_scanners=build_leak_scanners(config, detectors),
        fail_mode=config.defaults.fail_mode,
        tool_trust=ToolTrustPolicy(frozenset(config.tool_trust.trusted_local_tools)),
        inject_alias_instruction=config.defaults.inject_alias_instruction,
        detector_timeout=config.defaults.detector_timeout_seconds,
        max_fuzzy_chars=config.defaults.max_fuzzy_chars,
    )
