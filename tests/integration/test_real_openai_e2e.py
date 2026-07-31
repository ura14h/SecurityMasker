"""実Codex CLIからSecurityMasker経由でOpenAI実サーバへ接続するopt-in E2E。

このtestは課金と外部送信を伴うため、通常suiteでは実行しない。送信する検査値はこのtest専用の
合成名だけで、実在人物・実credentialをpromptへ含めない。Codexの既存認証は表示・複製せず、
command lineの一時overrideで永続configを変更しない。

    SM_RUN_OPENAI_E2E=1 .venv/bin/python -m pytest \
        tests/integration/test_real_openai_e2e.py -v
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import httpx
import pytest

from securitymasker.bootstrap import initialize_layout

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_OPENAI_E2E") != "1",
    reason="set SM_RUN_OPENAI_E2E=1 to contact the real OpenAI server",
)

REPO = Path(__file__).resolve().parents[2]
PERSON = "SYNTHETIC_PERSON_793421"
DICTIONARY = f"""\
version: 1
entities:
  - id: synthetic_person
    type: PERSON
    values: ["{PERSON}"]
    replacement_profile: prose_identifier
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


def _codex_app_server_command(
    codex: str,
    gateway_url: str,
    *,
    supports_websockets: bool,
) -> list[str]:
    provider = "model_providers.securitymasker"
    return [
        codex,
        "app-server",
        "--stdio",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "-c",
        'approval_policy="never"',
        "-c",
        "analytics.enabled=false",
        "-c",
        "check_for_update_on_startup=false",
        "-c",
        'web_search="disabled"',
        "-c",
        "agents.enabled=false",
        "-c",
        "mcp_servers={}",
        "-c",
        "plugins={}",
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
        f"{provider}.supports_websockets={str(supports_websockets).lower()}",
    ]


