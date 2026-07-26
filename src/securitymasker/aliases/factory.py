"""alias factory — 決定論的・冪等でcollision-safeなalias割り当て（§7）。

Operates on a single ``MaskingSession`` (its keys + mapping dicts). Callers must
serialize concurrent ``get_or_create_alias`` for the *same* session (the session
store provides the lock) so a first-seen secret gets exactly one alias even under
parallel requests (§30.4). Given that, this module is pure and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from securitymasker.aliases.profiles import alias_for
from securitymasker.errors import AliasCollisionError, MaskingError
from securitymasker.models import AliasMapping, MaskingSession
from securitymasker.sessions.crypto import encrypt, fingerprint

# alias token長はhex 12文字（48 bit）。session内10,000 mappingでも十分な空間を持つ。
# the birthday collision probability is ~1.8e-10, and guessing a specific alias is
# infeasible. The old 6-hex (24-bit) default collided at ~1-in-3000 for a 1k-entry
# session and was cheap to enumerate — see docs/adr/0007-alias-token-length.md.
# collision時はさらに延長するため、これは上限ではなく下限。
_MIN_TOKEN_HEX = 12
_MAX_TOKEN_HEX = 32
_TOKEN_STEP = 2

# sessionごとの異なるsecret数に上限を設ける（doc/06 P1-5）。
# hostile or runaway input can't grow a session's mapping table without limit.
# 一sessionで数千の異なるsecretに達した場合だけfail-closedになる。
MAX_MAPPINGS_PER_SESSION = 10_000


def _now() -> datetime:
    return datetime.now(UTC)


def get_or_create_alias(
    session: MaskingSession,
    *,
    original_value: str,
    fingerprint_value: str,
    entity_type: str,
    replacement_profile: str,
    restore_policy: str,
) -> AliasMapping:
    """値に対応する安定したalias mappingを返し、初出時は作成する。

    ``fingerprint_value`` is what identity is keyed on: the surface form when
    surface forms are kept distinct, or the normalized form when they are merged
    (decided by the caller / config). ``original_value`` is always the exact
    surface text to restore to.
    """
    fp = fingerprint(session.session_index_key, fingerprint_value, entity_type, replacement_profile)

    existing = session.mappings_by_fingerprint.get(fp)
    if existing is not None:
        existing.last_used_at = _now()
        return existing

    if len(session.mappings_by_fingerprint) >= MAX_MAPPINGS_PER_SESSION:
        # 無制限に増やさずfail-closedにし、messageには値を含めない（doc/06 P1-5）。
        # no value; the caller turns this into a request block.
        raise MaskingError(
            f"session mapping limit ({MAX_MAPPINGS_PER_SESSION}) reached; "
            "refusing to allocate more aliases"
        )

    alias = _allocate_alias(session, fp, entity_type, replacement_profile, original_value)

    now = _now()
    mapping = AliasMapping(
        entity_type=entity_type,
        alias=alias,
        encrypted_original=encrypt(session.aead_key, original_value, aad=fp.encode("ascii")),
        original_fingerprint=fp,
        replacement_profile=replacement_profile,
        restore_policy=restore_policy,
        created_at=now,
        last_used_at=now,
    )
    session.mappings_by_fingerprint[fp] = mapping
    session.mappings_by_alias[alias] = mapping
    return mapping


def _allocate_alias(
    session: MaskingSession,
    fp: str,
    entity_type: str,
    replacement_profile: str,
    original_value: str,
) -> str:
    """衝突しない最短aliasを選び、衝突時はtokenを延長する（§7）。"""
    for length in range(_MIN_TOKEN_HEX, _MAX_TOKEN_HEX + 1, _TOKEN_STEP):
        token = fp[:length]
        alias = alias_for(replacement_profile, entity_type, token, original_value)
        holder = session.mappings_by_alias.get(alias)
        if holder is None or holder.original_fingerprint == fp:
            return alias
        # else: a different secret already owns this alias — lengthen and retry.
    raise AliasCollisionError(
        f"could not allocate a unique alias for entity {entity_type} "
        f"with profile {replacement_profile}"
    )
