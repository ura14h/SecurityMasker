"""Audit round-3 regressions (findings 1-10). Synthetic data only (§30).

Each test reproduces a defect the third audit found and asserts the fixed
behaviour: config/CLI must not echo secrets, locks must not be silently lost or
duplicated, an unknown previous_response_id must fail closed regardless of payload
shape, Codex headers must survive, and unrestorable tool JSON must not be re-sent.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine, load_config
from securitymasker.errors import ConfigError, SessionError
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

SECRET_VALUE = "Zettai-Himitsu-Corp-9876"


# --- finding 1 [P0]: config errors must not echo dictionary values --------------


def _write(tmp_path, text: str):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_duplicate_value_error_does_not_leak_the_value(tmp_path) -> None:
    path = _write(tmp_path, (
        "version: 1\n"
        "entities:\n"
        f"  - id: e1\n    type: PERSON\n    values: ['{SECRET_VALUE}', '{SECRET_VALUE}']\n"
        "    replacement_profile: prose_identifier\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET_VALUE not in str(exc.value)


def test_type_error_does_not_leak_the_value(tmp_path) -> None:
    # A wrong-typed field makes Pydantic report the rejected INPUT by default.
    path = _write(tmp_path, (
        "version: 1\n"
        "entities:\n"
        f"  - id: e1\n    type: PERSON\n    values: '{SECRET_VALUE}'\n"
        "    replacement_profile: prose_identifier\n    priority: not-a-number\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET_VALUE not in str(exc.value)


def test_empty_value_error_does_not_leak_siblings(tmp_path) -> None:
    path = _write(tmp_path, (
        "version: 1\n"
        "entities:\n"
        f"  - id: e1\n    type: PERSON\n    values: ['{SECRET_VALUE}', '  ']\n"
        "    replacement_profile: prose_identifier\n"
    ))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert SECRET_VALUE not in str(exc.value)


# --- finding 2 [P0]: `run` must not echo the wrapped command line ---------------


def test_run_does_not_echo_arguments(monkeypatch, capsys) -> None:
    from securitymasker import cli
    from securitymasker.integrations.readiness import Readiness

    token = "sk-ant-" + "z" * 30
    # `run` is now fail-closed on readiness, so the probe must pass to reach the
    # launch path this test is about (see test_run_guarantee.py for the refusals).
    monkeypatch.setattr(cli, "check_readiness", lambda gw, **kw: Readiness(True, "ready"))
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: None)
    args = cli.build_parser().parse_args(["run", "claude", "--token", token])
    cli.cmd_run(args)
    err = capsys.readouterr().err
    assert token not in err, "wrapped command line leaked to stderr"
    assert "claude" in err  # the executable name is still useful and safe
    # Only a fingerprint of the session id is shown, never the id itself.
    assert "session " in err and "…" in err


# --- finding 4 [P1]: deleting an expired session must not drop a held lock ------


@pytest.mark.asyncio
async def test_delete_does_not_drop_a_held_lock() -> None:
    store = InMemorySessionStore()
    entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def holder() -> None:
        async with store.lock("s1"):
            entered.set()
            await store.delete("s1")          # session expires/reaped while held
            await asyncio.sleep(0.05)

    async def contender() -> None:
        await entered.wait()
        async with store.lock("s1"):
            second_entered.set()

    task = asyncio.gather(holder(), contender())
    await asyncio.sleep(0.02)
    # The contender must still be waiting: the lock outlives the session record.
    assert not second_entered.is_set(), "two holders entered the same session lock"
    await task


# --- finding 5 [P1]: unknown previous_response_id fails closed unconditionally ---


@pytest.fixture
def gateway(monkeypatch):
    calls: list[dict] = []

    async def fake_buffered(method, url, headers, body):
        calls.append({"headers": dict(headers), "body": body})
        return 200, {"content-type": "application/json"}, b'{"id": "resp_x"}'

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"headers": dict(headers), "body": body})
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    rt = GatewayRuntime(build_engine(SecurityMaskerConfig.model_validate({"version": 1})),
                        InMemorySessionStore(),
                        openai_upstream="http://oai.test", anthropic_upstream="http://an.test")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls


@pytest.mark.asyncio
async def test_unknown_previous_response_id_blocks_even_without_alias_shape(gateway) -> None:
    client, calls = gateway
    # A numeric alias is indistinguishable from ordinary data, so shape heuristics
    # cannot save us here: the unknown binding alone must fail closed.
    async with client:
        r = await client.post("/responses", json={"input": "code 6412112870",
                                                  "previous_response_id": "resp_never_seen"})
    assert r.status_code == 409 and calls == []


# --- finding 6 [P1]: Codex session headers must reach the upstream ---------------


@pytest.mark.asyncio
async def test_codex_headers_are_forwarded(gateway) -> None:
    client, calls = gateway
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


# --- finding 9 [P1]: unrestorable tool JSON must not be re-sent ------------------


def test_invalid_tool_json_becomes_an_error_event() -> None:
    from securitymasker.streaming.openai_responses_stream import ResponsesStreamProcessor
    from securitymasker.tool_trust import ToolTrustPolicy

    proc = ResponsesStreamProcessor({"SM_X": "real"}, lambda t: t,
                                    ToolTrustPolicy(frozenset({"tool_a"})))
    added = ('data: ' + json.dumps({
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": "fc_1", "type": "function_call", "name": "tool_a"}}) + "\n\n")
    broken = '{"host": "SM_X", '  # truncated => not valid JSON
    done = ('data: ' + json.dumps({
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1", "arguments": broken}) + "\n\n")
    out = (proc.feed((added + done).encode()) + proc.flush()).decode()

    events = [json.loads(line[6:]) for line in out.splitlines() if line.startswith("data: ")]
    assert not [e for e in events if e.get("type") == "response.function_call_arguments.done"]
    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1 and "not valid JSON" in errors[0]["error"]["message"]
    assert broken not in out  # the malformed JSON is not re-sent


# --- finding 10 [P1]: detectors are built once, not twice -----------------------


def test_detectors_are_built_once(monkeypatch) -> None:
    from securitymasker import config as cfgmod

    calls = {"n": 0}
    original = cfgmod.build_detectors

    def counting(cfg):
        calls["n"] += 1
        return original(cfg)

    monkeypatch.setattr(cfgmod, "build_detectors", counting)
    cfgmod.build_engine(SecurityMaskerConfig.model_validate({"version": 1}))
    assert calls["n"] == 1, "detector pipeline (and its models) built more than once"


# --- finding 3 [P1]: a lost Redis lock must abort, not continue ------------------


@pytest.mark.asyncio
async def test_lock_handle_raises_when_lost() -> None:
    from securitymasker.sessions.store import LockHandle

    lost = asyncio.Event()
    handle = LockHandle(lost)
    handle.check()          # still owned: no raise
    lost.set()
    with pytest.raises(SessionError):
        handle.check()
