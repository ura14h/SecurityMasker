"""Gateway runtime：masking engine、session store、upstream endpoint。

Built from ``SECURITYMASKER_CONFIG`` (the dictionary/policy YAML). If unset, the
engine is ``None`` and the proxy forwards transparently (no masking). Upstream
bases are configurable; defaults target Codex's ChatGPT backend and the Anthropic
API. For API-key OpenAI, set ``SECURITYMASKER_OPENAI_UPSTREAM=https://api.openai.com/v1``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from securitymasker.config import build_engine, load_config, parse_duration
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.gateway.identity import (
    DEFAULT_MAX_SKEW_SECONDS,
    MODE_LOCAL,
    VALID_MODES,
    normalize_mode,
)
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import SessionStore

# Codex（ChatGPT auth）backend。clientのOAuth bearerを透過する（§25）。
DEFAULT_OPENAI_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_ANTHROPIC_UPSTREAM = "https://api.anthropic.com"


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    # Compose／demoのservice名はloopbackとせず、実loopbackだけを認める。
    return host in LOOPBACK_HOSTS


def _build_store(*, idle_ttl: Any, absolute_ttl: Any) -> SessionStore:
    """``SECURITYMASKER_STORE``からsession storeを選択する（P1-9）。

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
        tenant_auth_secret: str | None = None,
        max_clock_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
        require_assertion_timestamp: bool = True,
    ) -> None:
        self.engine = engine
        self.store = store
        self.openai_upstream = openai_upstream.rstrip("/")
        self.anthropic_upstream = anthropic_upstream.rstrip("/")
        # tenant分離mode（doc/06 P0-9）。`local`は暗黙の単一tenant。
        # "multitenant" = tenant id proven by an HMAC the authenticator computes
        # with ``tenant_auth_secret`` (a bare header is never trusted).
        self.mode = mode
        self.tenant_header = tenant_header
        self.tenant_auth_secret = tenant_auth_secret
        self.max_clock_skew_seconds = max_clock_skew_seconds
        # 取得済みproofの無期限replayを防ぐためtimestampを既定で必須にする。
        # replayable for the lifetime of the secret (ADR-0008).
        self.require_assertion_timestamp = require_assertion_timestamp

    @classmethod
    def from_env(cls, *, engine: MaskingEngine | None = None,
                 config: Any = None) -> GatewayRuntime:
        """環境変数からruntimeを構築する。

        ``engine``/``config`` let a caller that has ALREADY built them (doctor)
        hand them over instead of paying for a second construction — with NER
        enabled that is a second ~1GB model load for one diagnosis.
        """
        # fail-closed起動（§26、doc/06 P0-1）。masking configは必須。
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
            if config is None:
                config = load_config(config_path)
            if engine is None:
                engine = build_engine(config)
            store = _build_store(
                idle_ttl=parse_duration(config.defaults.session_idle_ttl),
                absolute_ttl=parse_duration(config.defaults.session_absolute_ttl),
            )
        # `multitenant` was the old name for tenant-only isolation; normalize_mode
        # keeps it working while giving every consumer one definition to read.
        mode = normalize_mode(os.environ.get("SECURITYMASKER_MODE", MODE_LOCAL))
        if mode not in VALID_MODES:
            raise ConfigError(
                "SECURITYMASKER_MODE must be 'local', 'tenant' or 'tenant_user' "
                f"(got {mode!r})"
            )

        openai_upstream = os.environ.get(
            "SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM
        )
        anthropic_upstream = os.environ.get(
            "SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM
        )

        if engine is None:
            # raw bodyを転送するdev transparent modeは実provider経路に置かない。
            # front of a real provider (doc/06 P0-1). Loopback upstreams only.
            for name, url in (("OPENAI", openai_upstream), ("ANTHROPIC", anthropic_upstream)):
                if not _is_loopback(url):
                    raise ConfigError(
                        f"SECURITYMASKER_DEV_TRANSPARENT=1 forwards UNMASKED bodies and "
                        f"refuses non-loopback upstreams; SECURITYMASKER_{name}_UPSTREAM "
                        f"points at {urlsplit(url).hostname!r}. Set a masking config instead."
                    )

        tenant_auth_secret = os.environ.get("SECURITYMASKER_TENANT_AUTH_SECRET")
        if mode != MODE_LOCAL and not tenant_auth_secret:
            # secretなしではtenant headerを検証できず任意clientが詐称できる。
            # could claim any tenant (doc/06 P0-9).
            raise ConfigError(
                f"SECURITYMASKER_MODE={mode} requires SECURITYMASKER_TENANT_AUTH_SECRET; "
                "the trusted authenticator signs the tenant (and user) with it "
                "(HMAC-SHA256 hex in X-SecurityMasker-Tenant-Auth)."
            )
        return cls(
            engine,
            store,
            openai_upstream=openai_upstream,
            anthropic_upstream=anthropic_upstream,
            mode=mode,
            tenant_header=os.environ.get(
                "SECURITYMASKER_TENANT_HEADER", "x-securitymasker-tenant-id"
            ),
            tenant_auth_secret=tenant_auth_secret,
            max_clock_skew_seconds=int(
                os.environ.get("SECURITYMASKER_MAX_CLOCK_SKEW_SECONDS",
                               DEFAULT_MAX_SKEW_SECONDS)
            ),
            require_assertion_timestamp=(
                os.environ.get("SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS") != "1"
            ),
        )
