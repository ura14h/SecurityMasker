"""Audit round-4 regressions. Synthetic data only (§30).

Each test reproduces a defect the fourth audit found: config errors leaking values
by other routes, the x-codex-* wildcard bypassing PII scanning, a stale response
binding minting a new session, untrusted malformed tool JSON, and unbounded
in-memory lock growth.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine, load_config
from securitymasker.errors import ConfigError
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

SECRET = "Zettai-Himitsu-Corp-9876"
EMAIL = "synthetic.user@example.com"


def _write(tmp_path, text: str):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- finding 1 [P0]: remaining config-error leak routes -------------------------


def test_invalid_regex_error_does_not_leak_the_pattern(tmp_path) -> None:
    # A user regex usually embeds the very secret it matches.
    path = _write(tmp_path, (
        "version: 1\n"
        "patterns:\n"
        f"  - id: p1\n    pattern: '{SECRET}('\n    type: HOSTNAME\n"
        "    replacement_profile: hostname\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET not in str(exc.value)


def test_yaml_parse_error_does_not_leak_the_line(tmp_path) -> None:
    # Unclosed bracket: PyYAML quotes the offending line in its message.
    path = _write(tmp_path, (
        "version: 1\n"
        "entities:\n"
        f"  - id: e1\n    values: ['{SECRET}'\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET not in str(exc.value)


def test_capture_group_error_does_not_leak_the_pattern(tmp_path) -> None:
    path = _write(tmp_path, (
        "version: 1\n"
        "patterns:\n"
        f"  - id: p1\n    pattern: '{SECRET}'\n    type: HOSTNAME\n"
        "    replacement_profile: hostname\n    group: 4\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET not in str(exc.value)


# --- gateway fixture ------------------------------------------------------------


@pytest.fixture
def gateway(monkeypatch):
    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"headers": dict(headers), "body": body})
        return 200, {"content-type": "application/json"}, b'{"id": "resp_1"}'

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"headers": dict(headers), "body": body})
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    store = InMemorySessionStore()
    rt = GatewayRuntime(build_engine(SecurityMaskerConfig.model_validate({"version": 1})),
                        store, openai_upstream="http://oai.test",
                        anthropic_upstream="http://an.test")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls, store


# --- finding 2 [P0]: x-codex-* wildcard must not bypass PII scanning -------------


@pytest.mark.asyncio
async def test_wildcard_codex_header_with_pii_is_blocked(gateway) -> None:
    client, calls, _ = gateway
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"X-Codex-User-Context": EMAIL})
    assert r.status_code == 400, "PII in a wildcard header reached the upstream"
    assert calls == []


@pytest.mark.asyncio
async def test_wildcard_codex_header_without_pii_passes(gateway) -> None:
    client, calls, _ = gateway
    async with client:
        r = await client.post("/responses", json={"input": "hi"},
                              headers={"X-Codex-Turn": "3"})
    assert r.status_code == 200 and len(calls) == 1
    assert "x-codex-turn" in {k.lower() for k in calls[0]["headers"]}


# --- finding 4 [P1]: a stale binding must not mint a new session -----------------


@pytest.mark.asyncio
async def test_stale_binding_does_not_create_a_new_session(gateway) -> None:
    client, calls, store = gateway
    async with client:
        r1 = await client.post("/responses", json={"input": "code 6412112870"})
        assert r1.status_code == 200
        rid = r1.json()["id"]
        # The session expires/is purged while the binding survives.
        for key in list(store._sessions):
            await store.delete(key)
        r2 = await client.post("/responses", json={"input": "code 6412112870",
                                                   "previous_response_id": rid})
    assert r2.status_code == 409, "a stale binding silently started a new session"
    assert len(calls) == 1  # only the first turn was forwarded


# --- finding 5 [P1]: untrusted tool with malformed JSON must fail closed ---------


def test_untrusted_tool_invalid_json_is_not_resent() -> None:
    from securitymasker.gateway.responses_stream import ResponsesStreamProcessor
    from securitymasker.tool_trust import ToolTrustPolicy

    # No trusted tools at all: validity must still be enforced.
    proc = ResponsesStreamProcessor({}, lambda t: t, ToolTrustPolicy(frozenset()))
    added = ('data: ' + json.dumps({
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": "fc_1", "type": "function_call", "name": "untrusted_tool"}}) + "\n\n")
    broken = '{"host": "SM_X", '
    done = ('data: ' + json.dumps({
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1", "arguments": broken}) + "\n\n")
    out = (proc.feed((added + done).encode()) + proc.flush()).decode()

    assert broken not in out, "malformed JSON was re-sent for an untrusted tool"
    events = [json.loads(line[6:]) for line in out.splitlines() if line.startswith("data: ")]
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "not valid JSON" in errors[0]["error"]["message"]


def test_untrusted_tool_valid_json_keeps_aliases() -> None:
    from securitymasker.gateway.responses_stream import ResponsesStreamProcessor
    from securitymasker.tool_trust import ToolTrustPolicy

    proc = ResponsesStreamProcessor({"SM_X": "real-value"}, lambda t: t.replace("SM_X", "real-value"),
                                    ToolTrustPolicy(frozenset()))
    added = ('data: ' + json.dumps({
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": "fc_1", "type": "function_call", "name": "untrusted_tool"}}) + "\n\n")
    done = ('data: ' + json.dumps({
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1", "arguments": '{"host": "SM_X"}'}) + "\n\n")
    out = (proc.feed((added + done).encode()) + proc.flush()).decode()
    assert "SM_X" in out                 # alias preserved for the untrusted tool
    assert "real-value" not in out       # real value never handed over


# --- finding 7 [P1]: in-memory locks must not accumulate forever -----------------


@pytest.mark.asyncio
async def test_locks_are_reclaimed_for_dead_sessions() -> None:
    store = InMemorySessionStore()
    for i in range(200):
        key = f"eph-{i}"
        async with store.lock(key):
            await store.get_or_create(key)
        await store.delete(key)
        async with store.lock(key):   # a later touch must not leak an entry
            pass
    assert len(store._locks) == 0, f"{len(store._locks)} locks leaked"


@pytest.mark.asyncio
async def test_live_session_keeps_its_lock() -> None:
    store = InMemorySessionStore()
    async with store.lock("live"):
        await store.get_or_create("live")
    # Session still alive -> its lock is retained for the next request.
    assert "live" in store._locks


@pytest.mark.asyncio
async def test_waiters_keep_the_lock_entry_alive() -> None:
    store = InMemorySessionStore()
    entered = asyncio.Event()
    second = asyncio.Event()

    async def holder() -> None:
        async with store.lock("k"):
            entered.set()
            await asyncio.sleep(0.05)

    async def waiter() -> None:
        await entered.wait()
        async with store.lock("k"):
            second.set()

    await asyncio.gather(holder(), waiter())
    assert second.is_set()   # the waiter used the SAME lock, no double entry
