"""永続client設定を使う実Codex／Claude Code CLIのE2E検証。

通常運用と同じconfig/dict/state/key、`client-config`と同じ生成元の永続設定を使う。

**Egress.** Pointing a CLI at a local URL is a routing choice, not a containment
boundary: both tools have update checks, analytics and crash reporting that do not
go through the configured provider at all, so a routing mistake here would leak to
the internet rather than fail. Dummy credentials do not change that.

So containment is **checked structurally**. Linux requires no active non-loopback
interface and no default route. Windows requires two active outbound block rules
installed by an administrator for the dedicated standard-user SID. Those rules
cover every IPv4 and IPv6 destination except 127.0.0.0/8 and ::1, and the test user
cannot modify them. Both boundaries are inspected before any connection attempt.

An earlier version instead tried to connect to 1.1.1.1 and 8.8.8.8 and treated
failure as safety. That is not a proof of anything: a network can drop those two
addresses and still route to a provider, block IPv4 while allowing IPv6, or filter
public DNS but not HTTPS. Failing to reach two addresses says nothing about the
third. It also ran at import, so merely collecting this file opened outbound
connections. The check now touches the network only to enumerate interfaces, and
runs at fixture time, so an un-opted-in collection does nothing at all.

A connect attempt survives only as a secondary assertion AFTER the structural
check has passed: if the stack says isolated and a connection still succeeds,
something is wrong enough to fail rather than skip.

On Linux, run the WHOLE stack — pytest, the gateway, the mock and the CLI — inside
one namespace:

    devtools/run_cli_e2e.sh          # unshare -rn around pytest itself (Linux)

Isolating only the CLI does not work, and was an earlier mistake here: a network
namespace has its own loopback, so a CLI inside one cannot reach a gateway on the
parent's 127.0.0.1. Everything has to share the namespace.

On top of the boundary, the child gets a scrubbed environment, telemetry and
auto-update switched off, and proxy variables aimed at a closed loopback port, so
anything that honours them fails fast. That is defence in depth; the probe is the
guarantee.

On Windows, run the whole stack as the dedicated firewall-gated standard user via
``scripts\\windows-cli-e2e.cmd``. The operator and Codex Desktop remain under a
different user and retain their normal connection.

The gateway's upstream is the local mock, the CLIs get isolated homes so the
user's own ``~/.codex`` is neither read for credentials nor written to, and the
prompt is synthetic and contains no real personal or secret data.

    SM_RUN_CLI_E2E=1 .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from securitymasker.bootstrap import initialize_layout
from securitymasker.config import load_config
from securitymasker.integrations.client_config import (
    client_environment,
    client_setup_snippet,
)

REPO = Path(__file__).resolve().parents[2]
WINDOWS_FIREWALL_GATE = REPO / "devtools" / "windows_firewall_gate.ps1"


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


def _has_default_route_v4(proc: str = "/proc/net/route") -> bool:
    """A default route out of a non-loopback device."""
    try:
        lines = Path(proc).read_text().splitlines()[1:]
    except OSError:
        return True          # cannot tell -> assume not isolated
    for line in lines:
        fields = line.split()
        # Iface, Destination, ... — destination 0.0.0.0 is the default route.
        if len(fields) > 1 and fields[1] == "00000000" and fields[0] != "lo":
            return True
    return False


def _has_default_route_v6(proc: str = "/proc/net/ipv6_route") -> bool:
    try:
        lines = Path(proc).read_text().splitlines()
    except OSError:
        return False         # no IPv6 stack at all is fine
    for line in lines:
        fields = line.split()
        # dest(32 hex) prefixlen(hex) ... device(last). `::/0` is the default.
        if (len(fields) >= 10 and fields[0] == "0" * 32 and fields[1] == "00"
                and fields[-1] != "lo"):
            return True
    return False


def _active_non_loopback_interfaces(
    sysfs: Path = Path("/sys/class/net"),
) -> list[str]:
    """Administratively UPな非loopback interfaceを返す。

    Docker ``--network none`` でもkernel組み込みのtunnel deviceは列挙されるが、
    IFF_UPが落ちていればpacketを送出できない。状態を読めないdeviceは安全と推測せず、
    active扱いにしてfail-closedとする。
    """
    active: list[str] = []
    for _, name in socket.if_nameindex():
        if name == "lo":
            continue
        try:
            flags = int((sysfs / name / "flags").read_text().strip(), 0)
        except (OSError, ValueError):
            active.append(name)
            continue
        if flags & 0x1:  # Linux IFF_UP
            active.append(name)
    return sorted(active)


def _isolation_failure() -> str | None:
    """Why this host is not network-isolated, or None when it is.

    Structural, not probabilistic: a host with no active interface other than
    loopback and no default route cannot reach anything off-box, whatever the
    firewall policy happens to be. That is exactly what `unshare -n` and
    `--network none` produce, and it is checkable without sending a packet anywhere.

    The interface check is decisive — an administratively down device cannot send.
    Linux can still enumerate down tunnel devices in a network-none container, so
    mere presence is not treated as connectivity. The route checks are defence in
    depth, and ignore routes bound to `lo`, which a fresh namespace can carry
    without them meaning anything.
    """
    if sys.platform == "win32":
        return _windows_firewall_isolation_failure()
    if sys.platform != "linux":
        return (f"network isolation cannot be verified on {sys.platform}; run the "
                "whole stack in a Linux namespace (devtools/run_cli_e2e.sh) or a "
                "--network none container")
    external = _active_non_loopback_interfaces()
    if external:
        return f"active non-loopback interfaces are present: {external}"
    if _has_default_route_v4():
        return "an IPv4 default route exists"
    if _has_default_route_v6():
        return "an IPv6 default route exists"
    return None


def _windows_firewall_isolation_failure() -> str | None:
    """専用standard userへ有効なloopback-only firewall gateを要求する。"""
    try:
        completed = subprocess.run(  # noqa: S603
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINDOWS_FIREWALL_GATE),
                "-Action",
                "Verify",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Windows firewall gate inspection failed: {type(exc).__name__}"
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        return "Windows firewall gate is not active" + (
            f": {detail[-1]}" if detail else ""
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "Windows firewall gate returned invalid verification data"
    if result.get("isolated") is not True or len(result.get("rule_names", [])) != 2:
        return "Windows firewall gate did not verify both deny rules"
    return None


def _assert_nothing_routes_off_box() -> None:
    """Secondary check, only meaningful once the structural one has passed.

    If the stack says there is nowhere to go and a connection nevertheless
    succeeds, the structural check is being fooled — which is a failure, not a
    reason to skip quietly.
    """
    for host, port in (
        ("1.1.1.1", 443),
        ("8.8.8.8", 53),
        ("2606:4700:4700::1111", 443),
    ):
        try:
            with socket.create_connection((host, port), timeout=1.0):
                raise AssertionError(
                    f"reached {host}:{port} despite no default route — this "
                    "process is not contained"
                )
        except OSError:
            pass


PERSON = "山田太郎"                       # synthetic, already used across the suite
HOST = "prod-db01.internal.example"      # .example is reserved for documentation
CARRIER = "接続する Python を書いて"       # non-sensitive; proves the body ARRIVED

# A port nothing listens on: proxy settings pointed here fail immediately.
CLOSED_PORT = 1

# Only an environment read at import time. The isolation check is a fixture, so
# collecting this file never touches the network.
pytestmark = [
    pytest.mark.skipif(os.environ.get("SM_RUN_CLI_E2E") != "1",
                       reason="set SM_RUN_CLI_E2E=1 to drive the real CLI"),
]


@pytest.fixture(autouse=True)
def _require_network_isolation():
    """No test in this module runs on a host that can reach anything off-box."""
    reason = _isolation_failure()
    if reason is not None:
        pytest.skip(f"not network-isolated: {reason}")
    _assert_nothing_routes_off_box()


def _contained_env(**overrides: str) -> dict[str, str]:
    """A minimal environment with telemetry, updates and egress turned off.

    Inherited variables are dropped rather than filtered: a stray ANTHROPIC_API_URL
    or HTTPS_PROXY from the developer's shell is exactly the kind of thing that
    would route real traffic out of a test that looks local.
    """
    keep = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "SHELL",
        "USER",
        "TERM",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
    )
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


def _isolated_windows_profile(root: Path) -> dict[str, str]:
    """Windows native CLIへ空のuser-writable profile treeを渡す。"""
    if sys.platform != "win32":
        return {"HOME": str(root / "home")}
    home = root / "home"
    roaming = home / "AppData" / "Roaming"
    local = home / "AppData" / "Local"
    temporary = local / "Temp"
    for directory in (home, roaming, local, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(roaming),
        "LOCALAPPDATA": str(local),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "HOMEDRIVE": home.drive,
        "HOMEPATH": str(home)[len(home.drive) :],
    }


def _require_cli(name: str) -> str:
    override_name = "SM_CODEX_CLI" if name == "codex" else "SM_CLAUDE_CLI"
    override = os.environ.get(override_name)
    candidate = _windows_cli_candidate(name) if sys.platform == "win32" else None
    executable = (
        str(Path(override).resolve())
        if override
        else shutil.which(name)
        or (str(candidate) if candidate and candidate.is_file() else None)
    )
    if override and not Path(executable).is_file():
        executable = None
    if executable is not None:
        return executable
    message = f"required real CLI is not installed: {name}"
    if os.environ.get("SM_REQUIRE_ALL_CLIS") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _windows_cli_candidate(name: str) -> Path | None:
    if name == "codex" and os.environ.get("LOCALAPPDATA"):
        return (
            Path(os.environ["LOCALAPPDATA"])
            / "Programs"
            / "OpenAI"
            / "Codex"
            / "bin"
            / "codex.exe"
        )
    if name == "claude" and os.environ.get("USERPROFILE"):
        return Path(os.environ["USERPROFILE"]) / ".local" / "bin" / "claude.exe"
    return None


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


def _codex_home(tmp_path: Path, config) -> tuple[Path, Path]:
    """製品generatorで永続設定を作る隔離CODEX_HOME。"""
    home = tmp_path / "codex_home"
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.toml"
    config_path.write_text(client_setup_snippet(config), encoding="utf-8")
    return home, config_path


def _last_request(record: Path) -> dict:
    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines, "the upstream recorded no request at all"
    return json.loads(lines[-1])


def _aliases_sent_upstream(record: Path) -> set[str]:
    """Every alias in the outbound body, read from the request itself.

    Taken from what was actually sent rather than matched by prefix, so the
    hostname alias — which is host-shaped (`sm-host-….example.invalid`) and shares
    no prefix with `SM_PERSON_…` — is covered too.
    """
    body = json.dumps(_last_request(record)["body"], ensure_ascii=False)
    return set(re.findall(r"SM_[A-Z]+_[0-9A-F]+", body)) | set(
        re.findall(r"sm-[a-z]+-[0-9a-f]+\.example\.invalid", body)
    )


def _assert_masked_upstream(record: Path) -> None:
    """Only aliases left the process — and the body actually arrived."""
    body = json.dumps(_last_request(record)["body"], ensure_ascii=False)
    # CARRIER first: without it the checks below would also pass on a request that
    # never carried the prompt at all.
    assert CARRIER in body, "the prompt never reached the upstream at all"
    assert PERSON not in body, "the person's name reached the upstream"
    assert HOST not in body, "the internal hostname reached the upstream"

    aliases = _aliases_sent_upstream(record)
    assert any(a.startswith("SM_PERSON_") for a in aliases), (
        f"no PERSON alias in the outbound body: {body[:400]}"
    )
    assert any(a.startswith("sm-host-") for a in aliases), (
        f"no HOSTNAME alias in the outbound body: {body[:400]}"
    )


def _assert_restored_to_the_user(stdout: str, record: Path) -> None:
    """The user got their own data back, and no alias with it.

    Masking without restoration is not the feature — it is the product broken in a
    safe direction, and every upstream assertion would still pass. Checking that
    the originals appear is not sufficient either: a response containing BOTH the
    original and a leftover alias would pass that alone, so every alias actually
    sent is checked against the output.
    """
    for original, what in ((PERSON, "name"), (HOST, "hostname")):
        assert original in stdout, (
            f"the CLI never showed the restored {what}; the response was not "
            f"restored. stdout: {stdout[-800:]}"
        )
    leaked = sorted(a for a in _aliases_sent_upstream(record) if a in stdout)
    assert not leaked, f"aliases survived into the CLI output: {leaked}"


def _write_e2e_dictionary(path: Path) -> None:
    path.write_text(
        """\
