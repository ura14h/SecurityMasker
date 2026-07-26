"""中核data model（``doc/00-First-Order.md`` §29）。

Kept dependency-free (stdlib only) so the masking core never imports LiteLLM,
model libraries or provider SDKs. Enums make the string taxonomies explicit and
``mypy --strict`` checkable.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ContextKind(str, Enum):
    """値が見つかった場所。安全な置換形式を制約する（§17）。"""

    PROSE = "prose"
    MARKDOWN_CODE = "markdown_code"          # fenced block
    MARKDOWN_INLINE_CODE = "markdown_inline_code"
    JSON_STRING = "json_string"
    YAML_SCALAR = "yaml_scalar"
    SHELL = "shell"
    SOURCE_CODE = "source_code"
    DIFF = "diff"
    PATCH = "patch"
    URL = "url"
    FILE_PATH = "file_path"
    TOOL_ARGUMENT = "tool_argument"
    TOOL_RESULT = "tool_result"


class ReplacementProfile(str, Enum):
    """aliasが保持すべき構文上の形状（§9）。"""

    PROSE_IDENTIFIER = "prose_identifier"
    HOSTNAME = "hostname"
    EMAIL = "email"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    UUID = "uuid"
    NUMERIC = "numeric"
    FILE_PATH = "file_path"
    URL = "url"
    ENVIRONMENT_REFERENCE = "environment_reference"


class RestorePolicy(str, Enum):
    """clientへ返す前にaliasをどう復元するか（§10）。"""

    LITERAL = "literal"
    ENV_REFERENCE = "env_reference"
    REDACTED = "redacted"
    BLOCK = "block"


class EntityType(str, Enum):
    """検出したentity category。拡張可能で、相互運用のため文字列値を持つ。"""

    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    PROJECT_NAME = "PROJECT_NAME"
    PRODUCT_NAME = "PRODUCT_NAME"
    HOSTNAME = "HOSTNAME"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IP_ADDRESS = "IP_ADDRESS"
    URL = "URL"
    FILE_PATH = "FILE_PATH"
    UUID = "UUID"
    CREDIT_CARD = "CREDIT_CARD"
    API_KEY = "API_KEY"
    OAUTH_TOKEN = "OAUTH_TOKEN"
    JWT = "JWT"
    PRIVATE_KEY = "PRIVATE_KEY"
    DB_CONNECTION_STRING = "DB_CONNECTION_STRING"
    PASSWORD = "PASSWORD"
    GENERIC_SECRET = "GENERIC_SECRET"
    # Japan-specific (Phase 4 detectors; declared now for policy defaults).
    # A place name (city, prefecture, station). Deliberately DISTINCT from
    # JP_ADDRESS: "Yokohama" is not somebody's home address, and conflating them
    # would let a coarse NER hit inherit an address's sensitivity (ADR-0009).
    LOCATION = "LOCATION"
    JP_ADDRESS = "JP_ADDRESS"
    JP_POSTAL_CODE = "JP_POSTAL_CODE"
    JP_MY_NUMBER = "JP_MY_NUMBER"
    JP_PASSPORT_NUMBER = "JP_PASSPORT_NUMBER"
    JP_DRIVER_LICENSE_NUMBER = "JP_DRIVER_LICENSE_NUMBER"
    JP_CORPORATE_NUMBER = "JP_CORPORATE_NUMBER"
    JP_RESIDENCE_CARD = "JP_RESIDENCE_CARD"
    JP_PENSION_NUMBER = "JP_PENSION_NUMBER"
    JP_EMPLOYMENT_INSURANCE_NUMBER = "JP_EMPLOYMENT_INSURANCE_NUMBER"
    JP_HEALTH_INSURANCE_NUMBER = "JP_HEALTH_INSURANCE_NUMBER"
    JP_BANK_ACCOUNT = "JP_BANK_ACCOUNT"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    EMPLOYEE_ID = "EMPLOYEE_ID"
    CUSTOMER_ID = "CUSTOMER_ID"
    # Alias already present in the text (idempotency guard, §11).
    EXISTING_ALIAS = "EXISTING_ALIAS"


@dataclass(frozen=True)
class DetectionResult:
    """原文の``[start, end)``に対する一つのdetector hit。"""

    entity_type: str
    start: int
    end: int
    score: float
    detector: str
    context_kind: str
    replacement_profile: str
    restore_policy: str
    # The value as matched in the ORIGINAL text (surface form). Restoration must
    # reproduce this verbatim (§12).
    original_value: str
    # NFKC/NFC-normalized value used for detection/fingerprinting when merging.
    normalized_value: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span [{self.start}, {self.end})")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class AliasMapping:
    """aliasと暗号化した原値の双方向binding。"""

    entity_type: str
    alias: str
    encrypted_original: bytes
    original_fingerprint: str
    replacement_profile: str
    restore_policy: str
    created_at: datetime
    last_used_at: datetime
    # The surface form to restore to (kept for convenience; the authoritative
    # copy is ``encrypted_original`` — this is only populated in-memory).
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class MaskingSession:
    """一sessionの中核となる可逆state（§7）。"""

    session_id: str
    session_index_key: bytes
    aead_key: bytes
    tenant_id: str | None
    user_id: str | None
    client_type: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    mappings_by_fingerprint: MutableMapping[str, AliasMapping] = field(default_factory=dict)
    mappings_by_alias: MutableMapping[str, AliasMapping] = field(default_factory=dict)


@dataclass(frozen=True)
class MaskingPolicyDecision:
    """解決済みdetectionに対するpolicy評価結果（§29）。"""

    action: str  # "mask" | "block" | "ignore" | "audit"
    replacement_profile: str | None
    restore_policy: str | None
    reason: str
