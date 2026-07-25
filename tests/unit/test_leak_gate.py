"""Audit round-2 regression tests: final payload + header leak gate (doc/06 P0-4).

Reproduces the leaks found in re-audit finding 1 and 2, then proves they are
refused. A leak == the forwarder being called at all, so every blocked case
asserts ``calls == []``.

All data is synthetic (§30): the "card" is a Luhn-valid test number, hosts are
.invalid/.example, and no real person or secret appears.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"
# Luhn-valid synthetic test card (a standard test-suite number, not a real card).
CARD = "4111111111111111"
EMAIL = "taro@example.co.jp"
JP_PHONE = "090-1234-5678"


def _runtime(mode: str = "local") -> GatewayRuntime:
    config = SecurityMaskerConfig.model_validate({
        "version": 1,
        "entities": [
            {"id": "p", "type": "PERSON", "values": [PERSON],
             "replacement_profile": "prose_identifier", "restore_policy": "literal"},
            {"id": "h", "type": "HOSTNAME", "values": ["SecretHost"],
             "replacement_profile": "hostname", "restore_policy": "literal",
             "case_sensitive": False},
        ],
    })
    return GatewayRuntime(build_engine(config), InMemorySessionStore(),
                          openai_upstream="http://oai.test",
                          anthropic_upstream="http://anthropic.test", mode=mode)


@pytest.fixture
def app_and_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return Response(b"", media_type="text/event-stream")

    async def fake_buffered(method, url, headers, body):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return 200, {"content-type": "application/json"}, b"{}"

    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(_runtime())),
                               base_url="http://gw")
    return client, calls


# --- finding 1: deterministic PII in an unknown field must block ----------------


@pytest.mark.parametrize("value", [CARD, EMAIL, JP_PHONE])
@pytest.mark.asyncio
async def test_deterministic_pii_in_unknown_field_blocked(app_and_calls, value) -> None:
    client, calls = app_and_calls
    async with client:
        r = await client.post("/responses", json={"input": "hi", "metadata": {"note": value}})
    assert r.status_code == 400, f"{value} was not blocked"
    assert calls == [], f"{value} reached the upstream"


@pytest.mark.asyncio
async def test_case_variant_registered_value_in_unknown_field_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    # Registered case-insensitively as "SecretHost"; a case variant must not slip
    # through the final guard in a field the adapter never masks.
    async with client:
        r = await client.post("/responses", json={"input": "hi", "metadata": {"h": "secrethost"}})
    assert r.status_code == 400 and calls == []


@pytest.mark.asyncio
async def test_masked_request_with_email_still_forwards(app_and_calls) -> None:
    # The guard must not block on our OWN aliases: an email in normal content is
    # masked to an alias, and that alias must not re-trigger the email scanner.
    client, calls = app_and_calls
    async with client:
        r = await client.post("/responses", json={"input": f"連絡先は{EMAIL}です"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert EMAIL.encode() not in calls[0]["body"]


# --- finding 2: header leakage --------------------------------------------------


@pytest.mark.asyncio
async def test_custom_header_with_registered_secret_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    # HTTP header values are byte-oriented, so use the ASCII registered value.
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"X-Custom-Note": "host is SecretHost"})
    assert r.status_code == 400 and calls == []


@pytest.mark.asyncio
async def test_unknown_custom_header_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await client.post("/responses", json={"input": "hi"},
                          headers={"X-Custom-Note": "harmless"})
    assert len(calls) == 1
    assert "x-custom-note" not in {k.lower() for k in calls[0]["headers"]}


@pytest.mark.asyncio
async def test_anthropic_api_key_not_sent_to_openai(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await client.post("/responses", json={"input": "hi"},
                          headers={"x-api-key": "sk-ant-should-not-travel"})
    assert len(calls) == 1
    assert "x-api-key" not in {k.lower() for k in calls[0]["headers"]}


@pytest.mark.asyncio
async def test_openai_headers_not_sent_to_anthropic(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await client.post("/v1/messages", json={"max_tokens": 1, "messages": []},
                          headers={"chatgpt-account-id": "acct-123",
                                   "openai-organization": "org-123"})
    assert len(calls) == 1
    fwd = {k.lower() for k in calls[0]["headers"]}
    assert "chatgpt-account-id" not in fwd and "openai-organization" not in fwd


@pytest.mark.asyncio
async def test_provider_auth_reaches_its_own_upstream(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await client.post("/v1/messages", json={"max_tokens": 1, "messages": []},
                          headers={"x-api-key": "anthropic-key", "anthropic-version": "2023-06-01"})
    fwd = {k.lower(): v for k, v in calls[0]["headers"].items()}
    assert fwd.get("x-api-key") == "anthropic-key"      # reaches Anthropic
    assert fwd.get("anthropic-version") == "2023-06-01"


# --- Codex client headers: forwarded, but never unscanned --------------------------
#
# Codex sends session-id/thread-id/originator/chatgpt-account-id plus an open-ended
# x-codex-* family. Dropping them breaks the client, so they are allowed through —
# which means they are an egress path like any other and must go through the same
# PII gate as the body. The wildcard is the dangerous half: it admits header NAMES
# we have never seen, so their VALUES cannot be assumed safe.


@pytest.mark.asyncio
async def test_codex_headers_are_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await client.post("/responses", json={"input": "hi"}, headers={
            "session-id": "codex-session-1", "thread-id": "codex-thread-1",
            "originator": "codex_cli_rs", "chatgpt-account-id": "acct-1",
            "x-codex-something": "v",
        })
    fwd = {k.lower(): v for k, v in calls[0]["headers"].items()}
    for name in ("session-id", "thread-id", "originator", "chatgpt-account-id",
                 "x-codex-something"):
        assert name in fwd, f"Codex header {name} was dropped"


@pytest.mark.asyncio
async def test_wildcard_codex_header_with_pii_is_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"X-Codex-User-Context": EMAIL})
    assert r.status_code == 400, "PII in a wildcard header reached the upstream"
    assert calls == []


@pytest.mark.asyncio
async def test_wildcard_codex_header_without_pii_passes(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"X-Codex-Turn": "3"})
    assert r.status_code == 200 and len(calls) == 1
    assert "x-codex-turn" in {k.lower() for k in calls[0]["headers"]}
