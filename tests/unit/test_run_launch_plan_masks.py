"""End-to-end: the environment `run` builds really does produce masked traffic.

The launcher unit tests prove the launch PLAN is right; this proves the plan
works — a client configured exactly as `run` configures it (base URL + session
header) sends a request that reaches the upstream fully masked. Without this,
`run` could set plausible-looking variables the gateway ignores.

Synthetic data only; the "upstream" is an in-process stub that records the final
payload it received (doc/06 §7.1: verify the bytes the upstream actually got).
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.integrations.launcher import SESSION_HEADER, build_plan
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"
HOST = "prod-db01.internal.example"
GATEWAY = "http://gw"


def _config() -> SecurityMaskerConfig:
    return SecurityMaskerConfig.model_validate({
        "version": 1,
        "entities": [
            {"id": "p", "type": "PERSON", "values": [PERSON],
             "replacement_profile": "prose_identifier", "restore_policy": "literal"},
        ],
        "patterns": [
            {"id": "h", "pattern": r"prod-db01\.internal\.example", "type": "HOSTNAME",
             "replacement_profile": "hostname", "restore_policy": "literal"},
        ],
    })


@pytest.fixture
def gateway_and_upstream(monkeypatch):
    """A real gateway app in front of a recording stub upstream."""
    received: list[bytes] = []

    async def fake_buffered(method, url, headers, body):
        received.append(body)
        return 200, {"content-type": "application/json"}, b'{"id":"resp_1"}'

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        received.append(body)
        return Response(b"", media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    rt = GatewayRuntime(build_engine(_config()), InMemorySessionStore(),
                        openai_upstream="http://oai.test",
                        anthropic_upstream="http://anthropic.test")
    return gwapp.create_app(rt), received


def _claude_headers(plan) -> dict[str, str]:
    """Parse ANTHROPIC_CUSTOM_HEADERS exactly as Claude Code would."""
    out = {}
    for line in plan.env["ANTHROPIC_CUSTOM_HEADERS"].splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            out[name.strip()] = value.strip()
    return out


@pytest.mark.asyncio
async def test_claude_settings_produce_a_masked_upstream_payload(gateway_and_upstream) -> None:
    app, received = gateway_and_upstream
    plan = build_plan(["claude"], gateway=GATEWAY, session_id="sess-claude", environ={})
    base = plan.env["ANTHROPIC_BASE_URL"]
    headers = _claude_headers(plan)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        r = await c.post("/v1/messages", headers=headers, json={
            "model": "m", "max_tokens": 16,
            "messages": [{"role": "user", "content": f"担当は{PERSON}、接続先は{HOST}"}]})

    assert r.status_code == 200
    assert len(received) == 1
    payload = received[0]
    assert PERSON.encode() not in payload, "person reached the upstream unmasked"
    assert HOST.encode() not in payload, "hostname reached the upstream unmasked"
    assert b"SM_PERSON_" in payload            # it really was masked, not dropped


@pytest.mark.asyncio
async def test_codex_settings_produce_a_masked_upstream_payload(gateway_and_upstream) -> None:
    app, received = gateway_and_upstream
    plan = build_plan(["codex"], gateway=GATEWAY, session_id="sess-codex", environ={})
    joined = " ".join(plan.argv)
    assert GATEWAY in joined
    # The override is a TOML inline table, so parse it as TOML (a JSON parse would
    # have quietly accepted the malformed form this test exists to prevent).
    import tomllib

    header_arg = next(a for a in plan.argv
                      if SESSION_HEADER in a and a.startswith("model_providers"))
    header_value = tomllib.loads(header_arg)[
        "model_providers"]["securitymasker"]["http_headers"][SESSION_HEADER]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url=GATEWAY) as c:
        r = await c.post("/responses", headers={SESSION_HEADER: header_value},
                         json={"model": "m", "input": f"担当は{PERSON}、接続先は{HOST}"})

    assert r.status_code == 200
    assert len(received) == 1
    payload = received[0]
    assert PERSON.encode() not in payload
    assert HOST.encode() not in payload
    assert b"SM_PERSON_" in payload


@pytest.mark.asyncio
async def test_session_header_keeps_one_alias_table_across_turns(gateway_and_upstream) -> None:
    app, received = gateway_and_upstream
    plan = build_plan(["claude"], gateway=GATEWAY, session_id="sess-stable", environ={})
    headers = _claude_headers(plan)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url=plan.env["ANTHROPIC_BASE_URL"]) as c:
        for _ in range(3):
            await c.post("/v1/messages", headers=headers, json={
                "model": "m", "max_tokens": 16,
                "messages": [{"role": "user", "content": f"担当は{PERSON}"}]})

    aliases = {json.loads(p)["messages"][0]["content"] for p in received}
    assert all(PERSON not in a for a in aliases)
    assert len(aliases) == 1, f"session header did not hold the alias table: {aliases}"


# --- are the generated overrides well-formed and schema-correct? ----------------------


def test_generated_overrides_are_valid_toml_assignments() -> None:
    """Each `-c` value must parse as TOML and land where Codex expects it.

    NOT a claim that the real binary accepted them: see the note below. This
    checks what can actually be checked without starting a session — that we emit
    syntactically valid TOML whose keys match the documented custom-provider
    schema, so a quoting or nesting mistake fails here rather than at a user's
    first prompt.
    """
    import tomllib

    plan = build_plan(["codex"], gateway=GATEWAY, session_id="sess-toml", environ={})
    values = [plan.argv[i + 1] for i, a in enumerate(plan.argv) if a == "-c"]
    assert values, "no -c overrides were generated"

    merged: dict = {}
    for assignment in values:
        parsed = tomllib.loads(assignment)          # raises on malformed TOML
        # Merge the dotted-key documents into one structure.
        stack = [(merged, parsed)]
        while stack:
            into, frm = stack.pop()
            for key, value in frm.items():
                if isinstance(value, dict):
                    stack.append((into.setdefault(key, {}), value))
                else:
                    into[key] = value

    provider = merged["model_providers"]["securitymasker"]
    assert merged["model_provider"] == "securitymasker"
    assert provider["base_url"] == GATEWAY
    assert provider["wire_api"] == "responses"
    assert provider["requires_openai_auth"] is True
    assert SESSION_HEADER in provider["http_headers"]


def test_no_override_targets_the_users_real_config() -> None:
    """Per-process only: nothing may write to or point at ~/.codex."""
    plan = build_plan(["codex"], gateway=GATEWAY, session_id="sess-x", environ={})
    assert "CODEX_HOME" not in plan.env
    assert not any(".codex" in a for a in plan.argv)


# NOTE ON END-TO-END CLI VERIFICATION
# ----------------------------------
# An earlier test claimed the real `codex` binary had validated this
# configuration. It had not: it ran `codex --version`, which prints and exits
# BEFORE the config is built, so an unknown key returned 0 just the same. It also
# ended in `assert ... or True`, which cannot fail.
#
# There is no cheap honest replacement. `--strict-config` is rejected by every
# subcommand that would build the config (`login`, `mcp`), and `--help`/`--version`
# short-circuit; the remaining paths start a session and would make a network call,
# which the test suite must not do. So the claim is withdrawn rather than restated,
# and the checks above cover what is verifiable offline. Confirming the keys
# against a running Codex remains a manual step, recorded in doc/07.
