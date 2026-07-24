"""Session store interface (§8). In-memory now, Redis-swappable later.

The store owns session lifetime (idle + absolute TTL, §7) and hands out a
per-session async lock so alias allocation for one session is serialized across
concurrent requests without blocking other sessions (§30.4).
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from securitymasker.models import MaskingSession
from securitymasker.sessions.crypto import generate_session_keys

DEFAULT_IDLE_TTL = timedelta(hours=4)
DEFAULT_ABSOLUTE_TTL = timedelta(hours=24)


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
    def lock(self, session_id: str) -> AbstractAsyncContextManager[None]: ...
