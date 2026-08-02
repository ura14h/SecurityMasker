"""模擬upstreamのwire protocolを検証する。"""

from __future__ import annotations

import json

import httpx
import pytest

from devtools.mock_upstream import app


@pytest.mark.asyncio
async def test_anthropic_stream_has_complete_message_lifecycle() -> None:
    """Claude Codeが要求するMessage開始・終了情報を返す。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://mock.local"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={
                "model": "claude-synthetic",
                "max_tokens": 64,
                "stream": True,
                "messages": [{"role": "user", "content": "synthetic prompt"}],
            },
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0] == {
        "type": "message_start",
        "message": {
            "id": "msg-mock",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-synthetic",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        },
    }
    assert events[-2] == {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 1},
    }
    assert events[-1] == {"type": "message_stop"}
