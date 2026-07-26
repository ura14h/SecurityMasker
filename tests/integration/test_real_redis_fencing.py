"""実Redisでsession書込みのfencingを検証するopt-in integration test。"""

from __future__ import annotations

import asyncio
import os
import secrets

import pytest

from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.errors import SessionError
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.sessions.redis import RedisSessionStore

pytestmark = pytest.mark.skipif(
    os.environ.get("SM_RUN_REDIS") != "1",
    reason="set SM_RUN_REDIS=1 with SECURITYMASKER_REDIS_TEST_URL",
)


@pytest.mark.asyncio
async def test_real_redis_rejects_stale_writes_and_serializes_aliases() -> None:
    """Luaのowner確認とSETが実Redis上でも一つのatomic操作になることを確認する。"""
    redis = pytest.importorskip("redis.asyncio")
    url = os.environ.get("SECURITYMASKER_REDIS_TEST_URL", "redis://127.0.0.1:6379/15")
    client = redis.from_url(url)
    namespace = f"smtest:{secrets.token_hex(8)}"
    master = secrets.token_bytes(32)
    first = RedisSessionStore(client, master_key=master, namespace=namespace)
    second = RedisSessionStore(client, master_key=master, namespace=namespace)

    try:
        assert await client.ping()

        # stale holderのLua writeは、別ownerがlockを取った後には必ず拒否される。
        async with first.lock("stale", tenant_id="tenant") as held:
            session = await first.get_or_create(
                "stale", tenant_id="tenant", lock=held
            )
            data_key = first._key("stale", "tenant")
            lock_key = first._lock_key("stale", "tenant")
            before = await client.get(data_key)
            get_or_create_alias(
                session,
                original_value="山田太郎",
                fingerprint_value="山田太郎",
                entity_type="PERSON",
                replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                restore_policy=RestorePolicy.LITERAL.value,
            )
            await client.set(lock_key, "new-owner", ex=30)
            with pytest.raises(SessionError, match="fenced write"):
                await first.save(session, lock=held)
            assert await client.get(data_key) == before

        # 異なるworker相当のstore instanceも同じsession lockで直列化される。
        async def allocate(store: RedisSessionStore) -> str:
            async with store.lock("shared", tenant_id="tenant") as held:
                session = await store.get_or_create(
                    "shared", tenant_id="tenant", lock=held
                )
                mapping = get_or_create_alias(
                    session,
                    original_value="株式会社極秘技研",
                    fingerprint_value="株式会社極秘技研",
                    entity_type="ORGANIZATION",
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=RestorePolicy.LITERAL.value,
                )
                await store.save(session, lock=held)
                return mapping.alias

        aliases = await asyncio.gather(allocate(first), allocate(second))
        assert aliases[0] == aliases[1]
        loaded = await first.get("shared", tenant_id="tenant")
        assert loaded is not None
        assert len(loaded.mappings_by_alias) == 1
    finally:
        keys = await client.keys(f"{namespace}:*")
        if keys:
            await client.delete(*keys)
        await client.aclose()
