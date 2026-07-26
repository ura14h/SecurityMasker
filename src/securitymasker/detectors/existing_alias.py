"""SecurityMasker自身のaliasを認識して再マスクを防ぐ。

Makes masking idempotent: replaying conversation history that already contains
``SM_ORG_7F3A91`` must not turn it into ``SM_ORG_99AA12``. These hits are
reported as ``EXISTING_ALIAS`` and treated by ``policy`` as protected regions that
suppress any overlapping detection and are themselves left untouched.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# 特徴的なalias形式。Gatewayが既存aliasらしさを判断するheuristicだけに使う。
# seems to replay prior aliases" — never for protection, which matches the exact
# issued set (see ExistingAliasDetector). Profiles whose output is indistinguishable
# from ordinary data (numeric, and a bare ipv4/uuid) cannot be recognised by shape
# and are deliberately absent: guessing here would block legitimate traffic.
_ALIAS_RX = re.compile(
    r"""(?x)
      SM_[A-Z]+_[0-9A-F]{6,}                                  # prose identifier
    | sm-host-[0-9a-f]{6,}\.example\.invalid                  # hostname
    | sm-user-[0-9a-f]{6,}@example\.invalid                   # email
    | \$\{SECURITYMASKER_SECRET_[0-9A-F]{6,}\}                # env reference
    | (?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}         # ipv4 (RFC 5737)
    | 2001:db8::[0-9a-f]{1,16}                                # ipv6 (doc range)
    """
)


def contains_alias_shape(text: str) -> bool:
    """``text``にSecurityMasker alias形式のtokenがあればTrue。

    Used by the gateway to refuse a request that carries prior-turn aliases when no
    stable session could be resolved — restoring them would be impossible and a
    silent new session would corrupt the turn. Heuristic by nature:
    see the note on ``_ALIAS_RX``.
    """
    return _ALIAS_RX.search(text) is not None


class ExistingAliasDetector:
    """replayを冪等にするため、このsession自身のaliasを保護する。

    Matches the EXACT set of aliases issued in the current session rather than an
    alias *shape*: a shape regex can only describe the profiles whose output looks
    distinctive (prose/hostname/email/env_reference) and would silently miss
    ipv4, ipv6, uuid and numeric aliases — those got re-masked on every turn, so a
    replayed IPv4 alias drifted 198.51.100.83 -> .97 -> .246. Membership matching
    covers every profile and, by construction, protects nothing that was not
    actually issued here.
    """

    name = "existing_alias"

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not context.issued_aliases:
            return []
        text = context.norm.normalized
        # 長いaliasから処理し、短いprefix aliasの先取りを防ぐ。
        # a partial bite out of a collision-lengthened token.
        pattern = "|".join(
            re.escape(a) for a in sorted(context.issued_aliases, key=len, reverse=True)
        )
        results: list[DetectionResult] = []
        for m in re.finditer(pattern, text):
            o_start, o_end = context.norm.to_original_span(m.start(), m.end())
            results.append(
                DetectionResult(
                    entity_type=EntityType.EXISTING_ALIAS.value,
                    start=o_start,
                    end=o_end,
                    score=1.0,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=RestorePolicy.LITERAL.value,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=m.group(0),
                    metadata={"priority": 300},
                )
            )
        return results
