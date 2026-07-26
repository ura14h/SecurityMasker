"""``securitymasker run``のproxy path保証（§7、doc/06 P2-1）。

``run`` used to generate a session id and exec the tool. That gave the user no
assurance whatsoever that Codex or Claude Code actually went through
SecurityMasker — a stale config, a forgotten environment variable, or a plain
mistake meant the tool talked to the provider directly while the wrapper reported
success. Believing you are protected when you are not is worse than knowing you
are unprotected, so the wrapper now refuses to start the tool unless it can
establish the route itself.

The guarantee has three parts, all fail-closed:

1. the gateway answers ``/ready`` with ``ready: true`` (so it exists, has a
   masking config, and its session store works);
2. the launch environment/arguments we build point that tool at the gateway;
3. anything we cannot route — an unknown command, a Codex install we cannot
   override per-process — is refused rather than launched unprotected.

Per-tool knowledge lives here rather than in the CLI so the CLI stays free of
provider branching, and so each integration is unit-testable without a process.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_GATEWAY = "http://127.0.0.1:4000"
SESSION_ENV = "SECURITYMASKER_SESSION_ID"
SESSION_HEADER = "X-SecurityMasker-Session-ID"

# Environment variables that would send a tool straight at a provider, bypassing
# us. Their presence is a configuration error we refuse rather than silently win
# or silently lose against (the tool's own precedence rules are not ours to bet on).
_DIRECT_PROVIDER_ENV = (
    "ANTHROPIC_API_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)


class LaunchRefused(Exception):
    """proxy routeを設定できずtoolを起動しなかったことを示す。"""


@dataclass(frozen=True)
class LaunchPlan:
    """proxy経由と確認済みで、解決済み・実行可能な起動設定。"""

    argv: list[str]
    env: dict[str, str]
    tool_kind: str
    # Human-readable, secret-free description of how the route is enforced.
    route_note: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def new_session_id() -> str:
    """CSPRNGから新しいsession IDを生成する。予測値や導出値は使わない（§7）。"""
    return secrets.token_urlsafe(24)


def tool_kind(executable: str) -> str:
    """platform suffixを許容し、対象executableをbasenameで分類する。

    Splits on BOTH separators: a Windows path handed to a POSIX interpreter (or
    vice versa) must still resolve to the same tool, since misclassifying it would
    silently downgrade a known tool to the refused "unknown" path.
    """
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name == "codex":
        return "codex"
    if name in ("claude", "claude-code"):
        return "claude"
    return "unknown"


def _merge_anthropic_headers(existing: str | None, session_id: str) -> str:
    """既存値を失わず``ANTHROPIC_CUSTOM_HEADERS``へsession headerを追加する。

    Claude Code reads this as newline-separated ``Name: value`` lines. We keep the
    user's headers verbatim, drop only a previous session header of ours, and
    append the current one — so re-running never accumulates duplicates.
    """
    lines = [ln for ln in (existing or "").splitlines() if ln.strip()]
    kept = [ln for ln in lines if ln.split(":", 1)[0].strip().lower() != SESSION_HEADER.lower()]
    kept.append(f"{SESSION_HEADER}: {session_id}")
    return "\n".join(kept)


def _codex_overrides(gateway: str, session_id: str) -> list[str]:
    """CodexをGatewayへ向けるprocess単位のconfig override。

    Codex's ``-c key=value`` flags layer over ``~/.codex/config.toml`` for THIS
    invocation only, so the user's real configuration is never modified (a hard
    requirement — the wrapper must not edit files outside the repo). Values are
    TOML literals, hence the JSON-quoted strings.
    """
    provider = "securitymasker"
    # A TOML inline table: `{ "k" = "v" }`, NOT the JSON `{"k": "v"}`. `-c` values
    # are layered over config.toml and parsed as TOML, where a colon is a syntax
    # error — so the JSON form silently produced an unparseable override.
    header = "{" + f'{json.dumps(SESSION_HEADER)} = {json.dumps(session_id)}' + "}"
    return [
        "-c", f"model_provider={json.dumps(provider)}",
        "-c", f"model_providers.{provider}.name={json.dumps('SecurityMasker')}",
        "-c", f"model_providers.{provider}.base_url={json.dumps(gateway)}",
        "-c", f"model_providers.{provider}.wire_api={json.dumps('responses')}",
        # Keep Codex's own ChatGPT OAuth flow: it forwards its bearer token to our
        # base_url and we pass it through untouched (ADR-0006, §25).
        "-c", f"model_providers.{provider}.requires_openai_auth=true",
        "-c", f"model_providers.{provider}.http_headers={header}",
    ]


def build_plan(
    argv: list[str],
    *,
    gateway: str,
    session_id: str,
    environ: dict[str, str] | None = None,
    allow_unknown_tool: bool = False,
) -> LaunchPlan:
    """``argv``をproxy経由の起動設定へ解決する。

    The plan either routes the tool through the gateway or refuses. It cannot
    attest to what the child process does afterwards — see ``cmd_run`` for the
    boundary between what is verified and what is not.

    Raises ``LaunchRefused`` when the route cannot be established. Callers must
    treat that as "do not start the child process" (doc/06 P2-1).
    """
    if not argv:
        raise LaunchRefused("no command given")

    env = dict(os.environ if environ is None else environ)
    kind = tool_kind(argv[0])

    direct = [name for name in _DIRECT_PROVIDER_ENV if env.get(name)]
    if direct:
        raise LaunchRefused(
            f"{', '.join(sorted(direct))} is set, which would send traffic straight "
            "to the provider and bypass SecurityMasker. Unset it and re-run."
        )

    env[SESSION_ENV] = session_id
    warnings: list[str] = []

    if kind == "claude":
        # Claude Code honours ANTHROPIC_BASE_URL per process; the session header
        # rides along in ANTHROPIC_CUSTOM_HEADERS. Both are concrete values — a
        # literal "${SECURITYMASKER_SESSION_ID}" would never be expanded by the
        # tool and would silently become an unusable session id.
        env["ANTHROPIC_BASE_URL"] = gateway
        env["ANTHROPIC_CUSTOM_HEADERS"] = _merge_anthropic_headers(
            env.get("ANTHROPIC_CUSTOM_HEADERS"), session_id
        )
        return LaunchPlan(
            argv=list(argv), env=env, tool_kind=kind,
            route_note=f"ANTHROPIC_BASE_URL -> {gateway} (+ session header)",
        )

    if kind == "codex":
        # Per-process overrides only; the user's ~/.codex/config.toml is untouched.
        plan_argv = [argv[0], *_codex_overrides(gateway, session_id), *argv[1:]]
        return LaunchPlan(
            argv=plan_argv, env=env, tool_kind=kind,
            route_note=f"codex -c model_providers.securitymasker.base_url -> {gateway}",
        )

    # Unknown tool: we have no way to point it at the gateway, so we must not
    # imply protection. Refuse by default; the explicit opt-in launches it with
    # only the session id set and says plainly that nothing is guaranteed.
    if not allow_unknown_tool:
        raise LaunchRefused(
            f"{Path(argv[0]).name!r} is not a tool SecurityMasker knows how to route "
            "(supported: codex, claude). Refusing to launch it as if it were "
            "protected. Re-run with --unsafe-unknown-tool to start it anyway, "
            "UNPROTECTED."
        )
    warnings.append(
        "UNPROTECTED: this tool's traffic is not routed through SecurityMasker; "
        "only the session id was exported."
    )
    return LaunchPlan(
        argv=list(argv), env=env, tool_kind=kind,
        route_note="none — unknown tool launched unprotected",
        warnings=tuple(warnings),
    )


def describe_manual_setup(gateway: str) -> str:
    """起動拒否時に表示するsecret-freeな案内（§25）。

    Delegates to the per-tool helpers so the provider keys are defined in exactly
    one place: a second copy of this snippet would drift from the overrides
    ``build_plan`` actually applies, and the drift would only show up as a user
    silently talking to the provider direct.
    """
    from securitymasker.integrations.claude_code import claude_code_shell_snippet
    from securitymasker.integrations.codex import codex_config_toml

    return (
        "SecurityMasker did not start the tool. To route it manually:\n\n"
        # A concrete session id first: the snippets reference
        # SECURITYMASKER_SESSION_ID, and an unset one yields an EMPTY session
        # header, which the gateway treats as no session at all.
        f"  1. export {SESSION_ENV}=$(python -c "
        "'import secrets;print(secrets.token_urlsafe(24))')\n\n"
        f"  2. Claude Code:\n    {claude_code_shell_snippet(gateway)}\n\n"
        "  2. Codex — add to ~/.codex/config.toml:\n"
        + "\n".join(f"    {line}" for line in codex_config_toml(gateway).splitlines())
        + "\n\n  3. Start the gateway first:\n"
        "    securitymasker gateway --config <dictionary.yaml>"
    )
