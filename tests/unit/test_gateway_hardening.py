"""gatewayの入力検証、転送遮断、header衛生を検証する。

Reproduce the external-send leakage paths first, then assert the fix: the gateway
must never forward on config-missing, malformed/non-object/unsupported bodies,
oversized bodies, or unknown routes, must strip internal headers, and must expose
a readiness check distinct from liveness. Upstream calls are stubbed and asserted
to NOT happen (leak = a forwarder call).
"""

from __future__ import annotations

import gzip
import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"


def _runtime() -> GatewayRuntime:
    engine = MaskingEngine(
        [DictionaryDetector([DictionaryEntry(
            EntityType.PERSON.value, (PERSON,),
            ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])],
        registered_literals=(PERSON,),
        leak_scanners=[build_secret_detector()],
    )
    return GatewayRuntime(engine, InMemorySessionStore(),
                          openai_upstream="http://oai.test",
                          anthropic_upstream="http://anthropic.test",
                          product_mode="chatgpt")


@pytest.fixture
def app_and_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return Response(b"data: {}\n\n", media_type="text/event-stream")

    async def fake_buffered(method, url, headers, body):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return 200, {"content-type": "application/json"}, b"{}"

    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    app = gwapp.create_app(_runtime())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    return client, calls


async def _post(client, path, *, json_body=None, content=None, headers=None):
    kw: dict = {"headers": headers or {}}
    if json_body is not None:
        kw["json"] = json_body
    if content is not None:
        kw["content"] = content
    return await client.post(path, **kw)


# --- malformed / non-object / unsupported bodies must not be forwarded -------

@pytest.mark.asyncio
async def test_malformed_json_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await _post(client, "/responses", content=b'{"input": "', headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert calls == []  # nothing left the proxy
    assert PERSON not in r.text


@pytest.mark.asyncio
async def test_json_array_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await _post(client, "/responses", content=json.dumps([PERSON]).encode(),
                        headers={"content-type": "application/json"})
    assert r.status_code == 400 and calls == []


@pytest.mark.asyncio
async def test_gzip_content_encoding_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    body = gzip.compress(json.dumps({"input": PERSON}).encode())
    async with client:
        r = await _post(client, "/responses", content=body,
                        headers={"content-type": "application/json", "content-encoding": "gzip"})
    assert r.status_code in (400, 415) and calls == []


# --- route allowlist: unknown routes must not be forwarded -------------------

@pytest.mark.asyncio
async def test_unknown_post_route_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await _post(client, "/v1/chat/completions", json_body={"messages": [{"content": PERSON}]})
    assert r.status_code in (404, 405) and calls == []


@pytest.mark.asyncio
async def test_spoofed_messages_path_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        r = await _post(client, "/foo/messages", json_body={"messages": [{"content": PERSON}]})
    assert r.status_code in (404, 405) and calls == []


# --- header hygiene: internal session header must not leave; upstream is right --

@pytest.mark.asyncio
async def test_internal_session_header_not_forwarded(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        await _post(client, "/responses", json_body={"input": "hi"},
                    headers={"X-SecurityMasker-Session-ID": "s1"})
    assert len(calls) == 1
    fwd = {k.lower() for k in calls[0]["headers"]}
    assert "x-securitymasker-session-id" not in fwd
    assert calls[0]["url"].startswith("http://oai.test")  # correct upstream


@pytest.mark.asyncio
async def test_chatgpt_mode_does_not_expose_messages(app_and_calls) -> None:
    client, calls = app_and_calls
    async with client:
        response = await _post(
            client, "/v1/messages", json_body={"max_tokens": 1, "messages": []}
        )
    assert response.status_code == 404 and calls == []


# --- oversized body must not be forwarded -----------------------------------

@pytest.mark.asyncio
async def test_oversized_body_not_forwarded(monkeypatch, app_and_calls) -> None:
    client, calls = app_and_calls
    monkeypatch.setattr(gwapp, "MAX_BODY_BYTES", 1000)
    big = {"input": "x" * 5000}
    async with client:
        r = await _post(client, "/responses", json_body=big)
    assert r.status_code == 413 and calls == []


# --- final-payload guard blocks registered secrets in fields the adapter -----
#          never masks (unknown/structural/schema-key), and forwards nothing ------

@pytest.mark.asyncio
async def test_registered_secret_in_unknown_field_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    # `metadata` is an unknown field the Responses adapter passes through untouched.
    async with client:
        r = await _post(client, "/responses",
                        json_body={"input": "hi", "metadata": {"note": PERSON}})
    assert r.status_code == 400 and calls == []       # blocked, nothing forwarded
    assert PERSON not in r.text


@pytest.mark.asyncio
async def test_registered_secret_in_schema_key_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    # A tool JSON-Schema property NAME is structural — never masked, must block.
    tool = {"type": "function", "name": "f", "description": "d",
            "parameters": {"type": "object", "properties": {PERSON: {"type": "string"}}}}
    async with client:
        r = await _post(client, "/responses", json_body={"input": "hi", "tools": [tool]})
    assert r.status_code == 400 and calls == []


@pytest.mark.asyncio
async def test_high_precision_secret_in_unknown_field_blocked(app_and_calls) -> None:
    client, calls = app_and_calls
    secret = "sk-ant-api03-" + "A" * 80  # matches the high-precision secret detector
    async with client:
        r = await _post(client, "/responses",
                        json_body={"input": "hi", "metadata": {"key": secret}})
    assert r.status_code == 400 and calls == []
    assert secret not in r.text


@pytest.mark.asyncio
async def test_clean_request_still_forwards(app_and_calls) -> None:
    client, calls = app_and_calls
    # Secret only in message content -> fully masked -> guard passes -> forwarded.
    async with client:
        r = await _post(client, "/responses", json_body={"input": f"担当は{PERSON}"})
    assert r.status_code == 200 and len(calls) == 1
    assert PERSON.encode() not in calls[0]["body"]


# --- oversized field must fail closed, not be silently truncated -------------

@pytest.mark.asyncio
async def test_oversized_field_fails_closed(monkeypatch) -> None:
    from securitymasker.detectors import regex as regexmod
    from securitymasker.detectors.regex import RegexDetector, RegexEntry

    calls: list[dict] = []

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        calls.append({"url": url})
        return Response(b"", media_type="text/event-stream")

    async def fake_buffered(method, url, headers, body):
        calls.append({"url": url})
        return 200, {"content-type": "application/json"}, b"{}"

    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(regexmod, "_MAX_SCAN_CHARS", 100)
    # A masking regex over a field longer than the scan limit: silent truncation
    # would forward the tail unmasked; fail-closed must block instead (no forward).
    engine = MaskingEngine([RegexDetector(
        [RegexEntry(PERSON, EntityType.PERSON.value,
                    ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)],
        name="user")])
    rt = GatewayRuntime(engine, InMemorySessionStore(),
                        openai_upstream="http://oai.test",
                        anthropic_upstream="http://anthropic.test",
                        product_mode="chatgpt")
    app = gwapp.create_app(rt)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
        r = await c.post("/responses", json={"input": "x" * 200 + PERSON})
    assert r.status_code == 400 and calls == []


# --- readiness distinct from liveness ---------------------------------------

@pytest.mark.asyncio
async def test_readiness_ok_with_engine(app_and_calls) -> None:
    client, _ = app_and_calls
    async with client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200


def test_from_env_requires_config(monkeypatch) -> None:
    from securitymasker.errors import ConfigError

    monkeypatch.delenv("SECURITYMASKER_CONFIG", raising=False)
    with pytest.raises(ConfigError):
        GatewayRuntime.from_env()
