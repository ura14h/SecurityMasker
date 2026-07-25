"""Session resolution from request headers/body (§7).

Unlike the LiteLLM path, the proxy handles mask (request) and restore (response)
in a *single* handler invocation, so one resolved session covers the whole turn —
no pre/post correlation problem. Cross-turn consistency comes from a stable session
id: the ``X-SecurityMasker-Session-ID`` header (set by ``securitymasker run``),
then Codex's ``session-id``/``thread-id`` or ``previous_response_id`` (§7 priority).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

SESSION_HEADER = "x-securitymasker-session-id"


def resolve_session_id(headers: Mapping[str, str], body: dict[str, Any] | None = None) -> str:
    h = {k.lower(): v for k, v in headers.items()}

    explicit = h.get(SESSION_HEADER)
    if explicit:
        return explicit

    if body is not None:
        prev = body.get("previous_response_id")
        if isinstance(prev, str) and prev:
            return f"prev:{prev}"

    for header in ("session-id", "thread-id"):
        value = h.get(header)
        if value:
            return f"{header}:{value}"

    return f"eph:{uuid.uuid4()}"
