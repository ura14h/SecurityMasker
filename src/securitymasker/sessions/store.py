"""preview用memoryと通常運用SQLiteが共有するsession store interface。

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


class LockHandle:
    """process内lockを保持している区間だけで利用するhandle。"""

    def check(self) -> None:
        """process内lockはcontext終了まで失効しない。"""

    async def verify(self) -> None:
        """保存前の明示check。process内lockでは追加I/Oを必要としない。"""


def _now() -> datetime:
    return datetime.now(UTC)


def new_session(
    session_id: str,
    *,
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
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession: ...
    async def get_or_create(
        self,
        session_id: str,
        *,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession: ...
    async def save(self, session: MaskingSession, *, lock: LockHandle | None = None) -> None: ...
    async def delete(
        self, session_id: str, *, lock: LockHandle | None = None
    ) -> None: ...
    async def touch(
        self,
        session_id: str,
        *,
        lock: LockHandle | None = None,
    ) -> None: ...
    async def list_ids(self) -> list[str]: ...
    def lock(self, session_id: str) -> AbstractAsyncContextManager[LockHandle]: ...

    # Response-id -> session-key binding。OpenAI's
    # ``previous_response_id`` changes every turn, so it cannot itself identify a
    # session; binding each response id to the session that produced it is what
    # keeps a multi-turn conversation on one alias table.
    async def bind_response(self, response_id: str, session_key: str) -> None: ...
    async def resolve_response(self, response_id: str) -> str | None: ...
