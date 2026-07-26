"""process内metricとaudit event（§25）。

Only safe fields are ever recorded (entity types, counts, timings, fail-open/closed
outcomes, irreversible fingerprints). Original secret values, decrypted mappings,
keys, and full prompts must never pass through here (§25). The counters are a tiny
dependency-free registry; a Prometheus exporter can wrap ``Metrics.snapshot()``.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from securitymasker.logging import get_logger, safe_fingerprint

_audit_log = get_logger(component="securitymasker.audit")


@dataclass
class Metrics:
    _counters: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[_key(name, labels)] += value

    def observe_ms(self, name: str, millis: float, **labels: str) -> None:
        with self._lock:
            self._counters[_key(name + "_ms_sum", labels)] += millis
            self._counters[_key(name + "_count", labels)] += 1

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._counters)

    @contextmanager
    def timer(self, name: str, **labels: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe_ms(name, (time.perf_counter() - start) * 1000.0, **labels)


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    tags = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{tags}}}"


# Process-wide default registry.
METRICS = Metrics()


def audit(event: str, *, session_id: str | None = None, **safe_fields: Any) -> None:
    """安全なfieldだけを含むstructured audit eventを出力する（§25）。

    ``session_id`` is logged as an irreversible fingerprint, never in the clear.
    Callers must not pass original secrets; this does no scrubbing of its own.
    """
    fields = dict(safe_fields)
    if session_id is not None:
        fields["session_fp"] = safe_fingerprint(session_id)
    _audit_log.info(event, **fields)