version: 1
entities:
  - id: person
    type: PERSON
    values: ["山田太郎"]
    replacement_profile: prose_identifier
    restore_policy: literal
patterns:
  - id: prod_host
    pattern: 'prod-db01\\.internal\\.example'
    type: HOSTNAME
    replacement_profile: hostname
    restore_policy: literal
""",
        encoding="utf-8",
    )
    path.chmod(0o600)


@pytest.fixture
def stack(tmp_path: Path, request: pytest.FixtureRequest):
    """mode別v2 layout + mock upstream + source Gateway。"""
    mode = str(request.param)
    _require_cli("codex" if mode == "chatgpt" else "claude")
    record = tmp_path / "record.jsonl"
    mock_port, gw_port = _free_port(), _free_port()
    layout = initialize_layout(tmp_path / "product", mode=mode, port=gw_port)
    _write_e2e_dictionary(layout.dictionary)
    config = load_config(layout.config)
    mock = _serve(mock_port, record)
    gw_env = {
        **os.environ,
        "SM_MOCK_RECORD": str(record),
        "SECURITYMASKER_CONFIG": str(layout.config),
        "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{mock_port}",
        "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{mock_port}",
    }
    gateway = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(REPO / "securitymasker.py"),
            "gateway",
            "--config",
            str(layout.config),
        ],
        cwd=str(REPO), env=gw_env,
    )
    try:
        _wait(f"http://127.0.0.1:{mock_port}/health")
        _wait(f"http://127.0.0.1:{gw_port}/health")
        assert httpx.get(f"http://127.0.0.1:{gw_port}/ready", timeout=5).json()["ready"]
        yield record, config, layout
    finally:
        _stop(gateway, mock)


@pytest.mark.parametrize("stack", ["chatgpt"], indirect=True)
def test_real_codex_with_persistent_config_sends_only_aliases(stack, tmp_path) -> None:
    """隔離した永続config.tomlでmask・復元を確認する。"""
    codex = _require_cli("codex")
    record, config, _layout = stack
    home, config_path = _codex_home(tmp_path, config)
    original_config = config_path.read_bytes()
    result = subprocess.run(  # noqa: S603
        [codex, "exec", "--skip-git-repo-check",
         f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env=_contained_env(
            **_isolated_windows_profile(tmp_path),
            CODEX_HOME=str(home),
            OPENAI_API_KEY="dummy-not-a-real-key"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, f"codex failed: {(result.stderr or '')[-1500:]}"
    assert config_path.read_bytes() == original_config

    assert _last_request(record).get("transport") == "websocket", (
        "Codex did not use the Responses WebSocket transport"
    )
    _assert_masked_upstream(record)
    _assert_restored_to_the_user(result.stdout, record)


@pytest.mark.parametrize("stack", ["claude"], indirect=True)
def test_real_claude_with_persistent_environment_sends_only_aliases(
    stack, tmp_path
) -> None:
    """隔離したClaude設定directoryと製品生成environmentでmask・復元を確認する。"""
    claude = _require_cli("claude")
    record, config, _layout = stack
    claude_home = tmp_path / "claude_home"
    claude_home.mkdir()
    result = subprocess.run(  # noqa: S603
        [claude, "-p", f"担当は{PERSON}です。{HOST} に{CARRIER}。"],
        cwd=str(REPO),
        env=_contained_env(
            **_isolated_windows_profile(tmp_path),
            **client_environment(config),
            ANTHROPIC_API_KEY="dummy-not-a-real-key",
            CLAUDE_CONFIG_DIR=str(claude_home)),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, f"claude failed: {(result.stderr or '')[-1500:]}"

    assert _last_request(record)["path"].endswith("/messages"), (
        f"expected the Anthropic path, got {_last_request(record)['path']}"
    )
    _assert_masked_upstream(record)
    _assert_restored_to_the_user(result.stdout, record)
