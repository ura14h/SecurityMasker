"""structured loggingの補助処理。

Only safe fields may be logged (entity types, counts, timings, irreversible
fingerprints). Original secret values, decrypted mappings, keys, and full prompts
must never be logged. This module never serializes secret-bearing objects; callers
pass explicit, safe key/values.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any, TextIO, cast

import structlog


class _StderrProxy:
    """差し替えられる場合も含め、書込み時点のstderrへ転送する。"""

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


_STDERR = cast(TextIO, _StderrProxy())


def configure_logging(level: str = "INFO") -> None:
    """Gatewayのstderrへ簡潔な一行logを出すようstructlogを初期化する。"""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=_STDERR),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(
                colors=False,
                pad_event_to=0,
                pad_level=False,
                sort_keys=False,
            ),
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
