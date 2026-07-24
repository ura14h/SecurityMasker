"""Replacement profiles — shape an alias so it keeps the entity's syntax (§9).

Each profile turns a short opaque ``token`` (hex derived from the fingerprint) into
an alias string that stays syntactically valid in the entity's context, so that
generated code / JSON / shell / hostnames don't break (§6, §16). Reserved,
non-routable domains/ranges (``.invalid``, ``2001:db8::/32``, ``198.51.100.0/24``)
are used so an alias can't be mistaken for a real endpoint (§9.2, §9.4).
"""

from __future__ import annotations

import hashlib

from securitymasker.models import EntityType, ReplacementProfile

ALIAS_PREFIX = "SM_"
INVALID_DOMAIN = "example.invalid"
_DOC_IPV4_NET = "198.51.100"  # RFC 5737 documentation range
_DOC_IPV6_PREFIX = "2001:db8"  # RFC 3849 documentation range
ENV_SECRET_PREFIX = "SECURITYMASKER_SECRET_"

# Short, identifier-safe tag per entity type for prose aliases (SM_<TAG>_<HEX>).
_ENTITY_TAG: dict[str, str] = {
    EntityType.PERSON.value: "PERSON",
    EntityType.ORGANIZATION.value: "ORG",
    EntityType.PROJECT_NAME.value: "PROJECT",
    EntityType.PRODUCT_NAME.value: "PRODUCT",
    EntityType.HOSTNAME.value: "HOST",
    EntityType.EMPLOYEE_ID.value: "EMP",
    EntityType.CUSTOMER_ID.value: "CUST",
    EntityType.JP_ADDRESS.value: "ADDR",
}


def _tag(entity_type: str) -> str:
    return _ENTITY_TAG.get(entity_type, "VALUE")


def _digits_from(token: str, length: int) -> str:
    """Deterministically expand ``token`` into ``length`` decimal digits."""
    out: list[str] = []
    seed = token.encode("utf-8")
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(seed + str(counter).encode()).hexdigest()
        out.extend(str(int(c, 16) % 10) for c in block)
        counter += 1
    return "".join(out[:length])


def alias_for(
    profile: str,
    entity_type: str,
    token: str,
    original: str,
) -> str:
    """Build an alias for ``original`` given a ``profile`` and opaque ``token`` (hex)."""
    p = ReplacementProfile(profile)
    hexlow = token.lower()
    hexup = token.upper()

    if p is ReplacementProfile.PROSE_IDENTIFIER:
        return f"{ALIAS_PREFIX}{_tag(entity_type)}_{hexup}"

    if p is ReplacementProfile.HOSTNAME:
        return f"sm-host-{hexlow}.{INVALID_DOMAIN}"

    if p is ReplacementProfile.EMAIL:
        return f"sm-user-{hexlow}@{INVALID_DOMAIN}"

    if p is ReplacementProfile.IPV4:
        octet = (int(token, 16) % 254) + 1  # 1..254
        return f"{_DOC_IPV4_NET}.{octet}"

    if p is ReplacementProfile.IPV6:
        return f"{_DOC_IPV6_PREFIX}::{hexlow[:16] or '1'}"

    if p is ReplacementProfile.UUID:
        h = hashlib.sha256(token.encode()).hexdigest()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    if p is ReplacementProfile.NUMERIC:
        digit_len = sum(c.isdigit() for c in original) or len(original) or len(hexlow)
        return _digits_from(token, digit_len)

    if p is ReplacementProfile.ENVIRONMENT_REFERENCE:
        return f"${{{ENV_SECRET_PREFIX}{hexup}}}"

    # FILE_PATH / URL matched values are components; format as an identifier token.
    # Structural path/URL splitting is handled by the engine (Phase 2).
    return f"{ALIAS_PREFIX}{_tag(entity_type)}_{hexup}"
