"""Windows standard-user source archive gateの契約を検査する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "devtools/windows_source_gate.ps1"
RUNNER = ROOT / "scripts/windows-source-gate.cmd"


def test_preflight_requires_fixed_non_admin_user_and_clean_archive() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")

    assert '$ExpectedUser = "SecurityMaskerTester"' in source
    assert "fixed SecurityMaskerTester user" in source
    assert "must run as a standard user" in source
    assert '@(".git", ".venv")' in source
    assert '"securitymasker.config"' in source
    assert '"securitymasker.state"' in source
    assert '"PYTHONPATH", "VIRTUAL_ENV", "SECURITYMASKER_CONFIG"' in source


def test_preflight_requires_local_fixed_ntfs_without_reparse_points() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")

    assert 'Get-Volume -DriveLetter' in source
    assert '$volume.DriveType -ne "Fixed"' in source
    assert '$volume.FileSystem -ne "NTFS"' in source
    assert "ReparsePoint" in source
    assert "local drive-letter path" in source


def test_runner_checks_fresh_archive_before_setup_and_runs_both_modes() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    preflight = source.index('powershell.exe -NoLogo -NoProfile -NonInteractive')
    setup = source.index('call "%SCRIPT_DIRECTORY%test-setup.cmd"')
    release = source.index('call "%SCRIPT_DIRECTORY%release-check.cmd"')
    init = source.index("init --mode chatgpt --port 45677")
    assert preflight < setup < release < init
    assert "init --mode chatgpt --port 45677" in source
    assert 'init --directory "%PROJECT_DIRECTORY%\\securitymasker-claude"' in source
    assert "--mode claude --port 45678" in source
    assert 'CHATGPT_CONFIG=%PROJECT_DIRECTORY%\\securitymasker.config' in source
    assert 'CLAUDE_CONFIG=%PROJECT_DIRECTORY%\\securitymasker-claude' in source
    assert source.count(" doctor --config ") == 2
    assert source.count(" preview ") == 2
    assert source.count(" client-config ") == 2
    assert "git clone" not in source


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser contract")
def test_powershell_source_gate_script_parses() -> None:
    escaped = str(PREFLIGHT).replace("'", "''")
    command = f"[void][ScriptBlock]::Create([IO.File]::ReadAllText('{escaped}'))"
    result = subprocess.run(  # noqa: S603
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
