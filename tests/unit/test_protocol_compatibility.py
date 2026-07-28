"""Claude／OpenAI protocolの互換性と透過性を検証する。"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.responses import Response

from securitymasker.config import SecurityMaskerConfig, build_engine
from securitymasker.gateway import app as gateway_app
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.gateway.session import resolve_session
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"
SYNTHETIC_EMAIL = "synthetic.user@example.test"


def _engine():
    config = SecurityMaskerConfig.model_validate(
        {
            "version": 1,
            "entities": [
                {
                    "id": "person",
                    "type": "PERSON",
                    "values": [PERSON],
                    "replacement_profile": "prose_identifier",
                    "restore_policy": "literal",
                }
            ],
        }
    )
    return build_engine(config)


def _claude_runtime() -> GatewayRuntime:
    return GatewayRuntime(
        _engine(),
        InMemorySessionStore(),
        openai_upstream="http://127.0.0.1:48001",
        anthropic_upstream="http://127.0.0.1:48002",
        product_mode="claude",
    )


def test_claude_code_session_header_is_stable_and_has_priority() -> None:
    resolved = resolve_session(
        {
            "x-claude-code-session-id": "claude-session-1",
            "session-id": "lower-priority",
        }
    )
    assert resolved.stable
    assert resolved.session_id == "x-claude-code-session-id:claude-session-1"

    explicit = resolve_session(
        {
            "x-securitymasker-session-id": "explicit",
            "x-claude-code-session-id": "claude-session-1",
        }
    )
    assert explicit.session_id == "explicit"


@pytest.mark.asyncio
async def test_count_tokens_uses_same_masked_payload_and_session_as_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_buffered(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        payload = json.loads(body)
        calls.append({"url": url, "headers": headers, "body": payload})
        if url.endswith("/count_tokens"):
            return 200, {"content-type": "application/json"}, b'{"input_tokens":17}'
        masked = payload["messages"][0]["content"]
        response = {
            "id": "msg-synthetic",
            "content": [{"type": "text", "text": masked}],
        }
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(response).encode(),
        )

    monkeypatch.setattr(gateway_app, "forward_buffered", fake_buffered)
    app = gateway_app.create_app(_claude_runtime())
    headers = {
        "x-api-key": "synthetic-auth-value",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "feature-a",
        "anthropic-future-feature": "feature-b",
        "x-claude-code-session-id": "stable-claude-session",
    }
    payload = {
        "model": "claude-synthetic",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": f"担当は{PERSON}"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        counted = await client.post(
            "/v1/messages/count_tokens", headers=headers, json=payload
        )
        replied = await client.post("/v1/messages", headers=headers, json=payload)

    assert counted.status_code == 200
    assert counted.json() == {"input_tokens": 17}
    assert replied.status_code == 200
    assert PERSON in replied.json()["content"][0]["text"]
    assert len(calls) == 2

    count_text = calls[0]["body"]["messages"][0]["content"]  # type: ignore[index]
    message_text = calls[1]["body"]["messages"][0]["content"]  # type: ignore[index]
    assert PERSON not in count_text
    assert count_text == message_text
    forwarded_headers = {
        key.lower(): value for key, value in calls[0]["headers"].items()  # type: ignore[union-attr]
    }
    assert forwarded_headers["x-claude-code-session-id"] == "stable-claude-session"
    assert forwarded_headers["anthropic-future-feature"] == "feature-b"


@pytest.mark.asyncio
async def test_anthropic_open_header_is_leak_scanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def forbidden(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        nonlocal called
        called = True
        return 200, {}, b"{}"

    monkeypatch.setattr(gateway_app, "forward_buffered", forbidden)
    app = gateway_app.create_app(_claude_runtime())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.post(
            "/v1/messages",
            headers={"anthropic-user-context": SYNTHETIC_EMAIL},
            json={"max_tokens": 1, "messages": []},
        )

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
async def test_claude_models_and_head_root_are_mode_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    async def fake_streaming(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        processor: object = None,
        on_complete: object = None,
    ) -> Response:
        calls.append((url, headers))
        return Response(b'{"data":[]}', media_type="application/json")

    monkeypatch.setattr(gateway_app, "forward_streaming", fake_streaming)
    app = gateway_app.create_app(_claude_runtime())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        head = await client.head("/")
        models = await client.get(
            "/v1/models",
            headers={
                "x-api-key": "synthetic-auth-value",
                "anthropic-version": "2023-06-01",
            },
        )

    assert head.status_code == 200 and head.content == b""
    assert models.status_code == 200
    assert len(calls) == 1
    url, forwarded_headers = calls[0]
    assert url == "http://127.0.0.1:48002/v1/models"
    assert forwarded_headers["x-api-key"] == "synthetic-auth-value"
    assert forwarded_headers["anthropic-version"] == "2023-06-01"
