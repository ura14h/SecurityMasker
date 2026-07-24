"""Runtime wiring shared by the LiteLLM integration (kept out of litellm.py).

Builds the engine + session store from ``SECURITYMASKER_CONFIG`` and resolves a
stable session id from a request so masking (pre-call) and restoration (post-call /
streaming) use the same session (§7). Importing this module must not require
LiteLLM.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from securitymasker.config import build_engine, load_config
from securitymasker.engine import MaskingEngine
from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import SessionStore

SESSION_HEADER = "x-securitymasker-session-id"


class Runtime:
    """Holds the configured engine and session store for the gateway process."""

    def __init__(self, engine: MaskingEngine, store: SessionStore) -> None:
        self.engine = engine
        self.store = store

    @classmethod
    def from_env(cls) -> Runtime | None:
        path = os.environ.get("SECURITYMASKER_CONFIG")
        if not path:
            return None
        config = load_config(path)
        return cls(build_engine(config), InMemorySessionStore())


def extract_headers(data: dict[str, Any]) -> dict[str, str]:
    """Best-effort header extraction across LiteLLM request shapes (lowercased keys)."""
    for path in (
        ("proxy_server_request", "headers"),
        ("metadata", "headers"),
        ("litellm_metadata", "headers"),
    ):
        node: Any = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            return {str(k).lower(): str(v) for k, v in node.items()}
    return {}


def resolve_session_id(data: dict[str, Any]) -> str:
    """Pick a stable session id (§7 priority: header → conversation id → ephemeral).

    The chosen id is stashed in ``data['metadata']`` so later hooks for the same
    request resolve the same session.
    """
    metadata = data.setdefault("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}
    stashed = metadata.get("securitymasker_session_id")
    if isinstance(stashed, str):
        return stashed

    headers = extract_headers(data)
    session_id = headers.get(SESSION_HEADER)
    if not session_id:
        prev = data.get("previous_response_id")
        if isinstance(prev, str) and prev:
            session_id = f"prev:{prev}"
    if not session_id:
        conv = data.get("conversation_id") or data.get("thread_id")
        if isinstance(conv, str) and conv:
            session_id = f"conv:{conv}"
    if not session_id:
        session_id = f"eph:{uuid.uuid4()}"

    if isinstance(data.get("metadata"), dict):
        data["metadata"]["securitymasker_session_id"] = session_id
    return session_id
