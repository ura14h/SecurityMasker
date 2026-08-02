"""SecurityMaskerのコマンドラインインターフェース。

利用者の機密値は既定で表示しない。``preview``はマスク後の文字列とentity種別ごとの
検出件数だけを表示し、元の値を列挙しない。主なコマンドは次のとおり。

    securitymasker init [--directory DIRECTORY] [--force]
                        [--mode chatgpt|claude] [--port PORT]
    securitymasker gateway [--config PATH] [--mode MODE] [--port PORT]
    securitymasker preview [TEXT] [--config PATH]
    securitymasker client-config [--config PATH]
    securitymasker config-check [--config PATH]
    securitymasker entities [--config PATH]
    securitymasker doctor [--config PATH] [--json]
    securitymasker model-load [--config PATH]

引数なしではhelpを表示して終了する。常駐Gatewayを起動する場合は、意図しないport openや
長時間processを避けるため、``gateway`` commandを明示的に要求する。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import Counter
from typing import Any

from securitymasker import __version__
from securitymasker.config import (
    SecurityMaskerConfig,
    build_engine,
    load_config,
    resolve_config_path,
)
from securitymasker.distribution import version_text
from securitymasker.errors import ConfigError, SecurityMaskerError, SessionError
from securitymasker.logging import configure_logging, get_logger
from securitymasker.sessions.memory import InMemorySessionStore


def _load(path: str | None) -> SecurityMaskerConfig:
    return load_config(resolve_config_path(path))


def cmd_config_check(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if config.version != 1:
        raise ConfigError("version 1 securitymasker.config is required")
    runtime = (
        f", mode={config.runtime.mode}, port={config.runtime.port}"
        if config.runtime is not None
        else ""
    )
    print(
        f"OK: config valid — {len(config.entities)} entities, "
        f"{len(config.patterns)} patterns, secret_detector="
        f"{config.enable_secret_detector}, normalization={config.defaults.normalization}"
        f"{runtime}"
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """隣接config、単一辞書、state directory、master keyを安全に生成する。"""
    from securitymasker.bootstrap import default_init_directory, initialize_layout

    if args.force and args.directory is None:
        raise ConfigError("init --force requires an explicit --directory")
    directory = args.directory or default_init_directory(args.mode)
    layout = initialize_layout(directory, mode=args.mode, port=args.port, force=args.force)
    action = "reset" if layout.replaced_existing else "initialized"
    print(f"{action} SecurityMasker in {layout.root}")
    print("created securitymasker.config, securitymasker.dict and securitymasker.state/")
    if layout.replaced_existing:
        print("previous config, dictionary, sessions, aliases and master key were deleted")
    print("state database will be created on the first gateway start")
    return 0


def cmd_entities(args: argparse.Namespace) -> int:
    config = _load(args.config)
    for e in config.entities:
        # 機密値を端末履歴へ残さないよう、値ではなくvariant件数だけを表示する。
        print(f"{e.id}\t{e.type}\t{e.replacement_profile}\t{e.restore_policy}\tvariants={len(e.resolved_values())}")
    for p in config.patterns:
        print(f"{p.id}\t{p.type}\t{p.replacement_profile}\t{p.restore_policy}\t[regex]")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Gatewayと同じpipelineで外部送信せずmask結果を確認する。"""
    if args.text is not None:
        text = args.text
    elif sys.stdin.isatty():
        print("error: preview requires TEXT or non-empty standard input", file=sys.stderr)
        return 2
    else:
        text = sys.stdin.read()
        if not text:
            print("error: preview requires TEXT or non-empty standard input", file=sys.stderr)
            return 2

    config = _load(args.config)
    engine = build_engine(config)

    async def run() -> int:
        store = InMemorySessionStore()
        session = await store.get_or_create(f"cli-{uuid.uuid4()}")
        result = await engine.mask_text(session, text)
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


