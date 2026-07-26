"""SecurityMasker CLI（argparse、ADR-0003）。

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
from importlib import resources
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


def cmd_config_init(args: argparse.Namespace) -> int:
    """配布物内の合成値だけを含むstarter設定を安全に書き出す。"""
    output = Path(args.output)
    template = resources.files("securitymasker").joinpath(
        "resources/securitymasker.example.yaml"
    ).read_text(encoding="utf-8")
    try:
        if args.force:
            output.write_text(template, encoding="utf-8")
        else:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(template)
    except FileExistsError:
        print(
            f"error: {output} already exists; pass --force to replace it",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        print(f"error: cannot write {output}: {detail}", file=sys.stderr)
        return 1
    print(f"created {output}")
    return 0


def cmd_entities_list(args: argparse.Namespace) -> int:
    config = _load(args.config)
    for e in config.entities:
        # 値自体ではなくvariant件数だけを表示する（§12）。
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
    """全runtime checkを実行し、一つでもFAILなら非0で終了する（doc/06 P2-1）。"""
    from securitymasker import doctor as checks

    gateway = args.gateway or os.environ.get("SECURITYMASKER_GATEWAY_URL", DEFAULT_GATEWAY)
    config_path = args.config or os.environ.get("SECURITYMASKER_CONFIG")
    # 明示指定したGatewayには到達できることをoperatorが期待している。
    # so its absence is a failure rather than a pre-flight note.
    require_ready = args.require_ready or args.gateway is not None
    results, built = checks.run_checks_with_engine(
        config_path=config_path, environ=dict(os.environ), gateway=gateway,
        require_ready=require_ready)
    # store probeにはevent loopと構築済みruntimeが必要なため、ここで実行する。
    # rather than inside the pure check sequence.
    if config_path:
        try:
            from securitymasker.gateway.runtime import GatewayRuntime

            # `--config` must drive the probe too, not just the pure checks.
            os.environ["SECURITYMASKER_CONFIG"] = config_path
            # checkが構築済みのengine／configを渡し、二重buildを防ぐ。
            # them again would load the NER model a second time.
            runtime = GatewayRuntime.from_env(engine=built.engine, config=built.config)
            results.append(asyncio.run(checks.check_store_probe(runtime.store)))
        except (SecurityMaskerError, OSError) as exc:
            # 不正config、読取不能path、到達不能serviceなどoperator向け失敗を安全に報告する。
            # store) must surface as a check result, never a traceback.
            detail = getattr(exc, "strerror", None) or str(exc)
            results.append(checks.CheckResult(
                "store.probe", checks.Status.FAIL, f"runtime not constructible: {detail}"))

    print(checks.render_json(results) if args.json else checks.render(results))
    return 1 if any(r.failed for r in results) else 0


def cmd_run(args: argparse.Namespace) -> int:
    """proxy経由に設定したtoolだけを起動し、設定不能なら何も起動しない（§7）。

    Fail-closed: if the gateway is not ready, or we cannot route this particular
    tool, the child process is never started — reporting success while the tool
    talks straight to the provider would be the worst possible outcome.

    What that does and does not establish, stated precisely because "guaranteed"
    was overclaiming it:

    - Verified by unit tests: the settings handed to the child point at the
      gateway and carry the session header; direct-provider environment variables
      and unknown tools are refused; nothing launches unless /ready reports ready.
    - Verified by tests/integration/test_real_cli_e2e.py (opt-in): the real codex
      and claude binaries, launched this way, reach a mock upstream carrying only
      aliases. That test exists because settings we consider well-formed can still
      be rejected by the tool — as happened when http_headers was emitted as JSON
      rather than TOML and codex refused to start at all.
    - Still not covered: a real provider. The E2E upstream is a local mock, so
      this says nothing about provider-side behaviour.
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

    # executable名、session ID fingerprint、routeだけをlogへ記録する。
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


