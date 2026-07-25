"""Gateway runtime: masking engine + session store + upstream endpoints.

Built from ``SECURITYMASKER_CONFIG`` (the dictionary/policy YAML). If unset, the
engine is ``None`` and the proxy forwards transparently (no masking). Upstream
bases are configurable; defaults target Codex's ChatGPT backend and the Anthropic
API. For API-key OpenAI, set ``SECURITYMASKER_OPENAI_UPSTREAM=https://api.openai.com/v1``.
"""

from __future__ import annotations

import os
from typing import Any

from securitymasker.config import build_engine, load_config, parse_duration
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import SessionStore

# Codex (ChatGPT auth) backend; the client's OAuth bearer is passed through (§25).
DEFAULT_OPENAI_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_ANTHROPIC_UPSTREAM = "https://api.anthropic.com"


def _build_store(*, idle_ttl: Any, absolute_ttl: Any) -> SessionStore:
    """Select the session store from ``SECURITYMASKER_STORE`` (P1-9).

    Default ``memory`` is single-process. ``redis`` shares state across workers and
    is required for multi-worker deployments; if selected but unavailable, fail
    startup (fail-closed) rather than silently fall back to an unshared store.
    """
    backend = os.environ.get("SECURITYMASKER_STORE", "memory").lower()
    if backend == "memory":
        return InMemorySessionStore(idle_ttl=idle_ttl, absolute_ttl=absolute_ttl)
    if backend == "redis":
        url = os.environ.get("SECURITYMASKER_REDIS_URL")
        if not url:
            raise ConfigError("SECURITYMASKER_STORE=redis requires SECURITYMASKER_REDIS_URL")
        try:
            from redis.asyncio import from_url

            from securitymasker.sessions.redis import RedisSessionStore
        except ImportError as exc:  # optional dependency
            raise ConfigError(
                "SECURITYMASKER_STORE=redis requires the 'redis' package "
                "(pip install -e '.[redis]')"
            ) from exc
        client = from_url(url)
        return RedisSessionStore(client, idle_ttl=idle_ttl, absolute_ttl=absolute_ttl)
    raise ConfigError(f"unknown SECURITYMASKER_STORE {backend!r} (use 'memory' or 'redis')")


class GatewayRuntime:
    def __init__(
        self,
        engine: MaskingEngine | None,
        store: SessionStore,
        *,
        openai_upstream: str,
        anthropic_upstream: str,
        mode: str = "local",
        tenant_header: str = "x-securitymasker-tenant-id",
    ) -> None:
        self.engine = engine
        self.store = store
        self.openai_upstream = openai_upstream.rstrip("/")
        self.anthropic_upstream = anthropic_upstream.rstrip("/")
        # Tenant isolation mode (doc/06 P0-9): "local" = one implicit tenant;
        # "multitenant" = tenant read from a header a trusted authenticator sets.
        self.mode = mode
        self.tenant_header = tenant_header

    @classmethod
    def from_env(cls) -> GatewayRuntime:
        # Fail-closed startup (§26, doc/06 P0-1): a masking config is REQUIRED. The
        # engine-less transparent mode exists only as an explicit, dev-only opt-in.
        config_path = os.environ.get("SECURITYMASKER_CONFIG")
        dev_transparent = os.environ.get("SECURITYMASKER_DEV_TRANSPARENT") == "1"
        if not config_path:
            if not dev_transparent:
                raise ConfigError(
                    "SECURITYMASKER_CONFIG is required (a masking dictionary YAML). "
                    "Set SECURITYMASKER_DEV_TRANSPARENT=1 to run without masking "
                    "(development only; do not point at real providers)."
                )
            engine = None
            store: SessionStore = InMemorySessionStore()
        else:
            config = load_config(config_path)
            engine = build_engine(config)
            store = _build_store(
                idle_ttl=parse_duration(config.defaults.session_idle_ttl),
                absolute_ttl=parse_duration(config.defaults.session_absolute_ttl),
            )
        mode = os.environ.get("SECURITYMASKER_MODE", "local")
        if mode not in {"local", "multitenant"}:
            raise ConfigError("SECURITYMASKER_MODE must be 'local' or 'multitenant'")
        return cls(
            engine,
            store,
            openai_upstream=os.environ.get(
                "SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM
            ),
            anthropic_upstream=os.environ.get(
                "SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM
            ),
            mode=mode,
            tenant_header=os.environ.get(
                "SECURITYMASKER_TENANT_HEADER", "x-securitymasker-tenant-id"
            ),
        )
