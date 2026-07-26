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
        self.store: dict[str, bytes | str] = {}
        self.renewals: list[str] = []
        self.eval_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> bytes | str | None:
        return self.store.get(key)

    async def set(
        self, key: str, value: bytes | str, *, ex: int | None = None, nx: bool = False
    ) -> bool:
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
        self.eval_calls.append((script, numkeys))
        keys = args[:numkeys]
        argv = args[numkeys:]
        lock_key, token = keys[0], argv[0]
        cur = self.store.get(lock_key)
        stored = cur.decode() if isinstance(cur, bytes) else cur
        if stored != token:
            return -1 if numkeys == 2 and "del" in script else 0
        if numkeys == 2 and "redis.call('set'" in script:
            self.store[keys[1]] = argv[1]
            return 1
        if numkeys == 2 and "redis.call('del'" in script:
            return int(self.store.pop(keys[1], None) is not None)
        if "expire" in script:
            self.renewals.append(lock_key)
            return 1
        self.store.pop(lock_key, None)
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
async def test_lock_loss_is_detected_by_the_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    # Audit round-3 finding 3: a failed renewal was swallowed, so the holder kept
    # running (and sending) after another owner had taken the lock.
    import asyncio

    from securitymasker.sessions import redis as redismod

    monkeypatch.setattr(redismod, "_LOCK_RENEW_SECONDS", 0.02)
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    lock_key = "sm:_:lock:taken"
    with pytest.raises(SessionError):
        async with store.lock("taken") as held:
            # Another owner takes the lock over (ours expired and was re-acquired).
            fake.store[lock_key] = b"someone-elses-token"
            await asyncio.sleep(0.08)   # let the watchdog notice
            held.check()                # must refuse to continue


@pytest.mark.asyncio
async def test_stale_lock_owner_cannot_overwrite_session() -> None:
    """ownership確認とSETの間にleaseを奪われても古い状態を書けない。"""
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    data_key = "sm:t1:sess:s1"
    lock_key = "sm:t1:lock:s1"

    async with store.lock("s1", tenant_id="t1") as held:
        session = await store.get_or_create("s1", tenant_id="t1", lock=held)
        before = fake.store[data_key]
        get_or_create_alias(
            session,
            original_value="山田太郎",
            fingerprint_value="山田太郎",
            entity_type="PERSON",
            replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
            restore_policy=RestorePolicy.LITERAL.value,
        )
        fake.store[lock_key] = "new-owner"
        with pytest.raises(SessionError, match="fenced write"):
            await store.save(session, lock=held)

    assert fake.store[data_key] == before


@pytest.mark.asyncio
async def test_stale_lock_owner_cannot_delete_session() -> None:
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    data_key = "sm:t1:sess:s1"
    lock_key = "sm:t1:lock:s1"

    await store.create("s1", tenant_id="t1")
    async with store.lock("s1", tenant_id="t1") as held:
        fake.store[lock_key] = "new-owner"
        with pytest.raises(SessionError, match="fenced delete"):
            await store.delete("s1", tenant_id="t1", lock=held)

    assert data_key in fake.store


@pytest.mark.asyncio
async def test_lock_for_another_session_cannot_fence_write() -> None:
    fake = FakeRedis()
    store = RedisSessionStore(fake, master_key=MASTER)
    async with store.lock("first", tenant_id="t1") as held:
        with pytest.raises(SessionError, match="not fenced"):
            await store.create("second", tenant_id="t1", lock=held)
    assert "sm:t1:sess:second" not in fake.store


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
