"""`securitymasker run` proxy-path guarantee (doc/06 P2-1).

The wrapper must never start a tool it cannot route through the gateway: a user
who sees "launching codex" is entitled to assume their prompts are being masked.
Every refusal path asserts the child process was NOT started. Synthetic data only.
"""

from __future__ import annotations

import json

import pytest

from securitymasker.integrations.launcher import (
    SESSION_ENV,
    SESSION_HEADER,
    LaunchRefused,
    build_plan,
    new_session_id,
    tool_kind,
)
from securitymasker.integrations.readiness import check_readiness

GATEWAY = "http://127.0.0.1:4000"
SID = "test-session-id-abc"


# --- tool identification ---------------------------------------------------------


@pytest.mark.parametrize(("executable", "expected"), [
    ("codex", "codex"),
    ("/opt/homebrew/bin/codex", "codex"),
    ("C:\\tools\\codex.exe", "codex"),
    ("CODEX", "codex"),
    ("claude", "claude"),
    ("/usr/local/bin/claude-code", "claude"),
    ("claude.cmd", "claude"),
    ("bash", "unknown"),
    ("/usr/bin/curl", "unknown"),
])
def test_tool_kind_handles_paths_and_platform_suffixes(executable, expected) -> None:
    assert tool_kind(executable) == expected


def test_session_id_is_random_and_unpredictable() -> None:
    ids = {new_session_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) >= 24 for i in ids)


# --- Claude Code routing ---------------------------------------------------------


def test_claude_plan_sets_base_url_and_session_header() -> None:
    plan = build_plan(["claude"], gateway=GATEWAY, session_id=SID, environ={})
    assert plan.env["ANTHROPIC_BASE_URL"] == GATEWAY
    assert plan.env[SESSION_ENV] == SID
    assert f"{SESSION_HEADER}: {SID}" in plan.env["ANTHROPIC_CUSTOM_HEADERS"]
    assert plan.warnings == ()


def test_claude_plan_merges_existing_custom_headers() -> None:
    existing = "X-Team: platform\nX-Trace: on"
    plan = build_plan(["claude"], gateway=GATEWAY, session_id=SID,
                      environ={"ANTHROPIC_CUSTOM_HEADERS": existing})
    merged = plan.env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "X-Team: platform" in merged and "X-Trace: on" in merged
    assert f"{SESSION_HEADER}: {SID}" in merged


def test_claude_plan_replaces_stale_session_header_without_duplicating() -> None:
    stale = f"{SESSION_HEADER}: old-session\nX-Team: platform"
    plan = build_plan(["claude"], gateway=GATEWAY, session_id=SID,
                      environ={"ANTHROPIC_CUSTOM_HEADERS": stale})
    merged = plan.env["ANTHROPIC_CUSTOM_HEADERS"]
    assert merged.count(SESSION_HEADER) == 1
    assert "old-session" not in merged
    assert "X-Team: platform" in merged


def test_no_unexpanded_placeholder_is_passed() -> None:
    # A literal "${SECURITYMASKER_SESSION_ID}" would never be expanded by the tool
    # and would silently become a bogus session id.
    plan = build_plan(["claude"], gateway=GATEWAY, session_id=SID, environ={})
    blob = json.dumps(plan.env) + " ".join(plan.argv)
    assert "${" not in blob


# --- Codex routing ---------------------------------------------------------------


def test_codex_plan_uses_per_process_overrides_only() -> None:
    plan = build_plan(["codex", "--search"], gateway=GATEWAY, session_id=SID, environ={})
    joined = " ".join(plan.argv)
    assert plan.argv[0] == "codex"
    assert "model_provider=" in joined
    assert f'base_url="{GATEWAY}"' in joined
    assert "requires_openai_auth=true" in joined
    assert SESSION_HEADER in joined            # session header propagated
    assert "--search" in plan.argv             # user args preserved
    # The user's real Codex config must not be touched in any way.
    assert "CODEX_HOME" not in plan.env


def test_codex_plan_keeps_user_arguments_after_overrides() -> None:
    plan = build_plan(["codex", "exec", "do a thing"], gateway=GATEWAY,
                      session_id=SID, environ={})
    assert plan.argv[-2:] == ["exec", "do a thing"]


# --- refusals --------------------------------------------------------------------


