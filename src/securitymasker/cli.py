"""SecurityMasker CLI (argparse; ADR-0003).

Never prints original sensitive values by default (§12): ``entities test`` shows the
MASKED text and per-entity counts, not the originals. Subcommands:

    securitymasker config validate [--config PATH]
    securitymasker entities list   [--config PATH]
    securitymasker entities test "<text>" [--config PATH]
    securitymasker doctor          [--config PATH]
    securitymasker run <tool> [args...]      # session-scoped wrapper (§7)
    securitymasker sessions <list|inspect|revoke|purge>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

from securitymasker import __version__
from securitymasker.config import SecurityMaskerConfig, build_engine, load_config
from securitymasker.errors import ConfigError, SecurityMaskerError
from securitymasker.integrations.launcher import (
    DEFAULT_GATEWAY,
    LaunchRefused,
    build_plan,
    describe_manual_setup,
    new_session_id,
)
from securitymasker.integrations.readiness import check_readiness
from securitymasker.sessions.memory import InMemorySessionStore


def _load(path: str | None) -> SecurityMaskerConfig:
    if not path:
        raise ConfigError("no --config given (a dictionary YAML is required)")
    return load_config(path)


def cmd_config_validate(args: argparse.Namespace) -> int:
    config = _load(args.config)
    print(
        f"OK: config valid — {len(config.entities)} entities, "
        f"{len(config.patterns)} patterns, secret_detector="
        f"{config.enable_secret_detector}, normalization={config.defaults.normalization}"
    )
    return 0


def cmd_entities_list(args: argparse.Namespace) -> int:
    config = _load(args.config)
    for e in config.entities:
        # Show variant COUNT, never the values themselves (§12).
        print(f"{e.id}\t{e.type}\t{e.replacement_profile}\t{e.restore_policy}\tvariants={len(e.resolved_values())}")
    for p in config.patterns:
        print(f"{p.id}\t{p.type}\t{p.replacement_profile}\t{p.restore_policy}\t[regex]")
    return 0


def cmd_entities_test(args: argparse.Namespace) -> int:
    config = _load(args.config)
    engine = build_engine(config)

    async def run() -> int:
        store = InMemorySessionStore()
        session = await store.get_or_create(f"cli-{uuid.uuid4()}")
        result = await engine.mask_text(session, args.text)
        counts = Counter(d.entity_type for d in result.detections)
        print("masked:")
        print(f"  {result.masked_text}")
        print("detected (type: count):")
        for etype, n in sorted(counts.items()):
            print(f"  {etype}: {n}")
        if not counts:
            print("  (none)")
        return 0

    return asyncio.run(run())


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print(f"securitymasker {__version__}")
    print(f"python {sys.version.split()[0]}")
    try:
        from importlib.metadata import version

        print(f"gateway deps: starlette {version('starlette')}, uvicorn {version('uvicorn')}")
    except Exception:  # noqa: BLE001
        ok = False
        print("gateway deps missing (pip install -e .)")
    try:
        from securitymasker.detectors.presidio import PresidioDetector

        avail = "available" if PresidioDetector().available else "not installed"
        print(f"presidio (JA NER): {avail}")
    except Exception:  # noqa: BLE001
        print("presidio: not installed")
    if args.config:
        try:
            config = load_config(args.config)
            # Build the engine so the SAME startup checks the gateway runs fire here:
            # required env vars, required detector models, regex/enum validation
            # (doc/06 P0-6, P2-1). A no-op "config OK" would hide these.
            build_engine(config)
            print(
                f"config OK: {len(config.entities)} entities, {len(config.patterns)} "
                f"patterns, fail_mode={config.defaults.fail_mode}, "
                f"presidio={'on' if config.presidio.enabled else 'off'}, "
                f"ner={'on' if config.ner.model else 'off'}"
            )
        except SecurityMaskerError as exc:
            ok = False
            print(f"config ERROR: {exc}")
    else:
        print("no --config given (skip dictionary check)")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Launch a tool with its traffic GUARANTEED to traverse the proxy (§7).

    Fail-closed: if the gateway is not ready, or we cannot route this particular
    tool, the child process is never started — reporting success while the tool
    talks straight to the provider would be the worst possible outcome.
    """
    if not args.tool:
        print("usage: securitymasker run <tool> [args...]", file=sys.stderr)
        return 2

    gateway = args.gateway or os.environ.get("SECURITYMASKER_GATEWAY_URL", DEFAULT_GATEWAY)

    status = check_readiness(gateway)
    if not status.ok:
        print(f"error: {status.detail}. Refusing to launch "
              f"{Path(args.tool[0]).name!r} unprotected.", file=sys.stderr)
        print(describe_manual_setup(gateway), file=sys.stderr)
        return 3

    session_id = new_session_id()
    try:
        plan = build_plan(list(args.tool), gateway=gateway, session_id=session_id,
                          allow_unknown_tool=args.unsafe_unknown_tool)
    except LaunchRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(describe_manual_setup(gateway), file=sys.stderr)
        return 3

    # Log the executable NAME, a fingerprint of the session id, and the route —
    # never the command line (it routinely carries tokens as flags), the raw
    # session id, or any credential (§25).
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:12]
    for warning in plan.warnings:
        print(f"[securitymasker] WARNING: {warning}", file=sys.stderr)
    print(
        f"[securitymasker] session {digest}… — launching {Path(plan.argv[0]).name} "
        f"({len(args.tool) - 1} arg(s) not shown) via {plan.route_note}",
        file=sys.stderr,
    )
    os.execvpe(plan.argv[0], plan.argv, plan.env)  # replaces this process
    return 0  # unreachable


