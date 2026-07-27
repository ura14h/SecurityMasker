"""process内metricとaudit event。

Only safe fields are ever recorded (entity types, counts, timings, fail-open/closed
outcomes, irreversible fingerprints). Original secret values, decrypted mappings,
keys, and full prompts must never pass through here. The counters are a tiny
dependency-free registry; a Prometheus exporter can wrap ``Metrics.snapshot()``.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum

from securitymasker.logging import get_logger, safe_fingerprint
from securitymasker.models import EntityType

_audit_log = get_logger()


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


class Provider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    ADMIN = "admin"
    UNKNOWN = "unknown"


class BlockReason(StrEnum):
    REQUEST_FORMAT = "request_format"
    SESSION_UNRESOLVED = "session_unresolved"
    DETECTOR_TIMEOUT = "detector_timeout"
    DETECTOR_FAILURE = "detector_failure"
    STORE = "store"
    LEAK_GUARD = "leak_guard"
    MASKING = "masking"


class StoreOperation(StrEnum):
    READINESS = "readiness"
    REQUEST = "request"
    RESPONSE_BINDING = "response_binding"


class StreamErrorReason(StrEnum):
    PROCESSING = "processing"
    CANCELLED = "cancelled"
    RESPONSE_BINDING = "response_binding"


class AuditEvent(StrEnum):
    REQUEST_MASKED = "request_masked"
    REQUEST_BLOCKED = "request_blocked"
    STORE_ERROR = "store_error"
    STREAM_ERROR = "stream_error"


@dataclass(frozen=True)
class AuditRecord:
    """任意fieldを受け付けない固定schemaのaudit record。"""

    event: AuditEvent
    provider: Provider
    reason: BlockReason | StoreOperation | StreamErrorReason | None = None
    entity_count: int | None = None
    session_fp: str | None = None

    def fields(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {"provider": self.provider.value}
        if self.reason is not None:
            values["reason"] = self.reason.value
        if self.entity_count is not None:
            values["entity_count"] = self.entity_count
        if self.session_fp is not None:
            values["session_fp"] = self.session_fp
        return values


AuditSink = Callable[[AuditRecord], None]


def emit_audit(record: AuditRecord) -> None:
    """schema済みrecordだけをstructured logへ出力する。"""
    _audit_log.info(record.event.value, **record.fields())


_KNOWN_ENTITIES = frozenset(entity.value for entity in EntityType)


def _entity_label(entity_type: str) -> str:
    # user-defined entity名をlabelへ直接入れるとcardinalityを攻撃者が増やせる。
    return entity_type if entity_type in _KNOWN_ENTITIES else "CUSTOM"


@dataclass
class GatewayTelemetry:
    """Gatewayが使う低cardinality metricと固定schema auditの唯一の入口。"""

    metrics: Metrics = field(default_factory=lambda: METRICS)
    audit_sink: AuditSink = emit_audit

    def request_started(self, provider: Provider) -> None:
        self.metrics.incr("gateway_requests_total", provider=provider.value)

    def request_completed(
        self, provider: Provider, *, status_code: int, duration_ms: float
    ) -> None:
        if status_code < 400:
            outcome = "success"
        elif status_code < 500:
            outcome = "client_error"
        else:
            outcome = "server_error"
        self.metrics.incr(
            "gateway_responses_total", provider=provider.value, outcome=outcome
        )
        self.metrics.observe_ms(
            "gateway_request_duration",
            duration_ms,
            provider=provider.value,
            outcome=outcome,
        )

    def masked(
        self,
        provider: Provider,
        entity_counts: Mapping[str, int],
        *,
        session_id: str,
    ) -> None:
        total = 0
        bounded: dict[str, int] = defaultdict(int)
        for entity_type, count in entity_counts.items():
            if count <= 0:
                continue
            bounded[_entity_label(entity_type)] += count
            total += count
        for entity_type, count in bounded.items():
            self.metrics.incr(
                "gateway_masked_entities_total",
                count,
                provider=provider.value,
                entity=entity_type,
            )
        self.audit_sink(
            AuditRecord(
                event=AuditEvent.REQUEST_MASKED,
                provider=provider,
                entity_count=total,
                session_fp=safe_fingerprint(session_id),
            )
        )

    def blocked(
        self,
        provider: Provider,
        reason: BlockReason,
        *,
        session_id: str | None = None,
    ) -> None:
        self.metrics.incr(
            "gateway_blocks_total", provider=provider.value, reason=reason.value
        )
        if reason is BlockReason.DETECTOR_TIMEOUT:
            self.metrics.incr(
                "gateway_detector_timeouts_total", provider=provider.value
            )
        self.audit_sink(
            AuditRecord(
                event=AuditEvent.REQUEST_BLOCKED,
                provider=provider,
                reason=reason,
                session_fp=safe_fingerprint(session_id) if session_id else None,
            )
        )

    def store_error(self, provider: Provider, operation: StoreOperation) -> None:
        self.metrics.incr(
            "gateway_store_errors_total",
            provider=provider.value,
            operation=operation.value,
        )
        self.audit_sink(
            AuditRecord(
                event=AuditEvent.STORE_ERROR,
                provider=provider,
                reason=operation,
            )
        )

    def stream_error(self, provider: Provider, reason: StreamErrorReason) -> None:
        self.metrics.incr(
            "gateway_stream_errors_total",
            provider=provider.value,
            reason=reason.value,
        )
        self.audit_sink(
            AuditRecord(
                event=AuditEvent.STREAM_ERROR,
                provider=provider,
                reason=reason,
            )
        )


def provider_for_path(path: str) -> Provider:
    """routeを固定集合へ畳み込み、path自体をmetric labelにしない。"""
    if path in {"/responses", "/v1/responses", "/models", "/v1/models"}:
        return Provider.OPENAI
    if path in {"/messages", "/v1/messages"}:
        return Provider.ANTHROPIC
    if path in {"/health", "/ready"}:
        return Provider.ADMIN
    return Provider.UNKNOWN
