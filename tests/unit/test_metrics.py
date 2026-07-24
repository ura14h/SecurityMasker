"""Metrics + audit tests (§25: only safe fields, session id fingerprinted)."""

from __future__ import annotations

import logging

from securitymasker.metrics import Metrics, audit


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


def test_audit_fingerprints_session_id_and_never_logs_it_raw(caplog) -> None:
    with caplog.at_level(logging.INFO):
        audit("request_masked", session_id="super-secret-session-id", entity_count=3, blocked=0)
    text = "\n".join(r.getMessage() + str(getattr(r, "__dict__", {})) for r in caplog.records)
    # The raw session id must not appear; a fingerprint stands in for it.
    assert "super-secret-session-id" not in text
