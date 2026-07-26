"""Phase 8のone-file成果物を通常のPython test harnessから検証する。"""

from __future__ import annotations

import os
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


def _isolated_environment(home: Path, temp: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(temp),
    }


def test_binary_init_validate_preview_and_temp_cleanup(tmp_path: Path) -> None:
    binary = _binary()
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    product = tmp_path / "product"
    home.mkdir()
    temp.mkdir()
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
        [binary, "config", "validate", "--config", config],
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
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
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
    gateway = subprocess.Popen(  # noqa: S603
        [binary, "gateway", "--config", layout.config],
        env=gateway_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        gateway.terminate()
        gateway.wait(timeout=30)
        mock.terminate()
        mock.wait(timeout=15)

    assert gateway.returncode is not None
    assert not list(temp.glob("_MEI*")), "SIGTERM left an extraction directory"
