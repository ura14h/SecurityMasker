"""単一process用in-memory session store（§8、Phase 1）。

Not shared across worker processes — ``RedisSessionStore`` implements the same
``SessionStore`` Protocol for that. Enforces idle + absolute TTL and serializes
per-session writes with an ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

from securitymasker.errors import SessionError
from securitymasker.models import MaskingSession
from securitymasker.sessions.store import (
    DEFAULT_ABSOLUTE_TTL,
    DEFAULT_IDLE_TTL,
    LockHandle,
    is_expired,
    new_session,
)


def _now() -> datetime:
    return datetime.now(UTC)


class InMemorySessionStore:
    def __init__(
        self,
        *,
        idle_ttl: timedelta = DEFAULT_IDLE_TTL,
        absolute_ttl: timedelta = DEFAULT_ABSOLUTE_TTL,
    ) -> None:
        self._idle_ttl = idle_ttl
        self._absolute_ttl = absolute_ttl
        self._sessions: dict[str, MaskingSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}
        self._guard = asyncio.Lock()
        # response_id -> (session_key, bound_at) for multi-turn continuity (P1-1).
        self._response_bindings: dict[str, tuple[str, datetime]] = {}

    async def get(
        self, session_id: str, *, tenant_id: str | None = None, user_id: str | None = None
    ) -> MaskingSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if is_expired(session, self._idle_ttl):
            await self.delete(session_id)
            return None
        # keyはidentity namespace済みだが、保存sessionも照合するdefence-in-depth。
        # session whose recorded identity disagrees must never be handed back —
        # that would be a cross-boundary read (§8, doc/06 P0-9).
        if tenant_id is not None and session.tenant_id != tenant_id:
            raise SessionError("session does not belong to the requesting tenant")
        if user_id is not None and session.user_id != user_id:
            raise SessionError("session does not belong to the requesting user")
        return session

    async def create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession:
        if lock is not None:
            lock.check()
        session = new_session(
            session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            client_type=client_type,
            absolute_ttl=self._absolute_ttl,
        )
        self._sessions[session_id] = session
        return session

    async def get_or_create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession:
        if lock is not None:
            lock.check()
        async with self._guard:
            existing = await self.get(session_id, tenant_id=tenant_id, user_id=user_id)
            if existing is not None:
                return existing
            return await self.create(
                session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                client_type=client_type,
                lock=lock,
            )

    async def save(
        self, session: MaskingSession, *, lock: LockHandle | None = None
    ) -> None:
        if lock is not None:
            lock.check()
        # in-memory sessionはin-place更新されるためsaveはidle timeだけ更新する。
        session.last_used_at = _now()
        self._sessions[session.session_id] = session

    async def delete(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        lock: LockHandle | None = None,
    ) -> None:
        del tenant_id
        if lock is not None:
            lock.check()
        # sessionだけを削除し、独立したlifetimeを持つlockは残す。
        # it here (e.g. when an expired session is reaped from inside get()) would
        # let a concurrent request build a second lock for the same id and enter
        # the critical section in parallel (doc/06 P1-9).
        self._sessions.pop(session_id, None)

    async def touch(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        lock: LockHandle | None = None,
    ) -> None:
        del tenant_id
        if lock is not None:
            lock.check()
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_used_at = _now()

    async def list_ids(self) -> list[str]:
        return [
            sid
            for sid, s in list(self._sessions.items())
            if not is_expired(s, self._idle_ttl)
        ]

    async def bind_response(self, response_id: str, session_key: str) -> None:
        self._response_bindings[response_id] = (session_key, _now())

    async def resolve_response(self, response_id: str) -> str | None:
        entry = self._response_bindings.get(response_id)
        if entry is None:
            return None
        session_key, bound_at = entry
        if (_now() - bound_at) >= self._idle_ttl:
            self._response_bindings.pop(response_id, None)
            return None
        return session_key

    def lock(
        self, session_id: str, tenant_id: str | None = None
    ) -> AbstractAsyncContextManager[LockHandle]:
        del tenant_id
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        # 安全に回収できるようholderとwaiterの両方をreference countする。
        # dropping it while anyone still references it would let a newcomer build a
        # second lock for the same id and enter concurrently, while never dropping
        # it leaks one lock per session id forever — a memory DoS for ephemeral or
        # client-chosen ids (doc/06 P1-9, P1-5).
        self._lock_refs[session_id] = self._lock_refs.get(session_id, 0) + 1

        @asynccontextmanager
        async def _held() -> AsyncIterator[LockHandle]:
            try:
                async with lock:
                    # in-process asyncio lockは失効・takeoverしない。
                    # the handle never reports loss — unlike the Redis one.
                    yield LockHandle()
            finally:
                remaining = self._lock_refs.get(session_id, 1) - 1
                if remaining <= 0:
                    self._lock_refs.pop(session_id, None)
                    # holder／waiterがなくsessionも存在しない場合だけ回収する。
                    # gone; a live session keeps its lock for the next request.
                    if session_id not in self._sessions:
                        self._locks.pop(session_id, None)
                else:
                    self._lock_refs[session_id] = remaining

        return _held()
