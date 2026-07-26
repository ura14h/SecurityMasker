"""SecurityMaskerの中核data model。

masking coreがmodel libraryやprovider SDKへ依存しないよう標準libraryだけで構成する。
文字列taxonomyはEnumで明示し、``mypy --strict``で検査できる形にする。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ContextKind(str, Enum):
    """値が見つかった場所。安全な置換形式を制約する。"""

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
    """aliasが保持すべき構文上の形状。"""

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
    """clientへ返す前にaliasをどう復元するか。"""

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
    # 地名は個人住所とは限らないため、LOCATIONをJP_ADDRESSから分離する。
    # 粗いNER hitへ住所の高いsensitivityを誤って継承させない。
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
    # 既に本文へ存在するSecurityMasker aliasを表し、再マスクを防ぐ。
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
    # 原文で一致したsurface form。復元時はこの表記をそのまま再現する。
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
    """一sessionの中核となる可逆state。"""

    session_id: str
    session_index_key: bytes
    aead_key: bytes
    client_type: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    mappings_by_fingerprint: MutableMapping[str, AliasMapping] = field(default_factory=dict)
    mappings_by_alias: MutableMapping[str, AliasMapping] = field(default_factory=dict)


@dataclass(frozen=True)
class MaskingPolicyDecision:
    """解決済みdetectionに対するpolicy評価結果。"""

    action: str  # "mask" | "block" | "ignore" | "audit"
    replacement_profile: str | None
    restore_policy: str | None
    reason: str