@dataclass
class _AppServer:
    process: subprocess.Popen[str]
    notifications: list[dict[str, Any]] = field(default_factory=list)
    request_id: int = 0
    expected_tool_calls: int = 0
    tool_calls: int = 0

    @classmethod
    def start(
        cls,
        codex: str,
        gateway_url: str,
        *,
        supports_websockets: bool,
    ) -> _AppServer:
        process = subprocess.Popen(  # noqa: S603
            _codex_app_server_command(
                codex,
                gateway_url,
                supports_websockets=supports_websockets,
            ),
            env=_codex_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        server = cls(process)
        server.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "securitymasker-e2e",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        server.notify("initialized", {})
        return server

    @property
    def stdin(self) -> IO[str]:
        assert self.process.stdin is not None
        return self.process.stdin

    @property
    def stdout(self) -> IO[str]:
        assert self.process.stdout is not None
        return self.process.stdout

    def _send(self, message: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.stdin.flush()

    def _read(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex app-server response timed out")
        readable, _, _ = select.select([self.stdout], [], [], remaining)
        if not readable:
            raise TimeoutError("Codex app-server response timed out")
        line = self.stdout.readline()
        if not line:
            raise RuntimeError(
                f"Codex app-server closed unexpectedly with {self.process.poll()}"
            )
        message = json.loads(line)
        if not isinstance(message, dict):
            raise RuntimeError("Codex app-server emitted a non-object message")
        return message

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        self.request_id += 1
        expected_id = self.request_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": expected_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout
        while True:
            message = self._read(deadline)
            if message.get("id") == expected_id:
                if "error" in message:
                    raise RuntimeError(
                        f"Codex app-server {method} failed: {message['error']}"
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Codex app-server {method} returned no object")
                return result
            if "method" in message:
                self.notifications.append(message)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if message.get("method") != "item/tool/call":
            return False
        request_id = message.get("id")
        params = message.get("params")
        if not isinstance(request_id, int) or not isinstance(params, dict):
            raise RuntimeError("Codex app-server emitted an invalid dynamic tool request")
        if params.get("tool") != "repeat_probe":
            raise RuntimeError(f"unexpected dynamic tool: {params.get('tool')}")
        self.tool_calls += 1
        if self.tool_calls > self.expected_tool_calls:
            raise RuntimeError("Codex called repeat_probe too many times")
        remaining = self.expected_tool_calls - self.tool_calls
        tool_result = {
            "step": self.tool_calls,
            "remaining": remaining,
            "done": remaining == 0,
            "person": PERSON,
        }
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": json.dumps(tool_result, ensure_ascii=False),
                        }
                    ],
                    "success": True,
                },
            }
        )
        return True

    def wait_turn_complete(
        self,
        *,
        thread_id: str,
        turn_id: str,
        timeout: float,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        system_error_seen = False
        while True:
            for message in self.notifications:
                params = message.get("params")
                if not isinstance(params, dict):
                    continue
                turn = params.get("turn")
                if (
                    isinstance(turn, dict)
                    and message.get("method") == "turn/completed"
                    and params.get("threadId") == thread_id
                ):
                    if turn.get("id") != turn_id:
                        raise RuntimeError("Codex completed an unexpected turn")
                    if turn.get("status") != "completed":
                        raise RuntimeError(
                            f"Codex turn ended with status={turn.get('status')}"
                        )
                    return turn
                if (
                    message.get("method") == "thread/status/changed"
                    and params.get("threadId") == thread_id
                ):
                    status = params.get("status")
                    if status == {"type": "idle"}:
                        return None
                    if status == {"type": "systemError"} and not system_error_seen:
                        system_error_seen = True
                        deadline = min(deadline, time.monotonic() + 5.0)
            try:
                message = self._read(deadline)
            except TimeoutError:
                if not system_error_seen:
                    raise
                diagnostics: list[dict[str, Any]] = []
                for notification in self.notifications:
                    notification_params = notification.get("params")
                    if not isinstance(notification_params, dict):
                        continue
                    item = notification_params.get("item")
                    turn = notification_params.get("turn")
                    error = notification_params.get("error")
                    if (
                        error is not None
                        or (isinstance(item, dict) and item.get("type") == "error")
                        or (
                            isinstance(turn, dict)
                            and turn.get("status") != "inProgress"
                        )
                    ):
                        diagnostics.append(
                            {
                                "method": notification.get("method"),
                                "error": error,
                                "item": item,
                                "turn": turn,
                            }
                        )
                raise RuntimeError(
                    "Codex thread entered systemError: "
                    + json.dumps(diagnostics, ensure_ascii=False)
                ) from None
            if "id" in message and "method" in message:
                if self._handle_server_request(message):
                    continue
                raise RuntimeError(
                    f"unexpected Codex app-server request: {message.get('method')}"
                )
            if "method" in message:
                self.notifications.append(message)

    def close(self) -> str:
        self.process.terminate()
        try:
            _, stderr = self.process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            _, stderr = self.process.communicate(timeout=5)
        return stderr


@dataclass(frozen=True)
class _RunResult:
    latency_ms: float
    response_count: int
    connection_count: int
    gateway_log: str


def _agent_messages(
    notifications: list[dict[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
) -> list[str]:
    messages: list[str] = []
    for notification in notifications:
        if notification.get("method") != "item/completed":
            continue
        params = notification.get("params")
        if not isinstance(params, dict):
            continue
        item = params.get("item")
        if (
            params.get("threadId") == thread_id
            and params.get("turnId") == turn_id
            and isinstance(item, dict)
            and item.get("type") == "agentMessage"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
    return messages


def _run_codex_tool_chain(
    codex: str,
    tmp_path: Path,
    *,
    supports_websockets: bool,
    tool_calls: int,
) -> _RunResult:
    mode = "websocket" if supports_websockets else "http"
    port = _port()
    layout = initialize_layout(
        tmp_path / f"product-{mode}",
        mode="chatgpt",
        port=port,
    )
    layout.config.write_text(
        layout.config.read_text(encoding="utf-8").replace(
            "  japanese_ner:\n    enabled: true",
            "  japanese_ner:\n    enabled: false",
        ).replace("level: INFO", "level: DEBUG"),
        encoding="utf-8",
    )
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
    server: _AppServer | None = None
    turn_timeout = float(os.environ.get("SM_OPENAI_E2E_TURN_TIMEOUT", "180"))
    try:
        gateway_url = f"http://127.0.0.1:{port}"
        _wait(f"{gateway_url}/ready")
        server = _AppServer.start(
            codex,
            gateway_url,
            supports_websockets=supports_websockets,
        )
        thread = server.request(
            "thread/start",
            {
                "approvalPolicy": "never",
                "cwd": str(tmp_path),
                "developerInstructions": (
                    "Use only repeat_probe and call it sequentially until done is true. "
                    "Never answer while done is false. When done is true, output only "
                    "the two bracketed values requested by the user on one line."
                ),
                "dynamicTools": [
                    {
                        "type": "function",
                        "name": "repeat_probe",
                        "description": (
                            "Sequential test probe. Call exactly once per response. "
                            "Call again when done is false and stop when done is true."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                ],
                "environments": [],
                "ephemeral": True,
                "model": os.environ.get("SM_OPENAI_E2E_MODEL"),
                "modelProvider": "securitymasker",
                "sandbox": "read-only",
            },
        )
        thread_id = thread["thread"]["id"]
        server.expected_tool_calls = tool_calls
        started = time.monotonic()
        response = server.request(
            "turn/start",
            {
                "effort": "low",
                "input": [
                    {
                        "type": "text",
                        "text": (
                            "Call repeat_probe sequentially until it returns done=true; "
                            "never call it in parallel. Then output the final person value "
                            "and DONE in brackets on one line, with no other text."
                        ),
                    }
                ],
                "threadId": thread_id,
            },
        )
        turn_id = response["turn"]["id"]
        completed = server.wait_turn_complete(
            thread_id=thread_id,
            turn_id=turn_id,
            timeout=turn_timeout,
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        progress: dict[str, Any] = {
            "transport": mode,
            "tool_calls": server.tool_calls,
            "wall_ms": round(latency_ms, 1),
        }
        if completed is not None and isinstance(completed.get("durationMs"), int):
            progress["codex_duration_ms"] = completed["durationMs"]
        print(json.dumps(progress, ensure_ascii=False, sort_keys=True), flush=True)
        output = "\n".join(
            _agent_messages(
                server.notifications,
                thread_id=thread_id,
                turn_id=turn_id,
            )
        )
        assert server.tool_calls == tool_calls, output
        assert PERSON in output
        assert "DONE" in output
        assert not re.search(r"SM_[A-Z]+_[0-9A-F]+", output)
        assert not re.search(r"sm-[a-z]+-[0-9a-f]+\\.example\\.invalid", output)
    finally:
        failed = sys.exc_info()[0] is not None
        app_stderr = server.close() if server is not None else ""
        gateway.terminate()
        try:
            _, gateway_stderr = gateway.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
            _, gateway_stderr = gateway.communicate(timeout=5)
        if failed:
            safe_log = "\n".join(
                line
                for line in gateway_stderr.splitlines()
                if any(
                    marker in line
                    for marker in (
                        "gateway_",
                        "request_",
                        "sm_block",
                        "sm_websocket_",
                        "stream_",
                        "blocked",
                    )
                )
            )
            print(safe_log, file=sys.stderr)
            app_errors = "\n".join(
                line
                for line in app_stderr.splitlines()
                if "ERROR" in line or "WARN" in line
            )
            print(app_errors, file=sys.stderr)
        if server is not None and server.process.returncode not in {0, -15}:
            pytest.fail(f"Codex app-server failed: {app_stderr[-2000:]}")

    masked_counts = [
        int(value)
        for value in re.findall(r"request_masked entity_count=(\d+)", gateway_stderr)
    ]
    response_count = gateway_stderr.count("sm_websocket_turn_completed")
    connection_count = gateway_stderr.count("sm_websocket_connected")
    assert sum(count >= 1 for count in masked_counts) >= tool_calls
    if supports_websockets:
        assert connection_count == 1
        assert response_count >= tool_calls + 1
    else:
        assert connection_count == 0
        assert response_count == 0
    return _RunResult(
        latency_ms=latency_ms,
        response_count=response_count,
        connection_count=connection_count,
        gateway_log=gateway_stderr,
    )


def _run_with_capacity_retry(
    codex: str,
    tmp_path: Path,
    *,
    supports_websockets: bool,
    tool_calls: int,
) -> _RunResult:
    mode = "websocket" if supports_websockets else "http"
    for attempt in (1, 2):
        try:
            return _run_codex_tool_chain(
                codex,
                tmp_path / f"{mode}-attempt-{attempt}",
                supports_websockets=supports_websockets,
                tool_calls=tool_calls,
            )
        except RuntimeError as exc:
            if "serverOverloaded" not in str(exc) or attempt == 2:
                raise
            print(
                json.dumps(
                    {
                        "transport": mode,
                        "retry": attempt,
                        "reason": "serverOverloaded",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    raise AssertionError("unreachable")


def test_real_codex_websocket_tool_chain_and_optional_http_comparison(
    tmp_path: Path,
) -> None:
    """1 turn内の反復Responsesを検証し、opt-in時はHTTPと速度を比較する。"""
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("required real CLI is not installed: codex")
    tool_calls = int(os.environ.get("SM_OPENAI_E2E_TOOL_CALLS", "8"))
    assert 4 <= tool_calls <= 20

    websocket_result = _run_with_capacity_retry(
        codex,
        tmp_path,
        supports_websockets=True,
        tool_calls=tool_calls,
    )
    if os.environ.get("SM_OPENAI_E2E_COMPARE_HTTP") != "1":
        evidence = {
            "tool_calls_per_turn": tool_calls,
            "websocket_ms": round(websocket_result.latency_ms, 1),
            "websocket_connections": websocket_result.connection_count,
            "websocket_responses": websocket_result.response_count,
        }
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        return

    http_result = _run_with_capacity_retry(
        codex,
        tmp_path,
        supports_websockets=False,
        tool_calls=tool_calls,
    )
    speedup = (
        (http_result.latency_ms - websocket_result.latency_ms)
        / http_result.latency_ms
        * 100.0
    )
    evidence = {
        "tool_calls_per_turn": tool_calls,
        "websocket_ms": round(websocket_result.latency_ms, 1),
        "http_ms": round(http_result.latency_ms, 1),
        "websocket_connections": websocket_result.connection_count,
        "websocket_responses": websocket_result.response_count,
        "speedup_percent": round(speedup, 1),
    }
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
