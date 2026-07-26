"""Metrics + audit tests (§25: fixed schema, bounded labels, fingerprint only)."""

from __future__ import annotations

from securitymasker.metrics import (
    AuditEvent,
    AuditRecord,
    BlockReason,
    GatewayTelemetry,
    Metrics,
    Provider,
)


def test_counters_and_labels() -> None:
    m = Metrics()
    m.incr("masked_total", 2, entity="PERSON")
    m.incr("masked_total", 1, entity="PERSON")
    m.incr("masked_total", 5, entity="EMAIL")
    snap = m.snapshot()
    assert snap["masked_total{entity=PERSON}"] == 3
    assert snap["masked_total{entity=EMAIL}"] == 5


def test_timer_records_count_and_sum() -> None:
    m = Metrics()
    with m.timer("mask_latency", protocol="openai"):
        pass
    snap = m.snapshot()
    assert snap["mask_latency_count{protocol=openai}"] == 1
    assert snap["mask_latency_ms_sum{protocol=openai}"] >= 0.0


def test_gateway_telemetry_fingerprints_session_and_bounds_entity_labels() -> None:
    metrics = Metrics()
    records: list[AuditRecord] = []
    telemetry = GatewayTelemetry(metrics=metrics, audit_sink=records.append)
    raw_session = "super-secret-session-id"

    telemetry.masked(
        Provider.OPENAI,
        {"PERSON": 2, "attacker-controlled-label": 3},
        session_id=raw_session,
    )

    snap = metrics.snapshot()
    assert snap["gateway_masked_entities_total{entity=PERSON,provider=openai}"] == 2
    assert snap["gateway_masked_entities_total{entity=CUSTOM,provider=openai}"] == 3
    assert "attacker-controlled-label" not in repr(snap)
    assert len(records) == 1
    assert records[0].event is AuditEvent.REQUEST_MASKED
    assert records[0].entity_count == 5
    assert records[0].session_fp is not None
    assert raw_session not in repr(records[0])


def test_detector_timeout_has_fixed_reason_and_dedicated_counter() -> None:
    metrics = Metrics()
    records: list[AuditRecord] = []
    telemetry = GatewayTelemetry(metrics=metrics, audit_sink=records.append)
    telemetry.blocked(Provider.ANTHROPIC, BlockReason.DETECTOR_TIMEOUT)

    snap = metrics.snapshot()
    assert snap[
        "gateway_blocks_total{provider=anthropic,reason=detector_timeout}"
    ] == 1
    assert snap["gateway_detector_timeouts_total{provider=anthropic}"] == 1
    assert records == [
        AuditRecord(
            event=AuditEvent.REQUEST_BLOCKED,
            provider=Provider.ANTHROPIC,
            reason=BlockReason.DETECTOR_TIMEOUT,
        )
    ]