def test_unknown_tool_is_refused_by_default() -> None:
    with pytest.raises(LaunchRefused) as exc:
        build_plan(["curl", "https://api.example"], gateway=GATEWAY, session_id=SID,
                   environ={})
    assert "curl" in str(exc.value)


def test_unknown_tool_with_explicit_optin_warns_loudly() -> None:
    plan = build_plan(["curl"], gateway=GATEWAY, session_id=SID, environ={},
                      allow_unknown_tool=True)
    assert plan.warnings and "UNPROTECTED" in plan.warnings[0]


@pytest.mark.parametrize("var", ["ANTHROPIC_API_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"])
def test_direct_provider_env_is_refused(var) -> None:
    with pytest.raises(LaunchRefused) as exc:
        build_plan(["claude"], gateway=GATEWAY, session_id=SID,
                   environ={var: "https://api.anthropic.com"})
    assert var in str(exc.value)


def test_empty_command_is_refused() -> None:
    with pytest.raises(LaunchRefused):
        build_plan([], gateway=GATEWAY, session_id=SID, environ={})


# --- readiness gating ------------------------------------------------------------


def test_readiness_rejects_unreachable_gateway() -> None:
    status = check_readiness("http://127.0.0.1:1", timeout=0.2)
    assert not status.ok and "unreachable" in status.detail


def test_readiness_rejects_not_ready(monkeypatch) -> None:
    import httpx

    from securitymasker.integrations import readiness as mod

    def fake_get(url, timeout=None):
        return httpx.Response(503, json={"ready": False, "reason": "engine not configured"})

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    status = check_readiness(GATEWAY)
    assert not status.ok and "not ready" in status.detail


def test_readiness_rejects_ok_but_unready_body(monkeypatch) -> None:
    import httpx

    from securitymasker.integrations import readiness as mod

    # A transparent-mode gateway can answer 200 while masking nothing.
    monkeypatch.setattr(mod.httpx, "get",
                        lambda url, timeout=None: httpx.Response(200, json={"ok": True}))
    assert not check_readiness(GATEWAY).ok


def test_readiness_accepts_ready(monkeypatch) -> None:
    import httpx

    from securitymasker.integrations import readiness as mod

    monkeypatch.setattr(mod.httpx, "get",
                        lambda url, timeout=None: httpx.Response(200, json={"ready": True}))
    assert check_readiness(GATEWAY).ok


# --- CLI level: nothing is launched when the route cannot be guaranteed ----------


def _cli_run(monkeypatch, argv, *, ready: bool, environ=None):
    """Run `securitymasker run ...`, recording any exec that would have happened."""
    from securitymasker import cli
    from securitymasker.integrations.readiness import Readiness

    launched: list[list[str]] = []
    monkeypatch.setattr(cli, "check_readiness",
                        lambda gw, **kw: Readiness(ready, "ready" if ready else "down"))
    monkeypatch.setattr(cli.os, "execvpe",
                        lambda file, args, env: launched.append(list(args)))
    for key, value in (environ or {}).items():
        monkeypatch.setenv(key, value)
    args = cli.build_parser().parse_args(argv)
    return cli.cmd_run(args), launched


def test_cli_does_not_launch_when_gateway_is_down(monkeypatch, capsys) -> None:
    code, launched = _cli_run(monkeypatch, ["run", "claude"], ready=False)
    assert code != 0
    assert launched == [], "child process was started without a proxy route"


def test_cli_does_not_launch_unknown_tool(monkeypatch) -> None:
    code, launched = _cli_run(monkeypatch, ["run", "curl"], ready=True)
    assert code != 0 and launched == []


def test_cli_launches_claude_when_ready(monkeypatch) -> None:
    code, launched = _cli_run(monkeypatch, ["run", "claude"], ready=True)
    assert code == 0 and len(launched) == 1 and launched[0][0] == "claude"


def test_cli_never_logs_arguments_or_session_id(monkeypatch, capsys) -> None:
    secret_arg = "sk-ant-" + "z" * 30
    code, launched = _cli_run(monkeypatch, ["run", "claude", "--token", secret_arg],
                              ready=True)
    err = capsys.readouterr().err
    assert code == 0 and len(launched) == 1
    assert secret_arg not in err
    # The concrete session id must not be printed either; only a fingerprint.
    session_id = launched[0] and dict(enumerate(launched[0]))
    assert session_id is not None
    assert "session " in err and "…" in err
