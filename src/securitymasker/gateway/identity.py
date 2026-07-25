"""Caller identity: who a request is masked *for* (§7, §8, doc/06 P0-9).

Three modes, chosen explicitly at startup so nobody gets multi-user isolation by
accident or believes they have it when they do not:

``local``
    One implicit tenant, one implicit user. The single-workstation install.

``tenant``
    Several tenants, one trust domain each. A trusted authenticator in front of
    the proxy asserts the tenant. Users *within* a tenant share an alias table —
    correct for one-customer-per-tenant, wrong for mutually distrusting users, so
    the mode says so in its name rather than implying more than it delivers.

``tenant_user``
    Tenant AND user are asserted together and isolated separately.

The identity is never taken from a bare header: any client can set one. The
authenticator signs a **versioned canonical payload** with a shared secret, and we
verify it in constant time. Signing the fields jointly (not separately) is what
stops a caller from pairing tenant A's proof with user B's — the assertion binds
them as a unit. Length-prefixed encoding keeps ``tenant="a", user="b:c"`` from
colliding with ``tenant="a:b", user="c"``.

Everything here is pure: headers in, a verified identity or a refusal out. The
gateway handler stays free of crypto, and the store receives an already-validated
namespace rather than parsing HTTP itself.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

# Header names. The proof is produced by the authenticator, never by the client.
TENANT_HEADER = "x-securitymasker-tenant-id"
USER_HEADER = "x-securitymasker-user-id"
AUTH_HEADER = "x-securitymasker-tenant-auth"
TIMESTAMP_HEADER = "x-securitymasker-auth-timestamp"

LOCAL_TENANT = "local"
LOCAL_USER = "local"

# Bump when the signed payload's shape changes; old proofs then stop verifying
# instead of being reinterpreted under new rules.
ASSERTION_VERSION = "v2"

MODE_LOCAL = "local"
MODE_TENANT = "tenant"
MODE_TENANT_USER = "tenant_user"
VALID_MODES = frozenset({MODE_LOCAL, MODE_TENANT, MODE_TENANT_USER})

# How far a proof's timestamp may be from our clock. Generous enough for ordinary
# skew, short enough that a captured proof is not replayable indefinitely.
DEFAULT_MAX_SKEW_SECONDS = 300


class IdentityError(Exception):
    """Identity could not be established. Callers MUST fail closed (§26).

    The message is deliberately coarse — it never echoes the presented proof,
    the claimed identity, or the secret (§25).
    """


@dataclass(frozen=True)
class Identity:
    """A verified caller. ``user`` is ``LOCAL_USER`` when the mode has no users."""

    tenant: str
    user: str

    @property
    def namespace(self) -> str:
        """Store-key prefix. Length-prefixed so components cannot run together."""
        return f"{len(self.tenant)}:{self.tenant}\x1f{len(self.user)}:{self.user}"


def canonical_payload(
    tenant: str, user: str, timestamp: str = "", *, version: str = ASSERTION_VERSION
) -> bytes:
    """The exact bytes an authenticator signs.

    Length-prefixed and version-tagged: unambiguous under any tenant/user values,
    and a future format change cannot be replayed against this one.
    """
    parts = [version, tenant, user, timestamp]
    return "\x1f".join(f"{len(p)}:{p}" for p in parts).encode("utf-8")


def sign(secret: str, tenant: str, user: str, timestamp: str = "") -> str:
    """Produce the proof an authenticator sends (also used by tests/tooling)."""
    return hmac.new(secret.encode(), canonical_payload(tenant, user, timestamp),
                    sha256).hexdigest()


def _require(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "").strip()
    if not value:
        raise IdentityError(f"{name} is required in this mode but was not provided")
    return value


def resolve_identity(
    mode: str,
    headers: Mapping[str, str],
    *,
    auth_secret: str | None = None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
    now: float | None = None,
) -> Identity:
    """Verify the caller's identity, or raise ``IdentityError``.

    ``local`` needs no headers. The other modes require a proof that verifies over
    the *joint* canonical payload, so neither field can be swapped independently.
    """
    if mode == MODE_LOCAL:
        return Identity(LOCAL_TENANT, LOCAL_USER)
    if mode not in VALID_MODES:
        raise IdentityError(f"unknown identity mode {mode!r}")
    if not auth_secret:
        # Refusing here rather than trusting the header is the whole point.
        raise IdentityError("identity mode requires a configured authentication secret")

    lower = {k.lower(): v for k, v in headers.items()}
    tenant = _require(lower, TENANT_HEADER)
    user = _require(lower, USER_HEADER) if mode == MODE_TENANT_USER else LOCAL_USER
    timestamp = lower.get(TIMESTAMP_HEADER, "").strip()

    if timestamp:
        try:
            issued = float(timestamp)
        except ValueError:
            raise IdentityError("authentication timestamp is not a number") from None
        current = time.time() if now is None else now
        if abs(current - issued) > max_skew_seconds:
            raise IdentityError("authentication proof is outside the allowed time window")

    presented = lower.get(AUTH_HEADER, "").strip()
    expected = sign(auth_secret, tenant, user, timestamp)
    # Constant-time: a timing oracle here would leak the expected proof byte by byte.
    if not presented or not hmac.compare_digest(presented, expected):
        raise IdentityError("authentication proof is missing or invalid")

    return Identity(tenant, user)


def fingerprint(identity: Identity) -> str:
    """A short, non-reversible label safe to log or put in metrics (§25)."""
    return sha256(identity.namespace.encode()).hexdigest()[:12]
