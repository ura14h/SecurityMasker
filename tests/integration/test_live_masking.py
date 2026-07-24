"""Phase 2 live masking test: real proxy masks outbound & restores inbound (§30.5).

Boots the mock upstream + a LiteLLM proxy with SecurityMasker registered as a
callback and a dictionary config. Verifies the top acceptance criteria (§38):

  1. the final payload the mock receives contains NO original secret (leakage),
  2. aliases the model echoes back are restored before the client sees them,
     for chat + Responses, streaming + non-streaming,
  3. the same secret gets different aliases in different sessions,
  4. no secret lands in the proxy log.

Opt-in (boots two subprocesses). Run with:
    SM_RUN_LIVE=1 .venv/bin/python -m pytest tests/integration/test_live_masking.py -v
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
    reason="set SM_RUN_LIVE=1 to run the live masking integration test",
)

REPO = Path(__file__).resolve().parents[2]
INT = REPO / "tests" / "integration"
LITELLM_CONFIG = INT / "litellm.masking.yaml"
DICT_CONFIG = INT / "securitymasker.masking.yaml"
MOCK_PORT = 8082
PROXY_PORT = 4001
PERSON = "山田太郎"
HOST = "prod-db01.internal.example"
ORIGINALS = (PERSON, HOST)


def _wait(url: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"service not ready: {url}: {last}")


@pytest.fixture(scope="module")
def proxy(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("livemask")
    record = tmp / "record.jsonl"
    log = tmp / "litellm.log"
    env = {**os.environ, "SM_MOCK_RECORD": str(record), "SECURITYMASKER_CONFIG": str(DICT_CONFIG)}

    mock = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "tests.integration.mock_upstream:app",
         "--host", "127.0.0.1", "--port", str(MOCK_PORT), "--log-level", "warning"],
        cwd=str(REPO), env=env,
    )
    log_fh = open(log, "w")  # noqa: SIM115
    proxy_proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "litellm.proxy.proxy_cli", "--config", str(LITELLM_CONFIG),
         "--port", str(PROXY_PORT)],
        cwd=str(REPO), env=env, stdout=log_fh, stderr=subprocess.STDOUT,
    )
    try:
        _wait(f"http://127.0.0.1:{MOCK_PORT}/health")
        _wait(f"http://127.0.0.1:{PROXY_PORT}/health/liveliness")
        yield {"base": f"http://127.0.0.1:{PROXY_PORT}", "record": record, "log": log}
    finally:
        for p in (proxy_proc, mock):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log_fh.close()


def _headers(session: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-SecurityMasker-Session-ID": session}


PROMPT = f"担当は{PERSON}、接続先 {HOST}"


def test_chat_non_stream_masks_and_restores(proxy) -> None:
    r = httpx.post(
        f"{proxy['base']}/v1/chat/completions", headers=_headers("m1"),
        json={"model": "securitymasker-openai", "messages": [{"role": "user", "content": PROMPT}]},
        timeout=15,
    )
    reply = r.json()["choices"][0]["message"]["content"]
    assert PERSON in reply and HOST in reply  # restored for the client


def test_chat_stream_masks_and_restores(proxy) -> None:
    with httpx.stream(
        "POST", f"{proxy['base']}/v1/chat/completions", headers=_headers("m2"),
        json={"model": "securitymasker-openai", "stream": True,
              "messages": [{"role": "user", "content": PROMPT}]},
        timeout=15,
    ) as r:
        buf = ""
        for line in "".join(r.iter_text()).splitlines():
            line = line.strip()
            if line.startswith("data: ") and "[DONE]" not in line:
                delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                if delta:
                    buf += delta
    assert PERSON in buf and HOST in buf  # aliases split across chunks were restored


def test_responses_non_stream_restores(proxy) -> None:
    r = httpx.post(
        f"{proxy['base']}/v1/responses", headers=_headers("m3"),
        json={"model": "securitymasker-openai", "input": PROMPT}, timeout=15,
    )
    text = r.json()["output"][0]["content"][0]["text"]
    assert PERSON in text and HOST in text


def test_responses_stream_restores(proxy) -> None:
    with httpx.stream(
        "POST", f"{proxy['base']}/v1/responses", headers=_headers("m4"),
        json={"model": "securitymasker-openai", "stream": True, "input": PROMPT}, timeout=15,
    ) as r:
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
    assert PERSON in deltas and HOST in deltas


def _anthropic_headers(session: str) -> dict[str, str]:
    return {**_headers(session), "anthropic-version": "2023-06-01"}


def test_anthropic_non_stream_restores(proxy) -> None:
    r = httpx.post(
        f"{proxy['base']}/v1/messages", headers=_anthropic_headers("a1"),
        json={"model": "securitymasker-anthropic", "max_tokens": 64,
              "messages": [{"role": "user", "content": PROMPT}]},
        timeout=15,
    )
    text = r.json()["content"][0]["text"]
    assert PERSON in text and HOST in text


def test_anthropic_stream_restores(proxy) -> None:
    with httpx.stream(
        "POST", f"{proxy['base']}/v1/messages", headers=_anthropic_headers("a2"),
        json={"model": "securitymasker-anthropic", "max_tokens": 64, "stream": True,
              "messages": [{"role": "user", "content": PROMPT}]},
        timeout=15,
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
    assert PERSON in text and HOST in text  # aliases split across deltas were restored


def test_no_original_leaked_to_upstream(proxy) -> None:
    """The single most important acceptance test (§30.5): nothing original left."""
    record = Path(proxy["record"]).read_text(encoding="utf-8", errors="replace")
    assert record  # the mock did receive requests
    for original in ORIGINALS:
        assert original not in record, f"leaked {original!r} to upstream"


def test_no_secret_in_proxy_log(proxy) -> None:
    log = Path(proxy["log"]).read_text(encoding="utf-8", errors="replace")
    for original in ORIGINALS:
        assert original not in log


def test_different_sessions_get_different_aliases(proxy) -> None:
    for session in ("sX", "sY"):
        httpx.post(
            f"{proxy['base']}/v1/chat/completions", headers=_headers(session),
            json={"model": "securitymasker-openai", "messages": [{"role": "user", "content": PERSON}]},
            timeout=15,
        )
    records = [
        json.loads(line)
        for line in Path(proxy["record"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Collect the masked person alias sent for each of the two sessions.
    person_aliases = set()
    for rec in records:
        for msg in rec.get("body", {}).get("messages", []):
            content = msg.get("content")
            if isinstance(content, str) and content.startswith("SM_PERSON_"):
                person_aliases.add(content)
    assert len(person_aliases) >= 2, f"expected distinct aliases per session, got {person_aliases}"
