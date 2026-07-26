"""The real Codex and Claude Code CLIs, driven through `securitymasker run`.

Everything else about `run` is tested by inspecting the settings we generate. That
cannot catch the class of bug where the settings are well-formed by our reckoning
and rejected by the tool — which is exactly what happened: `http_headers` was
emitted as a JSON object, Codex parses `-c` values as TOML, and it refused to
start at all. `run codex` was completely broken while every unit test passed.

This test closes that gap by launching the actual binary and asserting on what
reached the server.

**Egress.** Pointing a CLI at a local URL is a routing choice, not a containment
boundary: both tools have update checks, analytics and crash reporting that do not
go through the configured provider at all, so a routing mistake here would leak to
the internet rather than fail. Dummy credentials do not change that.

So containment is **measured, not declared**: before anything starts, the test
tries to open a TCP connection to a routable address. If that succeeds, this
process can reach the internet, and the test skips. No environment variable
overrides this — an earlier version had an ``SM_E2E_ALLOW_UNSANDBOXED`` flag, and
a flag meaning "trust me, egress is blocked" is exactly the assurance that cannot
be checked. The probe can be, so it is.

To make the probe fail, run the WHOLE stack — pytest, the gateway, the mock and
the CLI — inside one network namespace or a ``--network none`` container:

    devtools/run_cli_e2e.sh          # unshare -rn around pytest itself (Linux)

Isolating only the CLI does not work, and was the previous mistake here: a network
namespace has its own loopback, so a CLI inside one cannot reach a gateway on the
parent's 127.0.0.1. Everything has to share the namespace.

On top of the boundary, the child gets a scrubbed environment, telemetry and
auto-update switched off, and proxy variables aimed at a closed loopback port, so
anything that honours them fails fast. That is defence in depth; the probe is the
guarantee.

The gateway's upstream is the local mock, the CLIs get isolated homes so the
user's own ``~/.codex`` is neither read for credentials nor written to, and the
prompt is synthetic (§30).

    SM_RUN_CLI_E2E=1 .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from securitymasker.integrations.launcher import SESSION_HEADER, build_plan

REPO = Path(__file__).resolve().parents[2]
DICT_CONFIG = REPO / "tests" / "integration" / "securitymasker.masking.yaml"


def _free_port() -> int:
    """A port the OS just told us is free.

    Fixed ports made these tests interfere: each one starts its own servers, and a
    previous test's dying uvicorn can still answer /health on the same port while
    the new one is binding — so a test would talk to the wrong gateway and fail
    intermittently. Nothing here is long-lived enough to need a stable port.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _can_reach_the_internet(timeout: float = 1.5) -> bool:
    """Whether this process can open a TCP connection off-box.

    Deliberately empirical. A firewall, an offline host and a network namespace
    all look different from the inside, and none of them can be recognised by
    checking for a tool or reading a flag — but all of them show up here. Only a
    connection is attempted; nothing is sent.

    A drop-style firewall makes this time out rather than refuse, so a timeout
    counts as blocked.
    """
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


PERSON = "山田太郎"                       # synthetic, already used across the suite
HOST = "prod-db01.internal.example"      # .example is reserved for documentation
CARRIER = "接続する Python を書いて"       # non-sensitive; proves the body ARRIVED

# A port nothing listens on: proxy settings pointed here fail immediately.
CLOSED_PORT = 1

pytestmark = [
    pytest.mark.skipif(os.environ.get("SM_RUN_CLI_E2E") != "1",
                       reason="set SM_RUN_CLI_E2E=1 to drive the real CLI"),
    pytest.mark.skipif(
        _can_reach_the_internet(),
        reason="this process can reach the internet, so the CLIs could too; run "
               "the whole stack in a network namespace (devtools/run_cli_e2e.sh) "
               "or a --network none container",
    ),
]


