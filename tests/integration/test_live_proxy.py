"""Phase 0 live integration test: boot the real LiteLLM proxy + mock upstream.

Verifies the plumbing end-to-end (``doc/00-First-Order.md`` §37 Phase 0):
  * the no-op SecurityMasker guardrail loads and the proxy boots,
  * all four protocol paths route to the mock (chat/completions, Responses,
    Anthropic Messages; stream + non-stream),
  * the mock actually receives the request (so the §30.5 leakage harness works),
  * with ``set_verbose: false`` the original secret does NOT appear in the proxy
    log (§25 raw-request logging check).

Opt-in (heavy: boots two subprocesses). Run with:
    SM_RUN_LIVE=1 .venv/bin/python -m pytest tests/integration/test_live_proxy.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_LIVE") != "1",
    reason="set SM_RUN_LIVE=1 to run the live proxy integration test",
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "tests" / "integration" / "litellm.integration.yaml"
MOCK_PORT = 8081
PROXY_PORT = 4000
SECRET = "ZZTOPSECRET_yamada_090_1234_5678"
ALIAS = "SM_ORG_7F3A91"


def _wait(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - polling for readiness
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"service not ready: {url}: {last}")


@pytest.fixture(scope="module")
def proxy(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("liveproxy")
    record = tmp / "mock_record.jsonl"
    proxy_log = tmp / "litellm.log"
    env = {**os.environ, "SM_MOCK_RECORD": str(record)}

    mock = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "tests.integration.mock_upstream:app",
         "--host", "127.0.0.1", "--port", str(MOCK_PORT), "--log-level", "warning"],
        cwd=str(REPO), env=env,
    )
    log_fh = open(proxy_log, "w")
    litellm = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "litellm.proxy.proxy_cli", "--config", str(CONFIG),
         "--port", str(PROXY_PORT)],
        cwd=str(REPO), env=env, stdout=log_fh, stderr=subprocess.STDOUT,
    )
    try:
        _wait(f"http://127.0.0.1:{MOCK_PORT}/health")
        _wait(f"http://127.0.0.1:{PROXY_PORT}/health/liveliness")
        yield {"base": f"http://127.0.0.1:{PROXY_PORT}", "record": record, "log": proxy_log}
    finally:
        for p in (litellm, mock):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log_fh.close()


def test_chat_completions_non_stream(proxy) -> None:
    r = httpx.post(
        f"{proxy['base']}/v1/chat/completions",
        json={"model": "securitymasker-openai",
              "messages": [{"role": "user", "content": f"connect for {SECRET}"}]},
        timeout=15,
    )
    assert r.status_code == 200
    assert ALIAS in r.text


def test_chat_completions_stream(proxy) -> None:
    import json

    with httpx.stream(
        "POST", f"{proxy['base']}/v1/chat/completions",
        json={"model": "securitymasker-openai", "stream": True,
              "messages": [{"role": "user", "content": f"connect for {SECRET}"}]},
        timeout=15,
    ) as r:
        body = "".join(r.iter_text())
    assert "data:" in body
    # The mock streams in small chunks, so reassemble deltas before checking.
    reassembled = ""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: ") and "[DONE]" not in line:
            delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
            if delta:
                reassembled += delta
    assert ALIAS in reassembled


def test_responses_stream(proxy) -> None:
    with httpx.stream(
        "POST", f"{proxy['base']}/v1/responses",
        json={"model": "securitymasker-openai", "stream": True,
              "input": f"connect for {SECRET}"},
        timeout=15,
    ) as r:
        body = "".join(r.iter_text())
    assert "response.output_text.delta" in body and ALIAS in body


def test_anthropic_messages_stream(proxy) -> None:
    import json

    with httpx.stream(
        "POST", f"{proxy['base']}/v1/messages",
        headers={"anthropic-version": "2023-06-01"},
        json={"model": "securitymasker-anthropic", "max_tokens": 64, "stream": True,
              "messages": [{"role": "user", "content": f"connect for {SECRET}"}]},
        timeout=15,
    ) as r:
        body = "".join(r.iter_text())
    assert "content_block_delta" in body
    # The mock streams small chunks; reassemble text_delta pieces before checking.
    text = ""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                text += ev["delta"]["text"]
    assert ALIAS in text


def test_secret_not_in_proxy_log(proxy) -> None:
    """§25: raw request/secret must not land in the proxy log (set_verbose=false)."""
    log_text = Path(proxy["log"]).read_text(encoding="utf-8", errors="replace")
    assert SECRET not in log_text
    assert "sk-mock-not-a-real-key" not in log_text


def test_mock_received_requests(proxy) -> None:
    """The leakage harness can observe outbound bodies (Phase 1 will assert absence)."""
    record = Path(proxy["record"]).read_text(encoding="utf-8", errors="replace")
    # Phase 0 guardrail is a no-op, so the secret is still forwarded here.
    assert SECRET in record
