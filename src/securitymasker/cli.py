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
import os
import sys
import uuid
from collections import Counter

from securitymasker import __version__
from securitymasker.config import SecurityMaskerConfig, build_engine, load_config
from securitymasker.errors import ConfigError, SecurityMaskerError
from securitymasker.sessions.memory import InMemorySessionStore

SESSION_ENV = "SECURITYMASKER_SESSION_ID"


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
    """Generate a session id, export it, and exec the wrapped tool (§7)."""
    if not args.tool:
        print("usage: securitymasker run <tool> [args...]", file=sys.stderr)
        return 2
    session_id = os.environ.get(SESSION_ENV) or str(uuid.uuid4())
    env = {**os.environ, SESSION_ENV: session_id}
    print(
        f"[securitymasker] session {session_id} — launching: {' '.join(args.tool)}",
        file=sys.stderr,
    )
    os.execvpe(args.tool[0], args.tool, env)  # replaces this process
    return 0  # unreachable


def cmd_gateway(args: argparse.Namespace) -> int:
    """Launch the SecurityMasker proxy (ADR-0006). Uses SECURITYMASKER_CONFIG."""
    import uvicorn

    from securitymasker.gateway.app import create_app

    if args.config:
        os.environ["SECURITYMASKER_CONFIG"] = args.config
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

    p_run = sub.add_parser("run", help="launch a tool under a fresh masking session")
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
