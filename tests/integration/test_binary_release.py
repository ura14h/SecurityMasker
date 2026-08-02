"""one-file実行ファイルを通常のPython test harnessから検証する。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from securitymasker.bootstrap import initialize_layout

BINARY_ENV = "SM_BINARY"
PROFILE_ENV = "SM_BINARY_PROFILE"
MODEL_HOME_ENV = "SM_BINARY_TEST_HF_HOME"
PERSON = "山田太郎"
PROMPT = f"担当者は{PERSON}です。"
REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get(BINARY_ENV),
    reason=f"set {BINARY_ENV} to the one-file artifact",
)


def _binary() -> Path:
    binary = Path(os.environ[BINARY_ENV]).resolve()
    assert binary.is_file()
    return binary


def _profile() -> str:
    profile = os.environ.get(PROFILE_ENV)
    assert profile in ("lite", "full")
    return profile


def _binary_temp(tmp_path: Path) -> Path:
    """one-file展開先を返す。WindowsではMAX_PATHを避ける短い専用pathを使う。"""
    configured = os.environ.get("SM_BINARY_WINDOWS_TEMP_ROOT")
    temp = Path(configured) if os.name == "nt" and configured else tmp_path / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    return temp


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(url: str, *, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200 and response.json().get("ready") is True:
                return
        except Exception as exc:  # noqa: BLE001 - 最後の診断だけ保持する
            last = exc
        time.sleep(0.5)
    raise AssertionError(f"binary gateway was not ready: {type(last).__name__}")


def _isolated_environment(
    home: Path, temp: Path, *, with_model: bool = True
) -> dict[str, str]:
    if os.name == "nt":
        local = home / "AppData" / "Local"
        roaming = home / "AppData" / "Roaming"
        local.mkdir(parents=True, exist_ok=True)
        roaming.mkdir(parents=True, exist_ok=True)
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        environment = {
            "APPDATA": str(roaming),
            "HOME": str(home),
            "LOCALAPPDATA": str(local),
            "PATH": str(Path(system_root) / "System32"),
            "SYSTEMROOT": system_root,
            "TEMP": str(temp),
            "TMP": str(temp),
            "USERPROFILE": str(home),
            "WINDIR": system_root,
        }
    else:
        environment = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(temp),
        }
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    if _profile() == "lite" and with_model:
        model_home = os.environ.get(MODEL_HOME_ENV)
        assert model_home, f"{MODEL_HOME_ENV} is required for the lite profile gate"
        environment["HF_HOME"] = model_home
    return environment


def test_binary_version_identifies_profile(tmp_path: Path) -> None:
    binary = _binary()
    home = tmp_path / "home"
    temp = _binary_temp(tmp_path)
    home.mkdir()

    environment = _isolated_environment(home, temp)
    if os.name == "nt":
        assert not (Path(environment["PATH"]) / "python.exe").exists()
    completed = subprocess.run(
        [binary, "--version"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"(binary {_profile()})" in completed.stdout
    assert "unknown-profile" not in completed.stdout
    assert not list(temp.glob("_MEI*")), "version exit left an extraction directory"


def test_binary_without_command_shows_help_and_exits(tmp_path: Path) -> None:
    binary = _binary()
    home = tmp_path / "home"
    temp = _binary_temp(tmp_path)
    home.mkdir()

    completed = subprocess.run(
        [binary],
        env=_isolated_environment(home, temp),
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr
    assert "SecurityMasker CLI" in completed.stdout
    assert "gateway_started" not in completed.stderr
    assert not list(temp.glob("_MEI*")), "help exit left a one-file extraction directory"


def test_lite_binary_without_model_fails_closed_and_guides_model_load(
    tmp_path: Path,
) -> None:
    if _profile() != "lite":
        pytest.skip("lite profile only")
    binary = _binary()
    home = tmp_path / "home"
    temp = _binary_temp(tmp_path)
    product = tmp_path / "product"
    empty_model_home = tmp_path / "empty-model-home"
    home.mkdir()
    empty_model_home.mkdir()
    environment = _isolated_environment(home, temp, with_model=False)
    environment["HF_HOME"] = str(empty_model_home)

    initialized = subprocess.run(
        [binary, "init", "--directory", product],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert initialized.returncode == 0, initialized.stderr
    completed = subprocess.run(
        [binary, "preview", PROMPT, "--config", product / "securitymasker.config"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "model-load" in combined
    assert PERSON not in combined
    assert not list(temp.glob("_MEI*")), "failed preview left an extraction directory"


def test_binary_init_validate_preview_and_temp_cleanup(tmp_path: Path) -> None:
    binary = _binary()
    home = tmp_path / "home"
    temp = _binary_temp(tmp_path)
    product = tmp_path / "product"
    home.mkdir()
    environment = _isolated_environment(home, temp)

    initialized = subprocess.run(
        [
            binary,
            "init",
            "--directory",
            product,
            "--mode",
            "chatgpt",
            "--port",
            str(_port()),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert initialized.returncode == 0, initialized.stderr
    config = product / "securitymasker.config"
    assert config.is_file()

    validated = subprocess.run(
        [binary, "config-check", "--config", config],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert validated.returncode == 0, validated.stderr
    assert "OK: config valid" in validated.stdout

    previewed = subprocess.run(
        [binary, "preview", PROMPT, "--config", config],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = previewed.stdout + previewed.stderr
    assert previewed.returncode == 0, combined
    assert PERSON not in combined
    assert "SM_PERSON_" in previewed.stdout
    assert "PERSON: 1" in previewed.stdout
    assert not list(temp.glob("_MEI*")), "normal one-file exit left an extraction directory"


@pytest.mark.parametrize(
    ("mode", "path", "payload", "response_text"),
    [
        (
            "chatgpt",
            "/responses",
            {"model": "gpt-test", "input": PROMPT},
            lambda body: body["output"][0]["content"][0]["text"],
        ),
        (
            "claude",
            "/v1/messages",
            {
                "model": "claude-test",
                "max_tokens": 64,
                "messages": [{"role": "user", "content": PROMPT}],
            },
            lambda body: body["content"][0]["text"],
        ),
    ],
)
def test_binary_gateway_masks_restores_persists_and_stops_on_signal(
    tmp_path: Path,
    mode: str,
    path: str,
    payload: dict[str, object],
    response_text: Callable[[dict[str, Any]], str],
) -> None:
    binary = _binary()
    mock_port = _port()
    gateway_port = _port()
    layout = initialize_layout(tmp_path / "product", mode=mode, port=gateway_port)
    record = tmp_path / "record.jsonl"
    home = tmp_path / "home"
    temp = _binary_temp(tmp_path)
    home.mkdir()
    base_environment = _isolated_environment(home, temp)
    mock_environment = {
        **os.environ,
        "SM_MOCK_RECORD": str(record),
    }
    gateway_environment = {
        **base_environment,
        "SECURITYMASKER_OPENAI_UPSTREAM": f"http://127.0.0.1:{mock_port}",
        "SECURITYMASKER_ANTHROPIC_UPSTREAM": f"http://127.0.0.1:{mock_port}",
    }

    mock = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "devtools.mock_upstream:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(mock_port),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=mock_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    gateway = subprocess.Popen(  # noqa: S603
        [binary, "gateway", "--config", layout.config],
        env=gateway_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    try:
        _wait_ready(f"http://127.0.0.1:{gateway_port}/ready")
        headers = {
            "content-type": "application/json",
            "x-securitymasker-session-id": f"binary-{mode}",
        }
        if mode == "claude":
            headers["anthropic-version"] = "2023-06-01"
        response = httpx.post(
            f"http://127.0.0.1:{gateway_port}{path}",
            headers=headers,
            json=payload,
            timeout=30,
        )
        assert response.status_code == 200, response.text
        restored = response_text(response.json())
        assert PERSON in restored
        upstream = record.read_text(encoding="utf-8")
        assert PERSON not in upstream
        assert "SM_PERSON_" in upstream
        assert (layout.state_directory / "securitymasker.db").is_file()
    finally:
        if os.name == "nt":
            gateway.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            gateway.terminate()
        gateway.wait(timeout=30)
        mock.terminate()
        mock.wait(timeout=15)

    assert gateway.returncode is not None
    assert not list(temp.glob("_MEI*")), "SIGTERM left an extraction directory"