def cmd_models_fetch(args: argparse.Namespace) -> int:
    """固定したNER modelを取得してdigestを検証する（ADR-0009）。

    Deliberately a separate step from serving: the runtime loads models offline,
    so nothing a user types can ever trigger a download.
    """
    from securitymasker.models_fetch import fetch

    model, revision = args.model, args.revision
    if not model or not revision:
        # operatorの再入力を避けるため設定済みpinへfallbackする。
        if not args.config:
            print("error: give --model/--revision, or --config to use its ner pin",
                  file=sys.stderr)
            return 2
        ner = load_config(args.config).ner
        model, revision = model or ner.model, revision or ner.revision
    if not model or not revision:
        print("error: no NER model/revision configured to fetch", file=sys.stderr)
        return 2

    result = fetch(model, revision, allow_unverified=args.allow_unverified)
    print(f"fetched {result.model}@{result.revision}")
    for name in sorted(result.verified):
        print(f"  verified   {name}")
    if not result.verified and args.allow_unverified:
        print("  WARNING: accepted UNVERIFIED — no artifact manifest on record")
    return 0 if result.ok else 1


def cmd_gateway(args: argparse.Namespace) -> int:
    """`SECURITYMASKER_CONFIG`を使ってSecurityMasker proxyを起動する（ADR-0006）。"""
    import uvicorn

    from securitymasker.gateway.app import create_app

    if args.config:
        os.environ["SECURITYMASKER_CONFIG"] = args.config

    # A non-loopback bind exposes the proxy — and the client credentials flowing
    # through it — to the network. Refuse unless the operator explicitly accepts
    # it AND has put an authenticator in front (doc/06 P0-9).
    from securitymasker.gateway.runtime import LOOPBACK_HOSTS

    if args.host not in LOOPBACK_HOSTS:
        from securitymasker.gateway.identity import isolates_callers

        acknowledged = os.environ.get("SECURITYMASKER_ALLOW_PUBLIC_BIND") == "1"
        # legacy名だけでなくcallerを分離する全modeを対象とする。
        # `tenant`/`tenant_user` deployments got the single-tenant warning.
        multitenant = isolates_callers(os.environ.get("SECURITYMASKER_MODE", "local"))
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
                "shares one alias table. Set SECURITYMASKER_MODE=tenant_user to "
                "separate callers by tenant AND user, or =tenant for tenant only.",
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
    # 未実装commandなので正直に非0終了する（doc/06 P2-1）。
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
    p_init = config_sub.add_parser(
        "init", help="write a safe starter config containing synthetic values"
    )
    p_init.add_argument(
        "--output", default="securitymasker.yaml", help="destination YAML path"
    )
    p_init.add_argument(
        "--force", action="store_true", help="replace an existing destination"
    )
    p_init.set_defaults(func=cmd_config_init)

    p_entities = sub.add_parser("entities", help="entity dictionary operations")
    entities_sub = p_entities.add_subparsers(dest="subaction", required=True)
    p_list = entities_sub.add_parser("list", help="list configured entities (no values)")
    add_config(p_list)
    p_list.set_defaults(func=cmd_entities_list)
    p_test = entities_sub.add_parser("test", help="mask a sample string and show detections")
    p_test.add_argument("text")
    add_config(p_test)
    p_test.set_defaults(func=cmd_entities_test)

    p_doctor = sub.add_parser("doctor", help="runtime, config and connectivity checks")
    p_doctor.add_argument("--json", action="store_true",
                          help="machine-readable output (no secrets)")
    p_doctor.add_argument("--gateway", default=None,
                          help="gateway URL to probe; passing it makes an unreachable "
                               "gateway a FAILURE rather than a warning")
    p_doctor.add_argument("--require-ready", action="store_true",
                          help="treat an unready gateway as a failure (for monitoring)")
    add_config(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_gateway = sub.add_parser("gateway", help="run the SecurityMasker proxy")
    p_gateway.add_argument("--host", default="127.0.0.1")
    p_gateway.add_argument("--port", type=int, default=4000)
    add_config(p_gateway)
    p_gateway.set_defaults(func=cmd_gateway)

    p_models = sub.add_parser("models", help="model preparation (offline runtime)")
    models_sub = p_models.add_subparsers(dest="subaction", required=True)
    p_fetch = models_sub.add_parser("fetch", help="download + verify a pinned NER model")
    p_fetch.add_argument("--model", default=None)
    p_fetch.add_argument("--revision", default=None)
    p_fetch.add_argument("--allow-unverified", action="store_true",
                         help="DANGEROUS: accept a model with no artifact manifest")
    add_config(p_fetch)
    p_fetch.set_defaults(func=cmd_models_fetch)

    p_run = sub.add_parser(
        "run", help="launch codex/claude configured to route via the proxy "
                    "(refuses to start if the route cannot be set up)")
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
