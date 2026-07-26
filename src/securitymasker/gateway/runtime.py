"""単一利用者・単一modeのGateway runtime。"""

from __future__ import annotations

import os

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


def _build_store(
    config: SecurityMaskerConfig, *, product_mode: str
) -> SQLiteSessionStore:
    """v2 configが明示するDB/keyから標準SQLite storeだけを構築する。"""
    if config.version != 2 or config.state is None or config.runtime is None:
        raise ConfigError("Gateway requires version 2 runtime and state settings")
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
        self.openai_upstream = openai_upstream.rstrip("/")
        self.anthropic_upstream = anthropic_upstream.rstrip("/")
        self.product_mode = product_mode
        self.telemetry = telemetry or GatewayTelemetry()

    @classmethod
    def from_env(
        cls,
        *,
        engine: MaskingEngine | None = None,
        config: SecurityMaskerConfig | None = None,
    ) -> GatewayRuntime:
        """必須のv2 configからfail-closedでruntimeを構築する。"""
        config_path = os.environ.get("SECURITYMASKER_CONFIG")
        if not config_path:
            raise ConfigError("SECURITYMASKER_CONFIG is required")
        if config is None:
            config = load_config(config_path)
        if config.version != 2 or config.runtime is None or config.state is None:
            raise ConfigError("Gateway requires a version 2 securitymasker.config")

        product_mode = os.environ.get(
            "SECURITYMASKER_PRODUCT_MODE", config.runtime.mode
        )
        if product_mode not in PRODUCT_MODES:
            raise ConfigError(
                "SECURITYMASKER_PRODUCT_MODE must be 'chatgpt' or 'claude'"
            )
        if engine is None:
            engine = build_engine(config)
        store = _build_store(config, product_mode=product_mode)
        return cls(
            engine,
            store,
            openai_upstream=os.environ.get(
                "SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM
            ),
            anthropic_upstream=os.environ.get(
                "SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM
            ),
            product_mode=product_mode,
        )
