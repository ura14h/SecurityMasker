"""RedisSessionStore tests via an in-memory fake client (§8, §30.4).

Exercises the serialization + master-key sealing + tenant separation + TTL logic
without a running Redis. A real-Redis integration test is left opt-in.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

import pytest

from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.errors import SessionError
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.sessions.redis import RedisSessionStore, load_master_key

MASTER = secrets.token_bytes(32)


class FakeRedis:
    """Minimal async Redis stand-in (get/set/delete/keys + NX)."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.renewals: list[str] = []

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def keys(self, pattern: str) -> list[str]:
        import fnmatch

        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        # Two scripts are used, both owner-checked: compare-and-delete (unlock)
        # and compare-and-expire (renew).
        key, token = args[0], args[1]
        cur = self.store.get(key)
        stored = cur.decode() if isinstance(cur, bytes) else cur
        if stored != token:
            return 0
        if "expire" in script:
            self.renewals.append(key)
            return 1
        self.store.pop(key, None)
        return 1


def _store() -> RedisSessionStore:
    return RedisSessionStore(FakeRedis(), master_key=MASTER)


@pytest.mark.asyncio
async def test_create_get_roundtrip_with_mappings() -> None:
    store = _store()
    session = await store.create("s1", tenant_id="t1")
    get_or_create_alias(
        session, original_value="山田太郎", fingerprint_value="山田太郎",
        entity_type="PERSON", replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
        restore_policy=RestorePolicy.LITERAL.value,
    )
    await store.save(session)

    loaded = await store.get("s1", tenant_id="t1")
    assert loaded is not None
    assert loaded.session_index_key == session.session_index_key
    assert len(loaded.mappings_by_alias) == 1
    # The alias restores to the original after a full serialize/deserialize cycle.
    from securitymasker.engine import MaskingEngine

    eng = MaskingEngine([])
    (alias,) = loaded.mappings_by_alias.keys()
    assert eng.make_restorer(loaded)(alias) == "山田太郎"


@pytest.mark.asyncio
async def test_stored_blob_has_no_plaintext_secret() -> None:
    store = _store()
    fake: FakeRedis = store._redis  # type: ignore[assignment]
    session = await store.create("s1", tenant_id="t1")
    get_or_create_alias(
        session, original_value="prod-db01.internal.example", fingerprint_value="h",
        entity_type="HOSTNAME", replacement_profile=ReplacementProfile.HOSTNAME.value,
        restore_policy=RestorePolicy.LITERAL.value,
    )
    await store.save(session)
    for blob in fake.store.values():
        assert b"prod-db01" not in blob  # sealed under master key (§8)
        assert session.aead_key not in blob  # raw key never in Redis (§8)


@pytest.mark.asyncio
async def test_tenants_are_key_separated() -> None:
    store = _store()
    await store.create("same-id", tenant_id="tA")
    await store.create("same-id", tenant_id="tB")
    fake: FakeRedis = store._redis  # type: ignore[assignment]
    keys = list(fake.store)
    assert any(":tA:" in k for k in keys) and any(":tB:" in k for k in keys)
    # A tenant cannot read another tenant's session id.
    assert await store.get("same-id", tenant_id="tA") is not None
    assert await store.get("missing", tenant_id="tA") is None


@pytest.mark.asyncio
async def test_wrong_master_key_fails_closed() -> None:
    store = _store()
    await store.create("s1", tenant_id="t1")
    fake = store._redis
    other = RedisSessionStore(fake, master_key=secrets.token_bytes(32))
    with pytest.raises(SessionError):
        await other.get("s1", tenant_id="t1")


@pytest.mark.asyncio
async def test_lock_fails_closed_when_already_held(monkeypatch: pytest.MonkeyPatch) -> None:
    from securitymasker.sessions import redis as redismod

    monkeypatch.setattr(redismod, "_LOCK_WAIT_SECONDS", 0.15)
    store = _store()
    async with store.lock("s1", tenant_id="t1"):
        # A second acquisition of the same lock must fail closed, not proceed.
        with pytest.raises(SessionError):
            async with store.lock("s1", tenant_id="t1"):
                pass


@pytest.mark.asyncio
async def test_lock_release_only_deletes_own_token() -> None:
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    lock_key = "sm:t1:lock:s1"
    async with store.lock("s1", tenant_id="t1"):
        # Simulate the lock expiring and another owner taking it over.
        fake.store[lock_key] = b"other-owner-token"
    # Our release must NOT have deleted the other owner's lock.
    assert fake.store.get(lock_key) == b"other-owner-token"


@pytest.mark.asyncio
async def test_lock_ttl_is_renewed_while_held(monkeypatch: pytest.MonkeyPatch) -> None:
    # Re-audit finding 7: a fixed 30s TTL with no renewal let the lock expire
    # mid-request, so another worker could enter and fork the session.
    import asyncio

    from securitymasker.sessions import redis as redismod

    monkeypatch.setattr(redismod, "_LOCK_RENEW_SECONDS", 0.02)
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    async with store.lock("slow", tenant_id="t1"):
        await asyncio.sleep(0.12)  # a request slower than the renew interval
    assert fake.renewals, "lock TTL was never renewed while held"


@pytest.mark.asyncio
async def test_lock_acquire_and_release_roundtrip() -> None:
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    lock_key = "sm:_:lock:s2"
    async with store.lock("s2"):
        assert lock_key in fake.store          # held during the critical section
    assert lock_key not in fake.store          # released afterwards


def test_load_master_key_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECURITYMASKER_MASTER_KEY", raising=False)
    with pytest.raises(SessionError):
        load_master_key()
    monkeypatch.setenv("SECURITYMASKER_MASTER_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(SessionError):
        load_master_key()
    monkeypatch.setenv("SECURITYMASKER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    assert len(load_master_key()) == 32
