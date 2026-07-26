"""structured loggingの補助処理。

Only safe fields may be logged (entity types, counts, timings, irreversible
fingerprints). Original secret values, decrypted mappings, keys, and full prompts
must never be logged. This module never serializes secret-bearing objects; callers
pass explicit, safe key/values.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import structlog


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def get_logger(**initial: Any) -> Any:
    return structlog.get_logger(**initial)


def safe_fingerprint(value: str) -> str:
    """値を開示せずlogを相関するための短い不可逆tag。

    Non-reversible and unsalted-per-process is acceptable here because it only ever
    labels aliases/session-ids in logs, never the original secret.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
