"""Gateway app tests (in-process, no network): session -> mask -> restore.

Drives the ASGI app via httpx.ASGITransport and stubs the upstream forwarders, so
the full handler path (mask request, restore response, leak-free outbound) is
covered in CI without booting subprocesses. The stub echoes the *masked* request
text back, so restoration is exercised against real aliases.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.regex import RegexDetector, RegexEntry
from securitymasker.engine import MaskingEngine
from securitymasker.gateway import app as gwapp
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.models import EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value
PERSON = "山田太郎"
HOST = "prod-db01.internal.example"


def _runtime() -> GatewayRuntime:
    engine = MaskingEngine([
        DictionaryDetector([DictionaryEntry(EntityType.PERSON.value, (PERSON,), PROSE, LITERAL)]),
        RegexDetector([RegexEntry(HOST.replace(".", r"\."), EntityType.HOSTNAME.value,
                                  ReplacementProfile.HOSTNAME.value, LITERAL, 150)], name="host"),
    ])
    return GatewayRuntime(engine, InMemorySessionStore(),
                          openai_upstream="http://up.test", anthropic_upstream="http://up.test",
                          product_mode="chatgpt")


@pytest.fixture
def app_client(monkeypatch):
    captured: dict[str, bytes] = {}

    async def fake_buffered(method, url, headers, body):
        captured["body"] = body
        text = json.loads(body).get("input", "")
        resp = {"id": "r", "output": [{"type": "message",
                "content": [{"type": "output_text", "text": text}]}]}
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode()

    async def fake_streaming(method, url, headers, body, processor=None, on_complete=None):
        captured["body"] = body
        text = json.loads(body).get("input", "")
        # Echo the masked text as split output_text deltas + a done event.
        events = [
            {"type": "response.output_text.delta", "output_index": 0,
             "content_index": 0, "delta": text[i : i + 3]}
            for i in range(0, len(text), 3)
        ]
        events.append({"type": "response.output_text.done", "output_index": 0,
                       "content_index": 0, "text": text})
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                      for e in events).encode()
        out = (processor.feed(sse) + processor.flush()) if processor is not None else sse
        return Response(out, media_type="text/event-stream")

    monkeypatch.setattr(gwapp, "forward_buffered", fake_buffered)
    monkeypatch.setattr(gwapp, "forward_streaming", fake_streaming)
    app = gwapp.create_app(_runtime())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://gw"), captured


@pytest.mark.asyncio
async def test_responses_non_stream_mask_and_restore(app_client) -> None:
    client, captured = app_client
    async with client:
        r = await client.post("/responses", headers={"X-SecurityMasker-Session-ID": "s1"},
                              json={"model": "m", "input": f"担当は{PERSON}、接続 {HOST}"})
    text = r.json()["output"][0]["content"][0]["text"]
    assert PERSON in text and HOST in text            # restored for the client
    assert PERSON.encode() not in captured["body"]    # outbound is masked (no leak)
    assert HOST.encode() not in captured["body"]


@pytest.mark.asyncio
async def test_responses_stream_mask_and_restore(app_client) -> None:
    client, captured = app_client
    async with client:
        r = await client.post("/responses", headers={"X-SecurityMasker-Session-ID": "s2"},
                              json={"model": "m", "stream": True, "input": f"{PERSON} at {HOST}"})
    deltas = ""
    for line in r.text.splitlines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            if ev.get("type") == "response.output_text.delta":
                deltas += ev["delta"]
    assert PERSON in deltas and HOST in deltas         # streaming restoration reaches client
    assert PERSON.encode() not in captured["body"]
