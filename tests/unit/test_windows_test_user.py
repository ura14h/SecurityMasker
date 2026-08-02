"""Windows test user lifecycleのfail-closedな管理者workflowを検査する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POWERSHELL = REPO / "devtools" / "windows_test_user.ps1"
CMD = REPO / "scripts" / "windows-test-user.cmd"


def test_lifecycle_uses_only_fixed_test_user_name() -> None:
    source = POWERSHELL.read_text(encoding="utf-8")

    assert '$User = "SecurityMaskerTester"' in source
    assert "[string]$User" not in source
    assert 'test user lifecycle must run from an elevated administrator shell' in source


def test_setup_prompts_for_password_without_command_line_value() -> None:
    source = POWERSHELL.read_text(encoding="utf-8")

    assert 'net.exe" user $User "*" /add' in source
    description = "SecurityMasker isolated CLI test user"
    assert f'$Description = "{description}"' in source
    assert "Set-LocalUser -Name $User -Description $Description" in source
    assert len(description) <= 48
    assert "New-LocalUser" not in source
    assert "ConvertFrom-SecureString" not in source
    assert "SecurityMaskerTester" in source


def test_remove_checks_runtime_and_uses_windows_profile_api() -> None:
    source = POWERSHELL.read_text(encoding="utf-8")

    assert "Get-Process -IncludeUserName" in source
    assert "Get-CimInstance Win32_Service" in source
    assert "Get-ScheduledTask" in source
    assert 'Registry::HKEY_USERS\\$Sid' in source
    assert "Remove-CimInstance -InputObject $profile" in source
    assert "Remove-LocalUser -Name $User" in source
    assert '$sid = [string]$profiles[0].SID' in source
    assert "Remove-Item" not in source
    assert "reparse-point test profile" in source
    assert "outside the local Users directory" in source


def test_remove_cleans_only_exact_securitymasker_firewall_rules() -> None:
    source = POWERSHELL.read_text(encoding="utf-8")

    assert 'SecurityMaskerCliEgressGate-v4' in source
    assert 'SecurityMaskerCliEgressGate-v6' in source
    assert 'SecurityMasker Windows CLI Egress Gate' in source
    assert "outside the SecurityMasker group" in source
    assert "PersistentStore" in source
    assert "ActiveStore" in source


def test_cmd_exposes_only_tester_lifecycle_without_caret_continuations() -> None:
    source = CMD.read_text(encoding="utf-8")

    assert '"setup" goto setup' in source
    assert '"remove" goto remove_tester' in source
    assert '"verify-absent" goto verify_absent' in source
    assert "-Action Setup" in source
    assert "-Action Remove" in source
    assert "-Action VerifyAbsent" in source
    assert "ExecutionPolicy Bypass ^\n" not in source


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser contract")
def test_powershell_lifecycle_script_parses() -> None:
    escaped = str(POWERSHELL).replace("'", "''")
    command = f"[void][ScriptBlock]::Create([IO.File]::ReadAllText('{escaped}'))"
    result = subprocess.run(  # noqa: S603
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
