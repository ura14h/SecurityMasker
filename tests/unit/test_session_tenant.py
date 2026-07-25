"""Milestone C tests (doc/06 P0-9, P1-1): session stability + tenant separation.

Session resolution must prefer a stable identifier over the per-turn
``previous_response_id``; a request that carries prior-turn aliases with no stable
session must block; and two tenants sharing one session id must never share an
alias table. Upstream calls are stubbed and asserted NOT to happen on a block.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.responses import Response

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.engine import MaskingEngine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.session import resolve_session
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"


# --- P1-1: session resolution priority -----------------------------------------


def test_stable_session_header_wins_over_previous_response_id() -> None:
    r = resolve_session({"session-id": "abc"}, {"previous_response_id": "resp_1"})
    assert r.session_id == "session-id:abc" and r.stable


def test_explicit_header_wins() -> None:
    r = resolve_session({"X-SecurityMasker-Session-ID": "s1", "thread-id": "t"}, None)
    assert r.session_id == "s1" and r.stable


def test_ephemeral_when_nothing_stable() -> None:
    r = resolve_session({}, {})
    assert not r.stable and r.session_id.startswith("eph:")


# --- gateway wiring -------------------------------------------------------------


def _engine() -> MaskingEngine:
    return MaskingEngine(
        [DictionaryDetector([DictionaryEntry(
            EntityType.PERSON.value, (PERSON,),
            ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])],
        registered_literals=(PERSON,))


TENANT_SECRET = "unit-test-secret"


def _proof(tenant: str) -> str:
    import hmac
    from hashlib import sha256

    return hmac.new(TENANT_SECRET.encode(), tenant.encode(), sha256).hexdigest()


def _app(monkeypatch, *, mode="local", tenant_header="x-securitymasker-tenant-id"):
    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"body": body})
        return 200, {"content-type": "application/json"}, b"{}"

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"body": body})
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    rt = GatewayRuntime(_engine(), InMemorySessionStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test",
                        mode=mode, tenant_header=tenant_header,
                        tenant_auth_secret=TENANT_SECRET)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls


@pytest.mark.asyncio
async def test_unresolved_session_with_aliases_blocks(monkeypatch) -> None:
    client, calls = _app(monkeypatch)
    # No stable session id, but the body carries a prior-turn alias shape.
    async with client:
        r = await client.post("/responses", json={"input": "reuse SM_PERSON_ABCDEF now"})
    assert r.status_code == 409 and calls == []  # blocked, nothing forwarded


@pytest.mark.asyncio
async def test_multitenant_without_tenant_blocks(monkeypatch) -> None:
    client, calls = _app(monkeypatch, mode="multitenant")
    async with client:
        r = await client.post("/responses", headers={"X-SecurityMasker-Session-ID": "s1"},
                              json={"input": "hi"})
    assert r.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_same_session_id_different_tenant_are_isolated(monkeypatch) -> None:
    client, calls = _app(monkeypatch, mode="multitenant")
    hdr = {"X-SecurityMasker-Session-ID": "s1"}
    async with client:
        await client.post("/responses",
                          headers={**hdr, "x-securitymasker-tenant-id": "A",
                                   "x-securitymasker-tenant-auth": _proof("A")},
                          json={"input": f"担当は{PERSON}"})
        await client.post("/responses",
                          headers={**hdr, "x-securitymasker-tenant-id": "B",
                                   "x-securitymasker-tenant-auth": _proof("B")},
                          json={"input": f"担当は{PERSON}"})
    assert len(calls) == 2
    # Same session id + same secret, but different tenants -> independent sessions
    # -> different aliases -> different masked bodies (no shared alias table).
    assert calls[0]["body"] != calls[1]["body"]
    assert PERSON.encode() not in calls[0]["body"]
    assert PERSON.encode() not in calls[1]["body"]
