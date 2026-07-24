"""Alias factory + profile tests (§30.1: same/diff alias, collision, profiles)."""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import UTC, datetime

import pytest

from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.aliases.profiles import alias_for
from securitymasker.errors import AliasCollisionError
from securitymasker.models import AliasMapping, ReplacementProfile, RestorePolicy
from securitymasker.sessions.crypto import decrypt
from securitymasker.sessions.store import new_session


def _now() -> datetime:
    return datetime.now(UTC)

PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
LITERAL = RestorePolicy.LITERAL.value


def _alloc(session, value, **kw):
    return get_or_create_alias(
        session,
        original_value=value,
        fingerprint_value=kw.get("fp", value),
        entity_type=kw.get("entity_type", "PERSON"),
        replacement_profile=kw.get("profile", PROSE),
        restore_policy=LITERAL,
    )


def test_same_value_same_alias_and_encrypted_original_restores() -> None:
    s = new_session("s1")
    m1 = _alloc(s, "山田太郎")
    m2 = _alloc(s, "山田太郎")
    assert m1 is m2
    assert decrypt(s.aead_key, m1.encrypted_original, aad=m1.original_fingerprint.encode()) == "山田太郎"


def test_surface_forms_get_distinct_aliases_by_default() -> None:
    s = new_session("s1")
    a = _alloc(s, "山田太郎", fp="山田太郎")
    b = _alloc(s, "山田 太郎", fp="山田 太郎")
    assert a.alias != b.alias


def test_collision_lengthens_token() -> None:
    s = new_session("s1")
    real = _alloc(s, "value-one", entity_type="PERSON")
    # Force a collision: seed a different fingerprint onto the same alias string.
    s.mappings_by_alias[real.alias] = real
    other = get_or_create_alias(
        s, original_value="value-two", fingerprint_value="totally-different",
        entity_type="PERSON", replacement_profile=PROSE, restore_policy=LITERAL,
    )
    assert other.alias != real.alias


def test_profile_hostname_is_valid_and_reserved() -> None:
    alias = alias_for(ReplacementProfile.HOSTNAME.value, "HOSTNAME", "7f3a91", "h")
    assert alias.endswith(".example.invalid")
    assert re.fullmatch(r"[a-z0-9.-]+", alias)


def test_profile_email_is_syntactically_valid() -> None:
    alias = alias_for(ReplacementProfile.EMAIL.value, "EMAIL", "2b891c", "e")
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", alias)
    assert alias.endswith("@example.invalid")


def test_profile_ipv4_is_documentation_range() -> None:
    alias = alias_for(ReplacementProfile.IPV4.value, "IP_ADDRESS", "abcdef", "1.2.3.4")
    assert ipaddress.ip_address(alias) in ipaddress.ip_network("198.51.100.0/24")


def test_profile_ipv6_is_documentation_range() -> None:
    alias = alias_for(ReplacementProfile.IPV6.value, "IP_ADDRESS", "dead", "::1")
    assert ipaddress.ip_address(alias) in ipaddress.ip_network("2001:db8::/32")


def test_profile_uuid_shaped() -> None:
    alias = alias_for(ReplacementProfile.UUID.value, "UUID", "abc123", "x")
    assert uuid.UUID(alias)


def test_profile_numeric_preserves_digit_count() -> None:
    alias = alias_for(ReplacementProfile.NUMERIC.value, "CUSTOMER_ID", "abc123", "0123456789")
    assert alias.isdigit() and len(alias) == 10


def test_profile_prose_is_program_identifier_safe() -> None:
    alias = alias_for(PROSE, "PERSON", "2b891c", "山田太郎")
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias)


def test_env_reference_profile_shape() -> None:
    alias = alias_for(ReplacementProfile.ENVIRONMENT_REFERENCE.value, "API_KEY", "a7c391", "sk-x")
    assert alias == "${SECURITYMASKER_SECRET_A7C391}"


def test_collision_exhaustion_raises() -> None:
    s = new_session("s1")
    # Fill the alias slot for every token length with a mapping owned by a FOREIGN
    # fingerprint, so no length can produce a free alias.
    from securitymasker.aliases import factory

    fp = factory.fingerprint(s.session_index_key, "x", "PERSON", PROSE)

    def _foreign(alias: str) -> AliasMapping:
        return AliasMapping(
            entity_type="PERSON", alias=alias, encrypted_original=b"",
            original_fingerprint="FOREIGN", replacement_profile=PROSE,
            restore_policy=LITERAL, created_at=_now(), last_used_at=_now(),
        )

    for length in range(6, 33, 2):
        alias = alias_for(PROSE, "PERSON", fp[:length], "x")
        s.mappings_by_alias[alias] = _foreign(alias)
    with pytest.raises(AliasCollisionError):
        _alloc(s, "x", fp="x", entity_type="PERSON")
