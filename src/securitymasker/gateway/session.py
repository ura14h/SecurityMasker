"""request header／bodyからのsession解決。

The proxy masks (request) and restores (response) in a *single* handler
invocation, so one resolved session covers the whole turn. Cross-turn consistency
comes from a stable session id. Priority prefers a stable identifier
over the per-turn ``previous_response_id``, which changes every turn and would
otherwise fork the alias table each time:

    1. 明示的な``X-SecurityMasker-Session-ID``
    2. Claude Codeのstable ``x-claude-code-session-id`` header
    3. a stable ``session-id`` / ``thread-id`` header
    4. ``previous_response_id`` (only as a last resort before ephemeral)
    5. a fresh ephemeral id

単一利用者のlocal runtimeなので、このmoduleは「どのsessionか」だけを解決する。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SESSION_HEADER = "x-securitymasker-session-id"


@dataclass(frozen=True)
class ResolvedSession:
    session_id: str
    stable: bool  # False => ephemeral (no durable identifier was available)
    # Present when the request referenced a prior response; the caller resolves it
    # against the store's response bindings to continue that session (P1-1).
    previous_response_id: str | None = None


def resolve_session(
    headers: Mapping[str, str], body: dict[str, Any] | None = None
) -> ResolvedSession:
    h = {k.lower(): v for k, v in headers.items()}

    explicit = h.get(SESSION_HEADER)
    if explicit:
        return ResolvedSession(explicit, stable=True)

    claude_session = h.get("x-claude-code-session-id")
    if claude_session:
        return ResolvedSession(
            f"x-claude-code-session-id:{claude_session}", stable=True
        )

    for header in ("session-id", "thread-id"):
        value = h.get(header)
        if value:
            return ResolvedSession(f"{header}:{value}", stable=True)

    if body is not None:
        prev = body.get("previous_response_id")
        if isinstance(prev, str) and prev:
            # NOT stable on its own: the id changes every turn, so using it as the
            # session key would fork the alias table each turn. It is only a lookup
            # handle — the caller resolves it against the store's response bindings
            # and, on a hit, continues the ORIGINAL session.
            return ResolvedSession(
                f"eph:{uuid.uuid4()}", stable=False, previous_response_id=prev
            )

    return ResolvedSession(f"eph:{uuid.uuid4()}", stable=False)