def _contained_env(**overrides: str) -> dict[str, str]:
    """A minimal environment with telemetry, updates and egress turned off.

    Inherited variables are dropped rather than filtered: a stray ANTHROPIC_API_URL
    or HTTPS_PROXY from the developer's shell is exactly the kind of thing that
    would route real traffic out of a test that looks local.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER", "TERM")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({
        # Nothing this test does should leave the machine; if any component
        # honours proxy settings, send it somewhere closed.
        "HTTP_PROXY": f"http://127.0.0.1:{CLOSED_PORT}",
        "HTTPS_PROXY": f"http://127.0.0.1:{CLOSED_PORT}",
        "ALL_PROXY": f"http://127.0.0.1:{CLOSED_PORT}",
        "NO_PROXY": "127.0.0.1,localhost",      # ...except our own stack
        # Documented switches for the non-provider traffic each CLI can make.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_BUG_COMMAND": "1",
        "CODEX_DISABLE_UPDATE_CHECK": "1",
        "DO_NOT_TRACK": "1",
    })
    env.update(overrides)
    return env
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


def _assert_masked_upstream(record: Path) -> None:
    """Only aliases left the process — and the body actually arrived."""
    body = json.dumps(_last_request(record)["body"], ensure_ascii=False)
    # CARRIER first: without it the checks below would also pass on a request that
    # never carried the prompt at all.
    assert CARRIER in body, "the prompt never reached the upstream at all"
    assert PERSON not in body, "the person's name reached the upstream"
    assert HOST not in body, "the internal hostname reached the upstream"
    assert "SM_PERSON_" in body, f"no PERSON alias in the outbound body: {body[:400]}"


def _assert_restored_to_the_user(stdout: str) -> None:
    """The user got their own data back.

    Masking without restoration is not the feature — it is the product broken in a
    safe direction, and every upstream assertion would still pass. The mock echoes
    what it was sent, so both originals come back down the stream and must appear.
    """
    for original, what in ((PERSON, "name"), (HOST, "hostname")):
        assert original in stdout, (
            f"the CLI never showed the restored {what}; the response was not "
            f"restored. stdout: {stdout[-800:]}"
        )
    assert "SM_PERSON_" not in stdout, "an alias leaked into the CLI output"


@pytest.fixture
def stack(tmp_path: Path):
    """Mock upstream + gateway pointed at it. Nothing leaves the machine."""
    record = tmp_path / "record.jsonl"
    mock_port, gw_port = _free_port(), _free_port()
    mock = _serve(mock_port, record)
    gw_env = {
        **os.environ,
        "SM_MOCK_RECORD": str(record),
        "SECURITYMASKER_CONFIG": str(DICT_CONFIG),
        "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{mock_port}",
        "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{mock_port}",
    }
    gateway = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "securitymasker.gateway.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(gw_port),
         "--log-level", "warning"],
        cwd=str(REPO), env=gw_env,
    )
    try:
        _wait(f"http://127.0.0.1:{mock_port}/health")
        _wait(f"http://127.0.0.1:{gw_port}/health")
        assert httpx.get(f"http://127.0.0.1:{gw_port}/ready", timeout=5).json()["ready"]
        yield record, f"http://127.0.0.1:{gw_port}"
    finally:
        _stop(gateway, mock)


@needs_codex
def test_real_codex_through_run_sends_only_aliases(stack, tmp_path) -> None:
    """The end-to-end claim: start the real tool, and no original reaches upstream."""
    record, gateway_url = stack
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "codex",
         "exec", "--skip-git-repo-check",
         f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env=_contained_env(
            CODEX_HOME=str(_codex_home(tmp_path)),
            OPENAI_API_KEY="dummy-not-a-real-key",
            SECURITYMASKER_GATEWAY_URL=gateway_url),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"codex failed: {result.stderr[-1500:]}"

    _assert_masked_upstream(record)
    _assert_restored_to_the_user(result.stdout)


@needs_codex
def test_real_codex_does_not_touch_the_users_codex_home(stack, tmp_path) -> None:
    """`run` must configure the tool per-process, never edit ~/.codex."""
    _record, gateway_url = stack
    home = _codex_home(tmp_path)
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "codex",
         "exec", "--skip-git-repo-check", "hello"],
        cwd=str(REPO),
        env=_contained_env(
            CODEX_HOME=str(home),
            OPENAI_API_KEY="dummy-not-a-real-key",
            SECURITYMASKER_GATEWAY_URL=gateway_url),
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
    probe_port = _free_port()
    mock = _serve(probe_port, record)
    try:
        _wait(f"http://127.0.0.1:{probe_port}/health")
        plan = build_plan(["codex"], gateway=f"http://127.0.0.1:{probe_port}",
                          session_id="sess-header-probe", environ={})
        argv = [*plan.argv[:1], "exec", "--skip-git-repo-check", *plan.argv[1:], "hi"]
        result = subprocess.run(  # noqa: S603
            argv, cwd=str(REPO),
            env=_contained_env(**plan.env,
                               CODEX_HOME=str(_codex_home(tmp_path)),
                               OPENAI_API_KEY="dummy-not-a-real-key"),
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
    record, gateway_url = stack
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "securitymasker.cli", "run", "claude",
         "-p", f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env=_contained_env(
            ANTHROPIC_API_KEY="dummy-not-a-real-key",
            CLAUDE_CONFIG_DIR=str(tmp_path / "claude_home"),
            SECURITYMASKER_GATEWAY_URL=gateway_url),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"claude failed: {result.stderr[-1500:]}"

    assert _last_request(record)["path"].endswith("/messages"), (
        f"expected the Anthropic path, got {_last_request(record)['path']}"
    )
    _assert_masked_upstream(record)
    _assert_restored_to_the_user(result.stdout)
