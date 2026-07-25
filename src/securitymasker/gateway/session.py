"""Session resolution from request headers/body (§7, doc/06 P1-1).

The proxy masks (request) and restores (response) in a *single* handler
invocation, so one resolved session covers the whole turn. Cross-turn consistency
comes from a stable session id. Priority (doc/06 P1-1) prefers a stable identifier
over the per-turn ``previous_response_id``, which changes every turn and would
otherwise fork the alias table each time:

    1. ``X-SecurityMasker-Session-ID`` (set by ``securitymasker run``)
    2. a stable ``session-id`` / ``thread-id`` header
    3. ``previous_response_id`` (only as a last resort before ephemeral)
    4. a fresh ephemeral id

Caller identity lives in ``gateway.identity``; this module only answers "which
session", and the two are composed by ``namespaced_key``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from securitymasker.gateway.identity import Identity

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
            # and, on a hit, continues the ORIGINAL session (doc/06 P1-1).
            return ResolvedSession(
                f"eph:{uuid.uuid4()}", stable=False, previous_response_id=prev
            )

    return ResolvedSession(f"eph:{uuid.uuid4()}", stable=False)


def namespaced_key(identity: Identity | str, session_id: str) -> str:
    """Identity-namespaced store key, so a session id cannot cross a boundary.

    The namespace is length-prefixed (see ``Identity.namespace``), so no
    combination of tenant/user/session values can be made to collide with a
    different combination. Two callers presenting the SAME session id therefore
    still get separate alias tables whenever their identities differ (§8, P0-9).

    A plain string is accepted for internal, identity-free keys (the readiness
    probe); it is namespaced under a reserved prefix that no verified identity can
    produce, so it can never alias a real caller's key.
    """
    if isinstance(identity, str):
        return f"\x1e{identity}\x1f{session_id}"
    return f"{identity.namespace}\x1f{session_id}"

