"""Tenant + user identity isolation (§8, doc/06 P0-9).

Tenant-only isolation is correct for one-customer-per-tenant, but two mutually
distrusting users inside one tenant used to share an alias table: user 2 could
present user 1's session id and read their mappings. `tenant_user` mode isolates
them, and this file is the proof — for the boundary itself, and for every way a
caller might try to talk their way across it.

Synthetic identities only (§30).
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.errors import ConfigError, SessionError
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.identity import (
    AUTH_HEADER,
    LOCAL_USER,
    MODE_LOCAL,
    MODE_TENANT,
    MODE_TENANT_USER,
    TENANT_HEADER,
    TIMESTAMP_HEADER,
    USER_HEADER,
    Identity,
    IdentityError,
    resolve_identity,
    sign,
)
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

SECRET = "unit-test-identity-secret"
PERSON = "山田太郎"


def _headers(tenant: str, user: str, *, secret: str = SECRET,
             timestamp: str | None = None, proof: str | None = None) -> dict[str, str]:
    """A complete, fresh assertion. Timestamps are mandatory, so one is included
    by default; pass ``timestamp=""`` to build the (refused) untimed form."""
    if timestamp is None:
        timestamp = str(int(time.time()))
    out = {TENANT_HEADER: tenant, USER_HEADER: user,
           AUTH_HEADER: proof if proof is not None else sign(secret, tenant, user, timestamp)}
    if timestamp:
        out[TIMESTAMP_HEADER] = timestamp
    return out


# --- identity verification --------------------------------------------------------


def test_local_mode_needs_no_headers() -> None:
    ident = resolve_identity(MODE_LOCAL, {})
    assert ident == Identity("local", LOCAL_USER)


def test_valid_assertion_is_accepted() -> None:
    ident = resolve_identity(MODE_TENANT_USER, _headers("acme", "alice"), auth_secret=SECRET)
    assert ident == Identity("acme", "alice")


def test_bare_headers_without_proof_are_rejected() -> None:
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER,
                         {TENANT_HEADER: "acme", USER_HEADER: "alice"}, auth_secret=SECRET)


def test_forged_proof_is_rejected() -> None:
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER,
                         _headers("acme", "alice", secret="wrong-secret"), auth_secret=SECRET)


def test_user_cannot_be_swapped_under_another_users_proof() -> None:
    # alice's proof, mallory's claimed user id -> the joint signature must fail.
    headers = _headers("acme", "alice")
    headers[USER_HEADER] = "mallory"
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET)


def test_tenant_cannot_be_swapped_under_another_tenants_proof() -> None:
    headers = _headers("acme", "alice")
    headers[TENANT_HEADER] = "evilcorp"
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET)


def test_tenant_user_recombination_is_rejected() -> None:
    # A proof for (acme, alice) must not authorize (alice, acme) or any regrouping.
    proof = sign(SECRET, "acme", "alice")
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER,
                         {TENANT_HEADER: "alice", USER_HEADER: "acme", AUTH_HEADER: proof},
                         auth_secret=SECRET)


def test_length_prefixing_prevents_delimiter_confusion() -> None:
    # ("a", "b:c") and ("a:b", "c") must not produce the same signed payload.
    assert sign(SECRET, "a", "b:c") != sign(SECRET, "a:b", "c")
    assert Identity("a", "b\x1fc").namespace != Identity("a\x1fb", "c").namespace


def test_missing_user_header_in_user_mode_is_rejected() -> None:
    headers = {TENANT_HEADER: "acme", AUTH_HEADER: sign(SECRET, "acme", LOCAL_USER)}
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET)


def test_missing_secret_refuses_rather_than_trusting_headers() -> None:
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, _headers("acme", "alice"), auth_secret=None)


def test_identity_errors_never_echo_proof_or_identity() -> None:
    secret_user = "alice-super-secret-id"
    proof = sign(SECRET, "acme", secret_user)
    headers = {TENANT_HEADER: "acme", USER_HEADER: secret_user, AUTH_HEADER: "deadbeef"}
    with pytest.raises(IdentityError) as exc:
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET)
    message = str(exc.value)
    assert secret_user not in message and proof not in message and SECRET not in message


# --- gateway-level isolation --------------------------------------------------------


@pytest.fixture
def gateway(monkeypatch):
    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"body": body})
        return 200, {"content-type": "application/json"}, b'{"id":"resp_1"}'

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"body": body})
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    config = SecurityMaskerConfig.model_validate({
        "version": 1,
        "entities": [{"id": "p", "type": "PERSON", "values": [PERSON],
                      "replacement_profile": "prose_identifier", "restore_policy": "literal"}],
    })
    rt = GatewayRuntime(build_engine(config), InMemorySessionStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test",
                        mode=MODE_TENANT_USER, tenant_auth_secret=SECRET)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls


@pytest.mark.asyncio
async def test_same_session_id_different_users_get_different_aliases(gateway) -> None:
    client, calls = gateway
    session = {"X-SecurityMasker-Session-ID": "shared-session-id"}
    async with client:
        await client.post("/responses", headers={**session, **_headers("acme", "alice")},
                          json={"input": f"担当は{PERSON}"})
        await client.post("/responses", headers={**session, **_headers("acme", "bob")},
                          json={"input": f"担当は{PERSON}"})
    assert len(calls) == 2
    first, second = (json.loads(c["body"])["input"] for c in calls)
    assert PERSON not in first and PERSON not in second
    assert first != second, "two users of one tenant shared an alias table"


@pytest.mark.asyncio
async def test_user_cannot_read_another_users_session(gateway) -> None:
    client, calls = gateway
    session = {"X-SecurityMasker-Session-ID": "alice-session"}
    async with client:
        await client.post("/responses", headers={**session, **_headers("acme", "alice")},
                          json={"input": f"担当は{PERSON}"})
        alice_alias = json.loads(calls[0]["body"])["input"]
        # Bob presents alice's session id and her alias: he must not be able to
        # continue her session, so the alias is unknown to him and is refused.
        r = await client.post("/responses", headers={**session, **_headers("acme", "bob")},
                              json={"input": alice_alias})
    # Either the alias is re-masked into bob's own table, or the request blocks —
    # what must NOT happen is bob reaching alice's mapping.
    if r.status_code == 200:
        bob_body = json.loads(calls[-1]["body"])["input"]
        assert bob_body != alice_alias or PERSON not in bob_body
    assert all(PERSON.encode() not in c["body"] for c in calls)


@pytest.mark.asyncio
async def test_user_cannot_reuse_another_users_previous_response_id(gateway) -> None:
    client, calls = gateway
    async with client:
        r1 = await client.post("/responses", headers=_headers("acme", "alice"),
                               json={"input": f"担当は{PERSON}"})
        rid = r1.json()["id"]
        # Bob references alice's response id: the binding is namespaced by identity,
        # so it is not visible to him and the request fails closed.
        r2 = await client.post("/responses", headers=_headers("acme", "bob"),
                               json={"input": "続き", "previous_response_id": rid})
    assert r2.status_code == 409, "bob resolved alice's response binding"


@pytest.mark.asyncio
async def test_forged_user_header_is_refused_by_the_gateway(gateway) -> None:
    client, calls = gateway
    headers = _headers("acme", "alice")
    headers[USER_HEADER] = "bob"          # keep alice's proof, claim to be bob
    async with client:
        r = await client.post("/responses", headers=headers, json={"input": "hi"})
    assert r.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_tampered_signature_is_refused_by_the_gateway(gateway) -> None:
    client, calls = gateway
    headers = _headers("acme", "alice")
    headers[AUTH_HEADER] = headers[AUTH_HEADER][:-1] + ("0" if headers[AUTH_HEADER][-1] != "0" else "1")
    async with client:
        r = await client.post("/responses", headers=headers, json={"input": "hi"})
    assert r.status_code == 403 and calls == []


# --- store-level boundary ------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_refuses_cross_user_read() -> None:
    store = InMemorySessionStore()
    await store.get_or_create("k", tenant_id="acme", user_id="alice")
    with pytest.raises(SessionError):
        await store.get("k", tenant_id="acme", user_id="bob")


@pytest.mark.asyncio
async def test_store_refuses_cross_tenant_read() -> None:
    store = InMemorySessionStore()
    await store.get_or_create("k", tenant_id="acme", user_id="alice")
    with pytest.raises(SessionError):
        await store.get("k", tenant_id="evilcorp", user_id="alice")


# --- startup configuration ------------------------------------------------------------


def test_tenant_user_mode_without_secret_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.setenv("SECURITYMASKER_MODE", MODE_TENANT_USER)
    monkeypatch.delenv("SECURITYMASKER_TENANT_AUTH_SECRET", raising=False)
    with pytest.raises(ConfigError):
        GatewayRuntime.from_env()


def test_unknown_mode_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.setenv("SECURITYMASKER_MODE", "multiuser-ish")
    with pytest.raises(ConfigError):
        GatewayRuntime.from_env()


def test_legacy_multitenant_mode_maps_to_tenant(monkeypatch) -> None:
    # Backwards compatibility: the old name keeps working but resolves to the
    # explicit tenant-only mode, which never claims user isolation.
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.setenv("SECURITYMASKER_MODE", "multitenant")
    monkeypatch.setenv("SECURITYMASKER_TENANT_AUTH_SECRET", SECRET)
    assert GatewayRuntime.from_env().mode == MODE_TENANT


def test_local_mode_still_needs_no_identity_config(monkeypatch) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.delenv("SECURITYMASKER_MODE", raising=False)
    monkeypatch.delenv("SECURITYMASKER_TENANT_AUTH_SECRET", raising=False)
    assert GatewayRuntime.from_env().mode == MODE_LOCAL


# --- assertion freshness / replay (ADR-0008) ----------------------------------------
# A proof with no timestamp is replayable for the lifetime of the secret, and
# float() happily parses "nan"/"inf" — against which every comparison is False, so
# a naive window check PASSES. Both were real holes.


def _timed(tenant: str, user: str, ts: str) -> dict[str, str]:
    return {TENANT_HEADER: tenant, USER_HEADER: user, TIMESTAMP_HEADER: ts,
            AUTH_HEADER: sign(SECRET, tenant, user, ts)}


def test_timestamp_is_required_by_default() -> None:
    headers = _headers("acme", "alice", timestamp="")
    with pytest.raises(IdentityError) as exc:
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET)
    assert "timestamp" in str(exc.value).lower()


def test_untimed_proof_only_with_an_explicit_downgrade() -> None:
    headers = _headers("acme", "alice", timestamp="")
    ident = resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET,
                             require_timestamp=False)
    assert ident == Identity("acme", "alice")


@pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "1.7e9", "0x64",
                                 "12.5", " 42 ", "-1", "abc", "9" * 20])
def test_non_canonical_timestamps_are_rejected(bad) -> None:
    # Notably "nan": abs(now - nan) > skew is False, so a value check alone PASSES.
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, _timed("acme", "alice", bad),
                         auth_secret=SECRET)


def test_expired_proof_is_rejected() -> None:
    now = 1_800_000_000
    stale = str(now - 301)
    with pytest.raises(IdentityError) as exc:
        resolve_identity(MODE_TENANT_USER, _timed("acme", "alice", stale),
                         auth_secret=SECRET, max_skew_seconds=300, now=now)
    assert "expired" in str(exc.value)


def test_future_dated_proof_is_rejected() -> None:
    now = 1_800_000_000
    ahead = str(now + 301)
    with pytest.raises(IdentityError) as exc:
        resolve_identity(MODE_TENANT_USER, _timed("acme", "alice", ahead),
                         auth_secret=SECRET, max_skew_seconds=300, now=now)
    assert "future" in str(exc.value)


@pytest.mark.parametrize("offset", [-300, -299, 0, 299, 300])
def test_proofs_inside_the_window_are_accepted(offset) -> None:
    now = 1_800_000_000
    ident = resolve_identity(MODE_TENANT_USER,
                             _timed("acme", "alice", str(now + offset)),
                             auth_secret=SECRET, max_skew_seconds=300, now=now)
    assert ident.user == "alice"


@pytest.mark.parametrize("offset", [-301, 301])
def test_proofs_outside_the_window_are_rejected(offset) -> None:
    now = 1_800_000_000
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, _timed("acme", "alice", str(now + offset)),
                         auth_secret=SECRET, max_skew_seconds=300, now=now)


def test_timestamp_is_covered_by_the_signature() -> None:
    # Re-dating a captured proof must invalidate it, or the window buys nothing.
    now = 1_800_000_000
    headers = _timed("acme", "alice", str(now))
    headers[TIMESTAMP_HEADER] = str(now + 100)      # keep the old signature
    with pytest.raises(IdentityError):
        resolve_identity(MODE_TENANT_USER, headers, auth_secret=SECRET,
                         max_skew_seconds=300, now=now)


def test_untimed_assertions_are_opt_in_at_startup(monkeypatch) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "tests/integration/securitymasker.masking.yaml")
    monkeypatch.setenv("SECURITYMASKER_MODE", MODE_TENANT_USER)
    monkeypatch.setenv("SECURITYMASKER_TENANT_AUTH_SECRET", SECRET)
    monkeypatch.delenv("SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS", raising=False)
    assert GatewayRuntime.from_env().require_assertion_timestamp is True
    monkeypatch.setenv("SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS", "1")
    assert GatewayRuntime.from_env().require_assertion_timestamp is False


def test_custom_tenant_header_is_honoured_end_to_end(monkeypatch) -> None:
    """SECURITYMASKER_TENANT_HEADER was stored on the runtime but never passed to
    verification, so configuring it silently did nothing."""
    import asyncio

    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"body": body})
        return 200, {"content-type": "application/json"}, b'{"id":"r"}'

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    custom = "x-corp-tenant"
    rt = GatewayRuntime(build_engine(SecurityMaskerConfig.model_validate({"version": 1})),
                        InMemorySessionStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test",
                        mode=MODE_TENANT, tenant_auth_secret=SECRET,
                        tenant_header=custom)
    app = gwapp.create_app(rt)
    ts = str(int(time.time()))

    async def _run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gw") as c:
            # The tenant arrives under the CONFIGURED name, not the default one.
            good = await c.post("/responses", json={"input": "hi"}, headers={
                custom: "acme", TIMESTAMP_HEADER: ts,
                AUTH_HEADER: sign(SECRET, "acme", LOCAL_USER, ts)})
            # The default header must no longer be accepted once one is configured.
            bad = await c.post("/responses", json={"input": "hi"}, headers={
                TENANT_HEADER: "acme", TIMESTAMP_HEADER: ts,
                AUTH_HEADER: sign(SECRET, "acme", LOCAL_USER, ts)})
            return good.status_code, bad.status_code

    good_status, bad_status = asyncio.run(_run())
    assert good_status == 200, "configured tenant header was ignored"
    assert bad_status == 403, "default header still accepted despite the override"
