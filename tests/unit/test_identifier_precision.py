"""Digits inside a structural identifier are not PII.

Found by the real-CLI end-to-end test, which the gateway refused outright:

    SecurityMasker blocked this request ... Entity type: CREDIT_CARD

Nothing sensitive was present. Codex puts several UUIDs in every request
(session, thread, installation, window, prompt-cache key), the final leak gate
walks every string in the payload, and a UUID's hex digits eventually match a
digit-shaped rule by chance. Measured over 3000 random UUIDv7 values, ~0.5%
matched PHONE, CREDIT_CARD or MY_NUMBER — so refusing a real conversation was a
matter of time, not bad luck.

The rule is narrow: only findings that are a PROPER substring of an identifier are
dropped, so a UUID registered as a secret in its own right still fires.
"""

from __future__ import annotations

import secrets
import time
import uuid

import pytest

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.detectors.identifiers import identifier_spans, inside_identifier

# The exact identifier from the failing run: it contains "03-9210-9274".
CODEX_SESSION_ID = "019f9c69-1f5a-7803-9210-9274c7fa4f67"


def _engine(**config):
    return build_engine(SecurityMaskerConfig.model_validate({"version": 1, **config}))


def _uuid7() -> str:
    ms = int(time.time() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


# --- the span helpers -------------------------------------------------------------


def test_identifier_spans_finds_a_uuid() -> None:
    text = f"session {CODEX_SESSION_ID} started"
    assert identifier_spans(text) == [(8, 8 + len(CODEX_SESSION_ID))]


def test_a_proper_substring_is_inside() -> None:
    spans = identifier_spans(CODEX_SESSION_ID)
    assert inside_identifier(spans, 16, 28)          # the phone-shaped part


def test_the_whole_identifier_is_not_inside_itself() -> None:
    """Covering the whole UUID must still count as a finding, or a UUID secret leaks."""
    spans = identifier_spans(CODEX_SESSION_ID)
    assert not inside_identifier(spans, 0, len(CODEX_SESSION_ID))


def test_a_bare_digit_run_is_not_an_identifier() -> None:
    assert identifier_spans("4111111111111111") == []


# --- detection ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_uuid_is_not_a_phone_number() -> None:
    assert await _engine().detect(CODEX_SESSION_ID) == []


@pytest.mark.asyncio
async def test_random_identifiers_do_not_trip_any_rule() -> None:
    engine = _engine()
    for _ in range(500):
        assert await engine.detect(_uuid7()) == [], "a random identifier matched a PII rule"


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "expected"), [
    ("連絡先は03-9210-9274です。", "PHONE"),
    ("card 4111111111111111", "CREDIT_CARD"),
])
async def test_real_values_in_prose_are_still_detected(text, expected) -> None:
    """The guard must not buy precision with recall."""
    found = {r.entity_type for r in await _engine().detect(text)}
    assert expected in found


@pytest.mark.asyncio
async def test_a_uuid_registered_as_a_secret_is_still_detected() -> None:
    # Customer ids and API keys are often UUIDs. Suppressing the whole-span match
    # would turn this guard into a leak.
    engine = _engine(entities=[{
        "id": "cust", "type": "CUSTOMER_ID", "values": [CODEX_SESSION_ID],
        "replacement_profile": "uuid", "restore_policy": "literal",
    }])
    found = {r.entity_type for r in await engine.detect(f"customer {CODEX_SESSION_ID}")}
    assert "CUSTOMER_ID" in found


@pytest.mark.asyncio
async def test_the_leak_gate_does_not_block_on_client_identifiers() -> None:
    """The gate walks every string, so the client's own metadata reaches it."""
    engine = _engine()
    payload = {
        "model": "gpt-5.6-sol",
        "prompt_cache_key": CODEX_SESSION_ID,
        "client_metadata": {"session_id": CODEX_SESSION_ID,
                            "thread_id": CODEX_SESSION_ID},
        "input": "hello",
    }
    await engine.assert_no_leak_in_payload(payload)   # must not raise
