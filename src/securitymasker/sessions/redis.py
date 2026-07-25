"""Redis-backed session store (§8, Phase 5).

Multi-worker sharing behind the same ``SessionStore`` Protocol. Security rules (§8):

- The whole serialized session (its per-session keys **and** mappings) is sealed
  with AES-256-GCM under a **process master key** taken from the environment
  (``SECURITYMASKER_MASTER_KEY``), so Redis never holds a usable key or a plaintext
  mapping — the master key lives only in the process/Secret Manager, not in Redis.
- Keys are namespaced by tenant, keeping tenants' key spaces separate.
- TTL is enforced on the Redis key too; alias allocation is serialized with a
  Redis ``SET NX`` lock so concurrent workers don't fork a secret's alias.

The async client is injected (``redis.asyncio.Redis`` or a compatible fake), so the
serialization/crypto logic is testable without a running Redis.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from securitymasker.errors import CryptoError, SessionError
from securitymasker.models import AliasMapping, MaskingSession
from securitymasker.sessions.crypto import decrypt, encrypt
from securitymasker.sessions.store import (
    DEFAULT_ABSOLUTE_TTL,
    DEFAULT_IDLE_TTL,
    LockHandle,
    is_expired,
    new_session,
)

MASTER_KEY_ENV = "SECURITYMASKER_MASTER_KEY"

# Atomic compare-and-delete so a worker only releases a lock it still owns (P1-9).
_UNLOCK_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
# Extend the TTL only while we are still the owner (compare-and-expire).
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
_LOCK_TTL_SECONDS = 30
_LOCK_WAIT_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.05
# Renew well inside the TTL so a slow request (large input, slow detector) cannot
# outlive its own lock and let a second worker fork the session (doc/06 P1-9).
_LOCK_RENEW_SECONDS = 10.0


def load_master_key() -> bytes:
    raw = os.environ.get(MASTER_KEY_ENV)
    if not raw:
        raise SessionError(
            f"{MASTER_KEY_ENV} is required for the Redis session store "
            "(32 bytes, base64-encoded)."
        )
    try:
        key = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise SessionError(f"{MASTER_KEY_ENV} is not valid base64") from exc
    if len(key) != 32:
        raise SessionError(f"{MASTER_KEY_ENV} must decode to 32 bytes, got {len(key)}")
    return key


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _serialize(session: MaskingSession) -> str:
    mappings = [
        {
            "entity_type": m.entity_type,
            "alias": m.alias,
            "enc": _b64(m.encrypted_original),
            "fp": m.original_fingerprint,
            "profile": m.replacement_profile,
            "policy": m.restore_policy,
            "created_at": m.created_at.isoformat(),
            "last_used_at": m.last_used_at.isoformat(),
        }
        for m in session.mappings_by_fingerprint.values()
    ]
    doc = {
        "session_id": session.session_id,
        "index_key": _b64(session.session_index_key),
        "aead_key": _b64(session.aead_key),
        "tenant_id": session.tenant_id,
        "user_id": session.user_id,
        "client_type": session.client_type,
        "created_at": session.created_at.isoformat(),
        "last_used_at": session.last_used_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "mappings": mappings,
    }
    return json.dumps(doc, ensure_ascii=False)


def _deserialize(text: str) -> MaskingSession:
    doc = json.loads(text)
    session = MaskingSession(
        session_id=doc["session_id"],
        session_index_key=_unb64(doc["index_key"]),
        aead_key=_unb64(doc["aead_key"]),
        tenant_id=doc.get("tenant_id"),
        user_id=doc.get("user_id"),
        client_type=doc.get("client_type", "unknown"),
        created_at=datetime.fromisoformat(doc["created_at"]),
        last_used_at=datetime.fromisoformat(doc["last_used_at"]),
        expires_at=datetime.fromisoformat(doc["expires_at"]),
    )
    for m in doc.get("mappings", []):
        mapping = AliasMapping(
            entity_type=m["entity_type"],
            alias=m["alias"],
            encrypted_original=_unb64(m["enc"]),
            original_fingerprint=m["fp"],
            replacement_profile=m["profile"],
            restore_policy=m["policy"],
            created_at=datetime.fromisoformat(m["created_at"]),
            last_used_at=datetime.fromisoformat(m["last_used_at"]),
        )
        session.mappings_by_fingerprint[mapping.original_fingerprint] = mapping
        session.mappings_by_alias[mapping.alias] = mapping
    return session


class RedisSessionStore:
    def __init__(
        self,
        client: Any,
        *,
        master_key: bytes | None = None,
        idle_ttl: timedelta = DEFAULT_IDLE_TTL,
        absolute_ttl: timedelta = DEFAULT_ABSOLUTE_TTL,
        namespace: str = "sm",
    ) -> None:
        self._redis = client
        self._master_key = master_key if master_key is not None else load_master_key()
        self._idle_ttl = idle_ttl
        self._absolute_ttl = absolute_ttl
        self._ns = namespace

    def _key(self, session_id: str, tenant_id: str | None) -> str:
        # Tenant-separated key space (§8). Session id is opaque already.
        return f"{self._ns}:{tenant_id or '_'}:sess:{session_id}"

    def _aad(self, session_id: str, tenant_id: str | None) -> bytes:
        return f"{tenant_id or '_'}:{session_id}".encode()

    async def get(self, session_id: str, tenant_id: str | None = None) -> MaskingSession | None:
        blob = await self._redis.get(self._key(session_id, tenant_id))
        if blob is None:
            return None
        try:
            plaintext = decrypt(self._master_key, blob if isinstance(blob, bytes) else bytes(blob),
                                aad=self._aad(session_id, tenant_id))
        except CryptoError as exc:
            raise SessionError("session decryption failed (tampering or wrong master key)") from exc
        session = _deserialize(plaintext)
        if is_expired(session, self._idle_ttl):
            await self.delete(session_id, tenant_id)
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
            session_id, tenant_id=tenant_id, user_id=user_id,
            client_type=client_type, absolute_ttl=self._absolute_ttl,
        )
        await self.save(session)
        return session

    async def get_or_create(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_type: str = "unknown",
    ) -> MaskingSession:
        existing = await self.get(session_id, tenant_id)
        if existing is not None:
            return existing
        return await self.create(
            session_id, tenant_id=tenant_id, user_id=user_id, client_type=client_type
        )

    async def save(self, session: MaskingSession) -> None:
        session.last_used_at = datetime.now(UTC)
        sealed = encrypt(
            self._master_key, _serialize(session),
            aad=self._aad(session.session_id, session.tenant_id),
        )
        ttl = int(self._idle_ttl.total_seconds())
        await self._redis.set(self._key(session.session_id, session.tenant_id), sealed, ex=ttl)

    async def delete(self, session_id: str, tenant_id: str | None = None) -> None:
        await self._redis.delete(self._key(session_id, tenant_id))

    async def touch(self, session_id: str, tenant_id: str | None = None) -> None:
        session = await self.get(session_id, tenant_id)
        if session is not None:
            await self.save(session)

    async def list_ids(self) -> list[str]:
        keys = await self._redis.keys(f"{self._ns}:*:sess:*")
        out = []
        for k in keys:
            text = k.decode() if isinstance(k, bytes) else k
            out.append(text.rsplit(":", 1)[-1])
        return out

    async def bind_response(self, response_id: str, session_key: str) -> None:
        """Bind a response id to the session that produced it (doc/06 P1-1)."""
        await self._redis.set(
            f"{self._ns}:resp:{response_id}", session_key.encode(),
            ex=int(self._idle_ttl.total_seconds()),
        )

    async def resolve_response(self, response_id: str) -> str | None:
        value = await self._redis.get(f"{self._ns}:resp:{response_id}")
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    @asynccontextmanager
    async def lock(
        self, session_id: str, tenant_id: str | None = None
    ) -> AsyncIterator[LockHandle]:
        """Cross-worker lock via SET NX with a unique owner token (§8, P1-9).

        Waits (bounded) for the lock and fails closed if it cannot be acquired, so
        the caller never runs the critical section unlocked; releases atomically so
        it can only delete a lock it still owns (never another owner's after TTL).
        """
        lock_key = f"{self._ns}:{tenant_id or '_'}:lock:{session_id}"
        token = secrets.token_hex(16)
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        acquired = False
        while True:
            acquired = bool(
                await self._redis.set(lock_key, token, nx=True, ex=_LOCK_TTL_SECONDS)
            )
            if acquired or time.monotonic() >= deadline:
                break
            await asyncio.sleep(_LOCK_POLL_SECONDS)
        if not acquired:
            raise SessionError(
                f"could not acquire session lock within {_LOCK_WAIT_SECONDS}s; "
                "refusing to proceed unlocked"
            )

        # Set when the watchdog finds we no longer own the lock. The holder MUST
        # check this before acting on the protected state (P1-9): a renewal that
        # returns 0 means another owner has taken over, and continuing would let
        # two workers mutate the same session.
        lost = asyncio.Event()

        async def _renew() -> None:
            """Extend the TTL while we still hold it, so the lock cannot expire
            mid-request and let another worker fork the same session (P1-9)."""
            while True:
                await asyncio.sleep(_LOCK_RENEW_SECONDS)
                try:
                    ok = await self._redis.eval(
                        _RENEW_LUA, 1, lock_key, token, str(_LOCK_TTL_SECONDS)
                    )
                except Exception:  # noqa: BLE001 - Redis unreachable: assume lost
                    lost.set()
                    return
                # Lua returns 0 when the key is gone or owned by someone else.
                if not ok:
                    lost.set()
                    return

        watchdog = asyncio.create_task(_renew())
        try:
            yield LockHandle(lost)
        finally:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
            # Best-effort atomic release; if it fails, the TTL reclaims the lock.
            with contextlib.suppress(Exception):
                await self._redis.eval(_UNLOCK_LUA, 1, lock_key, token)
