"""The real Codex and Claude Code CLIs, driven through `securitymasker run`.

Everything else about `run` is tested by inspecting the settings we generate. That
cannot catch the class of bug where the settings are well-formed by our reckoning
and rejected by the tool — which is exactly what happened: `http_headers` was
emitted as a JSON object, Codex parses `-c` values as TOML, and it refused to
start at all. `run codex` was completely broken while every unit test passed.

This test closes that gap by launching the actual binary and asserting on what
reached the server.

No real provider is contacted: the gateway's upstream is the local mock, and the
CLI is given an isolated ``CODEX_HOME`` so the user's own ``~/.codex`` is neither
read for credentials nor written to. The prompt is synthetic (§30).

    SM_RUN_CLI_E2E=1 .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from securitymasker.integrations.launcher import SESSION_HEADER, build_plan

REPO = Path(__file__).resolve().parents[2]
DICT_CONFIG = REPO / "tests" / "integration" / "securitymasker.masking.yaml"
MOCK_PORT = 8097
GW_PORT = 4017
PROBE_PORT = 8098

PERSON = "山田太郎"                       # synthetic, already used across the suite
HOST = "prod-db01.internal.example"      # .example is reserved for documentation
CARRIER = "接続する Python を書いて"       # non-sensitive; proves the body ARRIVED

pytestmark = [
    pytest.mark.skipif(os.environ.get("SM_RUN_CLI_E2E") != "1",
                       reason="set SM_RUN_CLI_E2E=1 to drive the real CLI"),
]
needs_codex = pytest.mark.skipif(shutil.which("codex") is None,
                                 reason="the codex CLI is not installed")
needs_claude = pytest.mark.skipif(shutil.which("claude") is None,
                                  reason="the claude CLI is not installed")


def _wait(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return
        except Exception:  # noqa: BLE001, S110 - not up yet is the normal case
            pass
        time.sleep(0.3)
    raise RuntimeError(f"never became ready: {url}")


def _serve(port: int, record: Path, env_extra: dict[str, str] | None = None):
    env = {**os.environ, "SM_MOCK_RECORD": str(record), **(env_extra or {})}
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "devtools.mock_upstream:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env,
    )
    return proc


def _stop(*procs: subprocess.Popen) -> None:
    for proc in procs:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _codex_home(tmp_path: Path) -> Path:
    """An isolated CODEX_HOME. The user's own ~/.codex is never touched."""
    home = tmp_path / "codex_home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _last_request(record: Path) -> dict:
    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines, "the upstream recorded no request at all"
    return json.loads(lines[-1])


@pytest.fixture
def stack(tmp_path: Path):
    """Mock upstream + gateway pointed at it. Nothing leaves the machine."""
    record = tmp_path / "record.jsonl"
    mock = _serve(MOCK_PORT, record)
    gw_env = {
        **os.environ,
        "SM_MOCK_RECORD": str(record),
        "SECURITYMASKER_CONFIG": str(DICT_CONFIG),
        "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{MOCK_PORT}",
        "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{MOCK_PORT}",
    }
    gateway = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "securitymasker.gateway.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(GW_PORT),
         "--log-level", "warning"],
        cwd=str(REPO), env=gw_env,
    )
    try:
        _wait(f"http://127.0.0.1:{MOCK_PORT}/health")
        _wait(f"http://127.0.0.1:{GW_PORT}/health")
        assert httpx.get(f"http://127.0.0.1:{GW_PORT}/ready", timeout=5).json()["ready"]
        yield record
    finally:
        _stop(gateway, mock)


