"""実Codex CLIからSecurityMasker経由でOpenAI実サーバへ接続するopt-in E2E。

このtestは課金と外部送信を伴うため、通常suiteでは実行しない。送信する値は予約済み
``.example`` domainとこのtest専用の合成名だけで、実在人物・実credentialをpromptへ
含めない。Codexの既存認証は表示・複製せず、``--ignore-user-config``と一時overrideで
永続configを変更しない。

    SM_RUN_OPENAI_E2E=1 .venv/bin/python -m pytest \
        tests/integration/test_real_openai_e2e.py -v
"""

from __future__ import annotations

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

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_OPENAI_E2E") != "1",
    reason="set SM_RUN_OPENAI_E2E=1 to contact the real OpenAI server",
)

REPO = Path(__file__).resolve().parents[2]
PERSON = "試験姓七九三試験名四二一"
HOST = "codex-ws-e2e-793421.internal.example"
DICTIONARY = f"""\
version: 1
entities:
  - id: synthetic_person
    type: PERSON
    values: ["{PERSON}"]
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100
  - id: synthetic_host
    type: HOSTNAME
    values: ["{HOST}"]
    replacement_profile: hostname
    restore_policy: literal
    priority: 100
patterns: []
"""


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return
        except Exception:  # noqa: BLE001 - 起動待ちでは未接続が通常
            pass
        time.sleep(0.4)
    raise RuntimeError(f"not ready: {url}")


def _codex_environment() -> dict[str, str]:
    """認証保存先だけを維持し、promptと無関係な環境変数を子へ渡さない。"""
    keep = (
        "PATH",
        "CODEX_HOME",
        "LANG",
        "LC_ALL",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
    )
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment["HOME"] = str(Path.home())
    return environment


def _codex_command(codex: str, gateway_url: str, output: Path) -> list[str]:
    provider = "model_providers.securitymasker"
    command = [
        codex,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "--output-last-message",
        str(output),
        "-c",
        'approval_policy="never"',
        "-c",
        'model_provider="securitymasker"',
        "-c",
        f'{provider}.name="SecurityMasker Gateway"',
        "-c",
        f'{provider}.base_url="{gateway_url}"',
        "-c",
        f'{provider}.wire_api="responses"',
        "-c",
        f"{provider}.requires_openai_auth=true",
        "-c",
        f"{provider}.supports_websockets=true",
    ]
    model = os.environ.get("SM_OPENAI_E2E_MODEL")
    if model:
        command.extend(("--model", model))
    command.append(
        "ツールや外部通信を使わず、次の角括弧内だけを順番どおり一行で返してください。"
        f"内容を説明・翻訳・修正しないでください。【{PERSON}】【{HOST}】"
    )
    return command


def test_real_codex_websocket_reaches_openai_and_restores(tmp_path: Path) -> None:
    """実接続がWebSocketで成立し、mask済み応答を利用者向けに復元する。"""
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("required real CLI is not installed: codex")

    port = _port()
    layout = initialize_layout(tmp_path / "product", mode="chatgpt", port=port)
    layout.dictionary.write_text(DICTIONARY, encoding="utf-8")
    layout.dictionary.chmod(0o600)
    gateway = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(REPO / "securitymasker.py"),
            "gateway",
            "--config",
            str(layout.config),
        ],
        cwd=REPO,
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    gateway_stderr = ""
    try:
        gateway_url = f"http://127.0.0.1:{port}"
        _wait(f"{gateway_url}/ready")
        output = tmp_path / "last-message.txt"
        result = subprocess.run(  # noqa: S603
            _codex_command(codex, gateway_url, output),
            cwd=tmp_path,
            env=_codex_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"codex failed: {result.stderr[-2000:]}"
        final = output.read_text(encoding="utf-8")
        assert PERSON in final
        assert HOST in final
        assert not re.search(r"SM_[A-Z]+_[0-9A-F]+", final)
        assert not re.search(r"sm-[a-z]+-[0-9a-f]+\\.example\\.invalid", final)
    finally:
        gateway.terminate()
        try:
            _, gateway_stderr = gateway.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
            _, gateway_stderr = gateway.communicate(timeout=5)

    assert "sm_websocket_connected" in gateway_stderr
    assert "request_masked" in gateway_stderr
    masked_counts = [
        int(value)
        for value in re.findall(r"request_masked entity_count=(\d+)", gateway_stderr)
    ]
    assert masked_counts and max(masked_counts) >= 2
