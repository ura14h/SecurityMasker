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
        config_path = os.environ.get("SECURITYMASKER_CONFIG")
        engine = build_engine(load_config(config_path)) if config_path else None
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