def cmd_client_config(args: argparse.Namespace) -> int:
    """mode別のclient設定を表示するだけで、実fileは変更しない。"""
    from securitymasker.integrations.client_config import client_setup_snippet

    print(client_setup_snippet(_load(args.config)), end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """read-only診断を実行し、一つでもFAILなら非0で終了する。"""
    from securitymasker import doctor as checks

    gateway = args.gateway or os.environ.get("SECURITYMASKER_GATEWAY_URL", "")
    config_path: str | None
    try:
        config_path = str(resolve_config_path(args.config))
    except ConfigError:
        config_path = args.config or os.environ.get("SECURITYMASKER_CONFIG")
    # 明示指定したGatewayには到達できることを利用者が期待しているため、
    # 到達不能を起動前の注意ではなく失敗として扱う。
    require_ready = args.require_ready or args.gateway is not None
    results, _ = checks.run_checks_with_engine(
        config_path=config_path, environ=dict(os.environ), gateway=gateway,
        require_ready=require_ready)

    print(checks.render_json(results) if args.json else checks.render(results))
    return 1 if any(r.failed for r in results) else 0


def cmd_model_load(args: argparse.Namespace) -> int:
    """固定したNER modelを取得・検証し、local実行可能な状態にする。

    Gatewayとは別の明示操作にすることで、利用者が入力した文字列を契機とするdownloadを
    防ぎ、次回起動時は検証済みのlocal artifactだけを読み込めるようにする。
    """
    from securitymasker.models_fetch import fetch

    model, revision = args.model, args.revision
    if not model or not revision:
        # 利用者の再入力を避けるため、設定済みの固定値へfallbackする。
        ner = _load(args.config).ner
        model, revision = model or ner.model, revision or ner.revision
    if not model or not revision:
        print("error: no NER model/revision configured to load", file=sys.stderr)
        return 2

    result = fetch(model, revision, allow_unverified=args.allow_unverified)
    print(f"model ready: {result.model}@{result.revision}")
    for name in sorted(result.verified):
        print(f"  verified   {name}")
    if not result.verified and args.allow_unverified:
        print("  WARNING: accepted UNVERIFIED — no artifact manifest on record")
    return 0 if result.ok else 1


def _serve_gateway(
    app: Any,
    *,
    host: str,
    port: int,
    mode: str,
    max_message_bytes: int,
) -> int:
    """先にloopback socketを確保し、起動・bind失敗・終了を製品logへ記録する。"""
    import uvicorn

    log = get_logger()
    server_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        # Uvicorn固有のlifecycle/access logは製品level契約と形式が異なるため抑止する。
        # bindとruntimeの失敗は下でSecurityMaskerの固定eventとして記録する。
        log_level="critical",
        access_log=False,
        ws_max_size=max_message_bytes,
        ws_per_message_deflate=False,
    )
    try:
        bound_socket = server_config.bind_socket()
    except SystemExit:
        log.error("gateway_bind_failed", host=host, port=port)
        return 1

    server = uvicorn.Server(server_config)
    log.info(
        "gateway_started",
        url=f"http://{host}:{port}",
        mode=mode,
    )
    try:
        server.run(sockets=[bound_socket])
    except KeyboardInterrupt:  # pragma: no cover - 実processのsignal testで確認する
        return 0
    except Exception as exc:  # noqa: BLE001 - 原文を含み得る例外本文はlogへ出さない
        log.error("gateway_runtime_error", reason=type(exc).__name__)
        return 1
    finally:
        bound_socket.close()
        log.info("gateway_stopped", mode=mode)
    return 0


