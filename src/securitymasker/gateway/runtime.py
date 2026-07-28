"""単一利用者・単一modeのGateway runtime。"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from securitymasker.config import (
    SecurityMaskerConfig,
    build_engine,
    load_config,
    parse_duration,
)
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.metrics import GatewayTelemetry
from securitymasker.sessions.sqlite import SQLiteSessionStore
from securitymasker.sessions.store import SessionStore

# Codex（ChatGPT auth）backend。clientのOAuth bearerを透過する。
DEFAULT_OPENAI_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
PRODUCT_MODES = frozenset({"chatgpt", "claude"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_upstream_url(value: str, *, provider: str) -> str:
    """providerの公式endpointまたはloopbackだけをupstreamとして受理する。

    環境変数で任意のHTTPS hostへ差し替えられると、認証headerを誤った接続先へ
    転送し得る。local integration test用のloopbackは許可するが、その他のhostは
    HTTPSであっても起動前に拒否する。
    """
    expected_by_provider = {
        "openai": DEFAULT_OPENAI_UPSTREAM,
        "anthropic": DEFAULT_ANTHROPIC_UPSTREAM,
    }
    expected = expected_by_provider.get(provider)
    if expected is None:
        raise ConfigError("unknown upstream provider")

    try:
        parts = urlsplit(value)
        # ``parts.port``は範囲外・非数値portでValueErrorを送出する。
        _ = parts.port
    except ValueError:
        raise ConfigError(f"{provider} upstream has an invalid port") from None

    if parts.scheme not in {"http", "https"}:
        raise ConfigError(f"{provider} upstream scheme must be http or https")
    if not parts.hostname:
        raise ConfigError(f"{provider} upstream must include a host")
    if parts.username or parts.password:
        raise ConfigError(f"{provider} upstream must not contain credentials")
    if parts.query or parts.fragment:
        raise ConfigError(f"{provider} upstream must not contain a query or fragment")

    normalized = value.rstrip("/")
    if normalized == expected:
        return normalized
    if parts.hostname in _LOOPBACK_HOSTS:
        return normalized
    raise ConfigError(
        f"{provider} upstream must be the official endpoint or a loopback test endpoint"
    )


def _build_store(
    config: SecurityMaskerConfig, *, product_mode: str
) -> SQLiteSessionStore:
    """configが明示するDB/keyから標準SQLite storeだけを構築する。"""
    if config.version != 1 or config.state is None or config.runtime is None:
        raise ConfigError("Gateway requires version 1 runtime and state settings")
    return SQLiteSessionStore(
        config.state.database,
        config.state.key,
        mode=product_mode,
        idle_ttl=parse_duration(config.defaults.session_idle_ttl),
        absolute_ttl=parse_duration(config.defaults.session_absolute_ttl),
    )


class GatewayRuntime:
    """一つのprovider routeと一つのlocal SQLiteを所有するruntime。"""

    def __init__(
        self,
        engine: MaskingEngine,
        store: SessionStore,
        *,
        openai_upstream: str,
        anthropic_upstream: str,
        product_mode: str,
        telemetry: GatewayTelemetry | None = None,
    ) -> None:
        if product_mode not in PRODUCT_MODES:
            raise ConfigError(
                "product mode must be 'chatgpt' or 'claude'; combined mode is not supported"
            )
        self.engine = engine
        self.store = store
        self.openai_upstream = validate_upstream_url(
            openai_upstream, provider="openai"
        )
        self.anthropic_upstream = validate_upstream_url(
            anthropic_upstream, provider="anthropic"
        )
        self.product_mode = product_mode
        self.telemetry = telemetry or GatewayTelemetry()

    @classmethod
    def from_env(
        cls,
        *,
        engine: MaskingEngine | None = None,
        config: SecurityMaskerConfig | None = None,
    ) -> GatewayRuntime:
        """必須configからfail-closedでruntimeを構築する。"""
        config_path = os.environ.get("SECURITYMASKER_CONFIG")
        if not config_path:
            raise ConfigError("SECURITYMASKER_CONFIG is required")
        if config is None:
            config = load_config(config_path)
        if config.version != 1 or config.runtime is None or config.state is None:
            raise ConfigError("Gateway requires a version 1 securitymasker.config")

        product_mode = os.environ.get(
            "SECURITYMASKER_PRODUCT_MODE", config.runtime.mode
        )
        if product_mode not in PRODUCT_MODES:
            raise ConfigError(
                "SECURITYMASKER_PRODUCT_MODE must be 'chatgpt' or 'claude'"
            )
        if engine is None:
            engine = build_engine(config)
        openai_upstream = validate_upstream_url(
            os.environ.get(
                "SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM
            ),
            provider="openai",
        )
        anthropic_upstream = validate_upstream_url(
            os.environ.get(
                "SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM
            ),
            provider="anthropic",
        )
        store = _build_store(config, product_mode=product_mode)
        return cls(
            engine,
            store,
            openai_upstream=openai_upstream,
            anthropic_upstream=anthropic_upstream,
            product_mode=product_mode,
        )
