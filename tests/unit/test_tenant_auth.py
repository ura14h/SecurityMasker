"""Audit round-2: tenant authenticity + readiness (doc/06 P0-9, P0-1).

Re-audit finding 3: multitenant mode trusted a free-form client header, so any
client could claim any tenant. The tenant must now be proven by an authenticator
that holds a startup-required secret; an unproven or forged tenant fails closed.

Finding 8: readiness must reflect the session store, and the dev transparent mode
must not be usable in front of a real provider.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.errors import ConfigError, SessionError
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

SECRET = "test-tenant-secret"


def _engine():
    return build_engine(SecurityMaskerConfig.model_validate({"version": 1}))


def _tenant_proof(tenant: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), tenant.encode(), sha256).hexdigest()


@pytest.fixture
def multitenant(monkeypatch):
    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"body": body})
        return 200, {"content-type": "application/json"}, b"{}"

    async def fake_streaming(method, url, headers, body, processor=None):
        calls.append({"body": body})
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    rt = GatewayRuntime(_engine(), InMemorySessionStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test",
                        mode="multitenant", tenant_auth_secret=SECRET)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls


@pytest.mark.asyncio
async def test_unproven_tenant_header_rejected(multitenant) -> None:
    client, calls = multitenant
    # A bare, client-chosen tenant header must NOT be trusted (finding 3).
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"x-securitymasker-tenant-id": "victim-tenant"})
    assert r.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_forged_tenant_proof_rejected(multitenant) -> None:
    client, calls = multitenant
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"x-securitymasker-tenant-id": "victim",
                                       "x-securitymasker-tenant-auth": _tenant_proof("victim", "wrong")})
    assert r.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_proof_for_other_tenant_rejected(multitenant) -> None:
    client, calls = multitenant
    # A valid proof for tenant A must not authorize a request claiming tenant B.
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"x-securitymasker-tenant-id": "B",
                                       "x-securitymasker-tenant-auth": _tenant_proof("A")})
    assert r.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_valid_tenant_proof_accepted(multitenant) -> None:
    client, calls = multitenant
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"x-securitymasker-tenant-id": "A",
                                       "x-securitymasker-tenant-auth": _tenant_proof("A")})
    assert r.status_code == 200 and len(calls) == 1


def test_multitenant_without_secret_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.setenv("SECURITYMASKER_MODE", "multitenant")
    monkeypatch.delenv("SECURITYMASKER_TENANT_AUTH_SECRET", raising=False)
    with pytest.raises(ConfigError):
        GatewayRuntime.from_env()


# --- finding 8: readiness reflects the store; dev mode can't front a provider ---


class _BrokenStore(InMemorySessionStore):
    async def get_or_create(self, session_id, **kw):
        raise SessionError("store unavailable")


@pytest.mark.asyncio
async def test_readiness_fails_when_store_unavailable(monkeypatch) -> None:
    rt = GatewayRuntime(_engine(), _BrokenStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test")
    app = gwapp.create_app(rt)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
        assert (await c.get("/health")).status_code == 200   # liveness still up
        assert (await c.get("/ready")).status_code == 503     # store is broken


def test_dev_transparent_refuses_real_upstream(monkeypatch) -> None:
    monkeypatch.delenv("SECURITYMASKER_CONFIG", raising=False)
    monkeypatch.setenv("SECURITYMASKER_DEV_TRANSPARENT", "1")
    monkeypatch.setenv("SECURITYMASKER_OPENAI_UPSTREAM", "https://api.openai.com/v1")
    with pytest.raises(ConfigError):
        GatewayRuntime.from_env()


def test_dev_transparent_allows_localhost(monkeypatch) -> None:
    monkeypatch.delenv("SECURITYMASKER_CONFIG", raising=False)
    monkeypatch.setenv("SECURITYMASKER_DEV_TRANSPARENT", "1")
    monkeypatch.setenv("SECURITYMASKER_OPENAI_UPSTREAM", "http://127.0.0.1:8081")
    monkeypatch.setenv("SECURITYMASKER_ANTHROPIC_UPSTREAM", "http://127.0.0.1:8081")
    rt = GatewayRuntime.from_env()
    assert rt.engine is None