def cmd_gateway(args: argparse.Namespace) -> int:
    """解決済みconfigを環境へ固定してSecurityMasker proxyを起動する。"""
    from securitymasker.gateway.app import create_app
    from securitymasker.gateway.websocket import MAX_MESSAGE_BYTES

    # config自体が壊れていて閾値を読めない場合にもERRORを表示できる既定設定。
    configure_logging()
    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except ConfigError as exc:
        get_logger().error("gateway_configuration_error", detail=str(exc))
        return 1

    configure_logging(config.logging.level)
    os.environ["SECURITYMASKER_CONFIG"] = str(config_path)

    runtime = config.runtime
    if config.version != 1 or runtime is None:
        get_logger().error(
            "gateway_configuration_error",
            detail="Gateway requires a version 1 securitymasker.config",
        )
        return 1
    host = args.host or runtime.host
    port = args.port or runtime.port
    product_mode = args.mode or runtime.mode
    os.environ["SECURITYMASKER_PRODUCT_MODE"] = product_mode

    # create_app()は必須設定を再検証し、不足時は外部へ接続せず起動を拒否する。
    try:
        app = create_app()
    except ConfigError as exc:
        get_logger().error("gateway_configuration_error", detail=str(exc))
        return 1
    except SessionError as exc:
        get_logger().error("gateway_store_error", detail=str(exc))
        return 1

    try:
        return _serve_gateway(
            app,
            host=host,
            port=port,
            mode=product_mode,
            max_message_bytes=MAX_MESSAGE_BYTES,
        )
    finally:
        app_state = getattr(app, "state", None)
        app_runtime = getattr(app_state, "runtime", None)
        close_store = getattr(getattr(app_runtime, "store", None), "close", None)
        if callable(close_store):
            close_store()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="securitymasker", description="SecurityMasker CLI")
    parser.add_argument("--version", action="version", version=version_text(__version__))
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--config",
            default=None,
            help="path to securitymasker.config (default: environment or adjacent file)",
        )

    p_init_layout = sub.add_parser(
        "init", help="create config, dictionary, state directory and master key"
    )
    p_init_layout.add_argument(
        "--directory",
        default=None,
        help="target directory (default: beside the executable or root script)",
    )
    p_init_layout.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="delete and recreate the managed config, dictionary and state (requires --directory)",
    )
    p_init_layout.add_argument(
        "--mode",
        choices=["chatgpt", "claude"],
        default="chatgpt",
        help="product mode written to the new config (default: chatgpt)",
    )
    p_init_layout.add_argument(
        "--port",
        type=int,
        default=4000,
        help="loopback port written to the new config (default: 4000)",
    )
    p_init_layout.set_defaults(func=cmd_init)

    p_config_check = sub.add_parser("config-check", help="validate the config")
    add_config(p_config_check)
    p_config_check.set_defaults(func=cmd_config_check)

    p_entities = sub.add_parser(
        "entities", help="list configured entities and patterns without their values"
    )
    add_config(p_entities)
    p_entities.set_defaults(func=cmd_entities)

    p_preview = sub.add_parser(
        "preview", help="mask text locally with the Gateway pipeline (no external send)"
    )
    p_preview.add_argument(
        "text",
        nargs="?",
        default=None,
        help="text to mask locally (default: standard input); the original is not printed",
    )
    add_config(p_preview)
    p_preview.set_defaults(func=cmd_preview)

    p_client_config = sub.add_parser(
        "client-config", help="print the manual client settings for the configured mode"
    )
    add_config(p_client_config)
    p_client_config.set_defaults(func=cmd_client_config)

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
    p_gateway.add_argument(
        "--mode",
        choices=["chatgpt", "claude"],
        default=None,
        help="temporarily override runtime.mode from the config",
    )
    p_gateway.add_argument(
        "--host",
        choices=["127.0.0.1", "::1", "localhost"],
        default=None,
        help="temporarily override the loopback host from the config",
    )
    p_gateway.add_argument(
        "--port",
        type=int,
        default=None,
        help="temporarily override runtime.port from the config",
    )
    add_config(p_gateway)
    p_gateway.set_defaults(func=cmd_gateway)

    p_model_load = sub.add_parser(
        "model-load", help="download and verify the pinned NER model for the next start"
    )
    p_model_load.add_argument(
        "--model",
        default=None,
        help="model ID (default: detectors.japanese_ner.model from the config)",
    )
    p_model_load.add_argument(
        "--revision",
        default=None,
        help="model revision (default: detectors.japanese_ner.revision from the config)",
    )
    p_model_load.add_argument(
        "--allow-unverified",
        action="store_true",
        help="DANGEROUS: accept a model with no artifact manifest",
    )
    add_config(p_model_load)
    p_model_load.set_defaults(func=cmd_model_load)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    gateway_options = ("--config", "--host", "--mode", "--port")
    if any(
        arguments[0] == option or arguments[0].startswith(f"{option}=")
        for option in gateway_options
    ):
        parser.error("gateway options require the explicit 'gateway' command")
    args = parser.parse_args(arguments)
    try:
        return int(args.func(args))
    except SecurityMaskerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
