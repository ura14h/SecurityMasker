"""実provider E2EのGateway選択を外部通信なしで検証する。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.integration.real_e2e_gateway import BINARY_ENV, REPO, gateway_command
from tests.integration.test_real_openai_e2e import _AppServer, _codex_environment


def test_gateway_command_uses_source_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(BINARY_ENV, raising=False)
    config = tmp_path / "securitymasker.config"

    assert gateway_command(config) == [
        sys.executable,
        str(REPO / "securitymasker.py"),
        "gateway",
        "--config",
        str(config),
    ]


def test_gateway_command_uses_explicit_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "securitymasker-lite.exe"
    binary.touch()
    config = tmp_path / "securitymasker.config"
    monkeypatch.setenv(BINARY_ENV, str(binary))

    assert gateway_command(config) == [
        str(binary.resolve()),
        "gateway",
        "--config",
        str(config),
    ]


def test_gateway_command_rejects_missing_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.exe"
    monkeypatch.setenv(BINARY_ENV, str(missing))

    with pytest.raises(RuntimeError, match=f"{BINARY_ENV} is not an executable file"):
        gateway_command(tmp_path / "securitymasker.config")


def test_app_server_reads_windows_pipe_and_stops_its_own_process() -> None:
    message = {"jsonrpc": "2.0", "id": 1, "result": {"ready": True}}
    script = (
        "import json,time; "
        f"print(json.dumps({message!r}), flush=True); "
        "time.sleep(60)"
    )
    stderr_file = tempfile.TemporaryFile(  # noqa: SIM115 - server.closeで閉じる
        mode="w+", encoding="utf-8"
    )
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        text=True,
    )
    server = _AppServer(process, stderr_file)
    server._start_reader()
    try:
        assert server._read(time.monotonic() + 5) == message
    finally:
        server.close()
    assert process.poll() is not None
    assert server.stopped_by_test is True


def test_codex_environment_keeps_windows_runtime_without_unrelated_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SM_SYNTHETIC_UNRELATED", "must-not-pass")
    if sys.platform == "win32":
        for name in (
            "APPDATA",
            "COMSPEC",
            "LOCALAPPDATA",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        ):
            monkeypatch.setenv(name, f"synthetic-{name.lower()}")

    environment = _codex_environment()

    assert "SM_SYNTHETIC_UNRELATED" not in environment
    if sys.platform == "win32":
        for name in (
            "APPDATA",
            "COMSPEC",
            "LOCALAPPDATA",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        ):
            assert environment[name] == f"synthetic-{name.lower()}"
