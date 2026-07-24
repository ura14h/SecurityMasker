"""Alias factory — deterministic, idempotent, collision-safe alias assignment (§7).

Operates on a single ``MaskingSession`` (its keys + mapping dicts). Callers must
serialize concurrent ``get_or_create_alias`` for the *same* session (the session
store provides the lock) so a first-seen secret gets exactly one alias even under
parallel requests (§30.4). Given that, this module is pure and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from securitymasker.aliases.profiles import alias_for
from securitymasker.errors import AliasCollisionError
from securitymasker.models import AliasMapping, MaskingSession
from securitymasker.sessions.crypto import encrypt, fingerprint

_MIN_TOKEN_HEX = 6
_MAX_TOKEN_HEX = 32
_TOKEN_STEP = 2


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
    """Return the stable alias mapping for a value, creating it on first sight.

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
    """Pick the shortest non-colliding alias; lengthen the token on collision (§7)."""
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
