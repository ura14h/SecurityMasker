"""Windows test user lifecycleのfail-closedな管理者workflowを検査する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POWERSHELL = REPO / "devtools" / "windows_test_user.ps1"
CMD = REPO / "scripts" / "windows-test-user.cmd"
RUNAS_CMD = REPO / "scripts" / "windows-source-gate-runas.cmd"
SESSION_CMD = REPO / "devtools" / "windows_source_gate_session.cmd"
OWNER_CMD = REPO / "scripts" / "windows-owner-gate.cmd"
OWNER_POWERSHELL = REPO / "devtools" / "windows_owner_gate.ps1"
OWNER_PROBE = REPO / "devtools" / "windows_owner_probe.py"


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
    runas = RUNAS_CMD.read_text(encoding="utf-8")
    session = SESSION_CMD.read_text(encoding="utf-8")
    owner_cmd = OWNER_CMD.read_text(encoding="utf-8")
    owner_ps = OWNER_POWERSHELL.read_text(encoding="utf-8")
    owner_probe = OWNER_PROBE.read_text(encoding="utf-8")

    assert '"setup" goto setup' in source
    assert '"remove" goto remove_tester' in source
    assert '"verify-absent" goto verify_absent' in source
    assert "-Action Setup" in source
    assert "-Action Remove" in source
    assert "-Action VerifyAbsent" in source
    assert "ExecutionPolicy Bypass ^\n" not in source
    assert "runas.exe /profile" in runas
    assert "/savecred" not in runas.lower()
    assert "/netonly" not in runas.lower()
    assert "SecurityMaskerTester" in runas
    assert "windows_source_gate_session.cmd" in runas
    assert 'set "EXPECTED_USER=SecurityMaskerTester"' in session
    assert "whoami.exe" in session
    assert "InstallAllUsers=0" in session
    assert "sys.version_info[:2] == (3, 12)" in session
    assert "Get-FileHash -Algorithm SHA256" in session
    assert 'if exist "%SOURCE_ROOT%\\"' in session
    assert 'set "SECURITYMASKER_PYTHON=%PYTHON%"' in session
    assert "windows-source-gate.cmd run" in session
    assert "SECURITYMASKER_PYTHON" in owner_cmd
    assert "pythoncore-*-64\\python.exe" in owner_cmd
    assert "-Action Setup" in owner_cmd
    assert "-Action Remove" in owner_cmd
    assert "Windows owner gate must run from an elevated" in owner_ps
    assert 'Join-Path $env:ProgramData "SecurityMaskerOwnerGate"' in owner_ps
    assert "SetOwner($administrators)" in owner_ps
    assert "unexpected entries" in owner_ps
    assert "Remove-Item -Recurse" not in owner_ps
    assert "require_private_dacl" in owner_probe
    assert "managed path must be owned by the current user" in owner_probe


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser contract")
def test_powershell_lifecycle_script_parses() -> None:
    for script in (POWERSHELL, OWNER_POWERSHELL):
        escaped = str(script).replace("'", "''")
        command = f"[void][ScriptBlock]::Create([IO.File]::ReadAllText('{escaped}'))"
        result = subprocess.run(  # noqa: S603
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
