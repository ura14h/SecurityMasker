"""Session store interface (§8). In-memory now, Redis-swappable later.

The store owns session lifetime (idle + absolute TTL, §7) and hands out a
per-session async lock so alias allocation for one session is serialized across
concurrent requests without blocking other sessions (§30.4).
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from securitymasker.models import MaskingSession
from securitymasker.sessions.crypto import generate_session_keys

DEFAULT_IDLE_TTL = timedelta(hours=4)
DEFAULT_ABSOLUTE_TTL = timedelta(hours=24)


class LockHandle:
    """Handle yielded by ``SessionStore.lock``.

    A distributed lock can expire or be taken over mid-request, so the holder must
    confirm it still owns the lock before mutating the protected state.

    - ``check()`` is a cheap, non-blocking read of what the watchdog last saw.
    - ``verify()`` actively re-reads ownership from the store; call it immediately
      BEFORE writing, so a lost lock aborts the request *before* a non-owner write
      lands (doc/06 P1-9).

    Residual risk: even ``verify()`` leaves a small window between the ownership
    read and the write. Closing it fully needs the write itself to be conditional
    on the token (a fencing token, or a Lua script that checks the lock and writes
    the session in one round trip); that is recorded in doc/07 as not implemented.
    """

    __slots__ = ("_lost", "_verifier")

    def __init__(self, lost: Any = None, verifier: Any = None) -> None:
        self._lost = lost
        self._verifier = verifier

    @property
    def lost(self) -> bool:
        return bool(self._lost is not None and self._lost.is_set())

    def check(self) -> None:
        if self.lost:
            self._raise()

    async def verify(self) -> None:
        """Actively confirm ownership, then raise if it has been lost."""
        if (self._verifier is not None and not await self._verifier()
                and self._lost is not None):
            self._lost.set()
        self.check()

    def _raise(self) -> None:
        from securitymasker.errors import SessionError

        raise SessionError(
            "session lock was lost while the request was in flight; "
            "refusing to continue without exclusivity"
        )


def _now() -> datetime:
    return datetime.now(UTC)


def new_session(
    session_id: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    client_type: str = "unknown",
    absolute_ttl: timedelta = DEFAULT_ABSOLUTE_TTL,
) -> MaskingSession:
    """Construct a session with fresh CSPRNG keys (§7)."""
    index_key, aead_key = generate_session_keys()
    now = _now()
    return MaskingSession(
        session_id=session_id,
        session_index_key=index_key,
        aead_key=aead_key,
        tenant_id=tenant_id,
        user_id=user_id,
        client_type=client_type,
        created_at=now,
        last_used_at=now,
        expires_at=now + absolute_ttl,
    )


def is_expired(session: MaskingSession, idle_ttl: timedelta, now: datetime | None = None) -> bool:
    now = now or _now()
    return now >= session.expires_at or (now - session.last_used_at) >= idle_ttl


@runtime_checkable
class SessionStore(Protocol):
    async def get(self, session_id: str) -> MaskingSession | None: ...
    async def create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
    ) -> MaskingSession: ...
    async def get_or_create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
    ) -> MaskingSession: ...
    async def save(self, session: MaskingSession) -> None: ...
    async def delete(self, session_id: str) -> None: ...
    async def touch(self, session_id: str) -> None: ...
    async def list_ids(self) -> list[str]: ...
    def lock(self, session_id: str) -> AbstractAsyncContextManager[LockHandle]: ...

    # Response-id -> session-key binding (doc/06 P1-1). OpenAI's
    # ``previous_response_id`` changes every turn, so it cannot itself identify a
    # session; binding each response id to the session that produced it is what
    # keeps a multi-turn conversation on one alias table.
    async def bind_response(self, response_id: str, session_key: str) -> None: ...
    async def resolve_response(self, response_id: str) -> str | None: ...
