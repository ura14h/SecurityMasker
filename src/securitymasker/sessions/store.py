"""session store interface（§8）。in-memoryとRedis実装を差し替え可能にする。

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
    """``SessionStore.lock``が返すhandle。

    A distributed lock can expire or be taken over mid-request, so the holder must
    confirm it still owns the lock before mutating the protected state.

    - ``check()`` is a cheap, non-blocking read of what the watchdog last saw.
    - ``verify()`` actively re-reads ownership from the store.
    - Redis handles additionally carry opaque fencing credentials. The Redis
      store checks those credentials and writes the session in one Lua execution,
      so a lease that expires after ``verify()`` cannot leave a stale write
      (doc/06 P1-9).
    """

    __slots__ = ("_fence_key", "_fence_token", "_lost", "_verifier")

    def __init__(
        self,
        lost: Any = None,
        verifier: Any = None,
        *,
        fence_key: str | None = None,
        fence_token: str | None = None,
    ) -> None:
        self._lost = lost
        self._verifier = verifier
        self._fence_key = fence_key
        self._fence_token = fence_token

    @property
    def lost(self) -> bool:
        return bool(self._lost is not None and self._lost.is_set())

    def check(self) -> None:
        if self.lost:
            self._raise()

    async def verify(self) -> None:
        """ownershipを能動的に確認し、喪失していれば例外を送出する。"""
        if (self._verifier is not None and not await self._verifier()
                and self._lost is not None):
            self._lost.set()
        self.check()

    def fence_token_for(self, expected_key: str) -> str:
        """指定されたlock用のopaque tokenを返し、取り違えはfail-closedにする。"""
        self.check()
        if self._fence_key != expected_key or self._fence_token is None:
            from securitymasker.errors import SessionError

            raise SessionError(
                "session write is not fenced by the lock for this session"
            )
        return self._fence_token

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
    """新しいCSPRNG鍵でsessionを構築する（§7）。"""
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
        lock: LockHandle | None = None,
    ) -> MaskingSession: ...
    async def get_or_create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession: ...
    async def save(self, session: MaskingSession, *, lock: LockHandle | None = None) -> None: ...
    async def delete(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        lock: LockHandle | None = None,
    ) -> None: ...
    async def touch(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        lock: LockHandle | None = None,
    ) -> None: ...
    async def list_ids(self) -> list[str]: ...
    def lock(
        self, session_id: str, tenant_id: str | None = None
    ) -> AbstractAsyncContextManager[LockHandle]: ...

    # Response-id -> session-key binding (doc/06 P1-1). OpenAI's
    # ``previous_response_id`` changes every turn, so it cannot itself identify a
    # session; binding each response id to the session that produced it is what
    # keeps a multi-turn conversation on one alias table.
    async def bind_response(self, response_id: str, session_key: str) -> None: ...
    async def resolve_response(self, response_id: str) -> str | None: ...
