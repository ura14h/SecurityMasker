"""置換profile — entityの構文を保つ形にaliasを整える（§9）。

Each profile turns a short opaque ``token`` (hex derived from the fingerprint) into
an alias string that stays syntactically valid in the entity's context, so that
generated code / JSON / shell / hostnames don't break (§6, §16). Reserved,
non-routable domains/ranges (``.invalid``, ``2001:db8::/32``, ``198.51.100.0/24``)
are used so an alias can't be mistaken for a real endpoint (§9.2, §9.4).
"""

from __future__ import annotations

import hashlib

from securitymasker.aliases.structure import file_path_alias, url_alias
from securitymasker.models import EntityType, ReplacementProfile

ALIAS_PREFIX = "SM_"
INVALID_DOMAIN = "example.invalid"
# RFC 5737の三documentation rangeを使い、254ではなく3×254 aliasを得る（ADR-0007）。
_DOC_IPV4_NETS = ("192.0.2", "198.51.100", "203.0.113")
_DOC_IPV4_NET = _DOC_IPV4_NETS[1]  # kept for existing references/tests
_DOC_IPV6_PREFIX = "2001:db8"  # RFC 3849 documentation range
ENV_SECRET_PREFIX = "SECURITYMASKER_SECRET_"

# prose alias用のentity type別で短くidentifier-safeなtag（SM_<TAG>_<HEX>）。
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
    """``token``を決定論的に``length``桁の10進数へ展開する。"""
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
    """``profile``と不透明なhex ``token``から``original``用aliasを作る。"""
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
        # 形状保持により空間は有限。IPv4 aliasはdocumentation range内に限定する。
        # the documentation ranges, so there are 3 x 254 = 762 of them per session.
        # これは意図したtrade-offで、枯渇を黙って許容しない（ADR-0007）。
        # tolerated: the factory keeps lengthening the token, finds every candidate
        # taken, and raises AliasCollisionError -> the request fails closed.
        n = int(token, 16)
        net = _DOC_IPV4_NETS[n % len(_DOC_IPV4_NETS)]
        octet = (n // len(_DOC_IPV4_NETS)) % 254 + 1  # 1..254
        return f"{net}.{octet}"

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

    # URL／FILE_PATHは有効な構文を保つためcomponent単位で再構築する。
    # parseable URL or a resolvable-looking path (invariant 3).
    # A value that cannot be rebuilt safely raises MaskingError -> request blocked.
    if p is ReplacementProfile.URL:
        return url_alias(token, original)

    if p is ReplacementProfile.FILE_PATH:
        return file_path_alias(token, original)

    return f"{ALIAS_PREFIX}{_tag(entity_type)}_{hexup}"