@needs_codex
def test_real_codex_through_run_sends_only_aliases(stack, tmp_path) -> None:
    """The end-to-end claim: start the real tool, and no original reaches upstream."""
    record = stack
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "codex",
         "exec", "--skip-git-repo-check",
         f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env={**os.environ,
             "CODEX_HOME": str(_codex_home(tmp_path)),
             "OPENAI_API_KEY": "dummy-not-a-real-key",
             "SECURITYMASKER_GATEWAY_URL": f"http://127.0.0.1:{GW_PORT}"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"codex failed: {result.stderr[-1500:]}"

    body = json.dumps(_last_request(record)["body"], ensure_ascii=False)

    # The body arrived — so the assertions below are about masking, not absence.
    assert CARRIER in body, "the prompt never reached the upstream at all"
    assert PERSON not in body, "the person's name reached the upstream"
    assert HOST not in body, "the internal hostname reached the upstream"
    # ...and it arrived as aliases rather than being dropped.
    assert "SM_PERSON_" in body, f"no PERSON alias in the outbound body: {body[:400]}"


@needs_codex
def test_real_codex_does_not_touch_the_users_codex_home(stack, tmp_path) -> None:
    """`run` must configure the tool per-process, never edit ~/.codex."""
    home = _codex_home(tmp_path)
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "codex",
         "exec", "--skip-git-repo-check", "hello"],
        cwd=str(REPO),
        env={**os.environ, "CODEX_HOME": str(home),
             "OPENAI_API_KEY": "dummy-not-a-real-key",
             "SECURITYMASKER_GATEWAY_URL": f"http://127.0.0.1:{GW_PORT}"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert not (home / "config.toml").exists(), (
        "run wrote a config.toml; the routing must live in per-process -c overrides"
    )


@needs_codex
def test_real_codex_actually_sends_our_session_header(tmp_path) -> None:
    """The regression that unit tests structurally could not catch.

    Codex parses each `-c` value as TOML and, per its own help, falls back to
    treating an unparseable value as a literal string. `http_headers` emitted as a
    JSON object therefore became a string, and Codex rejected it with "expected a
    map" and exited without sending anything. Asserting that the header ARRIVES,
    from the real binary, is the only check that fails when that regresses.
    """
    record = tmp_path / "probe.jsonl"
    mock = _serve(PROBE_PORT, record)
    try:
        _wait(f"http://127.0.0.1:{PROBE_PORT}/health")
        plan = build_plan(["codex"], gateway=f"http://127.0.0.1:{PROBE_PORT}",
                          session_id="sess-header-probe", environ={})
        argv = [*plan.argv[:1], "exec", "--skip-git-repo-check", *plan.argv[1:], "hi"]
        result = subprocess.run(  # noqa: S603
            argv, cwd=str(REPO),
            env={**os.environ, **plan.env,
                 "CODEX_HOME": str(_codex_home(tmp_path)),
                 "OPENAI_API_KEY": "dummy-not-a-real-key"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, (
            f"codex rejected the settings we generate: {result.stderr[-800:]}"
        )
    finally:
        _stop(mock)

    headers = {k.lower(): v for k, v in _last_request(record)["headers"].items()}
    assert headers.get(SESSION_HEADER.lower()) == "sess-header-probe", (
        "the real CLI did not send the session header; without it the gateway "
        "cannot bind the conversation to one alias table"
    )


@needs_claude
def test_real_claude_code_through_run_sends_only_aliases(stack, tmp_path) -> None:
    """Same guarantee for the Anthropic path, which routes by environment.

    Codex is configured with `-c` overrides and Claude Code with
    ANTHROPIC_BASE_URL plus custom headers, so the two have entirely separate
    failure modes and a passing Codex test says nothing about this one.
    """
    record = stack
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "claude",
         "-p", f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env={**os.environ,
             "ANTHROPIC_API_KEY": "dummy-not-a-real-key",
             "CLAUDE_CONFIG_DIR": str(tmp_path / "claude_home"),
             "SECURITYMASKER_GATEWAY_URL": f"http://127.0.0.1:{GW_PORT}"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"claude failed: {result.stderr[-1500:]}"

    request = _last_request(record)
    body = json.dumps(request["body"], ensure_ascii=False)

    assert request["path"].endswith("/messages"), (
        f"expected the Anthropic path, got {request['path']}"
    )
    assert CARRIER in body, "the prompt never reached the upstream at all"
    assert PERSON not in body, "the person's name reached the upstream"
    assert HOST not in body, "the internal hostname reached the upstream"
    assert "SM_PERSON_" in body, f"no PERSON alias in the outbound body: {body[:400]}"
