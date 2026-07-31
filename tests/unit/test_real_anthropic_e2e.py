"""実Anthropic E2E helperを外部通信なしで検証する。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.integration import test_real_anthropic_e2e as e2e


def test_claude_environment_is_temporary_and_deny_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-token")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-secret: must-not-pass")
    monkeypatch.setenv("HTTPS_PROXY", "https://unrelated.invalid")

    environment = e2e._claude_environment("http://127.0.0.1:45678", tmp_path)

    assert environment["ANTHROPIC_AUTH_TOKEN"] == "synthetic-token"
    assert environment["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:45678"
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path)
    assert environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "ANTHROPIC_CUSTOM_HEADERS" not in environment
    assert "HTTPS_PROXY" not in environment

    oauth_environment = e2e._claude_environment("http://127.0.0.1:45678")
    assert "CLAUDE_CONFIG_DIR" not in oauth_environment


def test_mcp_probe_returns_fixed_synthetic_tool_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = tmp_path / "calls.jsonl"
    monkeypatch.setenv("SM_CLAUDE_E2E_TOOL_CALLS", "2")
    monkeypatch.setenv("SM_CLAUDE_E2E_MCP_RECORD", str(record))
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "repeat_probe", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "repeat_probe", "arguments": {}},
        },
    ]
    stdin = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    stdout = io.StringIO()

    assert e2e._run_mcp_probe(stdin, stdout) == 0

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    first = json.loads(responses[2]["result"]["content"][0]["text"])
    second = json.loads(responses[3]["result"]["content"][0]["text"])
    assert first == {
        "step": 1,
        "remaining": 1,
        "done": False,
        "person": e2e.PERSON,
    }
    assert second == {
        "step": 2,
        "remaining": 0,
        "done": True,
        "person": e2e.PERSON,
    }
    assert e2e._tool_call_count(record) == 2


def test_claude_command_disables_builtin_tools_and_persistence(tmp_path: Path) -> None:
    command = e2e._claude_command("/usr/bin/claude", tmp_path / "mcp.json", 4)

    assert command[0] == "/usr/bin/claude"
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--allowedTools") + 1] == "mcp__probe__repeat_probe"
    assert "--no-session-persistence" in command
    assert "--strict-mcp-config" in command
    assert command[command.index("--setting-sources") + 1] == ""
    assert str(e2e.REPO) not in " ".join(command)
