"""Session + tenant resolution from request headers/body (§7, doc/06 P0-9/P1-1).

The proxy masks (request) and restores (response) in a *single* handler
invocation, so one resolved session covers the whole turn. Cross-turn consistency
comes from a stable session id. Priority (doc/06 P1-1) prefers a stable identifier
over the per-turn ``previous_response_id``, which changes every turn and would
otherwise fork the alias table each time:

    1. ``X-SecurityMasker-Session-ID`` (set by ``securitymasker run``)
    2. a stable ``session-id`` / ``thread-id`` header
    3. ``previous_response_id`` (only as a last resort before ephemeral)
    4. a fresh ephemeral id

Tenant (doc/06 P0-9): in ``local`` mode there is one implicit tenant. In
``multitenant`` mode the tenant is read from a configured header that a trusted
authenticator (not the end client) is responsible for setting; if it is absent the
caller must fail closed rather than share one tenant's table with another.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

SESSION_HEADER = "x-securitymasker-session-id"
DEFAULT_TENANT_HEADER = "x-securitymasker-tenant-id"
# Proof header set by the trusted authenticator, never by the end client.
TENANT_AUTH_HEADER = "x-securitymasker-tenant-auth"
LOCAL_TENANT = "local"


@dataclass(frozen=True)
class ResolvedSession:
    session_id: str
    stable: bool  # False => ephemeral (no durable identifier was available)


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
            # Weakly stable: it changes per turn, so continuity across >2 turns
            # needs an explicit session id. Good enough to bind a single follow-up.
            return ResolvedSession(f"prev:{prev}", stable=True)

    return ResolvedSession(f"eph:{uuid.uuid4()}", stable=False)


def resolve_tenant(
    mode: str,
    tenant_header: str,
    headers: Mapping[str, str],
    *,
    auth_secret: str | None = None,
    auth_header: str = TENANT_AUTH_HEADER,
) -> str | None:
    """Resolve the tenant, or ``None`` if it cannot be *proven* (=> block).

    In multitenant mode the tenant id alone is untrusted — any client can set a
    header. The fronting authenticator (which alone knows ``auth_secret``) must
    also send ``HMAC-SHA256(secret, tenant_id)`` hex in ``auth_header``; the tenant
    is accepted only if that proof verifies for *this* tenant id, so a client can
    neither invent a tenant nor replay another tenant's proof (doc/06 P0-9).
    """
    if mode != "multitenant":
        return LOCAL_TENANT
    h = {k.lower(): v for k, v in headers.items()}
    tenant = h.get(tenant_header.lower())
    if not tenant:
        return None
    if not auth_secret:
        return None  # misconfigured: never trust a bare header
    presented = h.get(auth_header.lower(), "")
    expected = hmac.new(auth_secret.encode(), tenant.encode(), sha256).hexdigest()
    if not presented or not hmac.compare_digest(presented, expected):
        return None
    return tenant


def namespaced_key(tenant: str, session_id: str) -> str:
    """Tenant-namespaced store key so one session id can't cross tenants (P0-9)."""
    return f"{tenant}\x1f{session_id}"


# Backwards-compatible shim used by older call sites/tests.
def resolve_session_id(headers: Mapping[str, str], body: dict[str, Any] | None = None) -> str:
    return resolve_session(headers, body).session_id
