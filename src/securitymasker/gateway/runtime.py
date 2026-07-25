"""Gateway runtime: masking engine + session store + upstream endpoints.

Built from ``SECURITYMASKER_CONFIG`` (the dictionary/policy YAML). If unset, the
engine is ``None`` and the proxy forwards transparently (no masking). Upstream
bases are configurable; defaults target Codex's ChatGPT backend and the Anthropic
API. For API-key OpenAI, set ``SECURITYMASKER_OPENAI_UPSTREAM=https://api.openai.com/v1``.
"""

from __future__ import annotations

import os

from securitymasker.config import build_engine, load_config
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import SessionStore

# Codex (ChatGPT auth) backend; the client's OAuth bearer is passed through (§25).
DEFAULT_OPENAI_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_ANTHROPIC_UPSTREAM = "https://api.anthropic.com"


class GatewayRuntime:
    def __init__(
        self,
        engine: MaskingEngine | None,
        store: SessionStore,
        *,
        openai_upstream: str,
        anthropic_upstream: str,
    ) -> None:
        self.engine = engine
        self.store = store
        self.openai_upstream = openai_upstream.rstrip("/")
        self.anthropic_upstream = anthropic_upstream.rstrip("/")

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
        else:
            engine = build_engine(load_config(config_path))
        return cls(
            engine,
            InMemorySessionStore(),
            openai_upstream=os.environ.get(
                "SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM
            ),
            anthropic_upstream=os.environ.get(
                "SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM
            ),
        )
