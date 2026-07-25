"""Phase 6 live proxy test: the purpose-built gateway masks + restores (ADR-0006).

Boots the mock upstream + the SecurityMasker proxy (no LiteLLM). Proves what the
LiteLLM path could not: the Responses **streaming** response is restored to the
client, while the outbound payload carries only aliases. Opt-in (subprocesses):

    SM_RUN_LIVE=1 .venv/bin/python -m pytest tests/integration/test_live_gateway.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_LIVE") != "1",
    reason="set SM_RUN_LIVE=1 to run the live gateway test",
)

REPO = Path(__file__).resolve().parents[2]
DICT_CONFIG = REPO / "tests" / "integration" / "securitymasker.masking.yaml"
MOCK_PORT = 8083
GW_PORT = 4002
PERSON = "山田太郎"
HOST = "prod-db01.internal.example"


def _wait(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.4)
    raise RuntimeError(f"not ready: {url}: {last}")


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("livegw")
    record = tmp / "record.jsonl"
    env = {**os.environ, "SM_MOCK_RECORD": str(record)}
    mock = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "tests.integration.mock_upstream:app",
         "--host", "127.0.0.1", "--port", str(MOCK_PORT), "--log-level", "warning"],
        cwd=str(REPO), env=env,
    )
    gw_env = {**env, "SECURITYMASKER_CONFIG": str(DICT_CONFIG),
              "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{MOCK_PORT}",
              "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{MOCK_PORT}"}
    gw = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "securitymasker.gateway.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(GW_PORT), "--log-level", "warning"],
        cwd=str(REPO), env=gw_env,
    )
    try:
        _wait(f"http://127.0.0.1:{MOCK_PORT}/health")
        _wait(f"http://127.0.0.1:{GW_PORT}/health")
        yield {"base": f"http://127.0.0.1:{GW_PORT}", "record": record}
    finally:
        for p in (gw, mock):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def _headers(session: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-SecurityMasker-Session-ID": session}


PROMPT = f"担当は{PERSON}、接続先 {HOST}"


def test_responses_non_stream_masks_and_restores(gateway) -> None:
    r = httpx.post(f"{gateway['base']}/responses", headers=_headers("g1"),
                   json={"model": "m", "input": PROMPT}, timeout=15)
    text = r.json()["output"][0]["content"][0]["text"]
    assert PERSON in text and HOST in text  # restored for the client


def test_responses_stream_masks_and_restores(gateway) -> None:
    with httpx.stream("POST", f"{gateway['base']}/responses", headers=_headers("g2"),
                      json={"model": "m", "stream": True, "input": PROMPT}, timeout=15) as r:
        deltas = ""
        for line in "".join(r.iter_text()).splitlines():
            line = line.strip()
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "response.output_text.delta":
                    deltas += ev.get("delta", "")
    # THE key result: streaming restoration reaches the client (LiteLLM could not).
    assert PERSON in deltas and HOST in deltas


def test_anthropic_stream_masks_and_restores(gateway) -> None:
    with httpx.stream(
        "POST", f"{gateway['base']}/v1/messages",
        headers={**_headers("g3"), "anthropic-version": "2023-06-01"},
        json={"model": "claude", "max_tokens": 64, "stream": True,
              "messages": [{"role": "user", "content": PROMPT}]}, timeout=15,
    ) as r:
        text = ""
        for line in "".join(r.iter_text()).splitlines():
            line = line.strip()
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                    text += ev["delta"]["text"]
    assert PERSON in text and HOST in text


def test_no_original_leaked_upstream(gateway) -> None:
    record = Path(gateway["record"]).read_text(encoding="utf-8", errors="replace")
    assert record
    assert PERSON not in record
    assert HOST not in record
