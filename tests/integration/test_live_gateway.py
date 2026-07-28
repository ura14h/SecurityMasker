"""現行configのmode別Gatewayをmock upstream相手に検証するlive test。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from securitymasker.bootstrap import initialize_layout

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_LIVE") != "1",
    reason="set SM_RUN_LIVE=1 to run the live gateway test",
)

REPO = Path(__file__).resolve().parents[2]
PERSON = "山田太郎"
HOST = "prod-db01.internal.example"
PROMPT = f"担当は{PERSON}、接続先 {HOST}"

DICTIONARY = f"""\
version: 1
entities:
  - id: synthetic_person
    type: PERSON
    values: ["{PERSON}"]
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100
  - id: synthetic_host
    type: HOSTNAME
    values: ["{HOST}"]
    replacement_profile: hostname
    restore_policy: literal
    priority: 100
patterns: []
"""


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception:  # noqa: BLE001 - 起動待ちでは未接続が通常
            pass
        time.sleep(0.4)
    raise RuntimeError(f"not ready: {url}")


def _stop(*processes: subprocess.Popen[bytes]) -> None:
    for process in processes:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def gateways(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("livegw")
    record = root / "record.jsonl"
    mock_port, chatgpt_port, claude_port = _port(), _port(), _port()
    chatgpt = initialize_layout(
        root / "chatgpt", mode="chatgpt", port=chatgpt_port
    )
    claude = initialize_layout(root / "claude", mode="claude", port=claude_port)
    chatgpt.dictionary.write_text(DICTIONARY, encoding="utf-8")
    claude.dictionary.write_text(DICTIONARY, encoding="utf-8")
    chatgpt.dictionary.chmod(0o600)
    claude.dictionary.chmod(0o600)

    mock_environment = {**os.environ, "SM_MOCK_RECORD": str(record)}
    mock = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "devtools.mock_upstream:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(mock_port),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=mock_environment,
    )
    gateway_environment = {
        **os.environ,
        "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{mock_port}",
        "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{mock_port}",
    }

    def serve(config: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603
            [sys.executable, "securitymasker.py", "gateway", "--config", str(config)],
            cwd=REPO,
            env=gateway_environment,
        )

    chatgpt_gateway = serve(chatgpt.config)
    claude_gateway = serve(claude.config)
    try:
        _wait(f"http://127.0.0.1:{mock_port}/health")
        _wait(f"http://127.0.0.1:{chatgpt_port}/ready")
        _wait(f"http://127.0.0.1:{claude_port}/ready")
        yield {
            "chatgpt": f"http://127.0.0.1:{chatgpt_port}",
            "claude": f"http://127.0.0.1:{claude_port}",
            "record": record,
        }
    finally:
        _stop(chatgpt_gateway, claude_gateway, mock)


def _headers(session: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-securitymasker-session-id": session,
    }


def test_responses_buffered_and_streaming_restore(gateways) -> None:
    base = gateways["chatgpt"]
    buffered = httpx.post(
        f"{base}/responses",
        headers=_headers("chatgpt-buffered"),
        json={"model": "m", "input": PROMPT},
        timeout=30,
    )
    text = buffered.json()["output"][0]["content"][0]["text"]
    assert PERSON in text and HOST in text

    with httpx.stream(
        "POST",
        f"{base}/responses",
        headers=_headers("chatgpt-stream"),
        json={"model": "m", "stream": True, "input": PROMPT},
        timeout=30,
    ) as response:
        deltas = ""
        for line in "".join(response.iter_text()).splitlines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event.get("type") == "response.output_text.delta":
                    deltas += event.get("delta", "")
    assert PERSON in deltas and HOST in deltas


def test_claude_stream_restores_and_wrong_route_is_local(gateways) -> None:
    base = gateways["claude"]
    assert httpx.post(
        f"{base}/responses", json={"input": PROMPT}, timeout=10
    ).status_code == 404

    with httpx.stream(
        "POST",
        f"{base}/v1/messages",
        headers={**_headers("claude-stream"), "anthropic-version": "2023-06-01"},
        json={
            "model": "claude",
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": PROMPT}],
        },
        timeout=30,
    ) as response:
        text = ""
        for line in "".join(response.iter_text()).splitlines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if (
                    event.get("type") == "content_block_delta"
                    and event["delta"].get("type") == "text_delta"
                ):
                    text += event["delta"]["text"]
    assert PERSON in text and HOST in text


def test_no_original_reaches_mock_upstream(gateways) -> None:
    record = Path(gateways["record"]).read_text(encoding="utf-8")
    assert record
    assert PERSON not in record
    assert HOST not in record
