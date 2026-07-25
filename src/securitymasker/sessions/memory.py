"""Single-process in-memory session store (§8, Phase 1).

Not shared across LiteLLM workers — Phase 5 adds ``RedisSessionStore`` behind the
same ``SessionStore`` Protocol. Enforces idle + absolute TTL and serializes
per-session writes with an ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

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
        self._guard = asyncio.Lock()
        # response_id -> (session_key, bound_at) for multi-turn continuity (P1-1).
        self._response_bindings: dict[str, tuple[str, datetime]] = {}

    async def get(self, session_id: str) -> MaskingSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if is_expired(session, self._idle_ttl):
            await self.delete(session_id)
            return None
        return session

    async def create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
    ) -> MaskingSession:
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
    ) -> MaskingSession:
        async with self._guard:
            existing = await self.get(session_id)
            if existing is not None:
                return existing
            return await self.create(
                session_id, tenant_id=tenant_id, user_id=user_id, client_type=client_type
            )

    async def save(self, session: MaskingSession) -> None:
        # In-memory sessions are mutated in place; save just refreshes idle time.
        session.last_used_at = _now()
        self._sessions[session.session_id] = session

    async def delete(self, session_id: str) -> None:
        # Delete the SESSION only. The lock has an independent lifetime: dropping
        # it here (e.g. when an expired session is reaped from inside get()) would
        # let a concurrent request build a second lock for the same id and enter
        # the critical section in parallel (doc/06 P1-9).
        self._sessions.pop(session_id, None)

    async def touch(self, session_id: str) -> None:
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

    def lock(self, session_id: str) -> AbstractAsyncContextManager[LockHandle]:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock

        @asynccontextmanager
        async def _held() -> AsyncIterator[LockHandle]:
            async with lock:
                # In-process asyncio locks cannot be lost or expire, so the handle
                # never reports loss — unlike the Redis one (doc/06 P1-9).
                yield LockHandle()

        return _held()
