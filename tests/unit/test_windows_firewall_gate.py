"""Windows実CLI firewall gateのfail-closedな配線を検査する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POWERSHELL_GATE = REPO / "devtools" / "windows_firewall_gate.ps1"
CMD_GATE = REPO / "scripts" / "windows-firewall-gate.cmd"
CMD_E2E = REPO / "scripts" / "windows-cli-e2e.cmd"


def test_firewall_gate_is_user_scoped_and_excludes_only_loopback() -> None:
    source = POWERSHELL_GATE.read_text(encoding="utf-8")

    assert "-LocalUser $sddl" in source
    assert '"0.0.0.0-126.255.255.255"' in source
    assert '"128.0.0.0-255.255.255.255"' in source
    assert '"::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"' in source
    assert "-Protocol Any" in source
    assert "-Profile Any" in source
    assert "-Direction Outbound" in source
    assert "-Action Block" in source


def test_firewall_gate_requires_a_different_non_admin_local_user() -> None:
    source = POWERSHELL_GATE.read_text(encoding="utf-8")

    assert "the firewall gate account must be a local Windows user" in source
    assert "install the gate for a different user" in source
    assert "the firewall gate account must not be an administrator" in source
    assert "the CLI E2E firewall gate must run as a standard user" in source


def test_firewall_install_rolls_back_and_remove_checks_rule_group() -> None:
    source = POWERSHELL_GATE.read_text(encoding="utf-8")

    assert "catch {\n            Remove-GateRules" in source
    assert "refusing to remove $name because its group" in source
    assert "PersistentStore" in source
    assert "ActiveStore" in source


def test_cmd_wrapper_exposes_only_install_verify_and_remove() -> None:
    source = CMD_GATE.read_text(encoding="utf-8")

    assert 'if /i "%~1"=="install" goto install' in source
    assert 'if /i "%~1"=="verify" goto verify' in source
    assert 'if /i "%~1"=="remove" goto remove_gate' in source
    assert "-Action Install" in source
    assert "-Action Verify" in source
    assert "-Action Remove" in source
    assert "ExecutionPolicy Bypass ^\n" not in source


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe batch dispatch contract")
def test_cmd_wrapper_dispatches_remove_from_lf_only_source(tmp_path: Path) -> None:
    source = CMD_GATE.read_text(encoding="utf-8")
    wrapper = tmp_path / "windows-firewall-gate.cmd"
    wrapper.write_text(source, encoding="utf-8", newline="\n")

    result = subprocess.run(  # noqa: S603
        ["cmd.exe", "/d", "/c", str(wrapper), "remove"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    diagnostic = result.stdout + result.stderr
    assert "batch label" not in diagnostic.lower()
    assert "バッチ ラベル" not in diagnostic


def test_windows_cli_runner_verifies_firewall_before_resolving_clis() -> None:
    source = CMD_E2E.read_text(encoding="utf-8")

    verify = source.index('windows-firewall-gate.cmd" verify')
    codex = source.index("SM_CODEX_CLI")
    claude = source.index("SM_CLAUDE_CLI")
    pytest = source.index("test_real_cli_e2e.py")
    assert verify < codex < claude < pytest
    assert "SM_REQUIRE_ALL_CLIS=1" in source
    assert "%LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin\\codex.exe" in source
    assert "%USERPROFILE%\\.local\\bin\\claude.exe" in source