def cmd_gateway(args: argparse.Namespace) -> int:
    """Launch the SecurityMasker proxy (ADR-0006). Uses SECURITYMASKER_CONFIG."""
    import uvicorn

    from securitymasker.gateway.app import create_app

    if args.config:
        os.environ["SECURITYMASKER_CONFIG"] = args.config

    # A non-loopback bind exposes the proxy — and the client credentials flowing
    # through it — to the network. Refuse unless the operator explicitly accepts
    # it AND has put an authenticator in front (doc/06 P0-9).
    from securitymasker.gateway.runtime import LOOPBACK_HOSTS

    if args.host not in LOOPBACK_HOSTS:
        acknowledged = os.environ.get("SECURITYMASKER_ALLOW_PUBLIC_BIND") == "1"
        multitenant = os.environ.get("SECURITYMASKER_MODE") == "multitenant"
        if not acknowledged:
            print(
                f"error: refusing to bind {args.host} (non-loopback). The proxy has no "
                "built-in authentication; put a trusted authenticator in front and set "
                "SECURITYMASKER_ALLOW_PUBLIC_BIND=1 to acknowledge.",
                file=sys.stderr,
            )
            return 2
        if not multitenant:
            print(
                "warning: public bind in single-tenant 'local' mode — every caller "
                "shares one alias table. Use SECURITYMASKER_MODE=multitenant.",
                file=sys.stderr,
            )

    configured = bool(os.environ.get("SECURITYMASKER_CONFIG"))
    dev = os.environ.get("SECURITYMASKER_DEV_TRANSPARENT") == "1"
    mode = "masking" if configured else ("DEV transparent (no masking!)" if dev else "will fail")
    print(
        f"[securitymasker] gateway on http://{args.host}:{args.port} ({mode})",
        file=sys.stderr,
    )
    # create_app() -> GatewayRuntime.from_env() fails closed if unconfigured (P0-1).
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    # Honest non-zero exit: this command is not implemented (doc/06 P2-1). The
    # in-memory store lives inside the gateway process and is not reachable here;
    # a CLI that manages sessions requires the shared Redis store to be wired to
    # this process too. Do NOT exit 0 as if it succeeded.
    print(
        f"sessions {args.subaction}: not implemented. The session store lives in the "
        "gateway process; CLI session management needs a shared (Redis) store. "
        "This command is a placeholder and intentionally exits non-zero.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="securitymasker", description="SecurityMasker CLI")
    parser.add_argument("--version", action="version", version=f"securitymasker {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=os.environ.get("SECURITYMASKER_CONFIG"),
                       help="path to the SecurityMasker dictionary YAML")

    p_config = sub.add_parser("config", help="config operations")
    config_sub = p_config.add_subparsers(dest="subaction", required=True)
    p_validate = config_sub.add_parser("validate", help="validate the config")
    add_config(p_validate)
    p_validate.set_defaults(func=cmd_config_validate)

    p_entities = sub.add_parser("entities", help="entity dictionary operations")
    entities_sub = p_entities.add_subparsers(dest="subaction", required=True)
    p_list = entities_sub.add_parser("list", help="list configured entities (no values)")
    add_config(p_list)
    p_list.set_defaults(func=cmd_entities_list)
    p_test = entities_sub.add_parser("test", help="mask a sample string and show detections")
    p_test.add_argument("text")
    add_config(p_test)
    p_test.set_defaults(func=cmd_entities_test)

    p_doctor = sub.add_parser("doctor", help="environment and config sanity checks")
    add_config(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_gateway = sub.add_parser("gateway", help="run the SecurityMasker proxy")
    p_gateway.add_argument("--host", default="127.0.0.1")
    p_gateway.add_argument("--port", type=int, default=4000)
    add_config(p_gateway)
    p_gateway.set_defaults(func=cmd_gateway)

    p_run = sub.add_parser(
        "run", help="launch codex/claude with traffic guaranteed to go via the proxy")
    p_run.add_argument("--gateway", default=None,
                       help=f"gateway base URL (default {DEFAULT_GATEWAY})")
    p_run.add_argument("--unsafe-unknown-tool", action="store_true",
                       help="launch an unroutable tool UNPROTECTED (not recommended)")
    p_run.add_argument("tool", nargs=argparse.REMAINDER, help="tool and args, e.g. codex")
    p_run.set_defaults(func=cmd_run)

    p_sessions = sub.add_parser("sessions", help="session management")
    p_sessions.add_argument("subaction", choices=["list", "inspect", "revoke", "purge"])
    p_sessions.add_argument("session_id", nargs="?")
    p_sessions.set_defaults(func=cmd_sessions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SecurityMaskerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
