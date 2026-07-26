"""response bindingによるmulti-turn sessionの継続性を検証する。

Re-audit finding 4: `previous_response_id` was treated as a stable session key,
but it changes every turn, so turn 3+ landed on a different alias table and prior
aliases could no longer be restored.

The id is now only a lookup handle: each response id is bound to the session that
produced it, so a chain of turns — each referencing the previous response — stays
on ONE alias table. Synthetic data only.
"""

from __future__ import annotations

import json

import httpx
import pytest

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"


@pytest.fixture
def chain(monkeypatch):
    """Mock upstream that echoes the masked prompt and returns a NEW id per turn."""
    state = {"n": 0}
    sent: list[bytes] = []

    async def fake_buffered(method, url, headers, body):
        state["n"] += 1
        sent.append(body)
        text = json.loads(body).get("input", "")
        resp = {"id": f"resp_{state['n']}", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]}]}
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode()

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    config = SecurityMaskerConfig.model_validate({
        "version": 1,
        "entities": [{"id": "p", "type": "PERSON", "values": [PERSON],
                      "replacement_profile": "prose_identifier", "restore_policy": "literal"}],
    })
    rt = GatewayRuntime(build_engine(config), InMemorySessionStore(),
                        openai_upstream="http://oai.test",
                        anthropic_upstream="http://an.test",
                        product_mode="chatgpt")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, sent


@pytest.mark.asyncio
async def test_three_turns_via_previous_response_id_keep_one_alias_table(chain) -> None:
    client, sent = chain
    async with client:
        # Turn 1 establishes the session (no explicit session header at all).
        r1 = await client.post("/responses", json={"input": f"担当は{PERSON}"})
        assert r1.status_code == 200
        rid1 = r1.json()["id"]

        # Turns 2 and 3 each reference only the PREVIOUS response id.
        r2 = await client.post("/responses",
                               json={"input": f"{PERSON}の件、続き", "previous_response_id": rid1})
        assert r2.status_code == 200
        rid2 = r2.json()["id"]

        r3 = await client.post("/responses",
                               json={"input": f"{PERSON}の件、さらに続き",
                                     "previous_response_id": rid2})
        assert r3.status_code == 200

    # The same secret must map to the SAME alias on every turn.
    aliases = []
    for body in sent:
        masked = json.loads(body)["input"]
        assert PERSON not in masked                       # never leaked
        aliases.append(masked.split("担当は")[-1].split("の件")[0].strip())
    assert aliases[0] == aliases[1] == aliases[2], aliases

    # And the response text is restored back to the real value for the client.
    assert PERSON in r3.json()["output"][0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_unknown_previous_response_id_is_not_stable(chain) -> None:
    client, _ = chain
    async with client:
        # References a response we never issued AND replays a prior alias: with no
        # resolvable session this must fail closed rather than fork silently.
        r = await client.post("/responses", json={
            "input": "reuse SM_PERSON_ABCDEF", "previous_response_id": "resp_unknown"})
    assert r.status_code == 409


# --- an unusable binding must fail closed, never start a fresh table ---------------
#
# The failure mode both of these guard is the same: continuing a conversation on a
# NEW alias table. The model would then be sent aliases this session cannot restore,
# and the user would silently get alias tokens back instead of their own data. 409
# tells the client to start a new conversation, which is the recoverable outcome.


@pytest.fixture
def bindings(monkeypatch):
    """Upstream that records forwards and returns a fixed id, plus its store."""
    calls: list[bytes] = []

    async def fake_buffered(method, url, headers, body):
        calls.append(body)
        return 200, {"content-type": "application/json"}, b'{"id": "resp_1"}'

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    store = InMemorySessionStore()
    rt = GatewayRuntime(build_engine(SecurityMaskerConfig.model_validate({"version": 1})),
                        store, openai_upstream="http://oai.test",
                        anthropic_upstream="http://an.test",
                        product_mode="chatgpt")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gwapp.create_app(rt)),
                               base_url="http://gw")
    return client, calls, store


@pytest.mark.asyncio
async def test_unknown_previous_response_id_blocks_even_without_alias_shape(bindings) -> None:
    client, calls, _ = bindings
    # A numeric alias is indistinguishable from ordinary data, so a "does the body
    # look like it contains aliases?" heuristic cannot save us: the unknown binding
    # on its own has to be enough to refuse.
    async with client:
        r = await client.post("/responses", json={"input": "code 6412112870",
                                                  "previous_response_id": "resp_never_seen"})
    assert r.status_code == 409 and calls == []


@pytest.mark.asyncio
async def test_stale_binding_does_not_create_a_new_session(bindings) -> None:
    client, calls, store = bindings
    async with client:
        r1 = await client.post("/responses", json={"input": "code 6412112870"})
        assert r1.status_code == 200
        rid = r1.json()["id"]
        # The session expires/is purged while the binding record survives.
        for key in list(store._sessions):
            await store.delete(key)
        r2 = await client.post("/responses", json={"input": "code 6412112870",
                                                   "previous_response_id": rid})
    assert r2.status_code == 409, "a stale binding silently started a new session"
    assert len(calls) == 1  # only the first turn was forwarded
