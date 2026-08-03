"""実Claude Code CLIからSecurityMasker経由でAnthropic実サーバへ接続するE2E。

このtestは課金と外部送信を伴うため通常suiteでは実行しない。送信する検査値は専用の
合成名だけで、実在人物、repository内容、実credentialをpromptへ含めない。Claude Codeの
既存認証は表示・複製せず、一時config directoryとprocess環境だけでGatewayへ向ける。

    SM_RUN_ANTHROPIC_E2E=1 .venv/bin/python -m pytest \
        tests/integration/test_real_anthropic_e2e.py -v

同じfileはClaude Codeが起動する最小stdio MCP probeも提供する。probeは固定合成値だけを
tool resultへ返し、実Messages tool loopの各requestで再マスクされることを検証可能にする。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO

import httpx
import pytest

from securitymasker.bootstrap import initialize_layout
from tests.integration.real_e2e_gateway import start_gateway

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_ANTHROPIC_E2E") != "1",
    reason="set SM_RUN_ANTHROPIC_E2E=1 to contact the real Anthropic server",
)

REPO = Path(__file__).resolve().parents[2]
PERSON = "SYNTHETIC_PERSON_684209"
DEFAULT_TOOL_CALLS = 4
DEFAULT_MODEL = "haiku"
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


def _claude_environment(
    gateway_url: str, config_dir: Path | None = None
) -> dict[str, str]:
    """認証に必要な最小環境だけを維持し、Claudeを一時Gatewayへ向ける。"""
    keep = [
        "PATH",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        # API key/tokenを明示した実行も許すが、値は表示・file保存しない。
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ]
    if os.name == "nt":
        keep.extend(
            (
                "APPDATA",
                "COMSPEC",
                "LOCALAPPDATA",
                "PATHEXT",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "WINDIR",
            )
        )
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment.update(
        {
            "HOME": str(Path.home()),
            "ANTHROPIC_BASE_URL": gateway_url,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_BUG_COMMAND": "1",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0",
            "CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY": "1",
            "DO_NOT_TRACK": "1",
        }
    )
    if config_dir is not None:
        environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return environment


def _mcp_response(message: dict[str, Any], call_number: int) -> dict[str, Any] | None:
    """stdio MCP probeの一requestを処理する。notificationには応答しない。"""
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "securitymasker-e2e-probe", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "repeat_probe",
                    "description": (
                        "Sequential E2E probe. Call once, inspect done, and repeat "
                        "until done is true."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
            ]
        }
    elif method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or params.get("name") != "repeat_probe":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "unknown tool"},
            }
        expected = int(os.environ.get("SM_CLAUDE_E2E_TOOL_CALLS", str(DEFAULT_TOOL_CALLS)))
        remaining = max(expected - call_number, 0)
        content = json.dumps(
            {
                "step": call_number,
                "remaining": remaining,
                "done": remaining == 0,
                "person": PERSON,
            },
            ensure_ascii=False,
        )
        result = {"content": [{"type": "text", "text": content}], "isError": False}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _run_mcp_probe(stdin: TextIO, stdout: TextIO) -> int:
    """改行区切りJSONのstdio MCP serverを実行する。"""
    call_number = 0
    record_path = os.environ.get("SM_CLAUDE_E2E_MCP_RECORD")
    for line in stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("method") == "tools/call":
            call_number += 1
            if record_path:
                with Path(record_path).open("a", encoding="utf-8") as record:
                    record.write(json.dumps({"call": call_number}) + "\n")
        response = _mcp_response(message, call_number)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def _mcp_config(path: Path, record: Path, tool_calls: int) -> None:
    config = {
        "mcpServers": {
            "probe": {
                "type": "stdio",
                "alwaysLoad": True,
                "command": sys.executable,
                "args": [str(Path(__file__).resolve()), "--mcp-probe"],
                "env": {
                    "SM_CLAUDE_E2E_TOOL_CALLS": str(tool_calls),
                    "SM_CLAUDE_E2E_MCP_RECORD": str(record),
                },
            }
        }
    }
    path.write_text(json.dumps(config), encoding="utf-8")


def _claude_command(claude: str, mcp_config: Path, tool_calls: int) -> list[str]:
    prompt = (
        f"Call repeat_probe sequentially exactly {tool_calls} times, never in parallel. "
        "Do not finish while done is false. When done is true, output only the final "
        "person value and DONE in brackets on one line, with no other text."
    )
    command = [
        claude,
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--no-chrome",
        "--strict-mcp-config",
        f"--mcp-config={mcp_config}",
        "--tools",
        "",
        "--allowedTools",
        "mcp__probe__repeat_probe",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--effort",
        "low",
        "--max-budget-usd",
        os.environ.get("SM_ANTHROPIC_E2E_MAX_BUDGET_USD", "1.00"),
        "--system-prompt",
        (
            "You are running a deterministic compatibility test. Use only the "
            "repeat_probe tool and follow the user instruction exactly."
        ),
    ]
    model = os.environ.get("SM_ANTHROPIC_E2E_MODEL", DEFAULT_MODEL)
    command.extend(["--model", model])
    command.append(prompt)
    return command


def _claude_echo_command(claude: str) -> list[str]:
    command = [
        claude,
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--no-chrome",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--effort",
        "low",
        "--max-budget-usd",
        os.environ.get("SM_ANTHROPIC_E2E_MAX_BUDGET_USD", "1.00"),
        "--system-prompt",
        "Output only the exact identifier requested by the user, with no other text.",
    ]
    model = os.environ.get("SM_ANTHROPIC_E2E_MODEL", DEFAULT_MODEL)
    command.extend(["--model", model])
    command.append(f"Output this exact identifier: {PERSON}")
    return command


def _parse_result(stdout: str) -> dict[str, Any]:
    """Claude CodeのJSON resultを返し、付随出力を成功扱いにしない。"""
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("Claude Code emitted a non-object JSON result")
    return payload


def _tool_call_count(record: Path) -> int:
    if not record.exists():
        return 0
    return len(record.read_text(encoding="utf-8").splitlines())


def _completed_stream_statuses(gateway_log: str) -> list[int]:
    """loggerのfield順へ依存せず完了streamのHTTP statusを抽出する。"""
    return [
        int(value)
        for value in re.findall(
            r"sm_upstream_stream_completed[^\n]*status_code=(\d+)", gateway_log
        )
    ]


def _run_claude(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Claudeとstdio MCP子processを同じprocess groupとして期限内に回収する。"""
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=os.name != "nt",
        creationflags=creation_flags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(f"Claude Code timed out after {timeout:.0f}s") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def test_real_claude_single_turn_through_anthropic(tmp_path: Path) -> None:
    """実Claude Codeの単一turnで上流maskと最終表示の復元を確認する。"""
    claude = shutil.which("claude")
    if claude is None:
        pytest.fail("required real CLI is not installed: claude")

    port = _port()
    layout = initialize_layout(tmp_path / "product", mode="claude", port=port)
    layout.config.write_text(
        layout.config.read_text(encoding="utf-8")
        .replace(
            "  japanese_ner:\n    enabled: true",
            "  japanese_ner:\n    enabled: false",
        )
        .replace("level: INFO", "level: DEBUG"),
        encoding="utf-8",
    )
    layout.dictionary.write_text(DICTIONARY, encoding="utf-8")
    layout.dictionary.chmod(0o600)

    gateway = start_gateway(
        layout.config,
        environment={
            **os.environ,
            "SECURITYMASKER_ANTHROPIC_UPSTREAM": "https://api.anthropic.com",
        },
    )
    gateway_stderr = ""
    try:
        gateway_url = f"http://127.0.0.1:{port}"
        _wait(f"{gateway_url}/ready")
        workdir = tmp_path / "empty-workdir"
        workdir.mkdir()
        started = time.monotonic()
        result = _run_claude(
            _claude_echo_command(claude),
            cwd=workdir,
            environment=_claude_environment(gateway_url),
            timeout=float(os.environ.get("SM_ANTHROPIC_E2E_TURN_TIMEOUT", "300")),
        )
        wall_ms = (time.monotonic() - started) * 1000.0
        payload = _parse_result(result.stdout)
        output = payload.get("result")
        assert result.returncode == 0, payload.get("subtype")
        assert payload.get("is_error") is False, payload.get("subtype")
        assert isinstance(output, str)
        assert output.strip() == PERSON
        assert not re.search(r"SM_[A-Z]+_[0-9A-F]+", output)
    finally:
        gateway_stderr = gateway.stop()

    masked_counts = [
        int(value)
        for value in re.findall(r"request_masked entity_count=(\d+)", gateway_stderr)
    ]
    completed = _completed_stream_statuses(gateway_stderr)
    assert any(count >= 1 for count in masked_counts)
    assert 200 in completed
    assert PERSON not in gateway_stderr
    print(
        json.dumps(
            {
                "completed_streams": completed.count(200),
                "turns": payload.get("num_turns"),
                "wall_ms": round(wall_ms, 1),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


@pytest.mark.skipif(
    os.environ.get("SM_RUN_ANTHROPIC_MCP_E2E") != "1",
    reason="set SM_RUN_ANTHROPIC_MCP_E2E=1 to run the extended MCP chain",
)
def test_real_claude_tool_chain_through_anthropic(tmp_path: Path) -> None:
    """実Messages tool loopの全requestでmaskし、最終表示でだけ復元する。"""
    claude = shutil.which("claude")
    if claude is None:
        pytest.fail("required real CLI is not installed: claude")
    tool_calls = int(os.environ.get("SM_ANTHROPIC_E2E_TOOL_CALLS", str(DEFAULT_TOOL_CALLS)))
    assert 2 <= tool_calls <= 12

    port = _port()
    layout = initialize_layout(tmp_path / "product", mode="claude", port=port)
    layout.config.write_text(
        layout.config.read_text(encoding="utf-8")
        .replace(
            "  japanese_ner:\n    enabled: true",
            "  japanese_ner:\n    enabled: false",
        )
        .replace("level: INFO", "level: DEBUG"),
        encoding="utf-8",
    )
    layout.dictionary.write_text(DICTIONARY, encoding="utf-8")
    layout.dictionary.chmod(0o600)

    gateway = start_gateway(
        layout.config,
        environment={
            **os.environ,
            "SECURITYMASKER_ANTHROPIC_UPSTREAM": "https://api.anthropic.com",
        },
    )
    gateway_stderr = ""
    try:
        gateway_url = f"http://127.0.0.1:{port}"
        _wait(f"{gateway_url}/ready")
        workdir = tmp_path / "empty-workdir"
        workdir.mkdir()
        mcp_record = tmp_path / "mcp-calls.jsonl"
        mcp_config = tmp_path / "mcp.json"
        _mcp_config(mcp_config, mcp_record, tool_calls)

        started = time.monotonic()
        result = _run_claude(
            _claude_command(claude, mcp_config, tool_calls),
            cwd=workdir,
            environment=_claude_environment(gateway_url),
            timeout=float(os.environ.get("SM_ANTHROPIC_E2E_TURN_TIMEOUT", "300")),
        )
        wall_ms = (time.monotonic() - started) * 1000.0
        payload = _parse_result(result.stdout)
        if result.returncode != 0:
            diagnostics = {
                key: payload.get(key)
                for key in ("subtype", "is_error", "result", "errors")
                if payload.get(key) is not None
            }
            pytest.fail(
                "Claude Code failed: "
                + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
            )
        output = payload.get("result")
        assert payload.get("is_error") is False, payload.get("subtype")
        assert isinstance(output, str)
        assert _tool_call_count(mcp_record) == tool_calls
        assert PERSON in output
        assert "DONE" in output
        assert not re.search(r"SM_[A-Z]+_[0-9A-F]+", output)
        assert not re.search(r"sm-[a-z]+-[0-9a-f]+\.example\.invalid", output)
    finally:
        failed = sys.exc_info()[0] is not None
        gateway_stderr = gateway.stop()
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
                        "sm_upstream_",
                        "stream_",
                        "blocked",
                    )
                )
            )
            print(safe_log, file=sys.stderr)

    masked_counts = [
        int(value)
        for value in re.findall(r"request_masked entity_count=(\d+)", gateway_stderr)
    ]
    completed = _completed_stream_statuses(gateway_stderr)
    assert sum(count >= 1 for count in masked_counts) >= tool_calls + 1
    assert completed.count(200) >= tool_calls + 1
    assert PERSON not in gateway_stderr
    print(
        json.dumps(
            {
                "completed_streams": completed.count(200),
                "tool_calls": tool_calls,
                "turns": payload.get("num_turns"),
                "wall_ms": round(wall_ms, 1),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__" and sys.argv[1:] == ["--mcp-probe"]:
    raise SystemExit(_run_mcp_probe(sys.stdin, sys.stdout))
