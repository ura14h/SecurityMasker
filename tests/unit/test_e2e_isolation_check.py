"""The real-CLI E2E's isolation check, tested where it can be tested.

The check itself only ever runs on Linux, and the machine writing it may not be,
so its /proc parsing is exercised here against real-format fixtures. Getting this
wrong is not a safety hole — a false "not isolated" only skips — but a false
"isolated" would let the E2E run uncontained, which is the thing it exists to
prevent.

The earlier version of this check tried to connect to two fixed addresses and read
failure as safety. That is not evidence: a network can drop those two and still
route to a provider. It also ran at import, so collecting the module opened
outbound connections.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "integration" / "test_real_cli_e2e.py"

# Real formats, copied shape-for-shape.
ROUTE_WITH_DEFAULT = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""
ROUTE_NAMESPACE = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
"""
ROUTE_LOOPBACK_ONLY = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
lo\t00000000\t00000000\t0001\t0\t0\t0\t00000000\t0\t0\t0
"""

V6_WITH_DEFAULT = (
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00 "
    "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
)
V6_LOOPBACK_ONLY = (
    "00000000000000000000000000000001 80 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00000000 00000002 00000000 80200001 lo\n"
)
V6_UNREACHABLE_DEFAULT_ON_LO = (
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 ffffffff 00000001 00000001 00200200 lo\n"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("_e2e_mod", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- IPv4 -------------------------------------------------------------------------


def test_a_real_default_route_is_detected(mod, tmp_path) -> None:
    assert mod._has_default_route_v4(_write(tmp_path, "r", ROUTE_WITH_DEFAULT))


def test_a_fresh_namespace_has_no_default_route(mod, tmp_path) -> None:
    assert not mod._has_default_route_v4(_write(tmp_path, "r", ROUTE_NAMESPACE))


def test_a_default_route_via_loopback_does_not_count(mod, tmp_path) -> None:
    # Nowhere to go: the decisive check is that only `lo` exists.
    assert not mod._has_default_route_v4(_write(tmp_path, "r", ROUTE_LOOPBACK_ONLY))


def test_an_unreadable_route_table_is_treated_as_not_isolated(mod, tmp_path) -> None:
    """Unknown must fail safe, i.e. towards skipping the test."""
    assert mod._has_default_route_v4(str(tmp_path / "does-not-exist"))


# --- IPv6 -------------------------------------------------------------------------


def test_ipv6_default_route_is_detected(mod, tmp_path) -> None:
    assert mod._has_default_route_v6(_write(tmp_path, "r6", V6_WITH_DEFAULT))


def test_ipv6_loopback_only_is_isolated(mod, tmp_path) -> None:
    assert not mod._has_default_route_v6(_write(tmp_path, "r6", V6_LOOPBACK_ONLY))


def test_ipv6_unreachable_default_on_lo_is_isolated(mod, tmp_path) -> None:
    """A namespace carries this entry; it does not mean the host can route."""
    assert not mod._has_default_route_v6(
        _write(tmp_path, "r6", V6_UNREACHABLE_DEFAULT_ON_LO))


def test_a_missing_ipv6_table_is_fine(mod, tmp_path) -> None:
    assert not mod._has_default_route_v6(str(tmp_path / "no-ipv6"))


# --- the verdict -------------------------------------------------------------------


def test_a_host_with_external_interfaces_is_not_isolated(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    reason = mod._isolation_failure()
    assert reason and "eth0" in reason


def test_loopback_only_with_no_routes_is_isolated(mod, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(mod, "_has_default_route_v4", lambda *a: False)
    monkeypatch.setattr(mod, "_has_default_route_v6", lambda *a: False)
    assert mod._isolation_failure() is None


def test_non_linux_is_never_considered_isolated(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    reason = mod._isolation_failure()
    assert reason and "darwin" in reason


def test_windows_requires_verified_firewall_gate(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="gate missing\n"
        ),
    )
    reason = mod._isolation_failure()
    assert reason and "gate missing" in reason


def test_windows_accepts_both_verified_firewall_rules(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"isolated":true,"rule_names":["v4","v6"]}',
            stderr="",
        ),
    )
    assert mod._isolation_failure() is None


def test_windows_rejects_incomplete_firewall_verification(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"isolated":true,"rule_names":["v4"]}',
            stderr="",
        ),
    )
    reason = mod._isolation_failure()
    assert reason and "both deny rules" in reason


def test_windows_client_profile_is_empty_and_self_contained(
    mod, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(mod.sys, "platform", "win32")
    profile = mod._isolated_windows_profile(tmp_path)

    home = tmp_path / "home"
    assert profile["HOME"] == str(home)
    assert profile["USERPROFILE"] == str(home)
    assert profile["APPDATA"] == str(home / "AppData" / "Roaming")
    assert profile["LOCALAPPDATA"] == str(home / "AppData" / "Local")
    assert profile["TEMP"] == str(home / "AppData" / "Local" / "Temp")
    assert Path(profile["TEMP"]).is_dir()


def test_real_cli_output_is_decoded_as_utf8() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert source.count('encoding="utf-8"') >= 2
    assert source.count("result.stderr or result.stdout") == 2


def test_claude_e2e_uses_empty_workdir_and_disables_ambient_context() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'workdir = tmp_path / "empty-workdir"' in source
    assert '"--no-session-persistence"' in source
    assert '"--disable-slash-commands"' in source
    assert '"--tools",\n            ""' in source
    assert '"--setting-sources",\n            ""' in source
    assert 'cwd=str(workdir)' in source


def test_importing_the_e2e_module_opens_no_connections(mod, monkeypatch) -> None:
    """Collection must not touch the network, opted in or not."""
    import socket as socket_module

    attempts: list = []
    monkeypatch.setattr(socket_module, "create_connection",
                        lambda addr, *a, **k: attempts.append(addr))
    spec = importlib.util.spec_from_file_location("_e2e_reimport", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert attempts == []


def test_e2e_uses_persistent_client_settings_not_the_run_wrapper() -> None:
    source = MODULE.read_text(encoding="utf-8")
    assert "initialize_layout" in source
    assert "client_setup_snippet" in source
    assert "client_environment" in source
    assert "build_plan" not in source
    assert '"securitymasker.cli", "run"' not in source
